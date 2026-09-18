"""`init-rules` preenche o bloco JSON pelo chat, sem exigir edição manual do arquivo."""

import json
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.rules import load_rules

TEXT = "Sou responsável pelos materiais que escolhi para este vídeo."


class VideoFormatFlagTests(unittest.TestCase):
    """#17: o conflito brief×rules mandava rodar `init-rules --force`, que não mudava o formato."""

    def test_format_sets_video_format_on_a_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_cli("init-rules", "--project", tmp, "--format", "reels")
            self.assertEqual("reels", out["video_format"])
            self.assertEqual("reels", load_rules(tmp)["video_format"])

    def test_format_on_an_existing_file_requires_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", "--project", tmp)
            refused = run_cli("init-rules", "--project", tmp, "--format", "reels", expect=2)
            self.assertIn("--format", json.dumps(refused, ensure_ascii=False))
            run_cli("init-rules", "--project", tmp, "--format", "reels", "--force")
            self.assertEqual("reels", load_rules(tmp)["video_format"])

    def test_format_changes_only_that_field_and_keeps_the_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(
                "init-rules",
                "--project",
                tmp,
                "--mode",
                "user_declaration",
                "--responsible",
                "Bruno",
                "--declaration",
                TEXT,
            )
            before = load_rules(tmp)
            prose = Path(tmp, "RULES.md").read_text(encoding="utf-8").split("```json")[0]
            run_cli("init-rules", "--project", tmp, "--format", "horizontal", "--force")
            after = load_rules(tmp)
            self.assertEqual("horizontal", after["video_format"])
            self.assertEqual(before["copyright"], after["copyright"])
            self.assertEqual(prose, Path(tmp, "RULES.md").read_text(encoding="utf-8").split("```json")[0])

    def test_the_brief_conflict_points_at_a_command_that_can_fix_it(self):
        from getbrolls.brief import validate_brief
        from tests.test_brief import VALID

        payload = json.loads(json.dumps(VALID))
        payload["video"]["delivery"]["format"] = "reels"
        _, conflicts = validate_brief(payload, {"video_format": "horizontal", "copyright": {}})
        self.assertTrue(conflicts)
        self.assertIn("init-rules --format reels --force --project", conflicts[0])


class InitRulesFlagTests(unittest.TestCase):
    def test_without_flags_it_copies_the_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", "--project", tmp)
            rules = load_rules(tmp)
            self.assertEqual("per_item_evidence", rules["copyright"]["mode"])
            self.assertIsNone(rules["copyright"]["responsible_person"])
            again = run_cli("init-rules", "--project", tmp, expect=2)
            self.assertIn("já existe", again["error"])

    def test_flags_fill_the_json_block_and_keep_the_prose(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli(
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
                "init-rules",
                "--mode",
                "user_declaration",
                "--project",
                tmp,
                expect=2,
            )
            self.assertIn("--responsible", error["error"])
            self.assertFalse((Path(tmp) / "RULES.md").exists())

    def test_force_without_content_flags_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", "--project", tmp)
            path = Path(tmp) / "RULES.md"
            path.write_text(
                path.read_text(encoding="utf-8").replace('"blocked_domains": []', '"blocked_domains": ["exemplo.com"]'),
                encoding="utf-8",
            )
            error = run_cli("init-rules", "--force", "--project", tmp, expect=2)
            self.assertIn("--mode", error["error"])
            self.assertEqual(["exemplo.com"], load_rules(tmp)["blocked_domains"])

    def test_force_keeps_the_choices_the_user_already_made(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", "--project", tmp)
            path = Path(tmp) / "RULES.md"
            path.write_text(
                path.read_text(encoding="utf-8")
                .replace('"blocked_domains": []', '"blocked_domains": ["exemplo.com"]')
                .replace('"editorial_rules": []', '"editorial_rules": ["preservar manchete"]'),
                encoding="utf-8",
            )
            run_cli(
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
            rules = load_rules(tmp)
            self.assertEqual(["exemplo.com"], rules["blocked_domains"])
            self.assertEqual(["preservar manchete"], rules["editorial_rules"])
            self.assertEqual("Bruno Moreira", rules["copyright"]["responsible_person"])

    def test_force_rewrites_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", "--project", tmp)
            run_cli(
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
            self.assertEqual("Bruno Moreira", load_rules(tmp)["copyright"]["responsible_person"])


if __name__ == "__main__":
    unittest.main()
