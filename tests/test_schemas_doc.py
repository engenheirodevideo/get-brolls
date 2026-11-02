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

from getbrolls import analysis_contract, assets, clients, layout, recipe_init, recipes, roteiro, vocab
from getbrolls.sdk import contracts, marketplace, marketplace_index, schemas

DOC = ROOT / "docs" / "SCHEMAS.md"
SCHEMAS = sorted((ROOT / "schemas").glob("*.schema.json"))
FAMILY_LITERAL = re.compile(r"getbrolls\.[a-z][a-z0-9_]*/[1-9][0-9]*")


def doc_text():
    return DOC.read_text(encoding="utf-8")


def families_in_code():
    """Toda família `getbrolls.<nome>/<N>` que o código grava ou confere."""
    found = {
        f"getbrolls.{layout.PROJECT_FAMILY}/{schemas.version(layout.PROJECT_FAMILY)}",
        f"getbrolls.{clients.CLIENT_SCHEMA}/{schemas.version(clients.CLIENT_SCHEMA)}",
        f"getbrolls.{clients.REGISTRY_SCHEMA}/{schemas.version(clients.REGISTRY_SCHEMA)}",
        f"getbrolls.{recipes.FAMILY}/{schemas.version(recipes.FAMILY)}",
        f"getbrolls.{recipes.COMPONENTS_FAMILY}/{schemas.version(recipes.COMPONENTS_FAMILY)}",
        f"getbrolls.{recipe_init.LOCK_FAMILY}/{schemas.version(recipe_init.LOCK_FAMILY)}",
        f"getbrolls.{marketplace.STATE_FAMILY}/{schemas.version(marketplace.STATE_FAMILY)}",
        f"getbrolls.{marketplace_index.FAMILY}/{schemas.version(marketplace_index.FAMILY)}",
        *(f"getbrolls.{name}/{version}" for name, version in analysis_contract.supported().items()),
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

    def test_every_schema_is_listed_with_its_version_field_and_value(self):
        lines = doc_text().splitlines()
        for name in schemas.names():
            field = schemas.version_field(name)
            value = schemas.load(name)["properties"][field].get("const")
            shown = f"`{value}`" if isinstance(value, str) else f"`{schemas.version(name)}`"
            link = f"](../schemas/{name}.schema.json)"
            with self.subTest(schema=name):
                rows = [line for line in lines if link in line]
                self.assertTrue(rows, f"{name}: sem linha com o link")
                self.assertTrue(
                    any(f"`{field}`" in row and shown in row for row in rows),
                    f"{name}: nenhuma linha traz `{field}` com {shown}",
                )

    def test_the_version_rule_names_the_registry_call(self):
        text = doc_text()
        self.assertIn("A versão mora no schema", text)
        self.assertIn("getbrolls.sdk.schemas.version", text)
        self.assertIn("tests/test_schema_registry.py", text)

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
        for key in ("cliente", "estilo"):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", text)

    def test_licence_keys_are_named_in_portuguese(self):
        text = doc_text()
        for key in assets.LICENSE_FIELDS:
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", text)
        self.assertFalse(hasattr(assets, "LICENSE_ALIASES"))


if __name__ == "__main__":
    unittest.main()
