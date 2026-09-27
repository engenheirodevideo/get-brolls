"""Contrato do plano de export: política de evolução, schema publicado aberto e plano de exemplo."""

import json
import re
import unittest
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT
from _schemas import example_plan, published, strict

from getbrolls import __version__, export_plan
from getbrolls.sdk import ExporterSpec, ExportResult, PluginError, exporters, testing
from getbrolls.sdk.exporters import find_local_paths, sample_plan
from getbrolls.sdk.jsonschema import errors

TAG_ID = re.compile(
    r"https://raw\.githubusercontent\.com/engenheirodevideo/get-brolls/v\d+\.\d+\.\d+/schemas/export_plan\.schema\.json"
)


def _objects(node, path="$"):
    """Todo sub-schema de objeto (com `properties`) do schema, com o caminho."""
    if not isinstance(node, dict):
        return []
    found = [(path, node)] if "properties" in node else []
    for name, sub in (node.get("properties") or {}).items():
        found += _objects(sub, f"{path}.{name}")
    found += _objects(node.get("items"), f"{path}[]")
    extra = node.get("additionalProperties")
    return found + _objects(extra, f"{path}.*")


EXTENSION = {
    "plugin": "exemplo", "name": "zoom", "args": [], "quoted": [], "line": 1, "anchor": 0, "word_offset": 0,
    "at_s": 0.0,
}  # fmt: skip


def _targets(plan):
    """Um objeto de cada nível do plano, para receber uma chave nova."""
    scene = plan["scenes"][0]
    scene["extensions"].append(dict(EXTENSION))
    plan["meta"]["fps"] = {"num": 30, "den": 1}
    plan["meta"]["canvas"] = {"width": 1080, "height": 1920}
    return {
        "topo": plan,
        "meta": plan["meta"],
        "fps": plan["meta"]["fps"],
        "canvas": plan["meta"]["canvas"],
        "cena": scene,
        "layout": scene["layout"],
        "vaga": scene["layout"]["slots"][0],
        "palavra": next(s for s in plan["scenes"] if s["words_timed"])["words_timed"][0],
        "camada": scene["layers"][0],
        "extensão": scene["extensions"][-1],
        "mídia": next(iter(plan["media"].values())),
    }


def _with_extra(where):
    plan = example_plan()
    _targets(plan)[where]["campo_futuro"] = None
    return plan


LEVELS = tuple(_targets(example_plan()))


class PublishedSchemaTests(unittest.TestCase):
    def test_id_points_to_a_release_tag(self):
        self.assertRegex(published()["$id"], TAG_ID)

    def test_id_tag_matches_getbrolls_version(self):
        """A tag do `$id` não é uma versão qualquer: é sempre `v{__version__}` — trava `bump_version.py`."""
        self.assertIn(f"/get-brolls/v{__version__}/schemas/export_plan.schema.json", published()["$id"])

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
        for fragment in (
            "ignora chave desconhecida",
            "em qualquer nível",
            "PluginError",
            "recusa export_version que não conhece",
            "plan_version",
            "aditiv",
            "fora de required",
        ):
            self.assertIn(fragment, text)

    def test_every_object_is_open_for_consumers(self):
        objects = _objects(published())
        self.assertGreaterEqual(len(objects), 11)
        for path, node in objects:
            with self.subTest(path=path):
                self.assertNotIn("additionalProperties", node)

    def test_an_unknown_key_passes_the_published_schema_at_any_level(self):
        for where in LEVELS:
            with self.subTest(where=where):
                self.assertEqual([], errors(_with_extra(where), published()))

    def test_the_strict_variant_refuses_any_unknown_key(self):
        for where in LEVELS:
            with self.subTest(where=where):
                found = errors(_with_extra(where), strict())
                self.assertTrue(any("campo_futuro: campo não previsto" in item for item in found), found)


class MetaReservationTests(unittest.TestCase):
    """`meta` reserva ids e base de tempo: sempre presentes, `null` enquanto não houver valor."""

    META = ("projeto_id", "cliente", "direcao", "fps", "canvas")

    def meta_errors(self, **values):
        plan = example_plan()
        plan["meta"].update(values)
        return errors(plan, strict())

    def test_meta_fields_are_required_and_nullable(self):
        meta = published()["properties"]["meta"]
        for name in self.META:
            with self.subTest(name=name):
                self.assertIn(name, meta["required"])
                self.assertIn("null", meta["properties"][name]["type"])
                self.assertTrue(meta["properties"][name].get("description"))
        self.assertEqual([], self.meta_errors(**dict.fromkeys(self.META)))

    def test_fps_is_a_fraction_and_canvas_is_width_by_height(self):
        self.assertEqual([], self.meta_errors(fps={"num": 30000, "den": 1001}, canvas={"width": 1080, "height": 1920}))
        for values in (
            {"fps": {"num": 0, "den": 1}},
            {"fps": 30},
            {"fps": {"num": 30}},
            {"canvas": {"width": 1080}},
            {"canvas": {"width": 1080, "height": 1920, "dpi": 72}},
        ):
            with self.subTest(values=values):
                self.assertTrue(self.meta_errors(**values))

    def test_client_and_direction_are_slugs(self):
        self.assertEqual([], self.meta_errors(cliente="acme-corp", direcao="rampa-e-whip"))
        for value in ("Acme Corp", "acme_corp", "-acme", ""):
            with self.subTest(value=value):
                self.assertTrue(self.meta_errors(cliente=value))


class CatalogReservationTests(unittest.TestCase):
    """Escopo de cliente, referência de catálogo e direção por cena: nomeados, ainda sem uso."""

    def test_schema_reserves_client_origin_ref_and_direction(self):
        schema = published()
        scene = schema["properties"]["scenes"]["items"]
        layer = scene["properties"]["layers"]["items"]
        origin = schema["properties"]["media"]["additionalProperties"]["properties"]["origin"]
        self.assertIn("client", origin["enum"])
        self.assertIn("ref", layer["required"])
        self.assertEqual(["string", "null"], layer["properties"]["ref"]["type"])
        self.assertIn("direction", scene["required"])
        self.assertEqual("array", scene["properties"]["direction"]["type"])
        for node in (origin, layer["properties"]["ref"], scene["properties"]["direction"]):
            self.assertIn("reservad", node["description"].lower())

    def test_core_output_leaves_them_empty(self):
        plan = example_plan()
        for scene in plan["scenes"]:
            self.assertEqual([], scene["direction"])
            self.assertTrue(all(layer["ref"] is None for layer in scene["layers"]))
        self.assertNotIn("client", {row["origin"] for row in plan["media"].values()})

    def test_client_origin_is_valid(self):
        plan = example_plan()
        next(iter(plan["media"].values()))["origin"] = "client"
        self.assertEqual([], errors(plan, strict()))


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

    def test_example_plan_is_internally_consistent(self):
        plan = example_plan()
        for scene in plan["scenes"]:
            timed = scene["words_timed"]
            if timed is None:
                continue
            with self.subTest(scene=scene["id"]):
                self.assertEqual(scene["words"], len(timed))
                end = scene["start_s"] + scene["duration_s"]
                self.assertTrue(all(scene["start_s"] <= w["start"] <= w["end"] <= end for w in timed))
        clips = [row["sha256"] for row in plan["media"].values() if row["source"] == "clip"]
        self.assertEqual(len(clips), len(set(clips)))
        self.assertNotIn("freesound", plan["media"]["asset:marca:logo"]["credit"])

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


class CheckExporterGuardTests(unittest.TestCase):
    def test_check_refuses_a_core_file_name(self):
        # O marcador (`.getbrolls-export.json`) já cai no validador: começa por ".".
        for name in ("getbrolls-plan.json", "GETBROLLS-PLAN.json"):
            files = {"index.html": "<p>ok</p>", name: "{}"}
            spec = ExporterSpec("demo_html", "Exporta", lambda plan, options, files=files: ExportResult(files))
            with self.subTest(name=name), self.assertRaises(AssertionError) as caught:
                testing.check_exporter(spec)
            self.assertIn("nome reservado do get-brolls", str(caught.exception))

    def test_a_missing_example_plan_is_a_clear_error(self):

        with (
            mock.patch.object(exporters, "SAMPLE_PLAN", ROOT / "examples" / "plans" / "sumiu.plan.json"),
            self.assertRaises(ValueError) as caught,
        ):
            sample_plan()
        self.assertIn("plano de exemplo", str(caught.exception))
        self.assertIn("examples/plans/sumiu.plan.json", str(caught.exception))


class EvolutionDocTests(unittest.TestCase):
    def doc_exporter(self):
        """A função `exporta` do exemplo de Exportadores do SDK.md, executada como está."""
        text = (ROOT / "docs" / "SDK.md").read_text(encoding="utf-8")
        code = text.split("## Exportadores", 1)[1].split("```python\n", 1)[1].split("```", 1)[0]
        code = code.replace('api.exporter("meu_banco_html", exporta, "Exporta o plano como página HTML")', "")
        namespace = {}
        # Roda o exemplo do próprio doc, como está.
        exec(compile(code, "SDK.md", "exec"), namespace)  # noqa: S102  # pylint: disable=exec-used
        return namespace["exporta"]

    def test_the_doc_example_refuses_an_unknown_export_version(self):
        exporta = self.doc_exporter()
        self.assertEqual([], errors(sample_plan(), strict()))
        testing.check_exporter(ExporterSpec("demo_html", "Exporta", exporta))
        with self.assertRaises(PluginError) as caught:
            exporta({**sample_plan(), "export_version": 2}, {"args": {}})
        self.assertIn("export_version 2 não é suportado", str(caught.exception))

    def test_sdk_doc_states_the_policy(self):
        text = (ROOT / "docs" / "SDK.md").read_text(encoding="utf-8")
        self.assertIn("### Evolução do plano de export", text)
        section = " ".join(text.split("### Evolução do plano de export", 1)[1].split("\n## ", 1)[0].split())
        for fragment in (
            "ignora chave desconhecida",
            "em qualquer nível",
            "PluginError",
            "recusa `export_version` que não conhece",
            "fora de `required`",
            "Os testes do core",
            "export_version",
            "plan_version",
            "CHANGELOG",
            "examples/plans/reels.plan.json",
        ):
            self.assertIn(fragment, section)


if __name__ == "__main__":
    unittest.main()
