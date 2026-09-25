"""Rotas de componentes depois da revisão adversarial: ocultos, symlinks, extensões, NFC e licença."""

import json
import os
import shutil
import tempfile
import unicodedata
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _isolation import GB_HOME
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import assets


class AssetHardeningTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-assets-h-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.addCleanup(shutil.rmtree, GB_HOME / "assets", ignore_errors=True)

    def _put(self, folder, name, text="x"):
        path = self.project / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_hidden_and_appledouble_files_are_ignored(self):
        self._put("assets/sfx", "._whoosh.wav")
        self._put("assets/sfx", ".whoosh.wav")
        self.assertEqual(assets.resolve(self.project, "sfx", "whoosh")["status"], "pending")
        self._put("assets/sfx", "whoosh.wav")
        self.assertEqual(assets.resolve(self.project, "sfx", "whoosh")["status"], "found")
        self.assertEqual([r["name"] for r in assets.listing(self.project, "sfx")], ["whoosh"])

    def test_directory_named_like_a_component_is_ignored(self):
        (self.project / "assets/sfx/whoosh.wav").mkdir(parents=True)
        self.assertEqual(assets.resolve(self.project, "sfx", "whoosh")["status"], "pending")

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlinks_inside_are_allowed_broken_are_ignored(self):
        real = self._put("assets/sfx", "original.wav")
        (self.project / "assets/sfx/atalho.wav").symlink_to(real)
        (self.project / "assets/sfx/quebrado.wav").symlink_to(self.project / "nao-existe.wav")
        self.assertEqual(assets.resolve(self.project, "sfx", "atalho")["status"], "found")
        self.assertEqual(assets.resolve(self.project, "sfx", "quebrado")["status"], "pending")

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_outside_is_a_row_error_in_listing(self):
        outside = Path(tempfile.mkdtemp(prefix="gb-out-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "boom.wav").write_text("x", encoding="utf-8")
        (self.project / "assets/sfx").mkdir(parents=True)
        (self.project / "assets/sfx/boom.wav").symlink_to(outside / "boom.wav")
        rows = assets.listing(self.project, "sfx")
        self.assertIn("fora", rows[0]["error"])

    def test_new_extensions(self):
        for folder, kind, name in (
            ("assets/marca", "marca", "logo.jpg"),
            ("assets/marca", "marca", "selo.JPEG"),
            ("assets/sfx", "sfx", "bip.aiff"),
            ("assets/musica", "musica", "tema.ogg"),
            ("assets/sfx", "sfx", "clique.aif"),
        ):
            with self.subTest(name=name):
                self._put(folder, name)
                self.assertEqual(assets.resolve(self.project, kind, Path(name).stem)["status"], "found")

    def test_names_are_nfc_normalized(self):
        self._put("assets/musica", unicodedata.normalize("NFD", "épica.mp3"))
        found = assets.resolve(self.project, "musica", unicodedata.normalize("NFD", "Épica"))
        self.assertEqual(found["status"], "found")
        self.assertTrue(assets.valid_name(unicodedata.normalize("NFD", "Épica")))
        self.assertFalse(assets.valid_name("../x"))
        self.assertFalse(assets.valid_name(None))

    def test_bad_license_sidecar_is_row_level(self):
        self._put("assets/sfx", "a.wav")
        self._put("assets/sfx", "a.licenca.json", "{quebrado")
        self._put("assets/sfx", "b.wav")
        self._put("assets/sfx", "b.licenca.json", json.dumps({"origem": "banco", "licenca": "", "credito": "X"}))
        self._put("assets/sfx", "c.wav")
        self._put("assets/sfx", "c.licenca.json", json.dumps({"origem": "banco", "licenca": "CC0", "credito": "X"}))
        rows = {r["name"]: r for r in assets.listing(self.project)}
        self.assertIn("JSON válido", rows["a"]["license_error"])
        self.assertIn("preenchidos", rows["b"]["license_error"])
        self.assertIsNone(rows["c"]["license_error"])
        self.assertEqual(rows["c"]["license"]["licenca"], "CC0")
        found = assets.resolve(self.project, "sfx", "b")
        self.assertEqual(found["status"], "found")
        self.assertIsNone(found["license"])
        self.assertIn("preenchidos", " ".join(found["warnings"]))
        self.assertNotIn("não registrada", " ".join(found["warnings"]))


if __name__ == "__main__":
    unittest.main()
