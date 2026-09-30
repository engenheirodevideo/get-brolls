"""`schema_version` tolerante em todo JSON persistido que já existia antes do campo.

Para cada arquivo: um arquivo antigo, sem o campo, carrega; a versão 2 é recusada com
a frase de "versão mais nova"; `True` e `1.0` não passam pelo inteiro 1; e toda
gravação nova declara `schema_version: 1`.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import export_folder, library, memory, queue, roteiro_ids, versioning
from getbrolls.ledger import Ledger
from getbrolls.models import candidate

NEWER = "versão mais nova do get-brolls"


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-schema-version-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


class ReadVersionTests(unittest.TestCase):
    def test_absent_means_one(self):
        self.assertEqual(1, versioning.read_version({}, "x.json"))

    def test_supported_value_is_returned(self):
        self.assertEqual(2, versioning.read_version({"schema_version": 2}, "x.json", supported=2))

    def test_newer_is_refused_with_the_standard_phrase(self):
        with self.assertRaises(ValueError) as ctx:
            versioning.read_version({"schema_version": 2}, "x.json")
        self.assertEqual(
            "x.json foi gravado por uma versão mais nova do get-brolls (schema_version 2); "
            "atualize antes de continuar.",
            str(ctx.exception),
        )

    def test_bool_float_text_and_zero_are_invalid_not_newer(self):
        for bad in (True, 1.0, "1", 0, -1, None):
            with self.subTest(value=bad), self.assertRaises(ValueError) as ctx:
                versioning.read_version({"schema_version": bad}, "x.json")
            self.assertIn("x.json é incompatível", str(ctx.exception))
            self.assertNotIn(NEWER, str(ctx.exception))

    def test_caller_message_replaces_the_invalid_one_only(self):
        with self.assertRaisesRegex(ValueError, "^meu erro$"):
            versioning.read_version({"schema_version": "1"}, "x.json", invalid="meu erro")
        with self.assertRaisesRegex(ValueError, NEWER):
            versioning.read_version({"schema_version": 3}, "x.json", invalid="meu erro")

    def test_stamp_puts_the_version_first_and_copies(self):
        data = {"items": [], "schema_version": 7}
        stamped = versioning.stamp(data)
        self.assertEqual(["schema_version", "items"], list(stamped))
        self.assertEqual(1, stamped["schema_version"])
        self.assertEqual(7, data["schema_version"])


class ManifestTests(TempDirTestCase):
    def manifest(self, **top):
        item = candidate("local", "legacy", "Antigo")
        return self.write_json(self.tmp / "brolls" / "manifest.json", {**top, "items": [item]})

    def test_old_file_without_the_field_loads_and_is_written_with_it(self):
        path = self.manifest()
        ledger = Ledger(self.tmp)
        self.assertEqual(1, len(ledger.data["items"]))
        ledger.save("test")
        self.assertEqual(1, json.loads(path.read_text(encoding="utf-8"))["schema_version"])

    def test_version_two_is_refused_as_newer(self):
        self.manifest(schema_version=2)
        with self.assertRaisesRegex(ValueError, NEWER):
            Ledger(self.tmp)

    def test_bool_or_float_is_refused(self):
        for bad in (True, 1.0):
            with self.subTest(value=bad):
                self.manifest(schema_version=bad)
                with self.assertRaisesRegex(ValueError, "manifest.json inválido"):
                    Ledger(self.tmp)


class ReferencesTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.ledger = Ledger(self.tmp)
        self.c = candidate("local", "ref", "Referência")
        self.ledger.add(self.c)
        self.path = self.ledger.root / "references.json"

    def remember(self):
        return memory.remember(self.ledger, self.c, "rejected", "não serve", "Bruno")

    def test_old_file_keeps_its_items_and_gains_the_version(self):
        old = {"items": [{"id": "local:velho", "decision": "approved", "reason": "bom", "by": "Bruno"}]}
        self.path.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        self.remember()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(["schema_version", "items"], list(data))
        self.assertEqual(1, data["schema_version"])
        self.assertEqual(old["items"][0], data["items"][0])
        self.assertEqual("local:ref", data["items"][1]["id"])

    def test_new_file_is_written_with_the_version(self):
        self.remember()
        self.assertEqual(1, json.loads(self.path.read_text(encoding="utf-8"))["schema_version"])

    def test_version_two_is_refused_as_newer_and_left_untouched(self):
        raw = json.dumps({"schema_version": 2, "items": []})
        self.path.write_text(raw, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, NEWER):
            self.remember()
        with self.assertRaisesRegex(ValueError, NEWER):
            memory.load_references(self.ledger.root)
        self.assertEqual(raw, self.path.read_text(encoding="utf-8"))

    def test_bool_or_float_is_refused(self):
        for bad in (True, 1.0):
            with self.subTest(value=bad):
                self.path.write_text(json.dumps({"schema_version": bad, "items": []}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "references.json é incompatível"):
                    memory.load_references(self.ledger.root)


class RoteiroStateTests(TempDirTestCase):
    def path(self):
        return roteiro_ids.state_path(self.tmp)

    def test_old_file_without_the_field_loads(self):
        self.write_json(self.path(), {"next_id": 4, "scenes": {}})
        self.assertEqual(4, roteiro_ids.read_state(self.tmp)["next_id"])

    def test_version_two_is_refused_as_newer(self):
        self.write_json(self.path(), {"schema_version": 2, "next_id": 4, "scenes": {}})
        with self.assertRaisesRegex(ValueError, NEWER):
            roteiro_ids.read_state(self.tmp)

    def test_bool_or_float_is_refused(self):
        for bad in (True, 1.0):
            with self.subTest(value=bad):
                self.write_json(self.path(), {"schema_version": bad, "next_id": 4, "scenes": {}})
                with self.assertRaisesRegex(ValueError, "ilegível"):
                    roteiro_ids.read_state(self.tmp)

    def test_write_declares_the_version(self):
        roteiro_ids.write_state(self.tmp, {"next_id": 2, "scenes": {}})
        self.assertEqual(1, json.loads(self.path().read_text(encoding="utf-8"))["schema_version"])
        self.assertEqual(2, roteiro_ids.read_state(self.tmp)["next_id"])


class QueueTests(TempDirTestCase):
    def path(self):
        return self.tmp / "work" / "queue.json"

    def test_old_file_without_the_field_loads_and_is_written_with_it(self):
        self.write_json(self.path(), {"items": [], "providers": {}})
        data = queue.load(self.path())
        queue.save(self.path(), data)
        self.assertEqual(1, json.loads(self.path().read_text(encoding="utf-8"))["schema_version"])

    def test_version_two_is_refused_as_newer(self):
        self.write_json(self.path(), {"schema_version": 2, "items": [], "providers": {}})
        with self.assertRaisesRegex(ValueError, NEWER):
            queue.load(self.path())

    def test_bool_or_float_is_refused(self):
        for bad in (True, 1.0):
            with self.subTest(value=bad):
                self.write_json(self.path(), {"schema_version": bad, "items": [], "providers": {}})
                with self.assertRaisesRegex(ValueError, "incompatível"):
                    queue.load(self.path())


class LibraryIndexTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"GB_HOME": str(self.tmp), "GB_LIBRARY": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_old_file_without_the_field_loads_and_is_written_with_it(self):
        self.write_json(library.index_path(), {"assets": [], "queries": [], "providers": {}, "preferences": []})
        data = library.load_index()
        library.save_index(data)
        self.assertEqual(1, json.loads(library.index_path().read_text(encoding="utf-8"))["schema_version"])

    def test_version_two_is_refused_as_newer(self):
        self.write_json(library.index_path(), {"schema_version": 2})
        with self.assertRaisesRegex(ValueError, NEWER):
            library.load_index()

    def test_bool_or_float_is_refused(self):
        for bad in (True, 1.0):
            with self.subTest(value=bad):
                self.write_json(library.index_path(), {"schema_version": bad})
                with self.assertRaisesRegex(ValueError, "não é uma biblioteca"):
                    library.load_index()


class ExportMarkerTests(TempDirTestCase):
    def test_marker_declares_the_version(self):
        body = json.loads(export_folder._marker_bytes({}, "complete", 1, {}))  # pylint: disable=protected-access
        self.assertEqual(1, body["schema_version"])

    def test_base_never_overrides_the_marker_version(self):
        raw = export_folder._marker_bytes({"schema_version": 9}, "complete", 1, {})  # pylint: disable=protected-access
        self.assertEqual(1, json.loads(raw)["schema_version"])

    def test_old_marker_without_the_field_is_still_read(self):
        path = self.write_json(self.tmp / "m.json", {"marker": export_folder.MARKER_ID, "state": "complete"})
        self.assertIsNotNone(export_folder._read_marker(path))  # pylint: disable=protected-access


if __name__ == "__main__":
    unittest.main()
