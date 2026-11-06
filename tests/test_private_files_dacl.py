"""O segundo passo de `runtime.make_private` no Windows: `icacls <pasta> /restore <arquivo> /L` grava a DACL inteira.

A concessão (`/grant:r … /inheritance:r`) deixa as outras entradas explícitas, como a da conta que criou o objeto;
o `/restore` as tira e deixa só o dono (OWNER RIGHTS), SYSTEM e Administradores. O Windows é simulado como em
`test_private_files` (o `icacls` é um gravador); o oráculo de verdade é `RealWindowsAclTests`, lá.
"""

import contextlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths as _test_paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from test_private_files import WindowsCase, _finished, expected_argv

from getbrolls import runtime

# pylint: disable=protected-access


def expected_restore(exe, path, acl):
    """O segundo argv, escrito à mão: a pasta do objeto, o arquivo que `/restore` lê e `/L`."""
    return [exe, str(Path(path).parent), "/restore", str(acl), "/L"]


def expected_acl(name, flags=""):
    """O texto de `/restore`, escrito à mão: o nome e a DACL protegida com os três SIDs, em UTF-16 LE com CRLF."""
    aces = "".join(f"(A;{flags};FA;;;{sid})" for sid in ("S-1-3-4", "S-1-5-18", "S-1-5-32-544"))
    return f"{name}\r\nD:PAI{aces}\r\n".encode("utf-16-le", "surrogatepass")


class ExactDaclTests(WindowsCase):
    """A concessão deixa outras entradas explícitas (a conta que criou o objeto); o `/restore` que vem depois
    grava a DACL inteira, só com os três SIDs, e só roda se a concessão deu certo."""

    def _file(self, name="private.json"):
        path = self.tmp / name
        path.write_text("{}", encoding="utf-8")
        return path

    def only_restore(self):
        """O único `(argv, texto)` de `/restore` pedido."""
        self.assertEqual(1, len(self.restored), self.calls())
        return self.restored[0]

    def acl_files(self):
        return [Path(argv[argv.index("/restore") + 1]) for argv, _acl in self.restored]

    def test_a_file_gets_the_grant_then_the_whole_dacl_with_only_the_three_sids(self):
        path = self._file()
        runtime.make_private(path)
        argv, acl = self.only_restore()
        self.assertEqual(self.calls(), [expected_argv(self.exe, path), expected_restore(self.exe, path, argv[3])])
        self.assertEqual(acl, expected_acl("private.json"))

    def test_a_folder_gets_the_three_sids_inheritable(self):
        folder = self.tmp / "sources"
        folder.mkdir()
        runtime.make_private(folder)
        argv, acl = self.only_restore()
        self.assertEqual(
            self.calls(),
            [expected_argv(self.exe, folder, "(OI)(CI)(F)"), expected_restore(self.exe, folder, argv[3])],
        )
        self.assertEqual(acl, expected_acl("sources", "OICI"))

    def test_the_acl_text_names_the_object_by_its_name_never_by_its_path(self):
        path = self._file("Pasta com espaço e ção.json")
        runtime.make_private(path)
        _argv, acl = self.only_restore()
        text = acl.decode("utf-16-le")
        self.assertEqual(text.split("\r\n", maxsplit=1)[0], "Pasta com espaço e ção.json")
        self.assertNotIn(str(self.tmp), text)
        self.assertFalse(acl.startswith(b"\xff\xfe"), "icacls /save writes no BOM")

    def test_a_failed_grant_never_reaches_the_restore(self):
        self.icacls.side_effect = subprocess.CalledProcessError(5, ["icacls"])
        path = self._file()
        runtime.make_private(path)
        self.assertEqual(self.calls(), [expected_argv(self.exe, path)])
        (row,) = self.recorder.ops()
        self.assertEqual((row["target"], row["outcome"], row["exit"]), ("icacls", "error", 5))

    def test_a_failed_restore_is_one_error_line_after_the_grant_and_never_raises(self):
        self.icacls.side_effect = [_finished(), subprocess.CalledProcessError(2, ["icacls"])]
        path = self._file()
        runtime.make_private(path)
        self.assertEqual(2, len(self.calls()))
        self.assertIn("/restore", self.calls()[1])
        (row,) = self.recorder.ops()
        self.assertEqual(
            (row["operation"], row["target"], row["outcome"], row["error"], row["exit"]),
            ("proc.run", "icacls", "error", "CalledProcessError", 2),
        )
        self.assertNotIn(str(self.tmp), repr(self.recorder.calls))
        self.assertNotIn("S-1-", repr(self.recorder.calls))

    def test_a_restore_that_hangs_is_an_error_line(self):
        self.icacls.side_effect = [_finished(), subprocess.TimeoutExpired(["icacls"], 30)]
        runtime.make_private(self._file())
        (row,) = self.recorder.ops()
        self.assertEqual((row["outcome"], row["error"]), ("error", "TimeoutExpired"))
        self.assertNotIn("exit", row, "the exit of the grant that passed is not the one of the restore")

    def test_the_acl_text_lives_in_the_profile_temporary_folder_never_in_temp_and_is_gone_afterwards(self):
        shared = self.tmp / "shared-temp"
        shared.mkdir()
        with (
            mock.patch.dict(os.environ, {"TEMP": str(shared), "TMP": str(shared), "TMPDIR": str(shared)}),
            mock.patch.object(tempfile, "tempdir", str(shared)),
        ):
            runtime.make_private(self._file())
        (acl_file,) = self.acl_files()
        self.assertEqual(acl_file.parent, self.local / "Temp")
        self.assertTrue(acl_file.name.startswith("gb-acl-"))
        self.assertFalse(acl_file.exists())
        self.assertEqual(list(shared.iterdir()), [])
        self.assertEqual(list((self.local / "Temp").iterdir()), [])

    def test_the_acl_text_is_made_only_after_the_grant_ran(self):
        seen = []
        real = self._icacls

        def icacls(argv, **kwargs):
            seen.append(sorted(entry.name for entry in (self.local / "Temp").iterdir()))
            return real(argv, **kwargs)

        self.icacls.side_effect = icacls
        runtime.make_private(self._file())
        self.assertEqual([], seen[0], "no ACL text while the grant runs")
        self.assertEqual(1, len(seen[1]))

    def test_without_a_profile_temporary_folder_the_grant_stays_and_the_line_is_an_error(self):
        for environ in ({"LOCALAPPDATA": ""}, {"LOCALAPPDATA": "relative"}, {"LOCALAPPDATA": str(self.tmp / "nada")}):
            with self.subTest(environ=environ), mock.patch.dict(os.environ, environ):
                self.icacls.reset_mock()
                self.recorder.calls.clear()
                path = self._file()
                runtime.make_private(path)
                self.assertEqual(self.calls(), [expected_argv(self.exe, path)])
                (row,) = self.recorder.ops()
                self.assertEqual(
                    (row["target"], row["outcome"], row["error"]), ("icacls", "error", "FileNotFoundError")
                )
                self.assertNotIn("exit", row)
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": ""}):
            self.recorder.calls.clear()
            runtime.make_private(self._file(), record=False)
        self.assertEqual(self.recorder.calls, [])

    def test_a_name_with_a_lone_surrogate_is_written_as_it_is_on_disk(self):
        acl = runtime._owner_only_acl("a\udc80.json", False)
        self.assertEqual(acl, expected_acl("a\udc80.json"))
        self.assertEqual("a\udc80.json", acl.decode("utf-16-le", "surrogatepass").split("\r\n", maxsplit=1)[0])

    def test_the_acl_text_is_gone_after_a_failure_or_an_interrupt(self):
        made = []
        real = tempfile.mkstemp

        def mkstemp(*args, **kwargs):
            made.append(real(*args, **kwargs))
            return made[-1]

        for failure in (subprocess.CalledProcessError(5, ["icacls"]), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), mock.patch("tempfile.mkstemp", mkstemp):
                made.clear()
                self.icacls.side_effect = [_finished(), failure]
                with contextlib.suppress(KeyboardInterrupt):
                    runtime.make_private(self._file())
                self.assertEqual(1, len(made))
                acl_file = made[0][1]
                self.assertFalse(Path(acl_file).exists())

    def test_nothing_is_written_when_the_object_is_refused_or_the_tool_is_missing(self):
        with mock.patch("tempfile.mkstemp", side_effect=AssertionError("no ACL text before the checks")) as mkstemp:
            runtime.make_private(self.tmp / "nothing")
            Path(self.exe).unlink()
            runtime.make_private(self._file())
        mkstemp.assert_not_called()
        self.icacls.assert_not_called()
        self.assertEqual([row["outcome"] for row in self.recorder.ops()], ["error", "error"])

    def test_each_call_is_exactly_one_op_line_named_by_the_tool(self):
        first, second = self._file("a.json"), self._file("b.json")
        runtime.make_private(first)
        runtime.make_private(second)
        self.assertEqual(4, len(self.calls()))
        rows = self.recorder.ops()
        self.assertEqual(
            [(row["operation"], row["target"], row["outcome"], row["exit"]) for row in rows],
            [("proc.run", "icacls", "ok", 0)] * 2,
        )
        self.assertNotIn(str(self.tmp), repr(self.recorder.calls))
        self.assertNotIn("gb-acl-", repr(self.recorder.calls))

    def test_the_handler_files_run_both_steps_without_a_line(self):
        path = self._file("getbrolls.log")
        runtime.make_private(path, record=False)
        self.assertEqual([argv[2] for argv in self.calls()], ["/grant:r", "/restore"])
        self.assertEqual(self.recorder.calls, [])
        self.icacls.reset_mock()
        self.icacls.side_effect = [_finished(), subprocess.CalledProcessError(2, ["icacls"])]
        runtime.make_private(path, record=False)
        self.assertEqual(2, len(self.calls()))
        self.assertEqual(self.recorder.calls, [])


if __name__ == "__main__":
    unittest.main()
