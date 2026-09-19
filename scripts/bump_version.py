#!/usr/bin/env python3
"""Sincroniza a versão do projeto nas fontes que a declaram.

`python3 scripts/bump_version.py X.Y.Z [--date AAAA-MM-DD] [--check]`
escreve, numa passada só, a versão nova em:

- `scripts/getbrolls/__init__.py` (`__version__`);
- `package.json` (`version`, round-trip `json.dumps(indent=2)` + `\n`);
- `package-lock.json` (`version` na raiz e em `packages[""]`, duas
  ocorrências);
- `.claude-plugin/plugin.json` (`version`);
- `.claude-plugin/marketplace.json` (`plugins[0].version`);
- `SKILL.md` (`metadata.version` e `metadata.updated`);
- `README.md` / `README.en.md` (badge: URL e `alt`);
- `docs/QUALITY.md` (título `# Qualidade e evidências — GET B-ROLLS <v>`);
- `CHANGELOG.md` (stub `## <v> — <data>` abaixo de `## Unreleased`, só se
  ainda não existir uma seção para essa versão);
- `skills/get-brolls/SKILL.md`, regerado chamando `gen_skill_mirror.py`.

`--check` não escreve nada: lê as mesmas fontes (exceto `CHANGELOG.md`, cujo
corpo é prosa humana) e sai com código 1 assim que alguma delas divergir da
versão pedida. `--date` aceita `AAAA-MM-DD`; sem ele, usa a data local de
hoje. `--root` é só para teste: aponta a raiz do repositório para uma cópia
temporária, nunca para a árvore real.

Sem dependências externas (stdlib).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _default_root() -> Path:
    return Path(__file__).resolve().parent.parent


@dataclass
class Target:
    """Uma fonte de versão: nome, checagem e escrita."""

    name: str
    check: Callable[[Path, str], bool]
    write: Callable[[Path, str, str], None]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# -- scripts/getbrolls/__init__.py -------------------------------------

_INIT_RE = re.compile(r'__version__ = "(\d+\.\d+\.\d+)"')


def _init_path(root: Path) -> Path:
    return root / "scripts" / "getbrolls" / "__init__.py"


def _check_init(root: Path, version: str) -> bool:
    path = _init_path(root)
    match = _INIT_RE.search(_read(path))
    return bool(match) and match.group(1) == version


def _write_init(root: Path, version: str, _date_str: str) -> None:
    path = _init_path(root)
    text = _read(path)
    new_text, count = _INIT_RE.subn(f'__version__ = "{version}"', text)
    if count != 1:
        raise ValueError(f"__version__ não encontrado em {path}")
    _write(path, new_text)


# -- package.json ---------------------------------------------------------


def _package_json_path(root: Path) -> Path:
    return root / "package.json"


def _check_package_json(root: Path, version: str) -> bool:
    data = json.loads(_read(_package_json_path(root)))
    return data.get("version") == version


def _write_package_json(root: Path, version: str, _date_str: str) -> None:
    path = _package_json_path(root)
    data = json.loads(_read(path))
    data["version"] = version
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# -- package-lock.json ------------------------------------------------


def _package_lock_path(root: Path) -> Path:
    return root / "package-lock.json"


def _check_package_lock(root: Path, version: str) -> bool:
    data = json.loads(_read(_package_lock_path(root)))
    root_ok = data.get("version") == version
    packages_ok = data.get("packages", {}).get("", {}).get("version") == version
    return root_ok and packages_ok


def _write_package_lock(root: Path, version: str, _date_str: str) -> None:
    path = _package_lock_path(root)
    data = json.loads(_read(path))
    data["version"] = version
    if "" in data.get("packages", {}):
        data["packages"][""]["version"] = version
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# -- .claude-plugin/plugin.json ----------------------------------------


def _plugin_json_path(root: Path) -> Path:
    return root / ".claude-plugin" / "plugin.json"


def _check_plugin_json(root: Path, version: str) -> bool:
    data = json.loads(_read(_plugin_json_path(root)))
    return data.get("version") == version


def _write_plugin_json(root: Path, version: str, _date_str: str) -> None:
    path = _plugin_json_path(root)
    data = json.loads(_read(path))
    data["version"] = version
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# -- .claude-plugin/marketplace.json -----------------------------------


def _marketplace_json_path(root: Path) -> Path:
    return root / ".claude-plugin" / "marketplace.json"


def _check_marketplace_json(root: Path, version: str) -> bool:
    data = json.loads(_read(_marketplace_json_path(root)))
    plugins = data.get("plugins") or []
    return bool(plugins) and plugins[0].get("version") == version


def _write_marketplace_json(root: Path, version: str, _date_str: str) -> None:
    path = _marketplace_json_path(root)
    data = json.loads(_read(path))
    data["plugins"][0]["version"] = version
    _write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# -- SKILL.md -------------------------------------------------------------

_SKILL_VERSION_RE = re.compile(r'(metadata:\s*\n\s*version:\s*)"(\d+\.\d+\.\d+)"')
_SKILL_UPDATED_RE = re.compile(r'(\n\s*updated:\s*)"(\d{4}-\d{2}-\d{2})"')


def _skill_md_path(root: Path) -> Path:
    return root / "SKILL.md"


def _check_skill_md(root: Path, version: str) -> bool:
    match = _SKILL_VERSION_RE.search(_read(_skill_md_path(root)))
    return bool(match) and match.group(2) == version


def _write_skill_md(root: Path, version: str, date_str: str) -> None:
    path = _skill_md_path(root)
    text = _read(path)
    text, count = _SKILL_VERSION_RE.subn(rf'\g<1>"{version}"', text)
    if count != 1:
        raise ValueError(f"metadata.version não encontrado em {path}")
    text, count = _SKILL_UPDATED_RE.subn(rf'\g<1>"{date_str}"', text)
    if count != 1:
        raise ValueError(f"metadata.updated não encontrado em {path}")
    _write(path, text)


# -- README.md / README.en.md --------------------------------------------

_BADGE_URL_RE = re.compile(r"(badge/version-)\d+\.\d+\.\d+(-blue)")


def _badge_alt_re(word: str) -> re.Pattern[str]:
    return re.compile(rf'(alt="{word} )\d+\.\d+\.\d+(")')


def _check_readme(path: Path, version: str, alt_word: str) -> bool:
    text = _read(path)
    url_match = re.search(r"badge/version-(\d+\.\d+\.\d+)-blue", text)
    alt_match = re.search(rf'alt="{alt_word} (\d+\.\d+\.\d+)"', text)
    return bool(url_match) and url_match.group(1) == version and bool(alt_match) and alt_match.group(1) == version


def _write_readme(path: Path, version: str, alt_word: str) -> None:
    text = _read(path)
    text, count = _BADGE_URL_RE.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise ValueError(f"badge de versão não encontrado em {path}")
    text, count = _badge_alt_re(alt_word).subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise ValueError(f'alt="{alt_word} X.Y.Z" não encontrado em {path}')
    _write(path, text)


def _check_readme_pt(root: Path, version: str) -> bool:
    return _check_readme(root / "README.md", version, "Vers[ãa]o")


def _write_readme_pt(root: Path, version: str, _date_str: str) -> None:
    _write_readme(root / "README.md", version, "Versão")


def _check_readme_en(root: Path, version: str) -> bool:
    return _check_readme(root / "README.en.md", version, "Version")


def _write_readme_en(root: Path, version: str, _date_str: str) -> None:
    _write_readme(root / "README.en.md", version, "Version")


# -- docs/QUALITY.md --------------------------------------------------


_QUALITY_TITLE_RE = re.compile(r"^# Qualidade e evidências — GET B-ROLLS (\d+\.\d+\.\d+)$", re.MULTILINE)
_QUALITY_TITLE_SUB_RE = re.compile(r"^(# Qualidade e evidências — GET B-ROLLS )\d+\.\d+\.\d+$", re.MULTILINE)


def _quality_md_path(root: Path) -> Path:
    return root / "docs" / "QUALITY.md"


def _check_quality_md(root: Path, version: str) -> bool:
    match = _QUALITY_TITLE_RE.search(_read(_quality_md_path(root)))
    return bool(match) and match.group(1) == version


def _write_quality_md(root: Path, version: str, _date_str: str) -> None:
    path = _quality_md_path(root)
    text = _read(path)
    text, count = _QUALITY_TITLE_SUB_RE.subn(rf"\g<1>{version}", text)
    if count != 1:
        raise ValueError(f"título de versão não encontrado em {path}")
    _write(path, text)


# -- CHANGELOG.md (fora da coerência: corpo é prosa humana) -----------


def _changelog_path(root: Path) -> Path:
    return root / "CHANGELOG.md"


def _check_changelog(root: Path, version: str) -> bool:
    text = _read(_changelog_path(root))
    return re.search(rf"^## {re.escape(version)} — ", text, re.MULTILINE) is not None


def _write_changelog(root: Path, version: str, date_str: str) -> None:
    path = _changelog_path(root)
    text = _read(path)
    if re.search(rf"^## {re.escape(version)} — ", text, re.MULTILINE):
        return  # stub já existe; não duplica.
    marker = "## Unreleased\n"
    if marker not in text:
        raise ValueError(f"'## Unreleased' não encontrado em {path}")
    stub = f"## Unreleased\n\n## {version} — {date_str}\n"
    _write(path, text.replace(marker, stub, 1))


# -- skills/get-brolls/SKILL.md (regerado, não editado diretamente) ---


def _gen_skill_mirror(root: Path) -> Path:
    return root / "scripts" / "gen_skill_mirror.py"


def _check_skill_mirror(root: Path, _version: str) -> bool:
    result = subprocess.run(
        [sys.executable, str(_gen_skill_mirror(root)), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode == 0


def _write_skill_mirror(root: Path, _version: str, _date_str: str) -> None:
    result = subprocess.run(
        [sys.executable, str(_gen_skill_mirror(root))],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ValueError(f"gen_skill_mirror.py falhou: {result.stderr.strip() or result.stdout.strip()}")


TARGETS: list[Target] = [
    Target("scripts/getbrolls/__init__.py", _check_init, _write_init),
    Target("package.json", _check_package_json, _write_package_json),
    Target("package-lock.json", _check_package_lock, _write_package_lock),
    Target(".claude-plugin/plugin.json", _check_plugin_json, _write_plugin_json),
    Target(
        ".claude-plugin/marketplace.json",
        _check_marketplace_json,
        _write_marketplace_json,
    ),
    Target("SKILL.md", _check_skill_md, _write_skill_md),
    Target("README.md", _check_readme_pt, _write_readme_pt),
    Target("README.en.md", _check_readme_en, _write_readme_en),
    Target("docs/QUALITY.md", _check_quality_md, _write_quality_md),
    Target("CHANGELOG.md", _check_changelog, _write_changelog),
    # A regeneração do espelho depende do SKILL.md já escrito; roda por
    # último tanto na checagem quanto na escrita.
    Target("skills/get-brolls/SKILL.md", _check_skill_mirror, _write_skill_mirror),
]


def run(root: Path, version: str, date_str: str, check: bool) -> int:
    if not VERSION_RE.match(version):
        print(f"versão inválida: {version!r} (esperado X.Y.Z)", file=sys.stderr)
        return 2
    if not DATE_RE.match(date_str):
        print(f"data inválida: {date_str!r} (esperado AAAA-MM-DD)", file=sys.stderr)
        return 2

    if check:
        divergent = [t.name for t in TARGETS if not t.check(root, version)]
        if divergent:
            print("fora de sincronia com a versão " + version + ":", file=sys.stderr)
            for name in divergent:
                print(f"  - {name}", file=sys.stderr)
            return 1
        print(f"todas as fontes coerentes com a versão {version}.")
        return 0

    for target in TARGETS:
        target.write(root, version, date_str)
    print(f"versão atualizada para {version}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="nova versão, no formato X.Y.Z")
    parser.add_argument(
        "--date",
        default=_date.today().isoformat(),  # noqa: DTZ011 - data local do CLI, sem troca de comportamento
        help="data AAAA-MM-DD usada no CHANGELOG e em SKILL.md (padrão: hoje)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="só compara; não escreve. Sai 1 se alguma fonte divergir.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help=argparse.SUPPRESS,  # só para teste: raiz alternativa do repositório.
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _default_root()
    return run(root, args.version, args.date, args.check)


if __name__ == "__main__":
    sys.exit(main())
