"""`--project` que o comando não opera (`doctor`, `profile`, `x --list`) não passa pela leitura do layout."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import


class ProjectNotOperatedTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-notop-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.file = self.base / "video.mp4"
        self.file.write_text("x", encoding="utf-8")
        self.old = self.base / "velho"
        self.old.mkdir()
        (self.old / "project.json").write_text(json.dumps({"template": "x"}), encoding="utf-8")

    def test_doctor_ignores_a_project_that_is_a_file_or_has_an_old_project_json(self):
        for project in (self.file, self.old):
            with self.subTest(project=project.name):
                done = run_cli("doctor", project=project)
                self.assertNotIn("OLD_SHAPE", [w["code"] for w in done.get("warnings", [])])

    def test_profile_and_x_list_accept_a_file_as_the_search_start(self):
        run_cli("profile", "--action", "show", project=self.file)
        run_cli("x", "--list", project=self.file)

    def test_a_command_that_operates_on_the_project_still_refuses_a_file(self):
        refused = run_cli("assets", "--action", "list", project=self.file, expect=1)
        self.assertIn("não é uma pasta", refused["error"])
