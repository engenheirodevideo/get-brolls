"""Observabilidade: `status` responde onde o projeto está e os comandos resumem o resultado."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/gb.py"
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls.commands import (
    FLOW_SUMMARIES,
    STATUS_STAGES,
    status_next,
    with_summary,
)
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment

# Infraestrutura de auditoria: `audited()` grava estes arquivos em qualquer comando.
AUDIT_FILES = {"diagnostics.jsonl", ".command.lock"}


def run_cli(test, *args):
    """Chamada barata de CLI: o ciclo real com FFmpeg vive em tests/test_cli.py."""
    done = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    test.assertEqual(0, done.returncode, done.stderr)
    return json.loads(done.stdout)


def fixture(root):
    """Projeto sintético com um item em cada etapa do fluxo."""
    ledger = Ledger(root)
    pending = candidate("local", "a", "Sem prévia")
    set_segment(pending, 0, 1)

    delivered = candidate("local", "b", "Entregue")
    set_segment(delivered, 0, 2)
    delivered["preview"]["gif_path"] = "previews/b.gif"
    delivered["approval"] = {
        "status": "approved",
        "by": "Revisor humano",
        "at": now(),
        "revision": 1,
    }
    delivered["rights"]["status"] = "permitted"
    delivered["rights"]["evidence"] = ["Evidência sintética do teste"]
    delivered["output"] = {"path": "clips/b.mp4", "sha256": "0" * 64, "verified": True}
    delivered["state"] = "verified"

    rejected = candidate("local", "c", "Rejeitado")
    set_segment(rejected, 0, 1)
    rejected["preview"]["contact_sheet_path"] = "previews/c.jpg"
    rejected["approval"]["status"] = "rejected"
    rejected["state"] = "rejected"

    items = [ledger.add(item) for item in (pending, delivered, rejected)]
    ledger.save_many("fixture", items)
    return ledger


def snapshot(root):
    """Conteúdo e mtime de cada arquivo do projeto, fora da auditoria da CLI."""
    state = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_dir():
            state[relative] = "dir"
            continue
        if path.name in AUDIT_FILES:
            continue
        state[relative] = (
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return state


class StatusCommandTests(unittest.TestCase):
    def call(self, *args):
        return run_cli(self, *args)

    def test_status_counts_and_lists_every_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            payload = self.call("status", "--project", tmp)
            self.assertEqual(
                {
                    "candidates": 3,
                    "previews": 2,
                    "pending": 1,
                    "approved": 1,
                    "rejected": 1,
                    "permitted": 1,
                    "delivered": 1,
                    "verified": 1,
                },
                payload["counts"],
            )
            self.assertEqual(["local:a"], payload["stages"]["pending"])
            self.assertEqual(["local:b"], payload["stages"]["delivered"])
            self.assertEqual(["local:c"], payload["stages"]["rejected"])
            self.assertEqual(
                ["local:a", "local:b", "local:c"],
                [item["id"] for item in payload["items"]],
            )
            self.assertEqual(3, payload["journal"]["events"])
            self.assertEqual("fixture", payload["journal"]["last"]["operation"])
            self.assertIsNone(payload["review_page"])

    def test_status_summary_comes_first_and_names_the_next_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            payload = self.call("status", "--project", tmp)
            self.assertEqual("summary", next(iter(payload)))
            summary = payload["summary"]
            self.assertEqual(["line", "stages", "next"], list(summary))
            self.assertIn("candidatos encontrados", summary["line"])
            self.assertEqual(
                [plural for _, _, plural in STATUS_STAGES],
                [stage["stage"] for stage in summary["stages"]],
            )
            self.assertIn("preview", summary["next"])

    def test_status_next_step_follows_the_flow(self):
        base = dict.fromkeys(
            (
                "candidates",
                "previews",
                "pending",
                "approved",
                "rejected",
                "permitted",
                "delivered",
                "verified",
            ),
            0,
        )
        self.assertIn("search", status_next(base))
        self.assertIn("preview", status_next({**base, "candidates": 2}))
        self.assertIn(
            "review", status_next({**base, "candidates": 2, "previews": 2})
        )
        self.assertIn(
            "permit",
            status_next({**base, "candidates": 2, "previews": 2, "approved": 2}),
        )
        self.assertIn(
            "fetch",
            status_next(
                {**base, "candidates": 2, "previews": 2, "approved": 2, "permitted": 2}
            ),
        )
        self.assertIn(
            "verify",
            status_next(
                {
                    **base,
                    "candidates": 2,
                    "previews": 2,
                    "approved": 2,
                    "permitted": 2,
                    "delivered": 2,
                }
            ),
        )
        self.assertIn(
            "completo",
            status_next(
                {
                    **base,
                    "candidates": 2,
                    "previews": 2,
                    "approved": 2,
                    "permitted": 2,
                    "delivered": 2,
                    "verified": 2,
                }
            ),
        )

    def test_status_never_writes_to_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            root = Path(tmp) / "brolls"
            before = snapshot(root)
            self.call("status", "--project", tmp)
            self.call("status", "--project", tmp)
            self.assertEqual(before, snapshot(root))
            self.assertTrue(before, "fixture vazio não provaria nada")

    def test_status_on_missing_project_fails_without_creating_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "projeto-inexistente"
            done = subprocess.run(
                [sys.executable, str(CLI), "status", "--project", str(absent)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(2, done.returncode, done.stdout)
            self.assertIn("Projeto não encontrado", done.stderr)
            self.assertIn("nenhum arquivo foi criado", done.stderr)
            self.assertEqual([], list(Path(tmp).iterdir()), "status criou arquivos")

    def test_status_reports_pending_write_without_completing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            root = Path(tmp) / "brolls"
            pending = root / ".pending-transaction.json"
            pending.write_text(
                json.dumps({"data": {"schema_version": 1, "items": []}, "events": []}),
                encoding="utf-8",
            )
            before = snapshot(root)
            payload = self.call("status", "--project", tmp)
            self.assertEqual("pending", payload["journal"]["recovered_write"])
            self.assertIn("gravação interrompida", payload["summary"]["line"])
            self.assertTrue(pending.is_file(), "status concluiu a transação pendente")
            self.assertEqual(before, snapshot(root))
            self.assertEqual(3, payload["counts"]["candidates"])

    def test_status_answers_while_another_command_holds_the_lock(self):
        from getbrolls.runtime import project_lock

        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            with project_lock(tmp):
                payload = self.call("status", "--project", tmp)
            self.assertEqual(3, payload["counts"]["candidates"])

    def test_status_degrades_on_corrupt_journal_and_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            root = Path(tmp) / "brolls"
            (root / "events.jsonl").write_text("{não é json}\n", encoding="utf-8")
            (root / "references.json").write_text("[]", encoding="utf-8")
            payload = self.call("status", "--project", tmp)
            self.assertIsNone(payload["journal"]["last"])
            self.assertIn("events.jsonl", payload["journal"]["error"])
            self.assertEqual(0, payload["references"])
            self.assertIn("references.json", payload["references_error"])

    def test_status_warns_when_editorial_rules_changed_the_target_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            rules = Path(tmp) / "RULES.md"
            source = json.loads(
                (ROOT / "docs" / "RULES.md").read_text(encoding="utf-8").split("```json")[1].split("```")[0]
            )
            source["video_format"] = "reels"
            rules.write_text(
                "# Regras\n\n```json\n" + json.dumps(source) + "\n```\n",
                encoding="utf-8",
            )
            payload = self.call("status", "--project", tmp)
            self.assertEqual(3, payload["format_pending"])
            self.assertIsNone(payload["rules_error"])
            self.assertTrue(all(item["format_pending"] for item in payload["items"]))
            self.assertIn("regras editoriais mudaram", payload["summary"]["next"])

    def test_status_reports_broken_rules_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            (Path(tmp) / "RULES.md").write_text("sem bloco json", encoding="utf-8")
            payload = self.call("status", "--project", tmp)
            self.assertIn("RULES.md", payload["rules_error"])
            self.assertEqual(0, payload["format_pending"])
            self.assertIsNone(payload["items"][0]["format_pending"])
            self.assertEqual(3, payload["counts"]["candidates"])


class ProgressSummaryTests(unittest.TestCase):
    def test_every_flow_command_declares_a_one_line_summary(self):
        for command in (
            "search",
            "resolve",
            "preview",
            "approve",
            "reject",
            "review",
            "import-review",
            "permit",
            "fetch",
            "verify",
            "status",
        ):
            self.assertIn(command, FLOW_SUMMARIES, command)

    def test_summary_is_additive_and_keeps_existing_keys(self):
        result = {"items": [{"id": "a"}], "errors": [], "excluded_by_rules": 2}
        enriched = with_summary("search", result)
        self.assertEqual(result, {k: v for k, v in enriched.items() if k != "summary"})
        self.assertNotIn("summary", result, "o resultado original não pode ser mutado")
        self.assertIn("1 registrado", enriched["summary"])
        self.assertIn("2 excluídos pelas regras", enriched["summary"])

    def test_summary_lines_describe_verb_object_and_result(self):
        lines = {
            "preview": with_summary(
                "preview",
                {"id": "local:a", "state": "awaiting_approval", "approval": {"status": "pending"}},
            )["summary"],
            "fetch": with_summary(
                "fetch", {"id": "local:a", "output": {"path": "clips/a.mp4"}}
            )["summary"],
            "verify": with_summary("verify", {"verified": [], "count": 2})["summary"],
            "permit": with_summary(
                "permit", {"id": "local:a", "rights": {"status": "permitted"}}
            )["summary"],
            "import-review": with_summary(
                "import-review", {"imported": 3, "by": "Revisor"}
            )["summary"],
        }
        self.assertIn("Gerei a prévia de local:a", lines["preview"])
        self.assertIn("clips/a.mp4", lines["fetch"])
        self.assertIn("2 arquivos coletados", lines["verify"])
        self.assertIn("permitted", lines["permit"])
        self.assertIn("3 decisões", lines["import-review"])

    def test_unknown_and_already_summarised_results_are_untouched(self):
        self.assertEqual({"a": 1}, with_summary("doctor", {"a": 1}))
        self.assertEqual({"summary": {"line": "x"}}, with_summary("status", {"summary": {"line": "x"}}))

    def test_search_summary_keeps_the_provider_note(self):
        enriched = with_summary(
            "search",
            {"items": [], "errors": [], "note": "APIs atuais pesquisam vídeos."},
        )
        self.assertIn("0 registrados", enriched["summary"])
        self.assertIn("APIs atuais pesquisam vídeos.", enriched["summary"])

    def test_reference_only_preview_says_it_generated_static_reference(self):
        line = with_summary(
            "preview",
            {"id": "local:a", "state": "reference_only", "approval": {"status": "pending"}},
        )["summary"]
        self.assertIn("somente a referência estática", line)

    def test_singular_and_plural_agree_with_the_counts(self):
        self.assertIn(
            "1 arquivo coletado: íntegro e decodificável",
            with_summary("verify", {"verified": [], "count": 1})["summary"],
        )
        self.assertIn(
            "2 arquivos coletados: íntegros e decodificáveis",
            with_summary("verify", {"verified": [], "count": 2})["summary"],
        )
        line = FLOW_SUMMARIES["status"]({"counts": {"candidates": 1, "previews": 2}})
        self.assertIn("1 candidato encontrado", line)
        self.assertIn("2 prévias geradas", line)

    def test_approve_and_reject_have_their_own_summary(self):
        self.assertIn(
            "Registrei a aprovação humana de local:a",
            with_summary(
                "approve",
                {"id": "local:a", "state": "approved", "approval": {"by": "Humano"}},
            )["summary"],
        )
        self.assertIn(
            "Rejeitei local:a",
            with_summary("reject", {"id": "local:a", "state": "rejected"})["summary"],
        )


if __name__ == "__main__":
    unittest.main()
