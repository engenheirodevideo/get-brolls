"""`_listing`: as duas travas que o Windows deixa na pasta saem das listagens só lá."""

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _listing

from getbrolls import effects, logs


class PlatformLockTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="gb-listing-")  # pylint: disable=consider-using-with
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for name in (logs.ROTATION_LOCK, effects.SALT_REPAIR_LOCK, "getbrolls.log", ".command.lock"):
            (self.folder / name).write_bytes(b"")

    def on(self, os_name):
        return mock.patch.object(_listing, "os", types.SimpleNamespace(name=os_name))

    def test_the_two_names_are_the_ones_the_code_writes(self):
        self.assertEqual({".getbrolls.log.lock", ".getbrolls-salt.lock"}, set(_listing.LOCK_NAMES))

    def test_on_windows_only_those_two_names_leave_the_listing(self):
        with self.on("nt"):
            self.assertEqual([".command.lock", "getbrolls.log"], _listing.visible_names(self.folder))
            self.assertEqual(
                [self.folder / ".command.lock", self.folder / "getbrolls.log"], _listing.visible_entries(self.folder)
            )
            self.assertTrue(_listing.is_platform_lock(logs.ROTATION_LOCK))
            self.assertFalse(_listing.is_platform_lock(".command.lock"))
            self.assertFalse(_listing.is_platform_lock("getbrolls.log.lock"))

    def test_elsewhere_the_listing_is_whole_so_a_stray_lock_is_still_a_finding(self):
        with self.on("posix"):
            self.assertEqual(
                [".command.lock", ".getbrolls-salt.lock", ".getbrolls.log.lock", "getbrolls.log"],
                _listing.visible_names(self.folder),
            )
            self.assertFalse(_listing.is_platform_lock(effects.SALT_REPAIR_LOCK))


if __name__ == "__main__":
    unittest.main()
