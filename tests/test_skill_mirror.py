"""Raiz e espelho do SKILL.md são o mesmo texto.

Desde a 2.4 o SKILL.md não tem seção de instalação, então a única divergência
legítima entre `SKILL.md` e `skills/get-brolls/SKILL.md` é o prefixo
`${CLAUDE_PLUGIN_ROOT}/` nos caminhos citados pelo plugin — e o comentário HTML
de sincronia, que diz de qual lado o arquivo está.
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from getbrolls import __version__

ROOT_SKILL = ROOT / "SKILL.md"
MIRROR_SKILL = ROOT / "skills" / "get-brolls" / "SKILL.md"

MAX_WORDS = 900
MAX_WORDS_PER_PARAGRAPH = 80

# Comandos de instalação não têm lugar no SKILL.md: quem instala é
# `/get-brolls-setup`, e o arquivo mais lido não gasta linha com isso.
INSTALL_MARKERS = (
    "install.sh",
    "install.ps1",
    "pip install",
    "brew install",
    "npm install",
    "winget install",
    "apt install",
    "git clone",
)


def body(path):
    text = path.read_text(encoding="utf-8")
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)


def frontmatter(path):
    match = re.search(r"\A---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"{path} sem frontmatter"
    return match.group(1)


def field(path, name):
    for line in frontmatter(path).splitlines():
        stripped = line.strip()
        if stripped.startswith(name + ":"):
            return stripped.split(":", 1)[1].strip().strip('"')
    return None


def normalize(text):
    """Reduz o espelho ao texto da raiz: some o prefixo do plugin."""
    return text.replace("${CLAUDE_PLUGIN_ROOT}/", "")


def strip_sync_comment(text):
    return [line for line in text.splitlines() if not line.startswith("<!--")]


class SkillMirrorTests(unittest.TestCase):
    def test_bodies_are_identical_apart_from_the_plugin_prefix(self):
        self.assertEqual(
            strip_sync_comment(body(ROOT_SKILL)),
            strip_sync_comment(normalize(body(MIRROR_SKILL))),
            "espelho fora de sincronia com a raiz",
        )

    def test_frontmatters_are_identical(self):
        self.assertEqual(frontmatter(ROOT_SKILL), frontmatter(MIRROR_SKILL))

    def test_versions_match_the_package(self):
        for path in (ROOT_SKILL, MIRROR_SKILL):
            self.assertEqual(field(path, "version"), __version__)

    def test_mirror_actually_uses_the_plugin_prefix(self):
        refs = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./\-]+)", MIRROR_SKILL.read_text(encoding="utf-8"))
        self.assertTrue(refs, "espelho sem referências ${CLAUDE_PLUGIN_ROOT}")
        for ref in refs:
            self.assertTrue((ROOT / ref.split("#", 1)[0]).exists(), f"alvo inexistente: {ref}")

    def test_root_never_uses_the_plugin_prefix(self):
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", ROOT_SKILL.read_text(encoding="utf-8"))


class SkillBudgetTests(unittest.TestCase):
    """O SKILL.md é lido inteiro em toda sessão: tamanho é contrato."""

    def test_within_the_word_budget(self):
        for path in (ROOT_SKILL, MIRROR_SKILL):
            words = len(body(path).split())
            self.assertLessEqual(words, MAX_WORDS, f"{path.name} com {words} palavras")

    def test_paragraphs_stay_readable(self):
        for paragraph in body(ROOT_SKILL).split("\n\n"):
            if paragraph.lstrip().startswith(("-", "#", "|")):
                continue
            words = len(paragraph.split())
            self.assertLessEqual(
                words,
                MAX_WORDS_PER_PARAGRAPH,
                f"parágrafo com {words} palavras: {paragraph[:60]}…",
            )

    def test_no_installation_commands(self):
        for path in (ROOT_SKILL, MIRROR_SKILL):
            text = path.read_text(encoding="utf-8")
            for marker in INSTALL_MARKERS:
                self.assertNotIn(
                    marker,
                    text,
                    f"{path.name} traz comando de instalação ({marker}); isso é do /get-brolls-setup",
                )

    def test_the_three_guards_are_present(self):
        text = body(ROOT_SKILL)
        self.assertIn("Literal primeiro", text)
        self.assertIn("Stock só sob pedido", text)
        self.assertIn("Parada obrigatória na revisão", text)
        self.assertIn("import-review", text)
        self.assertIn('approve --all --by NOME --channel chat --statement "frase"', text)
        self.assertIn("Silêncio não é aprovação", text)

    def test_path_and_platform_conventions_survive(self):
        """O que some numa reescrita: caminho absoluto, --project e Windows."""
        text = body(ROOT_SKILL)
        self.assertIn("caminho absoluto da instalação da skill", text)
        self.assertIn("`--project` é sempre a pasta do usuário", text)
        self.assertIn("No Windows, use `python` no lugar de `python3`", text)

    def test_the_chat_route_shows_every_required_flag(self):
        """Aprovação pelo chat sem --statement não é aprovação."""
        text = body(ROOT_SKILL)
        for form in (
            'approve --all --by NOME --channel chat --statement "frase exata" --project <projeto>',
            'approve --candidate <ID> --by NOME --channel chat --statement "frase exata" --project <projeto>',
        ):
            self.assertIn(form, text, f"forma incompleta de approve: {form}")

    def test_contact_sheet_is_locatable(self):
        text = body(ROOT_SKILL)
        self.assertIn("files.contact_sheet", text)
        self.assertIn("preview.frame_times_s", text)

    def test_instagram_is_routed_before_the_browser(self):
        self.assertIn("antes de tocar no navegador", body(ROOT_SKILL))

    def test_status_is_repassed_verbatim(self):
        self.assertIn("summary.do.for_human", body(ROOT_SKILL))
        self.assertIn("sem parafrasear", body(ROOT_SKILL))


class SkillCommandsExistTests(unittest.TestCase):
    """Todo subcomando citado no SKILL.md existe de verdade na CLI."""

    def test_every_mentioned_subcommand_is_registered(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from getbrolls.cli import SUMMARIES

        mentioned = set(re.findall(r"gb\.py\" ([a-z\-]+)", body(ROOT_SKILL)))
        self.assertTrue(mentioned, "SKILL.md não cita nenhum subcomando")
        for name in mentioned:
            self.assertIn(name, SUMMARIES, f"subcomando inexistente citado: {name}")


if __name__ == "__main__":
    unittest.main()
