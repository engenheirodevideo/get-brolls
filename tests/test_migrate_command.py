"""`migrate`: adota o layout 1 num projeto antigo só acrescentando `project.json`."""

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

# Infraestrutura de auditoria e trava: `audited()` as grava em qualquer comando que escreve.
INFRA = {"diagnostics.jsonl", ".command.lock", "getbrolls.log"}
OLD_ID = str(uuid.uuid4())
ROTEIRO = "---\ntype: roteiro\ngenero: reels\ntema: Teste\ncliente: acme-corp\n---\n\n## Cena 1\n"


def snapshot(root):
    return {
        str(path.relative_to(root)): (path.read_bytes() if path.is_file() else None)
        for path in sorted(root.rglob("*"))
        if path.name not in INFRA
    }


class MigrateCommandTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-migrate-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.project = self.base / "video-01"
        (self.project / "brolls" / "clips").mkdir(parents=True)
        (self.project / "brolls" / "clips" / "a.mp4").write_bytes(b"clip")
        (self.project / "brolls" / "clips" / "b.mp4").write_bytes(b"clip2")
        self.write_manifest({"schema_version": 1, "project_id": OLD_ID, "items": []})

    def write_manifest(self, data):
        (self.project / "brolls" / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    def test_plan_writes_nothing_and_takes_no_lock(self):
        before = snapshot(self.project)
        result = run_cli("migrate", "--action", "plan", project=self.project)
        self.assertEqual(before, snapshot(self.project))
        self.assertFalse((self.project / "brolls" / ".command.lock").exists())
        self.assertEqual("plan", result["action"])
        self.assertFalse(result["changed"])
        self.assertEqual("project.json", result["would_write"])
        self.assertEqual(2, result["legacy_clips"])
        self.assertEqual(OLD_ID, result["doc"]["id"])
        self.assertEqual([], errors(result["doc"], schemas.load("project")))

    def test_apply_adds_exactly_project_json_with_the_manifest_id(self):
        before = snapshot(self.project)
        result = run_cli("migrate", "--action", "apply", project=self.project)
        after = snapshot(self.project)
        self.assertEqual({"project.json"}, set(after) - set(before))
        self.assertEqual({key: value for key, value in after.items() if key != "project.json"}, before)
        self.assertTrue(result["changed"])
        self.assertEqual(OLD_ID, result["id"])
        self.assertEqual("project.json", result["wrote"])
        found = layout.info(self.project)
        self.assertEqual((1, "project.json"), (found.version, found.source))
        assert found.doc is not None
        self.assertEqual(OLD_ID, found.doc["id"])

    def test_old_clips_and_status_still_work_and_status_shows_layout(self):
        before = run_cli("status", project=self.project)["layout"]
        self.assertEqual({"version": 0, "source": "inferred", "problem": None}, before)
        run_cli("migrate", "--action", "apply", project=self.project)
        self.assertEqual(
            {"version": 1, "source": "project.json", "problem": None},
            run_cli("status", project=self.project)["layout"],
        )
        self.assertEqual(b"clip", (self.project / "brolls" / "clips" / "a.mp4").read_bytes())
        run_cli("verify", project=self.project)

    def test_status_reports_a_broken_project_json(self):
        (self.project / "project.json").write_text("{", encoding="utf-8")
        found = run_cli("status", project=self.project)["layout"]
        self.assertEqual(0, found["version"])
        self.assertIn("JSON inválido", found["problem"])

    def test_invalid_manifest_id_gets_a_new_uuid(self):
        self.write_manifest({"schema_version": 1, "project_id": "not-a-uuid", "items": []})
        plan = run_cli("migrate", "--action", "plan", project=self.project)
        doc = run_cli("migrate", "--action", "apply", project=self.project)
        self.assertNotEqual("not-a-uuid", doc["id"])
        self.assertEqual(doc["id"], str(uuid.UUID(doc["id"])))
        self.assertEqual(plan["doc"]["id"], doc["id"])

    def test_plan_shows_the_id_that_apply_writes(self):
        for raw in (OLD_ID, OLD_ID.upper(), "proj-antigo", None):
            with self.subTest(raw=raw):
                (self.project / "project.json").unlink(missing_ok=True)
                self.write_manifest({"schema_version": 1, "items": [], **({"project_id": raw} if raw else {})})
                plan = run_cli("migrate", "--action", "plan", project=self.project)
                applied = run_cli("migrate", "--action", "apply", project=self.project)
                if raw is None:
                    self.assertIsNone(plan["doc"]["id"])
                    self.assertTrue(any("gerado" in note for note in plan["notes"]), plan["notes"])
                else:
                    self.assertEqual(plan["doc"]["id"], applied["id"])
                self.assertEqual(applied["id"], layout.project_id(self.project))
        upper = {"schema_version": 1, "project_id": OLD_ID.upper(), "items": []}
        (self.project / "project.json").unlink()
        self.write_manifest(upper)
        plan = run_cli("migrate", "--action", "plan", project=self.project)
        self.assertEqual(OLD_ID, plan["doc"]["id"])
        self.assertTrue(any("minúsculas" in note for note in plan["notes"]), plan["notes"])

    def test_nonexistent_project_is_refused_and_nothing_is_created(self):
        ghost = self.base / "nao-existe"
        for action in ("plan", "apply"):
            with self.subTest(action=action):
                refused = run_cli("migrate", "--action", action, project=ghost, expect=1)
                self.assertIn("não é uma pasta", refused["error"])
                self.assertFalse(ghost.exists())

    def test_plan_warns_about_a_user_broll_folder(self):
        (self.project / "broll").mkdir()
        (self.project / "broll" / "meu-corte.mp4").write_bytes(b"da pessoa")
        plan = run_cli("migrate", "--action", "plan", project=self.project)
        self.assertTrue(any("broll/" in note and "clipes finais" in note for note in plan["notes"]), plan["notes"])
        applied = run_cli("migrate", "--action", "apply", project=self.project)
        self.assertTrue(any(w["code"] == "MIGRATE_BROLL_EXISTS" for w in applied["warnings"]), applied)

    def test_client_comes_from_the_roteiro_and_flag_wins(self):
        (self.project / "ROTEIRO.md").write_text(ROTEIRO, encoding="utf-8")
        plan = run_cli("migrate", "--action", "plan", project=self.project)
        self.assertEqual("acme-corp", plan["doc"]["client"])
        plan = run_cli("migrate", "--action", "plan", "--client", "other", project=self.project)
        self.assertEqual("other", plan["doc"]["client"])

    def test_unregistered_client_is_a_warning_not_an_error(self):
        plan = run_cli("migrate", "--action", "plan", "--client", "ghost-co", project=self.project)
        self.assertTrue(any("ghost-co" in note and "registr" in note for note in plan["notes"]))

    def test_bad_client_slug_is_a_usage_error(self):
        refused = run_cli("migrate", "--action", "plan", "--client", "ACME", project=self.project, expect=2)
        self.assertEqual("USAGE_ERROR", refused["error_code"])

    def test_project_without_brolls_is_handled(self):
        bare = self.base / "bare"
        bare.mkdir()
        plan = run_cli("migrate", "--action", "plan", project=bare)
        self.assertEqual(0, plan["legacy_clips"])
        self.assertTrue(plan["notes"])
        self.assertEqual([], list(bare.iterdir()))
        run_cli("migrate", "--action", "apply", project=bare)
        self.assertEqual(1, layout.info(bare).version)

    def test_second_apply_is_a_no_op_and_changes_nothing(self):
        first = run_cli("migrate", "--action", "apply", project=self.project)
        before = snapshot(self.project)
        again = run_cli("migrate", "--action", "apply", project=self.project)
        self.assertEqual((False, first["id"]), (again["changed"], again["id"]))
        self.assertIn("nada a migrar", again["summary"]["line"])
        self.assertEqual(before, snapshot(self.project))
        plan = run_cli("migrate", "--action", "plan", project=self.project)
        self.assertFalse(plan["changed"])
        self.assertIn("nada a migrar", plan["summary"]["line"])

    def test_broken_project_json_is_refused_and_preserved(self):
        (self.project / "project.json").write_text("{", encoding="utf-8")
        refused = run_cli("migrate", "--action", "apply", project=self.project, expect=1)
        self.assertIn("project.json", refused["error"])
        self.assertEqual("{", (self.project / "project.json").read_text(encoding="utf-8"))

    def test_pending_journal_is_refused(self):
        (self.project / "brolls" / ".pending-transaction.json").write_text("{}", encoding="utf-8")
        refused = run_cli("migrate", "--action", "apply", project=self.project, expect=1)
        self.assertIn("interrompida", refused["error"])
        self.assertFalse((self.project / "project.json").exists())
        self.assertTrue((self.project / "brolls" / ".pending-transaction.json").exists())

    def test_init_refusal_points_to_a_working_command(self):
        refused = run_cli("init", project=self.project, expect=1)
        self.assertIn("migrate", refused["error"])
        run_cli("migrate", "--action", "apply", project=self.project)

    def test_capabilities_marks_plan_as_read_only_by_action(self):
        commands = {row["name"]: row for row in run_cli("capabilities")["commands"]}
        self.assertEqual("by_action", commands["migrate"]["read_only"])
        self.assertEqual(["plan"], commands["migrate"]["read_only_actions"])


if __name__ == "__main__":
    unittest.main()
