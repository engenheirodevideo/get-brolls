"""`getbrolls setup` instala a venv do yt-dlp e o Playwright CLI no runtime compartilhado, sem rede nos testes.

Um `FakeRunner` toma o lugar de `bootstrap.run_step` (o único ponto que abre processo):
registra cada chamada e cria os arquivos que o passo real criaria. `run_step` em si é
testado à parte, com processos Python de verdade e sem rede.
"""

import contextlib
import hashlib
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
CLI_NAMES = ("playwright-cli", "playwright-cli.cmd") if WINDOWS else ("playwright-cli",)
VENV_LABELS = ("venv", "pip", "probe", "version", "upgrade")
TOOLS_LABELS = ("node", "npm", "playwright")
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


def _make_tools(folder):
    """O que o `npm ci` deixa: o Playwright CLI em `node_modules/.bin`."""
    for name in CLI_NAMES:
        _touch(folder / "node_modules" / ".bin" / name)


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
        self.node = "v22.11.0"
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
        elif label == "node":
            return bootstrap.StepResult(0, (self.node,))
        elif label == "npm":
            _make_tools(Path(str(cwd)))
        elif label == "playwright":
            return bootstrap.StepResult(0, ("0.1.21",))
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
        self.npm = str(self.base / "bin" / ("npm.cmd" if WINDOWS else "npm"))
        node = str(self.base / "bin" / ("node.exe" if WINDOWS else "node"))
        self.found: dict[str, str | None] = {"npm": self.npm, "node": node}
        real_which = shutil.which

        def which(name, *args, **kwargs):
            return self.found[name] if name in self.found else real_which(name, *args, **kwargs)

        for active in (
            patch.object(bootstrap.shutil, "which", side_effect=which),
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

    def tools_root(self):
        return self.home / "runtime" / str(_paths.part_sha("tools"))

    def venv_entry(self, result):
        return next(entry for entry in result["installed"] if entry["part"] == "venv")

    def tools_entry(self, result):
        return next(entry for entry in result["installed"] if entry["part"] == "tools")

    def labels(self, group):
        return [label for label in self.runner.labels() if label in group]

    def install(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()):
            return bootstrap.install(**kwargs)


class InstallTests(_Setup):
    def test_setup_installs_both_parts_into_the_shared_runtime(self):
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
        tools = self.tools_entry(result)
        self.assertEqual("installed", tools["status"], tools)
        self.assertEqual("0.1.21", tools["version"])
        self.assertEqual(str(self.tools_root() / ".tools"), tools["path"])
        tools_marker = _paths.read_marker("tools", self.tools_root())
        assert tools_marker is not None
        self.assertEqual("ready", tools_marker["status"])
        self.assertEqual(_paths.part_sha("tools"), tools_marker["sha"])
        self.assertEqual(self.tools_root() / ".tools", _paths.tools_dir().path)
        self.assertTrue(_paths.runtime_info()["tools"]["managed"])
        self.assertEqual(["venv", "pip", "probe", "version", "node", "npm", "playwright"], self.runner.labels())

    def test_npm_argv_is_constant_and_runs_in_the_tools_dir(self):
        self.install()
        _label, argv, cwd, env = next(call for call in self.runner.calls if call[0] == "npm")
        tools = self.tools_root() / ".tools"
        self.assertEqual([self.npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], argv)
        self.assertEqual(tools, Path(cwd))
        self.assertTrue(Path(env["npm_config_cache"]).is_relative_to(self.tools_root()))
        self.assertEqual("false", env["npm_config_update_notifier"])
        for name in ("package.json", "package-lock.json"):
            self.assertEqual((self.data / name).read_bytes(), (tools / name).read_bytes())
        bootstrap._assert_batch_safe(argv)  # pylint: disable=protected-access

    def test_tools_key_is_the_sha_of_both_package_files(self):
        blob = (self.data / "package.json").read_bytes() + b"\0" + (self.data / "package-lock.json").read_bytes()
        self.assertEqual(hashlib.sha256(blob).hexdigest()[:16], self.tools_root().name)

    def test_missing_npm_skips_playwright_with_a_hint_and_exits_4(self):
        self.found["npm"] = None
        with patch.object(bootstrap, "_resolved", return_value=self.resolved_without_playwright()):
            result = self.install()
        tools = self.tools_entry(result)
        self.assertEqual("skipped", tools["status"])
        self.assertIn("npm", tools["error"])
        self.assertTrue(tools["hint"])
        self.assertEqual("installed", self.venv_entry(result)["status"])
        self.assertEqual([], self.labels(TOOLS_LABELS))
        self.assertFalse(self.tools_root().exists())
        self.assertIn("playwright: skipped", result["summary"]["line"])
        self.assertEqual(4, cli.result_exit("setup", result))

    def test_missing_node_skips_playwright(self):
        self.found["node"] = None
        result = self.install()
        tools = self.tools_entry(result)
        self.assertEqual("skipped", tools["status"])
        self.assertIn("Node 22+", tools["error"])
        self.assertEqual([], self.labels(TOOLS_LABELS))

    def test_node_older_than_22_skips_playwright(self):
        self.runner.node = "v20.11.0"
        result = self.install()
        tools = self.tools_entry(result)
        self.assertEqual("skipped", tools["status"])
        self.assertIn("Node 22+", tools["error"])
        self.assertIn("v20.11.0", tools["error"])
        self.assertEqual(["node"], self.labels(TOOLS_LABELS))
        self.assertIsNone(_paths.read_marker("tools", self.tools_root()))

    def resolved_without_playwright(self):
        found: dict[str, tuple[str | None, str | None]] = {
            step: (sys.executable, None)
            for step, _name in bootstrap._step_names()  # pylint: disable=protected-access
        }
        found["playwright"] = (None, None)
        return found

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
        self.assertEqual("already", self.tools_entry(result)["status"])
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
        self.assertEqual("installed", self.tools_entry(payload)["status"])


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
        self.assertEqual([], self.labels(VENV_LABELS))
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
        self.assertEqual([], self.labels(VENV_LABELS))
        self.assertFalse(self.root().exists())
        self.assertEqual("installed", self.tools_entry(result)["status"])

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


class ToolsTests(_Setup):
    def failed_tools(self, result):
        entry = self.tools_entry(result)
        self.assertEqual("failed", entry["status"], entry)
        self.assertFalse((self.tools_root() / ".tools").exists())
        self.assertIsNone(_paths.read_marker("tools", self.tools_root()))
        self.assertFalse(_paths.runtime_info()["tools"]["managed"])
        self.assertEqual("installed", self.venv_entry(result)["status"])
        self.assertIn("playwright: failed", result["summary"]["line"])
        self.assertEqual(4, cli.result_exit("setup", result))
        return entry

    def test_failed_npm_leaves_no_ready_tools(self):
        self.runner.fail["npm"] = (1, ["npm ERR! code ENOTFOUND", "npm ERR! network request failed"])
        entry = self.failed_tools(self.install())
        self.assertIn("npm ci", entry["error"])
        self.assertIn("ENOTFOUND", "\n".join(entry["output_tail"]))
        self.assertNotIn("playwright", self.runner.labels())

    def test_npm_timeout_is_reported(self):
        self.runner.fail["npm"] = (124, ["fetching…"])
        entry = self.failed_tools(self.install())
        self.assertIn("tempo", entry["error"])

    def test_playwright_probe_failure_is_a_failed_install(self):
        self.runner.fail["playwright"] = (1, ["Error: Cannot find module"])
        entry = self.failed_tools(self.install())
        self.assertIn("Playwright CLI", entry["error"])

    def test_interrupted_tools_build_is_rebuilt(self):
        root = self.tools_root()
        _touch(root / ".tools" / "node_modules" / "half-written")
        _paths.write_marker("tools", root, "building")
        result = self.install()
        self.assertEqual("installed", self.tools_entry(result)["status"])
        self.assertFalse((root / ".tools" / "node_modules" / "half-written").exists())
        self.assertTrue(_paths.part_ready("tools", root))

    def test_ready_marker_without_the_cli_is_rebuilt(self):
        self.install()
        for name in CLI_NAMES:
            (self.tools_root() / ".tools" / "node_modules" / ".bin" / name).unlink()
        self.runner.calls.clear()
        result = self.install()
        self.assertEqual("installed", self.tools_entry(result)["status"])
        self.assertEqual(["node", "npm", "playwright"], self.runner.labels())

    def test_keyboard_interrupt_in_npm_leaves_no_ready_tools(self):
        self.runner.interrupt = "npm"
        with self.assertRaises(KeyboardInterrupt):
            self.install()
        self.assertFalse(_paths.part_ready("tools", self.tools_root()))

    def test_foreign_tools_with_a_working_cli_are_adopted(self):
        explicit = self.base / "explicit"
        _touch(explicit / ".venv" / YTDLP_LAYOUT)
        _make_tools(explicit / ".tools")
        os.environ["GB_RUNTIME_DIR"] = str(explicit)
        result = self.install()
        self.assertEqual("adopted", self.tools_entry(result)["status"])
        self.assertEqual([], self.runner.labels())
        self.assertIsNone(_paths.read_marker("tools", explicit))

    def test_foreign_broken_tools_are_a_usage_error_and_nothing_is_deleted(self):
        explicit = self.base / "explicit"
        _touch(explicit / ".venv" / YTDLP_LAYOUT)
        _touch(explicit / ".tools" / "something")
        os.environ["GB_RUNTIME_DIR"] = str(explicit)
        with self.assertRaises(UsageError) as caught:
            self.install()
        self.assertIn(".tools", str(caught.exception))
        self.assertTrue((explicit / ".tools" / "something").exists())
        self.assertEqual([], self.runner.labels())

    def test_a_symlinked_tools_part_is_never_removed(self):
        if WINDOWS:
            self.skipTest("symlink exige privilégio no Windows")
        target = self.base / "elsewhere"
        _touch(target / "keep")
        root = self.tools_root()
        root.mkdir(parents=True)
        (root / ".tools").symlink_to(target, target_is_directory=True)
        _paths.write_marker("tools", root, "building")
        with self.assertRaises(UsageError) as caught:
            self.install()
        self.assertIn("link", str(caught.exception))
        self.assertTrue((target / "keep").exists())
        self.assertEqual([], self.labels(TOOLS_LABELS))

    def test_tools_lock_is_refused_while_held(self):
        root = self.tools_root()
        root.mkdir(parents=True)
        with (root / bootstrap.LOCK_NAME).open("a+", encoding="utf-8") as held:
            runtime._acquire_lock(held)  # pylint: disable=protected-access
            try:
                with self.assertRaises(ValueError) as caught:
                    self.install()
            finally:
                runtime._release_lock(held)  # pylint: disable=protected-access
        self.assertIn("Outro `setup`", str(caught.exception))
        self.assertEqual([], self.labels(TOOLS_LABELS))

    def test_runtime_is_shared_across_plugin_updates(self):
        self.install()
        venv, tools = self.root() / ".venv", self.tools_root() / ".tools"
        other = _fake_wheel(self.base / "after-update")
        with patch.object(_paths, "install", return_value=other):
            self.assertEqual(venv, _paths.venv_dir().path)
            self.assertEqual(tools, _paths.tools_dir().path)
            self.assertTrue(_paths.runtime_info()["tools"]["managed"])
            lock = self.base / "after-update" / "site" / "getbrolls" / "_data" / "package-lock.json"
            lock.write_bytes(lock.read_bytes() + b"\n")
            self.assertNotEqual(tools, _paths.runtime_target("tools").path)
            self.assertFalse(_paths.runtime_info()["tools"]["managed"])


class WhereTests(_Setup):
    def test_where_reports_without_installing(self):
        result = bootstrap.where("tools")
        target = _paths.runtime_target("tools")
        self.assertEqual("tools", result["part"])
        self.assertEqual(str(target.path), result["path"])
        self.assertEqual("gb_home", result["source"])
        self.assertIs(False, result["exists"])
        self.assertIs(False, result["managed"])
        self.assertIsNone(result["marker"])
        self.assertNotIn("ready", result)
        self.assertFalse(self.home.exists())
        self.assertEqual(0, cli.result_exit("setup", result))
        self.assertEqual([], self.runner.labels())

    def test_where_all_lists_both_parts_after_install(self):
        self.install()
        result = bootstrap.where()
        self.assertEqual(str(self.home), result["gb_home"])
        self.assertIs(False, result["explicit"])
        for part, folder in (("venv", self.root() / ".venv"), ("tools", self.tools_root() / ".tools")):
            info = result[part]
            self.assertEqual(str(folder), info["path"])
            self.assertEqual(str(folder.parent), info["root"])
            self.assertEqual("ready", info["marker"])
            self.assertIs(True, info["managed"])
            self.assertIs(True, info["exists"])
            self.assertEqual(_paths.part_sha(part), info["sha"])
            self.assertEqual(str(folder), info["in_use"]["path"])
            self.assertTrue(Path(info["in_use"]["executable"]).is_relative_to(folder))
        self.assertTrue(result["tools"]["in_use"]["executable"].endswith(CLI_NAMES[-1]))

    def test_where_without_data_files_does_not_raise(self):
        with patch.object(_paths, "part_sha", return_value=None):
            result = bootstrap.where()
        self.assertIsNone(result["venv"]["sha"])
        self.assertIs(False, result["tools"]["managed"])

    def test_where_is_tolerant_of_a_broken_resolution(self):
        with patch.object(_paths, "runtime_target", side_effect=ValueError("perfil quebrado")):
            result = bootstrap.where("venv")
        self.assertIn("perfil quebrado", result["error"])
        self.assertEqual(0, cli.result_exit("setup", result))


class SystemHintTests(unittest.TestCase):
    def test_system_tools_are_only_verified_with_os_hints(self):
        expected = {
            "darwin": "brew install ffmpeg",
            "linux": "sudo apt install ffmpeg",
            "win32": "winget install Gyan.FFmpeg",
        }
        for platform, hint in expected.items():
            with (
                self.subTest(platform=platform),
                patch.object(sys, "platform", platform),
                patch.object(bootstrap, "_resolved", return_value={}),
            ):
                result = bootstrap.check()
            ffmpeg = next(step for step in result["steps"] if step["id"] == "ffmpeg")
            self.assertEqual(hint, ffmpeg["hint"])
            self.assertEqual([], ffmpeg["commands"])
            for step in result["steps"]:
                if step["id"] in ("ffmpeg", "ffprobe", "curl", "node", "npx"):
                    self.assertTrue(step["hint"], step)

    def test_hint_table_covers_every_system_tool(self):
        for name in ("ffmpeg", "ffprobe", "curl", "node", "npx"):
            for platform in ("darwin", "linux", "nt"):
                self.assertTrue(bootstrap.system_hint(name, platform), (name, platform))
        self.assertIsNone(bootstrap.system_hint("yt-dlp", "linux"))


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

    def test_where_cannot_be_combined_with_check(self):
        done = _cli("setup", "--where", "tools", "--check")
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual("USAGE_ERROR", json.loads(done.stderr)["error_code"])

    def test_where_from_the_cli_is_json_and_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            done = _cli("setup", "--where", env={"GB_HOME": str(home)})
            self.assertEqual(0, done.returncode, done.stderr)
            payload = json.loads(done.stdout)
            self.assertEqual({"venv", "tools"}, {"venv", "tools"} & set(payload))
            self.assertTrue(Path(payload["tools"]["path"]).is_relative_to(home / "runtime"))
            only = json.loads(_cli("setup", "--where", "venv", env={"GB_HOME": str(home)}).stdout)
            self.assertEqual("venv", only["part"])
            self.assertFalse((home / "runtime").exists())


def _cli(*args, env=None):
    merged = {key: value for key, value in os.environ.items() if key != "GB_RUNTIME_DIR"}
    merged.update(env or {})
    return subprocess.run(
        [*CLI_ARGV, *args], capture_output=True, text=True, encoding="utf-8", env=merged, timeout=120, check=False
    )


if __name__ == "__main__":
    unittest.main()
