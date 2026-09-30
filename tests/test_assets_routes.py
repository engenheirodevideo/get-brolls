"""Rotas de componentes: diretiva → arquivo, projeto antes da biblioteca pessoal."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _isolation import GB_HOME
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import assets, layout


class AssetRouteTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-assets-"))
        self.personal = GB_HOME / "assets"
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.personal, ignore_errors=True)

    def _put(self, root, folder, name, text="x"):
        path = root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_project_wins_over_personal(self):
        self._put(self.project, "assets/sfx", "Whoosh.wav")
        self._put(self.personal, "sfx", "whoosh.mp3")
        found = assets.resolve(self.project, "sfx", "whoosh")
        self.assertEqual(found["status"], "found")
        self.assertEqual(found["origin"], "project")
        self.assertTrue(found["path"].endswith("Whoosh.wav"))

    def test_personal_library_and_accent_fold(self):
        self._put(self.personal, "musica", "Épica.mp3")
        found = assets.resolve(self.project, "musica", "epica")
        self.assertEqual(found["origin"], "personal")

    def test_pending_and_license_warning(self):
        pending = assets.resolve(self.project, "sfx", "nada")
        self.assertEqual((pending["status"], pending["path"]), ("pending", None))
        self._put(self.project, "assets/sfx", "whoosh.wav")
        found = assets.resolve(self.project, "sfx", "whoosh")
        self.assertIn("licença não registrada", " ".join(found["warnings"]))
        self._put(
            self.project,
            "assets/sfx",
            "whoosh.licenca.json",
            json.dumps({"origem": "banco X", "licenca": "CC0", "credito": "Fulano"}),
        )
        found = assets.resolve(self.project, "sfx", "whoosh")
        self.assertEqual(found["license"]["licenca"], "CC0")
        self.assertEqual(found["warnings"], [])

    def test_license_sidecar_is_never_a_component(self):
        self._put(self.project, "assets/composicoes", "abertura.licenca.json", "{}")
        self.assertEqual(assets.resolve(self.project, "composicao", "abertura")["status"], "pending")

    def test_wrong_extension_ignored(self):
        self._put(self.project, "assets/sfx", "whoosh.txt")
        self.assertEqual(assets.resolve(self.project, "sfx", "whoosh")["status"], "pending")

    def test_ambiguity_is_decided_by_listing(self):
        self._put(self.project, "assets/sfx", "whoosh.wav")
        self._put(self.project, "assets/sfx", "whoosh.mp3")
        with self.assertRaises(ValueError) as ctx:
            assets.resolve(self.project, "sfx", "whoosh")
        self.assertIn("whoosh.wav", str(ctx.exception))
        self.assertIn("whoosh.mp3", str(ctx.exception))

    def test_invalid_names(self):
        for name in ("../x", "/etc/passwd", "a/b", "a\\b", "", ".."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                assets.resolve(self.project, "sfx", name)

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_outside_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="gb-out-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "boom.wav").write_text("x", encoding="utf-8")
        (self.project / "assets/sfx").mkdir(parents=True)
        (self.project / "assets/sfx/boom.wav").symlink_to(outside / "boom.wav")
        with self.assertRaises(ValueError) as ctx:
            assets.resolve(self.project, "sfx", "boom")
        self.assertIn("fora", str(ctx.exception))

    def test_listing_does_not_create_folders(self):
        self.assertEqual(assets.listing(self.project), [])
        self.assertFalse((self.project / "assets").exists())
        self._put(self.project, "assets/marca", "logo.svg")
        rows = assets.listing(self.project, "marca")
        self.assertEqual([r["name"] for r in rows], ["logo"])


class FrozenAssetFoldersTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-assets-folders-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_asset_folders_are_frozen_in_portuguese(self):
        self.assertEqual(
            ("marca", "lettering", "sfx", "musica", "imagem", "composicoes", "outros"), layout.ASSET_FOLDERS
        )

    def test_asset_folders_are_the_kind_folders_plus_outros(self):
        tails = {
            k.folder.removeprefix("assets/") for k in assets.ASSET_KINDS.values() if k.folder.startswith("assets/")
        }
        self.assertEqual(tails | {"outros"}, set(layout.ASSET_FOLDERS))
        self.assertNotIn("outros", {k.folder.removeprefix("assets/") for k in assets.ASSET_KINDS.values()})

    def test_imagem_kind_is_licensed_and_personal(self):
        spec = assets.ASSET_KINDS["imagem"]
        self.assertEqual("assets/imagem", spec.folder)
        self.assertEqual(assets.IMAGE, spec.extensions)
        self.assertTrue(spec.licensed)
        self.assertTrue(spec.personal)

    def test_where_finds_an_image_with_the_licence_warning(self):
        path = self.project / "assets" / "imagem" / "foto.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        found = run_cli("assets", "--action", "where", "--kind", "imagem", "--name", "foto", project=self.project)
        self.assertEqual("found", found["status"])
        self.assertEqual("project", found["origin"])
        self.assertTrue(found["path"].endswith("foto.png"))
        self.assertIn("foto.png: licença não registrada (foto.licenca.json)", found["warnings"])
