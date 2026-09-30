"""`init`: cria um projeto de layout 1 (`project.json`, `aroll/`, `assets/*`, `broll/`, `analysis/`)."""

import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import layout
from getbrolls.sdk import schemas
from getbrolls.sdk.jsonschema import errors

FOLDERS = [
    "aroll",
    "assets/marca",
    "assets/lettering",
    "assets/sfx",
    "assets/musica",
    "assets/imagem",
    "assets/composicoes",
    "assets/outros",
    "broll",
    "analysis",
]


class InitCommandTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-init-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.project = self.base / "video-01"

    def test_fresh_directory_gets_the_tree_and_a_valid_project_json(self):
        result = run_cli("init", project=self.project)
        self.assertEqual(1, result["layout"])
        self.assertEqual(str(self.project), result["project"])
        self.assertEqual(FOLDERS, result["folders"])
        self.assertIn("init", result["summary"]["line"].lower())
        for folder in FOLDERS:
            self.assertTrue((self.project / folder).is_dir(), folder)
        doc = json.loads((self.project / "project.json").read_text(encoding="utf-8"))
        self.assertEqual([], errors(doc, schemas.load("project")))
        self.assertEqual(result["id"], doc["id"])
        self.assertEqual(str(uuid.UUID(doc["id"])), doc["id"])
        self.assertEqual((1, "project.json"), (layout.info(self.project).version, layout.info(self.project).source))
        for key in ("client", "template", "canvas", "fps"):
            self.assertIsNone(doc[key])

    def test_no_manifest_candidates_or_clips_are_created(self):
        run_cli("init", project=self.project)
        brolls = self.project / "brolls"
        for name in ("manifest.json", "candidates", "clips", "previews", "events.jsonl"):
            self.assertFalse((brolls / name).exists(), name)
        # A trava e o log do comando são esperados; nada mais sob brolls/.
        if brolls.exists():
            allowed = {".command.lock", "getbrolls.log", "diagnostics.jsonl"}
            self.assertLessEqual({p.name for p in brolls.iterdir()}, allowed)

    def test_client_canvas_and_fps_are_recorded(self):
        run_cli("init", "--client", "acme", "--canvas", "1080x1920", "--fps", "30000/1001", project=self.project)
        doc = layout.load_project(self.project)
        assert doc is not None
        self.assertEqual("acme", doc["client"])
        self.assertEqual({"width": 1080, "height": 1920}, doc["canvas"])
        self.assertEqual({"num": 30000, "den": 1001}, doc["fps"])

    def test_second_init_fails_with_invalid_data(self):
        run_cli("init", project=self.project)
        before = (self.project / "project.json").read_bytes()
        refused = run_cli("init", project=self.project, expect=1)
        self.assertEqual("INVALID_DATA", refused["error_code"])
        self.assertIn("project.json", refused["error"])
        self.assertEqual(before, (self.project / "project.json").read_bytes())

    def test_existing_manifest_is_refused_with_the_migrate_hint(self):
        (self.project / "brolls").mkdir(parents=True)
        (self.project / "brolls" / "manifest.json").write_text('{"schema_version": 1, "items": []}', encoding="utf-8")
        refused = run_cli("init", project=self.project, expect=1)
        self.assertEqual("INVALID_DATA", refused["error_code"])
        self.assertIn("migrate", refused["error"])
        self.assertFalse((self.project / "project.json").exists())
        self.assertFalse((self.project / "broll").exists())

    def test_bad_fps_canvas_or_client_is_a_usage_error(self):
        for flag, value in (
            ("--fps", "0"),
            ("--fps", "30/0"),
            ("--fps", "abc"),
            ("--canvas", "0x1920"),
            ("--canvas", "1080"),
            ("--client", "ACME"),
        ):
            with self.subTest(flag=flag, value=value):
                refused = run_cli("init", flag, value, project=self.project, expect=2)
                self.assertEqual("USAGE_ERROR", refused["error_code"])
                self.assertFalse((self.project / "project.json").exists())

    def test_project_path_that_is_a_file_is_refused(self):
        self.project.write_text("x", encoding="utf-8")
        run_cli("init", project=self.project, expect=1)


if __name__ == "__main__":
    unittest.main()
