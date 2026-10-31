"""Ida e volta por família (entrega): o que o escritor grava vale o que o schema declara e valida contra ele.

Famílias deste grupo: export_plan, export_marker, review_decisions, instagram_summary, brief, project,
recipe_lock e as famílias de análise.
Para cada uma, o teste grava com o escritor real, confere que a versão gravada é `schemas.version(nome)`,
que o documento passa em `jsonschema.errors(doc, schemas.load(nome))`, que uma versão mais nova do
arquivo é recusada com a frase de versão (nunca um traceback) e, onde a família já existia na 2.5, que um
arquivo sem versão ainda lê como versão 1.
"""

import copy
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _schema_latency
import test_export_plan
import test_recipe_init
import test_review_edges
from _cli import run_cli
from _paths import ROOT

from getbrolls import analysis, export, export_folder, instagram_pairs, layout, recipe_init, review, roteiro_plan
from getbrolls import analysis_contract as ac
from getbrolls.brief import validate_brief
from getbrolls.brief_source import load_brief
from getbrolls.sdk import jsonschema, schemas

NEWER = "mais nova"
FIXTURES = ROOT / "tests" / "fixtures"
MARKER_BASE = {
    "export_version": 2,
    "exporter": "exporter_example",
    "plugin": "exporter_example",
    "plugin_version": "1.0.0",
    "getbrolls_version": "2.6.0",
    "created": "2026-10-24T12:00:00Z",
    "projeto_id": None,
    "cliente": None,
    "estilo": None,
}
PRODUCER = {"tool": "whisper_local", "model": "large-v3", "version": "0.3.0"}


def valid(case, doc, name):
    case.assertEqual([], jsonschema.errors(doc, schemas.load(name)), name)


def block(text):
    """O bloco ```json de um BRIEF.md."""
    found = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    assert found, "BRIEF.md sem bloco json"
    return json.loads(found.group(1))


class ProjectRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-delivery-project-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_writer_stamps_the_schema_version_and_the_layout_and_the_document_validates(self):
        doc = layout.new_project_doc()
        self.assertEqual(f"getbrolls.project/{schemas.version('project')}", doc["schema"])
        self.assertEqual(schemas.version("project", "layout"), doc["layout"])
        valid(self, doc, "project")
        layout.write_project(self.tmp, doc)
        self.assertEqual(doc, layout.load_project(self.tmp))

    def test_a_newer_schema_is_refused_with_the_standard_phrase_and_info_does_not_crash(self):
        doc = {**layout.new_project_doc(), "schema": f"getbrolls.project/{schemas.version('project') + 1}"}
        (self.tmp / "project.json").write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, NEWER):
            layout.load_project(self.tmp)
        found = layout.info(self.tmp)
        self.assertEqual(0, found.version)
        self.assertIn(NEWER, found.problem or "")

    def test_a_folder_without_project_json_is_layout_zero_and_a_file_without_schema_is_refused_cleanly(self):
        self.assertEqual((0, None), (layout.info(self.tmp).version, layout.load_project(self.tmp)))
        doc = layout.new_project_doc()
        del doc["schema"]
        (self.tmp / "project.json").write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fora do schema"):
            layout.load_project(self.tmp)


class RecipeLockRoundTrip(test_recipe_init.RecipeInitCase):
    def test_the_writer_stamps_the_schema_version_and_the_lock_validates(self):
        self.init()
        lock = self.lock()
        self.assertEqual(f"getbrolls.recipe_lock/{schemas.version('recipe_lock')}", lock["schema"])
        valid(self, lock, "recipe_lock")
        self.assertEqual(lock, recipe_init.validate_lock(lock))

    def test_a_newer_lock_is_refused_with_the_standard_phrase_and_a_lock_without_schema_cleanly(self):
        self.init()
        lock = self.lock()
        newer = {**lock, "schema": f"getbrolls.recipe_lock/{schemas.version('recipe_lock') + 1}"}
        with self.assertRaisesRegex(ValueError, NEWER):
            recipe_init.validate_lock(newer)
        del lock["schema"]
        with self.assertRaisesRegex(ValueError, "fora do schema"):
            recipe_init.validate_lock(lock)


class AnalysisRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-delivery-analysis-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "video"
        (self.project / "aroll").mkdir(parents=True)
        (self.project / "aroll" / "c01.mp4").write_bytes(b"voz da cena um" * 100)
        self.media = analysis.ensure_media(self.project, "aroll/c01.mp4", probe=False)
        self.media_id = self.media["media_id"]

    def body(self, name):
        doc = json.loads((FIXTURES / "analysis" / f"{name}.json").read_text(encoding="utf-8"))
        return {k: v for k, v in doc.items() if k not in ("schema", "media_id", "producer", "created", "time_unit")}

    def written(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def check_stamped(self, doc, name):
        self.assertEqual(f"getbrolls.{name}/{schemas.version(name)}", doc["schema"], name)
        valid(self, doc, name)
        self.assertEqual([], ac.check_doc(doc, name))

    def test_every_writer_stamps_the_schema_version_and_the_document_validates(self):
        self.check_stamped(self.written(self.project / "analysis" / "index.json"), "analysis_index")
        media_path = self.project / "analysis" / "media" / self.media_id / "media.json"
        self.check_stamped(self.written(media_path), "media")
        for name in ac.COMPONENTS:
            if name == "media":
                continue
            with self.subTest(component=name):
                path = analysis.write_component(self.project, self.media_id, name, self.body(name), producer=PRODUCER)
                self.check_stamped(self.written(path), name)
        marker = self.body("markers")["markers"][0]
        path = analysis.write_markers(
            self.project, PRODUCER, [{**marker, "media_id": self.media_id, "producer": PRODUCER}]
        )
        self.check_stamped(self.written(path), "markers")

    def test_a_newer_document_is_refused_by_name_and_an_unknown_one_is_not_the_newer_refusal(self):
        for name in ac.DOCUMENTS:
            doc = {**json.loads((FIXTURES / "analysis" / f"{name}.json").read_text(encoding="utf-8"))}
            doc["schema"] = f"getbrolls.{name}/{schemas.version(name) + 1}"
            with self.subTest(document=name):
                self.assertEqual([{"code": "NEWER_SCHEMA", "where": "$.schema"}], ac.check_doc(doc, name))
        with self.assertRaises(ac.Invalid) as unknown:
            ac.declared_schema({"schema": "getbrolls.outro/1"})
        self.assertEqual("UNKNOWN_SCHEMA", unknown.exception.code)

    def test_the_supported_versions_are_the_schema_versions(self):
        self.assertEqual({name: schemas.version(name) for name in ac.DOCUMENTS}, ac.supported())

    def test_a_large_document_validates_within_the_latency_bound(self):
        template = json.loads((FIXTURES / "analysis" / "transcript.json").read_text(encoding="utf-8"))
        word = template["words"][0]
        large = _schema_latency.large_document(template, "words", word)
        _schema_latency.assert_validates_within(self, large, schemas.load("transcript"))


class ExportPlanRoundTrip(test_export_plan.ExportPlanTestCase):
    def test_the_builder_stamps_both_versions_from_the_schema_and_the_plan_validates(self):
        plan, _ = test_export_plan.build(self.root, test_export_plan.min_project)
        self.assertEqual(schemas.version("export_plan"), plan["export_version"])
        self.assertEqual(schemas.version("export_plan", "plan_version"), plan["plan_version"])
        self.assertEqual(plan["plan_version"], roteiro_plan.plan_version())
        valid(self, plan, "export_plan")

    def test_the_plan_text_is_the_plan_as_json_and_still_validates(self):
        plan, _ = test_export_plan.build(self.root, test_export_plan.min_project)
        text = export._plan_text(plan)  # pylint: disable=protected-access
        self.assertTrue(text.endswith("\n"))
        valid(self, json.loads(text), "export_plan")

    def test_the_frozen_fixtures_validate(self):
        for name in ("export_plan_full.json", "export_plan_min.json"):
            with self.subTest(fixture=name):
                valid(self, json.loads((FIXTURES / name).read_text(encoding="utf-8")), "export_plan")

    def test_plan_version_has_a_floor_and_a_current_value_and_no_ceiling(self):
        schema = schemas.load("export_plan")["properties"]["plan_version"]
        self.assertNotIn("maximum", schema)
        self.assertGreaterEqual(schema["default"], schema["minimum"])
        plan, _ = test_export_plan.build(self.root, test_export_plan.min_project)
        self.assertEqual(schema["default"], plan["plan_version"])
        valid(self, {**plan, "plan_version": schema["minimum"]}, "export_plan")
        valid(self, {**plan, "plan_version": plan["plan_version"] + 5}, "export_plan")

    def test_the_example_plan_validates(self):
        example = json.loads((ROOT / "examples" / "plans" / "reels.plan.json").read_text(encoding="utf-8"))
        valid(self, example, "export_plan")


class ExportMarkerRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-delivery-marker-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / export_folder.MARKER

    def marker(self, state="complete"):
        return json.loads(export_folder._marker_bytes(MARKER_BASE, state, 1, {}))  # pylint: disable=protected-access

    def test_the_writer_stamps_the_schema_version_and_the_marker_validates(self):
        for state in ("staging", "complete"):
            with self.subTest(state=state):
                doc = self.marker(state)
                self.assertEqual(schemas.version("export_marker"), doc["schema_version"])
                valid(self, doc, "export_marker")

    def test_the_marker_the_writer_made_is_read_back(self):
        self.path.write_bytes(export_folder._marker_bytes(MARKER_BASE, "complete", 1, {}))  # pylint: disable=protected-access
        self.assertEqual(self.marker(), export_folder._read_marker(self.path))  # pylint: disable=protected-access

    def test_a_newer_marker_is_not_read_and_a_marker_from_2_5_without_a_version_still_is(self):
        newer = {**self.marker(), "schema_version": schemas.version("export_marker") + 1}
        self.path.write_text(json.dumps(newer), encoding="utf-8")
        self.assertIsNone(export_folder._read_marker(self.path))  # pylint: disable=protected-access
        old = self.marker()
        del old["schema_version"]
        self.path.write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(old, export_folder._read_marker(self.path))  # pylint: disable=protected-access

    def test_a_newer_staging_is_never_swept(self):
        root = self.tmp / "exports" / "exporter_example"
        staging = root / f"{export_folder.STAGING_PREFIX}abcd1234"
        staging.mkdir(parents=True)
        newer = {**self.marker("staging"), "schema_version": schemas.version("export_marker") + 1}
        (staging / export_folder.MARKER).write_text(json.dumps(newer), encoding="utf-8")
        warnings = export_folder.sweep_abandoned(root)
        self.assertTrue(staging.is_dir())
        self.assertEqual(1, len(warnings))


class ReviewDecisionsRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gb-delivery-review-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = test_review_edges._ledger(self.tmp, "one")  # pylint: disable=protected-access

    def exported(self):
        return test_review_edges._exported(self.ledger)  # pylint: disable=protected-access

    def test_the_page_payload_stamps_the_schema_version_and_validates(self):
        payload = self.exported()
        self.assertEqual(schemas.version("review_decisions"), payload["templateVersion"])
        valid(self, payload, "review_decisions")

    def test_a_newer_decisions_file_is_refused_with_the_standard_phrase_and_nothing_changes(self):
        payload = self.exported()
        payload["templateVersion"] = schemas.version("review_decisions") + 1
        saved = test_review_edges._save(self.ledger, payload)  # pylint: disable=protected-access
        before = self.ledger.path.read_bytes()
        with self.assertRaisesRegex(ValueError, NEWER):
            review.import_review(self.ledger, saved, "Human")
        self.assertEqual(before, self.ledger.path.read_bytes())

    def test_a_file_without_a_version_stays_the_other_collection_refusal(self):
        payload = self.exported()
        del payload["templateVersion"]
        saved = test_review_edges._save(self.ledger, payload)  # pylint: disable=protected-access
        with self.assertRaisesRegex(ValueError, "outra coleta"):
            review.import_review(self.ledger, saved, "Human")

    def test_the_current_file_imports(self):
        saved = test_review_edges._save(self.ledger, self.exported())  # pylint: disable=protected-access
        self.assertTrue(review.import_review(self.ledger, saved, "Human"))

    def test_a_large_decisions_file_validates_within_the_latency_bound(self):
        payload = self.exported()
        item = payload["items"][0]
        large = _schema_latency.large_document(payload, "items", item)
        _schema_latency.assert_validates_within(self, large, schemas.load("review_decisions"))


class InstagramSummaryRoundTrip(unittest.TestCase):
    def test_the_summary_stamps_the_schema_version_and_validates(self):
        results = [{"status": "done", "stem": "a"}, {"status": "failed", "stem": "b"}, {"status": "skipped"}]
        for stopped_by in (None, "cooldown"):
            with self.subTest(stopped_by=stopped_by):
                summary = instagram_pairs._build_summary(3, results, stopped_by)  # pylint: disable=protected-access
                self.assertEqual(schemas.version("instagram_summary"), summary["schema_version"])
                self.assertEqual((1, 1, 1), (summary["done"], summary["failed"], summary["skipped"]))
                valid(self, summary, "instagram_summary")

    def test_the_summary_written_to_disk_is_the_valid_document(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "sub" / "s.json"
            instagram_pairs.write_summary(target, instagram_pairs._build_summary(0, [], None))  # pylint: disable=protected-access
            valid(self, json.loads(target.read_text(encoding="utf-8")), "instagram_summary")


class BriefRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-delivery-brief-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_init_brief_writes_the_current_version_and_the_block_validates(self):
        run_cli("init-brief", "--project", self.tmp)
        data = block((self.tmp / "BRIEF.md").read_text(encoding="utf-8"))
        self.assertEqual(schemas.version("brief"), data["version"])
        valid(self, data, "brief")

    def test_init_brief_with_a_roteiro_writes_the_current_version_and_the_block_validates(self):
        (self.tmp / "ROTEIRO.md").write_text(test_export_plan.MIN_ROTEIRO, encoding="utf-8")
        run_cli("init-brief", "--project", self.tmp)
        data = block((self.tmp / "BRIEF.md").read_text(encoding="utf-8"))
        self.assertEqual((schemas.version("brief"), []), (data["version"], data["beats"]))
        valid(self, data, "brief")

    def test_the_template_validates_and_a_newer_version_is_refused_with_the_standard_phrase(self):
        run_cli("init-brief", "--project", self.tmp)
        data = load_brief(self.tmp)
        normalized, _ = validate_brief(data)
        self.assertEqual(schemas.version("brief"), normalized["version"])
        newer = copy.deepcopy(data)
        newer["version"] = schemas.version("brief") + 1
        with self.assertRaisesRegex(ValueError, NEWER):
            validate_brief(newer)

    def test_a_brief_without_a_version_or_with_a_wrong_one_keeps_the_existing_refusal(self):
        run_cli("init-brief", "--project", self.tmp)
        data = load_brief(self.tmp)
        for bad in (None, "1", True, 0, 1.0):
            broken = {**data, "version": bad}
            if bad is None:
                del broken["version"]
            with self.subTest(version=bad), self.assertRaisesRegex(ValueError, '"version" tem que ser o número'):
                validate_brief(broken)


if __name__ == "__main__":
    unittest.main()
