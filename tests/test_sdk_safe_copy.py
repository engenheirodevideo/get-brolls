"""Abrir e copiar arquivo da pessoa pelo descritor, sem seguir link (`sdk.safe_copy`)."""

import contextlib
import logging
import os
import shutil
import tempfile
import threading
import time
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
BY_COMPONENT = safe_copy._BY_COMPONENT
FD_DIR = Path("/dev/fd")


@contextlib.contextmanager
def swap_before_open(swap):
    """Roda `swap` uma vez, logo antes da primeira abertura de `open_under`: a da raiz,
    na descida por `dir_fd` (POSIX), ou a de `open_regular` onde ela não existe."""
    done = []

    def once():
        if not done:
            done.append(True)
            swap()

    if BY_COMPONENT:
        real_open_at = safe_copy._open_at

        def opening_at(name, flags, dir_fd=None):
            once()
            return real_open_at(name, flags, dir_fd)

        with patch.object(safe_copy, "_open_at", opening_at):
            yield
    else:
        real_open_regular = safe_copy.open_regular

        def opening_regular(path, **kwargs):
            once()
            return real_open_regular(path, **kwargs)

        with patch.object(safe_copy, "open_regular", opening_regular):
            yield


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


class DescentTestCase(SafeCopyTestCase):
    """Raiz com `sub/eco.wav` dentro e uma `sub/eco.wav` de fora, para trocar a pasta do meio."""

    def setUp(self):
        super().setUp()
        self.base = self.base.resolve()
        self.root = self.base / "acervo"
        (self.root / "sub").mkdir(parents=True)
        self.inner = self.root / "sub" / "eco.wav"
        self.inner.write_bytes(b"eco")
        self.outside = self.base / "fora"
        (self.outside / "sub").mkdir(parents=True)
        (self.outside / "sub" / "eco.wav").write_bytes(b"de fora")

    def swap_to_link(self):
        """A pasta `sub` da raiz vira um link para a `sub` de fora."""
        (self.root / "sub").rename(self.base / "sub-original")
        (self.root / "sub").symlink_to(self.outside / "sub")

    def swap_back(self):
        (self.root / "sub").unlink()
        (self.base / "sub-original").rename(self.root / "sub")

    def swap_root_to_link(self):
        """A própria raiz vira um link para a pasta de fora (que também tem `sub/eco.wav`)."""
        self.root.rename(self.base / "acervo-original")
        self.root.symlink_to(self.outside)


class DescentTests(DescentTestCase):
    """`open_under` no POSIX: desce da raiz uma pasta por vez, sem seguir link."""

    @unittest.skipUnless(BY_COMPONENT, "a descida por dir_fd é do POSIX")
    def test_a_root_swapped_for_a_link_before_the_call_is_refused(self):
        self.swap_root_to_link()
        self.refused(safe_copy.OUTSIDE, safe_copy.open_under, self.inner, [self.root])

    @unittest.skipUnless(BY_COMPONENT, "a descida por dir_fd é do POSIX")
    def test_a_root_swapped_for_a_link_right_before_it_is_opened_is_refused(self):
        real_open = safe_copy._open_at

        def swapping_open(name, flags, dir_fd=None):
            if dir_fd is None:
                self.swap_root_to_link()  # depois da escolha da raiz, antes de abri-la
            return real_open(name, flags, dir_fd)

        with patch.object(safe_copy, "_open_at", swapping_open):
            self.refused(safe_copy.OUTSIDE, safe_copy.open_under, self.inner, [self.root])

    @unittest.skipUnless(BY_COMPONENT, "a descida por dir_fd é do POSIX")
    def test_triple_swap_between_component_opens_is_refused(self):
        real_open = safe_copy._open_at
        opened = []

        def swapping_open(name, flags, dir_fd=None):
            opened.append(str(name))
            if name == "sub":
                self.swap_to_link()  # 1: a pasta do meio vira link logo antes de abrir
                try:
                    return real_open(name, flags, dir_fd)
                finally:
                    self.swap_back()  # 2: volta a ser pasta antes de qualquer conferência
                    self.swap_to_link()  # 3: e vira link de novo antes do arquivo
            return real_open(name, flags, dir_fd)

        with patch.object(safe_copy, "_open_at", swapping_open):
            self.refused(safe_copy.OUTSIDE, safe_copy.open_under, self.inner, [self.root])
        self.assertIn("sub", opened)
        self.assertNotIn("eco.wav", opened)

    @unittest.skipUnless(BY_COMPONENT, "a descida por dir_fd é do POSIX")
    def test_a_folder_swapped_after_it_was_opened_never_leads_outside(self):
        real_open = safe_copy._open_at

        def swapping_open(name, flags, dir_fd=None):
            if name == "eco.wav":
                self.swap_to_link()  # a pasta aberta já está presa pelo descritor
            return real_open(name, flags, dir_fd)

        with patch.object(safe_copy, "_open_at", swapping_open):
            fd, _ = safe_copy.open_under(self.inner, [self.root])
        self.addCleanup(os.close, fd)
        self.assertEqual(b"eco", os.read(fd, 100))

    @unittest.skipUnless(BY_COMPONENT, "a descida por dir_fd é do POSIX")
    def test_a_root_replaced_before_it_is_opened_is_refused(self):
        real_open_root = safe_copy.open_root

        with patch.object(safe_copy, "open_root", lambda root: real_open_root(self.outside)):
            self.refused(safe_copy.CHANGED, safe_copy.open_under, self.inner, [self.root])

    def test_the_legacy_check_still_refuses_a_single_swap(self):
        real_open = safe_copy.open_regular

        def swapping_open(path, **kwargs):
            self.swap_to_link()
            return real_open(path, **kwargs)

        if not POSIX:
            self.skipTest("symlink exige privilégio no Windows")
        with (
            patch.object(safe_copy, "_BY_COMPONENT", False),
            patch.object(safe_copy, "open_regular", swapping_open),
        ):
            self.refused(safe_copy.OUTSIDE, safe_copy.open_under, self.inner, [self.root])


@unittest.skipUnless(BY_COMPONENT and FD_DIR.is_dir(), "conta descritores por /dev/fd, na descida do POSIX")
class DescriptorLeakTests(DescentTestCase):
    """Nenhum ramo de `open_under` deixa descritor aberto (fora o que ele devolve)."""

    def open_fds(self):
        return len(list(FD_DIR.iterdir()))

    def assert_no_leak(self, run):
        before = self.open_fds()
        try:
            fd, _ = run()
        except safe_copy.UnsafeFileError:
            pass
        else:
            os.close(fd)
        self.assertEqual(before, self.open_fds())

    def test_every_refusal_and_the_success_close_what_they_opened(self):
        (self.root / "sub" / "pasta.wav").mkdir()
        (self.root / "sub" / "atalho.wav").symlink_to(self.inner)
        os.link(self.root / "sub" / "eco.wav", self.root / "outro-nome.wav")
        cases = {
            "success": (self.inner, {"single_link": False}),
            "linked": (self.inner, {}),
            "outside": (self.outside / "sub" / "eco.wav", {}),
            "missing_file": (self.root / "sub" / "nada.wav", {}),
            "missing_folder": (self.root / "nada" / "eco.wav", {}),
            "final_link": (self.root / "sub" / "atalho.wav", {}),
            "not_regular": (self.root / "sub" / "pasta.wav", {}),
        }
        for label, (path, kwargs) in cases.items():
            with self.subTest(label):
                self.assert_no_leak(lambda path=path, kwargs=kwargs: safe_copy.open_under(path, [self.root], **kwargs))

    @staticmethod
    def failing_at(call, calls):
        """`_open_at` que recusa a `call`-ésima abertura (da âncora até o arquivo), anotando cada uma em `calls`."""
        real_open = safe_copy._open_at

        def flaky_open(name, flags, dir_fd=None):
            calls.append(name)
            if len(calls) == call:
                raise PermissionError("negado")
            return real_open(name, flags, dir_fd)

        return flaky_open

    def test_a_failure_at_any_open_closes_the_folders_already_open(self):
        opened = []
        with patch.object(safe_copy, "_open_at", self.failing_at(0, opened)):
            fd, _ = safe_copy.open_under(self.inner, [self.root])
        os.close(fd)
        self.assertEqual(["eco.wav", "sub"], opened[:-3:-1])
        for call in range(1, len(opened) + 1):
            with self.subTest(call=call), patch.object(safe_copy, "_open_at", self.failing_at(call, [])):
                self.assert_no_leak(lambda: safe_copy.open_under(self.inner, [self.root]))

    def test_swaps_and_a_replaced_root_close_what_they_opened(self):
        real_open = safe_copy._open_at

        def swapping_open(name, flags, dir_fd=None):
            if name == "sub":
                self.swap_to_link()
            return real_open(name, flags, dir_fd)

        real_open_root = safe_copy.open_root

        with patch.object(safe_copy, "_open_at", swapping_open):
            self.assert_no_leak(lambda: safe_copy.open_under(self.inner, [self.root]))
        self.swap_back()
        with patch.object(safe_copy, "open_root", lambda root: real_open_root(self.outside)):
            self.assert_no_leak(lambda: safe_copy.open_under(self.inner, [self.root]))

    def test_a_root_swapped_for_a_link_closes_what_it_opened(self):
        real_open = safe_copy._open_at

        def swapping_open(name, flags, dir_fd=None):
            if dir_fd is None:
                self.swap_root_to_link()
            return real_open(name, flags, dir_fd)

        with patch.object(safe_copy, "_open_at", swapping_open):
            self.assert_no_leak(lambda: safe_copy.open_under(self.inner, [self.root]))
        self.assert_no_leak(lambda: safe_copy.open_under(self.inner, [self.root]))

    def test_a_check_that_raises_after_the_open_closes_the_file(self):
        with patch.object(safe_copy, "_check_fd", side_effect=RuntimeError("falhou")):
            before = self.open_fds()
            with self.assertRaises(RuntimeError):
                safe_copy.open_under(self.inner, [self.root])
            self.assertEqual(before, self.open_fds())


class LocalFileSwapTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.base = Path(tempfile.mkdtemp(prefix="gb-swap-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, self.outside, self.work = self.base / "acervo", self.base / "fora", self.base / "work"
        for folder in (self.root / "sub", self.outside / "sub", self.work):
            folder.mkdir(parents=True)
        (self.root / "sub" / "praia.mp4").write_bytes(b"de dentro")
        (self.outside / "sub" / "praia.mp4").write_bytes(b"de fora")
        manifest = {**MANIFEST, "permissions": {**MANIFEST["permissions"], "paths": [str(self.root)]}}
        self.api = PluginApi(read_manifest(self.install(manifest)), Registry())

    def swap_to_link(self):
        (self.root / "sub").rename(self.base / "sub-original")
        (self.root / "sub").symlink_to(self.outside / "sub")

    def swap_back(self):
        (self.root / "sub").unlink()
        (self.base / "sub-original").rename(self.root / "sub")

    def assert_refused_outside(self):
        with route_scope("demo", self.work), self.assertRaises(ProviderError) as caught:
            self.api.local_file(self.root / "sub" / "praia.mp4")
        self.assertIn("praia.mp4 está fora de permissions.paths", str(caught.exception))
        self.assertEqual([], list(self.work.iterdir()))

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_folder_swapped_before_the_open_is_refused(self):
        with swap_before_open(self.swap_to_link):
            self.assert_refused_outside()

    @unittest.skipUnless(BY_COMPONENT, "a descida por dir_fd é do POSIX")
    def test_triple_swap_during_the_open_is_refused(self):
        real_open = safe_copy._open_at

        def swapping_open(name, flags, dir_fd=None):
            if name != "sub":
                return real_open(name, flags, dir_fd)
            self.swap_to_link()  # 1: a pasta do meio vira link logo antes de abrir
            try:
                return real_open(name, flags, dir_fd)
            finally:
                self.swap_back()  # 2: volta a ser pasta para qualquer conferência depois
                self.swap_to_link()  # 3: e vira link de novo antes do arquivo

        with patch.object(safe_copy, "_open_at", swapping_open):
            self.assert_refused_outside()


class AncestorTestCase(SafeCopyTestCase):
    """`base/anc/root/sub/f.wav` dentro; `base/evil/root/sub/f.wav` de fora, com o mesmo
    formato, para trocar uma pasta ACIMA da raiz (ou a própria raiz) por um link."""

    def setUp(self):
        super().setUp()
        self.base = self.base.resolve()
        self.anc, self.evil = self.base / "anc", self.base / "evil"
        self.root = self.anc / "root"
        for top, body in ((self.anc, b"INSIDE"), (self.evil, b"OUTSIDE-SECRET")):
            (top / "root" / "sub").mkdir(parents=True)
            (top / "root" / "sub" / "f.wav").write_bytes(body)
        self.target = self.root / "sub" / "f.wav"

    def ancestor_to_link(self):
        self.anc.rename(self.base / "anc-real")
        self.anc.symlink_to(self.evil)

    def ancestor_back(self):
        self.anc.unlink()
        (self.base / "anc-real").rename(self.anc)

    def read_under(self, path, roots):
        """Bytes do arquivo aberto por `open_under`, ou o motivo da recusa."""
        try:
            fd, _ = safe_copy.open_under(path, roots)
        except UnsafeFileError as exc:
            return exc.reason
        try:
            return os.read(fd, 100)
        finally:
            os.close(fd)


@unittest.skipUnless(BY_COMPONENT, "a descida da âncora por dir_fd é do POSIX")
class AncestorTests(AncestorTestCase):
    """A raiz já resolvida é aberta descendo de `/`: nenhuma pasta acima dela vira link."""

    def test_open_root_walks_from_the_anchor_to_the_real_folder(self):
        fd = safe_copy.open_root(self.root)
        self.addCleanup(os.close, fd)
        self.assertEqual(self.root.stat().st_ino, os.fstat(fd).st_ino)

    def test_a_link_missing_folder_or_file_on_the_way_is_outside(self):
        self.refused(safe_copy.OUTSIDE, safe_copy.open_root, self.base / "nada" / "root")
        self.refused(safe_copy.OUTSIDE, safe_copy.open_root, self.target)
        self.refused(safe_copy.OUTSIDE, safe_copy.open_root, Path("relativo"))
        self.ancestor_to_link()
        self.refused(safe_copy.OUTSIDE, safe_copy.open_root, self.root)

    def test_an_ancestor_swapped_for_a_link_never_opens_an_outside_file(self):
        self.assertEqual(b"INSIDE", self.read_under(self.target, [self.root]))
        self.ancestor_to_link()
        self.assertEqual(safe_copy.OUTSIDE, self.read_under(self.target, [self.root]))

    def test_an_ancestor_that_keeps_flipping_never_opens_an_outside_file(self):
        stop = threading.Event()

        def flip():
            while not stop.is_set():
                self.ancestor_to_link()
                self.ancestor_back()

        flipper = threading.Thread(target=flip)
        flipper.start()
        seen = {}
        deadline = time.monotonic() + 2
        try:
            while time.monotonic() < deadline:
                got = self.read_under(self.target, [self.root])
                seen[got] = seen.get(got, 0) + 1
        finally:
            stop.set()
            flipper.join()
        self.assertEqual(0, seen.get(b"OUTSIDE-SECRET", 0), seen)
        self.assertGreater(sum(seen.values()), 0)

    @unittest.skipUnless(FD_DIR.is_dir(), "conta descritores por /dev/fd")
    def test_every_root_walk_refusal_closes_what_it_opened(self):
        before = len(list(FD_DIR.iterdir()))
        for root in (self.base / "nada" / "root", self.target):
            with self.subTest(root=root.name), self.assertRaises(UnsafeFileError):
                safe_copy.open_root(root)
        self.ancestor_to_link()
        self.assertEqual(safe_copy.OUTSIDE, self.read_under(self.target, [self.root]))
        self.assertEqual(before, len(list(FD_DIR.iterdir())))


class LocalFileRootTests(LoaderTestCase):
    """`api.local_file` resolve as raízes uma vez só por instância."""

    def setUp(self):
        super().setUp()
        self.base = Path(tempfile.mkdtemp(prefix="gb-roots-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.anc, self.evil = self.base / "anc", self.base / "evil"
        for top, body in ((self.anc, b"INSIDE"), (self.evil, b"OUTSIDE-SECRET")):
            (top / "root" / "sub").mkdir(parents=True)
            (top / "root" / "sub" / "f.wav").write_bytes(body)
        self.root = self.anc / "root"
        manifest = {**MANIFEST, "permissions": {**MANIFEST["permissions"], "paths": [str(self.root)]}}
        self.api = PluginApi(read_manifest(self.install(manifest)), Registry())

    def copy(self):
        work = Path(tempfile.mkdtemp(prefix="gb-work-", dir=self.base))
        with route_scope("demo", work):
            return self.api.local_file(self.root / "sub" / "f.wav").read_bytes()

    def assert_refused(self):
        with self.assertRaises(ProviderError) as caught:
            self.copy()
        self.assertIn("f.wav está fora de permissions.paths", str(caught.exception))

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_a_root_swapped_for_a_link_between_calls_is_refused(self):
        self.assertEqual(b"INSIDE", self.copy())
        self.root.rename(self.anc / "root-orig")
        self.root.symlink_to(self.evil / "root")
        self.assert_refused()

    @unittest.skipUnless(BY_COMPONENT, "a descida da âncora por dir_fd é do POSIX")
    def test_an_ancestor_swapped_for_a_link_between_calls_is_refused(self):
        self.assertEqual(b"INSIDE", self.copy())
        self.anc.rename(self.base / "anc-real")
        self.anc.symlink_to(self.evil)
        self.assert_refused()

    def test_a_hardlink_is_copied_and_logged_without_the_path(self):
        os.link(self.root / "sub" / "f.wav", self.base / "outro-nome.wav")
        with self.assertLogs("getbrolls.sdk", level="INFO") as cm:
            self.assertEqual(b"INSIDE", self.copy())
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_path_hardlink", joined)
        self.assertIn("plugin=demo", joined)
        self.assertNotIn(str(self.base), joined)

    def test_a_single_name_file_is_not_logged_as_hardlink(self):
        with self.assertLogs("getbrolls.sdk", level="INFO") as cm:
            self.assertEqual(b"INSIDE", self.copy())
            logging.getLogger("getbrolls.sdk").info("fim")
        self.assertNotIn("plugin_path_hardlink", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
