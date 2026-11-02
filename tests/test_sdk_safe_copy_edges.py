"""`sdk.safe_copy`: falhas de leitura do disco e o caminho sem `dir_fd` (Windows), por simulação."""

import errno
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _posix import without_nofollow
from _symlink import needs_symlink
from test_sdk_safe_copy import BY_COMPONENT, POSIX, DescentTestCase, SafeCopyTestCase

from getbrolls.sdk import safe_copy


class WithoutNofollowTests(SafeCopyTestCase):
    """O Windows não tem `O_NOFOLLOW`: o link é recusado antes de qualquer abertura."""

    @needs_symlink
    def test_a_link_is_refused_before_anything_is_opened(self):
        link = self.base / "atalho.wav"
        link.symlink_to(self.source)
        opened = []
        real_open = os.open
        with (
            without_nofollow(),
            patch.object(safe_copy.os, "open", lambda *a, **k: (opened.append(a), real_open(*a, **k))[1]),
        ):
            error = self.refused(safe_copy.OPEN_FAILED, safe_copy.open_regular, link)
        self.assertEqual("OSError", error.type_name)
        self.assertEqual([], opened)


class WithinRootsFailureTests(SafeCopyTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.base.resolve()
        self.root = self.base / "acervo"
        (self.root / "sub").mkdir(parents=True)
        self.inner = self.root / "sub" / "eco.wav"
        self.inner.write_bytes(b"eco")

    def stat_failing_for(self, *broken):
        real_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path in broken:
                raise PermissionError(errno.EACCES, "negado")
            return real_stat(path, *args, **kwargs)

        return patch.object(Path, "stat", stat)

    def test_an_ancestor_that_cannot_be_read_is_skipped(self):
        with self.stat_failing_for(self.base):
            self.assertTrue(safe_copy.within_roots(self.inner, [self.root]))

    def test_the_root_among_the_unreadable_ancestors_matches_nothing(self):
        with self.stat_failing_for(self.root):
            self.assertFalse(safe_copy.within_roots(self.inner, [self.root]))


class StillUnderTests(SafeCopyTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.base.resolve()
        self.root = self.base / "acervo"
        self.root.mkdir()
        self.inner = self.root / "eco.wav"
        self.inner.write_bytes(b"eco")
        (self.root / "outro.wav").write_bytes(b"outro")

    def test_the_same_file_inside_the_root_returns_its_resolved_path(self):
        fd = os.open(self.inner, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.assertEqual(self.inner, safe_copy.still_under(self.inner, [self.root], fd))

    def test_a_path_that_no_longer_resolves_is_changed(self):
        # O descritor segura outro arquivo: o Windows não apaga um arquivo aberto, e a conferência do caminho vem
        # antes de olhar o descritor.
        fd = os.open(self.root / "outro.wav", os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.inner.unlink()
        self.refused(safe_copy.CHANGED, safe_copy.still_under, self.inner, [self.root], fd)

    def test_a_path_that_points_to_another_file_than_the_descriptor_is_changed(self):
        fd = os.open(self.root / "outro.wav", os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.refused(safe_copy.CHANGED, safe_copy.still_under, self.inner, [self.root], fd)

    def test_a_descriptor_that_cannot_be_read_is_changed(self):
        fd = os.open(self.inner, os.O_RDONLY)
        os.close(fd)
        self.refused(safe_copy.CHANGED, safe_copy.still_under, self.inner, [self.root], fd)

    def test_a_path_resolved_differently_the_second_time_is_changed(self):
        fd = os.open(self.inner, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        real_resolve = Path.resolve
        answers = iter([self.inner, self.root / "outro.wav"])

        def resolve(path, strict=False):
            return next(answers, None) or real_resolve(path, strict=strict)

        with patch.object(Path, "resolve", resolve):
            self.refused(safe_copy.CHANGED, safe_copy.still_under, self.inner, [self.root], fd)


class WithoutDirFdTests(SafeCopyTestCase):
    """`open_under` onde `os.open` não aceita `dir_fd`: `open_regular` + `still_under`."""

    def setUp(self):
        super().setUp()
        self.base = self.base.resolve()
        self.root = self.base / "acervo"
        self.root.mkdir()
        self.inner = self.root / "eco.wav"
        self.inner.write_bytes(b"eco")

    def test_a_file_inside_the_root_opens_and_a_file_outside_is_refused(self):
        with patch.object(safe_copy, "_BY_COMPONENT", False):
            fd, info = safe_copy.open_under(self.inner, [self.root])
            self.addCleanup(os.close, fd)
            self.assertEqual(self.inner.stat().st_ino, info.st_ino)
            self.assertEqual(b"eco", os.read(fd, 10))
            self.refused(safe_copy.OUTSIDE, safe_copy.open_under, self.source, [self.root])


class RecheckByDescriptorTests(SafeCopyTestCase):
    def test_a_changed_file_is_refused_and_the_callers_descriptor_stays_open(self):
        fd = os.open(self.source, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        info = os.fstat(fd)
        self.refused(safe_copy.CHANGED, safe_copy.recheck, fd, info.st_dev, info.st_ino + 1, info.st_size)
        self.assertEqual(info.st_ino, os.fstat(fd).st_ino)  # `recheck` não fecha o descritor de quem chamou


class CopyTargetFailureTests(SafeCopyTestCase):
    def test_a_target_that_cannot_be_created_is_reported_with_the_error_type(self):
        fd = os.open(self.source, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        target = self.base / "nao-existe" / "copia.wav"
        error = self.refused(safe_copy.TARGET_FAILED, safe_copy.copy_from_fd, fd, target, 1000)
        self.assertEqual("FileNotFoundError", error.type_name)
        self.assertFalse(target.parent.exists())


@unittest.skipUnless(BY_COMPONENT, "a descida por dir_fd é do POSIX")
class DescentFailureTests(DescentTestCase):
    def test_a_folder_that_vanishes_before_it_is_opened_is_an_open_failure(self):
        real_open_at = safe_copy._open_at  # pylint: disable=protected-access
        vanished = []

        def opening_at(name, flags, dir_fd=None):
            if name == "sub" and not vanished:
                vanished.append(True)
                shutil.rmtree(self.root / "sub")
            return real_open_at(name, flags, dir_fd)

        with patch.object(safe_copy, "_open_at", opening_at):
            error = self.refused(safe_copy.OPEN_FAILED, safe_copy.open_under, self.inner, [self.root])
        self.assertEqual("FileNotFoundError", error.type_name)

    @needs_symlink
    @unittest.skipUnless(BY_COMPONENT, "open_root desce por dir_fd, que é do POSIX")
    def test_a_link_in_the_root_path_that_fails_to_open_is_outside(self):
        (self.base / "atalho").symlink_to(self.root)
        real_open_at = safe_copy._open_at  # pylint: disable=protected-access

        def opening_at(name, flags, dir_fd=None):
            if name == "atalho":
                raise OSError(errno.ELOOP, "laço")
            return real_open_at(name, flags, dir_fd)

        with patch.object(safe_copy, "_open_at", opening_at):
            self.refused(safe_copy.OUTSIDE, safe_copy.open_root, self.base / "atalho")

    @unittest.skipUnless(POSIX and os.geteuid() != 0, "permissão de pasta não vale para o root")
    def test_an_unreadable_ancestor_is_an_open_failure_with_the_error_type(self):
        closed = self.base / "fechada"
        (closed / "dentro").mkdir(parents=True)
        closed.chmod(0)
        self.addCleanup(closed.chmod, 0o700)
        error = self.refused(safe_copy.OPEN_FAILED, safe_copy.open_root, closed / "dentro")
        self.assertEqual("PermissionError", error.type_name)

    def test_a_folder_that_fails_to_open_and_cannot_even_be_stat_is_an_open_failure(self):
        real_open_at, real_stat = safe_copy._open_at, os.stat  # pylint: disable=protected-access

        def opening_at(name, flags, dir_fd=None):
            if name == "sub":
                raise PermissionError(errno.EACCES, "negado")
            return real_open_at(name, flags, dir_fd)

        def stat(path, *args, **kwargs):
            if kwargs.get("dir_fd") is not None and path == "sub":
                raise PermissionError(errno.EACCES, "negado")
            return real_stat(path, *args, **kwargs)

        with patch.object(safe_copy, "_open_at", opening_at), patch.object(safe_copy.os, "stat", stat):
            error = self.refused(safe_copy.OPEN_FAILED, safe_copy.open_root, self.root / "sub")
        self.assertEqual("PermissionError", error.type_name)


if __name__ == "__main__":
    unittest.main()
