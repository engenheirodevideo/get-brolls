"""Servidor de fundo no Windows e no POSIX, por simulação: liveness (`OpenProcess` +
`GetExitCodeProcess`), flags do `Popen`, `taskkill`, e o escalonamento SIGTERM -> SIGKILL."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _posix
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import _paths, serve

STILL_ACTIVE = 259


class _CodeBox:
    """Dublê de `ctypes.c_ulong`: o `GetExitCodeProcess` falso escreve o código aqui."""

    def __init__(self):
        self.value = 0


def _fake_ctypes(*, handle=77, exit_code=STILL_ACTIVE, ok=1):
    kernel32 = MagicMock()
    kernel32.OpenProcess.return_value = handle

    def get_exit_code(_handle, box):
        box.value = exit_code
        return ok

    kernel32.GetExitCodeProcess.side_effect = get_exit_code
    module = types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=kernel32), c_ulong=_CodeBox, byref=lambda box: box
    )
    return module, kernel32


def _project(root):
    brolls = Path(root) / "brolls"
    brolls.mkdir(parents=True)
    (brolls / "review.html").write_text("<html>storyboard</html>", encoding="utf-8")
    return Path(root)


class FakeClock:
    """Relógio que avança 1 s por leitura e dorme de mentira: o laço de espera termina na hora."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = 0

    def monotonic(self):
        self.now += 1.0
        return self.now

    def sleep(self, _seconds):
        self.sleeps += 1


class OsNameSeamTests(unittest.TestCase):
    def test_the_windows_check_follows_the_paths_os_name(self):
        with patch.object(_paths, "_os_name", return_value="nt"):
            self.assertTrue(serve._windows())
        with patch.object(_paths, "_os_name", return_value="posix"):
            self.assertFalse(serve._windows())
        self.assertEqual(os.name == "nt", serve._windows())


class WindowsAliveTests(unittest.TestCase):
    def _alive(self, **fake):
        module, kernel32 = _fake_ctypes(**fake)
        with patch.object(serve, "_windows", return_value=True), patch.dict(sys.modules, {"ctypes": module}):
            return serve._alive(4321), kernel32

    def test_a_still_active_process_is_alive_and_the_handle_is_closed(self):
        alive, kernel32 = self._alive(exit_code=STILL_ACTIVE)
        self.assertTrue(alive)
        self.assertEqual((0x1000, False, 4321), kernel32.OpenProcess.call_args.args)
        kernel32.CloseHandle.assert_called_once_with(77)

    def test_an_exited_process_is_not_alive(self):
        for code in (0, 1, 3221225786):
            with self.subTest(code=code):
                alive, kernel32 = self._alive(exit_code=code)
                self.assertFalse(alive)
                kernel32.CloseHandle.assert_called_once_with(77)

    def test_a_failed_exit_code_query_is_not_alive_even_with_the_active_code(self):
        alive, kernel32 = self._alive(exit_code=STILL_ACTIVE, ok=0)
        self.assertFalse(alive)
        kernel32.CloseHandle.assert_called_once_with(77)

    def test_a_pid_that_cannot_be_opened_is_not_alive_and_nothing_is_closed(self):
        alive, kernel32 = self._alive(handle=0)
        self.assertFalse(alive)
        kernel32.GetExitCodeProcess.assert_not_called()
        kernel32.CloseHandle.assert_not_called()

    def test_windows_never_signals_the_process_to_ask_whether_it_lives(self):
        module, _kernel32 = _fake_ctypes()
        with (
            patch.object(serve, "_windows", return_value=True),
            patch.dict(sys.modules, {"ctypes": module}),
            patch.object(serve.os, "kill") as killed,
        ):
            serve._alive(4321)
        killed.assert_not_called()

    def test_reap_does_nothing_on_windows(self):
        with patch.object(serve, "_windows", return_value=True), patch.object(serve.os, "waitpid") as waited:
            self.assertIsNone(serve._reap(4321))
        waited.assert_not_called()


class StartBackgroundPlatformTests(unittest.TestCase):
    def _start(self, windows):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            captured = {}

            def fake_popen(command, **kwargs):
                captured["command"] = command
                captured["kwargs"] = kwargs
                (root / "brolls" / serve.LOG_FILE).write_text('{"port": 5555, "session": "s"}\n', encoding="utf-8")
                process = MagicMock()
                process.pid = 31337
                process.poll.return_value = None
                return process

            with (
                patch.object(serve, "_windows", return_value=windows),
                patch.object(serve.subprocess, "Popen", side_effect=fake_popen),
            ):
                result = serve.start_background(root, port=0)
            record = json.loads((root / "brolls" / serve.PID_FILE).read_text(encoding="utf-8"))
        return result, record, captured

    def test_windows_detaches_the_child_from_the_console_and_the_process_group(self):
        result, record, captured = self._start(windows=True)
        self.assertEqual(0x00000008 | 0x00000200, captured["kwargs"]["creationflags"])
        self.assertNotIn("start_new_session", captured["kwargs"])
        self.assertEqual(31337, record["pid"])
        self.assertEqual(5555, result["port"])

    def test_posix_starts_a_new_session_instead(self):
        _result, _record, captured = self._start(windows=False)
        self.assertIs(True, captured["kwargs"]["start_new_session"])
        self.assertNotIn("creationflags", captured["kwargs"])
        self.assertEqual(subprocess.DEVNULL, captured["kwargs"]["stdin"])


class WindowsNeverReachesPosixNamesTests(unittest.TestCase):
    """With `os.WNOHANG` and `signal.SIGKILL` gone, as on Windows, the Windows path still works."""

    def test_reap_returns_before_waitpid(self):
        with (
            _posix.without_posix_names(),
            patch.object(serve, "_windows", return_value=True),
            patch.object(serve.os, "waitpid") as waited,
        ):
            self.assertFalse(hasattr(os, "WNOHANG"))
            self.assertIsNone(serve._reap(55))
        waited.assert_not_called()

    def test_stop_of_a_surviving_process_uses_taskkill_and_never_sigkill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            pid_file = root / "brolls" / serve.PID_FILE
            pid_file.write_text(json.dumps({"pid": 4321, "port": 9, "session": "s"}), encoding="utf-8")
            with (
                _posix.without_posix_names(),
                patch.object(serve, "_windows", return_value=True),
                patch.object(serve, "_ours", return_value=True),
                patch.object(serve, "_alive", return_value=True),
                patch.object(serve, "time", FakeClock()),
                patch.object(serve.effects, "run") as ran,
                patch.object(serve.effects, "kill") as killed,
            ):
                self.assertFalse(hasattr(signal, "SIGKILL"))
                result = serve.stop(root)
        self.assertEqual("taskkill", ran.call_args.args[0][0])
        killed.assert_not_called()
        self.assertEqual({"stopped": False, "reason": None, "pid": 4321}, result)


class StopTests(unittest.TestCase):
    def _stopping(self, root, pid=4321):
        pid_file = root / "brolls" / serve.PID_FILE
        pid_file.write_text(json.dumps({"pid": pid, "port": 9, "session": "s"}), encoding="utf-8")
        return pid_file

    def test_windows_stop_runs_taskkill_for_the_whole_tree_and_removes_the_pid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            pid_file = self._stopping(root)
            with (
                patch.object(serve, "_windows", return_value=True),
                patch.object(serve, "_ours", return_value=True),
                patch.object(serve, "_alive", return_value=False),
                patch.object(serve.effects, "run") as ran,
                patch.object(serve.effects, "kill") as killed,
            ):
                result = serve.stop(root)
            ran.assert_called_once_with(
                ["taskkill", "/PID", "4321", "/T", "/F"], operation="proc.kill", capture_output=True, check=False
            )
            killed.assert_not_called()
            self.assertEqual({"stopped": True, "reason": None, "pid": 4321}, result)
            self.assertFalse(pid_file.exists())

    def test_windows_stop_never_escalates_to_sigkill_when_the_process_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            pid_file = self._stopping(root)
            clock = FakeClock()
            with (
                patch.object(serve, "_windows", return_value=True),
                patch.object(serve, "_ours", return_value=True),
                patch.object(serve, "_alive", return_value=True),
                patch.object(serve, "time", clock),
                patch.object(serve.effects, "run"),
                patch.object(serve.effects, "kill") as killed,
            ):
                result = serve.stop(root)
            killed.assert_not_called()
            self.assertEqual({"stopped": False, "reason": None, "pid": 4321}, result)
            self.assertGreater(clock.sleeps, 0)
            self.assertFalse(pid_file.exists())

    def test_posix_stop_sends_sigterm_and_is_done_when_the_process_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            pid_file = self._stopping(root)
            with (
                patch.object(serve, "_windows", return_value=False),
                patch.object(serve, "_ours", return_value=True),
                patch.object(serve, "_alive", return_value=False),
                patch.object(serve, "_reap"),
                patch.object(serve.effects, "kill") as killed,
            ):
                result = serve.stop(root)
            killed.assert_called_once_with(4321, signal.SIGTERM, target="serve")
            self.assertEqual({"stopped": True, "reason": None, "pid": 4321}, result)
            self.assertFalse(pid_file.exists())

    def _stop_with_ignored_sigterm(self, root, survives_kill):
        """`stop` num POSIX cujo processo ignora o SIGTERM; devolve (sinais enviados, resultado)."""
        sent = []

        def kill(pid, sig, *, target=None):
            del target
            sent.append((pid, sig))
            if sig == _posix.SIGKILL:
                raise ProcessLookupError  # o `suppress(OSError)` do stop engole

        def alive(_pid):
            return survives_kill or _posix.SIGKILL not in [sig for _pid, sig in sent]

        with (
            _posix.posix_names(),
            patch.object(serve, "_windows", return_value=False),
            patch.object(serve, "_ours", return_value=True),
            patch.object(serve, "_alive", side_effect=alive),
            patch.object(serve, "_reap"),
            patch.object(serve, "time", FakeClock()),
            patch.object(serve.effects, "kill", side_effect=kill),
        ):
            return sent, serve.stop(root)

    def test_posix_stop_escalates_to_sigkill_when_sigterm_is_ignored(self):
        for survives_kill in (False, True):
            with self.subTest(survives_kill=survives_kill), tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp)
                pid_file = self._stopping(root)
                sent, result = self._stop_with_ignored_sigterm(root, survives_kill)
                self.assertEqual([(4321, signal.SIGTERM), (4321, _posix.SIGKILL)], sent)
                self.assertEqual({"stopped": not survives_kill, "reason": None, "pid": 4321}, result)
                self.assertFalse(pid_file.exists())

    def test_a_sigterm_that_fails_is_ignored_and_the_pid_file_still_goes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            pid_file = self._stopping(root)
            with (
                patch.object(serve, "_windows", return_value=False),
                patch.object(serve, "_ours", return_value=True),
                patch.object(serve, "_alive", return_value=False),
                patch.object(serve, "_reap"),
                patch.object(serve.effects, "kill", side_effect=PermissionError),
            ):
                result = serve.stop(root)
            self.assertTrue(result["stopped"])
            self.assertFalse(pid_file.exists())

    def test_a_pid_that_is_not_ours_is_never_signalled_on_either_platform(self):
        for windows in (True, False):
            with self.subTest(windows=windows), tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp)
                pid_file = self._stopping(root)
                with (
                    patch.object(serve, "_windows", return_value=windows),
                    patch.object(serve, "_ours", return_value=False),
                    patch.object(serve.effects, "run") as ran,
                    patch.object(serve.effects, "kill") as killed,
                ):
                    result = serve.stop(root)
                ran.assert_not_called()
                killed.assert_not_called()
                self.assertEqual({"stopped": False, "reason": "stale_pid", "pid": 4321}, result)
                self.assertFalse(pid_file.exists())

    def test_stop_without_a_pid_file_reports_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            self.assertEqual({"stopped": False, "reason": "not_running", "pid": None}, serve.stop(root))


if __name__ == "__main__":
    unittest.main()
