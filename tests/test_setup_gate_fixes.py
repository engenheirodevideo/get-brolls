"""`setup`/`doctor` depois dos gates da 2.6.0: venv quebrada, `--upgrade`, yt-dlp do PATH e trava.

Reaproveita o `FakeRunner` de `test_setup_install`: nenhum passo abre processo de verdade
nem usa rede. O `which` é controlado pelo teste (`self.found`): esta máquina pode ter um
yt-dlp global no PATH, e ele não pode decidir o resultado por conta própria.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_setup_install import PYTHON_LAYOUT, WINDOWS, YTDLP_LAYOUT, _Setup, _touch

from getbrolls import _paths, bootstrap, cli, runtime
from getbrolls.errors import LockedError, UsageError


def _step(result, step_id):
    return next(step for step in result["steps"] if step["id"] == step_id)


class _Gate(_Setup):
    def setUp(self):
        super().setUp()
        # Sem yt-dlp nem Playwright globais, a menos que o teste peça.
        self.found["yt-dlp"] = None
        self.found["playwright-cli"] = None
        self.global_ytdlp = str(_touch(self.base / "global" / "yt-dlp"))

    def venv(self):
        return self.root() / ".venv"

    def dangle_interpreter(self):
        """`bin/python` → `python3.14` → um Python que não existe mais (a venv do QA)."""
        python = self.venv() / PYTHON_LAYOUT
        python.unlink()
        target = python.with_name("python3.14")
        target.symlink_to(self.base / "nonexistent" / "python3.14")
        python.symlink_to(target.name)


@unittest.skipIf(WINDOWS, "links simbólicos exigem privilégio no Windows")
class BrokenVenvTests(_Gate):
    def test_part_ready_needs_the_venv_python(self):
        self.install()
        self.assertTrue(_paths.part_ready("venv", self.root()))
        self.dangle_interpreter()
        self.assertFalse(_paths.part_ready("venv", self.root()))

    def test_part_ready_when_pyvenv_home_exists_but_the_interpreter_is_gone(self):
        """Debian: `home = /usr/bin` continua existindo depois de remover o python3.X."""
        self.install()
        (self.venv() / "pyvenv.cfg").write_text(f"home = {self.base}\nversion = 3\n", encoding="utf-8")
        self.assertTrue(_paths.part_ready("venv", self.root()))
        self.dangle_interpreter()
        self.assertFalse(_paths.part_ready("venv", self.root()))

    def test_part_ready_needs_the_marker_python(self):
        self.install()
        path = _paths.marker_path("venv", self.root())
        marker = json.loads(path.read_text(encoding="utf-8"))
        marker["python"] = str(self.base / "nonexistent" / "python3")
        path.write_text(json.dumps(marker), encoding="utf-8")
        self.assertFalse(_paths.part_ready("venv", self.root()))

    def test_setup_rebuilds_a_venv_whose_interpreter_is_gone(self):
        self.install()
        self.dangle_interpreter()
        self.runner.calls.clear()
        result = self.install()
        self.assertEqual("installed", self.venv_entry(result)["status"])
        self.assertEqual(["venv", "pip", "probe", "version"], self.labels(("venv", "pip", "probe", "version")))
        self.assertTrue(_paths.part_ready("venv", self.root()))


class HealthProbeTests(_Gate):
    def test_setup_rebuilds_when_the_venv_cannot_import_yt_dlp(self):
        self.install()
        self.runner.calls.clear()
        self.runner.fail[bootstrap.HEALTH_LABEL] = (1, ["ModuleNotFoundError: No module named 'yt_dlp'"])
        result = self.install()
        self.assertEqual("installed", self.venv_entry(result)["status"])
        self.assertIn("venv", self.runner.labels())

    def test_health_probe_is_the_isolated_venv_python(self):
        self.install()
        _label, argv, _cwd, env = self.runner.health_calls()[0]
        self.assertEqual([str(self.venv() / PYTHON_LAYOUT), "-I", "-c", "import yt_dlp"], argv)
        self.assertNotIn("PYTHONPATH", env)

    def test_check_reports_a_broken_venv_even_with_a_global_yt_dlp(self):
        self.install()
        self.found["yt-dlp"] = self.global_ytdlp
        self.runner.health = 1
        result = bootstrap.check()
        step = _step(result, "ytdlp")
        self.assertIs(False, step["ok"], step)
        self.assertEqual(str((self.venv() / YTDLP_LAYOUT).resolve()), step["found"])
        self.assertEqual("venv", step["source"])
        self.assertIn("setup", step["note"])
        self.assertEqual([_paths.cli_prefix_text() + " setup"], step["commands"])
        self.assertIs(False, result["ready"])
        self.assertIn("ytdlp", result["summary"]["missing"])

    def test_doctor_agrees_with_check_on_a_broken_venv(self):
        self.install()
        self.found["yt-dlp"] = self.global_ytdlp
        self.runner.health = 1
        doctor = cli.main(["doctor"])
        self.assertIs(False, doctor["executables"]["yt-dlp"])
        self.assertIs(False, doctor["ready"])
        self.assertEqual(str((self.venv() / YTDLP_LAYOUT).resolve()), doctor["resolved"]["yt-dlp"])


class InUseTests(_Gate):
    def test_a_failed_venv_is_not_masked_by_a_global_yt_dlp(self):
        self.found["yt-dlp"] = self.global_ytdlp
        self.runner.fail["pip"] = (1, ["NewConnectionError: Failed to establish a new connection"])
        result = self.install()
        self.assertEqual("failed", self.venv_entry(result)["status"])
        step = _step(result, "ytdlp")
        self.assertIs(False, step["ok"], step)
        self.assertIn("rede", step["note"])
        self.assertIs(False, result["ready"])
        self.assertNotIn("Runtime pronto", result["summary"]["line"])
        self.assertEqual(4, cli.result_exit("setup", result))

    def test_a_global_yt_dlp_without_a_managed_venv_is_visible_as_path(self):
        self.found["yt-dlp"] = self.global_ytdlp
        step = _step(bootstrap.check(), "ytdlp")
        self.assertIs(True, step["ok"], step)
        self.assertEqual("path", step["source"])
        self.assertEqual(str(Path(self.global_ytdlp).resolve()), step["found"])
        self.assertIn("PATH", step["note"])

    def test_managed_venv_is_the_one_in_use_ahead_of_the_path(self):
        self.install()
        self.found["yt-dlp"] = self.global_ytdlp
        step = _step(bootstrap.check(), "ytdlp")
        self.assertIs(True, step["ok"], step)
        self.assertEqual("venv", step["source"])
        self.assertEqual(str((self.venv() / YTDLP_LAYOUT).resolve()), step["found"])

    def test_summary_never_says_ready_while_a_step_failed(self):
        self.found["playwright-cli"] = str(_touch(self.base / "global" / "playwright-cli"))
        self.runner.fail["npm"] = (1, ["npm ERR! network"])
        everything = {step: (sys.executable, None) for step, _name in bootstrap._step_names()}  # pylint: disable=protected-access
        with patch.object(bootstrap, "_resolved", return_value=everything):
            result = self.install()
        self.assertIs(False, _step(result, "playwright")["ok"])
        self.assertIs(False, result["ready"])
        self.assertFalse(result["summary"]["line"].startswith("Runtime pronto"), result["summary"]["line"])
        self.assertIn("playwright: failed", result["summary"]["line"])


class UpgradeReportingTests(_Gate):
    def setUp(self):
        super().setUp()
        # O pacote de mentira só tem os arquivos de dependência: o passo `data` não decide aqui.
        active = patch.object(bootstrap.commands, "missing_data_files", return_value=[])
        active.start()
        self.addCleanup(active.stop)

    def everything(self):
        return {step: (sys.executable, None) for step, _name in bootstrap._step_names()}  # pylint: disable=protected-access

    def test_failed_upgrade_and_failed_rebuild_report_the_real_state(self):
        self.install()
        self.runner.calls.clear()
        self.runner.fail["upgrade"] = (1, ["ERROR: Could not find a version"])
        self.runner.fail["pip"] = (1, ["NewConnectionError: Failed to establish a new connection"])
        result = self.install(upgrade="ytdlp")
        upgrade = result["upgrade"]
        self.assertEqual("failed", upgrade["status"])
        self.assertNotIn("foi refeita", upgrade["error"])
        self.assertIn("setup", upgrade["error"])
        self.assertEqual("failed", self.venv_entry(result)["status"])
        self.assertFalse(self.venv().exists())
        self.assertIs(False, _step(result, "ytdlp")["ok"])
        self.assertIs(False, result["ready"])
        self.assertEqual(4, cli.result_exit("setup", result))

    def test_failed_upgrade_with_a_good_rebuild_exits_1(self):
        self.install()
        self.runner.fail["upgrade"] = (1, ["ERROR: Could not find a version"])
        with patch.object(bootstrap, "_resolved", return_value=self.everything()):
            result = self.install(upgrade="ytdlp")
        self.assertEqual("failed", result["upgrade"]["status"])
        self.assertIn("fixada", result["upgrade"]["error"])
        self.assertIs(True, result["ready"])
        self.assertEqual(1, cli.result_exit("setup", result))
        self.assertFalse(result["summary"]["line"].startswith("Runtime pronto"))

    def test_unchanged_version_is_not_upgraded(self):
        self.install()
        with patch.object(bootstrap, "_resolved", return_value=self.everything()):
            result = self.install(upgrade="ytdlp")
        upgrade = result["upgrade"]
        self.assertEqual("unchanged", upgrade["status"])
        self.assertEqual(self.runner.version, upgrade["version"])
        self.assertTrue(upgrade["warnings"])
        marker = _paths.read_marker("venv", self.root())
        assert marker is not None
        self.assertEqual("ready", marker["status"])
        self.assertNotIn("upgraded", marker)
        self.assertEqual(0, cli.result_exit("setup", result))

    def test_changed_version_is_upgraded_with_the_previous_one(self):
        self.install()
        self.runner.upgrade_to = "2026.9.30"
        result = self.install(upgrade="ytdlp")
        upgrade = result["upgrade"]
        self.assertEqual("upgraded", upgrade["status"])
        self.assertEqual(("2026.8.19", "2026.9.30"), (upgrade["previous"], upgrade["version"]))


class LockAndLinkTests(_Gate):
    def test_busy_runtime_is_the_same_locked_error_as_a_busy_project(self):
        root = self.root()
        root.mkdir(parents=True)
        with (root / bootstrap.LOCK_NAME).open("a+", encoding="utf-8") as held:
            runtime._acquire_lock(held)  # pylint: disable=protected-access
            try:
                with self.assertRaises(LockedError) as caught:
                    self.install()
            finally:
                runtime._release_lock(held)  # pylint: disable=protected-access
        self.assertEqual("LOCKED", runtime.error_code_for(caught.exception))
        self.assertEqual(1, runtime.exit_code_for("LOCKED"))

    def test_project_lock_raises_locked_error(self):
        project = self.base / "proj"
        with runtime.project_lock(project), self.assertRaises(LockedError) as caught, runtime.project_lock(project):
            pass
        self.assertEqual("LOCKED", runtime.error_code_for(caught.exception))

    @unittest.skipIf(WINDOWS, "links simbólicos exigem privilégio no Windows")
    def test_a_symlinked_runtime_root_is_refused(self):
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        self.root().parent.mkdir(parents=True)
        self.root().symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(UsageError) as caught:
            self.install()
        self.assertIn("link", str(caught.exception))
        self.assertEqual([], list(elsewhere.iterdir()))
        self.assertEqual([], self.runner.labels())


class NpmEnvTests(_Gate):
    def test_npm_keeps_npm_token_but_other_steps_do_not(self):
        os.environ["NPM_TOKEN"] = "npm-secret"
        self.install()
        for label, _argv, _cwd, env in self.runner.calls:
            if label == "npm":
                self.assertEqual("npm-secret", env["NPM_TOKEN"])
                self.assertNotIn("X_TOKEN", env)
            else:
                self.assertNotIn("NPM_TOKEN", env, label)


class KillTreeTests(unittest.TestCase):
    def test_windows_taskkill_errors_are_suppressed(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 4242
        for error in (subprocess.TimeoutExpired("taskkill", 30), OSError("taskkill ausente")):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(bootstrap.os, "name", "nt"),
                patch.object(bootstrap.subprocess, "run", side_effect=error),
            ):
                bootstrap._kill_tree(proc)  # pylint: disable=protected-access
            proc.kill.assert_called()


if __name__ == "__main__":
    unittest.main()


class CheckAgreesWithSetupTests(_Gate):
    """`setup --check`/`doctor` dizem "não pronta" exatamente quando o `setup` refaria a venv."""

    def assert_not_ready(self):
        step = _step(bootstrap.check(), "ytdlp")
        self.assertIs(False, step["ok"], step)
        self.assertIn("setup", step["note"])
        self.assertIs(False, cli.main(["doctor"])["executables"]["yt-dlp"])

    def test_healthy_ready_venv_is_ok(self):
        self.install()
        self.assertIs(True, _step(bootstrap.check(), "ytdlp")["ok"])

    def test_pyvenv_home_pointing_to_a_missing_folder(self):
        self.install()
        (self.venv() / "pyvenv.cfg").write_text(f"home = {self.base / 'gone'}\nversion = 3\n", encoding="utf-8")
        self.assertEqual(0, bootstrap.venv_health(self.venv()).returncode)  # a sonda sozinha passaria
        self.assert_not_ready()
        self.runner.calls.clear()
        self.assertEqual("installed", self.venv_entry(self.install())["status"])  # e o setup refaz

    def test_marker_left_at_upgrading_with_a_working_venv(self):
        self.install()
        _paths.write_marker("venv", self.root(), "upgrading")
        self.assert_not_ready()
        self.assertEqual("installed", self.venv_entry(self.install())["status"])

    def test_marker_left_at_building(self):
        self.install()
        _paths.write_marker("venv", self.root(), "building")
        self.assert_not_ready()
        self.assertEqual("installed", self.venv_entry(self.install())["status"])

    def test_a_venv_without_marker_is_judged_by_the_probe_alone(self):
        self.install()
        _paths.marker_path("venv", self.root()).unlink()
        self.assertIsNone(bootstrap.ytdlp_problem("venv", str(self.venv() / YTDLP_LAYOUT)))


class ClassifyPipTests(unittest.TestCase):
    def classify(self, *lines, code=1):
        return bootstrap._classify_pip(bootstrap.StepResult(code, lines))  # pylint: disable=protected-access

    def test_requires_python_blames_the_python_version(self):
        message = self.classify(
            "ERROR: Package 'yt-dlp' requires a different Python: 3.9.6 not in '>=3.10'",
        )
        self.assertIn("Python", message)
        self.assertIn("3.11", message)
        self.assertNotEqual(bootstrap.PIP_CONFLICT_FAILED, message)

    def test_ignored_versions_with_requires_python(self):
        message = self.classify(
            "INFO: Ignored versions that require a different python version: 2.0 Requires-Python >=3.14",
            "ERROR: No matching distribution found for yt-dlp==2026.8.19",
        )
        self.assertTrue(message.startswith("Falha ao instalar as dependências Python: seu Python"))

    def test_no_matching_distribution_with_a_python_tag_hint(self):
        message = self.classify(
            "ERROR: Could not find a version that satisfies the requirement wheel==1 (from versions: none)",
            "ERROR: No matching distribution found for wheel==1",
            "  (no wheel for tag cp315-cp315-macosx_14_0_arm64)",
        )
        self.assertIn("seu Python", message)

    def test_no_matching_distribution_alone_is_not_blamed_on_python(self):
        message = self.classify("ERROR: No matching distribution found for yt-dlp==0.0.0")
        self.assertNotIn("seu Python", message)

    def test_resolution_conflict_has_its_own_message(self):
        message = self.classify(
            "ERROR: Cannot install yt-dlp and requests==2.0 because these versions have conflicting dependencies.",
            "ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/en/latest/topics/dependency-resolution/",
        )
        self.assertEqual(bootstrap.PIP_CONFLICT_FAILED, message)

    def test_constraint_file_conflict(self):
        message = self.classify("ERROR: Double requirement given: a==1 (from -c constraints.txt)", "constraint file")
        self.assertEqual(bootstrap.PIP_CONFLICT_FAILED, message)

    def test_network_and_generic_classes_stay(self):
        self.assertEqual(
            bootstrap.PIP_NETWORK_FAILED, self.classify("WARNING: Retrying ... NewConnectionError: Failed to establish")
        )
        self.assertEqual(bootstrap.PIP_FAILED.format(code=2), self.classify("boom", code=2))
