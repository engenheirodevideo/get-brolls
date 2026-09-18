"""Aprovação humana pelo chat: canal, frase literal, lote e compatibilidade do board."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/gb.py"
sys.path.insert(0, str(ROOT / "scripts"))

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)

from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment, signature
from getbrolls.review import (
    import_review,
    legacy_review_epoch,
    project_id,
    review_epoch,
)


def run_cli(test, *args, ok=True):
    done = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    test.assertEqual(0 if ok else 2, done.returncode, done.stderr)
    return json.loads(done.stdout if ok else done.stderr)


def fixture(root):
    """Dois candidatos com prévia, um sem prévia e um já aprovado e válido."""
    ledger = Ledger(root)
    ready_one = candidate("local", "a", "Com prévia 1")
    set_segment(ready_one, 0, 1)
    ready_one["preview"]["gif_path"] = "previews/a.gif"

    ready_two = candidate("local", "b", "Com prévia 2")
    set_segment(ready_two, 0, 2)
    ready_two["preview"]["contact_sheet_path"] = "previews/b.png"

    no_preview = candidate("local", "c", "Sem prévia")
    set_segment(no_preview, 0, 3)

    for item in (ready_one, ready_two, no_preview):
        ledger.add(item)
    ledger.save("fixture")
    return ledger


class ApproveChatTests(unittest.TestCase):
    def test_candidate_repeats_and_approves_exactly_those_ids(self):
        """Fricção 2 da rodada 2: `--all` aprovou um descarte que ainda tinha prévia."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = fixture(tmp)
            discarded = candidate("local", "d", "Descartado, mas com prévia em disco")
            set_segment(discarded, 0, 4)
            discarded["preview"]["gif_path"] = "previews/d.gif"
            ledger.add(discarded)
            ledger.save("fixture")
            result = run_cli(
                self,
                "approve",
                "--candidate",
                "local:a",
                "--candidate",
                "local:b",
                "--by",
                "Bruno",
                "--statement",
                "Aprovo esses dois que você me mostrou.",
                "--project",
                tmp,
            )
            self.assertEqual(["local:a", "local:b"], sorted(result["approved"]))
            self.assertEqual([], result["skipped"])
            fresh = Ledger(tmp)
            self.assertEqual("approved", fresh.get("local:a")["approval"]["status"])
            self.assertEqual("approved", fresh.get("local:b")["approval"]["status"])
            # O que o agente não citou continua sem decisão, mesmo tendo prévia.
            self.assertEqual("pending", fresh.get("local:d")["approval"]["status"])

    def test_a_single_candidate_keeps_the_item_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            result = run_cli(
                self,
                "approve",
                "--candidate",
                "local:a",
                "--by",
                "Bruno",
                "--statement",
                "Aprovo o primeiro.",
                "--project",
                tmp,
            )
            # Sem `--start/--end`: aprovar confirma o intervalo que a pessoa viu.
            self.assertEqual("local:a", result["id"])
            self.assertEqual("approved", result["approval"]["status"])
            self.assertEqual(1, result["segment"]["revision"])

    def test_an_unknown_id_in_the_list_is_an_error_not_a_silent_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            failed = run_cli(
                self,
                "approve",
                "--candidate",
                "local:a",
                "--candidate",
                "local:inexistente",
                "--by",
                "Bruno",
                "--statement",
                "Aprovo os dois.",
                "--project",
                tmp,
                ok=False,
            )
            self.assertIn("local:inexistente", json.dumps(failed, ensure_ascii=False))
            self.assertEqual("pending", Ledger(tmp).get("local:a")["approval"]["status"])

    def test_approve_all_warns_in_portuguese_naming_what_it_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            result = run_cli(
                self,
                "approve",
                "--all",
                "--by",
                "Bruno",
                "--statement",
                "Aprovei todos.",
                "--project",
                tmp,
            )
            warnings = [w for w in result.get("warnings", []) if w["code"] == "APPROVE_ALL_WIDE"]
            self.assertEqual(1, len(warnings), result.get("warnings"))
            message = warnings[0]["message"]
            self.assertIn("local:a", message)
            self.assertIn("local:b", message)
            self.assertIn("descartou sem rejeitar", message)
            self.assertIn("--candidate", message)

    def test_approve_all_covers_previewed_items_and_lists_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            result = run_cli(
                self,
                "approve",
                "--all",
                "--by",
                "Bruno",
                "--statement",
                "Aprovo os dois primeiros trechos.",
                "--project",
                tmp,
            )
            self.assertEqual(["local:a", "local:b"], sorted(result["approved"]))
            self.assertEqual(["local:c"], [item["id"] for item in result["skipped"]])
            self.assertIn("prévia", result["skipped"][0]["reason"])
            self.assertIn("Registrei", result["summary"]["line"])

            ledger = Ledger(tmp)
            approval = ledger.get("local:a")["approval"]
            self.assertEqual("approved", approval["status"])
            self.assertEqual("chat", approval["channel"])
            self.assertEqual("Aprovo os dois primeiros trechos.", approval["statement"])
            self.assertEqual("pending", ledger.get("local:c")["approval"]["status"])

            # Rodar de novo não reaprova o que já tem aprovação válida.
            again = run_cli(
                self,
                "approve",
                "--all",
                "--by",
                "Bruno",
                "--statement",
                "Aprovo os dois primeiros trechos.",
                "--project",
                tmp,
            )
            self.assertEqual([], again["approved"])
            self.assertEqual(
                ["local:a", "local:b", "local:c"],
                sorted(item["id"] for item in again["skipped"]),
            )

    def test_events_distinguish_chat_from_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            run_cli(
                self,
                "approve",
                "--candidate",
                "local:a",
                "--start",
                0,
                "--end",
                1,
                "--by",
                "Bruno",
                "--channel",
                "chat",
                "--statement",
                "Pode aprovar o trecho a.",
                "--project",
                tmp,
            )
            run_cli(
                self,
                "approve",
                "--candidate",
                "local:b",
                "--start",
                0,
                "--end",
                2,
                "--by",
                "Bruno",
                "--channel",
                "storyboard",
                "--project",
                tmp,
            )
            events = [
                json.loads(line)
                for line in (Path(tmp) / "brolls" / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            operations = [event["operation"] for event in events]
            self.assertIn("approve-chat", operations)
            self.assertIn("approve", operations)
            ledger = Ledger(tmp)
            self.assertEqual("storyboard", ledger.get("local:b")["approval"]["channel"])
            self.assertIsNone(ledger.get("local:b")["approval"]["statement"])

    def test_approve_all_rejects_candidate_and_interval_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            error = run_cli(
                self,
                "approve",
                "--all",
                "--candidate",
                "local:a",
                "--by",
                "Bruno",
                "--statement",
                "Aprovo.",
                "--project",
                tmp,
                ok=False,
            )
            self.assertIn("--all", error["error"])
            missing = run_cli(self, "approve", "--by", "Bruno", "--statement", "Aprovo.", "--project", tmp, ok=False)
            self.assertIn("--candidate", missing["error"])


class RejectManyTests(unittest.TestCase):
    """`reject` aceita a flag repetida: descartar 37 itens um a um era o atrito."""

    def test_candidate_repeats_and_rejects_every_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            result = run_cli(
                self,
                "reject",
                "--candidate",
                "local:a",
                "--candidate",
                "local:b",
                "--project",
                tmp,
            )
            self.assertEqual(["local:a", "local:b"], result["rejected"])
            self.assertIn("Rejeitei 2 itens", result["summary"]["line"])
            ledger = Ledger(tmp)
            for ident in ("local:a", "local:b"):
                self.assertEqual("rejected", ledger.get(ident)["approval"]["status"])
                self.assertEqual("rejected", ledger.get(ident)["state"])
            # Quem não foi nomeado continua intacto.
            self.assertNotEqual("rejected", ledger.get("local:c")["state"])

    def test_a_single_candidate_keeps_the_old_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            result = run_cli(self, "reject", "--candidate", "local:a", "--project", tmp)
            self.assertEqual("local:a", result["id"])
            self.assertEqual("rejected", result["state"])
            self.assertIn("Rejeitei local:a", result["summary"]["line"])

    def test_an_unknown_id_stops_the_batch_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            error = run_cli(
                self,
                "reject",
                "--candidate",
                "local:a",
                "--candidate",
                "local:nao-existe",
                "--project",
                tmp,
                ok=False,
            )
            self.assertIn("local:nao-existe", error["error"])
            # Validação antes da escrita: o item válido da mesma leva não mudou.
            self.assertNotEqual("rejected", Ledger(tmp).get("local:a")["state"])

    def test_the_batch_is_one_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            run_cli(self, "reject", "--candidate", "local:a", "--candidate", "local:b", "--project", tmp)
            events = [
                json.loads(line)
                for line in (Path(tmp) / "brolls" / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            batch = [e for e in events if e["operation"] == "reject"]
            self.assertEqual(["local:a", "local:b"], [e["id"] for e in batch])
            self.assertEqual(1, len({e["transaction"].split(":")[0] for e in batch}))

    def test_reject_still_requires_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            done = subprocess.run(
                [sys.executable, str(CLI), "reject", "--project", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertNotEqual(0, done.returncode)
            self.assertIn("--candidate", done.stderr)


class ApprovedItemsCarryTheAuditTrailTests(unittest.TestCase):
    """A saída precisa dizer o que foi aprovado, não só quantos itens."""

    def test_approve_all_lists_contact_sheet_and_segment_per_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            result = run_cli(
                self,
                "approve",
                "--all",
                "--by",
                "Bruno Moreira",
                "--statement",
                "Aprovo os dois que você mostrou.",
                "--project",
                tmp,
            )
            rows = {row["id"]: row for row in result["approved_items"]}
            self.assertEqual(sorted(result["approved"]), sorted(rows))
            self.assertEqual("previews/b.png", rows["local:b"]["contact_sheet"])
            # Sem folha de contato o campo existe e diz `null`, nunca some.
            self.assertIsNone(rows["local:a"]["contact_sheet"])
            self.assertEqual(
                {"start_s": 0, "end_s": 2, "revision": 1},
                rows["local:b"]["segment"],
            )
            self.assertEqual("Com prévia 2", rows["local:b"]["title"])

    def test_candidate_list_carries_the_same_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            result = run_cli(
                self,
                "approve",
                "--candidate",
                "local:a",
                "--candidate",
                "local:b",
                "--by",
                "Bruno Moreira",
                "--statement",
                "Aprovo esses dois.",
                "--project",
                tmp,
            )
            self.assertEqual(2, len(result["approved_items"]))
            for row in result["approved_items"]:
                self.assertEqual({"id", "title", "contact_sheet", "segment"}, set(row))


class ChatStatementRequiredTests(unittest.TestCase):
    def test_chat_approval_without_statement_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            single = run_cli(
                self,
                "approve",
                "--candidate",
                "local:a",
                "--start",
                0,
                "--end",
                1,
                "--by",
                "Bruno",
                "--project",
                tmp,
                ok=False,
            )
            self.assertIn("--statement", single["error"])
            batch = run_cli(self, "approve", "--all", "--by", "Bruno", "--project", tmp, ok=False)
            self.assertIn("--statement", batch["error"])
            # Nada foi gravado: a recusa acontece antes de tocar no ledger.
            self.assertEqual("pending", Ledger(tmp).get("local:a")["approval"]["status"])

    def test_storyboard_channel_still_works_without_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            run_cli(
                self,
                "approve",
                "--all",
                "--channel",
                "storyboard",
                "--by",
                "Bruno",
                "--project",
                tmp,
            )
            self.assertEqual("approved", Ledger(tmp).get("local:a")["approval"]["status"])


class ReviewEpochCompatibilityTests(unittest.TestCase):
    def old_style(self):
        c = candidate("local", "a", "Aprovado antes da 2.4")
        set_segment(c, 0, 1)
        c["preview"]["gif_path"] = "previews/a.gif"
        c["approval"] = {
            "status": "approved",
            "by": "Bruno",
            "at": now(),
            "revision": c["segment"]["revision"],
            "signature": signature(c),
        }
        c["state"] = "approved"
        return c

    def test_epoch_ignores_keys_added_after_the_export(self):
        c = self.old_style()
        before = review_epoch(c)
        c["approval"]["channel"] = "chat"
        c["approval"]["statement"] = "Aprovo."
        self.assertEqual(before, review_epoch(c))

    def test_legacy_epoch_differs_from_the_new_one_for_an_approved_item(self):
        c = self.old_style()
        self.assertNotEqual(review_epoch(c), legacy_review_epoch(c))

    def test_board_exported_with_old_approval_still_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            c = self.old_style()
            ledger.add(c)
            ledger.save("fixture")
            export = {
                "type": "getbrolls-review",
                "templateVersion": 2,
                "project": project_id(ledger),
                "items": [
                    {
                        "id": c["id"],
                        "signature": signature(c),
                        # Época no formato 2.3.x: o dicionário `approval` inteiro,
                        # com a assinatura que a fórmula nova não considera mais.
                        "reviewEpoch": legacy_review_epoch(c),
                        "state": "approved",
                        "comment": "",
                        "suggestion": "",
                    }
                ],
            }
            path = Path(tmp) / "board.json"
            path.write_text(json.dumps(export), encoding="utf-8")
            result = import_review(Ledger(tmp), str(path), "Bruno")
            self.assertEqual(1, result["imported"])
            self.assertEqual("storyboard", Ledger(tmp).get(c["id"])["approval"]["channel"])


if __name__ == "__main__":
    unittest.main()
