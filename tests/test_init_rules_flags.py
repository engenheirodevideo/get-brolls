"""`init-rules` preenche o bloco JSON pelo chat, sem exigir edição manual do arquivo."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/gb.py"
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls.rules import load_rules

TEXT = "Sou responsável pelos materiais que escolhi para este vídeo."


def run_cli(test, *args, ok=True):
    done = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    test.assertEqual(0 if ok else 2, done.returncode, done.stderr)
    return json.loads(done.stdout if ok else done.stderr)


class InitRulesFlagTests(unittest.TestCase):
    def test_without_flags_it_copies_the_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(self, "init-rules", "--project", tmp)
            rules = load_rules(tmp)
            self.assertEqual("per_item_evidence", rules["copyright"]["mode"])
            self.assertIsNone(rules["copyright"]["responsible_person"])
            again = run_cli(self, "init-rules", "--project", tmp, ok=False)
            self.assertIn("já existe", again["error"])

    def test_flags_fill_the_json_block_and_keep_the_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(
                self,
                "init-rules",
                "--mode",
                "user_declaration",
                "--responsible",
                "Bruno Moreira",
                "--declaration",
                TEXT,
                "--project",
                tmp,
            )
            raw = (Path(tmp) / "RULES.md").read_text(encoding="utf-8")
            self.assertIn("# Regras do usuário", raw)
            self.assertIn("## O que você decide", raw)
            self.assertEqual(1, raw.count("```json"))
            rules = load_rules(tmp)
            self.assertEqual("user_declaration", rules["copyright"]["mode"])
            self.assertEqual("Bruno Moreira", rules["copyright"]["responsible_person"])
            self.assertEqual(TEXT, rules["copyright"]["declaration"])

    def test_user_declaration_without_name_and_text_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            error = run_cli(
                self,
                "init-rules",
                "--mode",
                "user_declaration",
                "--project",
                tmp,
                ok=False,
            )
            self.assertIn("--responsible", error["error"])
            self.assertFalse((Path(tmp) / "RULES.md").exists())

    def test_force_rewrites_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(self, "init-rules", "--project", tmp)
            run_cli(
                self,
                "init-rules",
                "--force",
                "--mode",
                "user_declaration",
                "--responsible",
                "Bruno Moreira",
                "--declaration",
                TEXT,
                "--project",
                tmp,
            )
            self.assertEqual(
                "Bruno Moreira", load_rules(tmp)["copyright"]["responsible_person"]
            )


if __name__ == "__main__":
    unittest.main()
