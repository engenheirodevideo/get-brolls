"""Contrato do plano de export: política de evolução, schema publicado aberto e plano de exemplo."""

import json
import re
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT
from _schemas import example_plan, published, strict

from getbrolls import __version__, export_plan
from getbrolls.sdk import ExporterSpec, ExportResult, testing
from getbrolls.sdk.exporters import find_local_paths, sample_plan
from getbrolls.sdk.jsonschema import errors

TAG_ID = re.compile(
    r"https://raw\.githubusercontent\.com/engenheirodevideo/get-brolls/v\d+\.\d+\.\d+/schemas/export_plan\.schema\.json"
)


def _growable(schema):
    """Os objetos que podem crescer: topo, meta, cena, camada e entrada de mídia."""
    scene = schema["properties"]["scenes"]["items"]
    return {
        "topo": schema,
        "meta": schema["properties"]["meta"],
        "cena": scene,
        "camada": scene["properties"]["layers"]["items"],
        "mídia": schema["properties"]["media"]["additionalProperties"],
    }


def _with_extra(plan, where):
    plan = json.loads(json.dumps(plan))
    target = {
        "topo": plan,
        "meta": plan["meta"],
        "cena": plan["scenes"][0],
        "camada": plan["scenes"][0]["layers"][0],
        "mídia": next(iter(plan["media"].values())),
    }[where]
    target["campo_futuro"] = None
    return plan


class PublishedSchemaTests(unittest.TestCase):
    def test_id_points_to_a_release_tag(self):
        self.assertRegex(published()["$id"], TAG_ID)

    def test_old_schemas_keep_their_id(self):
        for name in ("brief", "candidate"):
            schema = json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
            self.assertIn("/get-brolls/main/schemas/", schema["$id"], name)

    def test_versions_stay_const(self):
        props = published()["properties"]
        self.assertEqual({"const": 1}, props["export_version"])
        self.assertEqual({"const": 2}, props["plan_version"])

    def test_description_states_the_evolution_policy(self):
        text = published()["description"]
        for fragment in ("ignora chave desconhecida", "PluginError", "export_version", "plan_version", "aditiv"):
            self.assertIn(fragment, text)

    def test_growable_objects_are_open_for_consumers(self):
        for where, node in _growable(published()).items():
            with self.subTest(where=where):
                self.assertNotIn("additionalProperties", node)
                self.assertEqual([], errors(_with_extra(example_plan(), where), published()))

    def test_fixed_objects_stay_closed(self):
        scene = published()["properties"]["scenes"]["items"]["properties"]
        layout = scene["layout"]
        for node in (layout, layout["properties"]["slots"]["items"], scene["extensions"]["items"]):
            self.assertIs(False, node["additionalProperties"])

    def test_the_strict_variant_refuses_any_unknown_key(self):
        for where in _growable(published()):
            with self.subTest(where=where):
                found = errors(_with_extra(example_plan(), where), strict())
                self.assertTrue(any("campo_futuro: campo não previsto" in item for item in found), found)


class ExamplePlanTests(unittest.TestCase):
    def test_example_plan_follows_the_strict_schema(self):
        plan = example_plan()
        self.assertEqual([], errors(plan, strict()))
        self.assertEqual([], export_plan.check_refs(plan))
        self.assertEqual([], find_local_paths(plan))

    def test_example_plan_covers_what_an_exporter_meets(self):
        plan = example_plan()
        layouts = {scene["layout"]["kind"] for scene in plan["scenes"]}
        self.assertGreaterEqual(len(layouts), 3, layouts)
        self.assertLessEqual({"clip", "aroll", "asset"}, {row["source"] for row in plan["media"].values()})
        self.assertTrue(plan["meta"]["legenda"])
        self.assertTrue(any(scene["layers"] for scene in plan["scenes"]))
        self.assertTrue(any(scene["words_timed"] for scene in plan["scenes"]))
        self.assertTrue(any(not row["available"] for row in plan["media"].values()))

    def test_sample_plan_is_the_example_with_the_current_version(self):
        plan = sample_plan()
        self.assertEqual(__version__, plan["getbrolls_version"])
        self.assertEqual({**example_plan(), "getbrolls_version": __version__}, plan)
        self.assertEqual([], errors(plan, strict()))

    def test_check_exporter_runs_the_example_plan(self):
        seen = []

        def export(plan, options):
            seen.append(plan)
            return ExportResult({"index.html": "<p>ok</p>"})

        testing.check_exporter(ExporterSpec("demo_html", "Exporta", export))
        self.assertEqual(sample_plan(), seen[0])
        self.assertGreaterEqual(len(seen[0]["scenes"]), 3)


class EvolutionDocTests(unittest.TestCase):
    def test_sdk_doc_states_the_policy(self):
        text = (ROOT / "docs" / "SDK.md").read_text(encoding="utf-8")
        self.assertIn("### Evolução do plano de export", text)
        section = text.split("### Evolução do plano de export", 1)[1].split("\n## ", 1)[0]
        for fragment in (
            "ignora chave desconhecida",
            "PluginError",
            "export_version",
            "plan_version",
            "CHANGELOG",
            "examples/plans/reels.plan.json",
        ):
            self.assertIn(fragment, section)


if __name__ == "__main__":
    unittest.main()
