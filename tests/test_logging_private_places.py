"""`getbrolls.logs`: a pasta e o arquivo privados do log onde o sistema não tem `O_DIRECTORY` /
`O_NOFOLLOW` (Windows, simulado), o que não é arquivo regular, a trava sem `flock`, e o log que
nunca derruba um comando (formatação quebrada, exceção hostil, pasta pessoal que não resolve).
"""

import contextlib
import errno
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths as _test_paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from _logs import home_log_lines, parse_line

from getbrolls import logs

# pylint: disable=protected-access

POSIX = os.name != "nt"


@contextlib.contextmanager
def without(name):
    """`os.<name>` ausente, como no Windows; volta ao sair."""
    if not hasattr(os, name):
        yield
        return
    with mock.patch.object(os, name, getattr(os, name)):
        delattr(os, name)
        yield


def _symlink_or_skip(case, link, target):
    try:
        link.symlink_to(target, target_is_directory=Path(target).is_dir())
    except (OSError, NotImplementedError) as exc:  # Windows sem privilégio de link
        case.skipTest(f"sem symlink: {exc}")


class EmptyValueTests(unittest.TestCase):
    """An empty text is `key=""` in the line, never `key=` (which would read as a missing value)."""

    def test_an_empty_value_is_quoted_and_reads_back_as_empty(self):
        line = logs.render_fields("demo", {"vazio": "", "nada": None, "texto": "ok"})
        self.assertEqual('event=demo vazio="" nada=- texto=ok', line)
        self.assertEqual({"event": "demo", "vazio": "", "nada": None, "texto": "ok"}, parse_line(line))


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-logs-private-")).resolve()
        self.addCleanup(_rmtree_quiet, self.tmp)


def _rmtree_quiet(path):
    with contextlib.suppress(OSError):
        for child in sorted(Path(path).rglob("*"), reverse=True):
            if child.is_dir() and not child.is_symlink():
                child.rmdir()
            else:
                child.unlink()
        Path(path).rmdir()


class PrivateFolderWithoutODirectoryTests(TmpCase):
    """Sem `O_DIRECTORY` (Windows), a pasta `logs/` é conferida por `lstat`, sem abrir."""

    def test_a_missing_logs_folder_is_created_under_a_new_home(self):
        folder = self.tmp / "home" / "logs"
        with without("O_DIRECTORY"):
            self.assertIsNone(logs._private_folder(folder))
        self.assertTrue(folder.is_dir())
        self.assertFalse(folder.is_symlink())

    def test_a_file_in_the_place_of_the_folder_is_refused(self):
        folder = self.tmp / "home" / "logs"
        folder.parent.mkdir()
        folder.write_text("não sou pasta", encoding="utf-8")
        with without("O_DIRECTORY"), self.assertRaises(OSError) as caught:
            logs._private_folder(folder)
        self.assertEqual(errno.EPERM, caught.exception.errno)
        self.assertIn("not a real folder", str(caught.exception))
        self.assertEqual("não sou pasta", folder.read_text(encoding="utf-8"))

    def test_a_link_in_the_place_of_the_folder_is_never_followed(self):
        elsewhere = self.tmp / "outra-pasta"
        elsewhere.mkdir()
        folder = self.tmp / "home" / "logs"
        folder.parent.mkdir()
        _symlink_or_skip(self, folder, elsewhere)
        with without("O_DIRECTORY"), self.assertRaises(OSError) as caught:
            logs._private_folder(folder)
        self.assertEqual(errno.EPERM, caught.exception.errno)
        self.assertEqual([], list(elsewhere.iterdir()))

    def test_the_installation_log_is_written_through_the_lstat_check(self):
        home = self.tmp / "home"
        state = mock.patch.dict(logs._state, {"signature": None, "target": None, "project": None})
        with (
            state,
            mock.patch.dict(os.environ, {"GB_HOME": str(home), "GB_LOG_LEVEL": "INFO"}),
            without("O_DIRECTORY"),
        ):
            try:
                logs.configure(None, read_only=False, installation=True)
                logs.event(logs.get("cli"), logging.INFO, "command_start", command="setup")
            finally:
                logs._degrade()
        rows = [parse_line(line) for line in home_log_lines(home)]
        self.assertEqual([("command_start", "setup")], [(row["event"], row["command"]) for row in rows])


class OpenPrivateTests(TmpCase):
    def test_without_o_nofollow_a_link_in_the_place_of_the_log_is_refused(self):
        real = self.tmp / "alvo.txt"
        real.write_text("intocado", encoding="utf-8")
        log = self.tmp / "getbrolls.log"
        _symlink_or_skip(self, log, real)
        with without("O_NOFOLLOW"), self.assertRaises(OSError) as caught:
            logs._open_private(log)
        self.assertEqual(errno.EPERM, caught.exception.errno)
        self.assertIn("link", str(caught.exception))
        self.assertEqual("intocado", real.read_text(encoding="utf-8"))

    @unittest.skipUnless(POSIX and Path(os.devnull).exists(), "dispositivo de caractere POSIX")
    def test_a_device_in_the_place_of_the_log_is_refused_and_closed(self):
        before = _open_fds()
        with self.assertRaises(OSError) as caught:
            logs._open_private(os.devnull)
        self.assertEqual(errno.EPERM, caught.exception.errno)
        self.assertIn("not a regular file", str(caught.exception))
        if before is not None:
            self.assertEqual(before, _open_fds())  # o descritor aberto foi fechado


def _open_fds():
    """Descritores abertos deste processo, onde o sistema os lista; None no resto."""
    for folder in ("/dev/fd", "/proc/self/fd"):
        with contextlib.suppress(OSError):
            return len(list(Path(folder).iterdir()))
    return None


class FolderLockWithoutFlockTests(TmpCase):
    def test_without_flock_the_lock_is_never_held_and_creates_nothing(self):
        with mock.patch.object(logs, "fcntl", None), logs.folder_lock(self.tmp, 5.0) as held:
            self.assertFalse(held)
        self.assertEqual([], list(self.tmp.iterdir()))


class LoggingNeverBreaksTests(unittest.TestCase):
    def test_a_message_whose_arguments_do_not_format_is_kept_raw_and_redacted(self):
        logger = logging.getLogger("test-broken-format")
        record = logger.makeRecord(
            "test-broken-format", logging.INFO, __file__, 0, "Bearer faketoken-abc123 total=%d", ("x",), None
        )
        self.assertTrue(logs.RedactingFilter().filter(record))
        self.assertEqual((), record.args)
        self.assertNotIn("faketoken-abc123", record.msg)
        self.assertIn("total=%d", record.msg)
        self.assertEqual(record.msg, record.getMessage())  # já não levanta

    def test_an_exception_class_that_breaks_on_inspection_has_no_error_code(self):
        class Hostile(type):
            @property
            def error_code(cls):
                raise RuntimeError("classe hostil")

        class PluginError(Exception, metaclass=Hostile):
            pass

        self.assertIsNone(logs.error_code(PluginError("x")))

    def test_without_a_home_folder_configure_degrades_and_log_path_is_none(self):
        state = mock.patch.dict(logs._state, {"signature": None, "target": None, "project": None})
        no_home = mock.patch.object(logs._paths, "gb_home", side_effect=RuntimeError("sem HOME"))
        with state, no_home:
            try:
                logs.configure(None, read_only=False, installation=True)  # nunca levanta
                handlers = list(logs._root.handlers)
                self.assertEqual([logging.NullHandler], [type(handler) for handler in handlers])
                self.assertIsNone(logs._state["signature"])
                self.assertIsNone(logs.log_path(None))
                self.assertIsNone(logs.log_path("proj"))  # nem o log do projeto: sem pasta pessoal, nenhum
            finally:
                logs._degrade()


if __name__ == "__main__":
    unittest.main()
