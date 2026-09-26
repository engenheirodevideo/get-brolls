"""Nomes reservados do SDK: tipos de contribuição, chave `engines` e ids de plugin."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from test_sdk_loader import MANIFEST, LoaderTestCase
from test_sdk_manifest import BASE, write_plugin

from getbrolls.sdk import loader, scaffold
from getbrolls.sdk.contracts import CORE, RESERVED_IDS
from getbrolls.sdk.manifest import CONTRIBUTION_KINDS, SUPPORTED_KINDS, ManifestError, read_manifest

FUTURE_KINDS = ("capturers", "engines", "catalogs", "roteiro_templates")
RESERVED = ("cliente", "catalogo", "direcao", "template", "projeto")


class ReservedContributionTests(unittest.TestCase):
    def test_future_kinds_are_known_but_not_supported(self):
        for kind in FUTURE_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(kind, CONTRIBUTION_KINDS)
                self.assertNotIn(kind, SUPPORTED_KINDS)
                manifest = {**BASE, "contributes": {kind: ["acme_drive"]}}
                with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError) as caught:
                    read_manifest(write_plugin(tmp, manifest))
                self.assertIn(f"contributes.{kind} ainda não é suportado nesta versão", str(caught.exception))

    def test_an_empty_future_kind_is_accepted(self):
        manifest = {**BASE, "contributes": {**BASE["contributes"], **{kind: [] for kind in FUTURE_KINDS}}}
        with tempfile.TemporaryDirectory() as tmp:
            data = read_manifest(write_plugin(tmp, manifest))
        self.assertEqual(["acme_drive"], data["contributes"]["providers"])


class EnginesKeyTests(unittest.TestCase):
    def test_top_level_engines_is_refused_as_not_supported_yet(self):
        manifest = {**BASE, "engines": {"hyperframes": ">=0.8.73,<0.9"}}
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError) as caught:
            read_manifest(write_plugin(tmp, manifest))
        self.assertIn("engines ainda não é suportado nesta versão", str(caught.exception))
        self.assertNotIn("campo desconhecido", str(caught.exception))

    def test_empty_engines_is_accepted_like_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = read_manifest(write_plugin(tmp, {**BASE, "engines": {}}))
        self.assertEqual("acme_drive", data["id"])


class ReservedIdTests(LoaderTestCase):
    def test_the_list_of_reserved_ids(self):
        self.assertEqual((CORE, *RESERVED), RESERVED_IDS)

    def test_manifest_refuses_a_reserved_id(self):
        for ident in RESERVED:
            with self.subTest(ident=ident), tempfile.TemporaryDirectory() as tmp:
                folder = write_plugin(tmp, {**BASE, "id": ident, "contributes": {}})
                with self.assertRaises(ManifestError) as caught:
                    read_manifest(folder)
                self.assertIn(f'id "{ident}" é reservado do get-brolls', str(caught.exception))

    def test_new_refuses_a_reserved_id(self):
        parent = Path(tempfile.mkdtemp(prefix="gb-new-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        for ident in RESERVED:
            with self.subTest(ident=ident):
                with self.assertRaises(ValueError) as caught:
                    scaffold.new(ident, "provider", parent=parent)
                self.assertIn("reservado", str(caught.exception))
                self.assertFalse((parent / ident).exists())
        out = run_cli("plugins", "--action", "new", "--id", "direcao", "--kind", "command", "--path", parent, expect=2)
        self.assertIn("reservado", out["message"])

    def test_install_refuses_a_reserved_id(self):
        source = Path(tempfile.mkdtemp(prefix="gb-src-")) / "cliente"
        self.addCleanup(shutil.rmtree, source.parent, ignore_errors=True)
        source.mkdir()
        (source / "getbrolls-plugin.json").write_text(
            json.dumps({**MANIFEST, "id": "cliente", "contributes": {}}), encoding="utf-8"
        )
        (source / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
        out = run_cli("plugins", "--action", "install", "--source", source, expect=2, env={"GB_HOME": str(self.home)})
        self.assertIn('id "cliente" é reservado do get-brolls', out["message"])
        self.assertFalse((self.home / "plugins" / "cliente").exists())

    def test_enable_refuses_a_reserved_id(self):
        self.install({**MANIFEST, "id": "projeto", "contributes": {}}, code="def register(api):\n    pass\n")
        rows = {row["id"]: row for row in loader.inventory()}
        self.assertEqual("invalid", rows["projeto"]["status"])
        with self.assertRaises(ValueError) as caught:
            loader.enable("projeto", confirm=True)
        self.assertIn('id "projeto" é reservado do get-brolls', str(caught.exception))
        self.assertFalse(loader.state_path().exists())


if __name__ == "__main__":
    unittest.main()
