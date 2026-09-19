"""Characterization tests for the `verify`/`fetch` audit trail seam.

Pins current behavior that a refactor of `commands.py::execute()` (the dispatcher this
suite exists to protect) could silently change:

- `verify` detecting a tampered or deleted output file.
- Changing an approved segment invalidating the approval, and `fetch` staying refused
  until the candidate is approved again at the new interval.
- `permit` recording rights independently of approval/rejection state, and `fetch`
  still being gated purely by a missing/stale approval.
- The download size cap surfacing through the real `fetch` command for a remote
  candidate, not just inside `http.download` itself.

Every test drives the real CLI dispatch (`run_cli`, a subprocess over `scripts/gb.py`,
or `getbrolls.cli.main` in-process where a mock is required) so an argument-wiring or
lock-ordering change in the dispatcher would fail these, not just a unit-level call.
"""

import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

# The skill's personal folder goes to a temp dir: no test touches ~/.getbrolls.
import _isolation  # noqa: F401  (import side effect: defines GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT  # noqa: F401  (import side effect: inserts scripts/ into sys.path)

from getbrolls import cli, http, providers
from getbrolls.ledger import Ledger
from getbrolls.models import approve, candidate, set_segment
from getbrolls.runtime import OperationError

APPROVAL_STATEMENT = "Aprovo este trecho para o vídeo."
EVIDENCE = "Material próprio de teste sintético."


def _project(tmp, duration=3):
    """A project folder with one synthetic local source video."""
    root = Path(tmp)
    src = root / "original.mp4"
    synth_video(src, size="160x90", duration=duration, rate=10)
    return root, src


def _approve(root, base, start, end):
    run_cli("preview", *base, "--start", start, "--end", end)
    run_cli("approve", *base, "--start", start, "--end", end, "--by", "Ana", "--statement", APPROVAL_STATEMENT)


def _collected_candidate(root, src, start=0, end=1):
    """Resolve, preview, approve, permit and fetch one local candidate through the CLI."""
    resolved = run_cli("resolve", "--file", src, project=root)
    cid = resolved["id"]
    base = ["--candidate", cid, "--project", str(root)]
    _approve(root, base, start, end)
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


@skip_unless_ffmpeg
class VerifyTamperTests(unittest.TestCase):
    """`verify` re-hashes every collected clip against the sha256 recorded at fetch time."""

    def test_tampered_output_is_refused_and_verified_is_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            cid, _base, out = _collected_candidate(root, src)
            self.assertTrue(out["output"]["verified"])
            clip = root / "brolls" / out["output"]["path"]
            original_sha = out["output"]["sha256"]

            data = bytearray(clip.read_bytes())
            data[len(data) // 2] ^= 0xFF
            clip.write_bytes(bytes(data))

            error = run_cli("verify", project=root, expect=2)
            self.assertEqual(error["error_code"], "INVALID_DATA")
            self.assertIn("Arquivo alterado após coleta", error["message"])
            self.assertIn(cid, error["message"])

            # A failed re-check clears the stale `verified: True` before raising, so a
            # later `deliver` cannot ship the tampered clip claiming it is still good.
            # `sha256` is left as-is — it is the record of what was actually collected.
            item = _item(root, cid)
            self.assertFalse(item["output"]["verified"])
            self.assertEqual(item["output"]["sha256"], original_sha)

    def test_a_deleted_output_file_fails_distinctly_from_a_byte_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            cid, _base, out = _collected_candidate(root, src)
            clip = root / "brolls" / out["output"]["path"]
            clip.unlink()

            error = run_cli("verify", project=root, expect=2)
            self.assertEqual(error["error_code"], "INVALID_DATA")
            # A missing file fails inside ffprobe (probe() runs before the sha comparison),
            # so the message names the tool and the path, not the tamper wording.
            self.assertNotIn("Arquivo alterado após coleta", error["message"])
            self.assertIn("ffprobe", error["message"])
            # Compare by file name: on Windows `tempfile` may hand out the 8.3 short form of the
            # folder while the tool reports the long one, so the full path text can differ.
            self.assertIn(clip.name, error["message"])

            # Same reset as the byte-tamper case: a missing file cannot stay verified=True.
            item = _item(root, cid)
            self.assertFalse(item["output"]["verified"])

    def test_verify_with_nothing_collected_yet_reports_zero_and_does_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            run_cli("resolve", "--file", src, project=root)
            result = run_cli("verify", project=root)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["verified"], [])


@skip_unless_ffmpeg
class StaleApprovalTests(unittest.TestCase):
    """Changing an approved segment must invalidate approval end to end, through `fetch`."""

    def test_fetch_is_refused_after_segment_change_until_reapproved_at_the_new_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp, duration=5)
            resolved = run_cli("resolve", "--file", src, project=root)
            cid = resolved["id"]
            base = ["--candidate", cid, "--project", str(root)]
            _approve(root, base, 0, 1)
            run_cli("permit", *base, "--evidence", EVIDENCE)

            before = _item(root, cid)
            self.assertEqual(before["approval"]["status"], "approved")
            self.assertEqual(before["segment"]["revision"], 1)

            # A new preview interval is the exact seam the refactor threatens: it bumps
            # segment.revision and resets approval to pending (models.set_segment ->
            # invalidate_approval), through the real `preview` dispatch, not a direct
            # models.py call.
            run_cli("preview", *base, "--start", 2, "--end", 3)
            after = _item(root, cid)
            self.assertEqual(after["approval"], {"status": "pending", "by": None, "at": None, "revision": None})
            self.assertEqual(after["segment"]["revision"], 2)
            self.assertEqual(after["state"], "awaiting_approval")
            # permit was never rerun; the segment change does not touch rights at all —
            # the only thing blocking fetch is the stale/missing approval.
            self.assertEqual(after["rights"]["status"], "permitted")

            refused = run_cli("fetch", *base, expect=2)
            self.assertEqual(refused["error_code"], "INVALID_DATA")
            self.assertIn("Aprovação humana ausente ou inválida", refused["message"])
            still_nothing = _item(root, cid)
            self.assertIsNone(still_nothing["output"]["path"])

            _approve(root, base, 2, 3)
            collected = run_cli("fetch", *base)
            self.assertTrue(collected["output"]["verified"])
            final = _item(root, cid)
            self.assertEqual(final["approval"]["status"], "approved")
            self.assertEqual(final["segment"]["revision"], 2)
            self.assertEqual(final["state"], "verified")

    def test_permit_on_a_rejected_candidate_records_rights_but_fetch_stays_blocked(self):
        """permit does not gate on candidate state: it records evidence even after a
        reject, but fetch is still refused because approval — not rights — is missing.
        This is current behavior, not a claim that it is the intended design."""
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            resolved = run_cli("resolve", "--file", src, project=root)
            cid = resolved["id"]
            base = ["--candidate", cid, "--project", str(root)]
            run_cli("preview", *base, "--start", 0, "--end", 1)
            run_cli("reject", *base)
            rejected = _item(root, cid)
            self.assertEqual(rejected["approval"]["status"], "rejected")
            self.assertEqual(rejected["state"], "rejected")

            permitted = run_cli("permit", *base, "--evidence", EVIDENCE)
            self.assertEqual(permitted["rights"]["status"], "permitted")
            self.assertEqual(permitted["state"], "rejected")

            refused = run_cli("fetch", *base, expect=2)
            self.assertEqual(refused["error_code"], "INVALID_DATA")
            self.assertIn("Aprovação humana ausente ou inválida", refused["message"])


class FetchSizeCapTests(unittest.TestCase):
    """The size cap lives in `http.download`; a local-file candidate's `fetch` branch
    never calls it (it copies/cuts straight from `local_path`), so it cannot be exercised
    offline through a purely local-file fetch. This drives it at the narrowest real seam
    that still goes through the `fetch` command dispatch: a manually-built remote
    (`method: https`) candidate with the response opener mocked, calling
    `getbrolls.cli.main` in-process (the same in-process pattern `test_nasa_spaced_urls.py`
    and `test_runtime_diagnostics.py` use) since a subprocess run_cli call cannot see a
    mock in this process. This pins that the cap surfaces as the command's own
    INVALID_DATA/exit-2 envelope, and that a rejected download leaves the ledger
    untouched, not just that `http.download` itself raises."""

    def test_fetch_refuses_a_remote_file_over_the_download_size_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = Ledger(root)
            c = candidate("commons", "12345", "Synthetic remote", "https://commons.wikimedia.org/wiki/File:x.mp4")
            c["media_url"] = "https://commons.wikimedia.org/x.mp4"
            c["acquisition"] = {"status": "available", "method": "https", "evidence": [c["source_url"]]}
            c["media"].update(width=640, height=360, duration_s=3.0)
            set_segment(c, 0, 1)
            approve(c, "Ana", "chat", APPROVAL_STATEMENT)
            c["rights"]["status"] = "permitted"
            c["rights"]["evidence"].append(EVIDENCE)
            ledger.add(c)
            ledger.save("fixture", c)

            class OversizedResponse:
                headers: ClassVar = {"Content-Length": str(700 * 1024 * 1024)}

                def __enter__(self):
                    return self

                def __exit__(self, *exc_info):
                    return False

                def read(self, _n):
                    return b""

            class OversizedOpener:
                def open(self, *_args, **_kwargs):
                    return OversizedResponse()

            with (
                patch.object(providers, "refresh", return_value={**c, "media_url": c["media_url"]}),
                patch.object(http, "_opener", return_value=OversizedOpener()),
                self.assertRaises(OperationError) as ctx,
            ):
                cli.main(["fetch", "--candidate", c["id"], "--project", str(root)])

            self.assertEqual(ctx.exception.payload["error_code"], "INVALID_DATA")
            self.assertIn("excede limite de download", ctx.exception.payload["message"])
            self.assertIn("700.0 MB", ctx.exception.payload["message"])

            item = _item(root, c["id"])
            self.assertIsNone(item["output"]["path"])
            self.assertFalse(item["output"]["verified"])


if __name__ == "__main__":
    unittest.main()
