"""`project.json`, o layout do projeto (0 inferido, 1 declarado) e a identidade única.

O layout 0 nunca é gravado: é a ausência do arquivo. O `id` do `project.json` é o
mesmo `project_id` do `brolls/manifest.json`, então um projeto tem uma identidade só.
"""

import copy
import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT
from _schemas import _close

from getbrolls import _paths, layout, review, versioning
from getbrolls.ledger import Ledger, existing_project_id
from getbrolls.sdk import schemas
from getbrolls.sdk.jsonschema import check_schema, errors

NEWER = "versão mais nova do get-brolls"
PROJECT_SCHEMA = ROOT / "schemas" / "project.schema.json"


class TempProject(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-layout-")).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def write_raw(self, text):
        (self.project / layout.PROJECT_FILE).write_text(text, encoding="utf-8")

    def write_doc(self, doc):
        self.write_raw(json.dumps(doc))


class ReadSchemaTests(unittest.TestCase):
    def test_declared_version_is_returned(self):
        self.assertEqual(1, versioning.read_schema({"schema": "getbrolls.project/1"}, "project"))
        self.assertEqual(2, versioning.read_schema({"schema": "getbrolls.project/2"}, "project", supported=2))

    def test_absent_means_one(self):
        self.assertEqual(1, versioning.read_schema({}, "project"))

    def test_newer_is_refused_with_the_standard_phrase(self):
        with self.assertRaises(ValueError) as ctx:
            versioning.read_schema({"schema": "getbrolls.project/2"}, "project", label="project.json")
        self.assertEqual(
            "project.json foi gravado por uma versão mais nova do get-brolls (schema getbrolls.project/2); "
            "atualize antes de continuar.",
            str(ctx.exception),
        )

    def test_other_family_or_malformed_is_invalid_not_newer(self):
        for bad in ("getbrolls.client/1", "getbrolls.project/0", "getbrolls.project/01", "project/1", 1, None, True):
            with self.subTest(value=bad), self.assertRaises(ValueError) as ctx:
                versioning.read_schema({"schema": bad}, "project", label="project.json")
            self.assertIn("project.json é incompatível", str(ctx.exception))
            self.assertNotIn(NEWER, str(ctx.exception))

    def test_invalid_hook_replaces_only_the_invalid_case(self):
        class OwnError(ValueError):
            pass

        with self.assertRaises(ValueError) as ctx:
            versioning.read_schema({"schema": "getbrolls.client/1"}, "project", invalid="mensagem própria")
        self.assertEqual("mensagem própria", str(ctx.exception))
        with self.assertRaises(OwnError):
            versioning.read_schema({"schema": "getbrolls.client/1"}, "project", invalid=lambda: OwnError("x"))
        with self.assertRaises(ValueError) as ctx:
            versioning.read_schema({"schema": "getbrolls.project/2"}, "project", invalid=lambda: OwnError("x"))
        self.assertNotIsInstance(ctx.exception, OwnError)
        self.assertIn(NEWER, str(ctx.exception))

    def test_stamp_schema_puts_the_field_first_and_copies(self):
        data = {"id": "x", "schema": "velho"}
        stamped = versioning.stamp_schema(data, "project")
        self.assertEqual(["schema", "id"], list(stamped))
        self.assertEqual("getbrolls.project/1", stamped["schema"])
        self.assertEqual("velho", data["schema"])
        self.assertEqual("getbrolls.project/3", versioning.stamp_schema({}, "project", 3)["schema"])


class InfoTests(TempProject):
    def test_no_file_means_layout_zero_inferred(self):
        found = layout.info(self.project)
        self.assertEqual((0, "inferred", None, None), (found.version, found.source, found.doc, found.problem))
        self.assertIsNone(layout.load_project(self.project))
        self.assertIsNone(layout.project_id(self.project))

    def test_valid_file_means_layout_one(self):
        doc = layout.new_project_doc(client="acme")
        layout.write_project(self.project, doc)
        found = layout.info(self.project)
        self.assertEqual((1, "project.json", None), (found.version, found.source, found.problem))
        self.assertEqual(doc, found.doc)
        self.assertEqual(doc["id"], layout.project_id(self.project))

    def test_invalid_file_is_layout_zero_with_a_problem_and_load_raises(self):
        for text in ("{", "[]", json.dumps({"schema": "getbrolls.project/1", "layout": 1})):
            with self.subTest(text=text):
                self.write_raw(text)
                found = layout.info(self.project)
                self.assertEqual(0, found.version)
                self.assertIsNone(found.doc)
                assert found.problem is not None
                self.assertIn("project.json", found.problem)
                with self.assertRaises(ValueError):
                    layout.load_project(self.project)

    def test_newer_schema_is_refused(self):
        doc = layout.new_project_doc()
        doc["schema"] = "getbrolls.project/2"
        self.write_doc(doc)
        with self.assertRaisesRegex(ValueError, NEWER):
            layout.load_project(self.project)
        self.assertIn(NEWER, layout.info(self.project).problem or "")

    def test_nan_and_infinity_are_refused(self):
        base = json.dumps(layout.new_project_doc())
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                self.write_raw(base.replace('"ext": {}', f'"ext": {{"x": {token}}}'))
                with self.assertRaises(ValueError):
                    layout.load_project(self.project)

    def test_nan_in_fps_or_canvas_is_refused_in_code(self):
        for field, value in (
            ("fps", {"num": float("nan"), "den": 1}),
            ("canvas", {"width": 1080, "height": float("inf")}),
            ("fps", {"num": True, "den": 1}),
            ("fps", {"num": 30, "den": 0}),
        ):
            with self.subTest(field=field, value=value):
                doc = layout.new_project_doc()
                doc[field] = value
                with self.assertRaises(ValueError):
                    layout.validate_project(doc)

    def test_bad_id_is_refused(self):
        for bad in ("não-é-uuid", str(uuid.uuid4()).upper(), ""):
            with self.subTest(value=bad):
                doc = layout.new_project_doc()
                doc["id"] = bad
                with self.assertRaises(ValueError):
                    layout.validate_project(doc)

    def test_layout_zero_is_never_written(self):
        doc = layout.new_project_doc()
        doc["layout"] = 0
        with self.assertRaises(ValueError):
            layout.write_project(self.project, doc)
        self.assertFalse((self.project / layout.PROJECT_FILE).exists())

    def test_symlinked_project_file_is_a_problem(self):
        target = self.project / "outro.json"
        layout.write_project(self.project, layout.new_project_doc())
        (self.project / layout.PROJECT_FILE).rename(target)
        try:
            (self.project / layout.PROJECT_FILE).symlink_to(target)
        except OSError:
            self.skipTest("sem permissão para criar link simbólico")
        with self.assertRaisesRegex(ValueError, "link"):
            layout.load_project(self.project)
        self.assertEqual(0, layout.info(self.project).version)


class WriteTests(TempProject):
    def test_write_refuses_to_overwrite(self):
        first = layout.new_project_doc()
        layout.write_project(self.project, first)
        before = (self.project / layout.PROJECT_FILE).read_bytes()
        with self.assertRaisesRegex(ValueError, "já existe"):
            layout.write_project(self.project, layout.new_project_doc())
        self.assertEqual(before, (self.project / layout.PROJECT_FILE).read_bytes())

    def test_written_file_round_trips_and_ends_in_newline(self):
        doc = layout.new_project_doc(
            client="acme", canvas=layout.parse_canvas("1080x1920"), fps=layout.parse_fps("30000/1001")
        )
        path = layout.write_project(self.project, doc)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("}\n"))
        self.assertEqual(doc, json.loads(text))
        self.assertEqual(doc, layout.load_project(self.project))

    def test_new_doc_defaults(self):
        doc = layout.new_project_doc()
        self.assertEqual("getbrolls.project/1", doc["schema"])
        self.assertEqual("schema", next(iter(doc)))
        self.assertEqual(1, doc["layout"])
        self.assertEqual(str(uuid.UUID(doc["id"])), doc["id"])
        for key in ("client", "template", "canvas", "fps"):
            self.assertIsNone(doc[key])
        self.assertEqual({}, doc["ext"])

    def test_new_doc_reuses_the_given_id(self):
        ident = str(uuid.uuid4())
        self.assertEqual(ident, layout.new_project_doc(project_id=ident)["id"])

    def test_bad_client_slug_is_refused(self):
        for bad in ("ACME", "acme corp", "-acme", "a" * 65):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                layout.new_project_doc(client=bad)


class ParserTests(unittest.TestCase):
    def test_canvas(self):
        self.assertEqual({"width": 1080, "height": 1920}, layout.parse_canvas("1080x1920"))
        for bad in ("0x1920", "-1x10", "abc", "1080", "1080x", "x1920", "1080x1920x3", "1e3x10", "nanxnan", ""):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                layout.parse_canvas(bad)

    def test_fps(self):
        self.assertEqual({"num": 30, "den": 1}, layout.parse_fps("30"))
        self.assertEqual({"num": 30000, "den": 1001}, layout.parse_fps("30000/1001"))
        for bad in ("0", "-1", "abc", "30/0", "30/", "/1", "29.97", "nan", "inf", ""):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                layout.parse_fps(bad)


class IdentityTests(TempProject):
    def test_review_project_id_prefers_project_json_and_mirrors_it(self):
        doc = layout.new_project_doc()
        layout.write_project(self.project, doc)
        ledger = Ledger(self.project)
        self.assertNotIn("project_id", ledger.data)
        self.assertEqual(doc["id"], review.project_id(ledger))
        self.assertEqual(doc["id"], Ledger(self.project).data["project_id"])

    def test_review_project_id_without_project_json_is_unchanged(self):
        ledger = Ledger(self.project)
        first = review.project_id(ledger)
        self.assertEqual(first, Ledger(self.project).data["project_id"])
        self.assertEqual(first, review.project_id(Ledger(self.project)))
        self.assertFalse((self.project / layout.PROJECT_FILE).exists())

    def test_existing_project_id_prefers_project_json(self):
        ledger = Ledger(self.project)
        manifest_id = review.project_id(ledger)
        self.assertEqual(manifest_id, existing_project_id(self.project))
        doc = layout.new_project_doc()
        layout.write_project(self.project, doc)
        self.assertEqual(doc["id"], existing_project_id(self.project))

    def test_existing_project_id_never_raises_on_a_bad_project_json(self):
        self.write_raw("{")
        self.assertIsNone(existing_project_id(self.project))
        (self.project / layout.PROJECT_FILE).unlink()
        manifest_id = review.project_id(Ledger(self.project))
        self.write_raw("{")
        self.assertEqual(manifest_id, existing_project_id(self.project))

    def test_review_project_id_refuses_a_bad_project_json(self):
        self.write_raw("{")
        with self.assertRaisesRegex(ValueError, "project.json"):
            review.project_id(Ledger(self.project))


class SchemaFileTests(unittest.TestCase):
    def test_schema_is_in_the_supported_subset_and_loads(self):
        schema = json.loads(PROJECT_SCHEMA.read_text(encoding="utf-8"))
        check_schema(schema)
        self.assertEqual(schema, schemas.load("project"))
        self.assertIn("project", schemas.NAMES)
        self.assertEqual({"const": layout.PROJECT_SCHEMA}, schema["properties"]["schema"])
        self.assertEqual({"enum": [1]}, schema["properties"]["layout"])

    def test_schema_is_shipped_as_package_data(self):
        self.assertIn("schemas/project.schema.json", _paths.REQUIRED_DATA)
        manifest = (ROOT / "packaging" / "data_manifest.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn("schemas/project.schema.json", manifest)

    def test_written_doc_matches_the_published_and_closed_schema(self):
        schema = schemas.load("project")
        closed = copy.deepcopy(schema)
        _close(closed)
        full = layout.new_project_doc(
            client="acme",
            template="cat:getbrolls/template/reels-acme@3",
            canvas=layout.parse_canvas("1080x1920"),
            fps=layout.parse_fps("30"),
        )
        for doc in (layout.new_project_doc(), full):
            with self.subTest(doc=doc):
                self.assertEqual([], errors(doc, schema))
                self.assertEqual([], errors(doc, closed))

    def test_closed_schema_catches_a_stray_field(self):
        closed = copy.deepcopy(schemas.load("project"))
        _close(closed)
        doc = {**layout.new_project_doc(), "solto": 1}
        self.assertIn("$.solto: campo não previsto no schema", errors(doc, closed))


if __name__ == "__main__":
    unittest.main()
