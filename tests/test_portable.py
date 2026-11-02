"""`_portable.unlink_frozen`: apagar o que a entrega congelou, no POSIX e na simulação do Windows."""

import os
import stat
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _portable


class UnlinkFrozenTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="gb-portable-")  # pylint: disable=consider-using-with
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.clip = self.folder / "clip.mp4"
        self.clip.write_bytes(b"clip")
        self.clip.chmod(stat.S_IREAD)

    def test_a_frozen_file_is_gone_afterwards(self):
        _portable.unlink_frozen(self.clip)
        self.assertFalse(self.clip.exists())

    def test_a_frozen_hardlink_leaves_the_other_name_alone(self):
        other = self.folder / "entrega.mp4"
        os.link(self.clip, other)
        _portable.unlink_frozen(other)
        self.assertEqual(b"clip", self.clip.read_bytes())

    def test_on_windows_the_write_bit_comes_back_before_the_unlink(self):
        calls = []
        real_chmod = Path.chmod

        def chmod(path, mode, **kwargs):
            calls.append((path.name, mode))
            return real_chmod(path, mode, **kwargs)

        with (
            mock.patch.object(_portable, "os", types.SimpleNamespace(name="nt")),
            mock.patch.object(Path, "chmod", chmod),
        ):
            _portable.unlink_frozen(self.clip)
        self.assertEqual([("clip.mp4", stat.S_IWRITE | stat.S_IREAD)], calls)
        self.assertFalse(self.clip.exists())

    def test_elsewhere_the_mode_is_not_touched(self):
        # `setUp` froze the file; on Windows the read-only attribute would stop the unlink below, and the
        # patched `Path.chmod` must stay the proof that `unlink_frozen` itself touched nothing.
        self.clip.chmod(stat.S_IWRITE | stat.S_IREAD)  # the real `Path.chmod`: the patch below starts after this
        with (
            mock.patch.object(_portable, "os", types.SimpleNamespace(name="posix")),
            mock.patch.object(Path, "chmod", side_effect=AssertionError("chmod")),
        ):
            _portable.unlink_frozen(self.clip)
        self.assertFalse(self.clip.exists())


if __name__ == "__main__":
    unittest.main()
