"""Arquivos locais e pastas: raízes de `permissions.paths` (Minor 1), `api.local_file`
endurecido (Minor 2 / ledger T3) e `folder_digest` limitado (RT-11)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase
from test_sdk_manifest import BASE, write_plugin

from getbrolls import http
from getbrolls.http import ProviderError
from getbrolls.sdk import api as sdk_api
from getbrolls.sdk import loader
from getbrolls.sdk.api import PluginApi, route_scope
from getbrolls.sdk.manifest import ManifestError, read_manifest
from getbrolls.sdk.registry import Registry, get_registry

POSIX = os.name != "nt"


class PathRootsTests(unittest.TestCase):
    """Minor 1: raiz ampla demais (sistema, unidade, home) não vale como `permissions.paths`."""

    def read(self, paths):
        with tempfile.TemporaryDirectory() as tmp:
            return read_manifest(write_plugin(tmp, {**BASE, "permissions": {"paths": paths}}))

    def test_filesystem_drive_and_home_roots_are_refused(self):
        home = str(Path.home())
        for bad in ("/", "\\", "//", "~", "~/", "~\\", "~/.", "C:\\", "C:/", "c:", "D:\\\\", home, home + "/"):
            with self.subTest(bad=bad), self.assertRaises(ManifestError) as caught:
                self.read([bad])
            self.assertIn("permissions.paths", str(caught.exception))

    def test_specific_folders_are_accepted_on_any_platform(self):
        roots = ["~/Movies", "~\\Videos\\brolls", "/Volumes/NAS/brolls", "C:\\Users\\ana\\Videos", "D:/acervo"]
        self.assertEqual(roots, self.read(roots)["permissions"]["paths"])

    def test_a_root_of_another_platform_is_ignored_at_runtime(self):
        manifest = {**MANIFEST, "permissions": {**MANIFEST["permissions"], "paths": ["C:\\acervo", "/srv/acervo"]}}
        roots = PluginApi(manifest, Registry())._roots()
        expected = Path("/srv/acervo") if POSIX else Path("C:\\acervo")
        self.assertEqual([expected.resolve()], roots)


class LocalFileHardeningTests(LoaderTestCase):
    """Minor 2 / T3: abre sem seguir link, confere o fd, copia com teto e cria o destino exclusivo."""

    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp(prefix="gb-acervo-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.outside = Path(tempfile.mkdtemp(prefix="gb-fora-"))
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        self.work = Path(tempfile.mkdtemp(prefix="gb-work-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        (self.root / "praia.mp4").write_bytes(b"video da praia")
        (self.outside / "segredo-externo.mp4").write_bytes(b"fora da raiz")

    def api(self):
        manifest = {**MANIFEST, "permissions": {**MANIFEST["permissions"], "paths": [str(self.root)]}}
        return PluginApi(read_manifest(self.install(manifest)), Registry())

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_refusal_names_the_callers_file_never_the_resolved_target(self):
        link = self.root / "atalho.mp4"
        link.symlink_to(self.outside / "segredo-externo.mp4")
        with route_scope("demo", self.work), self.assertRaises(ProviderError) as caught:
            self.api().local_file(link)
        self.assertIn("atalho.mp4", str(caught.exception))
        self.assertNotIn("segredo-externo", str(caught.exception))

    def test_nul_byte_and_root_errors_become_provider_errors(self):
        api = self.api()
        with route_scope("demo", self.work), self.assertRaises(ProviderError):
            api.local_file(str(self.root) + "/a\0b.mp4")
        with (
            route_scope("demo", self.work),
            patch.object(PluginApi, "_roots", side_effect=RuntimeError("sem home")),
            self.assertRaises(ProviderError),
        ):
            api.local_file(self.root / "praia.mp4")

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_dangling_symlink_at_the_destination_is_never_followed(self):
        planted = self.outside / "plantado.mp4"
        (self.work / "local.mp4").symlink_to(planted)
        with route_scope("demo", self.work), self.assertRaises(ProviderError):
            self.api().local_file(self.root / "praia.mp4")
        self.assertFalse(planted.exists())

    @unittest.skipUnless(POSIX and hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW é POSIX")
    def test_a_link_swapped_in_after_resolve_is_not_followed(self):
        link = self.root / "trocado.mp4"
        link.symlink_to(self.outside / "segredo-externo.mp4")
        with (
            route_scope("demo", self.work),
            patch.object(sdk_api.Path, "resolve", lambda self, strict=False: self.absolute()),
            self.assertRaises(ProviderError),
        ):
            self.api().local_file(link)
        self.assertEqual([], list(self.work.iterdir()))

    def test_copy_is_bounded_by_the_bytes_actually_read(self):
        real_fstat = os.fstat

        def small_fstat(fd):
            st = real_fstat(fd)
            return SimpleNamespace(st_mode=st.st_mode, st_size=1)

        with (
            route_scope("demo", self.work),
            patch.object(http, "DOWNLOAD_MAX_BYTES", 4),
            patch.object(sdk_api.os, "fstat", small_fstat),
            self.assertRaises(ProviderError) as caught,
        ):
            self.api().local_file(self.root / "praia.mp4")
        self.assertIn("teto", str(caught.exception))
        self.assertEqual([], list(self.work.iterdir()))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO é POSIX")
    def test_fifo_is_refused_without_blocking(self):
        fifo = self.root / "fila.mp4"
        os.mkfifo(fifo)
        with route_scope("demo", self.work), self.assertRaises(ProviderError) as caught:
            self.api().local_file(fifo)
        self.assertIn("fila.mp4", str(caught.exception))


class DigestLimitTests(LoaderTestCase):
    """RT-11: hash em pedaços, com teto por arquivo e total; estourar suspende, não derruba."""

    def test_digest_reads_in_chunks_not_whole_files(self):
        folder = self.install()
        expected = loader.folder_digest(folder)
        with patch.object(Path, "read_bytes", side_effect=MemoryError()):
            self.assertEqual(expected, loader.folder_digest(folder))

    def test_oversized_plugin_is_suspended_with_a_reason_and_builtins_load(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "cache.bin").write_bytes(b"x" * 64)
        with patch.object(loader, "DIGEST_MAX_FILE_BYTES", 16):
            rows = {row["id"]: row for row in loader.inventory()}
            self.assertEqual("suspended", rows["demo"]["status"])
            self.assertIn("teto", rows["demo"]["reason"])
            reg = get_registry()
        self.assertIn("youtube", reg.provider_names())
        self.assertNotIn("demo", reg.provider_names())

    def test_total_cap_also_applies(self):
        folder = self.install(code=PLUGIN_CODE)
        loader.enable("demo", confirm=True)
        for n in range(4):
            (folder / f"parte{n}.txt").write_bytes(b"y" * 40)
        with patch.object(loader, "DIGEST_MAX_TOTAL_BYTES", 100):
            rows = {row["id"]: row for row in loader.inventory()}
        self.assertEqual("suspended", rows["demo"]["status"])
        with patch.object(loader, "DIGEST_MAX_TOTAL_BYTES", 100), self.assertRaises(ValueError):
            loader.enable("demo", confirm=True)


if __name__ == "__main__":
    unittest.main()
