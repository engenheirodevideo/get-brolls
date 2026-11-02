"""Rascunho, pacote de entrega e volta: `getbrolls.draft/1`, `getbrolls.handoff/1` e `getbrolls.handoff_return/1`.

Os três formatos estão publicados em schemas/ e na pasta de dados do pacote; quem os grava chega depois
(rascunho e entrega). Os exemplos daqui são os da spec, com sha256 de mentira.
"""

import copy
import json
import unittest
from typing import Any

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT
from _schemas import close, example_plan, strict
from _schemas import published as export_plan_schema

from getbrolls import __version__, _paths
from getbrolls.sdk import jsonschema, schemas
from getbrolls.sdk.jsonschema import check_schema

SHA = "a" * 64
AT = "2026-10-20T14:02:11Z"
NAMES = ("draft", "handoff", "handoff_return")
MEDIA_ID = "clip:commons:6739626:shot:c01"
MISSING = [
    {"scene": "c02", "item": "c02.broll.1", "do": {"line": "Falta o b-roll da cena c02.", "command": None, "needs": []}}
]
CHECKS = [{"code": "LICENCE_PENDING", "status": "warn", "line": "A licença do whoosh está pendente."}]
PLAN = {
    "export_version": 2,
    "exporter": "hyperframes",
    "out_dir": "handoffs/hyperframes/002",
    "scenes": [],
    "media": {},
}
BRIDGE = {"id": "hyperframes", "version": "2.6.0", "origin": "official", "sha256": SHA}
DRAFT = {
    "schema": "getbrolls.draft/1",
    "origin": {"getbrolls_version": "2.6.0", "built_at": AT, "project_id": None, "layout": 1},
    "inputs": {"roteiro_sha256": SHA, "shelf_sha256": SHA, "recipe": {"name": "reels-split", "version": 2}},
    "plan": PLAN,
    "missing": MISSING,
    "checks": CHECKS,
}
HANDOFF = {
    "schema": "getbrolls.handoff/1",
    "id": "002",
    "env": "hyperframes",
    "created_at": AT,
    "getbrolls_version": "2.6.0",
    "project_id": None,
    "previous": None,
    "draft": {"id": "003", "sha256": SHA},
    "review": {"sha256": SHA, "by": "Bruno", "channel": "chat", "at": AT},
    "recipe": {"name": "reels-split", "version": 2},
    "timeline": {
        "fps": None, "canvas": {"width": 1080, "height": 1920}, "canvas_source": "project", "duration_s": 41.2,
    },
    "plan": PLAN,
    "missing": MISSING,
    "checks": CHECKS,
    "media_files": {
        MEDIA_ID: {
            "file": "assets/c01-saturn-v.mp4", "sha256": SHA, "bytes": 1234,
            "licence_ref": {"tier": "project", "sidecar_sha256": SHA},
        }
    },
    "files": [{"path": "RASCUNHO.md", "sha256": SHA}],
    "bridge": {**BRIDGE, "files": ["hyperframes/BRIEF.md", "hyperframes/LEIA-ME.md"]},
    "speech": None,
    "env_detail": {"entry": "index.html"},
}  # fmt: skip
RETURN = {
    "schema": "getbrolls.handoff_return/1",
    "seq": 1,
    "handoff": "002",
    "handoff_sha256": SHA,
    "env": "hyperframes",
    "read_at": AT,
    "bridge": BRIDGE,
    "state": "project",
    "artifacts": [{"kind": "project", "path": "project/", "files": 41}],
    "renders": [{"path": "project/renders/final.mp4", "bytes": 18234112}],
    "scenes": {"total": 5, "seen": None, "method": "count"},
    "package_intact": True,
    "problems": [],
    "untrusted_text": {"origin": "hyperframes", "body": "Projeto criado com 5 cenas."},
    "env_detail": {"project_dir": "project/", "entry": "index.html", "has_hyperframes_json": True},
}
EXAMPLES = {"draft": DRAFT, "handoff": HANDOFF, "handoff_return": RETURN}


class PublishedTests(unittest.TestCase):
    def test_each_family_is_published_and_packaged(self):
        manifest = (ROOT / "packaging" / "data_manifest.txt").read_text(encoding="utf-8").splitlines()
        for name in NAMES:
            with self.subTest(name=name):
                rel = f"schemas/{name}.schema.json"
                self.assertIn(name, schemas.NAMES)
                self.assertIn(rel, manifest)
                self.assertIn(rel, _paths.REQUIRED_DATA)
                schema = schemas.load(name)
                self.assertEqual(f"getbrolls {name.replace('_', ' ')} v1", schema["title"])
                self.assertTrue(schema["$id"].endswith(f"/v{__version__}/{rel}"), schema["$id"])
                self.assertEqual(f"getbrolls.{name}/1", schema["properties"]["schema"]["const"])

    def test_the_spec_examples_are_valid_open_and_closed(self):
        for name, doc in EXAMPLES.items():
            with self.subTest(name=name):
                self.assertEqual([], jsonschema.errors(doc, schemas.load(name)))
                self.assertEqual([], jsonschema.errors(doc, close(schemas.load(name))))

    def test_a_real_export_plan_fits_the_plan_node_open_and_closed(self):
        # O plano real tem as 12 chaves de schemas/export_plan.schema.json e é válido nele, com o out_dir de
        # exportação (exports/) e com o de pacote de entrega (handoffs/); o nó `plan` não fecha o resto das chaves
        # (a conferência fina é em código). close() fecha só nó com `properties`; o nó `plan` se mantém aberto.
        published = export_plan_schema()
        for out_dir in ("exports/hyperframes/001", "handoffs/hyperframes/002"):
            real = example_plan()
            real["out_dir"] = out_dir
            with self.subTest(out_dir=out_dir):
                self.assertEqual([], jsonschema.errors(real, published))
                self.assertEqual([], jsonschema.errors(real, strict(published)))
                for name in ("draft", "handoff"):
                    doc = {**copy.deepcopy(EXAMPLES[name]), "plan": real}
                    self.assertEqual([], jsonschema.errors(doc, schemas.load(name)))
                    self.assertEqual([], jsonschema.errors(doc, close(schemas.load(name))))

    def test_the_plan_node_keeps_its_keys_and_its_out_dir_grammar(self):
        out_dir = export_plan_schema()["properties"]["out_dir"]["pattern"]
        for name in ("draft", "handoff"):
            plan = schemas.load(name)["properties"]["plan"]
            with self.subTest(name=name):
                self.assertEqual(out_dir, plan["properties"]["out_dir"]["pattern"])
                for key in plan["required"]:
                    doc = copy.deepcopy(EXAMPLES[name])
                    del doc["plan"][key]
                    self.assertNotEqual([], jsonschema.errors(doc, schemas.load(name)), key)
                for key, value in (
                    ("export_version", 3),
                    ("exporter", "Hyper"),
                    ("out_dir", "exports/hyperframes/1"),
                    ("out_dir", "/abs/exports/hyperframes/001"),
                    ("out_dir", "handoffs/hyperframes/002/../003"),
                    ("scenes", {}),
                    ("media", []),
                ):
                    doc = copy.deepcopy(EXAMPLES[name])
                    doc["plan"][key] = value
                    self.assertNotEqual([], jsonschema.errors(doc, schemas.load(name)), (key, value))

    def test_bad_values_are_refused(self):
        cases = (
            ("draft", ("schema",), "getbrolls.draft/2"),
            ("draft", ("origin", "layout"), 0),
            ("draft", ("inputs", "roteiro_sha256"), "abc"),
            ("draft", ("missing", 0, "scene"), "c00"),
            ("draft", ("checks", 0, "code"), "licence_pending"),
            ("handoff", ("id",), "2"),
            ("handoff", ("env",), "Hyper"),
            ("handoff", ("review", "channel"), "email"),
            ("handoff", ("timeline", "canvas_source"), "chute"),
            ("handoff", ("media_files", MEDIA_ID, "file"), "/abs/c01.mp4"),
            ("handoff", ("media_files", MEDIA_ID, "file"), "c01.mp4"),
            ("handoff", ("media_files", MEDIA_ID, "file"), "../assets/c01.mp4"),
            ("handoff", ("files", 0, "path"), "../fora.md"),
            ("handoff", ("files", 0, "path"), "a/../fora.md"),
            ("handoff", ("bridge", "files"), ["hyperframes/../fora.md"]),
            ("handoff", ("previous",), {"id": "1"}),
            ("handoff", ("bridge", "origin"), "local"),
            ("handoff", ("bridge", "sha256"), "abc"),
            ("handoff", ("bridge", "version"), "2.6"),
            ("handoff", ("files", 0, "path"), "/abs/RASCUNHO.md"),
            ("handoff", ("files", 0, "path"), "project/x.json"),
            ("handoff", ("files", 0, "path"), "return/read-001.json"),
            ("handoff", ("files", 0, "path"), "handoff.json"),
            ("handoff", ("review", "statement"), "a frase dita"),
            ("handoff", ("speech",), {"media_id": "aroll:x"}),
            ("handoff", ("speech",), {"media_id": "aroll:x", "sha256": "abc"}),
            ("handoff", ("env_detail",), []),
            ("handoff_return", ("handoff_sha256",), "abc"),
            ("handoff_return", ("state",), "done"),
            ("handoff_return", ("seq",), 0),
            ("handoff_return", ("scenes", "method"), "guess"),
            ("handoff_return", ("scenes", "method"), "storyboard_extra"),
            ("handoff_return", ("untrusted_text", "body"), "x" * 4097),
            ("handoff_return", ("renders", 0, "path"), "/abs/final.mp4"),
            ("handoff_return", ("renders", 0, "path"), "project/../final.mp4"),
            ("handoff_return", ("artifacts", 0, "path"), "project/../x"),
        )
        for name, path, value in cases:
            with self.subTest(name=name, path=path):
                doc = copy.deepcopy(EXAMPLES[name])
                node: Any = doc
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = value
                self.assertNotEqual([], jsonschema.errors(doc, schemas.load(name)))

    def test_a_second_handoff_points_to_the_previous_one(self):
        # O segundo pacote é um NNN novo que aponta o anterior (a anterior fica intacta); a primeira entrega leva null.
        doc = copy.deepcopy(HANDOFF)
        doc["previous"] = {"id": "001"}
        self.assertEqual([], jsonschema.errors(doc, schemas.load("handoff")))
        self.assertEqual([], jsonschema.errors(doc, close(schemas.load("handoff"))))
        del doc["previous"]
        self.assertEqual([], jsonschema.errors(doc, schemas.load("handoff")))

    def test_the_return_counts_scenes_and_no_schema_requires_the_speech_text(self):
        # A fala do A-ROLL viaja como áudio e sha256 (media_files, apontado por speech); o retorno só conta cenas.
        method = schemas.load("handoff_return")["properties"]["scenes"]["properties"]["method"]
        self.assertEqual(["count"], method["enum"])
        for name in NAMES:
            with self.subTest(name=name):
                required = set(schemas.load(name)["required"])
                self.assertFalse(required & {"speech_text", "fala", "transcript", "storyboard"}, required)
        self.assertNotIn("storyboard", schemas.load("handoff")["properties"])

    def test_the_speech_marker_points_at_a_file_or_is_null(self):
        speech = {"media_id": "aroll:take1:speech", "sha256": SHA}
        doc = copy.deepcopy(HANDOFF)
        doc["speech"] = speech
        self.assertEqual([], jsonschema.errors(doc, schemas.load("handoff")))
        self.assertEqual([], jsonschema.errors(doc, close(schemas.load("handoff"))))
        del doc["speech"]
        self.assertEqual([], jsonschema.errors(doc, schemas.load("handoff")))
        self.assertNotIn("speech", schemas.load("handoff")["required"])
        description = schemas.load("handoff")["properties"]["speech"]["description"]
        self.assertIn("media_files", description)

    def test_the_environment_detail_is_an_open_object_on_both_sides(self):
        doc = copy.deepcopy(HANDOFF)
        doc["env_detail"] = {"qualquer": [1, {"x": None}]}
        self.assertEqual([], jsonschema.errors(doc, schemas.load("handoff")))
        self.assertNotIn("env_detail", schemas.load("handoff")["required"])

    def test_every_path_field_refuses_traversal_and_odd_shapes(self):
        bad = (
            "/abs.md", "../a.md", "a/../b.md", "a/..", "..", "./a.md", "a/./b.md", "a/.", "a//b.md",
            "a\\b.md", "a:b.md", "a\x00b.md", "a\x1fb.md", "a\nb.md", "a.md\n", ".hidden.md", "", "x" * 241,
        )  # fmt: skip
        good = ("a.md", "a/b.md", "a..b.mp4", "a/b.c.d", "a/.hidden/x", "a/...", "x" * 240)
        fields = (
            ("handoff", ("files", 0, "path"), "{}"),
            ("handoff_return", ("renders", 0, "path"), "{}"),
            ("handoff_return", ("artifacts", 0, "path"), "{}"),
        )
        for name, path, template in fields:
            for value in bad:
                with self.subTest(name=name, path=path, value=value):
                    self.assertNotEqual([], self._errors_at(name, path, template.format(value)))
            for value in good:
                with self.subTest(name=name, path=path, value=value, good=True):
                    self.assertEqual([], self._errors_at(name, path, template.format(value)))
        # a pasta é um artefato válido (`project/`): só ela aceita a barra no fim
        self.assertEqual([], self._errors_at("handoff_return", ("artifacts", 0, "path"), "project/"))
        for name, path in (("handoff", ("files", 0, "path")), ("handoff_return", ("renders", 0, "path"))):
            self.assertNotEqual([], self._errors_at(name, path, "project/x/"))
        # arquivo da ponte: sempre dentro da pasta do ambiente; arquivo de mídia: um nível sob assets/
        for value in ("hyperframes/a.md", "hyperframes/sub/a..b.md"):
            self.assertEqual([], self._errors_at("handoff", ("bridge", "files"), [value]), value)
        tails = ("../a.md", "a/../b.md", "a/..", "./a.md", "a/./b.md", "a/.", "a//b.md", "a\\b.md", "a:b.md")
        tails += ("a\x00b.md", "a\nb.md", "a.md\n", "a/", "", "x" * 201, "/a.md")
        for value in (
            "a.md",
            "Hyper/a.md",
            "hyperframes",
            "hyperframes/",
            "../hyperframes/a.md",
            *("hyperframes/" + t for t in tails),
        ):
            with self.subTest(value=value, bridge=True):
                self.assertNotEqual([], self._errors_at("handoff", ("bridge", "files"), [value]))
        for value in ("assets/a.mp4", "assets/a..b.mp4"):
            self.assertEqual([], self._errors_at("handoff", ("media_files", MEDIA_ID, "file"), value), value)
        for value in (
            "assets/",
            "assets/.x",
            "assets/a/b.mp4",
            "assets/a\\b",
            "assets/a:b",
            "assets/a\nb",
            "assets/a\n",
        ):
            self.assertNotEqual([], self._errors_at("handoff", ("media_files", MEDIA_ID, "file"), value), value)

    @staticmethod
    def _errors_at(name, path, value):
        doc = copy.deepcopy(EXAMPLES[name])
        node: Any = doc
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        return jsonschema.errors(doc, schemas.load(name))

    def test_one_grammar_serves_every_path_field(self):
        look = "(?!.*(?:^|/)\\.\\.?(?:/|(?![\\s\\S])))(?!.*//)"
        handoff, ret = schemas.load("handoff")["properties"], schemas.load("handoff_return")["properties"]
        patterns = (
            handoff["files"]["items"]["properties"]["path"]["pattern"],
            handoff["bridge"]["properties"]["files"]["items"]["pattern"],
            handoff["media_files"]["additionalProperties"]["properties"]["file"]["pattern"],
            ret["artifacts"]["items"]["properties"]["path"]["pattern"],
            ret["renders"]["items"]["properties"]["path"]["pattern"],
        )
        for pattern in patterns:
            with self.subTest(pattern=pattern):
                self.assertTrue(pattern.startswith(("^" + look, "^(?!project/")), pattern)
                self.assertIn(look, pattern)
                self.assertTrue(pattern.endswith("(?![\\s\\S])"), pattern)

    def test_shared_shapes_are_identical_in_every_schema(self):
        loaded = {name: schemas.load(name) for name in NAMES}
        for key in ("missing", "checks"):
            self.assertEqual(loaded["draft"]["properties"][key], loaded["handoff"]["properties"][key], key)
        self.assertEqual(loaded["draft"]["properties"]["plan"], loaded["handoff"]["properties"]["plan"])
        found: dict[str, set[str]] = {}

        def walk(node, key):
            if isinstance(node, dict):
                pattern = node.get("pattern")
                if pattern and key:
                    found.setdefault(key, set()).add(pattern)
                for sub_key, sub in (node.get("properties") or {}).items():
                    walk(sub, sub_key)
                walk(node.get("items"), key)
                walk(node.get("additionalProperties"), key)

        for schema in loaded.values():
            walk(schema, "")
        groups = {
            "sha256": ("sha256", "roteiro_sha256", "shelf_sha256", "sidecar_sha256", "handoff_sha256"),
            "timestamp": ("built_at", "created_at", "read_at", "at"),
        }
        for label, keys in groups.items():
            patterns = set().union(*(found.get(key, set()) for key in keys))
            self.assertEqual(1, len(patterns), (label, patterns))
        self.assertEqual({"^[0-9]{3,9}$"}, found["handoff"])

    def test_the_core_describes_no_environment_by_name_and_states_the_layout(self):
        for name in NAMES:
            text = json.dumps(schemas.load(name), ensure_ascii=False).lower()
            with self.subTest(name=name):
                self.assertNotIn("hyperframes", text.replace('"hyperframes"', ""))
        description = schemas.load("handoff")["description"]
        for part in ("handoffs/<ambiente>/<NNN>/", "handoff.json", "project/", "return/", "<ambiente>/"):
            self.assertIn(part, description)

    def test_review_never_carries_the_spoken_sentence(self):
        review = schemas.load("handoff")["properties"]["review"]
        self.assertIs(False, review["additionalProperties"])
        doc = copy.deepcopy(HANDOFF)
        doc["review"]["statement"] = "a frase dita"
        self.assertNotEqual([], jsonschema.errors(doc, schemas.load("handoff")))

    def test_the_return_names_the_handoff_it_read_by_hash(self):
        self.assertIn("handoff_sha256", schemas.load("handoff_return")["required"])
        doc = copy.deepcopy(RETURN)
        del doc["handoff_sha256"]
        self.assertNotEqual([], jsonschema.errors(doc, schemas.load("handoff_return")))

    def test_untrusted_text_is_bounded_in_characters_and_the_writer_bounds_bytes(self):
        # maxLength conta caracteres; o limite de 4 KiB em bytes é do writer (o schema diz isso).
        body = schemas.load("handoff_return")["properties"]["untrusted_text"]["properties"]["body"]
        self.assertIn("bytes", body["description"])
        doc = copy.deepcopy(RETURN)
        doc["untrusted_text"]["body"] = "é" * 3000
        self.assertEqual(6000, len(doc["untrusted_text"]["body"].encode("utf-8")))
        self.assertEqual([], jsonschema.errors(doc, schemas.load("handoff_return")))
        doc["untrusted_text"]["body"] = "é" * 4097
        self.assertNotEqual([], jsonschema.errors(doc, schemas.load("handoff_return")))

    def test_the_gaps_the_subset_cannot_express_are_documented_as_checked_in_code(self):
        # Este subconjunto de JSON Schema não tem $ref, propertyNames, if/then nem not: o que ele não diz o
        # schema diz em texto, e quem grava confere em código.
        handoff = schemas.load("handoff")
        self.assertIn("canvas", handoff["properties"]["timeline"]["description"])
        self.assertIn("null", handoff["properties"]["timeline"]["properties"]["fps"]["description"])
        self.assertIn(
            "pendente",
            handoff["properties"]["media_files"]["additionalProperties"]["properties"]["licence_ref"]["description"],
        )
        self.assertIn("media_id", handoff["properties"]["media_files"]["description"])
        self.assertIn("renders", schemas.load("handoff_return")["properties"]["state"]["description"])

    def test_the_schemas_stay_inside_the_sdk_subset(self):
        for name in NAMES:
            with self.subTest(name=name):
                check_schema(schemas.load(name))


if __name__ == "__main__":
    unittest.main()
