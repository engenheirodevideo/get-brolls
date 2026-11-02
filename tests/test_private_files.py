"""Arquivos e pastas "privados" (0600/0700 no POSIX) ganham a ACL equivalente no Windows.

`private_files.make_private(path)` roda `%SystemRoot%\\System32\\icacls.exe <caminho> /grant:r *S-1-3-4:(F)
*S-1-5-18:(F) *S-1-5-32-544:(F) /inheritance:r /L` (pasta: `(OI)(CI)(F)`) e, depois dela, `icacls <pasta>
/restore <arquivo> /L`, que grava a DACL inteira em SDDL: só o dono (OWNER RIGHTS), SYSTEM e Administradores,
o mesmo que o CPython dá a `os.mkdir(caminho, 0o700)`, por SID e nunca por nome, sem a entrada explícita de
quem criou o arquivo. Listas de argumentos, sem shell, e nada no POSIX, onde o `chmod` já rodou. O ramo do
Windows roda aqui em qualquer sistema com `private_files._is_windows` ligado e `private_files._run_icacls` trocado
por um gravador: o argv é conferido letra por letra. Uma falha vira uma linha `event=op` com `outcome=error` (o
esquema de sempre, sem código novo) e nunca derruba o comando. O teste de verdade (`RealWindowsAclTests`) só
roda no Windows e lê a ACL de volta em SDDL com `icacls /save`.
"""

import ast
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths as _test_paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

from getbrolls import acquisition, effects, effects_salt, http, library, logs, private_files, profile, queue, serve
from getbrolls.sdk import safe_copy

# pylint: disable=protected-access

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "scripts" / "getbrolls"


def expected_argv(exe, path, rights="(F)"):
    """O argv inteiro, escrito à mão: os três SIDs bem conhecidos, a concessão antes de `/inheritance:r`, `/L`."""
    return [
        exe,
        str(path),
        "/grant:r",
        f"*S-1-3-4:{rights}",
        f"*S-1-5-18:{rights}",
        f"*S-1-5-32-544:{rights}",
        "/inheritance:r",
        "/L",
    ]


class Recorder:
    """Faz as vezes de `logs.event`: guarda os campos de cada chamada."""

    def __init__(self):
        self.calls = []

    def __call__(self, _logger, level, name, **fields):
        self.calls.append((level, name, fields))

    def ops(self):
        return [fields for _level, name, fields in self.calls if name == "op"]


def _finished(returncode=0):
    return subprocess.CompletedProcess(["icacls"], returncode, b"", b"")


class WindowsCase(unittest.TestCase):
    """O Windows simulado: `_is_windows` ligado, `_run_icacls` gravado, um `SystemRoot` com `icacls.exe`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-private-")).resolve()
        self.addCleanup(self._clean)
        system_root = self.tmp / "Windows"
        (system_root / "System32").mkdir(parents=True)
        self.exe = str(Path(system_root, "System32", "icacls.exe"))
        Path(self.exe).write_bytes(b"")
        self.recorder = Recorder()
        # O tamanho de cada caminho na hora do `icacls`: a ACL chega antes do primeiro byte.
        self.sizes = {}
        # `(argv, bytes do arquivo que /restore lê)`, guardados na hora da chamada: o arquivo some depois dela.
        self.restored = []
        self.icacls = mock.MagicMock(side_effect=self._icacls)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(private_files, "_is_windows", return_value=True))
        stack.enter_context(mock.patch.object(private_files, "_run_icacls", self.icacls))
        stack.enter_context(mock.patch.object(logs, "event", self.recorder))
        stack.enter_context(mock.patch.object(effects._logger, "isEnabledFor", return_value=True))
        stack.enter_context(mock.patch.dict(logs._state, {"project": None, "command": None}))
        # A pasta temporária do perfil, onde nasce o texto do `/restore` (nunca a do `TEMP`).
        (self.local / "Temp").mkdir(parents=True)
        self.environ = stack.enter_context(
            mock.patch.dict(os.environ, {"SYSTEMROOT": str(system_root), "LOCALAPPDATA": str(self.local)})
        )

    @property
    def local(self):
        """`%LOCALAPPDATA%` do Windows simulado."""
        return self.tmp / "AppData" / "Local"

    def _icacls(self, argv, **_kwargs):
        if "/restore" in argv:
            self.restored.append((list(argv), Path(argv[argv.index("/restore") + 1]).read_bytes()))
            return _finished()
        target = Path(argv[1])
        self.sizes[str(target)] = target.stat().st_size if target.is_file() else None
        return _finished()

    def _clean(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        """Todo argv pedido ao `icacls`, na ordem: a concessão e o `/restore` de cada objeto."""
        return [call.args[0] for call in self.icacls.call_args_list]

    def argvs(self):
        """Só as concessões (`/grant:r`), na ordem."""
        return [argv for argv in self.calls() if "/grant:r" in argv]

    def granted(self, path):
        """Quantas vezes o `icacls` foi pedido para exatamente `path`."""
        return sum(1 for argv in self.argvs() if argv[1] == str(path))

    def assert_one_grant(self, path, rights="(F)"):
        found = [argv for argv in self.argvs() if argv[1] == str(path)]
        self.assertEqual(found, [expected_argv(self.exe, path, rights)])

    def assert_empty_when_granted(self, path):
        """O arquivo ainda estava vazio quando a ACL chegou: nenhum conteúdo antes dela."""
        self.assertEqual(self.sizes[str(path)], 0)


class ArgvTests(WindowsCase):
    def test_a_file_gets_the_three_well_known_sids_and_no_inheritance(self):
        path = self.tmp / "private.json"
        path.write_text("{}", encoding="utf-8")
        private_files.make_private(path)
        self.assert_one_grant(path)

    def test_a_folder_gets_an_inheritable_grant(self):
        folder = self.tmp / "sources"
        folder.mkdir()
        private_files.make_private(folder)
        self.assert_one_grant(folder, "(OI)(CI)(F)")

    def test_it_runs_a_list_without_a_shell_and_waits_for_a_bounded_time(self):
        path = self.tmp / "private.json"
        path.write_text("{}", encoding="utf-8")
        private_files.make_private(path)
        self.assertEqual(2, self.icacls.call_count)
        for call in self.icacls.call_args_list:
            with self.subTest(argv=call.args[0]):
                self.assertIsInstance(call.args[0], list)
                self.assertFalse(call.kwargs.get("shell", False))
                self.assertIs(call.kwargs.get("check"), True)
                self.assertIs(call.kwargs.get("capture_output"), True)
                self.assertIn(call.kwargs["timeout"], range(1, 121))

    def test_the_grant_comes_before_the_inheritance_is_removed(self):
        path = self.tmp / "a"
        path.write_text("", encoding="utf-8")
        private_files.make_private(path)
        (argv,) = self.argvs()
        self.assertLess(argv.index("/grant:r"), argv.index("/inheritance:r"))

    def test_the_account_never_comes_from_the_environment(self):
        path = self.tmp / "a"
        path.write_text("", encoding="utf-8")
        for env in ({}, {"USERNAME": "/deny", "USERDOMAIN": "x(y)"}, {"USERNAME": "ana", "USERDOMAIN": "CASA"}):
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                for name in ("USERNAME", "USERDOMAIN"):
                    if name not in env:
                        os.environ.pop(name, None)
                self.icacls.reset_mock()
                private_files.make_private(path)
                self.assertEqual(self.argvs(), [expected_argv(self.exe, path)])

    def test_the_tool_is_the_one_in_system32_never_a_bare_name(self):
        path = self.tmp / "a"
        path.write_text("", encoding="utf-8")
        private_files.make_private(path)
        self.assertEqual(2, len(self.calls()))
        for argv in self.calls():
            self.assertEqual(argv[0], self.exe)
            self.assertTrue(Path(argv[0]).is_absolute())

    def test_without_systemroot_it_is_the_default_windows_folder(self):
        del self.environ["SYSTEMROOT"]
        path = self.tmp / "a"
        path.write_text("", encoding="utf-8")
        default = str(Path("C:\\Windows", "System32", "icacls.exe"))
        real_is_file = Path.is_file
        with mock.patch.object(Path, "is_file", lambda self: str(self) == default or real_is_file(self)):
            private_files.make_private(path)
        self.assertEqual(self.argvs(), [expected_argv(default, path)])

    def test_a_path_with_spaces_stays_one_argument(self):
        folder = self.tmp / "Pasta com espaço"
        folder.mkdir()
        private_files.make_private(folder)
        self.assertEqual(self.argvs()[0][1], str(folder))

    def test_a_mock_of_subprocess_run_never_sees_the_call(self):
        """`make_private` não chama `subprocess.run` nem `effects.run` pelo atributo: um mock deles, que conta
        as chamadas, não vê o `icacls` (o `Popen` fica com `BoundRunTests`, que roda o executor de verdade)."""
        path = self.tmp / "a"
        path.write_text("", encoding="utf-8")
        with (
            mock.patch("subprocess.run") as run,
            mock.patch("subprocess.Popen") as popen,
            mock.patch.object(effects, "run") as effects_run,
        ):
            private_files.make_private(path)
        run.assert_not_called()
        popen.assert_not_called()
        effects_run.assert_not_called()
        self.assertEqual(self.argvs(), [expected_argv(self.exe, path)])


class LoggingTests(WindowsCase):
    def test_a_success_is_one_op_line_named_by_the_tool_never_by_a_path(self):
        path = self.tmp / "queue.json"
        path.write_text("{}", encoding="utf-8")
        private_files.make_private(path)
        (row,) = self.recorder.ops()
        self.assertEqual((row["operation"], row["target"], row["outcome"]), ("proc.run", "icacls", "ok"))
        self.assertEqual(row["exit"], 0)
        self.assertNotIn(str(self.tmp), repr(self.recorder.calls))
        self.assertNotIn("S-1-", repr(self.recorder.calls))

    def test_a_failed_tool_is_an_error_line_and_never_raises(self):
        self.icacls.side_effect = subprocess.CalledProcessError(5, ["icacls"])
        path = self.tmp / "queue.json"
        path.write_text("{}", encoding="utf-8")
        private_files.make_private(path)
        (row,) = self.recorder.ops()
        self.assertEqual((row["operation"], row["target"], row["outcome"]), ("proc.run", "icacls", "error"))
        self.assertEqual((row["error_code"], row["error"], row["exit"]), ("INVALID_DATA", "CalledProcessError", 5))

    def test_a_tool_that_cannot_start_is_an_error_line_and_never_raises(self):
        self.icacls.side_effect = FileNotFoundError(2, "icacls")
        path = self.tmp / "queue.json"
        path.write_text("{}", encoding="utf-8")
        private_files.make_private(path)
        (row,) = self.recorder.ops()
        self.assertEqual((row["outcome"], row["error_code"], row["error"]), ("error", "IO_ERROR", "FileNotFoundError"))

    def test_no_icacls_in_system32_is_an_error_line_and_nothing_runs(self):
        Path(self.exe).unlink()
        path = self.tmp / "queue.json"
        path.write_text("{}", encoding="utf-8")
        private_files.make_private(path)
        self.icacls.assert_not_called()
        (row,) = self.recorder.ops()
        self.assertEqual((row["operation"], row["target"], row["outcome"]), ("proc.run", "icacls", "error"))
        self.assertEqual((row["error_code"], row["error"]), ("IO_ERROR", "FileNotFoundError"))

    def test_a_tool_that_hangs_is_an_error_line_and_never_raises(self):
        self.icacls.side_effect = subprocess.TimeoutExpired(["icacls"], 30)
        path = self.tmp / "queue.json"
        path.write_text("{}", encoding="utf-8")
        private_files.make_private(path)
        (row,) = self.recorder.ops()
        self.assertEqual((row["outcome"], row["error"]), ("error", "TimeoutExpired"))

    @unittest.skipIf(os.name == "nt", "symlink creation needs a privilege on Windows")
    def test_a_link_is_refused_and_its_target_is_never_touched(self):
        target = self.tmp / "target"
        target.write_text("x", encoding="utf-8")
        link = self.tmp / "link"
        link.symlink_to(target)
        private_files.make_private(link)
        self.icacls.assert_not_called()
        self.assertEqual([row["outcome"] for row in self.recorder.ops()], ["error"])

    def test_a_junction_is_refused_but_another_reparse_point_is_not(self):
        path = self.tmp / "folder"
        path.mkdir()
        for tag, allowed in (
            (private_files._LINK_TAGS[1], False),
            (private_files._LINK_TAGS[0], False),
            (0x9000001A, True),
        ):
            with self.subTest(tag=hex(tag)):
                self.icacls.reset_mock()
                self.recorder.calls.clear()
                info = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_reparse_tag=tag)
                with mock.patch("os.lstat", return_value=info):
                    private_files.make_private(path)
                self.assertEqual(self.icacls.called, allowed)
                self.assertEqual([row["outcome"] for row in self.recorder.ops()], ["ok" if allowed else "error"])

    def test_a_missing_file_is_an_error_line_and_never_raises(self):
        private_files.make_private(self.tmp / "nothing")
        self.icacls.assert_not_called()
        self.assertEqual([row["outcome"] for row in self.recorder.ops()], ["error"])

    def test_the_handler_files_run_the_tool_without_writing_a_line(self):
        path = self.tmp / "getbrolls.log"
        path.write_text("", encoding="utf-8")
        private_files.make_private(path, record=False)
        self.assert_one_grant(path)
        self.assertEqual(self.recorder.calls, [])

    def test_the_handler_files_swallow_a_failure_without_a_line(self):
        path = self.tmp / "getbrolls.log"
        path.write_text("", encoding="utf-8")
        for failure in (subprocess.CalledProcessError(5, ["icacls"]), subprocess.TimeoutExpired(["icacls"], 30)):
            with self.subTest(failure=type(failure).__name__):
                self.icacls.side_effect = failure
                private_files.make_private(path, record=False)
                self.assertEqual(self.recorder.calls, [])
        self.icacls.reset_mock()
        Path(self.exe).unlink()
        private_files.make_private(path, record=False)
        self.icacls.assert_not_called()
        self.assertEqual(self.recorder.calls, [])


class PosixTests(unittest.TestCase):
    """Onde o `chmod` já rodou, o helper não faz nada: nem processo, nem linha de log."""

    def test_it_is_a_true_no_op_off_windows(self):
        folder = Path(tempfile.mkdtemp(prefix="gb-private-"))
        self.addCleanup(folder.rmdir)
        recorder = Recorder()
        with (
            mock.patch.object(private_files, "_is_windows", return_value=False),
            mock.patch.object(private_files, "_run_icacls") as icacls,
            mock.patch("subprocess.run") as run,
            mock.patch.object(logs, "event", recorder),
        ):
            private_files.make_private(folder)
            private_files.make_private(folder, record=False)
            private_files.make_private(folder / "missing")
        icacls.assert_not_called()
        run.assert_not_called()
        self.assertEqual(recorder.calls, [])

    def test_the_real_platform_check_follows_os_name(self):
        self.assertEqual(private_files._is_windows(), os.name == "nt")


class BoundRunTests(unittest.TestCase):
    """`private_files._run_icacls` is `subprocess.run` with the `Popen` of import time: mocks of either never see it."""

    def test_it_runs_a_real_child_while_subprocess_is_mocked(self):
        refuse = AssertionError("a mock of subprocess saw the icacls child")
        with mock.patch("subprocess.run", side_effect=refuse), mock.patch("subprocess.Popen", side_effect=refuse):
            done = private_files._run_icacls(
                [sys.executable, "-c", "print('ok')"], check=True, capture_output=True, timeout=60
            )
        self.assertEqual((done.returncode, done.stdout.strip()), (0, b"ok"))

    def test_a_run_that_is_not_a_plain_function_is_used_as_it_is(self):
        def enclosing():
            outer = 1

            def reads_outer(*_args, **_kwargs):
                return outer

            return reads_outer

        for run in (mock.MagicMock(), enclosing()):
            with self.subTest(run=run):
                self.assertIs(private_files._run_bound_at_import(run), run)

    def test_only_make_private_launches_the_bound_runner(self):
        """The guard cannot see a call of `_run_icacls`: one anywhere else would start a process with no line."""
        callers = []
        for path in sorted(PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    name = getattr(getattr(node, "func", None), "id", None) or getattr(
                        getattr(node, "func", None), "attr", None
                    )
                    if isinstance(node, ast.Call) and name == "_run_icacls":
                        callers.append((path.relative_to(PACKAGE).as_posix(), function.name))
        self.assertEqual(sorted(set(callers)), [("private_files.py", "make_private")])

    def test_it_keeps_the_failures_of_subprocess_run(self):
        with self.assertRaises(subprocess.CalledProcessError) as failed:
            private_files._run_icacls(
                [sys.executable, "-c", "raise SystemExit(3)"], check=True, capture_output=True, timeout=60
            )
        self.assertEqual(failed.exception.returncode, 3)
        with self.assertRaises(subprocess.TimeoutExpired):
            private_files._run_icacls(
                [sys.executable, "-c", "import time; time.sleep(30)"], check=True, capture_output=True, timeout=0.5
            )


class SitesTests(WindowsCase):
    """Cada lugar que pede 0600/0700 passa pelo helper, na criação, no Windows simulado."""

    def test_ensure_private_file_applies_it_to_a_new_file_only(self):
        path = self.tmp / "diagnostics.jsonl"
        private_files._ensure_private_file(path)
        self.assert_one_grant(path)
        path.write_text('{"a": 1}\n', encoding="utf-8")
        private_files._ensure_private_file(path)
        self.assertEqual(self.granted(path), 1)

    def test_a_private_mkdir_applies_it_to_the_folder_it_created(self):
        folder = self.tmp / "plugin-data" / "x"
        effects.mkdir(folder, mode=0o700, parents=True, exist_ok=True)
        self.assert_one_grant(folder, "(OI)(CI)(F)")
        self.assertEqual(self.granted(folder.parent), 0)

    def test_a_private_mkdir_leaves_an_existing_folder_alone(self):
        folder = self.tmp / "mine"
        folder.mkdir()
        effects.mkdir(folder, mode=0o700, exist_ok=True)
        self.icacls.assert_not_called()

    def test_an_ordinary_mkdir_never_runs_the_tool(self):
        effects.mkdir(self.tmp / "ordinary", parents=True)
        effects.mkdir(self.tmp / "shared", mode=0o755)
        self.icacls.assert_not_called()

    def test_queue_save_covers_the_temporary_that_becomes_queue_json(self):
        path = self.tmp / "work" / "queue.json"
        queue.save(path, {"items": []})
        (argv,) = [argv for argv in self.argvs() if Path(argv[1]).parent == path.parent]
        self.assertTrue(Path(argv[1]).name.startswith(".queue-"))
        self.assertEqual(argv, expected_argv(self.exe, argv[1]))
        self.assert_empty_when_granted(argv[1])
        self.assertTrue(path.is_file())

    def test_ctrl_c_while_queue_save_waits_for_the_tool_leaves_no_temporary(self):
        path = self.tmp / "work" / "queue.json"
        self.icacls.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            queue.save(path, {"items": []})
        self.assertEqual(list(path.parent.iterdir()), [])

    def test_the_library_index_is_covered(self):
        library._write_private(self.tmp / "library" / "index.json", "{}")
        (argv,) = [argv for argv in self.argvs() if Path(argv[1]).parent == self.tmp / "library"]
        self.assertIn("index.json", Path(argv[1]).name)
        self.assertEqual(argv, expected_argv(self.exe, argv[1]))
        self.assert_empty_when_granted(argv[1])

    def test_the_trust_store_and_its_folder_are_covered(self):
        home = self.tmp / "home"
        profile._write_store(home, {"profiles": {}})
        self.assertEqual(self.granted(home), 1)
        self.assertEqual(self.argvs()[0], expected_argv(self.exe, home, "(OI)(CI)(F)"))
        (argv,) = [a for a in self.argvs() if Path(a[1]).parent == home]
        self.assert_empty_when_granted(argv[1])

    def test_ctrl_c_while_the_trust_store_waits_for_the_tool_leaves_no_temporary(self):
        home = self.tmp / "home"
        home.mkdir()
        self.icacls.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            profile._write_store(home, {"profiles": {}})
        self.assertEqual(list(home.iterdir()), [])

    def test_serve_pid_and_log_are_covered(self):
        pid = self.tmp / ".serve.pid"
        serve._write_private_text(pid, json.dumps({"pid": 1}))
        self.assert_one_grant(pid)
        self.assert_empty_when_granted(pid)

    def test_the_rotated_serve_log_is_covered(self):
        log = self.tmp / ".serve.log"
        log.write_text("lines\n", encoding="utf-8")
        serve._rotate_log(log)
        rotated = self.tmp / ".serve.log.1"
        self.assert_one_grant(rotated)
        self.assert_empty_when_granted(rotated)

    def test_saved_decisions_are_covered(self):
        saved = serve.save_review(self.tmp, {"decisions": []})
        self.assert_one_grant(saved)
        self.assert_empty_when_granted(saved)

    def test_ctrl_c_while_saved_decisions_wait_for_the_tool_leaves_no_file(self):
        self.icacls.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            serve.save_review(self.tmp, {"decisions": []})
        self.assertEqual(list((self.tmp / serve.REVIEWS_DIR).iterdir()), [])

    def test_a_copy_that_asks_for_a_private_mode_is_covered_and_a_public_one_is_not(self):
        source = self.tmp / "source.bin"
        source.write_bytes(b"data")
        fd = os.open(source, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        private = self.tmp / "private.bin"
        safe_copy.copy_from_fd(fd, private, 100)
        self.assert_one_grant(private)
        self.assert_empty_when_granted(private)
        self.assertEqual(private.read_bytes(), b"data")
        os.lseek(fd, 0, os.SEEK_SET)
        public = self.tmp / "public.bin"
        safe_copy.copy_from_fd(fd, public, 100, mode=0o644)
        self.assertEqual(self.granted(public), 0)

    def test_ctrl_c_while_a_private_copy_waits_for_the_tool_leaves_no_partial_file(self):
        source = self.tmp / "source.bin"
        source.write_bytes(b"data")
        fd = os.open(source, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.icacls.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            safe_copy.copy_from_fd(fd, self.tmp / "private.bin", 100)
        self.assertFalse((self.tmp / "private.bin").exists())

    def test_the_sources_index_gets_the_acl_before_its_first_byte(self):
        cache = self.tmp / ".getbrolls-sources"
        cache.mkdir()
        acquisition._save_index(cache, {"c1": []})
        (argv,) = self.argvs()
        self.assertTrue(Path(argv[1]).name.startswith(".index-"))
        self.assertEqual(argv, expected_argv(self.exe, argv[1]))
        self.assert_empty_when_granted(argv[1])
        self.assertEqual(
            json.loads((cache / acquisition.INDEX_NAME).read_text(encoding="utf-8")), {"schema_version": 1, "c1": []}
        )

    def test_ctrl_c_while_the_sources_index_waits_for_the_tool_leaves_no_temporary(self):
        cache = self.tmp / ".getbrolls-sources"
        cache.mkdir()
        self.icacls.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            acquisition._save_index(cache, {"c1": []})
        self.assertEqual(list(cache.iterdir()), [])

    def test_cached_media_is_private_before_it_takes_the_cache_name(self):
        cache = self.tmp / ".getbrolls-sources"
        cache.mkdir()
        target = self.tmp / "work" / "download.mp4"
        target.parent.mkdir()
        target.write_bytes(b"media")
        final = acquisition._move_into_cache(cache, "c1", target, "ab" * 6, ".mp4")
        self.assertEqual(self.argvs(), [expected_argv(self.exe, target)])
        self.assertEqual(final.read_bytes(), b"media")
        self.assertFalse(target.exists())

    def test_the_provider_cache_is_created_empty_private_then_written(self):
        url = "https://example.org/api"
        cache_dir = self.tmp / "cache"
        cache_path = cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        cache_dir.mkdir()
        leftover = cache_path.with_suffix(".tmp")
        leftover.write_text("left by a run that died", encoding="utf-8")
        response = mock.MagicMock()
        response.headers = {}
        response.read.return_value = b'{"ok": true}'
        with (
            mock.patch.object(http, "_safe_network"),
            mock.patch.object(http.urllib.request, "build_opener") as builder,
            mock.patch.dict(os.environ, {"GB_CACHE_DIR": str(cache_dir)}),
        ):
            os.environ.pop("GETBROLLS_CACHE_DIR", None)
            builder.return_value.open.return_value.__enter__.return_value = response
            self.assertEqual(http.get_json(url, cache_ttl=60), {"ok": True})
        self.assertEqual(self.argvs(), [expected_argv(self.exe, leftover)])
        self.assert_empty_when_granted(leftover)
        self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), {"ok": True})
        self.assertFalse(leftover.exists())

    def test_the_installation_log_folder_and_file_are_covered_silently(self):
        home = self.tmp / "gbhome"
        folder = home / "logs"
        logs._private_folder(folder)
        self.assertEqual(self.granted(home), 1)
        self.assertEqual(self.granted(folder), 1)
        logs._private_folder(folder)
        self.assertEqual(self.granted(folder), 1)
        log = folder / "getbrolls.log"
        logs._open_private(log).close()
        self.assertEqual(self.granted(log), 1)
        log.write_text("a line\n", encoding="utf-8")
        logs._open_private(log).close()
        self.assertEqual(self.granted(log), 1)
        self.assertEqual(self.recorder.ops(), [])

    def test_the_salt_and_its_folder_are_covered_silently(self):
        home = self.tmp / "gbhome"
        salt = home / effects_salt.SALT_FILE
        effects_salt._create_salt(salt)
        self.assertEqual(self.granted(home), 1)
        covered = [Path(argv[1]) for argv in self.argvs()]
        self.assertTrue(any(item.parent == home and item.name.startswith(effects_salt.SALT_FILE) for item in covered))
        self.assertEqual(self.recorder.ops(), [])

    def test_the_salt_written_in_place_is_covered(self):
        home = self.tmp / "gbhome"
        home.mkdir()
        salt = home / effects_salt.SALT_FILE
        effects_salt._create_in_place(salt)
        self.assert_one_grant(salt)

    def test_an_existing_home_is_never_restricted_by_the_salt(self):
        home = self.tmp / "gbhome"
        home.mkdir()
        effects_salt._create_salt(home / effects_salt.SALT_FILE)
        self.assertEqual(self.granted(home), 0)


# Funções que pedem um modo restrito sem terem nada a proteger: arquivo de prova apagado na hora ou trava
# sem conteúdo.
EXEMPT = {
    ("effects.py", "_accepts_a_file"): "probe file removed at once",
    ("library.py", "_create_lock"): "empty O_EXCL lock file with no content",
}
COVERED_BY = frozenset({"make_private", "_ensure_private_file", "_write_private", "copy_from_fd"})
PRIVATE = (0o600, 0o700)


class EveryPrivateSiteTests(unittest.TestCase):
    """Quem pede 0600/0700 no código do pacote tem de passar pelo helper (ou dizer por que não)."""

    def _functions(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield node

    def _calls(self, function):
        """As chamadas do corpo da função, sem entrar nas funções aninhadas."""
        stack = list(function.body)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Call):
                yield node
            stack.extend(ast.iter_child_nodes(node))

    def _name(self, call):
        func = call.func
        return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")

    def _has_private_literal(self, call):
        values = list(call.args) + [keyword.value for keyword in call.keywords if keyword.arg == "mode"]
        return any(isinstance(v, ast.Constant) and v.value in PRIVATE for v in values)

    def _is_effects_mkdir(self, call):
        func = call.func
        return isinstance(func, ast.Attribute) and func.attr == "mkdir" and getattr(func.value, "id", "") == "effects"

    def _asks_for_a_private_mode(self, call):
        """`os.open(..., 0o600)`, `x.chmod(0o600|0o700)`, `touch`, `mkstemp` ou um `mkdir(..., mode=0o700)`."""
        names = {"open", "chmod", "fchmod", "mkstemp", "touch", "mkdir"}
        return self._has_private_literal(call) and self._name(call) in names

    def _covers(self, call):
        """`make_private` e amigos, ou o `effects.mkdir` com modo restrito (ele aplica a ACL na criação)."""
        if self._is_effects_mkdir(call):
            return self._has_private_literal(call)
        return self._name(call) in COVERED_BY

    def test_every_function_that_creates_a_private_file_applies_the_acl(self):
        problems = []
        for path in sorted(PACKAGE.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for function in self._functions(tree):
                calls = list(self._calls(function))
                if not any(self._asks_for_a_private_mode(call) for call in calls):
                    continue
                if (path.name, function.name) in EXEMPT:
                    continue
                if not any(self._covers(call) for call in calls):
                    problems.append(f"{path.relative_to(PACKAGE)}:{function.lineno} {function.name}")
        self.assertEqual(problems, [], "pede 0600/0700 sem chamar `make_private`")

    def test_the_exemptions_still_name_a_function_that_asks_for_a_private_mode(self):
        for (name, function_name), reason in EXEMPT.items():
            with self.subTest(function=function_name, reason=reason):
                (path,) = list(PACKAGE.rglob(name))
                tree = ast.parse(path.read_text(encoding="utf-8"))
                (function,) = [f for f in self._functions(tree) if f.name == function_name]
                self.assertTrue(any(self._asks_for_a_private_mode(call) for call in self._calls(function)))


# The DACL in SDDL: `D:<flags>(<type>;<inheritance>;<rights>;;;<SID>)…`; OW = OWNER RIGHTS, SY = SYSTEM,
# BA = Administrators, FA = full control, P = protected (nothing inherited from the folder above).
PRIVATE_SIDS = ["BA", "OW", "SY"]


def _sddl_dacl(saved):
    """The `D:` part of what `icacls /save` wrote (UTF-16, with or without a BOM), without a SACL."""
    if saved[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = saved.decode("utf-16")
    elif b"\x00" in saved:
        text = saved.decode("utf-16-le")
    else:
        text = saved.decode("utf-8", "replace")
    (line,) = [line.strip() for line in text.splitlines() if line.strip().startswith("D:")]
    return line.split("S:", 1)[0]


def _parsed(dacl):
    """`(flags of the DACL, [(type, inheritance flags, rights, sid), …])`."""
    flags = dacl[2:].split("(", 1)[0]
    aces = []
    for ace in re.findall(r"\(([^)]*)\)", dacl):
        kind, inheritance, rights, _object, _inherited_object, sid = ace.split(";")[:6]
        aces.append((kind, {inheritance[i : i + 2] for i in range(0, len(inheritance), 2)}, rights, sid))
    return flags, aces


class SddlParsingTests(unittest.TestCase):
    """The reader of the oracle below, on the text `icacls /save` writes (it runs on every system)."""

    def test_a_saved_dacl_is_read_back_as_flags_and_entries(self):
        line = "D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)S:AI(ML;;NW;;;LW)"
        saved = ("sources\r\n" + line + "\r\n").encode("utf-16-le")
        for raw in (saved, b"\xff\xfe" + saved, ("sources\n" + line + "\n").encode("ascii")):
            with self.subTest(raw=raw[:4]):
                flags, aces = _parsed(_sddl_dacl(raw))
                self.assertEqual(flags, "PAI")
                self.assertEqual(sorted(sid for *_rest, sid in aces), PRIVATE_SIDS)
                self.assertTrue(all(ace[:3] == ("A", {"OI", "CI"}, "FA") for ace in aces), aces)

    def test_a_file_dacl_has_entries_with_no_inheritance_flag(self):
        flags, aces = _parsed("D:PAI(A;;FA;;;OW)(A;;FA;;;SY)(A;;FA;;;BA)")
        self.assertEqual(flags, "PAI")
        self.assertEqual(sorted(sid for *_rest, sid in aces), PRIVATE_SIDS)
        self.assertTrue(all(ace[:3] == ("A", set(), "FA") for ace in aces), aces)

    def test_an_inherited_entry_carries_id(self):
        _flags, aces = _parsed("D:AI(A;ID;FA;;;OW)(A;ID;FA;;;SY)(A;ID;FA;;;BA)")
        self.assertTrue(all(ace[1] == {"ID"} for ace in aces), aces)


class _FailureRecorder:
    """The real `subprocess.run` in place of `private_files._run_icacls`, keeping the exit of every failure."""

    def __init__(self):
        self.failures = []

    def __call__(self, argv, *, check, capture_output, timeout):
        try:
            return subprocess.run(argv, check=check, capture_output=capture_output, timeout=timeout)
        except subprocess.CalledProcessError as exc:
            self.failures.append(exc.returncode)
            raise


@unittest.skipUnless(os.name == "nt", "the real ACL exists only on Windows")
class RealWindowsAclTests(unittest.TestCase):
    """The oracle: `icacls /save` reads the ACL back in SDDL (the same text in every language of Windows) and
    only OWNER RIGHTS, SYSTEM and Administrators stay, with nothing inherited (Windows CI decides)."""

    def setUp(self):
        self.icacls = str(Path(os.environ.get("SYSTEMROOT") or "C:\\Windows", "System32", "icacls.exe"))

    def _dacl(self, path):
        out = Path(tempfile.mkdtemp(prefix="gb-acl-save-"))
        try:
            saved = out / "acl.txt"
            subprocess.run([self.icacls, str(path), "/save", str(saved)], check=True, capture_output=True)
            return _sddl_dacl(saved.read_bytes())
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def _new_file(self):
        folder = Path(tempfile.mkdtemp(prefix="gb-acl-"))
        self.addCleanup(shutil.rmtree, folder, True)
        path = folder / "private.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def assert_private(self, path, inheritance):
        dacl = self._dacl(path)
        flags, aces = _parsed(dacl)
        self.assertIn("P", flags, dacl)
        self.assertEqual(sorted(sid for *_rest, sid in aces), PRIVATE_SIDS, dacl)
        self.assertTrue(all(ace[:3] == ("A", inheritance, "FA") for ace in aces), dacl)

    def test_a_file_keeps_only_the_owner_system_and_administrators(self):
        path = self._new_file()
        private_files.make_private(path)
        self.assert_private(path, set())
        self.assertEqual(path.read_text(encoding="utf-8"), "{}")
        path.write_text('{"a": 1}', encoding="utf-8")

    def test_a_folder_and_what_is_made_in_it_keep_only_the_same_three(self):
        parent = Path(tempfile.mkdtemp(prefix="gb-acl-"))
        self.addCleanup(shutil.rmtree, parent, True)
        folder = parent / "sources"
        effects.mkdir(folder, mode=0o700)
        self.assert_private(folder, {"OI", "CI"})
        child = folder / "child.txt"
        child.write_text("x", encoding="utf-8")
        flags, aces = _parsed(self._dacl(child))
        self.assertEqual(sorted(sid for *_rest, sid in aces), PRIVATE_SIDS, (flags, aces))
        self.assertTrue(all("ID" in ace[1] for ace in aces), aces)
        self.assertEqual(child.read_text(encoding="utf-8"), "x")

    def test_a_grant_that_fails_never_leaves_the_file_without_an_acl(self):
        """A grant `icacls` refuses (a SID nobody has, a name that resolves to nobody) with
        `/inheritance:r` after it: the file keeps an ACL and stays readable."""
        unknown = f"gb-no-such-{uuid.uuid4().hex[:12]}"
        for principals in (("*S-1-3-4", "*S-1-5-21-0-0-0-999999"), (unknown,)):
            with self.subTest(principals=principals):
                path = self._new_file()
                before = self._dacl(path)
                run = _FailureRecorder()
                with (
                    mock.patch.object(private_files, "_PRINCIPALS", principals),
                    mock.patch.object(private_files, "_run_icacls", run),
                ):
                    private_files.make_private(path)
                if principals == (unknown,):
                    self.assertTrue(run.failures, "a name that resolves to nobody must make icacls fail")
                    self.assertEqual(self._dacl(path), before, "a refused grant must leave the ACL untouched")
                _flags, aces = _parsed(self._dacl(path))
                self.assertTrue(aces, "an empty DACL locks everybody out")
                self.assertEqual(path.read_text(encoding="utf-8"), "{}")

    def test_an_explicit_entry_of_another_account_is_dropped(self):
        """What `/grant:r` keeps (an explicit entry of another account, as the one of the account that created
        the object on Python 3.11) is gone after `make_private`: Everyone (`WD`) read, granted by hand."""
        path = self._new_file()
        subprocess.run([self.icacls, str(path), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True)
        self.assertIn("WD", self._dacl(path))
        private_files.make_private(path)
        self.assert_private(path, set())

    def test_a_name_with_spaces_and_accents_and_its_folder_keep_only_the_same_three(self):
        parent = Path(tempfile.mkdtemp(prefix="gb-acl-"))
        self.addCleanup(shutil.rmtree, parent, True)
        folder = parent / "Pasta com espaço"
        folder.mkdir()
        path = folder / "decisão final.json"
        path.write_text("{}", encoding="utf-8")
        private_files.make_private(folder)
        private_files.make_private(path)
        self.assert_private(folder, {"OI", "CI"})
        self.assert_private(path, set())
        self.assertEqual(path.read_text(encoding="utf-8"), "{}")

    def test_a_failure_to_set_the_acl_never_breaks_the_caller(self):
        private_files.make_private(Path(tempfile.gettempdir()) / "gb-acl-never-created-file")


if __name__ == "__main__":
    unittest.main()
