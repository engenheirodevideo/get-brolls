"""capabilities._json_safe: valores de default do argparse viram JSON sem perder o tipo."""

import unittest
from pathlib import Path, PureWindowsPath

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import capabilities


class JsonSafeTests(unittest.TestCase):
    def test_scalars_pass_through_unchanged(self):
        for value in ("texto", 3, 1.5, True, None):
            with self.subTest(value=value):
                self.assertEqual(value, capabilities._json_safe(value))

    def test_sequences_and_sets_become_lists_recursively(self):
        self.assertEqual(["a", 1, [2, None]], capabilities._json_safe(("a", 1, [2, None])))
        self.assertEqual([7], capabilities._json_safe(frozenset({7})))
        self.assertEqual([7], capabilities._json_safe({7}))

    def test_any_other_object_becomes_its_string(self):
        self.assertEqual("a/b", capabilities._json_safe(Path("a/b")))
        self.assertEqual(["a/b"], capabilities._json_safe((Path("a/b"),)))

    def test_a_windows_path_is_written_with_slashes(self):
        self.assertEqual("a/b", capabilities._json_safe(PureWindowsPath("a\\b")))
        self.assertEqual(["C:/x/y.json"], capabilities._json_safe([PureWindowsPath("C:\\x\\y.json")]))


if __name__ == "__main__":
    unittest.main()
