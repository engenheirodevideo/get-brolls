"""Rotas de componentes com entrada hostil: ocultos, symlinks, extensões, NFC e licença."""

import json
import os
import shutil
import tempfile
import unicodedata
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _isolation import GB_HOME
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

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

    @needs_symlink
    def test_symlinks_inside_are_allowed_broken_are_ignored(self):
        real = self._put("assets/sfx", "original.wav")
        (self.project / "assets/sfx/atalho.wav").symlink_to(real)
        (self.project / "assets/sfx/quebrado.wav").symlink_to(self.project / "nao-existe.wav")
        self.assertEqual(assets.resolve(self.project, "sfx", "atalho")["status"], "found")
        self.assertEqual(assets.resolve(self.project, "sfx", "quebrado")["status"], "pending")

    @needs_symlink
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

    def test_license_sidecar_with_bom_is_read(self):
        self._put("assets/sfx", "d.wav")
        data = json.dumps({"origem": "banco", "licenca": "CC0", "credito": "X"})
        (self.project / "assets/sfx/d.licenca.json").write_bytes(data.encode("utf-8-sig"))
        found = assets.resolve(self.project, "sfx", "d")
        self.assertEqual((found["license"]["licenca"], found["license_error"]), ("CC0", None))

    def _sidecar(self, name, data):
        self._put("assets/sfx", f"{name}.wav")
        self._put("assets/sfx", f"{name}.licenca.json", json.dumps(data))
        return assets.resolve(self.project, "sfx", name)

    def test_english_keys_are_refused_with_the_portuguese_name(self):
        found = self._sidecar("en", {"source": "banco", "license_name": "CC0", "attribution": "Fulano"})
        self.assertIsNone(found["license"])
        self.assertEqual(
            "en.licenca.json usa chaves em inglês, que saíram; troque source → origem, license_name → licenca, "
            "attribution → credito.",
            found["license_error"],
        )

    def test_one_english_key_beside_the_portuguese_ones_is_refused_too(self):
        data = {"origem": "banco", "licenca": "CC0", "credito": "X", "source": "banco"}
        found = self._sidecar("mix", data)
        self.assertIsNone(found["license"])
        self.assertIn("source → origem", found["license_error"])

    def test_extras_survive_after_the_portuguese_fields(self):
        found = self._sidecar("extra", {"origem": "banco", "licenca": "CC0", "credito": "X", "url": "https://x"})
        self.assertIsNone(found["license_error"])
        self.assertEqual({"origem": "banco", "licenca": "CC0", "credito": "X", "url": "https://x"}, found["license"])

    def test_missing_field_message_names_the_portuguese_keys(self):
        found = self._sidecar("falta", {"origem": "banco", "credito": "X"})
        self.assertEqual(
            "falta.licenca.json precisa de origem, licenca e credito preenchidos, em texto.", found["license_error"]
        )

    def test_english_keys_are_refused_in_any_letter_case(self):
        found = self._sidecar("caixa", {"Source": "banco", "License_Name": "CC0", "ATTRIBUTION": "Fulano"})
        self.assertIsNone(found["license"])
        self.assertEqual(
            "caixa.licenca.json usa chaves em inglês, que saíram; troque Source → origem, License_Name → licenca, "
            "ATTRIBUTION → credito.",
            found["license_error"],
        )

    def test_english_keys_win_over_a_newer_schema_version(self):
        data = {"schema_version": 99, "source": "banco", "license_name": "CC0", "attribution": "X"}
        found = self._sidecar("ordem", data)
        self.assertIn("usa chaves em inglês", found["license_error"])
        self.assertNotIn("versão mais nova", found["license_error"])

    def test_blank_values_count_as_missing(self):
        found = self._sidecar("branco", {"origem": "banco", "licenca": "   ", "credito": "X"})
        self.assertIsNone(found["license"])
        self.assertIn("preenchidos", found["license_error"])

    def test_a_value_that_is_not_text_is_refused(self):
        found = self._sidecar("numero", {"origem": 1, "licenca": "CC0", "credito": "X"})
        self.assertIsNone(found["license"])
        self.assertIn("preenchidos", found["license_error"])

    def test_a_sidecar_that_is_not_an_object_is_refused(self):
        for name, data in (("lista", [1]), ("nulo", None), ("texto", "CC0")):
            with self.subTest(name=name):
                found = self._sidecar(name, data)
                self.assertIsNone(found["license"])
                self.assertIn("preenchidos", found["license_error"])

    @unittest.skipIf(
        os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "chmod 000 não vale no Windows nem como root"
    )
    def test_an_unreadable_sidecar_is_an_io_problem_not_invalid_json(self):
        self._put("assets/sfx", "trancado.wav")
        sidecar = self.project / "assets/sfx/trancado.licenca.json"
        sidecar.write_text(json.dumps({"origem": "banco", "licenca": "CC0", "credito": "X"}), encoding="utf-8")
        sidecar.chmod(0)
        self.addCleanup(sidecar.chmod, 0o644)
        found = assets.resolve(self.project, "sfx", "trancado")
        self.assertIsNone(found["license"])
        self.assertEqual("trancado.licenca.json não pôde ser lido: Permission denied.", found["license_error"])
        self.assertNotIn("JSON", found["license_error"])
        self.assertNotIn(str(self.project), found["license_error"])
        self.assertEqual([found["license_error"]], found["warnings"])

    def test_schema_version_one_is_accepted_and_newer_is_refused(self):
        base = {"origem": "banco", "licenca": "CC0", "credito": "X"}
        found = self._sidecar("v1", {"schema_version": 1, **base})
        self.assertEqual((base, None), (found["license"], found["license_error"]))
        found = self._sidecar("v2", {"schema_version": 2, **base})
        self.assertIsNone(found["license"])
        self.assertIn("versão mais nova do getbrolls", found["license_error"])
        found = self._sidecar("vtexto", {"schema_version": "1", **base})
        self.assertIsNone(found["license"])
        self.assertIn("incompatível", found["license_error"])


if __name__ == "__main__":
    unittest.main()
