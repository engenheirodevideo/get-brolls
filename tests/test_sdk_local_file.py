"""`api.local_file`: só dentro de `permissions.paths`, sempre cópia para o workdir."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import http
from getbrolls.http import ProviderError
from getbrolls.sdk.api import PluginApi, route_scope
from getbrolls.sdk.manifest import read_manifest
from getbrolls.sdk.registry import Registry

CAN_SYMLINK = os.name != "nt"


class LocalFileTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp(prefix="gb-acervo-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.outside = Path(tempfile.mkdtemp(prefix="gb-fora-"))
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        self.work = Path(tempfile.mkdtemp(prefix="gb-work-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        (self.root / "praia.MP4").write_bytes(b"video da praia")
        (self.outside / "segredo.mp4").write_bytes(b"fora da raiz")

    def api(self, paths=None):
        manifest = {
            **MANIFEST,
            "permissions": {**MANIFEST["permissions"], "paths": [str(self.root)] if paths is None else paths},
        }
        return PluginApi(read_manifest(self.install(manifest)), Registry())

    def test_file_inside_a_root_is_copied_not_linked(self):
        api = self.api()
        source = self.root / "praia.MP4"
        with route_scope("demo", self.work):
            target = api.local_file(source)
        self.assertEqual(self.work.resolve() / "local.mp4", target)
        self.assertEqual(b"video da praia", target.read_bytes())
        self.assertNotEqual(source.stat().st_ino, target.stat().st_ino)
        self.assertEqual(b"video da praia", source.read_bytes())

    def test_file_outside_every_root_is_refused_and_logged(self):
        api = self.api()
        with (
            route_scope("demo", self.work),
            self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
            self.assertRaises(ProviderError) as caught,
        ):
            api.local_file(self.outside / "segredo.mp4")
        self.assertIn("fora de permissions.paths", str(caught.exception))
        self.assertIn("event=plugin_path_refused", "\n".join(cm.output))
        self.assertNotIn(str(self.outside), "\n".join(cm.output))
        self.assertEqual([], list(self.work.iterdir()))

    @unittest.skipUnless(CAN_SYMLINK, "symlink exige privilégio no Windows")
    def test_symlink_inside_the_root_pointing_outside_is_refused(self):
        link = self.root / "atalho.mp4"
        link.symlink_to(self.outside / "segredo.mp4")
        with route_scope("demo", self.work), self.assertRaises(ProviderError):
            self.api().local_file(link)

    def test_parent_segments_cannot_climb_out_of_the_root(self):
        sneaky = self.root / ".." / self.outside.name / "segredo.mp4"
        with route_scope("demo", self.work), self.assertRaises(ProviderError):
            self.api().local_file(sneaky)

    def test_needs_declared_paths_and_an_active_route(self):
        with route_scope("demo", self.work), self.assertRaises(ProviderError) as caught:
            self.api(paths=[]).local_file(self.root / "praia.MP4")
        self.assertIn("permissions.paths", str(caught.exception))
        with self.assertRaises(ProviderError) as caught:
            self.api().local_file(self.root / "praia.MP4")
        self.assertIn("Route.prepare", str(caught.exception))

    def test_missing_file_folder_and_size_cap(self):
        api = self.api()
        with route_scope("demo", self.work):
            with self.assertRaises(ProviderError):
                api.local_file(self.root / "nao-existe.mp4")
            with self.assertRaises(ProviderError):
                api.local_file(self.root)
            with patch.object(http, "DOWNLOAD_MAX_BYTES", 4), self.assertRaises(ProviderError) as caught:
                api.local_file(self.root / "praia.MP4")
        self.assertIn("teto", str(caught.exception))

    def test_tilde_root_is_expanded(self):
        with patch.dict(os.environ, {"HOME": str(self.root.parent), "USERPROFILE": str(self.root.parent)}):
            api = self.api(paths=["~/" + self.root.name])
            with route_scope("demo", self.work):
                self.assertTrue(api.local_file(self.root / "praia.MP4").is_file())


if __name__ == "__main__":
    unittest.main()
