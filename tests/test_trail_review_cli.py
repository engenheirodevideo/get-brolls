"""Characterization of `import-review`/`approve`/`reject`/`permit` through the real CLI.

Existing import-review tests (`test_approve_chat.py`, `test_security_regressions.py`)
call `getbrolls.review.import_review` directly, bypassing `execute()`'s dispatch, the
project lock and the format gate (`sync_formats`). These tests instead go through the
real CLI path (`gb.py import-review`/`approve`/`reject`/`permit` via subprocess), so
they pin the exit code, the JSON envelope (stdout on success, stderr JSON on failure)
and the resulting manifest.json exactly as `execute()`/`audited()`/`entrypoint()`
produce them today, ahead of a refactor of `commands.py` `execute()`.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import CLI, ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.ledger import Ledger
from getbrolls.models import candidate, set_segment
from getbrolls.rendering import render


def _review_payload(page):
    match = re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
    assert match, "review.html sem o payload embutido"
    return match.group(1)


def project_with(folder, *names):
    """Um candidato por nome, com intervalo definido e sem prévia."""
    ledger = Ledger(folder)
    for name in names:
        c = candidate("local", name, "Synthetic")
        set_segment(c, 0, 1)
        ledger.add(c)
    ledger.save("fixture")
    return ledger


def exported(ledger, state="approved"):
    """Payload que a página do Storyboard exportaria, com todo item marcado `state`."""
    page = Path(render(ledger)).read_text(encoding="utf-8")
    payload = json.loads(_review_payload(page))
    for item in payload["items"]:
        item["state"] = state
    return payload


def save_review(ledger, payload, name="20260917-120000.json"):
    reviews = ledger.root / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    path = reviews / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class ImportReviewHappyPathTests(unittest.TestCase):
    def test_import_review_with_file_applies_the_decision_through_the_real_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            path = save_review(ledger, exported(ledger))
            result = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp)
            self.assertEqual(1, result["imported"])
            self.assertEqual([], result["skipped"])
            self.assertIn("Importei 1 decisão", result["summary"]["line"])
            fresh = Ledger(tmp).get("local:one")
            self.assertEqual("approved", fresh["approval"]["status"])
            self.assertEqual("storyboard", fresh["approval"]["channel"])

    def test_import_review_without_file_picks_the_single_newest_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            path = save_review(ledger, exported(ledger), "20260917-120000.json")
            result = run_cli("import-review", "--by", "Ana", project=tmp)
            self.assertEqual(1, result["imported"])
            self.assertEqual(str(path), result["file"])
            self.assertEqual("approved", Ledger(tmp).get("local:one")["approval"]["status"])


class ImportReviewRejectionTests(unittest.TestCase):
    """One failure mode per item, run through the CLI to pin exit code/envelope/manifest."""

    def test_unknown_candidate_id_fails_the_whole_file_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one", "two")
            payload = exported(ledger)
            payload["items"].append(
                {
                    "id": "local:ghost",
                    "signature": "0" * 64,
                    "reviewEpoch": "0" * 64,
                    "state": "approved",
                    "comment": "",
                    "suggestion": "",
                }
            )
            path = save_review(ledger, payload)
            before = ledger.path.read_bytes()
            error = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp, expect=2)
            self.assertEqual("Candidato não encontrado neste projeto.", error["error"])
            self.assertEqual("INVALID_DATA", error["error_code"])
            self.assertEqual(before, ledger.path.read_bytes())

    def test_stale_signature_skips_only_that_item_and_applies_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one", "two")
            payload = exported(ledger)
            payload["items"][0]["signature"] = "0" * 64
            path = save_review(ledger, payload)
            result = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp)
            self.assertEqual(1, result["imported"])
            self.assertEqual(
                [{"id": "local:one", "reason": "signature_mismatch"}],
                [{"id": s["id"], "reason": s["reason"]} for s in result["skipped"]],
            )
            fresh = {c["id"]: c for c in Ledger(tmp).data["items"]}
            self.assertEqual("pending", fresh["local:one"]["approval"]["status"])
            self.assertEqual("approved", fresh["local:two"]["approval"]["status"])

    def test_stale_review_epoch_skips_only_that_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one", "two")
            payload = exported(ledger)
            payload["items"][0]["reviewEpoch"] = "0" * 64
            path = save_review(ledger, payload)
            result = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp)
            self.assertEqual(1, result["imported"])
            self.assertEqual("stale_epoch", result["skipped"][0]["reason"])
            self.assertEqual("pending", Ledger(tmp).get("local:one")["approval"]["status"])

    def test_invalid_state_value_skips_only_that_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one", "two")
            payload = exported(ledger)
            payload["items"][0]["state"] = "bogus"
            path = save_review(ledger, payload)
            result = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp)
            self.assertEqual(1, result["imported"])
            self.assertEqual("invalid_item", result["skipped"][0]["reason"])
            self.assertEqual("pending", Ledger(tmp).get("local:one")["approval"]["status"])

    def test_oversized_file_is_refused_before_reading_any_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            payload = exported(ledger)
            # `import_review` (review.py) refuses anything over 2_000_000 bytes on disk,
            # before it even parses JSON.
            payload["items"][0]["comment"] = "x" * 2_000_100
            path = save_review(ledger, payload)
            self.assertGreater(path.stat().st_size, 2_000_000)
            before = ledger.path.read_bytes()
            error = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp, expect=2)
            self.assertIn("2 MB", error["error"])
            self.assertEqual(before, ledger.path.read_bytes())

    def test_malformed_json_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            path = save_review(ledger, exported(ledger))
            path.write_text("{not valid json", encoding="utf-8")
            before = ledger.path.read_bytes()
            error = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp, expect=2)
            self.assertEqual("INVALID_DATA", error["error_code"])
            self.assertEqual(before, ledger.path.read_bytes())

    def test_without_by_is_refused_by_argument_parsing_before_any_write(self):
        # `--by` is `required=True` in argparse: this fails before `audited()`/`execute()`
        # ever run, so the CLI prints plain usage text on stderr, not a JSON envelope.
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            path = save_review(ledger, exported(ledger))
            before = ledger.path.read_bytes()
            done = subprocess.run(
                [sys.executable, str(CLI), "import-review", "--file", str(path), "--project", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(2, done.returncode)
            self.assertIn("--by", done.stderr)
            self.assertEqual(before, ledger.path.read_bytes())


class ApproveAllMixedBatchTests(unittest.TestCase):
    """Four candidates in four different states, one `approve --all` through the CLI."""

    def test_approve_all_reports_the_right_outcome_per_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)

            previewed_pending = candidate("local", "a", "Previewed and pending")
            set_segment(previewed_pending, 0, 1)
            previewed_pending["preview"]["gif_path"] = "previews/a.gif"
            ledger.add(previewed_pending)

            never_previewed = candidate("local", "b", "Never previewed")
            set_segment(never_previewed, 0, 1)
            ledger.add(never_previewed)

            rejected = candidate("local", "c", "Rejected")
            set_segment(rejected, 0, 1)
            rejected["preview"]["gif_path"] = "previews/c.gif"
            ledger.add(rejected)

            already_approved = candidate("local", "d", "Already approved")
            set_segment(already_approved, 0, 1)
            already_approved["preview"]["gif_path"] = "previews/d.gif"
            ledger.add(already_approved)
            ledger.save("fixture")

            run_cli("reject", "--candidate", "local:c", "--project", tmp)
            run_cli(
                "approve",
                "--candidate",
                "local:d",
                "--by",
                "Ana",
                "--statement",
                "Aprovo d de antemão.",
                "--project",
                tmp,
            )

            result = run_cli(
                "approve",
                "--all",
                "--by",
                "Ana",
                "--statement",
                "Aprovo tudo que apareceu.",
                "--project",
                tmp,
            )
            self.assertEqual(["local:a"], result["approved"])
            skipped = {s["id"]: s["reason"] for s in result["skipped"]}
            self.assertEqual(
                {
                    "local:b": "sem prévia gerada; rode preview antes",
                    "local:c": "rejeitado por decisão humana",
                    "local:d": "já tem aprovação válida para este intervalo",
                },
                skipped,
            )
            fresh = {c["id"]: c for c in Ledger(tmp).data["items"]}
            self.assertEqual("approved", fresh["local:a"]["approval"]["status"])
            self.assertEqual("pending", fresh["local:b"]["approval"]["status"])
            self.assertEqual("rejected", fresh["local:c"]["approval"]["status"])
            self.assertEqual("approved", fresh["local:d"]["approval"]["status"])


class RejectThenApproveTests(unittest.TestCase):
    """`reject --candidate X --reason ...` followed by `approve` of the same id by flags."""

    def test_approve_by_flags_does_not_require_a_new_preview_and_clears_the_earlier_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            x = candidate("local", "x", "Reject then approve")
            set_segment(x, 0, 1)
            x["preview"]["gif_path"] = "previews/x.gif"
            ledger.add(x)
            ledger.save("fixture")

            run_cli("reject", "--candidate", "local:x", "--reason", "Motivo do descarte.", "--project", tmp)
            rejected = Ledger(tmp).get("local:x")
            self.assertEqual("rejected", rejected["approval"]["status"])
            self.assertEqual("Motivo do descarte.", rejected["rejection"]["reason"])

            # No `--start`/`--end`: `approve --candidate` alone confirms the interval
            # already on file, exactly like approving a never-rejected candidate — a new
            # preview is not required.
            result = run_cli(
                "approve",
                "--candidate",
                "local:x",
                "--by",
                "Ana",
                "--statement",
                "Aprovo este trecho agora.",
                "--project",
                tmp,
            )
            self.assertEqual("approved", result["approval"]["status"])
            self.assertEqual("Ana", result["approval"]["by"])

            fresh = Ledger(tmp).get("local:x")
            self.assertEqual("approved", fresh["approval"]["status"])
            self.assertEqual("approved", fresh["state"])
            # `approve()` (models.py) removes the earlier `rejection` block: a
            # successful approval supersedes it, so the item is not left looking
            # rejected and approved at the same time.
            self.assertNotIn("rejection", fresh)


if __name__ == "__main__":
    unittest.main()
