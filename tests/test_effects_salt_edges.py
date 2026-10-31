"""`getbrolls.effects`: o sal do log nas bordas (arquivo que não abre, pasta no lugar dele, dono
errado, `$GB_HOME` sem pasta pessoal) e a redação de alvos fora do comum.

Cada caso confere o estado que o `doctor` vê (`salt_state`), o aviso único `log_salt_unavailable`
e que o arquivo no disco ficou como estava; nunca um hash sem sal.
"""

import errno
import os
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths as _test_paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from test_effects import EffectsCase, target_of

from getbrolls import _salt_read, effects, effects_salt

# pylint: disable=protected-access

POSIX = os.name != "nt"
NOT_ROOT = POSIX and os.geteuid() != 0
HASH = r"#[0-9a-f]{8}"


class SaltCase(EffectsCase):
    """Um `$GB_HOME` próprio, nenhum sal em cache e nenhum aviso já dado neste processo."""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "gbhome"
        env = mock.patch.dict(os.environ, {"GB_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        salts = mock.patch.dict(effects_salt._salts, clear=True)
        salts.start()
        self.addCleanup(salts.stop)
        warned = mock.patch.object(effects_salt, "_salt_warned", set())
        warned.start()
        self.addCleanup(warned.stop)

    def salt_file(self):
        return self.home / effects_salt.SALT_FILE

    def warnings(self):
        return [fields["reason"] for _level, name, fields in self.recorder.calls if name == "log_salt_unavailable"]

    def write_salt(self, text):
        self.home.mkdir(exist_ok=True)
        self.salt_file().write_text(text, encoding="ascii")


class SaltFileStateTests(SaltCase):
    def test_a_salt_another_process_linked_first_is_the_one_used(self):
        # The race of two repairers: the name is already taken when this one links, so it keeps
        # the salt that won and drops its own temporary file (no coverage left to chance).
        good = "a" * 64
        self.write_salt(good + "\n")
        self.assertEqual(("ok", bytes.fromhex(good)), effects_salt._link_new_salt(self.salt_file()))
        self.assertEqual([effects_salt.SALT_FILE], [p.name for p in self.home.iterdir()])

    @unittest.skipUnless(NOT_ROOT, "permissões POSIX, sem root")
    def test_a_salt_this_user_cannot_read_is_unreadable_never_replaced_and_warns_once(self):
        good = "ab" * 32 + "\n"
        self.write_salt(good)
        self.salt_file().chmod(0)
        self.addCleanup(self.salt_file().chmod, 0o600)
        self.assertEqual("unreadable", effects.salt_state())
        first = target_of("texto livre do cliente")
        second = target_of("texto livre do cliente")
        self.assertRegex(first, f"^{HASH}$")
        self.assertEqual(first, second)  # o sal do processo vale até o fim dele
        self.assertEqual(["unreadable"], self.warnings())
        self.assertEqual([30], [level for level, name, _ in self.recorder.calls if name == "log_salt_unavailable"])
        self.salt_file().chmod(0o600)
        self.assertEqual(good, self.salt_file().read_text(encoding="ascii"))  # nunca trocado
        self.assertEqual([effects_salt.SALT_FILE], sorted(p.name for p in self.home.iterdir()))

    def test_a_link_swapped_in_after_the_check_is_refused_as_a_link(self):
        self.write_salt("ab" * 32 + "\n")
        real_open = os.open

        def raced(path, flags, *args):
            if Path(path) == self.salt_file():
                raise OSError(errno.ELOOP, "link no lugar do sal")
            return real_open(path, flags, *args)

        with mock.patch.object(effects_salt.os, "open", side_effect=raced):
            self.assertEqual("link", effects.salt_state())
            self.assertEqual(("link", None), effects_salt._inspect_salt(self.salt_file()))

    def test_a_salt_that_fails_to_open_for_another_reason_is_unreadable(self):
        self.write_salt("ab" * 32 + "\n")
        with mock.patch.object(effects_salt.os, "open", side_effect=OSError(errno.EIO, "disco")):
            self.assertEqual(("unreadable", None), effects_salt._inspect_salt(self.salt_file()))

    @unittest.skipUnless(POSIX, "abrir uma pasta com os.open é POSIX")
    def test_a_folder_in_the_place_of_the_salt_is_a_link_and_is_left_alone(self):
        self.salt_file().mkdir(parents=True)
        self.assertEqual("link", effects.salt_state())
        shown = target_of("texto livre do cliente")
        self.assertRegex(shown, f"^{HASH}$")
        self.assertEqual(["link"], self.warnings())
        self.assertTrue(self.salt_file().is_dir())
        self.assertEqual([effects_salt.SALT_FILE], sorted(p.name for p in self.home.iterdir()))

    @unittest.skipUnless(POSIX, "dono do arquivo é POSIX")
    def test_a_salt_of_another_user_is_never_used(self):
        self.write_salt("cd" * 32 + "\n")
        with mock.patch.object(effects_salt.os, "geteuid", return_value=os.geteuid() + 1):
            self.assertEqual("link", effects.salt_state())
            shown = target_of("texto livre do cliente")
        self.assertEqual(["link"], self.warnings())
        self.fresh_salts()
        self.assertNotEqual(shown, target_of("texto livre do cliente"))  # o do outro dono não era o usado
        self.assertEqual("cd" * 32 + "\n", self.salt_file().read_text(encoding="ascii"))

    def fresh_salts(self):
        effects_salt._salts.clear()
        effects_salt._salt_warned.clear()

    def test_a_read_that_fails_after_opening_is_unreadable(self):
        self.write_salt("ab" * 32 + "\n")
        with mock.patch.object(effects_salt.os, "read", side_effect=OSError(errno.EIO, "disco")):
            self.assertEqual(("unreadable", None), effects_salt._inspect_salt(self.salt_file()))
        self.assertEqual(("ok", bytes.fromhex("ab" * 32)), effects_salt._inspect_salt(self.salt_file()))

    @unittest.skipUnless(NOT_ROOT, "permissões POSIX, sem root")
    def test_a_malformed_salt_in_a_read_only_home_says_both_and_stays_on_disk(self):
        self.write_salt("nao-e-sal\n")
        self.home.chmod(0o500)
        self.addCleanup(self.home.chmod, 0o700)
        shown = target_of("texto livre do cliente")
        self.assertRegex(shown, f"^{HASH}$")
        self.assertEqual(["malformed", "readonly"], self.warnings())  # o conserto negado também é dito
        self.assertEqual("nao-e-sal\n", self.salt_file().read_text(encoding="ascii"))
        self.assertEqual([effects_salt.SALT_FILE], sorted(p.name for p in self.home.iterdir()))


class SaltSharingViolationTests(SaltCase):
    """Windows refuses to open or rename a file another process is renaming or reading (`PermissionError`,
    `WinError` 32 or 5): the read and the set-aside try again a few times before giving up."""

    @staticmethod
    def sharing_violation():
        # This shape (a `winerror`) is what `os.replace` and `os.link` raise; CPython's `os.open` on Windows raises a
        # plain EACCES with no `winerror` (see `OpenRefusalWithoutWinerrorTests`). Kept: the retry logic is the same.
        error = PermissionError(errno.EACCES, "O arquivo está em uso por outro processo")
        error.winerror = 32  # pyright: ignore[reportAttributeAccessIssue]  # set by hand: POSIX has no such attribute
        return error

    def failing_open(self, times):
        """`os.open` that raises a sharing violation `times` times for the salt, then opens it."""
        real_open, left = os.open, [times]

        def flaky(path, flags, *args, **kwargs):
            if Path(path) == self.salt_file() and left[0] > 0:
                left[0] -= 1
                raise self.sharing_violation()
            return real_open(path, flags, *args, **kwargs)

        return mock.patch.object(effects_salt.os, "open", side_effect=flaky)

    def test_an_open_that_is_busy_a_few_times_is_read_in_the_end(self):
        self.write_salt("ab" * 32 + "\n")
        with self.failing_open(effects_salt._SHARING_TRIES - 1), mock.patch.object(effects_salt.time, "sleep") as slept:
            self.assertEqual(("ok", bytes.fromhex("ab" * 32)), effects_salt._inspect_salt(self.salt_file()))
        self.assertEqual(effects_salt._SHARING_TRIES - 1, slept.call_count)
        self.assertEqual([], self.warnings())

    def test_an_open_that_stays_busy_is_unreadable_after_exactly_the_tries(self):
        self.write_salt("ab" * 32 + "\n")
        with (
            self.failing_open(effects_salt._SHARING_TRIES + 3) as opened,
            mock.patch.object(effects_salt.time, "sleep") as slept,
        ):
            self.assertEqual(("unreadable", None), effects_salt._inspect_salt(self.salt_file()))
        self.assertEqual(effects_salt._SHARING_TRIES, opened.call_count)
        self.assertEqual(effects_salt._SHARING_TRIES - 1, slept.call_count)

    def test_a_permission_error_that_is_no_sharing_violation_is_not_tried_again(self):
        self.write_salt("ab" * 32 + "\n")
        denied = PermissionError(errno.EACCES, "Permission denied")  # no `winerror`: a real EACCES (POSIX)
        with (
            mock.patch.object(_salt_read, "WINDOWS", False),  # on Windows this shape may be a sharing violation
            mock.patch.object(effects_salt.os, "open", side_effect=denied) as opened,
            mock.patch.object(effects_salt.time, "sleep") as slept,
        ):
            self.assertEqual(("unreadable", None), effects_salt._inspect_salt(self.salt_file()))
        self.assertEqual((1, 0), (opened.call_count, slept.call_count))

    def test_a_busy_open_does_not_make_a_process_use_a_salt_of_its_own(self):
        self.write_salt("cd" * 32 + "\n")
        with self.failing_open(2), mock.patch.object(effects_salt.time, "sleep"):
            shown = target_of("texto livre do cliente")
        self.assertEqual([], self.warnings())
        self.fresh_salts()
        self.assertEqual(shown, target_of("texto livre do cliente"))  # the one on disk was used

    def fresh_salts(self):
        effects_salt._salts.clear()
        effects_salt._salt_warned.clear()

    def busy_replace(self, times):
        """`Path.replace` that raises a sharing violation `times` times, then moves the file."""
        real_replace, left = Path.replace, [times]

        def flaky(path, target):
            if left[0] > 0:
                left[0] -= 1
                raise self.sharing_violation()
            return real_replace(path, target)

        return mock.patch.object(Path, "replace", flaky)

    def test_a_set_aside_that_is_busy_a_few_times_still_moves_the_file(self):
        self.write_salt("nao-e-sal\n")
        with self.busy_replace(effects_salt._SHARING_TRIES - 1), mock.patch.object(effects_salt.time, "sleep") as slept:
            effects_salt._set_aside(self.salt_file())
        self.assertEqual(effects_salt._SHARING_TRIES - 1, slept.call_count)
        self.assertFalse(self.salt_file().exists())

    def test_a_set_aside_that_stays_busy_raises_after_exactly_the_tries(self):
        self.write_salt("nao-e-sal\n")
        with (
            self.busy_replace(effects_salt._SHARING_TRIES + 3),
            mock.patch.object(effects_salt.time, "sleep") as slept,
            self.assertRaises(PermissionError),
        ):
            effects_salt._set_aside(self.salt_file())
        self.assertEqual(effects_salt._SHARING_TRIES - 1, slept.call_count)
        self.assertEqual("nao-e-sal\n", self.salt_file().read_text(encoding="ascii"))

    def test_a_repair_that_is_busy_a_few_times_ends_with_the_salt_that_persisted(self):
        self.write_salt("nao-e-sal\n")
        with self.busy_replace(2), mock.patch.object(effects_salt.time, "sleep"):
            shown = target_of("texto livre do cliente")
        self.assertEqual(["malformed"], self.warnings())  # the malformed file is warned about once, nothing more
        on_disk = self.salt_file().read_text(encoding="ascii").strip()
        self.assertRegex(on_disk, r"^[0-9a-f]{64}$")
        self.fresh_salts()
        self.assertEqual(shown, target_of("texto livre do cliente"))  # the persisted salt, not one of the process


class SaltReadRetryTests(unittest.TestCase):
    """A salt still being written (short hex) is read again: exactly `tries` reads, then malformed."""

    def inspect(self, answers, tries):
        reads = iter(answers)
        with (
            mock.patch.object(_salt_read, "read_salt_once", side_effect=lambda _path: next(reads)) as read,
            mock.patch.object(effects_salt.time, "sleep") as slept,
        ):
            result = effects_salt._inspect_salt(Path("sal"), tries=tries)  # pylint: disable=protected-access
        return result, read.call_count, slept.call_count

    def test_a_salt_that_stays_partial_is_read_exactly_tries_times(self):
        for tries in (1, 2, 3, 25):
            with self.subTest(tries=tries):
                result, reads, sleeps = self.inspect([(None, "ab")] * (tries + 5), tries)
                self.assertEqual((("malformed", None), tries, tries - 1), (result, reads, sleeps))

    def test_a_salt_completed_on_the_last_read_is_used(self):
        salt = "c" * 64
        result, reads, sleeps = self.inspect([(None, "")] * 2 + [(None, salt)], 3)
        self.assertEqual((("ok", bytes.fromhex(salt)), 3, 2), (result, reads, sleeps))

    def test_text_that_is_not_hex_is_malformed_at_once(self):
        result, reads, sleeps = self.inspect([(None, "não é hex")], 25)
        self.assertEqual((("malformed", None), 1, 0), (result, reads, sleeps))


class NoHomeSaltTests(SaltCase):
    def test_without_a_home_folder_doctor_says_unreadable(self):
        with mock.patch.object(effects_salt._paths, "gb_home", side_effect=RuntimeError("sem HOME")):
            self.assertEqual("unreadable", effects.salt_state())

    def test_without_a_home_folder_the_process_uses_its_own_salt_and_warns_once(self):
        with mock.patch.object(effects_salt._paths, "gb_home", side_effect=RuntimeError("sem HOME")):
            first = target_of("texto livre do cliente")
            second = target_of("texto livre do cliente")
        self.assertRegex(first, f"^{HASH}$")
        self.assertEqual(first, second)
        self.assertEqual(["readonly"], self.warnings())
        self.assertFalse(self.home.exists())

    def test_a_home_with_no_existing_folder_up_to_the_root_is_readonly(self):
        # Um `$GB_HOME` num disco que não existe (Windows `Z:\`): nada até a raiz para criar o sal.
        with mock.patch.object(effects_salt.os, "lstat", side_effect=FileNotFoundError(errno.ENOENT, "sem disco")):
            self.assertEqual("readonly", effects.salt_state())

    def test_a_parent_that_cannot_be_inspected_is_readonly(self):
        real_lstat = os.lstat
        blocked = self.home.parent

        def lstat(path, *args, **kwargs):
            path = Path(path)
            if path in (self.salt_file(), self.home):
                raise FileNotFoundError(errno.ENOENT, "ainda não existe")
            if path == blocked:
                raise PermissionError(errno.EACCES, "sem permissão")
            return real_lstat(path, *args, **kwargs)

        with mock.patch.object(effects_salt.os, "lstat", side_effect=lstat):
            self.assertEqual("readonly", effects.salt_state())
        self.assertFalse(self.home.exists())


class TargetRedactionEdgeTests(SaltCase):
    def test_a_path_under_an_explicit_runtime_dir_is_relative_to_it(self):
        runtime_dir = self.tmp / "runtime-cliente"
        target = runtime_dir / "pasta-cliente" / "estado.json"
        outside = target_of(target)
        self.assertRegex(outside, f"^{HASH}(\\.json)?$")  # sem a variável, só o hash do caminho inteiro
        with mock.patch.dict(os.environ, {"GB_RUNTIME_DIR": str(runtime_dir)}):
            shown = target_of(target)
        self.assertRegex(shown, f"^\\$GB_RUNTIME_DIR/{HASH}/{HASH}(\\.json)?$")
        self.assertNotIn("cliente", shown)

    def test_a_shell_text_with_an_unclosed_quote_keeps_only_its_first_word(self):
        shown = target_of([effects.ShellText('ffmpeg -i "/srv/cliente/segredo.mov')])
        self.assertEqual("ffmpeg", shown)
        self.assertEqual("ffmpeg", target_of((effects.ShellText("C:/tools/ffmpeg.EXE 'sem fim"),)))

    def test_a_command_given_as_bytes_is_never_spelled_out(self):
        self.assertEqual("-", effects.redact_target((b"/srv/cliente/bin/segredo",)))
        with effects.op("proc.run", [b"/srv/cliente/bin/segredo", b"--token"]):
            pass
        row = self.recorder.one()
        self.assertEqual(("proc.run", "-", "ok"), (row["operation"], row["target"], row["outcome"]))
        self.assertNotIn("cliente", repr(self.recorder.calls))

    def test_an_empty_command_logs_the_dash(self):
        self.assertEqual("-", effects.redact_target([""]))
        self.assertEqual("-", effects.redact_target([]))


if __name__ == "__main__":
    unittest.main()
