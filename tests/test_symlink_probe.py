"""`_symlink`: a sonda diz a verdade sobre o sistema e não deixa nada para trás."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _symlink


class ProbeTests(unittest.TestCase):
    def test_it_agrees_with_what_this_system_can_do(self):
        with tempfile.TemporaryDirectory(prefix="gb-symlink-check-") as raw:
            base = Path(raw)
            try:
                (base / "link").symlink_to(base, target_is_directory=True)
                can = True
            except (OSError, NotImplementedError):
                can = False
        self.assertEqual(can, _symlink.probe_symlinks())
        self.assertEqual(can, _symlink.CAN_SYMLINK)

    def test_a_system_that_refuses_the_link_is_reported_as_unable(self):
        for error in (
            OSError(1314, "privilégio ausente"),
            PermissionError("negado"),
            NotImplementedError("sem suporte"),
        ):
            with self.subTest(error=type(error).__name__), mock.patch.object(Path, "symlink_to", side_effect=error):
                self.assertFalse(_symlink.probe_symlinks())

    def test_a_link_that_does_not_resolve_is_not_a_working_link(self):
        with mock.patch.object(Path, "read_text", return_value="outra coisa"):
            self.assertFalse(_symlink.probe_symlinks())

    def test_the_probe_leaves_no_folder_behind(self):
        made = []
        real = tempfile.TemporaryDirectory

        def tracking(*args, **kwargs):
            folder = real(*args, **kwargs)  # pylint: disable=consider-using-with
            made.append(Path(folder.name))
            return folder

        with mock.patch.object(_symlink.tempfile, "TemporaryDirectory", tracking):
            _symlink.probe_symlinks()
        self.assertEqual(1, len(made))
        self.assertFalse(made[0].exists())

    def test_the_skip_reason_names_the_missing_capability(self):
        self.assertIn("link simbólico", _symlink.SKIP_REASON)
        self.assertIn("os.symlink", _symlink.SKIP_REASON)

    def test_the_decorator_skips_only_when_the_probe_failed(self):
        ran = []

        @_symlink.needs_symlink
        def body():
            ran.append(True)

        if _symlink.CAN_SYMLINK:
            body()
            self.assertEqual([True], ran)
        else:
            with self.assertRaises(unittest.SkipTest):
                body()


if __name__ == "__main__":
    unittest.main()
