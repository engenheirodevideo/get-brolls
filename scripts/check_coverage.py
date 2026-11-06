#!/usr/bin/env python3
"""Gate de cobertura: roda a suíte sob `coverage`, medindo também os subprocessos da
CLI, e falha quando alguma meta de `[tool.getbrolls.coverage]` (pyproject.toml) não é
atingida, dizendo o arquivo e a lacuna.

Uso (da raiz do repositório, com `coverage` instalado no mesmo Python):

    python3 scripts/check_coverage.py                       # roda a suíte e confere
    python3 scripts/check_coverage.py --keep DIR            # guarda os dados e o coverage.json em DIR
    python3 scripts/check_coverage.py --from-json FILE      # só confere um coverage.json pronto

Saída 0 com todas as metas atingidas; 1 quando a suíte falha ou uma meta fica de fora;
2 para erro de uso ou de ambiente (configuração inválida, `coverage` ausente, nenhum dado).
"""

# pylint: disable=duplicate-code
# The `_utf8_output` helper is repeated on purpose: each standalone script is self-contained.

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

# Warnings that would land on a measured subprocess's stderr, which tests assert on.
_QUIET = "no-data-collected, module-not-imported, module-not-measured, already-imported, couldnt-parse"

Runner = Callable[..., subprocess.CompletedProcess]


class ConfigError(ValueError):
    """`[tool.getbrolls.coverage]` is missing or has an invalid value."""


@dataclasses.dataclass(frozen=True)
class Floor:
    """Minimum line and branch percentages, compared without rounding."""

    lines: float
    branches: float


@dataclasses.dataclass(frozen=True)
class Targets:
    """Coverage targets: the total, every file under `file_prefix`, and the strict files."""

    source: str
    total: Floor
    file_prefix: str
    per_file: Floor
    strict_files: tuple[str, ...]
    strict: Floor


@dataclasses.dataclass(frozen=True)
class FileCoverage:
    """Counts for one measured file, path relative to the repository root (POSIX)."""

    path: str
    statements: int
    missing: tuple[int, ...]
    branches: int
    missing_branches: tuple[tuple[int, int], ...]

    @property
    def line_pct(self) -> float:
        """Covered statements, in percent (100 for a file with none)."""
        return _pct(self.statements - len(self.missing), self.statements)

    @property
    def branch_pct(self) -> float:
        """Covered branch arcs, in percent (100 for a file with none)."""
        return _pct(self.branches - len(self.missing_branches), self.branches)


def _utf8_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def _pct(hit: int, total: int) -> float:
    return 100.0 * hit / total if total else 100.0


def _percent(table: dict, key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 100:  # noqa: PLR2004
        raise ConfigError(f"[tool.getbrolls.coverage] {key} precisa ser um número de 0 a 100, não {value!r}.")
    return float(value)


def _text(table: dict, key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[tool.getbrolls.coverage] {key} precisa ser um texto não vazio, não {value!r}.")
    return value.strip().rstrip("/")


def load_targets(pyproject: Path = PYPROJECT) -> Targets:
    """Read the targets from `pyproject.toml`; `ConfigError` names the bad key."""
    try:
        with pyproject.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Não consegui ler {pyproject}: {exc}") from exc
    table = document.get("tool", {}).get("getbrolls", {}).get("coverage")
    if not isinstance(table, dict):
        raise ConfigError(f"{pyproject} não tem a tabela [tool.getbrolls.coverage].")
    strict = table.get("strict_files")
    if not isinstance(strict, list) or not all(isinstance(item, str) and item for item in strict):
        raise ConfigError("[tool.getbrolls.coverage] strict_files precisa ser uma lista de caminhos.")
    return Targets(
        source=_text(table, "source"),
        total=Floor(_percent(table, "total_lines"), _percent(table, "total_branches")),
        file_prefix=_text(table, "file_prefix"),
        per_file=Floor(_percent(table, "file_lines"), _percent(table, "file_branches")),
        strict_files=tuple(strict),
        strict=Floor(_percent(table, "strict_lines"), _percent(table, "strict_branches")),
    )


def validate_targets(targets: Targets, root: Path) -> None:
    """Every path of the targets names real files under `root`: a typo is an error, never a target
    that silently matches nothing."""
    key = "[tool.getbrolls.coverage]"
    if not (root / targets.source).is_dir():
        raise ConfigError(f"{key} source: {targets.source} não é uma pasta em {root}.")
    prefix = root / targets.file_prefix
    if not prefix.is_dir() or not any(prefix.rglob("*.py")):
        raise ConfigError(f"{key} file_prefix: {targets.file_prefix} não é uma pasta com arquivos .py em {root}.")
    missing = [path for path in targets.strict_files if not (root / path).is_file()]
    if missing:
        raise ConfigError(f"{key} strict_files: não existe(m) em {root}: {', '.join(missing)}.")


def rcfile_text(root: Path, source: str, data_file: Path) -> str:
    """The coverage rcfile of one gate run: absolute paths, so any subprocess cwd works."""
    return (
        "[run]\n"
        "parallel = True\n"
        "branch = True\n"
        f"source = {root / source}\n"
        f"data_file = {data_file}\n"
        f"disable_warnings = {_QUIET}\n"
        "\n[report]\n"
        "skip_empty = True\n"
    )


def _relative(name: str, root: Path) -> str | None:
    try:
        return Path(name).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def parse_report(report: dict, root: Path) -> list[FileCoverage]:
    """Files of a `coverage json` report, relative to `root`; files outside it are dropped."""
    files = []
    for name, info in sorted((report.get("files") or {}).items()):
        rel = _relative(name, root)
        if rel is None:
            continue
        summary = info["summary"]
        files.append(
            FileCoverage(
                path=rel,
                statements=summary["num_statements"],
                missing=tuple(info.get("missing_lines") or ()),
                branches=summary.get("num_branches", 0),
                missing_branches=tuple(tuple(arc) for arc in info.get("missing_branches") or ()),
            )
        )
    return files


def ranges(numbers: Sequence[int]) -> str:
    """`[1, 2, 3, 7]` as `1-3, 7`."""
    spans: list[list[int]] = []
    for number in sorted(numbers):
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ", ".join(f"{first}-{last}" if first != last else str(first) for first, last in spans)


def _arcs(arcs: Sequence[tuple[int, int]]) -> str:
    return ", ".join(f"{start}->{end if end > 0 else 'exit'}" for start, end in arcs)


def _file_gaps(item: FileCoverage, floor: Floor, label: str) -> list[str]:
    gaps = []
    if item.line_pct < floor.lines:
        gaps.append(
            f"{item.path}: linhas {item.line_pct:.2f}% < {floor.lines:g}% ({label}); faltam: {ranges(item.missing)}"
        )
    if item.branch_pct < floor.branches:
        gaps.append(
            f"{item.path}: ramos {item.branch_pct:.2f}% < {floor.branches:g}% ({label}); faltam: "
            f"{_arcs(item.missing_branches)}"
        )
    return gaps


def totals(files: Sequence[FileCoverage]) -> FileCoverage:
    """Every file summed into one record named `total`."""
    return FileCoverage(
        path="total",
        statements=sum(item.statements for item in files),
        missing=tuple(line for item in files for line in item.missing),
        branches=sum(item.branches for item in files),
        missing_branches=tuple(arc for item in files for arc in item.missing_branches),
    )


def _under(item: FileCoverage, prefix: str) -> bool:
    return PurePosixPath(prefix) in PurePosixPath(item.path).parents


def evaluate(files: Sequence[FileCoverage], targets: Targets) -> list[str]:
    """Every missed target, one line each; empty when all of them are met."""
    total = totals(files)
    gaps = []
    if total.line_pct < targets.total.lines:
        gaps.append(f"total ({targets.source}): linhas {total.line_pct:.2f}% < {targets.total.lines:g}%")
    if total.branch_pct < targets.total.branches:
        gaps.append(f"total ({targets.source}): ramos {total.branch_pct:.2f}% < {targets.total.branches:g}%")
    measured = {item.path for item in files}
    gaps += [
        f"{path}: arquivo de contrato sem medição (não foi importado pela suíte?)"
        for path in targets.strict_files
        if path not in measured
    ]
    for item in files:
        if item.path in targets.strict_files:
            gaps += _file_gaps(item, targets.strict, "contrato")
        elif _under(item, targets.file_prefix):
            gaps += _file_gaps(item, targets.per_file, "por arquivo")
    return gaps


def summarize(files: Sequence[FileCoverage], targets: Targets, lowest: int = 15) -> str:
    """Totals plus the `lowest` files under `file_prefix`, worst first."""
    total = totals(files)
    package = sorted(
        (item for item in files if _under(item, targets.file_prefix)),
        key=lambda item: (item.line_pct, item.branch_pct, item.path),
    )
    out = [f"total ({targets.source}): linhas {total.line_pct:.2f}%, ramos {total.branch_pct:.2f}%"]
    out += [f"  {item.path}: linhas {item.line_pct:.2f}%, ramos {item.branch_pct:.2f}%" for item in package[:lowest]]
    return "\n".join(out)


def suite_environment(source_env: dict[str, str], rcfile: Path) -> dict[str, str]:
    """The suite's environment: subprocesses measured, and never the developer's `GB_HOME`."""
    environment = {key: value for key, value in source_env.items() if key != "GB_HOME"}
    environment["COVERAGE_PROCESS_START"] = str(rcfile)
    return environment


def measure(python: str, root: Path, workdir: Path, targets: Targets, runner: Runner = subprocess.run) -> int:
    """Run the suite under coverage into `workdir`, then combine it into `workdir/coverage.json`.

    Returns the suite's exit code. The main process and every child that inherits
    `COVERAGE_PROCESS_START` start measuring through the `.pth` hook that `coverage` installs.
    """
    probe = runner([python, "-c", "import coverage"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        raise ConfigError(
            f"o Python {python} não tem o pacote `coverage`; instale o pinado em requirements-dev.txt "
            "(python3 -m pip install -r requirements-dev.txt) antes de rodar o gate."
        )
    rcfile = workdir / "coveragerc"
    rcfile.write_text(rcfile_text(root, targets.source, workdir / "data" / ".coverage"), encoding="utf-8", newline="\n")
    (workdir / "data").mkdir(exist_ok=True)
    # A `--keep` folder reused from an earlier run: its data would be combined into this one.
    for stale in (workdir / "data").glob(".coverage*"):
        stale.unlink()
    environment = suite_environment(dict(os.environ), rcfile)
    suite = runner([python, "-m", "unittest", "discover", "-s", "tests"], cwd=root, env=environment, check=False)
    if not any((workdir / "data").glob(".coverage.*")):
        raise ConfigError(
            "Nenhum dado de cobertura foi gravado: o `coverage` deste Python não instala o gancho de "
            "subprocesso (a1_coverage.pth). Instale o pinado em requirements-dev.txt."
        )
    tool = [python, "-m", "coverage"]
    runner([*tool, "combine", "--rcfile", str(rcfile), "-q"], cwd=workdir, check=True)
    # `combine` deletes every file it merged and only warns about one it cannot read (cut short
    # by a killed process, or corrupt): whatever is left was not counted, so the report would lie.
    leftovers = sorted(part.name for part in (workdir / "data").glob(".coverage.*"))
    if leftovers:
        raise ConfigError(
            f"Dados de cobertura que o coverage não conseguiu ler (cortados ou corrompidos) ficariam fora da "
            f"conta: {', '.join(leftovers)} (em {workdir / 'data'}). Rode o gate de novo."
        )
    runner(
        [*tool, "json", "--rcfile", str(rcfile), "-q", "-o", str(workdir / "coverage.json")], cwd=workdir, check=True
    )
    return suite.returncode


def check(report_path: Path, root: Path, targets: Targets) -> int:
    """Print the summary and every gap of a `coverage json` report; 0 when every target is met."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Não consegui ler o relatório {report_path}: {exc}") from exc
    files = parse_report(report, root)
    if not files:
        raise ConfigError(f"O relatório {report_path} não mede nenhum arquivo de {root}.")
    print(summarize(files, targets))
    gaps = evaluate(files, targets)
    if gaps:
        print(f"\nCobertura abaixo da meta ({len(gaps)}):", file=sys.stderr)
        for gap in gaps:
            print(f"- {gap}", file=sys.stderr)
        return EXIT_FAILED
    print("\nCobertura OK: todas as metas de [tool.getbrolls.coverage] atingidas.")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check_coverage.py", description=(__doc__ or "").split("\n\n", maxsplit=1)[0])
    parser.add_argument("--from-json", type=Path, help="só confere este coverage.json, sem rodar a suíte")
    parser.add_argument("--keep", type=Path, help="pasta onde ficam os dados e o coverage.json da rodada")
    parser.add_argument("--python", default=sys.executable, help="Python da suíte (padrão: este)")
    return parser


def main(argv: Sequence[str] | None = None, runner: Runner = subprocess.run, root: Path = ROOT) -> int:
    """Entry point; `runner` and `root` are seams for the tests."""
    _utf8_output()
    args = _parser().parse_args(argv)
    try:
        targets = load_targets(root / "pyproject.toml")
        validate_targets(targets, root)
        if args.from_json is not None:
            return check(args.from_json, root, targets)
        with contextlib.ExitStack() as stack:
            if args.keep is not None:
                workdir = args.keep.resolve()
                workdir.mkdir(parents=True, exist_ok=True)
            else:
                workdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="gb-coverage-")))
            suite = measure(args.python, root, workdir, targets, runner)
            result = check(workdir / "coverage.json", root, targets)
    except (ConfigError, subprocess.CalledProcessError) as exc:
        print(f"check_coverage: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if suite != 0:
        print(f"\nA suíte falhou (saída {suite}); a cobertura acima não vale como gate.", file=sys.stderr)
        return EXIT_FAILED
    return result


if __name__ == "__main__":
    sys.exit(main())
