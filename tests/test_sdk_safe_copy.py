"""Abrir e copiar arquivo da pessoa pelo descritor, sem seguir link (`sdk.safe_copy`)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.http import ProviderError
from getbrolls.sdk import safe_copy
from getbrolls.sdk.api import PluginApi, route_scope
from getbrolls.sdk.manifest import read_manifest
from getbrolls.sdk.registry import Registry
from getbrolls.sdk.safe_copy import UnsafeFileError

POSIX = os.name != "nt"


class SafeCopyTestCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-safe-copy-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.source = self.base / "som.wav"
        self.source.write_bytes(b"onda sonora")

    def refused(self, reason, fn, *args, **kwargs):
        with self.assertRaises(UnsafeFileError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(reason, caught.exception.reason)
        return caught.exception


class OpenRegularTests(SafeCopyTestCase):
    def test_regular_file_opens_with_its_stat(self):
        fd, info = safe_copy.open_regular(self.source)
        self.addCleanup(os.close, fd)
        self.assertEqual(len(b"onda sonora"), info.st_size)
        self.assertEqual(self.source.stat().st_ino, info.st_ino)

    def test_directory_and_missing_file_are_refused(self):
        with self.assertRaises(UnsafeFileError) as caught:
            safe_copy.open_regular(self.base)
        # O Windows nem abre uma pasta com os.open; no POSIX ela abre e o fstat recusa.
        self.assertIn(caught.exception.reason, (safe_copy.NOT_REGULAR, safe_copy.OPEN_FAILED))
        error = self.refused(safe_copy.OPEN_FAILED, safe_copy.open_regular, self.base / "nada.wav")
        self.assertEqual("FileNotFoundError", error.type_name)

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_symlink_is_not_followed(self):
        link = self.base / "atalho.wav"
        link.symlink_to(self.source)
        self.refused(safe_copy.OPEN_FAILED, safe_copy.open_regular, link)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO é POSIX")
    def test_fifo_is_refused_without_blocking(self):
        fifo = self.base / "fila.wav"
        os.mkfifo(fifo)
        self.refused(safe_copy.NOT_REGULAR, safe_copy.open_regular, fifo)

    def test_hardlink_is_refused_unless_allowed(self):
        os.link(self.source, self.base / "outro-nome.wav")
        self.refused(safe_copy.LINKED, safe_copy.open_regular, self.source)
        fd, info = safe_copy.open_regular(self.source, single_link=False)
        os.close(fd)
        self.assertEqual(2, info.st_nlink)


class CopyFromFdTests(SafeCopyTestCase):
    def open(self):
        fd, _ = safe_copy.open_regular(self.source)
        self.addCleanup(os.close, fd)
        return fd

    def test_copy_is_a_new_file_with_the_same_bytes(self):
        target = self.base / "copia.wav"
        self.assertEqual(11, safe_copy.copy_from_fd(self.open(), target, cap=1024))
        self.assertEqual(b"onda sonora", target.read_bytes())
        self.assertNotEqual(self.source.stat().st_ino, target.stat().st_ino)
        if POSIX:
            self.assertEqual(0o600, target.stat().st_mode & 0o777)

    def test_cap_counts_bytes_read_and_removes_the_partial_file(self):
        target = self.base / "copia.wav"
        self.refused(safe_copy.TOO_BIG, safe_copy.copy_from_fd, self.open(), target, 4)
        self.assertFalse(target.exists())

    def test_existing_target_is_never_overwritten(self):
        target = self.base / "copia.wav"
        target.write_bytes(b"da pessoa")
        self.refused(safe_copy.TARGET_EXISTS, safe_copy.copy_from_fd, self.open(), target, 1024)
        self.assertEqual(b"da pessoa", target.read_bytes())

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_planted_link_at_the_target_is_not_followed(self):
        planted = self.base / "plantado.wav"
        target = self.base / "copia.wav"
        target.symlink_to(planted)
        with self.assertRaises(UnsafeFileError):
            safe_copy.copy_from_fd(self.open(), target, 1024)
        self.assertFalse(planted.exists())


class RecheckTests(SafeCopyTestCase):
    def found(self):
        info = self.source.stat()
        return info.st_dev, info.st_ino, info.st_size

    def test_same_file_passes_by_path_and_by_fd(self):
        fd, info = safe_copy.recheck(self.source, *self.found())
        self.addCleanup(os.close, fd)
        self.assertEqual(self.found(), (info.st_dev, info.st_ino, info.st_size))
        same_fd, _ = safe_copy.recheck(fd, *self.found())
        self.assertEqual(fd, same_fd)

    def test_file_swapped_after_the_hit_is_refused(self):
        found = self.found()
        swapped = self.base / "trocado.wav"
        swapped.write_bytes(b"outro audio")
        swapped.replace(self.source)
        self.refused(safe_copy.CHANGED, safe_copy.recheck, self.source, *found)

    def test_changed_size_or_new_hardlink_is_refused(self):
        dev, ino, size = self.found()
        self.refused(safe_copy.CHANGED, safe_copy.recheck, self.source, dev, ino, size + 1)
        os.link(self.source, self.base / "outro-nome.wav")
        self.refused(safe_copy.LINKED, safe_copy.recheck, self.source, dev, ino, size)


class RootTests(SafeCopyTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.base.resolve()
        self.root = self.base / "acervo"
        (self.root / "sub").mkdir(parents=True)
        self.inner = self.root / "sub" / "eco.wav"
        self.inner.write_bytes(b"eco")

    def test_within_roots_compares_the_real_folders(self):
        self.assertTrue(safe_copy.within_roots(self.inner, [self.root]))
        self.assertFalse(safe_copy.within_roots(self.source, [self.root]))
        self.assertFalse(safe_copy.within_roots(self.inner, [self.base / "nao-existe"]))
        self.assertFalse(safe_copy.within_roots(self.root, [self.root]))  # a raiz em si não é um arquivo dentro dela

    def test_open_under_accepts_a_file_inside(self):
        fd, info = safe_copy.open_under(self.inner, [self.root])
        os.close(fd)
        self.assertEqual(self.inner.stat().st_ino, info.st_ino)
        self.refused(safe_copy.OUTSIDE, safe_copy.open_under, self.source, [self.root])

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_folder_swapped_after_the_open_is_caught(self):
        fd, _ = safe_copy.open_regular(self.inner)
        self.addCleanup(os.close, fd)
        outside = self.base / "fora"
        outside.mkdir()
        (outside / "eco.wav").write_bytes(b"outro eco")
        (self.root / "sub").rename(self.base / "sub-original")
        (self.root / "sub").symlink_to(outside)
        self.refused(safe_copy.OUTSIDE, safe_copy.still_under, self.inner, [self.root], fd)


class LocalFileSwapTests(LoaderTestCase):
    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_folder_swapped_before_the_open_is_refused(self):
        base = Path(tempfile.mkdtemp(prefix="gb-swap-")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root, outside, work = base / "acervo", base / "fora", base / "work"
        for folder in (root / "sub", outside / "sub", work):
            folder.mkdir(parents=True)
        (root / "sub" / "praia.mp4").write_bytes(b"de dentro")
        (outside / "sub" / "praia.mp4").write_bytes(b"de fora")
        manifest = {**MANIFEST, "permissions": {**MANIFEST["permissions"], "paths": [str(root)]}}
        api = PluginApi(read_manifest(self.install(manifest)), Registry())
        real_open = safe_copy.open_regular

        def swapping_open(path, **kwargs):
            (root / "sub").rename(base / "sub-original")
            (root / "sub").symlink_to(outside / "sub")
            return real_open(path, **kwargs)

        with (
            route_scope("demo", work),
            patch.object(safe_copy, "open_regular", swapping_open),
            self.assertRaises(ProviderError) as caught,
        ):
            api.local_file(root / "sub" / "praia.mp4")
        self.assertIn("praia.mp4 está fora de permissions.paths", str(caught.exception))
        self.assertEqual([], list(work.iterdir()))


if __name__ == "__main__":
    unittest.main()
