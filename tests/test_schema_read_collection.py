"""Leitura que valida contra o schema (família do ledger): recusa limpa, forma de 2.5, custo e instalação quebrada.

Cada arquivo lê a versão do schema, recusa a versão mais nova com a frase de sempre (nunca um traceback) e
valida o documento; o manifesto é a exceção documentada: `validate_manifest` segue sendo a autoridade de
leitura dos itens (arquivos de 2.5 e edições à mão), e o schema do candidato vale para quem grava.
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
import _schema_latency
import _timing

# isort: split
from getbrolls import _paths as core_paths
from getbrolls import acquisition, library, queue, runtime
from getbrolls.errors import DataRootError
from getbrolls.ledger import Ledger
from getbrolls.models import candidate
from getbrolls.sdk import jsonschema, schemas

NEWER = "versão mais nova do getbrolls"
FIXTURE_V250 = Path(__file__).resolve().parent / "fixtures" / "golden_v25"


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-read-collection-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


class JournalTests(TempCase):
    def journal(self, **top):
        ledger = Ledger(self.tmp, recover=False)
        item = candidate("local", "um", "Um")
        doc = {"data": {"items": [item]}, "events": [{"transaction": "t:0"}], **top}
        self.write_json(ledger.pending, doc)
        return ledger.pending

    def test_a_newer_journal_is_refused_with_the_standard_phrase_and_kept(self):
        pending = self.journal(schema_version=schemas.version("pending_transaction") + 1)
        raw = pending.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ValueError, NEWER):
            Ledger(self.tmp)
        self.assertEqual(raw, pending.read_text(encoding="utf-8"))
        self.assertFalse((self.tmp / "brolls" / "manifest.json").exists())

    def test_a_journal_with_a_bad_version_is_the_usual_invalid_journal(self):
        for bad in (True, 1.0, "1", 0):
            with self.subTest(value=bad):
                self.journal(schema_version=bad)
                with self.assertRaisesRegex(ValueError, "Journal de recuperação inválido"):
                    Ledger(self.tmp)

    def test_a_journal_that_breaks_the_schema_is_refused_and_kept(self):
        pending = self.journal(events="nao e lista")
        with self.assertRaisesRegex(ValueError, "Journal de recuperação inválido"):
            Ledger(self.tmp)
        self.assertTrue(pending.exists())

    def test_a_journal_from_2_5_is_recovered(self):
        self.journal()
        self.assertEqual(["local:um"], [c["id"] for c in Ledger(self.tmp).data["items"]])


class ManifestReadTests(TempCase):
    def test_a_v250_style_manifest_with_loose_items_still_loads(self):
        item = candidate("local", "antigo", "Antigo")
        del item["schema_version"]
        item["campo_extra_de_2_5"] = {"qualquer": "coisa"}
        self.write_json(self.tmp / "brolls" / "manifest.json", {"project_id": 7, "items": [item]})
        ledger = Ledger(self.tmp)
        self.assertEqual("local:antigo", ledger.get("local:antigo")["id"])
        self.assertEqual(schemas.version("manifest"), ledger.data["schema_version"])
        # o item solto não passa no schema publicado do candidato: ele vale para quem grava, não para quem lê
        self.assertNotEqual([], jsonschema.errors(item, schemas.load("candidate")))

    def test_the_golden_v250_manifest_loads(self):
        found = sorted(FIXTURE_V250.rglob("manifest.json"))
        self.assertTrue(found, "o golden v250 tem manifestos")
        for path in found:
            with self.subTest(path=path.name):
                project = self.tmp / path.parent.parent.name
                self.write_json(project / "brolls" / "manifest.json", json.loads(path.read_text(encoding="utf-8")))
                Ledger(project, recover=False)

    def test_a_manifest_whose_items_are_not_a_list_is_refused_as_invalid(self):
        self.write_json(self.tmp / "brolls" / "manifest.json", {"items": {"a": 1}})
        with self.assertRaisesRegex(ValueError, "manifest.json inválido"):
            Ledger(self.tmp)

    def test_an_item_with_the_candidate_version_of_another_number_is_refused(self):
        item = candidate("local", "um", "Um")
        item["schema_version"] = schemas.version("candidate") + 1
        self.write_json(self.tmp / "brolls" / "manifest.json", {"items": [item]})
        with self.assertRaisesRegex(ValueError, "manifest.json inválido"):
            Ledger(self.tmp)


class QueueReadTests(TempCase):
    def path(self):
        return self.tmp / "work" / "queue.json"

    def test_an_item_that_is_not_an_object_is_a_clean_refusal_not_a_traceback(self):
        self.write_json(self.path(), {"items": ["texto"], "providers": {}})
        with self.assertRaisesRegex(ValueError, "queue.json incompatível"):
            queue.load(self.path())

    def test_a_status_that_is_not_text_is_refused(self):
        self.write_json(self.path(), {"items": [{"id": "x", "provider": "youtube", "status": 3}], "providers": {}})
        with self.assertRaisesRegex(ValueError, "queue.json incompatível"):
            queue.load(self.path())

    def test_version_one_loads_and_the_next_save_is_the_schema_version(self):
        self.write_json(self.path(), {"schema_version": 1, "items": [], "providers": {}})
        queue.save(self.path(), queue.load(self.path()))
        self.assertEqual(
            schemas.version("queue"), json.loads(self.path().read_text(encoding="utf-8"))["schema_version"]
        )


class LibraryReadTests(TempCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"GB_HOME": str(self.tmp), "GB_LIBRARY": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_every_malformed_index_is_refused_with_the_library_message(self):
        broken = (
            {"assets": "x"},
            {"assets": ["x"]},
            {"assets": [{"asset_id": "a"}]},
            {"assets": [{"asset_id": "a", "used_by": "x"}]},
            {"queries": [{"query": "q"}]},
            {"providers": {"youtube": {"outcomes": {}}}},
            {"providers": {"youtube": "x"}},
            {"preferences": {}},
        )
        for doc in broken:
            with self.subTest(doc=doc):
                self.write_json(library.index_path(), doc)
                with self.assertRaisesRegex(ValueError, "não é uma biblioteca"):
                    library.load_index()

    def test_a_well_formed_index_with_extra_fields_loads(self):
        doc = {"assets": [{"asset_id": "a", "used_by": [], "extra": 1}], "extra": True}
        self.write_json(library.index_path(), doc)
        self.assertEqual("a", library.load_index()["assets"][0]["asset_id"])


class SourceIndexReadTests(TempCase):
    def setUp(self):
        super().setUp()
        self.cache = self.tmp / ".getbrolls-sources"
        self.cache.mkdir()
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        self.warnings = event["warnings"]

    def test_a_newer_index_counts_as_empty_with_a_warning_and_is_not_renamed(self):
        path = self.write_json(
            self.cache / acquisition.INDEX_NAME,
            {"schema_version": schemas.version("source_index") + 1, "youtube:a": [{"sha": "x"}]},
        )
        self.assertEqual({}, acquisition._load_index(self.cache))  # pylint: disable=protected-access
        self.assertEqual(["SOURCE_INDEX_UNREADABLE"], [w["code"] for w in self.warnings])
        self.assertIn(NEWER, self.warnings[0]["message"])
        self.assertTrue(path.exists())

    def test_a_bad_version_counts_as_empty_with_a_warning(self):
        self.write_json(self.cache / acquisition.INDEX_NAME, {"schema_version": True, "youtube:a": []})
        self.assertEqual({}, acquisition._load_index(self.cache))  # pylint: disable=protected-access
        self.assertEqual(["SOURCE_INDEX_UNREADABLE"], [w["code"] for w in self.warnings])

    def test_the_version_key_is_never_an_entry_of_the_id_map(self):
        self.write_json(self.cache / acquisition.INDEX_NAME, {"schema_version": 1, "youtube:a": [{"sha": "x"}]})
        self.assertEqual({"youtube:a": [{"sha": "x"}]}, acquisition._load_index(self.cache))  # pylint: disable=protected-access
        self.assertEqual([], self.warnings)


class LatencyTests(TempCase):
    def test_a_large_manifest_validates_fast(self):
        item = candidate("local", "x", "Um")
        doc = _schema_latency.large_document({"schema_version": 1}, "items", item)
        _schema_latency.assert_validates_within(self, doc, schemas.load("manifest"))

    def test_a_large_queue_validates_fast(self):
        item = {
            "id": "youtube:x",
            "provider": "youtube",
            "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "status": "pending",
            "added_at": "2026-01-01T00:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "reason": None,
        }
        doc = _schema_latency.large_document(queue.empty_state(), "items", item)
        _schema_latency.assert_validates_within(self, doc, schemas.load("queue"))

    def test_a_large_manifest_loads_through_the_ledger_within_the_limit(self):
        items = [candidate("local", f"c{i}", f"Item {i}") for i in range(2000)]
        self.write_json(self.tmp / "brolls" / "manifest.json", {"items": items})
        best, _ = _timing.best_of(lambda: Ledger(self.tmp, recover=False), _schema_latency.DEFAULT_LIMIT_S)
        self.assertLess(best, _schema_latency.DEFAULT_LIMIT_S)


class BrokenDataRootTests(TempCase):
    """Instalação sem a pasta de schemas: a leitura recusa com o erro de pré-requisito, nunca com traceback."""

    def setUp(self):
        super().setUp()
        schemas.load.cache_clear()
        self.addCleanup(schemas.load.cache_clear)
        fake = core_paths.Install("wheel", self.tmp / "pkg", self.tmp / "pkg" / "_data", None)
        (self.tmp / "pkg" / "_data").mkdir(parents=True)
        patcher = mock.patch.object(core_paths, "install", return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_queue_load_is_a_data_root_error(self):
        path = self.write_json(self.tmp / "work" / "queue.json", {"items": [], "providers": {}})
        with self.assertRaisesRegex(DataRootError, "reinstale|Reinstale"):
            queue.load(path)

    def test_opening_a_ledger_is_a_data_root_error(self):
        self.write_json(self.tmp / "brolls" / "manifest.json", {"items": []})
        with self.assertRaises(DataRootError):
            Ledger(self.tmp, recover=False)

    def test_the_library_load_is_a_data_root_error(self):
        env = mock.patch.dict(os.environ, {"GB_HOME": str(self.tmp / "home"), "GB_LIBRARY": ""})
        env.start()
        self.addCleanup(env.stop)
        self.write_json(library.index_path(), {"assets": []})
        with self.assertRaises(DataRootError):
            library.load_index()


if __name__ == "__main__":
    unittest.main()
