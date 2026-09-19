"""Regression tests for six approval/rights-trail defects fixed together:

1. Rejecting an already-fetched candidate must clear `output` (both
   `commands.mark_rejected` and the Storyboard rejection branch inside
   `review.import_review`), so a rejected item stops counting as
   delivered/verified in `status`.
2. `approve()` (models.py) must drop a stale `rejection` dict left by an
   earlier reject, whether the approval comes from the CLI or from
   `import-review`.
3. `verify` on a sha256 mismatch or a missing output file must flip
   `output.verified` to False and persist it before failing, and a later
   clean `verify` (file restored) must set it back to True.
4. `latest_review_file` must break same-mtime ties by the numeric `-N`
   suffix `serve.save_review` appends for same-second saves, not by raw
   filename order (where `-1.json` sorts before the unsuffixed name).
5. `ledger.validate_manifest` must refuse an item whose `schema_version` is
   present and not a known value, while still loading an item that has no
   `schema_version` key at all (older manifests on users' disks predate it).
6. `permit` on a never-approved (pending) candidate records rights on its
   own; `fetch` stays blocked purely by the missing approval — approval and
   rights are independent gates by design.

Every test drives the real CLI dispatch (`run_cli`, a subprocess over
`scripts/gb.py`), following the pattern in tests/test_trail_verify_fetch.py
and tests/test_trail_review_cli.py.
"""

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# The skill's personal folder goes to a temp dir: no test touches ~/.getbrolls.
import _isolation  # noqa: F401  (import side effect: defines GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT  # noqa: F401  (import side effect: inserts scripts/ into sys.path)

from getbrolls.ledger import Ledger
from getbrolls.models import candidate, set_segment
from getbrolls.rendering import render
from getbrolls.review import latest_review_file

APPROVAL_STATEMENT = "Aprovo este trecho para o vídeo."
EVIDENCE = "Material próprio de teste sintético."


def _project(tmp, duration=3):
    """A project folder with one synthetic local source video."""
    root = Path(tmp)
    src = root / "original.mp4"
    synth_video(src, size="160x90", duration=duration, rate=10)
    return root, src


def _approve(base, start, end):
    run_cli("preview", *base, "--start", start, "--end", end)
    run_cli("approve", *base, "--start", start, "--end", end, "--by", "Ana", "--statement", APPROVAL_STATEMENT)


def _collected_candidate(root, src, start=0, end=1):
    """Resolve, preview, approve, permit and fetch one local candidate through the CLI."""
    resolved = run_cli("resolve", "--file", src, project=root)
    cid = resolved["id"]
    base = ["--candidate", cid, "--project", str(root)]
    _approve(base, start, end)
    run_cli("permit", *base, "--evidence", EVIDENCE)
    out = run_cli("fetch", *base)
    return cid, base, out


def _manifest(root):
    return json.loads((Path(root) / "brolls/manifest.json").read_text(encoding="utf-8"))


def _item(root, cid):
    for c in _manifest(root)["items"]:
        if c["id"] == cid:
            return c
    raise AssertionError(f"candidate {cid} missing from manifest")


def _review_payload(page):
    match = re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
    assert match, "review.html sem o payload embutido"
    return json.loads(match.group(1))


def _save_review(ledger, payload, name):
    reviews = ledger.root / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    path = reviews / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@skip_unless_ffmpeg
class RejectAfterFetchClearsOutputTests(unittest.TestCase):
    """Item 1: an already-fetched candidate must not stay both rejected and
    delivered/verified once someone rejects it."""

    def test_cli_reject_clears_output_and_status_stops_double_counting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            cid, _base, out = _collected_candidate(root, src)
            self.assertTrue(out["output"]["path"])
            self.assertTrue(out["output"]["verified"])
            clip = root / "brolls" / out["output"]["path"]
            self.assertTrue(clip.exists())

            run_cli("reject", "--candidate", cid, "--reason", "Não serve mais.", "--project", root)

            item = _item(root, cid)
            self.assertEqual("rejected", item["approval"]["status"])
            self.assertEqual("rejected", item["state"])
            self.assertIsNone(item["output"]["path"])
            self.assertIsNone(item["output"]["sha256"])
            self.assertFalse(item["output"]["verified"])
            # The canonical clip under brolls/ is never deleted; only the manifest
            # stops pointing at it.
            self.assertTrue(clip.exists())
            # Rejection does not touch segment.revision.
            self.assertEqual(out["segment"]["revision"], item["segment"]["revision"])

            status = run_cli("status", project=root)
            self.assertEqual(1, status["counts"]["rejected"])
            self.assertEqual(0, status["counts"]["delivered"])
            self.assertEqual(0, status["counts"]["verified"])

    def test_import_review_rejection_clears_output_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            cid, _base, out = _collected_candidate(root, src)
            self.assertTrue(out["output"]["path"])

            ledger = Ledger(root)
            page = Path(render(ledger)).read_text(encoding="utf-8")
            payload = _review_payload(page)
            for entry in payload["items"]:
                entry["state"] = "rejected"
            path = _save_review(ledger, payload, "20260918-090000.json")

            run_cli("import-review", "--file", str(path), "--by", "Ana", project=root)

            item = _item(root, cid)
            self.assertEqual("rejected", item["approval"]["status"])
            self.assertEqual("rejected", item["state"])
            self.assertIsNone(item["output"]["path"])
            self.assertIsNone(item["output"]["sha256"])
            self.assertFalse(item["output"]["verified"])

            status = run_cli("status", project=root)
            self.assertEqual(1, status["counts"]["rejected"])
            self.assertEqual(0, status["counts"]["delivered"])
            self.assertEqual(0, status["counts"]["verified"])


class ApproveClearsStaleRejectionTests(unittest.TestCase):
    """Item 2: a successful approval must not leave a stale `rejection` behind."""

    def test_reject_then_approve_by_flags_clears_the_earlier_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            item = candidate("local", "x", "Reject then approve")
            set_segment(item, 0, 1)
            item["preview"]["gif_path"] = "previews/x.gif"
            ledger.add(item)
            ledger.save("fixture")

            run_cli("reject", "--candidate", "local:x", "--reason", "Motivo do descarte.", "--project", tmp)
            self.assertIn("rejection", Ledger(tmp).get("local:x"))

            run_cli(
                "approve",
                "--candidate",
                "local:x",
                "--by",
                "Ana",
                "--statement",
                APPROVAL_STATEMENT,
                "--project",
                tmp,
            )

            fresh = Ledger(tmp).get("local:x")
            self.assertEqual("approved", fresh["approval"]["status"])
            self.assertNotIn("rejection", fresh)

    def test_import_review_approval_clears_the_earlier_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            item = candidate("local", "y", "Reject then approve via review")
            set_segment(item, 0, 1)
            ledger.add(item)
            ledger.save("fixture")

            run_cli("reject", "--candidate", "local:y", "--reason", "Motivo do descarte.", "--project", tmp)
            self.assertIn("rejection", Ledger(tmp).get("local:y"))

            page = Path(render(Ledger(tmp))).read_text(encoding="utf-8")
            payload = _review_payload(page)
            for entry in payload["items"]:
                entry["state"] = "approved"
            path = _save_review(Ledger(tmp), payload, "20260918-091000.json")

            run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp)

            fresh = Ledger(tmp).get("local:y")
            self.assertEqual("approved", fresh["approval"]["status"])
            self.assertNotIn("rejection", fresh)


@skip_unless_ffmpeg
class VerifyRestoresVerifiedFlagTests(unittest.TestCase):
    """Item 3: a mismatch clears `output.verified`; a later clean verify sets it
    back to True once the file is restored."""

    def test_verify_after_restoring_the_original_bytes_sets_verified_back_to_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            cid, _base, out = _collected_candidate(root, src)
            clip = root / "brolls" / out["output"]["path"]
            original_sha = out["output"]["sha256"]
            original_bytes = clip.read_bytes()

            tampered = bytearray(original_bytes)
            tampered[len(tampered) // 2] ^= 0xFF
            clip.write_bytes(bytes(tampered))

            error = run_cli("verify", project=root, expect=2)
            self.assertEqual("INVALID_DATA", error["error_code"])
            failed = _item(root, cid)
            self.assertFalse(failed["output"]["verified"])
            # The state follows the flag: never "verified" with verified == False.
            self.assertEqual("approved", failed["state"])

            clip.write_bytes(original_bytes)
            result = run_cli("verify", project=root)
            self.assertEqual(1, result["count"])
            item = _item(root, cid)
            self.assertTrue(item["output"]["verified"])
            self.assertEqual("verified", item["state"])
            self.assertEqual(original_sha, item["output"]["sha256"])

    def test_a_clip_that_matches_its_hash_but_does_not_decode_is_not_marked_verified(self):
        """The flag only goes back to True after probe, hash AND decode all pass."""
        from getbrolls import commands
        from getbrolls.cli import main
        from getbrolls.runtime import OperationError

        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            cid, _base, _out = _collected_candidate(root, src)
            # Start from a previously failed check, with the file intact on disk.
            ledger = Ledger(root)
            stored = ledger.get(cid)
            stored["output"]["verified"] = False
            stored["state"] = "approved"
            ledger.save("test", stored)

            def decode_fails(args, *_, **__):
                if "null" in args:
                    raise ValueError("decode failed")
                return real_run(args)

            real_run = commands.run
            with (
                patch.object(commands, "run", side_effect=decode_fails),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(OperationError),
            ):
                main(["verify", "--project", str(root)])
            item = _item(root, cid)
            self.assertFalse(item["output"]["verified"])
            self.assertEqual("approved", item["state"])


class VerifyChecksEveryClipTests(unittest.TestCase):
    """One bad clip must not hide another: every collected clip is checked and flagged."""

    @skip_unless_ffmpeg
    def test_two_altered_clips_are_both_flagged_in_one_run_and_neither_is_delivered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, first_src = _project(tmp)
            second_src = root / "second.mp4"
            synth_video(second_src, size="160x90", duration=3, rate=10, pattern="testsrc2")
            first, _base1, out1 = _collected_candidate(root, first_src)
            second, _base2, out2 = _collected_candidate(root, second_src)
            for out in (out1, out2):
                clip = root / "brolls" / out["output"]["path"]
                data = bytearray(clip.read_bytes())
                data[len(data) // 2] ^= 0xFF
                clip.write_bytes(bytes(data))

            run_cli("verify", project=root, expect=2)
            self.assertFalse(_item(root, first)["output"]["verified"])
            self.assertFalse(_item(root, second)["output"]["verified"])

            report = run_cli("deliver", project=root)
            self.assertEqual({first, second}, {entry["id"] for entry in report["skipped"]})

    @skip_unless_ffmpeg
    def test_deleting_the_altered_clip_and_fetching_again_is_a_real_way_out(self):
        """The delivery skip reason promises this recovery path; keep it true."""
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            cid, base, out = _collected_candidate(root, src)
            clip = root / "brolls" / out["output"]["path"]
            data = bytearray(clip.read_bytes())
            data[len(data) // 2] ^= 0xFF
            clip.write_bytes(bytes(data))
            run_cli("verify", project=root, expect=2)

            clip.unlink()
            run_cli("fetch", *base)
            result = run_cli("verify", project=root)
            self.assertEqual(1, result["count"])
            self.assertTrue(_item(root, cid)["output"]["verified"])


class LatestReviewFileTieBreakTests(unittest.TestCase):
    """Item 4: same-second saves must be ordered by the `-N` suffix
    `serve.save_review` appends, not by raw filename comparison."""

    def test_the_numerically_later_same_second_suffix_wins_the_tie(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            item = candidate("local", "one", "Synthetic")
            set_segment(item, 0, 1)
            ledger.add(item)
            ledger.save("fixture")

            page = Path(render(ledger)).read_text(encoding="utf-8")
            payload = _review_payload(page)

            older = json.loads(json.dumps(payload))
            for entry in older["items"]:
                entry["state"] = "pending"
            newer = json.loads(json.dumps(payload))
            for entry in newer["items"]:
                entry["state"] = "approved"

            # `serve.save_review` names the first save of a second with no suffix and
            # every later save in that same second `-1`, `-2`, ... Lexicographically
            # `-1.json` sorts before the unsuffixed name (`-` < `.`), so a naive
            # raw-filename tie-break would pick the older, unsuffixed save.
            stamp = "20260918-093000"
            older_path = _save_review(ledger, older, f"{stamp}.json")
            newer_path = _save_review(ledger, newer, f"{stamp}-1.json")
            same_mtime = newer_path.stat().st_mtime
            os.utime(older_path, (same_mtime, same_mtime))
            os.utime(newer_path, (same_mtime, same_mtime))

            self.assertEqual(newer_path, latest_review_file(ledger.root))

            result = run_cli("import-review", "--by", "Ana", project=tmp)
            self.assertEqual(str(newer_path), result["file"])
            self.assertEqual("approved", Ledger(tmp).get("local:one")["approval"]["status"])


class ManifestItemSchemaVersionTests(unittest.TestCase):
    """Item 5: `validate_manifest` checks the item-level `schema_version` written
    by `models.candidate()`, but must keep accepting older items that predate it."""

    def _manifest_path(self, tmp):
        root = Path(tmp) / "brolls"
        root.mkdir(parents=True, exist_ok=True)
        return root / "manifest.json"

    def test_an_item_without_a_schema_version_key_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._manifest_path(tmp)
            item = candidate("local", "legacy", "Manifesto antigo")
            del item["schema_version"]
            raw = json.dumps({"schema_version": 1, "items": [item]})
            path.write_text(raw, encoding="utf-8")

            result = run_cli("status", project=tmp)
            self.assertEqual(1, result["counts"]["candidates"])

    def test_an_item_with_an_unknown_schema_version_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._manifest_path(tmp)
            item = candidate("local", "future", "Manifesto de versão futura")
            item["schema_version"] = 2
            raw = json.dumps({"schema_version": 1, "items": [item]})
            path.write_text(raw, encoding="utf-8")

            error = run_cli("status", project=tmp, expect=2)
            self.assertEqual("INVALID_DATA", error["error_code"])
            self.assertIn("manifest.json inválido", error["error"])
            self.assertEqual(raw, path.read_text(encoding="utf-8"))

    def test_an_item_schema_version_of_true_or_a_float_is_refused(self):
        """Finding: the item-level check used plain `!=`, so `True` (`True == 1`) and
        `1.0` (`1.0 == 1`) passed as if they were the int `1`. The top-level
        `schema_version` check already requires `type(value) is int`; the item-level
        one must mirror that strictness instead of a looser `!=`."""
        for bad_value in (True, 1.0):
            with self.subTest(schema_version=bad_value), tempfile.TemporaryDirectory() as tmp:
                path = self._manifest_path(tmp)
                item = candidate("local", "future", "Manifesto de versão futura")
                item["schema_version"] = bad_value
                raw = json.dumps({"schema_version": 1, "items": [item]})
                path.write_text(raw, encoding="utf-8")

                error = run_cli("status", project=tmp, expect=2)
                self.assertEqual("INVALID_DATA", error["error_code"])
                self.assertIn("manifest.json inválido", error["error"])

    def test_an_item_schema_version_of_the_real_int_1_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._manifest_path(tmp)
            item = candidate("local", "legacy", "Manifesto com schema_version 1")
            item["schema_version"] = 1
            raw = json.dumps({"schema_version": 1, "items": [item]})
            path.write_text(raw, encoding="utf-8")

            result = run_cli("status", project=tmp)
            self.assertEqual(1, result["counts"]["candidates"])


class ReviewTemplateVersionTypeConfusionTests(unittest.TestCase):
    """Item 5 (review side): `import_review` compared `templateVersion` with plain
    `!=`, so a float like `2.0` (`2.0 == 2`) silently passed as if it were the real
    int `REVIEW_TEMPLATE_VERSION`. Must mirror `validate_manifest`'s `type(...) is
    int` strictness and still be refused with the usual "outra coleta" message."""

    def _payload(self, ledger):
        page = Path(render(ledger)).read_text(encoding="utf-8")
        payload = _review_payload(page)
        for entry in payload["items"]:
            entry["state"] = "approved"
        return payload

    def test_a_float_template_version_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            item = candidate("local", "one", "Synthetic")
            set_segment(item, 0, 1)
            ledger.add(item)
            ledger.save("fixture")
            payload = self._payload(ledger)
            payload["templateVersion"] = 2.0
            path = _save_review(ledger, payload, "20260918-100000.json")
            error = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp, expect=2)
            self.assertIn("outra coleta", error["error"])

    def test_a_boolean_template_version_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            item = candidate("local", "one", "Synthetic")
            set_segment(item, 0, 1)
            ledger.add(item)
            ledger.save("fixture")
            payload = self._payload(ledger)
            payload["templateVersion"] = True
            path = _save_review(ledger, payload, "20260918-100000.json")
            error = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp, expect=2)
            self.assertIn("outra coleta", error["error"])

    def test_the_real_int_template_version_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            item = candidate("local", "one", "Synthetic")
            set_segment(item, 0, 1)
            ledger.add(item)
            ledger.save("fixture")
            path = _save_review(ledger, self._payload(ledger), "20260918-100000.json")
            result = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp)
            self.assertEqual(1, result["imported"])


@skip_unless_ffmpeg
class PermitOnPendingCandidateTests(unittest.TestCase):
    """Item 6: approval and rights are independent gates — `permit` works on a
    pending (never-approved) candidate, but `fetch` stays blocked by approval."""

    def test_permit_on_a_pending_candidate_records_rights_but_fetch_stays_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            resolved = run_cli("resolve", "--file", src, project=root)
            cid = resolved["id"]
            base = ["--candidate", cid, "--project", str(root)]
            run_cli("preview", *base, "--start", 0, "--end", 1)

            pending = _item(root, cid)
            self.assertEqual("pending", pending["approval"]["status"])

            permitted = run_cli("permit", *base, "--evidence", EVIDENCE)
            self.assertEqual("permitted", permitted["rights"]["status"])
            self.assertEqual("pending", permitted["approval"]["status"])

            refused = run_cli("fetch", *base, expect=2)
            self.assertEqual("INVALID_DATA", refused["error_code"])
            self.assertIn("Aprovação humana ausente ou inválida", refused["message"])
            self.assertIsNone(_item(root, cid)["output"]["path"])


if __name__ == "__main__":
    unittest.main()
