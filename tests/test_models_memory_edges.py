"""Recusas de `models.approve`/`require_fetch` e de `memory` que ficavam sem teste."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import memory
from getbrolls.ledger import Ledger
from getbrolls.models import approve, candidate, require_fetch, set_segment


def segmented():
    c = candidate("local", "m", "Modelo")
    set_segment(c, 1.0, 3.0)
    return c


class SetSegmentTests(unittest.TestCase):
    def test_interval_past_the_video_duration_is_refused_and_keeps_the_segment(self):
        c = candidate("local", "dur", "Duração")
        c["media"]["duration_s"] = 5.0
        set_segment(c, 1.0, 5.05)  # até 0,1 s de folga
        with self.assertRaisesRegex(ValueError, "excede a duração"):
            set_segment(c, 1.0, 5.2)
        self.assertEqual((1.0, 5.05), (c["segment"]["start_s"], c["segment"]["end_s"]))

    def test_non_finite_negative_or_reversed_interval_is_refused(self):
        for start, end in ((float("nan"), 2.0), (0.0, float("inf")), (-1.0, 2.0), (3.0, 3.0), (4.0, 2.0)):
            with self.subTest(start=start, end=end):
                c = candidate("local", "iv", "Intervalo")
                with self.assertRaisesRegex(ValueError, "Intervalo inválido"):
                    set_segment(c, start, end)
                self.assertIsNone(c["segment"]["start_s"])


class ApproveRefusalTests(unittest.TestCase):
    def test_video_without_a_chosen_interval_cannot_be_approved(self):
        c = candidate("local", "sem-intervalo", "Sem intervalo")
        with self.assertRaisesRegex(ValueError, "selecione um intervalo antes de aprovar"):
            approve(c, "Bruno")
        self.assertEqual("pending", c["approval"]["status"])
        self.assertEqual("candidate", c["state"])

    def test_image_needs_no_interval_to_be_approved(self):
        c = candidate("local", "imagem", "Imagem")
        c["media"]["kind"] = "image"
        approve(c, "Bruno")
        self.assertEqual(("approved", "approved"), (c["approval"]["status"], c["state"]))

    def test_blank_approver_is_refused_and_nothing_changes(self):
        c = segmented()
        with self.assertRaisesRegex(ValueError, "Informe quem aprovou"):
            approve(c, "   ")
        self.assertEqual("pending", c["approval"]["status"])
        self.assertEqual("awaiting_approval", c["state"])

    def test_unknown_channel_is_refused(self):
        c = segmented()
        with self.assertRaisesRegex(ValueError, "Canal de aprovação inválido"):
            approve(c, "Bruno", channel="email")
        self.assertEqual("pending", c["approval"]["status"])

    def test_non_string_statement_is_refused(self):
        c = segmented()
        with self.assertRaisesRegex(ValueError, "Frase de aprovação inválida"):
            approve(c, "Bruno", channel="chat", statement=42)
        self.assertEqual("pending", c["approval"]["status"])

    def test_chat_approval_needs_a_non_blank_statement(self):
        for statement in (None, "   "):
            with self.subTest(statement=statement):
                c = segmented()
                with self.assertRaisesRegex(ValueError, "exige --statement"):
                    approve(c, "Bruno", channel="chat", statement=statement)
                self.assertEqual("pending", c["approval"]["status"])

    def test_chat_approval_with_statement_is_recorded(self):
        c = approve(segmented(), "Bruno", channel="chat", statement="pode usar")
        self.assertEqual("approved", c["state"])
        self.assertEqual("pode usar", c["approval"]["statement"])
        self.assertEqual("chat", c["approval"]["channel"])


class RequireFetchTests(unittest.TestCase):
    def test_pending_approval_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Aprovação humana ausente"):
            require_fetch(segmented())

    def test_stale_signature_is_refused_after_the_segment_moves(self):
        c = approve(segmented(), "Bruno")
        c["segment"]["end_s"] = 9.0
        with self.assertRaisesRegex(ValueError, "Aprovação humana ausente"):
            require_fetch(c)

    def test_rights_without_evidence_are_refused(self):
        c = approve(segmented(), "Bruno")
        c["rights"]["status"] = "permitted"
        with self.assertRaisesRegex(ValueError, "Registre a autorização"):
            require_fetch(c)

    def test_reference_only_source_is_refused(self):
        c = approve(segmented(), "Bruno")
        c["rights"].update(status="permitted", evidence=["licença CC0"])
        with self.assertRaisesRegex(ValueError, "somente referência"):
            require_fetch(c)

    def test_available_permitted_approved_source_passes(self):
        c = approve(segmented(), "Bruno")
        c["rights"].update(status="permitted", evidence=["licença CC0"])
        c["acquisition"]["status"] = "available"
        require_fetch(c)


class MemoryRefusalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-memory-edges-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = Ledger(self.tmp)
        self.c = candidate("local", "mem", "Memória")
        self.ledger.add(self.c)
        self.path = self.ledger.root / "references.json"

    def test_non_object_references_file_is_refused_and_left_untouched(self):
        self.path.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "references.json é incompatível: esperado um objeto JSON"):
            memory.load_references(self.ledger.root)
        with self.assertRaisesRegex(ValueError, "esperado um objeto JSON"):
            memory.remember(self.ledger, self.c, "rejected", "não serve", "Bruno")
        self.assertEqual("[1, 2]", self.path.read_text(encoding="utf-8"))

    def test_blank_reason_or_owner_is_refused_and_nothing_is_written(self):
        for reason, by in (("  ", "Bruno"), ("não serve", ""), ("", "  ")):
            with self.subTest(reason=reason, by=by):
                with self.assertRaisesRegex(ValueError, "exige motivo e responsável"):
                    memory.remember(self.ledger, self.c, "rejected", reason, by)
                self.assertFalse(self.path.exists())

    def test_positive_reference_needs_a_valid_approval(self):
        with self.assertRaisesRegex(ValueError, "Aprove este insert"):
            memory.remember(self.ledger, self.c, "approved", "bom", "Bruno")
        self.assertFalse(self.path.exists())

    def test_rejected_reference_is_recorded_without_approval(self):
        entry = memory.remember(self.ledger, self.c, "rejected", "não serve", "Bruno")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([entry], data["items"])
        self.assertEqual("rejected", entry["decision"])


if __name__ == "__main__":
    unittest.main()
