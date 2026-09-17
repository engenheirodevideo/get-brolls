"""Declaração de responsabilidade dita no chat, sem edição manual de RULES.md."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/gb.py"
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls.ledger import Ledger
from getbrolls.models import candidate, set_segment

TEXT = "Gravei este material e assumo a responsabilidade pelo uso."


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
    ledger = Ledger(root)
    c = candidate("local", "a", "Trecho")
    set_segment(c, 0, 1)
    ledger.add(c)
    ledger.save("fixture")
    return c["id"]


class PermitDeclarationTests(unittest.TestCase):
    def test_declaration_from_chat_records_person_channel_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = fixture(tmp)
            result = run_cli(
                self,
                "permit",
                "--candidate",
                ident,
                "--declared-by",
                "Bruno Moreira",
                "--declaration-text",
                TEXT,
                "--project",
                tmp,
            )
            rights = result["rights"]
            self.assertEqual("permitted", rights["status"])
            self.assertEqual("user_declaration", rights["basis"])
            self.assertEqual("Bruno Moreira", rights["responsible_person"])
            self.assertEqual("chat", rights["declaration_channel"])
            self.assertEqual(1, len(rights["evidence"]))
            self.assertIn("Bruno Moreira", rights["evidence"][0])
            self.assertIn(TEXT, rights["evidence"][0])

    def test_declaration_requires_real_name_and_real_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = fixture(tmp)
            base = ["permit", "--candidate", ident, "--project", tmp]
            error = run_cli(
                self,
                *base,
                "--declared-by",
                "usuário",
                "--declaration-text",
                TEXT,
                ok=False,
            )
            self.assertIn("nome", error["error"].lower())
            short = run_cli(
                self,
                *base,
                "--declared-by",
                "Bruno Moreira",
                "--declaration-text",
                "curto demais",
                ok=False,
            )
            self.assertIn("20", short["error"])
            alone = run_cli(self, *base, "--declared-by", "Bruno Moreira", ok=False)
            self.assertIn("--declaration-text", alone["error"])

    def test_without_new_flags_permit_keeps_current_behaviour(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = fixture(tmp)
            base = ["permit", "--candidate", ident, "--project", tmp]
            result = run_cli(self, *base, "--evidence", "Material próprio do teste")
            self.assertEqual("per_item_evidence", result["rights"]["basis"])
            self.assertIsNone(result["rights"].get("declaration_channel"))
            nothing = run_cli(self, *base, ok=False)
            self.assertIn("--evidence", nothing["error"])
            declaration = run_cli(self, *base, "--declaration", ok=False)
            self.assertIn("RULES.md", declaration["error"])


if __name__ == "__main__":
    unittest.main()
