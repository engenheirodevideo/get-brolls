"""Ida e volta por família (acervo): o que o escritor grava vale o que o schema declara e valida contra ele.

Famílias: manifest, candidate, references, roteiro_state, pending_transaction, queue, library_index e
source_index. Para cada uma, o teste grava com o escritor real, confere que a versão gravada é
`schemas.version(nome)` e que o documento passa em `jsonschema.errors(doc, schemas.load(nome))`; depois
um arquivo de 2.5 (sem o campo de versão) ainda lê como versão 1.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

# isort: split
from getbrolls import acquisition, library, memory, queue, roteiro_ids
from getbrolls.ledger import Ledger
from getbrolls.models import candidate
from getbrolls.sdk import jsonschema, schemas


class RoundTripCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-roundtrip-collection-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def read(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def assert_round_trip(self, name, doc):
        """A versão do documento é a do schema e o documento passa no schema."""
        self.assertEqual(schemas.version(name), doc[schemas.version_field(name)], name)
        self.assertEqual([], jsonschema.errors(doc, schemas.load(name)), name)


class LedgerRoundTrip(RoundTripCase):
    def saved(self):
        ledger = Ledger(self.tmp)
        ledger.add(candidate("local", "um", "Um"))
        ledger.save("test", ledger.get("local:um"))
        return ledger

    def test_manifest_and_candidate_files_carry_the_schema_version(self):
        ledger = self.saved()
        manifest = self.read(ledger.path)
        self.assert_round_trip("manifest", manifest)
        self.assertEqual(1, schemas.version("manifest"))
        for item in manifest["items"]:
            self.assert_round_trip("candidate", item)
        for path in (ledger.root / "candidates").glob("*.json"):
            self.assert_round_trip("candidate", self.read(path))

    def test_an_empty_ledger_starts_at_the_schema_version(self):
        self.assertEqual({"schema_version": schemas.version("manifest"), "items": []}, Ledger(self.tmp).data)

    def test_the_journal_carries_the_schema_version_and_validates(self):
        ledger = Ledger(self.tmp)
        ledger.add(candidate("local", "um", "Um"))
        with mock.patch.object(Ledger, "_finish_transaction"):
            ledger.save("test", ledger.get("local:um"))
        self.assert_round_trip("pending_transaction", self.read(ledger.pending))

    def test_a_journal_from_2_5_without_the_version_still_recovers(self):
        ledger = Ledger(self.tmp)
        ledger.add(candidate("local", "um", "Um"))
        with mock.patch.object(Ledger, "_finish_transaction"):
            ledger.save("test", ledger.get("local:um"))
        journal = self.read(ledger.pending)
        del journal["schema_version"]
        ledger.pending.write_text(json.dumps(journal), encoding="utf-8")
        recovered = Ledger(self.tmp)
        self.assertEqual(["local:um"], [c["id"] for c in recovered.data["items"]])
        self.assertFalse(ledger.pending.exists())


class MemoryRoundTrip(RoundTripCase):
    def test_references_are_written_with_the_schema_version(self):
        ledger = Ledger(self.tmp)
        c = candidate("local", "ref", "Referência")
        ledger.add(c)
        memory.remember(ledger, c, "rejected", "não serve", "Bruno")
        data = self.read(ledger.root / memory.REFERENCES_FILE)
        self.assert_round_trip("references", data)
        self.assertEqual(1, len(data["items"]))

    def test_references_from_2_5_without_the_version_read_as_version_1(self):
        ledger = Ledger(self.tmp)
        (ledger.root / memory.REFERENCES_FILE).write_text(json.dumps({"items": []}), encoding="utf-8")
        self.assertEqual({"items": []}, memory.load_references(ledger.root))


class RoteiroStateRoundTrip(RoundTripCase):
    def test_state_is_written_with_the_schema_version(self):
        scenes = {"c001": {"title": "Abertura", "layout": "tela", "args": []}}
        roteiro_ids.write_state(self.tmp, {"next_id": 2, "scenes": scenes})
        self.assert_round_trip("roteiro_state", self.read(roteiro_ids.state_path(self.tmp)))

    def test_state_from_2_5_without_the_version_reads_as_version_1(self):
        path = roteiro_ids.state_path(self.tmp)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"next_id": 3, "scenes": {}}), encoding="utf-8")
        self.assertEqual({"next_id": 3, "scenes": {}}, roteiro_ids.read_state(self.tmp))


class QueueRoundTrip(RoundTripCase):
    def test_queue_is_written_with_the_schema_version(self):
        path = self.tmp / "work" / "queue.json"
        data = queue.empty_state()
        queue.add(data, "youtube", ["https://www.youtube.com/watch?v=abcdefghijk"])
        queue.save(path, data)
        doc = self.read(path)
        self.assert_round_trip("queue", doc)
        self.assertEqual(2, schemas.version("queue"))
        self.assertEqual(1, len(doc["items"]))
        self.assertEqual(doc, queue.load(path))

    def test_queue_from_2_5_without_the_version_reads_as_version_1(self):
        path = self.tmp / "work" / "queue.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"items": [], "providers": {}}), encoding="utf-8")
        self.assertEqual({"items": [], "providers": {}}, queue.load(path))


class LibraryRoundTrip(RoundTripCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"GB_HOME": str(self.tmp), "GB_LIBRARY": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_index_is_written_with_the_schema_version(self):
        data = library.load_index()
        data["assets"].append({"asset_id": "a1", "used_by": []})
        data["providers"]["youtube"] = {"outcomes": {"hit": 1}, "last_at": None}
        library.save_index(data)
        doc = self.read(library.index_path())
        self.assert_round_trip("library_index", doc)
        self.assertEqual(2, schemas.version("library_index"))

    def test_the_empty_index_starts_at_the_schema_version(self):
        self.assertEqual(schemas.version("library_index"), library.load_index()["schema_version"])

    def test_index_from_2_5_without_the_version_reads_as_version_1(self):
        library.index_path().parent.mkdir(parents=True)
        library.index_path().write_text(json.dumps({"assets": []}), encoding="utf-8")
        self.assertEqual([], library.load_index()["assets"])


class SourceIndexRoundTrip(RoundTripCase):
    def test_index_is_written_with_the_schema_version_and_read_without_it(self):
        entry = {"path": "x.mp4", "sha": "abc", "start": 0.0, "duration": 4.0}
        acquisition._save_index(self.tmp, {"youtube:abc": [entry]})  # pylint: disable=protected-access
        doc = self.read(self.tmp / acquisition.INDEX_NAME)
        self.assert_round_trip("source_index", doc)
        self.assertEqual({"youtube:abc": [entry]}, acquisition._load_index(self.tmp))  # pylint: disable=protected-access

    def test_index_from_2_5_without_the_version_reads_as_version_1(self):
        (self.tmp / acquisition.INDEX_NAME).write_text(json.dumps({"youtube:abc": [{"sha": "x"}]}), encoding="utf-8")
        self.assertEqual({"youtube:abc": [{"sha": "x"}]}, acquisition._load_index(self.tmp))  # pylint: disable=protected-access


if __name__ == "__main__":
    unittest.main()
