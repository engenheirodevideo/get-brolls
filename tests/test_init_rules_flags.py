"""`init-rules` preenche o bloco JSON pelo chat, sem exigir edição manual do arquivo."""

import errno
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT
from test_brief import VALID

from getbrolls import cli
from getbrolls.brief import validate_brief
from getbrolls.rules import load_rules
from getbrolls.runtime import OperationError

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
            refused = run_cli("init-rules", "--project", tmp, "--format", "reels", expect=1)
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

    def test_force_on_a_rules_file_not_in_utf8_is_refused_in_portuguese(self):
        """RULES.md salvo em cp1252: recusa em português nomeando o arquivo, sem regravar nada."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "RULES.md"
            text = (ROOT / "docs" / "RULES.md").read_text(encoding="utf-8")
            path.write_text(text, encoding="cp1252", errors="replace")
            before = path.read_bytes()
            failed = run_cli("init-rules", "--project", tmp, "--force", "--format", "reels", expect=1)
            self.assertEqual("INVALID_DATA", failed["error_code"])
            self.assertIn("RULES.md", failed["error"])
            self.assertIn("UTF-8", failed["error"])
            self.assertNotIn("codec", failed["error"])
            self.assertEqual(before, path.read_bytes())

    def test_force_writes_through_a_rules_link(self):
        """RULES.md pode ser link para outra nota: a regravação vai no arquivo de verdade."""
        with tempfile.TemporaryDirectory() as tmp:
            notes = Path(tmp) / "notas"
            notes.mkdir()
            real = notes / "regras.md"
            shutil.copyfile(ROOT / "docs" / "RULES.md", real)
            link = Path(tmp) / "RULES.md"
            try:
                link.symlink_to(real)
            except (OSError, NotImplementedError):
                self.skipTest("este sistema não cria link simbólico")
            run_cli("init-rules", "--project", tmp, "--format", "reels", "--force")
            self.assertTrue(link.is_symlink())
            self.assertIn('"video_format": "reels"', real.read_text(encoding="utf-8"))
            self.assertEqual("reels", load_rules(tmp)["video_format"])

    def test_the_brief_conflict_points_at_a_command_that_can_fix_it(self):

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
            again = run_cli("init-rules", "--project", tmp, expect=1)
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
                expect=1,
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
            error = run_cli("init-rules", "--force", "--project", tmp, expect=1)
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


class PartialRulesFileTests(unittest.TestCase):
    """RULES.md de camada (só o que o projeto muda por cima do global) com `--force`.

    `copyright` nunca vem do global: um RULES.md de projeto sem ele não carrega.
    """

    PARTIAL = '# Regras\n\n```json\n{"video_format": "native"}\n```\n'

    def setUp(self):
        # O global completo é o que faz a camada parcial do projeto valer.
        home = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(home.cleanup)
        shutil.copyfile(ROOT / "docs" / "RULES.md", Path(home.name) / "RULES.md")
        self.env = {"GB_HOME": home.name}
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def block(self, path):
        return json.loads(path.read_text(encoding="utf-8").split("```json")[1].split("```")[0])

    def test_force_format_on_a_file_without_copyright_is_refused(self):
        """`--format` sozinho não inventa `copyright`, nem diz sucesso deixando um arquivo que não carrega."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "RULES.md"
            path.write_text(self.PARTIAL, encoding="utf-8")
            failed = run_cli("init-rules", "--project", tmp, "--force", "--format", "reels", expect=1, env=self.env)
            self.assertEqual("INVALID_DATA", failed["error_code"])
            self.assertIn('"copyright"', failed["error"])
            self.assertIn("init-rules --force --mode per_item_evidence --format reels", failed["error"])
            self.assertEqual(self.PARTIAL, path.read_text(encoding="utf-8"))

    def test_force_format_with_a_mode_on_a_partial_file_loads(self):
        """O comando que a recusa sugere deixa um RULES.md que carrega, com a prosa preservada."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "RULES.md"
            path.write_text(self.PARTIAL, encoding="utf-8")
            out = run_cli(
                "init-rules",
                "--project",
                tmp,
                "--force",
                "--mode",
                "per_item_evidence",
                "--format",
                "reels",
                env=self.env,
            )
            self.assertEqual("reels", out["video_format"])
            self.assertEqual("per_item_evidence", out["copyright"]["mode"])
            self.assertEqual({"video_format", "copyright"}, set(self.block(path)))
            self.assertTrue(path.read_text(encoding="utf-8").startswith("# Regras\n"))
            rules = load_rules(tmp)
            self.assertEqual("reels", rules["video_format"])
            self.assertEqual("per_item_evidence", rules["copyright"]["mode"])

    def test_copyright_flags_on_a_partial_file_start_from_the_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "RULES.md"
            path.write_text(self.PARTIAL, encoding="utf-8")
            out = run_cli(
                "init-rules",
                "--project",
                tmp,
                "--force",
                "--responsible",
                "Bruno Moreira",
                "--declaration",
                TEXT,
                env=self.env,
            )
            self.assertEqual("user_declaration", out["copyright"]["mode"])
            block = self.block(path)
            self.assertEqual("native", block["video_format"])
            self.assertEqual("Bruno Moreira", block["copyright"]["responsible_person"])
            self.assertEqual(TEXT, load_rules(tmp)["copyright"]["declaration"])

    def test_failed_write_keeps_the_old_rules_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", "--project", tmp)
            path = Path(tmp) / "RULES.md"
            before = path.read_bytes()
            with (
                mock.patch.object(os, "fsync", side_effect=OSError(errno.ENOSPC, "No space left on device")),
                self.assertRaises(OperationError),
            ):
                cli.main(["init-rules", "--project", tmp, "--force", "--format", "reels"])
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(["RULES.md"], sorted(p.name for p in Path(tmp).iterdir() if p.name != "brolls"))


if __name__ == "__main__":
    unittest.main()
