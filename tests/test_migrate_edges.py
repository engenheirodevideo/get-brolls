"""migrate: leituras que falham no meio do plano, notas de cliente e aviso no apply."""

import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
from _cli import run_cli
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import clients, layout, migrate, roteiro_frontmatter


class MigrateEdgeTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-migrate-edges-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.project = self.base / "video-01"
        (self.project / "brolls").mkdir(parents=True)
        self.user_folder = self.project / layout.CLIP_FOLDER
        self.user_folder.mkdir(parents=True, exist_ok=True)

    def write_manifest(self, text):
        (self.project / "brolls" / "manifest.json").write_text(text, encoding="utf-8")

    def test_a_roteiro_that_vanishes_between_the_check_and_the_read_gives_no_client(self):
        with patch.object(roteiro_frontmatter, "is_roteiro", return_value=True):
            self.assertIsNone(migrate._roteiro_client(self.project))

    def test_a_roteiro_that_is_not_utf8_gives_no_client(self):
        (self.project / "ROTEIRO.md").write_bytes(b"---\ncliente: \xff\xfe\n---\n")
        with patch.object(roteiro_frontmatter, "is_roteiro", return_value=True):
            self.assertIsNone(migrate._roteiro_client(self.project))

    def test_an_unreadable_manifest_means_the_project_id_will_be_generated(self):
        self.write_manifest("{ nao e json")
        known, notes, source = migrate._manifest_id(self.project)
        self.assertIsNone(known)
        self.assertEqual("generated", source)
        self.assertIn("será gerado", notes[0])

    def test_an_uppercase_manifest_id_is_normalised_and_said_so(self):
        raw = str(uuid.uuid4()).upper()
        self.write_manifest(json.dumps({"schema_version": 1, "project_id": raw, "items": []}))
        known, notes, source = migrate._manifest_id(self.project)
        self.assertEqual(raw.lower(), known)
        self.assertEqual("normalized", source)
        self.assertIn("minúsculas", notes[0])

    def test_a_clip_folder_that_cannot_be_listed_adds_no_note(self):
        self.user_folder.joinpath("a.mp4").write_bytes(b"clip")
        with patch.object(Path, "iterdir", side_effect=PermissionError("sem acesso")):
            self.assertEqual([], migrate._broll_notes(self.project))

    def test_a_clip_folder_with_files_adds_the_note_and_an_empty_one_does_not(self):
        self.assertEqual([], migrate._broll_notes(self.project))
        self.user_folder.joinpath("a.mp4").write_bytes(b"clip")
        self.assertEqual(1, len(migrate._broll_notes(self.project)))

    def test_no_client_gives_no_note(self):
        self.assertEqual([], migrate._client_notes(None))

    def test_a_registered_client_gives_no_note(self):
        with patch.object(clients, "client_root", return_value=self.base):
            self.assertEqual([], migrate._client_notes("acme-corp"))

    def test_a_client_registry_that_cannot_be_checked_is_reported_in_the_note(self):
        with patch.object(clients, "client_root", side_effect=ValueError("clients.json ilegível")):
            notes = migrate._client_notes("acme-corp")
        self.assertEqual(1, len(notes))
        self.assertIn("acme-corp", notes[0])
        self.assertIn("clients.json ilegível", notes[0])

    def test_apply_with_an_unregistered_client_warns_and_still_writes_the_client(self):
        result = run_cli("migrate", "--action", "apply", "--client", "ghost-co", project=self.project)
        self.assertEqual("ghost-co", result["doc"]["client"])
        self.assertIn("CLIENT_NOT_REGISTERED", [w["code"] for w in result["warnings"]])
        written = json.loads((self.project / "project.json").read_text(encoding="utf-8"))
        self.assertEqual("ghost-co", written["client"])


if __name__ == "__main__":
    unittest.main()
