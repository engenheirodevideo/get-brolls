"""Manifesto getbrolls-plugin.json: forma, compatibilidade e recusas explícitas."""

import json
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.sdk.manifest import ManifestError, compatibility_problem, read_manifest, satisfies

BASE = {
    "id": "acme_drive",
    "name": "Acervo Acme",
    "version": "1.0.0",
    "sdk_api": 1,
    "requires_getbrolls": ">=2.5,<3",
    "entry": "plugin.py",
    "contributes": {"providers": ["acme_drive"]},
    "permissions": {"network": ["api.acme.example"], "env": ["ACME_TOKEN"]},
}


def write_plugin(root, manifest, folder=None, entry=True):
    path = Path(root) / (folder or manifest.get("id", "sem_id"))
    path.mkdir(parents=True, exist_ok=True)
    (path / "getbrolls-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    if entry:
        (path / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    return path


class ManifestTests(unittest.TestCase):
    def test_valid_manifest_is_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = read_manifest(write_plugin(tmp, BASE))
        self.assertEqual(["acme_drive"], data["contributes"]["providers"])
        self.assertEqual([], data["contributes"]["presets"])
        self.assertEqual({}, data["schema"])
        self.assertEqual(["ACME_TOKEN"], data["permissions"]["env"])

    def test_each_bad_field_is_refused_with_its_name(self):
        cases = {
            "id": {**BASE, "id": "Acme"},
            "version": {**BASE, "version": "1.0"},
            "entry": {**BASE, "entry": "../fora.py"},
            "contributes": {**BASE, "contributes": {"providers": ["Nome Ruim"]}},
            "network": {**BASE, "permissions": {"network": ["https://api.acme.example"]}},
            "env": {**BASE, "permissions": {"env": ["acme_token"]}},
            "description": {**BASE, "description": 123},
            "desconhecido": {**BASE, "desconhecido": 1},
            "core": {**BASE, "id": "core"},
        }
        for label, manifest in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                folder = write_plugin(tmp, manifest, folder="acme_drive")
                with self.assertRaises(ManifestError):
                    read_manifest(folder, require_folder_match=False)

    def test_unsupported_kinds_are_refused(self):
        manifest = {**BASE, "contributes": {"exporters": ["acme_sheet"]}}
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError) as caught:
            read_manifest(write_plugin(tmp, manifest))
        self.assertIn("exporters", str(caught.exception))
        self.assertIn("ainda não é suportado", str(caught.exception))

    def test_folder_must_match_id_when_installed(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError):
            read_manifest(write_plugin(tmp, BASE, folder="outra_pasta"))

    def test_missing_entry_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError):
            read_manifest(write_plugin(tmp, BASE, entry=False))

    def test_version_ranges(self):
        self.assertTrue(satisfies("2.5.0", ">=2.5,<3"))
        self.assertTrue(satisfies("2.6.1", ">=2.6.0"))
        self.assertFalse(satisfies("3.0.0", ">=2.5,<3"))
        self.assertFalse(satisfies("2.4.9", ">=2.5"))
        self.assertTrue(satisfies("2.5.0", "==2.5.0"))
        with self.assertRaises(ManifestError):
            satisfies("2.5.0", "~2.5")

    def test_compatibility_problem_names_the_reason(self):
        self.assertIsNone(compatibility_problem({**BASE}, version="2.5.0"))
        self.assertIn("sdk_api", compatibility_problem({**BASE, "sdk_api": 2}, version="2.5.0"))  # type: ignore[arg-type]
        self.assertIn(">=2.5,<3", compatibility_problem({**BASE}, version="3.0.0"))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
