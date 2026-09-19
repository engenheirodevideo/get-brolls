"""Characterization tests for the audit trail's remaining edges: delivery path
collisions, project-lock contention between write commands, malformed
manifest.json (schema version and item shape), and the JSON error envelope
that `runtime.audited()` produces per failure class.

These go through the real CLI (`tests/_cli.py::run_cli`, a subprocess over
`scripts/gb.py`) wherever feasible, so they also pin argument wiring, the
project lock and the format gate around `commands.execute()` — not just the
underlying helper functions. Where a scenario is already exercised this way
elsewhere in the suite, this file says so instead of duplicating it.
"""

import json
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment
from getbrolls.runtime import project_lock


def fetched_candidate(source_id, title, shot=None, clip="clips/x.mp4", sheet="previews/x.jpg"):
    """A candidate already through search/approve/permit/fetch, built directly
    (same shape as tests/test_delivery.py::fetched) so `deliver` has real,
    verified media to place without needing network or ffmpeg.
    """
    c = candidate("local", source_id, title, source_url="https://example.org/" + source_id)
    set_segment(c, 0, 2)
    c["creator"]["name"] = "Autora Exemplo"
    c["preview"]["contact_sheet_path"] = sheet
    c["approval"] = {
        "status": "approved",
        "by": "Ana",
        "at": now(),
        "revision": 1,
        "channel": "chat",
        "statement": "aprovo",
    }
    c["rights"]["status"] = "permitted"
    c["rights"]["evidence"] = ["Condições conferidas na página da fonte"]
    c["output"] = {"path": clip, "sha256": "a" * 64, "verified": True}
    c["state"] = "verified"
    if shot:
        c["shot"] = shot
        c["id"] += ":shot:" + shot
    return c


def deliverable_project(tmp):
    """A project with one fetched candidate and its media already on disk,
    ready for `deliver`.
    """
    ledger = Ledger(tmp)
    stored = ledger.add(fetched_candidate("a", "Palco", shot="abertura"))
    ledger.save_many("fixture", [stored])
    for rel in (stored["output"]["path"], stored["preview"].get("contact_sheet_path")):
        if rel:
            path = ledger.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"conteudo de " + rel.encode())
    return ledger


class DeliveryDestinationConflictTests(unittest.TestCase):
    """entrega/<beat>/... already holds a different file someone else put there.

    tests/test_delivery.py already pins this at the `delivery.build_delivery()`
    unit level (`test_a_file_the_person_edited_is_never_overwritten`,
    `test_one_edited_file_does_not_abort_the_rest_of_the_batch`). What is new
    here is driving the same collision through the real CLI subprocess (exit
    code, JSON envelope) and pinning that `--dry-run` reports the same plan
    without ever touching the foreign file, even when the collision already
    exists on disk.
    """

    def _plant_conflict(self, tmp):
        """Ask `deliver --dry-run` where a file would land, then put a foreign
        file there before any real delivery ever ran.
        """
        plan = run_cli("deliver", "--dry-run", project=tmp)
        dest = Path(tmp) / plan["items"][0]["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"corte que a pessoa mexeu na mao")
        return dest

    def test_deliver_refuses_to_overwrite_a_foreign_file_at_the_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            deliverable_project(tmp)
            dest = self._plant_conflict(tmp)

            error = run_cli("deliver", project=tmp, expect=2)

            self.assertEqual("INVALID_DATA", error["error_code"])
            self.assertEqual("ValueError", error["type"])
            self.assertIn(dest.name, error["error"])
            # The person's file survives exactly as they left it.
            self.assertEqual(b"corte que a pessoa mexeu na mao", dest.read_bytes())

    def test_dry_run_reports_the_same_plan_without_touching_the_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = deliverable_project(tmp)
            manifest_before = (ledger.root / "manifest.json").read_bytes()
            dest = self._plant_conflict(tmp)

            plan = run_cli("deliver", "--dry-run", project=tmp)

            self.assertTrue(plan["dry_run"])
            self.assertEqual(
                [{"id": "local:a:shot:abertura", "beat": "abertura", "method": "planned"}],
                [{k: v for k, v in item.items() if k != "path"} for item in plan["items"]],
            )
            self.assertEqual(b"corte que a pessoa mexeu na mao", dest.read_bytes())
            self.assertFalse((Path(tmp) / "entrega" / "README.md").exists())
            self.assertEqual(manifest_before, (ledger.root / "manifest.json").read_bytes())


class ConcurrentWriteCommandTests(unittest.TestCase):
    """Two write commands competing for the same project's lock
    (`runtime.project_lock`/`runtime._acquire_lock`, a non-blocking flock on
    `brolls/.command.lock`).

    tests/test_queue_integrity.py::QueueActionReadOnlyTests already proves,
    in-process via `audited()` directly, that a read-only action succeeds
    under a held lock and that a write action conflicts. tests/test_status.py
    ::test_status_answers_while_another_command_holds_the_lock proves the
    read-only side through the real CLI. What's new here: holding the lock
    from this test process exactly like the production code does, then
    driving a second *write* command through a real CLI subprocess (not
    `audited()` called directly) to pin its exit code and JSON error class,
    alongside a read-only command from the same subprocess path so both
    sides of the same lock episode are proven together.
    """

    def test_second_write_command_fails_fast_while_a_read_only_command_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            Ledger(tmp)  # creates brolls/ so status has a project to read
            with project_lock(tmp):
                status = run_cli("status", project=tmp)
                self.assertIn("counts", status)

                error = run_cli(
                    "queue",
                    "--action",
                    "add",
                    "--provider",
                    "instagram",
                    "--url",
                    "https://www.instagram.com/reel/ABC123xyz/",
                    project=tmp,
                    expect=2,
                )
            self.assertEqual("INVALID_DATA", error["error_code"])
            self.assertEqual("ValueError", error["type"])
            # Nothing got queued: the write never reached queue.json.
            queue_file = Path(tmp) / "work" / "queue.json"
            self.assertFalse(queue_file.exists())

            # With the lock released, the same write now goes through.
            ok = run_cli(
                "queue",
                "--action",
                "add",
                "--provider",
                "instagram",
                "--url",
                "https://www.instagram.com/reel/ABC123xyz/",
                project=tmp,
            )
            self.assertEqual("add", ok["action"])


class ManifestSchemaVersionTests(unittest.TestCase):
    """manifest.json with a top-level `schema_version` other than 1, and with
    a structurally invalid item, both refused by `ledger.validate_manifest()`
    before `status` ever changes the file.

    Item-level `schema_version` is written by `models.candidate()` but is
    NOT checked by `validate_manifest()` today (it only validates provider/
    source_id/title/state/segment/output/rights shape) — so there is no
    item-level schema_version behavior to pin here without inventing one.
    """

    def _manifest_path(self, tmp):
        root = Path(tmp) / "brolls"
        root.mkdir(parents=True, exist_ok=True)
        return root / "manifest.json"

    def test_an_unknown_top_level_schema_version_is_refused_and_the_file_is_untouched(self):
        for schema_version in (0, 2, 99):
            with self.subTest(schema_version=schema_version), tempfile.TemporaryDirectory() as tmp:
                path = self._manifest_path(tmp)
                raw = json.dumps({"schema_version": schema_version, "items": []})
                path.write_text(raw, encoding="utf-8")

                error = run_cli("status", project=tmp, expect=2)

                self.assertEqual("INVALID_DATA", error["error_code"])
                self.assertEqual("ValueError", error["type"])
                self.assertEqual(raw, path.read_text(encoding="utf-8"))

    def test_a_structurally_invalid_item_is_refused_and_the_file_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._manifest_path(tmp)
            # A real item shape, missing the required "media" mapping.
            item = candidate("local", "a", "Sem media")
            del item["media"]
            raw = json.dumps({"schema_version": 1, "items": [item]})
            path.write_text(raw, encoding="utf-8")

            error = run_cli("status", project=tmp, expect=2)

            self.assertEqual("INVALID_DATA", error["error_code"])
            self.assertEqual("ValueError", error["type"])
            self.assertEqual(raw, path.read_text(encoding="utf-8"))


class FailureEnvelopeClassesTests(unittest.TestCase):
    """One real example per failure class that `runtime.audited()` distinguishes
    in its except clauses, produced through the real CLI where feasible offline.
    Pins `error_code`/`type` (the exception class name) and the exit code —
    not the PT-BR message text.

    Not reproduced here (already covered elsewhere, or not feasible offline):
    - KeyError/TypeError/AttributeError -> INTERNAL_ERROR: covered directly
      against `runtime.audited()` by tests/test_runtime_diagnostics.py
      (`InternalErrorClassificationTests`), and the CLI-entrypoint envelope/
      exit-3 wrapping by the same file's `CliEntrypointErrorEnvelopeTests`.
    - `ProviderError` -> INVALID_DATA with a PROVIDER_ERROR warning: covered
      by tests/test_error_reporting.py (around `test_provider_error_...`,
      line ~414-435); reproducing it needs a provider network call to mock,
      which duplicates that file's own setup.
    - KeyboardInterrupt -> INTERRUPTED: not reproducible through a real CLI
      subprocess offline without sending a live signal.
    """

    def test_invalid_argument_value_is_invalid_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            error = run_cli("resolve", "--file", str(Path(tmp) / "missing.mp4"), project=tmp, expect=2)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertEqual("ValueError", error["type"])

    def test_missing_project_folder_is_invalid_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            error = run_cli("status", project=str(Path(tmp) / "never-created"), expect=2)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertEqual("ValueError", error["type"])

    def test_unknown_candidate_id_is_invalid_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            error = run_cli("permit", "--candidate", "does-not-exist", "--evidence", "x", project=tmp, expect=2)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertEqual("ValueError", error["type"])

    def test_corrupted_manifest_is_invalid_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "brolls"
            root.mkdir(parents=True)
            (root / "manifest.json").write_text("{not json", encoding="utf-8")
            error = run_cli("status", project=tmp, expect=2)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertEqual("ValueError", error["type"])

    def test_lock_held_is_invalid_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            Ledger(tmp)
            with project_lock(tmp):
                error = run_cli("permit", "--candidate", "x", "--evidence", "y", project=tmp, expect=2)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertEqual("ValueError", error["type"])

    def test_io_error_from_a_project_path_that_cannot_hold_a_folder(self):
        """A regular file where the project directory should be: the project
        lock's own `mkdir` raises a real OSError before `execute()` runs.
        """
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "im-a-file"
            blocked.write_text("not a directory", encoding="utf-8")
            error = run_cli("init-rules", project=str(blocked), expect=2)
        self.assertEqual("IO_ERROR", error["error_code"])
        self.assertIn(error["type"], ("NotADirectoryError", "FileExistsError"))


if __name__ == "__main__":
    unittest.main()
