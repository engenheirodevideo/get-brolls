"""`getbrolls setup` instala a venv do yt-dlp no runtime compartilhado, sem rede nos testes.

Um `FakeRunner` toma o lugar de `bootstrap.run_step` (o único ponto que abre processo):
registra cada chamada e cria os arquivos que o passo real criaria. `run_step` em si é
testado à parte, com processos Python de verdade e sem rede.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV, ROOT

from getbrolls import _paths, bootstrap, cli, runtime
from getbrolls.errors import UsageError

WINDOWS = os.name == "nt"
PYTHON_LAYOUT = "Scripts/python.exe" if WINDOWS else "bin/python"
YTDLP_LAYOUT = "Scripts/yt-dlp.exe" if WINDOWS else "bin/yt-dlp"
_CLEARED = ("GB_HOME", "GETBROLLS_HOME", "GB_RUNTIME_DIR", "GB_ENV_FILE", "GB_YTDLP_PATH", "GB_VENV_PATH")


def _touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fake_wheel(base):
    """Pacote instalado com os arquivos de dependência reais do repositório."""
    pkg = base / "site" / "getbrolls"
    data = pkg / "_data"
    _touch(data / "MANIFEST", "")
    for name in ("requirements.txt", "package.json", "package-lock.json"):
        shutil.copyfile(ROOT / name, data / name)
    return _paths.detect(pkg)


def _make_venv(folder):
    """O que `python -m venv` deixa e o `part_ready` confere."""
    _touch(folder / PYTHON_LAYOUT)
    _touch(folder / "pyvenv.cfg", f"home = {Path(sys.executable).parent}\nversion = 3\n")


class FakeRunner:
    """Substituto de `run_step`: registra `(label, argv, cwd, env)` e simula cada passo."""

    def __init__(self, fail=None, interrupt=None, version="2026.8.19"):
        self.calls = []
        self.fail = dict(fail or {})
        self.interrupt = interrupt
        self.version = version
        self.markers = []  # status do marcador visto em cada chamada de pip

    def labels(self):
        return [call[0] for call in self.calls]

    def __call__(self, argv, *, cwd=None, env=None, timeout, label):
        del timeout
        self.calls.append((label, list(argv), cwd, env))
        if label == self.interrupt:
            raise KeyboardInterrupt
        if label in self.fail:
            code, output = self.fail.pop(label)
            return bootstrap.StepResult(code, tuple(output))
        if label == "venv":
            _make_venv(Path(argv[-1]))
        elif label in ("pip", "upgrade"):
            venv = Path(argv[0]).parents[1]
            marker = _paths.read_marker("venv", venv.parent) or {}
            self.markers.append(marker.get("status"))
            _touch(venv / YTDLP_LAYOUT)
        elif label == "version":
            return bootstrap.StepResult(0, (self.version,))
        return bootstrap.StepResult(0, ())


class _Setup(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with  # limpo no addCleanup
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home = self.base / "home"
        self.inst = _fake_wheel(self.base)
        self.data = self.base / "site" / "getbrolls" / "_data"
        env = {key: value for key, value in os.environ.items() if key not in _CLEARED}
        env.update(GB_HOME=str(self.home), PYTHONPATH="p")
        env.update({"PEXELS_API_KEY": "k", "X_TOKEN": "t"})
        self.runner = FakeRunner()
        for active in (
            patch.object(_paths, "install", return_value=self.inst),
            patch.dict(os.environ, env, clear=True),
            patch.dict(_paths._PROFILE_ENV, {}, clear=True),  # pylint: disable=protected-access
            patch.object(bootstrap, "run_step", side_effect=self._run_step),
        ):
            active.start()
            self.addCleanup(active.stop)

    def _run_step(self, *args, **kwargs):
        return self.runner(*args, **kwargs)

    def root(self):
        return self.home / "runtime" / str(_paths.part_sha("venv"))

    def venv_entry(self, result):
        return next(entry for entry in result["installed"] if entry["part"] == "venv")

    def install(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return bootstrap.install(**kwargs)


class InstallTests(_Setup):
    def test_setup_installs_the_venv_into_the_shared_runtime(self):
        result = self.install()
        entry = self.venv_entry(result)
        self.assertEqual("installed", entry["status"], entry)
        self.assertEqual(str(self.root() / ".venv"), entry["path"])
        self.assertEqual("gb_home", entry["source"])
        marker = _paths.read_marker("venv", self.root())
        assert marker is not None
        self.assertEqual("ready", marker["status"])
        self.assertEqual(_paths.part_sha("venv"), marker["sha"])
        self.assertEqual(self.root() / ".venv", _paths.venv_dir().path)
        self.assertEqual("gb_home", _paths.venv_dir().source)
        self.assertTrue(_paths.runtime_info()["venv"]["managed"])
        self.assertEqual(["venv", "pip", "probe", "version"], self.runner.labels())

    def test_tools_are_a_pending_step_not_an_error(self):
        result = self.install()
        tools = next(entry for entry in result["installed"] if entry["part"] == "tools")
        self.assertEqual("pending", tools["status"])
        self.assertIsNone(tools["error"])
        self.assertIn("playwright", result["summary"]["line"])

    def test_missing_tools_exit_4(self):
        found: dict[str, tuple[str | None, str | None]] = {
            step: (sys.executable, None) for step, _name in bootstrap._step_names()
        }  # pylint: disable=protected-access
        found["playwright"] = (None, None)
        with patch.object(bootstrap, "_resolved", return_value=found):
            result = self.install()
        self.assertIs(False, result["ready"])
        self.assertEqual(4, cli.result_exit("setup", result))
        self.assertEqual("installed", self.venv_entry(result)["status"])

    def test_setup_is_idempotent(self):
        self.install()
        self.runner.calls.clear()
        result = self.install()
        self.assertEqual("already", self.venv_entry(result)["status"])
        self.assertEqual([], self.runner.labels())

    def test_venv_uses_the_running_interpreter(self):
        self.install()
        label, argv, _cwd, _env = self.runner.calls[0]
        self.assertEqual("venv", label)
        self.assertEqual([sys.executable, "-m", "venv"], argv[:3])
        self.assertEqual(str(self.root() / ".venv"), argv[-1])

    def test_pip_installs_the_recorded_copy_of_requirements(self):
        self.install()
        _label, argv, _cwd, _env = next(call for call in self.runner.calls if call[0] == "pip")
        recorded = Path(argv[argv.index("-r") + 1])
        self.assertEqual(self.root() / "requirements.txt", recorded)
        self.assertEqual((self.data / "requirements.txt").read_bytes(), recorded.read_bytes())
        self.assertEqual(str(self.root() / ".venv" / PYTHON_LAYOUT), argv[0])
        for flag in ("--disable-pip-version-check", "--no-input"):
            self.assertIn(flag, argv)
        self.assertNotIn("--require-hashes", argv)

    def test_child_environment_drops_secrets_and_pythonpath(self):
        self.install()
        for _label, _argv, _cwd, env in self.runner.calls:
            self.assertNotIn("PEXELS_API_KEY", env)
            self.assertNotIn("X_TOKEN", env)
            self.assertNotIn("PYTHONPATH", env)
            self.assertIn("PATH", env)

    def test_low_disk_space_is_a_warning(self):
        usage = shutil._ntuple_diskusage(10, 10, 100)  # pyright: ignore[reportAttributeAccessIssue]  # pylint: disable=protected-access
        with patch.object(bootstrap.shutil, "disk_usage", return_value=usage):
            result = self.install()
        entry = self.venv_entry(result)
        self.assertEqual("installed", entry["status"])
        self.assertTrue(any("1 GiB" in warning for warning in entry["warnings"]), entry)

    def test_setup_never_touches_the_project_or_the_checkout(self):
        before = {name: (ROOT / name).exists() for name in (".venv", ".tools")}
        cwd = self.base / "cwd"
        cwd.mkdir()
        previous = Path.cwd()
        os.chdir(cwd)
        try:
            self.install()
        finally:
            os.chdir(previous)
        self.assertEqual(before, {name: (ROOT / name).exists() for name in (".venv", ".tools")})
        self.assertEqual([], list(cwd.iterdir()))
        for _label, argv, cwd_arg, _env in self.runner.calls:
            self.assertTrue(Path(cwd_arg).is_relative_to(self.home), cwd_arg)
            for arg in argv[1:]:
                if Path(arg).is_absolute():
                    self.assertTrue(Path(arg).is_relative_to(self.home), arg)

    def test_stdout_is_only_the_result_json(self):
        out, err = io.StringIO(), io.StringIO()
        with (
            patch.object(sys, "argv", ["getbrolls", "setup"]),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.entrypoint()
        payload = json.loads(out.getvalue())
        self.assertEqual(code, cli.result_exit("setup", payload))
        self.assertIn("getbrolls setup:", err.getvalue())
        self.assertEqual("installed", self.venv_entry(payload)["status"])


class FailureTests(_Setup):
    def failed(self, result):
        entry = self.venv_entry(result)
        self.assertEqual("failed", entry["status"], entry)
        self.assertFalse((self.root() / ".venv").exists())
        self.assertFalse(_paths.part_ready("venv", self.root()))
        self.assertIsNone(_paths.read_marker("venv", self.root()))
        self.assertFalse(_paths.runtime_info()["venv"]["managed"])
        self.assertEqual(4, cli.result_exit("setup", result))
        return entry

    def test_pip_failure_for_the_python_version(self):
        self.runner.fail["pip"] = (1, ["ERROR: Package 'x' requires a different Python: 3.10.0 not in '>=3.11'"])
        entry = self.failed(self.install())
        self.assertIn("validado em Python 3.11–3.13", entry["error"])
        self.assertIn("Package 'x'", "\n".join(entry["output_tail"]))

    def test_pip_failure_for_the_network_or_index(self):
        self.runner.fail["pip"] = (
            1,
            ["WARNING: Retrying ... NewConnectionError: Failed to establish a new connection"],
        )
        entry = self.failed(self.install())
        self.assertIn("rede", entry["error"])

    def test_missing_ensurepip_points_at_python3_venv(self):
        self.runner.fail["venv"] = (1, ["Error: ensurepip is not available. apt install python3.12-venv"])
        entry = self.failed(self.install())
        self.assertIn("python3-venv", entry["error"])
        self.assertNotIn("pip", self.runner.labels())

    def test_step_timeout_is_reported(self):
        self.runner.fail["pip"] = (124, ["baixando…"])
        entry = self.failed(self.install())
        self.assertIn("tempo", entry["error"])

    def test_probe_failure_is_a_failed_install(self):
        self.runner.fail["probe"] = (1, ["ModuleNotFoundError: No module named 'yt_dlp_ejs'"])
        entry = self.failed(self.install())
        self.assertTrue(entry["error"])

    def test_interrupted_build_is_rebuilt(self):
        root = self.root()
        _touch(root / ".venv" / "half-written")
        _paths.write_marker("venv", root, "building")
        result = self.install()
        self.assertEqual("installed", self.venv_entry(result)["status"])
        self.assertIn("venv", self.runner.labels())
        self.assertFalse((root / ".venv" / "half-written").exists())
        self.assertTrue(_paths.part_ready("venv", root))

    def test_keyboard_interrupt_propagates_and_leaves_no_ready_part(self):
        self.runner.interrupt = "pip"
        with self.assertRaises(KeyboardInterrupt):
            self.install()
        self.assertFalse(_paths.part_ready("venv", self.root()))
        marker = _paths.read_marker("venv", self.root())
        self.assertNotEqual("ready", (marker or {}).get("status"))

    def test_a_symlinked_part_is_never_removed(self):
        if WINDOWS:
            self.skipTest("symlink exige privilégio no Windows")
        target = self.base / "elsewhere"
        _touch(target / "keep")
        root = self.root()
        root.mkdir(parents=True)
        (root / ".venv").symlink_to(target, target_is_directory=True)
        _paths.write_marker("venv", root, "building")
        with self.assertRaises(UsageError) as caught:
            self.install()
        self.assertIn("link", str(caught.exception))
        self.assertTrue((target / "keep").exists())
        self.assertEqual([], self.runner.labels())


class ForeignAndPinTests(_Setup):
    def test_foreign_runtime_with_working_ytdlp_is_adopted(self):
        explicit = self.base / "explicit"
        _touch(explicit / ".venv" / YTDLP_LAYOUT)
        os.environ["GB_RUNTIME_DIR"] = str(explicit)
        result = self.install()
        self.assertEqual("adopted", self.venv_entry(result)["status"])
        self.assertEqual([], self.runner.labels())
        self.assertIsNone(_paths.read_marker("venv", explicit))

    def test_foreign_broken_runtime_is_a_usage_error_and_nothing_is_deleted(self):
        explicit = self.base / "explicit"
        _touch(explicit / ".venv" / "something")
        os.environ["GB_RUNTIME_DIR"] = str(explicit)
        with self.assertRaises(UsageError) as caught:
            self.install()
        self.assertIn("GB_RUNTIME_DIR", str(caught.exception))
        self.assertTrue((explicit / ".venv" / "something").exists())
        self.assertEqual([], self.runner.labels())

    def test_explicit_dir_of_another_version_is_refused(self):
        explicit = self.base / "explicit"
        _make_venv(explicit / ".venv")
        _touch(explicit / ".venv" / YTDLP_LAYOUT)
        _paths.write_marker("venv", explicit, "ready")
        marker = json.loads(_paths.marker_path("venv", explicit).read_text(encoding="utf-8"))
        marker["sha"] = "0" * 16
        _paths.marker_path("venv", explicit).write_text(json.dumps(marker), encoding="utf-8")
        os.environ["GB_RUNTIME_DIR"] = str(explicit)
        with self.assertRaises(UsageError) as caught:
            self.install()
        self.assertIn("outra versão", str(caught.exception))
        self.assertTrue((explicit / ".venv" / YTDLP_LAYOUT).exists())
        self.assertEqual([], self.runner.labels())

    def test_pinned_ytdlp_is_not_managed(self):
        os.environ["GB_YTDLP_PATH"] = sys.executable
        result = self.install()
        entry = self.venv_entry(result)
        self.assertEqual("pinned", entry["status"])
        self.assertIn("GB_YTDLP_PATH", entry["note"])
        self.assertEqual([], self.runner.labels())
        self.assertFalse((self.home / "runtime").exists())

    def test_second_setup_refuses_while_the_root_is_locked(self):
        root = self.root()
        root.mkdir(parents=True)
        with (root / bootstrap.LOCK_NAME).open("a+", encoding="utf-8") as held:
            runtime._acquire_lock(held)  # pylint: disable=protected-access
            try:
                with self.assertRaises(ValueError) as caught:
                    self.install()
            finally:
                runtime._release_lock(held)  # pylint: disable=protected-access
        self.assertIn("Outro `setup`", str(caught.exception))
        self.assertEqual([], self.runner.labels())


class UpgradeTests(_Setup):
    def test_upgrade_ytdlp_updates_the_managed_venv_and_records_it(self):
        self.runner.version = "2026.9.1"
        result = self.install(upgrade="ytdlp")
        _label, argv, _cwd, _env = next(call for call in self.runner.calls if call[0] == "upgrade")
        for arg in ("--upgrade", "yt-dlp[default]", "yt-dlp-ejs", "--disable-pip-version-check", "--no-input"):
            self.assertIn(arg, argv)
        self.assertEqual(["building", "upgrading"], self.runner.markers)
        marker = _paths.read_marker("venv", self.root())
        assert marker is not None
        self.assertEqual("ready", marker["status"])
        self.assertEqual("2026.9.1", marker["upgraded"]["yt-dlp"])
        self.assertEqual("upgraded", result["upgrade"]["status"])
        self.assertEqual("2026.9.1", result["upgrade"]["version"])

    def test_failed_upgrade_rebuilds_the_pinned_set(self):
        self.install()
        self.runner.calls.clear()
        self.runner.fail["upgrade"] = (1, ["ERROR: Could not find a version"])
        result = self.install(upgrade="ytdlp")
        self.assertEqual("failed", result["upgrade"]["status"])
        self.assertIn("fixada", result["upgrade"]["error"])
        self.assertEqual(["upgrade", "venv", "pip", "probe", "version"], self.runner.labels())
        marker = _paths.read_marker("venv", self.root())
        assert marker is not None
        self.assertEqual("ready", marker["status"])
        self.assertNotIn("upgraded", marker)
        self.assertIn("upgrade", result["summary"]["line"])

    def test_upgrade_with_a_pin_is_a_usage_error(self):
        os.environ["GB_YTDLP_PATH"] = sys.executable
        with self.assertRaises(UsageError) as caught:
            self.install(upgrade="ytdlp")
        self.assertIn("GB_YTDLP_PATH", str(caught.exception))
        self.assertEqual([], self.runner.labels())


class BatchGuardTests(unittest.TestCase):
    def test_batch_guard_refuses_non_constant_args(self):
        with self.assertRaises(RuntimeError):
            bootstrap._assert_batch_safe([r"C:\n\npm.cmd", "ci", "--prefix", "C:/a&b"])  # pylint: disable=protected-access
        with self.assertRaises(RuntimeError):
            bootstrap._assert_batch_safe([r"C:\n\npm.CMD", "ci", "a b"])  # pylint: disable=protected-access
        bootstrap._assert_batch_safe([r"C:\n\npm.cmd", "ci", "--ignore-scripts", "--no-audit", "--no-fund"])  # pylint: disable=protected-access
        bootstrap._assert_batch_safe([sys.executable, "-c", "print('a&b')"])  # pylint: disable=protected-access

    def test_run_step_applies_the_guard(self):
        with self.assertRaises(RuntimeError):
            bootstrap.run_step(["tool.bat", "a|b"], timeout=5, label="x")


class RunStepTests(unittest.TestCase):
    def test_output_tail_merges_stderr_and_keeps_the_last_lines(self):
        code = (
            "import sys\nfor i in range(60): print(i)\nsys.stdout.flush()\nprint('err', file=sys.stderr)\nsys.exit(3)"
        )
        done = bootstrap.run_step([sys.executable, "-c", code], timeout=60, label="t")
        self.assertEqual(3, done.returncode)
        self.assertEqual(bootstrap.TAIL_LINES, len(done.output))
        self.assertEqual("err", done.output[-1])
        self.assertEqual("59", done.output[-2])

    def test_missing_executable_is_127(self):
        done = bootstrap.run_step([str(Path(tempfile.gettempdir()) / "no-such-tool-gb")], timeout=5, label="t")
        self.assertEqual(127, done.returncode)

    def test_timeout_kills_the_whole_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "grandchild.pid"
            code = (
                "import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                "print('started', flush=True)\n"
                "time.sleep(60)\n"
            )
            started = time.monotonic()
            done = bootstrap.run_step([sys.executable, "-c", code], timeout=3, label="t")
            self.assertLess(time.monotonic() - started, 30)
            self.assertEqual(124, done.returncode)
            self.assertIn("started", done.output)
            grandchild = int(pid_file.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and _alive(grandchild):
                time.sleep(0.2)
            self.assertFalse(_alive(grandchild))


def _alive(pid):
    if WINDOWS:
        done = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False, timeout=30
        )
        return str(pid) in done.stdout
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
        if waited == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class SetupFlagTests(unittest.TestCase):
    def test_setup_flags_are_mutually_exclusive(self):
        done = subprocess.run(
            [*CLI_ARGV, "setup", "--check", "--upgrade", "ytdlp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual("USAGE_ERROR", json.loads(done.stderr)["error_code"])
        self.assertEqual("", done.stdout)


if __name__ == "__main__":
    unittest.main()
