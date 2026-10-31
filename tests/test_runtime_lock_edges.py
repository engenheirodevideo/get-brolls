"""Bordas do runtime: travas (Windows simulado, link, pasta sem acesso), remoção de árvore e
o log de diagnóstico quando não há onde gravar.

Tudo aqui é simulado: o `msvcrt` é um módulo falso em `sys.modules`, a falta de
`O_NOFOLLOW` e da pasta pessoal são patches, e a corrida do `force_rmtree` é um
`shutil.rmtree` falso que chama a retentativa com o caminho trocado por link.
"""

import argparse
import contextlib
import errno
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

from getbrolls import config, effects, file_locks, logs, private_files, runtime
from getbrolls.errors import LockedError

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0
# `os.chflags` só existe no macOS/BSD; por `getattr` o pyright aceita o teste em todo sistema.
_chflags = getattr(os, "chflags", None)


def _fake_msvcrt(calls, fail=None):
    """Um `msvcrt` falso: registra cada `locking` e, com `fail`, levanta esse erro."""

    def locking(descriptor, mode, size):
        calls.append((descriptor, mode, size))
        if fail is not None:
            raise fail

    module = types.ModuleType("msvcrt")
    module.LK_NBLCK = 1  # type: ignore[attr-defined]
    module.LK_UNLCK = 2  # type: ignore[attr-defined]
    module.locking = locking  # type: ignore[attr-defined]
    return module


class WindowsLockBackendTests(unittest.TestCase):
    def test_without_an_injected_backend_the_system_msvcrt_is_used(self):
        calls = []
        with (
            patch.dict(sys.modules, {"msvcrt": _fake_msvcrt(calls)}),
            tempfile.TemporaryFile(mode="w+") as stream,
        ):
            stream.write("x")  # trava que já tem conteúdo: nada é acrescentado
            stream.flush()
            runtime._acquire_lock(stream, platform="nt")  # pylint: disable=protected-access
            runtime._release_lock(stream, platform="nt")  # pylint: disable=protected-access
            stream.seek(0)
            self.assertEqual("x", stream.read())
        self.assertEqual([(1, 1), (2, 1)], [(mode, size) for _fd, mode, size in calls])

    def test_a_busy_windows_lock_is_reported_as_blocking(self):
        calls = []
        busy = _fake_msvcrt(calls, fail=OSError(errno.EACCES, "locked"))
        with tempfile.TemporaryFile(mode="w+") as stream:
            with self.assertRaises(BlockingIOError) as raised:
                runtime._acquire_lock(stream, platform="nt", windows=busy)  # pylint: disable=protected-access
            self.assertIsInstance(raised.exception.__cause__, OSError)
            # O byte travado foi criado antes da tentativa, uma vez só.
            stream.seek(0)
            self.assertEqual("\0", stream.read())
        self.assertEqual(1, len(calls))


class NeverRaisingViewTests(unittest.TestCase):
    """`is_folder` and `_visible` count what the OS refuses to show as absent, never as a traceback."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-view-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_a_folder_that_cannot_be_inspected_counts_as_absent(self):
        with patch.object(Path, "is_dir", side_effect=PermissionError(errno.EACCES, "denied")):
            self.assertFalse(runtime.is_folder(self.base))
        self.assertTrue(runtime.is_folder(self.base))

    def test_a_path_that_cannot_be_inspected_counts_as_not_visible(self):
        with patch.object(Path, "exists", side_effect=PermissionError(errno.EACCES, "denied")):
            self.assertFalse(runtime._visible(self.base))  # pylint: disable=protected-access
        self.assertTrue(runtime._visible(self.base))  # pylint: disable=protected-access
        self.assertFalse(runtime._visible(self.base / "nao-existe"))  # pylint: disable=protected-access


class LockFileEdgeTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-lock-edges-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    @needs_symlink
    def test_a_linked_lock_is_refused_where_o_nofollow_is_missing(self):
        target = self.base / "fora.lock"
        link = self.base / ".command.lock"
        link.symlink_to(target)
        # `_LOCK_FLAGS` is fixed at import: without also dropping O_NOFOLLOW from it, the open itself refuses the
        # link (ELOOP) and the explicit check never runs.
        with (
            patch.object(file_locks.os, "O_NOFOLLOW", 0, create=True),
            patch.object(file_locks, "_LOCK_FLAGS", os.O_RDWR | os.O_CREAT | os.O_APPEND),
        ):
            with (
                self.assertRaisesRegex(ValueError, r"\.command\.lock .* é um link") as raised,
                runtime.exclusive_lock(link, "ocupada"),
            ):
                self.fail("a trava num link não pode ser tomada")
            self.assertEqual("INVALID_DATA", runtime.error_code_for(raised.exception))
            with runtime.try_lock(link, 0) as held:
                self.assertFalse(held)
        self.assertFalse(target.exists(), "a trava nunca cria o arquivo apontado pelo link")
        self.assertTrue(link.is_symlink())

    def test_try_lock_in_a_missing_folder_yields_false_and_creates_nothing(self):
        missing = self.base / "nao-existe" / ".lock"
        with runtime.try_lock(missing, 0) as held:
            self.assertFalse(held)
        self.assertFalse(missing.parent.exists())

    def test_try_lock_yields_false_when_the_lock_call_fails(self):
        lock = self.base / ".lock"
        with (
            patch.object(file_locks, "_acquire_lock", side_effect=OSError(errno.ENOLCK, "no locks")),
            patch.object(file_locks, "_release_lock") as release,
            runtime.try_lock(lock, 5) as held,
        ):
            self.assertFalse(held)
        release.assert_not_called()  # trava que não foi tomada não é solta
        self.assertTrue(lock.is_file())

    def test_a_busy_lock_is_locked_even_when_its_log_line_fails(self):
        lock = self.base / ".command.lock"
        with (
            patch.object(file_locks, "_acquire_lock", side_effect=BlockingIOError),
            patch.object(logs, "refusal", side_effect=RuntimeError("log quebrado")) as refusal,
            self.assertRaises(LockedError) as raised,
            runtime.exclusive_lock(lock, "Projeto ocupado."),
        ):
            self.fail("a trava ocupada não pode ser tomada")
        refusal.assert_called_once()
        self.assertEqual("Projeto ocupado.", str(raised.exception))
        self.assertEqual("LOCKED", runtime.error_code_for(raised.exception))


class ProjectLockWaitTests(unittest.TestCase):
    """`GB_LOCK_WAIT_S` and the explicit `wait_s` of `project_lock`: wait for a busy project, then `LOCKED`."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-lock-wait-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        (self.project / "brolls").mkdir()
        self.lock_file = self.project / "brolls" / runtime.COMMAND_LOCK

    @contextlib.contextmanager
    def busy_for(self, seconds):
        """The project lock held by another open file for `seconds`, released by a timer thread."""
        holder = contextlib.ExitStack()
        holder.enter_context(runtime.exclusive_lock(self.lock_file, "ocupada"))
        timer = threading.Timer(seconds, holder.close)
        timer.start()
        try:
            yield
        finally:
            timer.join()
            timer.cancel()

    def take(self, **kwargs):
        with runtime.project_lock(self.project, **kwargs):
            return "taken"

    def test_a_busy_project_is_locked_at_once_by_default(self):
        with (
            patch.dict(os.environ, {"GB_LOCK_WAIT_S": ""}),
            self.busy_for(0.4),
            self.assertRaises(LockedError) as raised,
        ):
            self.take()
        self.assertEqual("LOCKED", runtime.error_code_for(raised.exception))

    def test_the_env_wait_outlasts_a_short_hold(self):
        with patch.dict(os.environ, {"GB_LOCK_WAIT_S": "5"}), self.busy_for(0.3):
            self.assertEqual("taken", self.take())

    def test_the_env_wait_ends_in_locked_when_the_hold_is_longer(self):
        with (
            patch.dict(os.environ, {"GB_LOCK_WAIT_S": "0.2"}),
            self.busy_for(1.5),
            self.assertRaises(LockedError),
        ):
            self.take()

    def test_an_explicit_wait_wins_over_the_env(self):
        with (
            patch.dict(os.environ, {"GB_LOCK_WAIT_S": "5"}),
            self.busy_for(0.4),
            self.assertRaises(LockedError),
        ):
            self.take(wait_s=0)
        with patch.dict(os.environ, {"GB_LOCK_WAIT_S": "0"}), self.busy_for(0.3):
            self.assertEqual("taken", self.take(wait_s=5))

    def test_an_invalid_env_value_is_a_usage_style_error_naming_the_key(self):
        for value in ("abc", "-1", "nan", "inf", "99999"):
            with (
                self.subTest(value=value),
                patch.dict(os.environ, {"GB_LOCK_WAIT_S": value}),
                self.assertRaisesRegex(ValueError, "GB_LOCK_WAIT_S"),
            ):
                self.take()

    def test_an_invalid_env_value_is_ignored_when_the_wait_is_explicit(self):
        with patch.dict(os.environ, {"GB_LOCK_WAIT_S": "abc"}):
            self.assertEqual("taken", self.take(wait_s=0))

    def test_no_project_takes_no_lock_and_reads_no_setting(self):
        with patch.dict(os.environ, {"GB_LOCK_WAIT_S": "abc"}), runtime.project_lock(None):
            pass

    def test_the_wait_is_a_valid_declared_key(self):
        self.assertIn("GB_LOCK_WAIT_S", config.KEYS)
        with patch.dict(os.environ, {"GB_LOCK_WAIT_S": " 2.5 "}):
            self.assertEqual(2.5, config.lock_wait_s())
        with patch.dict(os.environ, {"GB_LOCK_WAIT_S": ""}):
            self.assertEqual(0.0, config.lock_wait_s())


class RecordWarningTests(unittest.TestCase):
    def test_a_failing_log_never_drops_the_warning(self):
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        try:
            with patch.object(logs, "event", side_effect=RuntimeError("log quebrado")) as log_event:
                runtime.record_warning("STATIC_FALLBACK", "Use estático")
        finally:
            runtime.ACTIVE.reset(token)
        log_event.assert_called_once()
        self.assertEqual([{"code": "STATIC_FALLBACK", "message": "Use estático"}], event["warnings"])


@unittest.skipIf(os.name == "nt", "modos POSIX e links simbólicos")
class ForceRmtreeEdgeTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-rmtree-edges-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for path in [self.base, *self.base.rglob("*")]:
            with contextlib.suppress(OSError):
                if not path.is_symlink():
                    path.chmod(0o700)
        shutil.rmtree(self.base, ignore_errors=True)

    def op_lines_of(self, remove):
        """The `event=op` fields `force_rmtree` logs while `remove` runs it."""
        rows = []

        def record(_logger, _level, name, **fields):
            if name == "op":
                rows.append(fields)

        with (
            patch.object(logs, "event", record),
            patch.object(effects._logger, "isEnabledFor", return_value=True),  # pylint: disable=protected-access
        ):
            remove()
        return rows

    def the_op_line(self, remove):
        rows = self.op_lines_of(remove)
        self.assertEqual(1, len(rows), rows)
        return rows[0]

    def test_a_removed_tree_logs_outcome_ok(self):
        root = self.base / "some"
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "a.txt").write_text("a", encoding="utf-8")
        row = self.the_op_line(lambda: runtime.force_rmtree(root))
        self.assertEqual(("fs.rmtree", "ok"), (row["operation"], row["outcome"]))
        self.assertFalse(os.path.lexists(root))

    def test_a_tree_that_could_not_be_removed_never_logs_outcome_ok(self):
        root = self.base / "stays"
        root.mkdir()
        (root / "a.txt").write_text("a", encoding="utf-8")
        with patch.object(private_files.shutil, "rmtree"):  # the delete does nothing, as when every retry fails
            row = self.the_op_line(lambda: runtime.force_rmtree(root))  # still never raises
        self.assertEqual("fs.rmtree", row["operation"])
        self.assertEqual("error", row["outcome"])
        self.assertTrue(row["error"])
        self.assertTrue(root.is_dir(), "what is left stays for the next sweep")

    def test_the_error_that_stopped_the_delete_is_the_one_the_line_names(self):
        root = self.base / "stopped"
        root.mkdir()
        with patch.object(private_files.shutil, "rmtree", side_effect=PermissionError(errno.EACCES, "denied")):
            row = self.the_op_line(lambda: runtime.force_rmtree(root))
        self.assertEqual(("error", "PermissionError"), (row["outcome"], row["error"]))

    def test_an_error_after_the_tree_is_gone_still_logs_ok(self):
        root = self.base / "gone"
        root.mkdir()

        def remove_then_fail(path, **_kwargs):
            Path(path).rmdir()
            raise OSError(errno.EIO, "late failure")

        with patch.object(private_files.shutil, "rmtree", side_effect=remove_then_fail):
            row = self.the_op_line(lambda: runtime.force_rmtree(root))
        self.assertEqual("ok", row["outcome"])

    @unittest.skipUnless(_chflags is not None and hasattr(stat, "UF_IMMUTABLE"), "needs BSD file flags")
    def test_an_immutable_file_leaves_the_tree_and_the_line_says_so(self):
        root = self.base / "immutable"
        root.mkdir()
        kept = root / "kept.txt"
        kept.write_text("k", encoding="utf-8")
        assert _chflags is not None
        _chflags(kept, stat.UF_IMMUTABLE)
        self.addCleanup(_chflags, kept, 0)
        row = self.the_op_line(lambda: runtime.force_rmtree(root))
        self.assertEqual("error", row["outcome"])
        self.assertTrue(kept.exists())

    @unittest.skipIf(IS_ROOT, "root ignora o modo da pasta")
    def test_a_root_without_any_permission_is_removed(self):
        root = self.base / "travada"
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "a.txt").write_text("a", encoding="utf-8")
        root.chmod(0)
        runtime.force_rmtree(root)
        self.assertFalse(os.path.lexists(root))
        self.assertTrue(self.base.is_dir(), "a pasta que contém a árvore fica")

    def test_a_linked_root_is_left_alone_with_its_target(self):
        outside = self.base / "fora"
        outside.mkdir()
        (outside / "dado.txt").write_text("fica", encoding="utf-8")
        outside.chmod(0o500)
        link = self.base / "link"
        link.symlink_to(outside, target_is_directory=True)
        runtime.force_rmtree(link)  # nunca levanta
        self.assertTrue(link.is_symlink())
        self.assertEqual("fica", (outside / "dado.txt").read_text(encoding="utf-8"))
        self.assertEqual(0o500, stat.S_IMODE(outside.stat().st_mode), "o alvo do link nunca é tocado")

    def test_a_parent_swapped_for_a_link_mid_walk_is_never_chmodded(self):
        # Corrida: durante o walk, uma pasta da árvore vira link para fora dela. A
        # retentativa libera a escrita da pasta de cima da entrada que falhou — num link,
        # o `chmod` seguiria o link e mudaria o modo da pasta de fora.
        root = self.base / "arvore"
        root.mkdir()
        outside = self.base / "fora"
        outside.mkdir()
        outside.chmod(0o500)
        swapped = root / "trocada"
        swapped.symlink_to(outside, target_is_directory=True)
        real_rmtree = shutil.rmtree
        retries = []

        def racing_rmtree(path, *args, **kwargs):
            callback = kwargs.get("onexc") or kwargs.get("onerror")
            if callback is None or Path(path) != root:
                return real_rmtree(path, *args, **kwargs)
            failed = swapped / "sumiu.txt"
            retries.append(failed)
            callback(os.unlink, str(failed), OSError(errno.ENOENT, "sumiu"))
            return None

        with patch.object(private_files.shutil, "rmtree", side_effect=racing_rmtree):
            runtime.force_rmtree(root)
        self.assertEqual([swapped / "sumiu.txt"], retries)
        self.assertEqual(0o500, stat.S_IMODE(outside.stat().st_mode), "o alvo do link nunca é tocado")
        self.assertTrue(swapped.is_symlink())


class ScrubHomeTests(unittest.TestCase):
    def test_a_root_home_folder_scrubs_nothing(self):
        text = "/srv/ana/projeto/brolls/manifest.json"
        with patch.object(runtime.Path, "home", return_value=Path("/")):
            self.assertEqual(text, runtime.scrub_home(text))


class NoHomeDiagnosticsTests(unittest.TestCase):
    """Sem `GB_HOME` e sem pasta pessoal resolvível, um erro fora de projeto não grava nada."""

    def _no_home(self):
        environ = {k: v for k, v in os.environ.items() if k not in ("GB_HOME", "GETBROLLS_HOME")}
        return (
            patch.dict(os.environ, environ, clear=True),
            patch.object(runtime.Path, "home", side_effect=RuntimeError("sem HOME")),
        )

    def test_diagnostics_path_without_home_is_none(self):
        env, home = self._no_home()
        with env, home:
            self.assertIsNone(runtime.diagnostics_path(None))
            self.assertIsNone(runtime.write_diagnostics_log(None, {"operation": "x", "status": "error"}))

    def test_audited_error_without_home_still_returns_the_envelope(self):
        env, home = self._no_home()
        with env, home, self.assertRaises(runtime.OperationError) as raised:
            runtime.audited(argparse.Namespace(command="plugins", project=None), self._fail)
        payload = raised.exception.payload
        self.assertEqual("INVALID_DATA", payload["error_code"])
        self.assertEqual("dado recusado", payload["message"])
        self.assertIsNone(payload["log"])
        self.assertIsNone(payload["app_log"])

    @staticmethod
    def _fail(_args):
        raise ValueError("dado recusado")


class DiagnosticsWriteFailureTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-diag-edges-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        (self.project / "brolls").mkdir()
        self.log = self.project / "brolls" / "diagnostics.jsonl"

    def test_write_diagnostics_log_returns_none_when_the_file_cannot_be_written(self):
        with patch.object(private_files, "_ensure_private_file", side_effect=PermissionError("negado")):
            self.assertIsNone(runtime.write_diagnostics_log(self.project, {"operation": "x", "status": "error"}))
        self.assertFalse(self.log.exists())

    def test_write_diagnostics_log_appends_one_event(self):
        written = runtime.write_diagnostics_log(self.project, {"operation": "x", "status": "error"})
        self.assertIsNotNone(written)
        self.assertEqual(self.log.resolve(), Path(str(written)).resolve())
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(["x"], [row["operation"] for row in rows])
        self.assertIn("at", rows[0])

    def test_unwritable_log_with_a_non_dict_result_warns_on_stderr(self):
        stderr = io.StringIO()
        with (
            patch.object(private_files, "_ensure_private_file", side_effect=PermissionError("negado")),
            contextlib.redirect_stderr(stderr),
        ):
            result = runtime.audited(argparse.Namespace(command="resolve", project=str(self.project)), lambda _: None)
        self.assertIsNone(result)
        warning = json.loads(stderr.getvalue())
        self.assertEqual("LOG_UNAVAILABLE", warning["code"])
        self.assertFalse(self.log.exists())

    def test_app_log_that_cannot_be_checked_is_null_in_the_envelope(self):
        def fail(_args):
            raise OSError(errno.EIO, "disco")

        with (
            patch.object(logs, "log_path", side_effect=RuntimeError("quebrado")) as log_path,
            self.assertRaises(runtime.OperationError) as raised,
        ):
            runtime.audited(argparse.Namespace(command="resolve", project=str(self.project)), fail)
        log_path.assert_called_once()
        payload = raised.exception.payload
        self.assertEqual("IO_ERROR", payload["error_code"])
        self.assertIsNone(payload["app_log"])
        self.assertEqual(str(self.log.resolve()), payload["log"])
        self.assertEqual("error", json.loads(self.log.read_text(encoding="utf-8"))["status"])


if __name__ == "__main__":
    unittest.main()
