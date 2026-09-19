"""Audit-trail structured logging: the call sites added in commands.py,
library.py and review.py for the approval/rights trail.

Drives the real CLI (`run_cli`, a subprocess over `scripts/gb.py`), the same
pattern as `tests/test_trail_reject_verify.py`. Every command runs with
`GB_LOG_LEVEL=DEBUG` so the file handler records everything, including the
DEBUG-level events, and the assertions read `<project>/brolls/getbrolls.log`
back and check both the event sequence and that no private value ever
reached it.
"""

import json
import re
import tempfile
import unittest
from pathlib import Path

# The skill's personal folder goes to a temp dir: no test touches ~/.getbrolls.
import _isolation  # noqa: F401  (import side effect: defines GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT  # noqa: F401  (import side effect: inserts scripts/ into sys.path)

DEBUG_ENV = {"GB_LOG_LEVEL": "DEBUG"}

APPROVER_NAME = "Ana"
APPROVAL_STATEMENT = "Aprovo este trecho exatamente como está para o vídeo."
FAKE_URL = "https://example.com/segredo-nunca-deveria-aparecer-no-log"
EVIDENCE_TEXT = f"Material próprio, gravado por mim; contexto em {FAKE_URL}."
REJECTION_REASON = "Não serve mais para este corte, mudamos de ideia sobre o trecho."

_KV = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|-|\S+)')


def _log_path(project):
    return Path(project).resolve() / "brolls" / "getbrolls.log"


def _project(tmp, duration=3):
    """A project folder with one synthetic local source video, as in
    tests/test_trail_reject_verify.py."""
    root = Path(tmp)
    src = root / "original.mp4"
    synth_video(src, size="160x90", duration=duration, rate=10)
    return root, src


def _parse_line(line):
    """One log line's `key=value` pairs, including the leading `level=`/`logger=`
    housekeeping fields and the `event=` name itself, decoded the same way
    `logs.render_fields` encoded them: `-` -> None, quoted -> JSON-unescaped."""
    fields = {}
    for match in _KV.finditer(line):
        key, raw = match.group(1), match.group(2)
        if raw == "-":
            value = None
        elif raw.startswith('"'):
            value = json.loads(raw)
        else:
            value = raw
        fields[key] = value
    return fields


def _events(text, name=None):
    """Every parsed log line that carries an `event=` field, in file order,
    optionally filtered to one event name."""
    rows = []
    for line in text.splitlines():
        if "event=" not in line:
            continue
        fields = _parse_line(line)
        if "event" not in fields:
            continue
        if name is None or fields["event"] == name:
            rows.append(fields)
    return rows


@skip_unless_ffmpeg
class CandidateWalkThroughLoggingTests(unittest.TestCase):
    """Walks one candidate through the whole approval/rights trail and checks
    the resulting `getbrolls.log` line by line."""

    def test_the_full_trail_is_logged_in_order_with_the_right_fields(self):  # noqa: PLR0915 - existing size; asserts every step of the approval/rights trail in order
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            base = ["--project", str(root)]

            resolved = run_cli("resolve", "--file", str(src), *base, env=DEBUG_ENV)
            cid = resolved["id"]
            cbase = ["--candidate", cid, *base]

            run_cli("preview", *cbase, "--start", 0, "--end", 1, env=DEBUG_ENV)
            run_cli(
                "approve",
                *cbase,
                "--by",
                APPROVER_NAME,
                "--statement",
                APPROVAL_STATEMENT,
                env=DEBUG_ENV,
            )
            run_cli("permit", *cbase, "--evidence", EVIDENCE_TEXT, env=DEBUG_ENV)
            fetched = run_cli("fetch", *cbase, env=DEBUG_ENV)
            run_cli("verify", *base, env=DEBUG_ENV)
            run_cli("deliver", *base, env=DEBUG_ENV)
            run_cli("reject", *cbase, "--reason", REJECTION_REASON, env=DEBUG_ENV)

            text = _log_path(root).read_text(encoding="utf-8")
            events = _events(text)
            names_in_order = [e["event"] for e in events]

            # The eight audited transitions appear, in the order the commands ran
            # (other events, e.g. `command_start`/`command_end`/`config`, may be
            # interleaved between them — only relative order of these matters).
            expected_order = ["resolve", "preview", "approve", "permit", "fetch", "verify", "deliver", "reject"]
            filtered = [n for n in names_in_order if n in expected_order]
            self.assertEqual(expected_order, filtered)

            resolve_event = next(e for e in events if e["event"] == "resolve")
            self.assertEqual("local", resolve_event["provider"])
            self.assertEqual(cid, resolve_event["candidate"])
            self.assertEqual("file", resolve_event["kind"])

            preview_event = next(e for e in events if e["event"] == "preview")
            self.assertEqual(cid, preview_event["candidate"])
            self.assertEqual("cut", preview_event["mode"])
            self.assertEqual(0.0, float(preview_event["start_s"]))
            self.assertEqual(1.0, float(preview_event["end_s"]))

            approve_event = next(e for e in events if e["event"] == "approve")
            self.assertEqual(cid, approve_event["candidate"])
            self.assertEqual("chat", approve_event["channel"])
            self.assertEqual("True", approve_event["by_present"])
            self.assertEqual("True", approve_event["statement_present"])

            permit_event = next(e for e in events if e["event"] == "permit")
            self.assertEqual(cid, permit_event["candidate"])
            self.assertEqual("evidence", permit_event["route"])
            self.assertIsNone(permit_event["preset"])

            fetch_event = next(e for e in events if e["event"] == "fetch")
            self.assertEqual(cid, fetch_event["candidate"])
            self.assertEqual("local", fetch_event["kind"])
            self.assertEqual(fetched["output"]["sha256"][:12], fetch_event["sha256_prefix"])

            verify_event = next(e for e in events if e["event"] == "verify")
            self.assertEqual(cid, verify_event["candidate"])
            self.assertEqual("ok", verify_event["result"])

            deliver_event = next(e for e in events if e["event"] == "deliver")
            self.assertEqual("1", deliver_event["delivered"])
            self.assertEqual("0", deliver_event["conflicts"])
            self.assertEqual("False", deliver_event["dry_run"])

            reject_event = next(e for e in events if e["event"] == "reject")
            self.assertEqual(cid, reject_event["candidate"])
            self.assertEqual("False", reject_event["had_review"])
            self.assertEqual("True", reject_event["reason_present"])
            self.assertEqual("True", reject_event["output_cleared"])

    def test_private_values_never_reach_the_log_at_debug_level(self):
        """The approver's name, the approval statement, the rights evidence text
        (including the fake URL inside it) and the rejection reason are all
        real values a human typed for this run; only booleans/counts about them
        may appear in the log, never the text itself."""
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            base = ["--project", str(root)]

            resolved = run_cli("resolve", "--file", str(src), *base, env=DEBUG_ENV)
            cid = resolved["id"]
            cbase = ["--candidate", cid, *base]

            run_cli("preview", *cbase, "--start", 0, "--end", 1, env=DEBUG_ENV)
            run_cli(
                "approve",
                *cbase,
                "--by",
                APPROVER_NAME,
                "--statement",
                APPROVAL_STATEMENT,
                env=DEBUG_ENV,
            )
            run_cli("permit", *cbase, "--evidence", EVIDENCE_TEXT, env=DEBUG_ENV)
            run_cli("fetch", *cbase, env=DEBUG_ENV)
            run_cli("verify", *base, env=DEBUG_ENV)
            run_cli("deliver", *base, env=DEBUG_ENV)
            run_cli("reject", *cbase, "--reason", REJECTION_REASON, env=DEBUG_ENV)

            text = _log_path(root).read_text(encoding="utf-8")
            self.assertNotIn(APPROVER_NAME, text)
            self.assertNotIn(APPROVAL_STATEMENT, text)
            self.assertNotIn(EVIDENCE_TEXT, text)
            self.assertNotIn(FAKE_URL, text)
            self.assertNotIn(REJECTION_REASON, text)


class StdoutStaysOneJsonDocumentTests(unittest.TestCase):
    """Every command in the trail must keep printing exactly one JSON document
    on stdout, DEBUG logging notwithstanding. `run_cli` already parses stdout
    with `json.loads` (which rejects trailing data after the document), so a
    call that returns here without raising already proves this; the resolve
    call is also checked directly against the raw stdout text for clarity."""

    @skip_unless_ffmpeg
    def test_resolve_preview_and_fetch_each_print_a_single_json_document(self):
        import os
        import subprocess
        import sys

        from _paths import CLI

        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            base = ["--project", str(root)]

            environment = dict(os.environ)
            environment.update(DEBUG_ENV)
            done = subprocess.run(
                [sys.executable, str(CLI), "resolve", "--file", str(src), *base],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                check=False,
            )
            self.assertEqual(0, done.returncode, done.stderr)
            decoder = json.JSONDecoder()
            _value, end = decoder.raw_decode(done.stdout)
            # No non-whitespace content after the single parsed JSON document.
            self.assertEqual("", done.stdout[end:].strip())

            resolved = json.loads(done.stdout)
            cid = resolved["id"]
            cbase = ["--candidate", cid, *base]

            run_cli("preview", *cbase, "--start", 0, "--end", 1, env=DEBUG_ENV)
            run_cli(
                "approve",
                *cbase,
                "--by",
                APPROVER_NAME,
                "--statement",
                APPROVAL_STATEMENT,
                env=DEBUG_ENV,
            )
            run_cli("permit", *cbase, "--evidence", "Material próprio de teste.", env=DEBUG_ENV)
            fetched = run_cli("fetch", *cbase, env=DEBUG_ENV)
            self.assertTrue(fetched["output"]["path"])


class ApproveAllLoggingTests(unittest.TestCase):
    """`approve --all` logs the per-item events plus one `approve_all` summary."""

    def test_approve_all_logs_a_summary_and_one_event_per_approved_item(self):
        """Uses `--candidate` repeated (not `--all`), so the pre-existing
        `APPROVE_ALL_WIDE` warning (which names the approver for the human
        reading the response, and is mirrored into the log like every other
        `record_warning`, unrelated to this task's call sites) never fires;
        this keeps the privacy assertion below meaningful."""
        from getbrolls.ledger import Ledger
        from getbrolls.models import candidate, set_segment

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            one = candidate("local", "a", "Com prévia 1")
            set_segment(one, 0, 1)
            one["preview"]["gif_path"] = "previews/a.gif"
            two = candidate("local", "b", "Com prévia 2")
            set_segment(two, 0, 2)
            two["preview"]["gif_path"] = "previews/b.gif"
            ledger.add(one)
            ledger.add(two)
            ledger.save("fixture")

            run_cli(
                "approve",
                "--candidate",
                "local:a",
                "--candidate",
                "local:b",
                "--by",
                APPROVER_NAME,
                "--statement",
                APPROVAL_STATEMENT,
                project=tmp,
                env=DEBUG_ENV,
            )

            text = _log_path(tmp).read_text(encoding="utf-8")
            events = _events(text)
            approve_events = [e for e in events if e["event"] == "approve"]
            summary_events = [e for e in events if e["event"] == "approve_all"]
            self.assertEqual(2, len(approve_events))
            self.assertEqual({"local:a", "local:b"}, {e["candidate"] for e in approve_events})
            self.assertEqual(1, len(summary_events))
            self.assertEqual("2", summary_events[0]["approved"])
            self.assertEqual("0", summary_events[0]["skipped"])
            self.assertNotIn(APPROVER_NAME, text)
            self.assertNotIn(APPROVAL_STATEMENT, text)


class ImportReviewLoggingTests(unittest.TestCase):
    """`import-review` logs `review_file_selected`, one `import_review_item` per
    decision, and one `import_review` summary — never the reviewer's name."""

    def test_import_review_logs_selection_items_and_summary(self):
        import re as _re

        from getbrolls.ledger import Ledger
        from getbrolls.models import candidate, set_segment
        from getbrolls.rendering import render

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            item = candidate("local", "z", "Synthetic")
            set_segment(item, 0, 1)
            ledger.add(item)
            ledger.save("fixture")

            page = Path(render(ledger)).read_text(encoding="utf-8")
            match = _re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
            assert match is not None
            payload = json.loads(match.group(1))
            for entry in payload["items"]:
                entry["state"] = "approved"
            reviews = ledger.root / "reviews"
            reviews.mkdir(parents=True, exist_ok=True)
            path = reviews / "20260919-100000.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            run_cli("import-review", "--by", APPROVER_NAME, project=tmp, env=DEBUG_ENV)

            text = _log_path(tmp).read_text(encoding="utf-8")
            events = _events(text)
            selected = [e for e in events if e["event"] == "review_file_selected"]
            items = [e for e in events if e["event"] == "import_review_item"]
            summary = [e for e in events if e["event"] == "import_review"]
            self.assertEqual(1, len(selected))
            self.assertEqual(path.name, selected[0]["name"])
            self.assertEqual("1", selected[0]["candidates"])
            self.assertEqual(1, len(items))
            self.assertEqual("local:z", items[0]["candidate"])
            self.assertEqual("approved", items[0]["result"])
            self.assertEqual(1, len(summary))
            self.assertEqual("latest", summary[0]["file_source"])
            self.assertEqual("1", summary[0]["applied"])
            self.assertEqual("0", summary[0]["skipped"])
            self.assertEqual("False", summary[0]["rejected_file"])
            self.assertNotIn(APPROVER_NAME, text)


class LibraryLoggingTests(unittest.TestCase):
    """`learn`/`library` log one `event=library` line naming the action."""

    def test_learn_query_and_library_search_are_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(
                "learn",
                "--query",
                "praia ao entardecer",
                "--provider",
                "pexels",
                "--outcome",
                "hit",
                project=tmp,
                env=DEBUG_ENV,
            )
            run_cli("library", "--search", "praia", project=tmp, env=DEBUG_ENV)

            text = _log_path(tmp).read_text(encoding="utf-8")
            events = _events(text)
            library_events = [e for e in events if e["event"] == "library"]
            actions = [e["action"] for e in library_events]
            self.assertIn("learn", actions)
            self.assertIn("search", actions)


class WarningMirrorPrivacyTests(unittest.TestCase):
    """A warning reaches the log by its code only, never by its message text."""

    def test_the_mirrored_warning_carries_the_code_and_not_the_message(self):
        from getbrolls import runtime

        with self.assertLogs("getbrolls.runtime", level="WARNING") as captured:
            runtime.record_warning("APPROVE_ALL_WIDE", "aprovando tudo em nome de Ana: confira a lista")
        joined = "\n".join(captured.output)
        self.assertIn("code=APPROVE_ALL_WIDE", joined)
        self.assertNotIn("Ana", joined)
        self.assertNotIn("confira", joined)


if __name__ == "__main__":
    unittest.main()
