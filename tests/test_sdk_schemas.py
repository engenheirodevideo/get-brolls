"""`sdk.schemas`: os schemas públicos do core, pelo nome."""

import importlib
import json
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # `_paths` também insere scripts/ em sys.path

from getbrolls import _paths as core_paths
from getbrolls.sdk import jsonschema, schemas

# Valor de versão de cada schema, igual ao que o código grava hoje: a tabela é a prova de que o esqueleto novo
# não mudou nenhum valor (campo, valor).
EXPECTED_VERSIONS = {
    "analysis_index": ("schema", 1),
    "brief": ("version", 1),
    "candidate": ("schema_version", 1),
    "capabilities": ("schema_version", 1),
    "client": ("schema", 1),
    "clients": ("schema", 1),
    "components": ("schema", 1),
    "draft": ("schema", 1),
    "export_marker": ("schema_version", 1),
    "export_plan": ("export_version", 2),
    "handoff": ("schema", 1),
    "handoff_return": ("schema", 1),
    "instagram_summary": ("schema_version", 1),
    "library_index": ("schema_version", 2),
    "manifest": ("schema_version", 1),
    "markers": ("schema", 1),
    "marketplace_index": ("schema", 1),
    "marketplaces": ("schema", 1),
    "media": ("schema", 1),
    "pending_transaction": ("schema_version", 1),
    "plugin_manifest": ("sdk_api", 1),
    "plugins_state": ("schema", 1),
    "profile_toml": ("schema_version", 1),
    "project": ("schema", 1),
    "queue": ("schema_version", 2),
    "recipe": ("schema", 1),
    "recipe_lock": ("schema", 1),
    "references": ("schema_version", 1),
    "review_decisions": ("templateVersion", 2),
    "roteiro_state": ("schema_version", 1),
    "rules_block": ("version", 1),
    "runtime_marker": ("schema", 1),
    "scenes": ("schema", 1),
    "silence": ("schema", 1),
    "source_index": ("schema_version", 1),
    "speakers": ("schema", 1),
    "transcript": ("schema", 1),
    "trusted_profiles": ("schema_version", 1),
    "visual": ("schema", 1),
}


def fake(properties):
    """Um schema só com `properties`, para provar cada regra de `version` sem depender de um arquivo."""
    return {"type": "object", "properties": properties}


class RegistryTests(unittest.TestCase):
    def test_names_come_from_the_files_and_the_alias_follows(self):
        on_disk = sorted(path.name.removesuffix(".schema.json") for path in (ROOT / "schemas").glob("*.schema.json"))
        self.assertEqual(on_disk, list(schemas.names()))
        self.assertEqual(tuple(on_disk), tuple(schemas.NAMES))
        self.assertIn("export_plan", schemas.NAMES)

    def test_every_row_of_the_table_is_a_real_schema_and_every_schema_has_a_row(self):
        self.assertEqual(set(schemas.names()), set(schemas.VERSION_FIELD))
        self.assertEqual(set(EXPECTED_VERSIONS), set(schemas.names()))

    def test_every_schema_reports_the_field_and_value_the_code_writes_today(self):
        for name, (field, value) in EXPECTED_VERSIONS.items():
            with self.subTest(name=name):
                self.assertEqual(field, schemas.version_field(name))
                self.assertEqual(value, schemas.version(name))
                self.assertIn(field, schemas.load(name)["properties"])

    def test_every_family_constant_carries_the_number_the_registry_reports(self):
        family_of = {"plugins_state": "plugins"}
        for name in schemas.names():
            prop = schemas.load(name)["properties"].get("schema")
            if prop and isinstance(prop.get("const"), str):
                with self.subTest(name=name):
                    self.assertEqual(f"getbrolls.{family_of.get(name, name)}/{schemas.version(name)}", prop["const"])

    def test_maximum_wins_over_const_and_a_const_alone_is_the_version(self):
        with patch.object(
            schemas, "load", return_value=fake({"schema_version": {"type": "integer", "minimum": 1, "maximum": 4}})
        ):
            self.assertEqual(4, schemas.version("queue"))
        with patch.object(schemas, "load", return_value=fake({"schema_version": {"const": 3}})):
            self.assertEqual(3, schemas.version("manifest"))
        both = {"schema_version": {"maximum": 5, "const": 3}}
        with patch.object(schemas, "load", return_value=fake(both)):
            self.assertEqual(5, schemas.version("queue"))

    def test_the_family_constant_gives_its_number(self):
        with patch.object(schemas, "load", return_value=fake({"schema": {"const": "getbrolls.project/7"}})):
            self.assertEqual(7, schemas.version("project"))

    def test_a_schema_without_a_readable_version_is_refused(self):
        broken = (
            fake({}),
            fake({"schema_version": {"type": "integer"}}),
            fake({"schema_version": {"const": "um"}}),
            fake({"schema_version": {"const": True}}),
            fake({"schema_version": {"const": 0}}),
        )
        for doc in broken:
            with (
                self.subTest(doc=doc),
                patch.object(schemas, "load", return_value=doc),
                self.assertRaisesRegex(ValueError, "versão"),
            ):
                schemas.version("manifest")

    def test_a_family_constant_that_is_not_a_family_is_refused(self):
        for const in ("getbrolls.project/0", "project/1", "getbrolls.Project/1", "getbrolls.project/01"):
            with (
                self.subTest(const=const),
                patch.object(schemas, "load", return_value=fake({"schema": {"const": const}})),
                self.assertRaisesRegex(ValueError, "versão"),
            ):
                schemas.version("project")

    def test_a_second_pinned_field_is_read_the_same_way(self):
        self.assertEqual(1, schemas.version("project", "layout"))
        self.assertEqual(3, schemas.version("export_plan", "plan_version"))
        with self.assertRaisesRegex(ValueError, "campo de versão"):
            schemas.version("queue", "layout")

    def test_a_floor_with_a_default_reads_the_default_and_the_floor_still_validates(self):
        prop = {"type": "integer", "minimum": 3, "default": 4}
        schema = fake({"plan_version": prop})
        with patch.object(schemas, "load", return_value=schema):
            self.assertEqual(4, schemas.version("export_plan", "plan_version"))
        for number, expected in ((2, False), (3, True), (4, True)):
            with self.subTest(number=number):
                self.assertEqual(expected, not jsonschema.errors({"plan_version": number}, schema))

    def test_the_default_of_the_published_plan_version_is_not_below_its_floor(self):
        prop = schemas.load("export_plan")["properties"]["plan_version"]
        self.assertGreaterEqual(prop["default"], prop["minimum"])
        self.assertEqual(prop["default"], schemas.version("export_plan", "plan_version"))

    def test_an_unknown_name_has_no_version(self):
        for call in (schemas.version, schemas.version_field):
            with self.subTest(call=call.__name__), self.assertRaisesRegex(ValueError, "Schema desconhecido: "):
                call("nao-existe")

    def test_version_follows_the_loaded_schema_and_its_cache(self):
        schemas.load.cache_clear()
        self.addCleanup(schemas.load.cache_clear)
        with patch.object(schemas, "load", return_value=fake({"schema_version": {"const": 9}})) as mocked:
            self.assertEqual(9, schemas.version("manifest"))
            mocked.assert_called_once_with("manifest")


class SchemasTests(unittest.TestCase):
    def test_every_public_name_loads_the_packaged_schema(self):
        for name in schemas.NAMES:
            with self.subTest(name=name):
                on_disk = json.loads(core_paths.data_path("schemas", f"{name}.schema.json").read_text(encoding="utf-8"))
                self.assertEqual(on_disk, schemas.load(name))

    def test_an_unknown_name_is_refused_and_names_the_schema(self):
        for name in ("nao-existe", "", "brief.schema", "../brief"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Schema desconhecido: "):
                schemas.load(name)

    def test_the_schema_is_read_once_per_process(self):
        self.assertIs(schemas.load("brief"), schemas.load("brief"))

    def test_the_old_version_names_are_gone_from_the_modules(self):
        # A versão se lê só por `schemas.version(<família>)`; o nome antigo em cada módulo não existe mais.
        old_names = (
            ("getbrolls.capabilities", "SCHEMA_VERSION"),
            ("getbrolls.clients", "SUPPORTED"),
            ("getbrolls.profile", "SCHEMA_VERSION"),
            ("getbrolls.recipes", "SUPPORTED"),
            ("getbrolls.sdk.marketplace", "STATE_VERSION"),
            ("getbrolls.sdk.marketplace_index", "SCHEMA_VERSION"),
        )
        for module_name, name in old_names:
            with self.subTest(module=module_name, name=name):
                module = importlib.import_module(module_name)
                with self.assertRaises(AttributeError):
                    getattr(module, name)


if __name__ == "__main__":
    unittest.main()
