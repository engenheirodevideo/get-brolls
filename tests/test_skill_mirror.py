"""Raiz e espelho do SKILL.md são o mesmo texto.

Desde a 2.4 o SKILL.md não tem seção de instalação, então a única divergência
legítima entre `SKILL.md` e `skills/get-brolls/SKILL.md` é o prefixo
`${CLAUDE_PLUGIN_ROOT}/` nos caminhos citados pelo plugin — e o comentário HTML
de sincronia, que diz de qual lado o arquivo está. A sincronia em si é gerada
por `scripts/gen_skill_mirror.py --check`; esta suíte assere o contrato do
conteúdo, não a mecânica de sincronia.
"""

import contextlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from _paths import ROOT

from getbrolls import __version__

ROOT_SKILL = ROOT / "SKILL.md"
MIRROR_SKILL = ROOT / "skills" / "get-brolls" / "SKILL.md"
GEN_SKILL_MIRROR = ROOT / "scripts" / "gen_skill_mirror.py"

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


class SkillMirrorTests(unittest.TestCase):
    def test_generator_reproduces_the_versioned_mirror_byte_for_byte(self):
        """`--check` roda o gerador contra o `SKILL.md` atual e compara com o
        espelho versionado: sai 0 quando os dois batem byte a byte."""
        result = subprocess.run(
            [sys.executable, str(GEN_SKILL_MIRROR), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"espelho fora de sincronia com o gerador:\n{result.stdout}{result.stderr}",
        )

    def test_editing_the_mirror_by_hand_makes_check_exit_1(self):
        """`--check` detecta divergência: aponta o gerador para uma cópia
        editada à mão do espelho, fora do repositório, e nunca escreve no
        `skills/get-brolls/SKILL.md` versionado."""
        import gen_skill_mirror

        with tempfile.TemporaryDirectory() as tmp:
            tampered = Path(tmp) / "SKILL.md"
            mirror_text = MIRROR_SKILL.read_text(encoding="utf-8")
            tampered.write_text(
                mirror_text.replace("perguntas do brief.", "perguntas do brief editadas à mão."),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with (
                unittest.mock.patch.object(gen_skill_mirror, "MIRROR_SKILL", tampered),
                contextlib.redirect_stdout(buffer),
            ):
                exit_code = gen_skill_mirror.main(["--check"])

        self.assertEqual(exit_code, 1, "edição manual do espelho deveria falhar o --check")
        self.assertIn("perguntas do brief", buffer.getvalue())
        # A cópia editada nunca substitui o espelho versionado de verdade.
        self.assertNotIn("editadas à mão", MIRROR_SKILL.read_text(encoding="utf-8"))

    def test_broken_repo_path_reference_fails_loudly(self):
        """Um candidato que comece com pasta de topo conhecida (`scripts/`, ...)
        mas não exista de verdade (nem arquivo, nem pasta) é um caminho quebrado
        no `SKILL.md` da raiz: precisa falhar alto, em `--check` e na escrita,
        não ficar sem prefixo em silêncio."""
        import gen_skill_mirror

        broken_ref = "scripts/does-not-exist-getbrolls.py"
        frontmatter_text, existing_body = gen_skill_mirror.split_frontmatter(ROOT_SKILL.read_text(encoding="utf-8"))
        tampered_root = frontmatter_text + existing_body + f"\n\nVeja também `{broken_ref}`.\n"

        with tempfile.TemporaryDirectory() as tmp:
            tampered_root_path = Path(tmp) / "SKILL.md"
            tampered_root_path.write_text(tampered_root, encoding="utf-8")
            mirror_target = Path(tmp) / "mirror-skill.md"

            for argv in (["--check"], []):
                with self.subTest(argv=argv or ["<escrita>"]):
                    buffer = io.StringIO()
                    with (
                        unittest.mock.patch.object(gen_skill_mirror, "ROOT_SKILL", tampered_root_path),
                        unittest.mock.patch.object(gen_skill_mirror, "MIRROR_SKILL", mirror_target),
                        contextlib.redirect_stderr(buffer),
                    ):
                        exit_code = gen_skill_mirror.main(argv)
                    self.assertEqual(exit_code, 1)
                    self.assertIn(broken_ref, buffer.getvalue())
                    self.assertFalse(mirror_target.exists(), "não deve escrever o espelho com caminho quebrado")

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

    def test_frontmatter_is_valid_yaml_without_a_parser(self):
        """GitHub renderiza o frontmatter como YAML: um `: ` solto num valor sem
        aspas ("Also in English: …") derruba a página inteira com "mapping values
        are not allowed in this context". Sem PyYAML na suíte, a regra é sintática:
        todo valor de primeiro nível que contenha `: ` ou ` #` precisa estar entre
        aspas, e aspas abertas precisam fechar na mesma linha."""
        for path in (ROOT_SKILL, MIRROR_SKILL):
            for line in frontmatter(path).splitlines():
                if not line or line.startswith((" ", "\t")) or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                value = value.strip()
                if not value:
                    continue
                with self.subTest(file=path.name, key=key):
                    if value[0] in "'\"":
                        self.assertEqual(value[-1], value[0], f"aspas sem fechar em {key}")
                        inner = value[1:-1]
                        if value[0] == "'":
                            self.assertNotIn("'", inner.replace("''", ""), f"aspa simples solta em {key}")
                    else:
                        self.assertNotIn(": ", value, f"`: ` sem aspas em {key}")
                        self.assertNotIn(" #", value, f"` #` sem aspas em {key}")
                        self.assertNotIn(value[0], "[]{}&*!|>%@`", f"{key} começa com caractere reservado")


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
        self.assertIn('approve --candidate <ID> --by NOME --channel chat --statement "frase"', text)
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
        # A rota do chat aprova pelos IDs que o agente mostrou; `--all` é a exceção
        # anunciada, não o caminho padrão.
        for form in (
            (
                'approve --candidate ID1 --candidate ID2 … --by NOME --channel chat --statement "frase exata" '
                "--project <projeto>"
            ),
            "use `--all` só quando todos os candidatos com prévia foram mostrados",
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
        from getbrolls.cli import SUMMARIES

        mentioned = set(re.findall(r"gb\.py\" ([a-z\-]+)", body(ROOT_SKILL)))
        self.assertTrue(mentioned, "SKILL.md não cita nenhum subcomando")
        for name in mentioned:
            self.assertIn(name, SUMMARIES, f"subcomando inexistente citado: {name}")


if __name__ == "__main__":
    unittest.main()
