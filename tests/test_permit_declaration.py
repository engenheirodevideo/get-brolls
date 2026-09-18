"""Declaração de responsabilidade dita no chat, sem edição manual de RULES.md."""

import tempfile
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.ledger import Ledger
from getbrolls.models import candidate, set_segment

TEXT = "Gravei este material e assumo a responsabilidade pelo uso."


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
                *base,
                "--declared-by",
                "usuário",
                "--declaration-text",
                TEXT,
                expect=2,
            )
            self.assertIn("nome", error["error"].lower())
            short = run_cli(
                *base,
                "--declared-by",
                "Bruno Moreira",
                "--declaration-text",
                "curto demais",
                expect=2,
            )
            self.assertIn("20", short["error"])
            alone = run_cli(*base, "--declared-by", "Bruno Moreira", expect=2)
            self.assertIn("--declaration-text", alone["error"])

    def test_generic_single_word_names_are_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = fixture(tmp)
            base = ["permit", "--candidate", ident, "--project", tmp, "--declaration-text", TEXT]
            for generic in ("eu", "user", "cliente", "usuário", "usuario", "me", "admin", "Admin"):
                refused = run_cli(*base, "--declared-by", generic, expect=2)
                self.assertIn("--declared-by", refused["error"])
                self.assertIn("não identifica ninguém", refused["error"])

    def test_one_word_is_refused_even_when_it_is_a_real_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = fixture(tmp)
            refused = run_cli(
                "permit",
                "--candidate",
                ident,
                "--project",
                tmp,
                "--declaration-text",
                TEXT,
                "--declared-by",
                "Bruno",
                expect=2,
            )
            self.assertIn("duas palavras", refused["error"])

    def test_name_plus_surname_or_initial_is_accepted_and_teste_still_passes(self):
        for name in ("Bruno Moreira", "Bruno M.", "Ana Teste"):
            with tempfile.TemporaryDirectory() as tmp:
                ident = fixture(tmp)
                result = run_cli(
                    "permit",
                    "--candidate",
                    ident,
                    "--project",
                    tmp,
                    "--declaration-text",
                    TEXT,
                    "--declared-by",
                    name,
                )
                self.assertEqual("user_declaration", result["rights"]["basis"])
                self.assertEqual(name, result["rights"]["responsible_person"])

    def test_without_new_flags_permit_keeps_current_behaviour(self):
        with tempfile.TemporaryDirectory() as tmp:
            ident = fixture(tmp)
            base = ["permit", "--candidate", ident, "--project", tmp]
            result = run_cli(*base, "--evidence", "Material próprio do teste")
            self.assertEqual("per_item_evidence", result["rights"]["basis"])
            self.assertIsNone(result["rights"].get("declaration_channel"))
            nothing = run_cli(*base, expect=2)
            self.assertIn("--evidence", nothing["error"])
            declaration = run_cli(*base, "--declaration", expect=2)
            self.assertIn("RULES.md", declaration["error"])


if __name__ == "__main__":
    unittest.main()
