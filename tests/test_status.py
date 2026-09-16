"""Observabilidade: `status` responde onde o projeto está e os comandos resumem o resultado."""

import hashlib
import json
import shutil
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
                [label for _, label in STATUS_STAGES],
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


class ProgressSummaryTests(unittest.TestCase):
    def test_every_flow_command_declares_a_one_line_summary(self):
        for command in (
            "search",
            "resolve",
            "preview",
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

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_real_lifecycle_reports_progress_at_every_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "original.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x180:duration=3:rate=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            resolved = run_cli(self, "resolve", "--file", src, "--project", root)
            base = ["--candidate", resolved["id"], "--project", root]
            self.assertIn("Registrei o candidato", resolved["summary"])
            preview = run_cli(self, "preview", *base, "--start", 0, "--end", 1)
            self.assertIn("Gerei a prévia", preview["summary"])
            run_cli(self, "approve", *base, "--start", 0, "--end", 1, "--by", "Humano")
            permitted = run_cli(
                self, "permit", *base, "--evidence", "Vídeo sintético do teste"
            )
            self.assertIn("condições de uso", permitted["summary"])
            fetched = run_cli(self, "fetch", *base)
            self.assertIn("Coletei o corte final", fetched["summary"])
            verified = run_cli(self, "verify", "--project", root)
            self.assertIn("1 arquivo coletado", verified["summary"])
            reviewed = run_cli(self, "review", "--project", root)
            self.assertIn("Gerei o Storyboard", reviewed["summary"])
            state = run_cli(self, "status", "--project", root)
            self.assertEqual(1, state["counts"]["verified"])
            self.assertIn("completo", state["summary"]["next"])


if __name__ == "__main__":
    unittest.main()
