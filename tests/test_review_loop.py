"""#44: importar decisões sem fricção — sem `--file` e sem perder o lote inteiro por um item."""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls.ledger import Ledger
from getbrolls.models import candidate, set_segment
from getbrolls.rendering import render
from getbrolls.review import import_review, latest_review_file


def _review_payload(page):
    match = re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
    assert match, "review.html sem o payload embutido"
    return match.group(1)


def exported(ledger, state="approved"):
    page = Path(render(ledger)).read_text(encoding="utf-8")
    payload = json.loads(_review_payload(page))
    for item in payload["items"]:
        item["state"] = state
    return payload


def project_with(folder, *names):
    ledger = Ledger(folder)
    for name in names:
        c = candidate("local", name, "Synthetic")
        set_segment(c, 0, 1)
        ledger.add(c)
    return ledger


def save_review(ledger, payload, name="20260917-120000.json"):
    reviews = ledger.root / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    path = reviews / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class AutoDiscoveryTests(unittest.TestCase):
    def test_import_without_file_uses_the_newest_review_saved_by_the_page(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = project_with(folder, "one")
            save_review(ledger, exported(ledger, "rejected"), "20260917-100000.json")
            save_review(ledger, exported(ledger), "20260917-110000.json")
            result = import_review(ledger, None, "Human")
            self.assertEqual(1, result["imported"])
            self.assertEqual("approved", ledger.data["items"][0]["review"]["state"])
            self.assertTrue(result["file"].endswith("20260917-110000.json"))

    def test_latest_review_file_is_none_without_any_saved_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = project_with(folder, "one")
            self.assertIsNone(latest_review_file(ledger.root))

    def test_import_without_file_and_without_reviews_explains_what_to_do(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = project_with(folder, "one")
            with self.assertRaisesRegex(ValueError, "Salvar decisões"):
                import_review(ledger, None, "Human")


class PartialImportTests(unittest.TestCase):
    def test_a_stale_item_is_skipped_and_the_valid_ones_are_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = project_with(folder, "one", "two")
            payload = exported(ledger)
            payload["items"][1]["reviewEpoch"] = "0" * 64
            path = save_review(ledger, payload)
            result = import_review(ledger, path, "Human")
            self.assertEqual(1, result["imported"])
            self.assertEqual(
                [{"id": payload["items"][1]["id"], "reason": "stale_epoch"}],
                [{"id": s["id"], "reason": s["reason"]} for s in result["skipped"]],
            )
            states = {c["id"]: c["approval"]["status"] for c in ledger.data["items"]}
            self.assertEqual("approved", states[payload["items"][0]["id"]])
            self.assertEqual("pending", states[payload["items"][1]["id"]])

    def test_a_changed_segment_is_skipped_as_signature_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = project_with(folder, "one", "two")
            payload = exported(ledger)
            payload["items"][0]["signature"] = "0" * 64
            path = save_review(ledger, payload)
            result = import_review(ledger, path, "Human")
            self.assertEqual(1, result["imported"])
            self.assertEqual("signature_mismatch", result["skipped"][0]["reason"])

    def test_an_adjustment_without_a_comment_is_skipped_as_invalid_item(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = project_with(folder, "one", "two")
            payload = exported(ledger)
            payload["items"][0]["state"] = "changes"
            payload["items"][0]["comment"] = "  "
            path = save_review(ledger, payload)
            result = import_review(ledger, path, "Human")
            self.assertEqual(1, result["imported"])
            self.assertEqual("invalid_item", result["skipped"][0]["reason"])

    def test_nothing_applied_raises_and_leaves_the_ledger_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = project_with(folder, "one")
            payload = exported(ledger)
            payload["items"][0]["reviewEpoch"] = "0" * 64
            path = save_review(ledger, payload)
            before = ledger.path.read_bytes()
            with self.assertRaises(ValueError):
                import_review(ledger, path, "Human")
            self.assertEqual(before, ledger.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
