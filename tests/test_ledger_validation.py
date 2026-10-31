"""Recusas do manifesto e do journal de recuperação: candidato corrompido, insert repetido
com origem diferente, journal com eventos inválidos e `events.jsonl` ilegível.

Em toda recusa o arquivo da pessoa fica como estava: nada é reiniciado nem meio gravado.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from _cli import run_cli

from getbrolls.ledger import Ledger, validate_manifest
from getbrolls.models import candidate

_DROP = object()


def _item(**changes):
    c = candidate("local", "a", "Um")
    for path, value in changes.items():
        section, _, field = path.partition("__")
        if field:
            if value is _DROP:
                del c[section][field]
            else:
                c[section][field] = value
        else:
            c[section] = value
    return c


class ManifestCandidateValidationTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-ledger-validation-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.root = self.project / "brolls"
        self.root.mkdir()
        self.manifest = self.root / "manifest.json"

    def test_a_manifest_that_is_not_an_object_is_refused(self):
        for data in ([], "itens", None):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "manifest.json inválido"):
                validate_manifest(data)

    def test_an_item_without_a_usable_id_is_refused(self):
        for label, items in {
            "id_missing": [{k: v for k, v in _item().items() if k != "id"}],
            "id_empty": [_item(id="")],
            "id_is_a_number": [_item(id=7)],
            "id_repeated": [_item(), _item()],
        }.items():
            with self.subTest(label), self.assertRaisesRegex(ValueError, "manifest.json inválido"):
                validate_manifest({"schema_version": 1, "items": items})

    def test_a_valid_item_with_previews_and_output_loads(self):
        item = _item(output__path="clips/um.mp4", preview__poster_path="previews/um.jpg")
        self.manifest.write_text(json.dumps({"schema_version": 1, "items": [item]}), encoding="utf-8")
        self.assertEqual("clips/um.mp4", Ledger(self.project).get(item["id"])["output"]["path"])

    def test_a_corrupt_candidate_is_refused_and_the_manifest_is_untouched(self):
        cases = {
            "segment_without_revision": _item(segment__revision=_DROP),
            "segment_without_start": _item(segment__start_s=_DROP),
            "revision_is_text": _item(segment__revision="1"),
            "revision_is_bool": _item(segment__revision=True),
            "revision_is_negative": _item(segment__revision=-1),
            "evidence_is_text": _item(rights__evidence="prova"),
            "evidence_has_a_number": _item(rights__evidence=["ok", 3]),
            "evidence_missing": _item(rights__evidence=_DROP),
            "output_without_verified": _item(output__verified=_DROP),
            "output_without_path": _item(output__path=_DROP),
            "output_outside_the_tree": _item(output__path="/etc/passwd"),
            "output_climbs_out": _item(output__path="clips/../../fora.mp4"),
            "output_with_backslash": _item(output__path="clips\\fora.mp4"),
            "output_is_a_number": _item(output__path=7),
            "preview_outside_the_tree": _item(preview__gif_path="tmp/x.gif"),
        }
        for label, item in cases.items():
            with self.subTest(label):
                raw = json.dumps({"schema_version": 1, "items": [item]})
                self.manifest.write_text(raw, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "manifest.json inválido ou incompatível"):
                    Ledger(self.project)
                self.assertEqual(raw, self.manifest.read_text(encoding="utf-8"))
                self.assertEqual([], list((self.root / "candidates").iterdir()))


class AddTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-ledger-add-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.ledger = Ledger(self.project)
        self.first = candidate("local", "a", "Um", source_url="https://example.org/a")
        self.ledger.add(self.first)

    def test_the_same_insert_with_the_same_metadata_returns_the_stored_one(self):
        again = candidate("local", "a", "Um", source_url="https://example.org/a")
        again["state"] = "approved"  # campo fora da origem: não conta como metadado novo
        self.assertIs(self.first, self.ledger.add(again))
        self.assertEqual([self.first], self.ledger.data["items"])

    def test_the_same_insert_with_different_metadata_is_refused(self):
        cases = {
            "title": candidate("local", "a", "Outro", source_url="https://example.org/a"),
            "source_url": candidate("local", "a", "Um", source_url="https://example.org/b"),
            "context_image_sha256": {
                **candidate("local", "a", "Um", source_url="https://example.org/a"),
                "context_image_sha256": "abc",
            },
        }
        for label, other in cases.items():
            with self.subTest(label), self.assertRaisesRegex(ValueError, "metadados diferentes. Use outro --shot"):
                self.ledger.add(other)
        self.assertEqual([self.first], self.ledger.data["items"])
        self.assertEqual("Um", self.ledger.get("local:a")["title"])


class RecoveryJournalTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-ledger-journal-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.root = self.project / "brolls"
        self.root.mkdir()
        self.pending = self.root / ".pending-transaction.json"
        self.item = candidate("local", "a", "Um")
        self.data = {"schema_version": 1, "items": [self.item]}

    def _journal(self, events):
        raw = json.dumps({"data": self.data, "events": events})
        self.pending.write_text(raw, encoding="utf-8")
        return raw

    def test_a_journal_with_invalid_events_is_refused_and_kept(self):
        for label, events in {
            "events_is_text": "eventos",
            "event_is_a_number": [1],
            "transaction_is_a_number": [{"transaction": 7}],
            "transaction_missing": [{"operation": "resolve"}],
        }.items():
            with self.subTest(label):
                raw = self._journal(events)
                with self.assertRaisesRegex(ValueError, "Journal de recuperação inválido"):
                    Ledger(self.project)
                self.assertEqual(raw, self.pending.read_text(encoding="utf-8"))
                self.assertFalse((self.root / "manifest.json").exists(), "nada é gravado antes da recusa")
                self.assertFalse((self.root / "events.jsonl").exists())

    def test_a_journal_that_is_not_a_json_object_is_refused_and_kept(self):
        for label, raw in {"not_json": "{journal", "a_list": "[]", "a_text": '"journal"'}.items():
            with self.subTest(label):
                self.pending.write_text(raw, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Journal de recuperação inválido"):
                    Ledger(self.project)
                self.assertEqual(raw, self.pending.read_text(encoding="utf-8"))
                self.assertFalse((self.root / "manifest.json").exists(), "nada é gravado antes da recusa")

    def test_an_unreadable_events_log_stops_the_recovery_and_keeps_both_files(self):
        for label, history in {
            "not_json": '{"transaction": "t:0"}\nnão é json\n',
            "not_an_object": "[1, 2]\n",
        }.items():
            with self.subTest(label):
                events_log = self.root / "events.jsonl"
                events_log.write_text(history, encoding="utf-8")
                raw = self._journal([{"transaction": "novo:0", "operation": "resolve"}])
                with self.assertRaisesRegex(ValueError, "events.jsonl inválido. Histórico preservado"):
                    Ledger(self.project)
                self.assertEqual(raw, self.pending.read_text(encoding="utf-8"), "o journal fica para a próxima")
                self.assertEqual(history, events_log.read_text(encoding="utf-8"), "o histórico nunca é reescrito")

    def test_the_cli_reports_an_unreadable_events_log_as_invalid_data(self):
        events_log = self.root / "events.jsonl"
        events_log.write_text("não é json\n", encoding="utf-8")
        raw = self._journal([{"transaction": "novo:0", "operation": "resolve"}])

        error = run_cli("permit", "--candidate", "local:a", "--evidence", "evidência", project=self.project, expect=1)

        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertEqual("ValueError", error["type"])
        self.assertIn("events.jsonl inválido", error["error"])
        self.assertEqual(raw, self.pending.read_text(encoding="utf-8"))
        self.assertEqual("não é json\n", events_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
