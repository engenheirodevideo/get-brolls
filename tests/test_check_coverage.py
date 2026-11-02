"""`scripts/check_coverage.py`: as metas saem do pyproject.toml, e o gate falha nomeando a lacuna.

A rodada de verdade (a suíte inteira sob coverage) é o próprio gate do CI; aqui o `runner`
é um falso que grava o que a rodada gravaria, para conferir a orquestração sem recursão.
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # efeito de import: põe scripts/ no sys.path, antes do check_coverage
import check_coverage as gate

TARGETS = """
[tool.getbrolls.coverage]
source = "scripts"
total_lines = 95
total_branches = 90
file_prefix = "scripts/getbrolls/"
file_lines = 90
file_branches = 85
strict_files = ["scripts/getbrolls/cli.py"]
strict_lines = 100
strict_branches = 100
"""


def _temp_dir(case):
    path = tempfile.mkdtemp(prefix="gb-check-coverage-")
    case.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return Path(path)


def _entry(statements, missing=(), branches=0, missing_branches=()):
    return {
        "summary": {"num_statements": statements, "num_branches": branches},
        "missing_lines": list(missing),
        "missing_branches": [list(arc) for arc in missing_branches],
    }


def _report(root, files):
    return {"files": {str(root / name): entry for name, entry in files.items()}}


class LoadTargetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = _temp_dir(self)
        self.pyproject = self.tmp / "pyproject.toml"

    def test_the_repository_targets_are_the_ones_bruno_chose(self):
        targets = gate.load_targets(_paths.ROOT / "pyproject.toml")
        self.assertEqual("scripts", targets.source)
        self.assertEqual((95.0, 90.0), (targets.total.lines, targets.total.branches))
        self.assertEqual((90.0, 85.0), (targets.per_file.lines, targets.per_file.branches))
        self.assertEqual((100.0, 100.0), (targets.strict.lines, targets.strict.branches))
        self.assertEqual(
            {
                f"scripts/getbrolls/{name}.py"
                for name in (
                    "cli",
                    "cli_parser",
                    "cli_project_args",
                    "cli_summaries",
                    "runtime",
                    "errors",
                    "_paths",
                    "runtime_parts",
                    "install_report",
                    "ledger",
                    "effects",
                    "logs",
                    "_salt_read",
                    "redaction",
                    "private_files",
                    "file_locks",
                    "error_codes",
                    "command_policy",
                    "diagnostics",
                    "effects_salt",
                )
            },
            set(targets.strict_files),
        )
        self.assertEqual("scripts/getbrolls", targets.file_prefix)

    def test_trailing_slash_and_spaces_are_trimmed(self):
        self.pyproject.write_text(TARGETS, encoding="utf-8")
        self.assertEqual("scripts/getbrolls", gate.load_targets(self.pyproject).file_prefix)

    def test_each_invalid_value_names_its_key(self):
        cases = {
            "total_lines": ("total_lines = 95", "total_lines = 101"),
            "file_branches": ("file_branches = 85", 'file_branches = "85"'),
            "strict_lines": ("strict_lines = 100", "strict_lines = true"),
            "source": ('source = "scripts"', 'source = "  "'),
            "strict_files": ('strict_files = ["scripts/getbrolls/cli.py"]', 'strict_files = ["", "x"]'),
        }
        for key, (good, bad) in cases.items():
            with self.subTest(key=key):
                self.pyproject.write_text(TARGETS.replace(good, bad), encoding="utf-8")
                with self.assertRaises(gate.ConfigError) as caught:
                    gate.load_targets(self.pyproject)
                self.assertIn(key, str(caught.exception))

    def test_missing_table_and_unreadable_file_are_config_errors(self):
        self.pyproject.write_text("[tool.other]\nx = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(gate.ConfigError, r"\[tool.getbrolls.coverage\]"):
            gate.load_targets(self.pyproject)
        self.pyproject.write_text("[tool\n", encoding="utf-8")
        with self.assertRaisesRegex(gate.ConfigError, "Não consegui ler"):
            gate.load_targets(self.pyproject)
        with self.assertRaisesRegex(gate.ConfigError, "Não consegui ler"):
            gate.load_targets(self.tmp / "ausente.toml")


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.root = _temp_dir(self).resolve()
        pyproject = self.root / "pyproject.toml"
        pyproject.write_text(TARGETS, encoding="utf-8")
        self.targets = gate.load_targets(pyproject)

    def files(self, entries):
        return gate.parse_report(_report(self.root, entries), self.root)

    def test_everything_met_has_no_gap(self):
        files = self.files(
            {
                "scripts/getbrolls/cli.py": _entry(10, branches=4),
                "scripts/getbrolls/x.py": _entry(100, missing=range(1, 11), branches=20, missing_branches=[(1, 2)] * 3),
                "scripts/bump_version.py": _entry(1000, missing=range(1, 11), branches=100),
            }
        )
        self.assertEqual([], gate.evaluate(files, self.targets))

    def test_a_contract_file_needs_every_line_and_branch(self):
        files = self.files(
            {"scripts/getbrolls/cli.py": _entry(1000, missing=[7, 8, 9, 12], branches=40, missing_branches=[(3, -1)])}
        )
        gaps = gate.evaluate(files, self.targets)
        self.assertEqual(
            [
                "scripts/getbrolls/cli.py: linhas 99.60% < 100% (contrato); faltam: 7-9, 12",
                "scripts/getbrolls/cli.py: ramos 97.50% < 100% (contrato); faltam: 3->exit",
            ],
            gaps,
        )

    def test_a_package_file_below_its_floor_is_named_with_its_gap(self):
        files = self.files(
            {
                "scripts/getbrolls/cli.py": _entry(1000, branches=100),
                "scripts/getbrolls/sdk/y.py": _entry(
                    10, missing=[4, 5], branches=10, missing_branches=[(4, 5), (6, 9)]
                ),
            }
        )
        self.assertEqual(
            [
                "scripts/getbrolls/sdk/y.py: linhas 80.00% < 90% (por arquivo); faltam: 4-5",
                "scripts/getbrolls/sdk/y.py: ramos 80.00% < 85% (por arquivo); faltam: 4->5, 6->9",
            ],
            gate.evaluate(files, self.targets),
        )

    def test_helpers_count_only_toward_the_total(self):
        files = self.files(
            {
                "scripts/getbrolls/cli.py": _entry(100, branches=10),
                "scripts/bump_version.py": _entry(10, missing=range(1, 8), branches=10, missing_branches=[(1, 2)] * 5),
            }
        )
        self.assertEqual(
            ["total (scripts): linhas 93.64% < 95%", "total (scripts): ramos 75.00% < 90%"],
            gate.evaluate(files, self.targets),
        )

    def test_an_unmeasured_contract_file_is_a_gap(self):
        self.assertEqual(
            ["scripts/getbrolls/cli.py: arquivo de contrato sem medição (não foi importado pela suíte?)"],
            gate.evaluate(self.files({"scripts/getbrolls/x.py": _entry(5)}), self.targets),
        )

    def test_files_outside_the_root_are_dropped_and_empty_files_count_as_covered(self):
        report = _report(self.root, {"scripts/getbrolls/cli.py": _entry(0)})
        report["files"]["/elsewhere/other.py"] = _entry(10, missing=range(1, 11))
        files = gate.parse_report(report, self.root)
        self.assertEqual(["scripts/getbrolls/cli.py"], [item.path for item in files])
        self.assertEqual((100.0, 100.0), (files[0].line_pct, files[0].branch_pct))

    def test_summary_lists_the_worst_package_files_first(self):
        files = self.files(
            {
                "scripts/getbrolls/a.py": _entry(10, missing=[1]),
                "scripts/getbrolls/b.py": _entry(10, missing=[1, 2]),
                "scripts/gb.py": _entry(10, missing=range(1, 11)),
            }
        )
        lines = gate.summarize(files, self.targets, lowest=1).splitlines()
        self.assertEqual(
            ["total (scripts): linhas 56.67%, ramos 100.00%", "  scripts/getbrolls/b.py: linhas 80.00%, ramos 100.00%"],
            lines,
        )

    def test_rcfile_uses_absolute_paths_and_keeps_subprocess_stderr_quiet(self):
        text = gate.rcfile_text(self.root, "scripts", self.root / "d" / ".coverage")
        self.assertIn(f"source = {self.root / 'scripts'}\n", text)
        self.assertIn(f"data_file = {self.root / 'd' / '.coverage'}\n", text)
        self.assertIn("parallel = True", text)
        self.assertIn("branch = True", text)
        self.assertIn("module-not-measured", text)

    def test_suite_environment_drops_gb_home_and_points_children_at_the_rcfile(self):
        env = gate.suite_environment({"GB_HOME": "/real", "PATH": "/bin"}, Path("/w/coveragerc"))
        self.assertEqual({"PATH": "/bin", "COVERAGE_PROCESS_START": str(Path("/w/coveragerc"))}, env)


class FakeRun:
    """Stands in for `subprocess.run`: records argv, and writes what coverage would write."""

    def __init__(self, root, report, **options):
        self.root, self.report, self.calls = root, report, []
        self.options = {"suite_exit": 0, "data": True, "fail_on": None, "coverage_missing": False, "unusable": False}
        self.options.update(options)

    def suite(self):
        """argv and options of the suite run (the `-m unittest` call)."""
        return next((argv, kwargs) for argv, kwargs in self.calls if argv[1:3] == ["-m", "unittest"])

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.options["fail_on"] and self.options["fail_on"] in argv:
            raise subprocess.CalledProcessError(1, argv)
        if argv[1:] == ["-c", "import coverage"]:
            return subprocess.CompletedProcess(
                argv, 1 if self.options["coverage_missing"] else 0, "", "ModuleNotFoundError"
            )
        if argv[1:3] == ["-m", "unittest"]:
            rcfile = Path(kwargs["env"]["COVERAGE_PROCESS_START"])
            if self.options["data"]:
                (rcfile.parent / "data" / ".coverage.host.1.x").write_text("", encoding="utf-8")
            if self.options["unusable"]:
                (rcfile.parent / "data" / ".coverage.host.2.cortado").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.options["suite_exit"])
        if "combine" in argv:
            # Like coverage: every file it could read is merged and deleted; an unreadable one stays.
            data = Path(argv[argv.index("--rcfile") + 1]).parent / "data"
            for part in data.glob(".coverage.*"):
                if not part.name.endswith(".cortado"):
                    part.unlink()
            (data / ".coverage").write_text("", encoding="utf-8")
        if "json" in argv:
            Path(argv[argv.index("-o") + 1]).write_text(json.dumps(self.report), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.root = _temp_dir(self).resolve()
        (self.root / "pyproject.toml").write_text(TARGETS, encoding="utf-8")
        (self.root / "scripts" / "getbrolls").mkdir(parents=True)
        (self.root / "scripts" / "getbrolls" / "cli.py").write_text("", encoding="utf-8")
        self.good = _report(self.root, {"scripts/getbrolls/cli.py": _entry(10, branches=2)})
        self.bad = _report(self.root, {"scripts/getbrolls/cli.py": _entry(10, missing=[3], branches=2)})

    def main(self, *argv, runner=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main(list(argv), runner=runner or FakeRun(self.root, self.good), root=self.root)
        return code, out.getvalue(), err.getvalue()

    def test_full_run_measures_combines_and_passes(self):
        runner = FakeRun(self.root, self.good)
        keep = self.root / "keep"
        (keep / "data").mkdir(parents=True)
        # Left by an earlier run, and unreadable: combined with this run it would end the gate.
        (keep / "data" / ".coverage.anterior.cortado").write_text("", encoding="utf-8")
        code, out, _ = self.main("--keep", str(keep), "--python", "py", runner=runner)
        self.assertEqual(gate.EXIT_OK, code)
        self.assertIn("Cobertura OK", out)
        probe, suite, combine, export = (argv for argv, _ in runner.calls)
        self.assertEqual(["py", "-c", "import coverage"], probe)
        self.assertEqual(["py", "-m", "unittest", "discover", "-s", "tests"], suite)
        _, options = runner.suite()
        self.assertEqual(self.root, options["cwd"])
        self.assertEqual(str(keep / "coveragerc"), options["env"]["COVERAGE_PROCESS_START"])
        self.assertNotIn("GB_HOME", options["env"])
        self.assertEqual(["py", "-m", "coverage", "combine", "--rcfile", str(keep / "coveragerc"), "-q"], combine)
        self.assertEqual(str(keep / "coverage.json"), export[-1])
        self.assertFalse((keep / "data" / ".coverage.anterior.cortado").exists())
        self.assertIn(f"data_file = {keep / 'data' / '.coverage'}", (keep / "coveragerc").read_text(encoding="utf-8"))

    def test_without_keep_the_run_uses_a_temporary_folder(self):
        runner = FakeRun(self.root, self.good)
        code, _, _ = self.main(runner=runner)
        self.assertEqual(gate.EXIT_OK, code)
        workdir = Path(runner.suite()[1]["env"]["COVERAGE_PROCESS_START"]).parent
        self.assertTrue(workdir.name.startswith("gb-coverage-"))
        self.assertFalse(workdir.exists())

    def test_a_python_without_coverage_stops_before_the_suite(self):
        runner = FakeRun(self.root, self.good, coverage_missing=True)
        code, _, err = self.main("--python", "py", runner=runner)
        self.assertEqual(gate.EXIT_USAGE, code)
        self.assertEqual([["py", "-c", "import coverage"]], [argv for argv, _ in runner.calls])
        self.assertIn("o Python py não tem o pacote `coverage`", err)

    def test_a_data_file_coverage_could_not_combine_is_an_environment_error(self):
        runner = FakeRun(self.root, self.good, unusable=True)
        code, out, err = self.main("--keep", str(self.root / "keep"), runner=runner)
        self.assertEqual(gate.EXIT_USAGE, code)
        self.assertIn(".coverage.host.2.cortado", err)
        self.assertIn("não conseguiu ler (cortados ou corrompidos)", err)
        self.assertNotIn("Cobertura OK", out)
        self.assertFalse(any("json" in argv for argv, _ in runner.calls))

    def test_a_config_path_that_matches_no_file_is_refused_before_running(self):
        cases = {
            "strict_files": (
                'strict_files = ["scripts/getbrolls/cli.py"]',
                'strict_files = ["scripts/getbrolls/cl.py"]',
                "scripts/getbrolls/cl.py",
            ),
            "file_prefix": (
                'file_prefix = "scripts/getbrolls/"',
                'file_prefix = "scripts/getbroll"',
                "scripts/getbroll",
            ),
            "source": ('source = "scripts"', 'source = "script"', "script"),
        }
        for key, (good, bad, shown) in cases.items():
            with self.subTest(key=key):
                (self.root / "pyproject.toml").write_text(TARGETS.replace(good, bad), encoding="utf-8")
                runner = FakeRun(self.root, self.good)
                code, _, err = self.main(runner=runner)
                self.assertEqual((gate.EXIT_USAGE, []), (code, runner.calls))
                self.assertIn(f"[tool.getbrolls.coverage] {key}", err)
                self.assertIn(shown, err)

    def test_a_file_prefix_without_python_files_is_refused(self):
        (self.root / "scripts" / "getbrolls" / "cli.py").rename(self.root / "scripts" / "cli.py")
        (self.root / "pyproject.toml").write_text(
            TARGETS.replace('strict_files = ["scripts/getbrolls/cli.py"]', 'strict_files = ["scripts/cli.py"]'),
            encoding="utf-8",
        )
        code, _, err = self.main("--from-json", str(self.root / "nada.json"))
        self.assertEqual(gate.EXIT_USAGE, code)
        self.assertIn("file_prefix", err)

    def test_a_missed_target_fails_and_names_the_gap(self):
        code, _, err = self.main(runner=FakeRun(self.root, self.bad))
        self.assertEqual(gate.EXIT_FAILED, code)
        self.assertIn("- scripts/getbrolls/cli.py: linhas 90.00% < 100% (contrato); faltam: 3", err)

    def test_a_failing_suite_fails_the_gate_even_with_coverage_met(self):
        code, out, err = self.main(runner=FakeRun(self.root, self.good, suite_exit=1))
        self.assertEqual(gate.EXIT_FAILED, code)
        self.assertIn("Cobertura OK", out)
        self.assertIn("A suíte falhou (saída 1)", err)

    def test_no_data_means_the_subprocess_hook_is_missing(self):
        code, _, err = self.main(runner=FakeRun(self.root, self.good, data=False))
        self.assertEqual(gate.EXIT_USAGE, code)
        self.assertIn("a1_coverage.pth", err)

    def test_a_failing_coverage_command_is_an_environment_error(self):
        code, _, err = self.main(runner=FakeRun(self.root, self.good, fail_on="combine"))
        self.assertEqual(gate.EXIT_USAGE, code)
        self.assertIn("check_coverage:", err)

    def test_from_json_only_checks_the_given_report(self):
        report = self.root / "coverage.json"
        report.write_text(json.dumps(self.bad), encoding="utf-8")
        runner = FakeRun(self.root, self.good)
        code, out, _ = self.main("--from-json", str(report), runner=runner)
        self.assertEqual((gate.EXIT_FAILED, []), (code, runner.calls))
        self.assertIn("total (scripts): linhas 90.00%", out)

    def test_unreadable_or_empty_reports_are_environment_errors(self):
        broken = self.root / "broken.json"
        broken.write_text("{", encoding="utf-8")
        empty = self.root / "empty.json"
        empty.write_text(json.dumps({"files": {}}), encoding="utf-8")
        for path, message in ((broken, "Não consegui ler o relatório"), (empty, "não mede nenhum arquivo")):
            with self.subTest(path=path.name):
                code, _, err = self.main("--from-json", str(path))
                self.assertEqual(gate.EXIT_USAGE, code)
                self.assertIn(message, err)

    def test_invalid_targets_stop_before_running_anything(self):
        (self.root / "pyproject.toml").write_text(
            TARGETS.replace("total_lines = 95", "total_lines = -1"), encoding="utf-8"
        )
        runner = FakeRun(self.root, self.good)
        code, _, err = self.main(runner=runner)
        self.assertEqual((gate.EXIT_USAGE, []), (code, runner.calls))
        self.assertIn("total_lines", err)


class ScriptTests(unittest.TestCase):
    def test_the_script_checks_a_report_and_exits_with_its_verdict(self):
        root = _paths.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "coverage.json"
            report.write_text(
                json.dumps(_report(root, {"scripts/getbrolls/cli.py": _entry(4, missing=[2])})), encoding="utf-8"
            )
            done = subprocess.run(
                [sys.executable, str(root / "scripts" / "check_coverage.py"), "--from-json", str(report)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(gate.EXIT_FAILED, done.returncode, done.stderr)
        self.assertIn("scripts/getbrolls/cli.py: linhas 75.00% < 100% (contrato); faltam: 2", done.stderr)
        self.assertIn("scripts/getbrolls/runtime.py: arquivo de contrato sem medição", done.stderr)


class RangesTests(unittest.TestCase):
    def test_consecutive_lines_collapse(self):
        self.assertEqual("1-3, 7, 9-10", gate.ranges([9, 1, 2, 3, 7, 10]))
        self.assertEqual("", gate.ranges([]))


if __name__ == "__main__":
    unittest.main()
