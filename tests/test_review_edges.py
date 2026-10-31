"""Bordas do `review.py`: escolha da decisão mais nova, recusas do arquivo de decisões,
itens inválidos pulados, estados de ajuste e o log que nunca derruba a importação."""

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _logs import Rows
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import layout, review
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, empty_output, set_segment
from getbrolls.rendering import render
from getbrolls.review import import_review, latest_review_file


def _exported(ledger, state="approved"):
    page = Path(render(ledger)).read_text(encoding="utf-8")
    match = re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
    assert match, "review.html sem o payload embutido"
    payload = json.loads(match.group(1))
    for item in payload["items"]:
        item["state"] = state
    return payload


def _ledger(folder, *names):
    ledger = Ledger(folder)
    for name in names:
        c = candidate("local", name, "Synthetic")
        set_segment(c, 0, 1)
        ledger.add(c)
    return ledger


def _save(ledger, payload, name="20260917-120000.json"):
    reviews = ledger.root / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    path = reviews / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class LatestReviewFileTests(unittest.TestCase):
    def test_a_reviews_folder_without_files_has_no_latest(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "reviews" / "pasta.json").mkdir(parents=True)  # diretório não conta
            (Path(folder) / "reviews" / "nota.txt").write_text("x", encoding="utf-8")
            self.assertIsNone(latest_review_file(folder))

    def test_a_same_second_tie_picks_the_numbered_suffix_not_the_bare_name(self):
        with tempfile.TemporaryDirectory() as folder:
            reviews = Path(folder) / "reviews"
            reviews.mkdir()
            names = ["20260917-120000.json", "20260917-120000-1.json", "20260917-120000-2.json"]
            for name in names:
                (reviews / name).write_text("{}", encoding="utf-8")
                os.utime(reviews / name, (1_700_000_000, 1_700_000_000))
            found = latest_review_file(folder)
            assert found is not None
            self.assertEqual("20260917-120000-2.json", found.name)

    def latest_in_both_listing_orders(self, folder):
        """`latest_review_file` with the folder listed in name order and in reverse: the answer
        must not depend on the order the file system returns (APFS sorts, ext4 and NTFS do not)."""
        real_glob = Path.glob
        found = []
        for reverse in (False, True):

            def listing(path, pattern, reverse=reverse):
                return iter(sorted(real_glob(path, pattern), reverse=reverse))

            with patch.object(Path, "glob", listing):
                latest = latest_review_file(folder)
            assert latest is not None
            found.append(latest.name)
        return found[0], found[1]

    def test_the_latest_file_does_not_depend_on_the_listing_order(self):
        with tempfile.TemporaryDirectory() as folder:
            reviews = Path(folder) / "reviews"
            reviews.mkdir()
            # `-0` and no suffix are the same position in the same second: a real tie.
            for name in ("20260917-120000.json", "20260917-120000-0.json", "20260917-120000-1.json"):
                (reviews / name).write_text("{}", encoding="utf-8")
                os.utime(reviews / name, (1_700_000_000, 1_700_000_000))
            self.assertEqual(("20260917-120000-1.json",) * 2, self.latest_in_both_listing_orders(folder))
            (reviews / "20260917-120000-1.json").unlink()
            first, second = self.latest_in_both_listing_orders(folder)
            self.assertEqual(first, second)

    def test_a_file_with_a_foreign_name_still_competes_by_modification_time(self):
        with tempfile.TemporaryDirectory() as folder:
            reviews = Path(folder) / "reviews"
            reviews.mkdir()
            (reviews / "20260917-120000.json").write_text("{}", encoding="utf-8")
            (reviews / "manual.json").write_text("{}", encoding="utf-8")
            os.utime(reviews / "20260917-120000.json", (1_700_000_000, 1_700_000_000))
            os.utime(reviews / "manual.json", (1_700_000_100, 1_700_000_100))
            found = latest_review_file(folder)
            assert found is not None
            self.assertEqual("manual.json", found.name)
            os.utime(reviews / "manual.json", (1_699_999_900, 1_699_999_900))
            found = latest_review_file(folder)
            assert found is not None
            self.assertEqual("20260917-120000.json", found.name)


class RefusedDecisionFileTests(unittest.TestCase):
    def test_a_blank_reviewer_is_refused_before_anything_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = _ledger(folder, "one")
            before = json.dumps(ledger.data, sort_keys=True)
            with self.assertRaisesRegex(ValueError, "--by"):
                import_review(ledger, None, "   ")
            self.assertEqual(before, json.dumps(ledger.data, sort_keys=True))
            self.assertFalse((ledger.root / "reviews").exists())

    def test_an_empty_or_non_list_items_value_is_refused(self):
        for items in ([], None, "x", {"a": 1}):
            with self.subTest(items=items), tempfile.TemporaryDirectory() as folder:
                ledger = _ledger(folder, "one")
                payload = _exported(ledger)
                payload["items"] = items
                before = ledger.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "vazio"):
                    import_review(ledger, _save(ledger, payload), "Human")
                self.assertEqual(before, ledger.path.read_bytes())

    def test_an_item_without_a_string_id_is_refused_as_a_whole(self):
        for bad in ("texto", 7, {"state": "approved"}, {"id": 3}):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as folder:
                ledger = _ledger(folder, "one")
                payload = _exported(ledger)
                payload["items"].append(bad)
                before = ledger.path.read_bytes()
                with self.assertRaisesRegex(ValueError, "sem identificação"):
                    import_review(ledger, _save(ledger, payload), "Human")
                self.assertEqual(before, ledger.path.read_bytes())

    def test_a_decision_file_over_two_megabytes_is_refused_unread(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = _ledger(folder, "one")
            payload = _exported(ledger)
            payload["padding"] = "x" * 2_000_001
            before = json.dumps(ledger.data, sort_keys=True)
            with self.assertRaisesRegex(ValueError, "passa de 2 MB"):
                import_review(ledger, _save(ledger, payload), "Human")
            self.assertEqual(before, json.dumps(ledger.data, sort_keys=True))

    def test_the_same_id_twice_is_refused_naming_the_item(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = _ledger(folder, "one")
            payload = _exported(ledger)
            payload["items"].append(dict(payload["items"][0]))
            before = ledger.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "aparece duas vezes") as ctx:
                import_review(ledger, _save(ledger, payload), "Human")
            self.assertIn(payload["items"][0]["id"], str(ctx.exception))
            self.assertEqual(before, ledger.path.read_bytes())

    def test_an_item_missing_its_signature_or_epoch_is_refused(self):
        for missing in ("signature", "reviewEpoch"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder:
                ledger = _ledger(folder, "one")
                payload = _exported(ledger)
                del payload["items"][0][missing]
                with self.assertRaisesRegex(ValueError, "sem assinatura"):
                    import_review(ledger, _save(ledger, payload), "Human")
                self.assertNotIn("review", ledger.data["items"][0])

    def test_a_file_from_another_collection_is_refused(self):
        mutations = {
            "type": lambda p: p.update(type="outro"),
            "project": lambda p: p.update(project="outro-projeto"),
            "version_bool": lambda p: p.update(templateVersion=True),
            "version_float": lambda p: p.update(templateVersion=2.0),
            "version_old": lambda p: p.update(templateVersion=1),
        }
        for label, mutate in mutations.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as folder:
                ledger = _ledger(folder, "one")
                payload = _exported(ledger)
                mutate(payload)
                with self.assertRaisesRegex(ValueError, "outra coleta"):
                    import_review(ledger, _save(ledger, payload), "Human")
        with tempfile.TemporaryDirectory() as folder:
            ledger = _ledger(folder, "one")
            with self.assertRaisesRegex(ValueError, "outra coleta"):
                import_review(ledger, _save(ledger, ["lista"]), "Human")


class SkippedItemTests(unittest.TestCase):
    def _import_with(self, mutate):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        ledger = _ledger(folder, "one", "two")
        payload = _exported(ledger)
        mutate(payload["items"][1])
        path = _save(ledger, payload)
        return ledger, payload, import_review(ledger, path, "Human")

    def test_oversized_comment_or_suggestion_is_skipped_and_the_rest_applied(self):
        cases = {
            "comment_too_long": {"comment": "a" * 10001},
            "suggestion_too_long": {"suggestion": "https://example.com/" + "a" * 2000},
            "comment_not_text": {"comment": 5},
            "suggestion_not_text": {"suggestion": ["https://example.com/x"]},
        }
        for label, fields in cases.items():
            with self.subTest(label):
                ledger, payload, result = self._import_with(lambda item, f=fields: item.update(f))
                self.assertEqual(1, result["imported"])
                self.assertEqual("invalid_item", result["skipped"][0]["reason"])
                self.assertEqual(payload["items"][1]["id"], result["skipped"][0]["id"])
                self.assertIn("grande demais", result["skipped"][0]["detail"])
                skipped_item = next(c for c in ledger.data["items"] if c["id"] == payload["items"][1]["id"])
                self.assertNotIn("review", skipped_item)
                self.assertEqual("pending", skipped_item["approval"]["status"])

    def test_an_unknown_state_is_skipped_as_invalid_item(self):
        ledger, payload, result = self._import_with(lambda item: item.update(state="maybe"))
        self.assertEqual(1, result["imported"])
        self.assertEqual("invalid_item", result["skipped"][0]["reason"])
        self.assertIn("Decisão desconhecida", result["skipped"][0]["detail"])
        skipped_item = next(c for c in ledger.data["items"] if c["id"] == payload["items"][1]["id"])
        self.assertNotIn("review", skipped_item)

    def test_a_non_public_suggestion_link_is_skipped(self):
        for link in ("http://example.com/clip", "https://user:pass@example.com/clip", "https://localhost/clip"):
            with self.subTest(link=link):
                ledger, payload, result = self._import_with(lambda item, u=link: item.update(suggestion=u))
                self.assertEqual(1, result["imported"])
                self.assertEqual("invalid_item", result["skipped"][0]["reason"])
                self.assertIn("https público", result["skipped"][0]["detail"])
                skipped_item = next(c for c in ledger.data["items"] if c["id"] == payload["items"][1]["id"])
                self.assertNotIn("review", skipped_item)

    def test_changes_without_a_comment_is_skipped_asking_what_to_change(self):
        for state in ("changes", "alternative"):
            with self.subTest(state):
                ledger, payload, result = self._import_with(lambda item, s=state: item.update(state=s, comment="  "))
                self.assertEqual(1, result["imported"])
                self.assertEqual("invalid_item", result["skipped"][0]["reason"])
                self.assertIn("Faltou dizer", result["skipped"][0]["detail"])
                skipped_item = next(c for c in ledger.data["items"] if c["id"] == payload["items"][1]["id"])
                self.assertNotIn("review", skipped_item)

    def test_when_every_item_is_skipped_the_import_fails_with_each_reason(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        ledger = _ledger(folder, "one")
        payload = _exported(ledger)
        payload["items"][0]["state"] = "changes"
        payload["items"][0]["comment"] = ""
        path = _save(ledger, payload)
        with self.assertRaises(ValueError) as caught:
            import_review(ledger, path, "Human")
        self.assertIn("Nenhuma decisão pôde ser importada.", str(caught.exception))
        self.assertIn("Faltou dizer", str(caught.exception))

    def test_a_public_https_suggestion_is_kept_on_the_review(self):
        link = "https://example.com/better-clip"
        ledger, payload, result = self._import_with(lambda item: item.update(suggestion=link))
        self.assertEqual(2, result["imported"])
        self.assertEqual([], result["skipped"])
        kept = next(c for c in ledger.data["items"] if c["id"] == payload["items"][1]["id"])
        self.assertEqual(link, kept["review"]["suggestion"])
        self.assertEqual("Human", kept["review"]["by"])


class AdjustmentStateTests(unittest.TestCase):
    def test_changes_and_alternative_keep_the_item_awaiting_approval(self):
        for state in ("changes", "alternative", "pending"):
            with self.subTest(state), tempfile.TemporaryDirectory() as folder:
                ledger = _ledger(folder, "one")
                payload = _exported(ledger, state)
                payload["items"][0]["comment"] = "trocar o corte"
                with self.assertLogs("getbrolls.review", level="INFO") as capture:
                    result = import_review(ledger, _save(ledger, payload), "Human")
                self.assertEqual(1, result["imported"])
                item = ledger.data["items"][0]
                self.assertEqual("awaiting_approval", item["state"])
                self.assertEqual("pending", item["approval"]["status"])
                self.assertEqual(state, item["review"]["state"])
                self.assertEqual("trocar o corte", item["review"]["comment"])
                rows = Rows(capture)
                self.assertEqual("pending", rows.one("import_review_item", candidate=item["id"])["result"])
                self.assertEqual("False", rows.one("import_review")["rejected_file"])


class RejectionAndRulesTests(unittest.TestCase):
    def test_a_rejection_resets_the_delivered_output_and_names_the_reviewer(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = _ledger(folder, "one")
            item = ledger.data["items"][0]
            item["output"] = {"path": "clips/one.mp4", "sha256": "a" * 64, "verified": True}
            ledger.save("fixture", item)
            payload = _exported(ledger, "rejected")
            with self.assertLogs("getbrolls.review", level="INFO") as capture:
                result = import_review(ledger, _save(ledger, payload), "Human")
            self.assertEqual(1, result["imported"])
            fresh = Ledger(folder).get(item["id"])
            self.assertEqual("rejected", fresh["state"])
            self.assertEqual("rejected", fresh["approval"]["status"])
            self.assertEqual("Human", fresh["approval"]["by"])
            self.assertEqual(empty_output(), fresh["output"])
            rows = Rows(capture)
            self.assertEqual("rejected", rows.one("import_review_item", candidate=item["id"])["result"])
            self.assertEqual("True", rows.one("import_review")["rejected_file"])

    def test_approval_is_skipped_when_the_user_rules_block_the_asset(self):
        blocked_type = {"asset_types": ["image"], "blocked_domains": []}
        blocked_domain = {"asset_types": ["video"], "blocked_domains": ["example.com"]}
        for label, rules in (("asset_type", blocked_type), ("domain", blocked_domain)):
            with self.subTest(label), tempfile.TemporaryDirectory() as folder:
                ledger = _ledger(folder, "one")
                item = ledger.data["items"][0]
                item["source_url"] = "https://example.com/watch?v=1"
                ledger.save("fixture", item)
                payload = _exported(ledger)
                with self.assertRaisesRegex(ValueError, "Asset bloqueado pelas regras"):
                    import_review(ledger, _save(ledger, payload), "Human", rules=rules)
                fresh = Ledger(folder).get(item["id"])
                self.assertEqual("pending", fresh["approval"]["status"])
                self.assertNotIn("review", fresh)

    def test_rules_that_allow_the_asset_let_the_approval_through(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = _ledger(folder, "one", "two")
            item = ledger.data["items"][0]
            item["source_url"] = "https://example.com/watch?v=1"
            ledger.save("fixture", item)
            rules = {"asset_types": ["video"], "blocked_domains": ["blocked.example"]}
            result = import_review(ledger, _save(ledger, _exported(ledger)), "Human", rules=rules)
            self.assertEqual(2, result["imported"])
            self.assertEqual("approved", Ledger(folder).get(item["id"])["approval"]["status"])


class ProjectIdTests(unittest.TestCase):
    def test_without_project_json_a_uuid_is_created_once_and_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = _ledger(folder, "one")
            first = review.project_id(ledger)
            self.assertRegex(first, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
            self.assertEqual(first, review.project_id(ledger))
            self.assertEqual(first, Ledger(folder).data["project_id"])

    def test_project_json_wins_and_is_copied_into_a_manifest_without_an_id(self):
        with tempfile.TemporaryDirectory() as folder:
            declared = "11111111-2222-4333-8444-555555555555"
            layout.write_project(Path(folder), layout.new_project_doc(project_id=declared))
            ledger = _ledger(folder, "one")
            self.assertNotIn("project_id", ledger.data)
            self.assertEqual(declared, review.project_id(ledger))
            self.assertEqual(declared, Ledger(folder).data["project_id"])

    def test_project_json_wins_over_an_id_the_manifest_already_has_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            declared = "11111111-2222-4333-8444-555555555555"
            layout.write_project(Path(folder), layout.new_project_doc(project_id=declared))
            ledger = _ledger(folder, "one")
            ledger.data["project_id"] = "id-antigo-do-manifesto"
            ledger.save("fixture")
            before = ledger.path.read_bytes()
            self.assertEqual(declared, review.project_id(ledger))
            self.assertEqual(before, ledger.path.read_bytes())


class LoggingNeverBreaksImportTests(unittest.TestCase):
    def test_a_failing_log_line_after_the_save_does_not_undo_the_import(self):
        real_event = review.logs.event

        def flaky(logger, level, event_name, **fields):
            if event_name == "import_review_item":
                raise RuntimeError("log quebrado")
            real_event(logger, level, event_name, **fields)

        with tempfile.TemporaryDirectory() as folder:
            ledger = _ledger(folder, "one")
            payload = _exported(ledger)
            path = _save(ledger, payload)
            with patch.object(review.logs, "event", side_effect=flaky):
                result = import_review(ledger, path, "Human")
            self.assertEqual(1, result["imported"])
            self.assertEqual("approved", Ledger(folder).data["items"][0]["approval"]["status"])


if __name__ == "__main__":
    unittest.main()
