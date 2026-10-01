"""`docs/SCHEMAS.md` lista todo formato publicado e os nomes que o código reserva.

Um `schemas/*.schema.json` novo, uma família `getbrolls.<nome>/<N>` nova, uma pasta de
componente, um estado de análise, um papel de mídia, uma diretiva ou um id de plugin
reservado só entra no código junto com a linha dele no documento.
"""

import json
import re
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

# isort: split
# `_paths` acima põe `scripts/` em sys.path; só depois dele o script do bump importa.
import bump_version

from getbrolls import analysis_contract, assets, clients, layout, roteiro, templates, vocab
from getbrolls.sdk import contracts, marketplace, marketplace_index

DOC = ROOT / "docs" / "SCHEMAS.md"
SCHEMAS = sorted((ROOT / "schemas").glob("*.schema.json"))
FAMILY_LITERAL = re.compile(r"getbrolls\.[a-z][a-z0-9_]*/[1-9][0-9]*")


def doc_text():
    return DOC.read_text(encoding="utf-8")


def families_in_code():
    """Toda família `getbrolls.<nome>/<N>` que o código grava ou confere."""
    found = {
        layout.PROJECT_SCHEMA,
        f"getbrolls.{clients.CLIENT_SCHEMA}/{clients.SUPPORTED}",
        f"getbrolls.{clients.REGISTRY_SCHEMA}/{clients.SUPPORTED}",
        f"getbrolls.{templates.FAMILY}/{templates.SUPPORTED}",
        f"getbrolls.{templates.LOCK_FAMILY}/{templates.SUPPORTED}",
        f"getbrolls.{marketplace.STATE_FAMILY}/{marketplace.STATE_VERSION}",
        f"getbrolls.{marketplace_index.FAMILY}/{marketplace_index.SCHEMA_VERSION}",
        *(f"getbrolls.{name}/{version}" for name, version in analysis_contract.SUPPORTED.items()),
    }
    for path in (ROOT / "scripts" / "getbrolls").rglob("*.py"):
        found.update(FAMILY_LITERAL.findall(path.read_text(encoding="utf-8")))
    return found


class PublishedSchemas(unittest.TestCase):
    def test_every_published_schema_is_linked(self):
        text = doc_text()
        self.assertTrue(SCHEMAS)
        for path in SCHEMAS:
            with self.subTest(schema=path.name):
                self.assertIn(f"](../schemas/{path.name})", text)

    def test_no_link_points_to_a_schema_that_does_not_exist(self):
        linked = set(re.findall(r"\]\(\.\./schemas/([^)#]+)\)", doc_text()))
        self.assertEqual({path.name for path in SCHEMAS}, linked)

    def test_every_schema_constant_is_in_the_family_table(self):
        text = doc_text()
        for path in SCHEMAS:
            constant = json.loads(path.read_text(encoding="utf-8")).get("properties", {}).get("schema", {}).get("const")
            if constant:
                with self.subTest(schema=path.name):
                    self.assertIn(f"`{constant}`", text)

    def test_every_family_in_the_code_is_named(self):
        text = doc_text()
        for family in sorted(families_in_code()):
            with self.subTest(family=family):
                self.assertIn(f"`{family}`", text)

    def test_the_id_host_constant_is_the_documented_one(self):
        text = doc_text()
        self.assertIn("`SCHEMA_ID_BASE`", text)
        self.assertIn(f"{bump_version.SCHEMA_ID_BASE}/v<versão>/schemas/<arquivo>", text)


class ReservedNamesAndVocabulary(unittest.TestCase):
    def test_every_asset_folder_is_listed(self):
        text = doc_text()
        for folder in layout.ASSET_FOLDERS:
            with self.subTest(folder=folder):
                self.assertRegex(text, rf"(?m)^\| `{folder}` \|")

    def test_every_analysis_status_is_listed_with_its_reason_rule(self):
        text = doc_text()
        for status in vocab.ANALYSIS_STATUSES:
            with self.subTest(status=status):
                row = re.search(rf"(?m)^\| `{status}` \| (.+) \|$", text)
                self.assertIsNotNone(row, status)
                assert row is not None
                self.assertEqual(status in vocab.ANALYSIS_STATUSES_WITH_REASON, "exige `reason`" in row[1])

    def test_every_media_role_is_named(self):
        text = doc_text()
        for role in vocab.MEDIA_ROLES:
            with self.subTest(role=role):
                self.assertIn(f"`{role}`", text)

    def test_reserved_directives_ids_and_frontmatter_keys_are_named(self):
        text = doc_text()
        for directive in roteiro.RESERVED_DIRECTIVES:
            with self.subTest(directive=directive):
                self.assertIn(f"`[{directive}: …]`", text)
        for ident in contracts.RESERVED_IDS:
            with self.subTest(ident=ident):
                self.assertIn(f"`{ident}`", text)
        for key in ("cliente", "direcao"):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", text)

    def test_licence_keys_are_named_in_both_spellings(self):
        text = doc_text()
        for key, aliases in assets.LICENSE_ALIASES.items():
            for name in (key, *aliases):
                with self.subTest(key=name):
                    self.assertIn(f"`{name}`", text)


if __name__ == "__main__":
    unittest.main()
