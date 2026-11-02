"""Leitura do arquivo de decisões do `import-review` pela CLI real: JSON hostil é INVALID_DATA, nunca INTERNAL_ERROR.

Aninhamento fundo, `NaN`/`Infinity` e escape de surrogate sozinho caem na mesma recusa de dados dos
outros arquivos inválidos (saída 1, `error_code` INVALID_DATA, mensagem em português) e não gravam nada.
Uma pasta ou um FIFO no lugar do arquivo é problema de E/S (IO_ERROR), como era antes da leitura única.
"""

import json
import os
import tempfile
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_trail_review_cli import exported, project_with, save_review

from getbrolls.ledger import Ledger


class ReviewDecisionsReadTests(unittest.TestCase):
    def _refused(self, raw=None, tamper=None):
        """Grava o arquivo de decisões e devolve o erro do `import-review` e se o manifesto ficou igual.

        O arquivo é `raw` ou, com `tamper`, o JSON válido que a página salvaria depois de adulterado."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            path = save_review(ledger, exported(ledger))
            path.write_bytes(tamper(path.read_bytes()) if tamper is not None else raw or b"")
            before = ledger.path.read_bytes()
            error = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp, expect=1)
            return error, before == ledger.path.read_bytes()

    def _assert_invalid_data(self, raw=None, tamper=None):
        error, untouched = self._refused(raw, tamper)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("arquivo de escolhas", error["error"])
        self.assertNotIn("Traceback", error["error"])
        self.assertTrue(untouched)

    def test_deeply_nested_file_is_invalid_data_not_an_internal_error(self):
        self._assert_invalid_data(b"[" * 1_500_000)

    def test_nested_object_past_the_depth_limit_is_invalid_data(self):
        self._assert_invalid_data(b'{"type": "getbrolls-review", "items": ' + b"[" * 200 + b"]" * 200 + b"}")

    def test_nan_and_infinity_in_an_otherwise_valid_file_are_invalid_data(self):
        for constant in (b"NaN", b"Infinity", b"-Infinity", b"1e400"):
            with self.subTest(constant=constant):
                self._assert_invalid_data(
                    tamper=lambda raw, c=constant: raw.replace(b'"start_s": 0', b'"start_s": ' + c, 1)
                )

    def test_lone_surrogate_escape_in_an_otherwise_valid_file_is_invalid_data(self):
        self._assert_invalid_data(
            tamper=lambda raw: raw.replace(b'"state": "approved"', b'"state": "approved", "comment": "\\ud800"', 1)
        )

    def test_malformed_json_keeps_the_invalid_data_refusal(self):
        self._assert_invalid_data(b"{not valid json")

    def test_a_directory_in_place_of_the_file_is_an_io_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            folder = ledger.root / "reviews" / "decisions.json"
            folder.mkdir(parents=True)
            error = run_cli("import-review", "--file", str(folder), "--by", "Ana", project=tmp, expect=1)
            self.assertEqual("IO_ERROR", error["error_code"])
            self.assertIn("não é um arquivo comum", error["error"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO só existe em POSIX")
    def test_a_fifo_in_place_of_the_file_is_an_io_error_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            fifo = ledger.root / "reviews" / "decisions.json"
            fifo.parent.mkdir(parents=True, exist_ok=True)
            os.mkfifo(fifo)
            error = run_cli("import-review", "--file", str(fifo), "--by", "Ana", project=tmp, expect=1)
            self.assertEqual("IO_ERROR", error["error_code"])
            self.assertIn("não é um arquivo comum", error["error"])

    def test_a_valid_file_still_imports_the_same_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project_with(tmp, "one")
            payload = exported(ledger)
            payload["items"][0]["comment"] = "Plano aberto, ótimo — 🚀"
            path = save_review(ledger, payload)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            result = run_cli("import-review", "--file", str(path), "--by", "Ana", project=tmp)
            self.assertEqual(1, result["imported"])
            self.assertEqual([], result["skipped"])
            fresh = Ledger(tmp).get("local:one")
            self.assertEqual("approved", fresh["approval"]["status"])
            self.assertEqual("Plano aberto, ótimo — 🚀", fresh["review"]["comment"])


if __name__ == "__main__":
    unittest.main()
