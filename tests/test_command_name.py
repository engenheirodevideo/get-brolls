"""O comando do produto é `getbrolls`: os documentos para pessoas e as mensagens do código não usam o nome antigo.

`python3 scripts/gb.py` (e o `gb <comando>` de antes) só fica onde a instalação importa: a nota de instalação
dos documentos de entrada, os procedimentos pela pasta da instalação (que precisam rodar o script dela, não o
`getbrolls` que estiver no PATH), o contrato da CLI e o que o agente executa pelo caminho da instalação. O que
vai para o projeto cita o subcomando sem prefixo, e "get-brolls" só sobra como nome da skill e em identificadores.
"""

import re
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT

from getbrolls import cli, install_report, rendering
from getbrolls.sdk import scaffold

# Os subcomandos da CLI, o mais longo primeiro (`init-brief` antes de `init`).
SUBCOMMANDS = "|".join(re.escape(name) for name in sorted(cli.SUMMARIES, key=len, reverse=True))
# O script da instalação, por qualquer Python e qualquer caminho: `python3 scripts/gb.py`, `python scripts\gb.py`,
# `python .\scripts\gb.py`, `python3.12 scripts/gb.py`, `python3 "$GB_SKILL_DIR/scripts/gb.py"`,
# `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py"` e `python3 <caminho absoluto da instalação>/scripts/gb.py`.
OLD_INVOCATION = re.compile(
    r"""\bpy(?:thon[\d.]*)?(?:\.exe)?\s+(?:-\d[\d.]*\s+)?["']?(?:<[^>]*>|[^\s"'<>])*scripts[/\\]gb\.py"""
)
# Os nomes curtos de antes: `gb <subcomando>` e `gb.py <subcomando>` sem a pasta `scripts/` (nem `scripts\`).
SHORT_NAME = re.compile(rf"(?<![\w./\\-])gb (?:{SUBCOMMANDS})\b|(?<![\w./\\\"$-])gb\.py [a-z]")
# Nome do produto com a grafia antiga, e os atalhos sem o namespace do plugin do Claude Code.
OLD_PRODUCT_NAME = re.compile(r"\bGet B-rolls\b")
# `get-brolls` como nome do produto na prosa ("o get-brolls não segue links"). Ficam de fora os identificadores:
# a skill e seus caminhos (`skills/get-brolls/`, `"skills" / "get-brolls"`), a URL do repositório, as tags do
# frontmatter (`[get-brolls, ...]`, `["get-brolls", ...]`), os comandos do plugin (`/getbrolls:get-brolls-brief`),
# o repositório antigo de plugins (`get-brolls-plugins`) e arquivos (`get-brolls.env`).
PRODUCT_PROSE = re.compile(r"(?:^|(?<=[\s(\"']))get-brolls(?![\w/@\"'-]|\.\w)")
# Nos documentos, a pasta do clone (`cd get-brolls`) e a etapa do ledger do vault (`step: get-brolls`) são nomes.
DOC_IDENTIFIERS = re.compile(r"\bcd get-brolls\b|`step: get-brolls`")
SHORT_SLASH_COMMAND = re.compile(r"(?<![:\w])/get-brolls-(?:brief|setup|status|review|eval)\b")

# Documentos para pessoas. O resto do que o agente executa (SKILL.md, skills/, references/, commands/) e o
# histórico (CHANGELOG.md, eval/runs/ datados) ficam de fora: lá o script pela raiz da instalação é de propósito.
HUMAN_DOC_GLOBS = (
    "*.md",
    "docs/*.md",
    ".github/**/*.md",
    "examples/plugins/*/README.md",
    "eval/README.md",
    "eval/runs/TEMPLATE.md",
)
HUMAN_DOC_FILES = (".env.example", ".env.example.pt-BR")
NOT_HUMAN_DOCS = {"CHANGELOG.md", "SKILL.md"}
# O resumo de versões antigas dos READMEs ("- **2.3.7** ...") guarda o nome que o comando tinha então.
RELEASE_HISTORY_LINE = re.compile(r"^\s*- \*\*\d+\.\d+")
# O cabeçalho do Storyboard escreve o nome em maiúsculas (CSS), como o título da página e o H1 dos READMEs.
STORYBOARD_BRAND_LABEL = re.compile(r'class="edition"')

# Documentos de entrada: uma nota de instalação cada (num clone sem o pacote, o script roda os mesmos comandos).
INSTALL_NOTES = ("README.md", "README.en.md", "docs/MANUAL.md", "docs/GUIDE.md")
# Seções dos documentos de entrada que são procedimentos pela pasta da instalação (clone ou plugin): rodam o
# script dela, nunca o `getbrolls` do PATH, que pode ser outra instalação. Fora delas, só a nota de instalação.
CHECKOUT_SECTIONS = {
    "README.md": ("2. Prepare o ambiente", "Instalação como skill do Codex, do Claude Code e de outros agentes"),
    "README.en.md": ("1. Add the skill to your agent", "2. Prepare the environment"),
    # Clone como skill (Codex, Claude Code e outros agentes), pelo caminho absoluto da pasta; o plugin, pela raiz
    # dele; e a permissão opcional, que escreve o caminho da instalação.
    "docs/GUIDE.md": (
        "macOS",
        "Windows",
        "Codex, Claude Code e outros agentes",
        "Plugin do Claude Code — 2.3.6",
        "Permissões (opcional)",
    ),
    "docs/MANUAL.md": (),
}
# Um bloco de código que roda o instalador do clone; o resto da seção confere o resultado com o script do clone.
CLONE_INSTALLER = re.compile(r"scripts[/\\]install\.(?:sh|ps1)\b")
# O comando do pacote seguido de um subcomando (`getbrolls doctor`, `getbrolls setup --where`).
PACKAGE_COMMAND = re.compile(rf"(?<![\w./:@-])getbrolls (?:{SUBCOMMANDS})\b")
HEADING = re.compile(r"^#{1,6} +(.+?) *$")
# Seções que rodam o instalador do clone: a busca abaixo tem que achar pelo menos estas.
INSTALLER_SECTIONS = (
    ("README.md", "2. Prepare o ambiente"),
    ("README.en.md", "2. Prepare the environment"),
    ("docs/GUIDE.md", "macOS"),
    ("docs/GUIDE.md", "Windows"),
)
# Quantas linhas, no máximo, de cada outro documento citam o script do clone, e por quê.
OTHER_ALLOWED_LINES = {
    # O contrato descreve as duas formas de chamar a CLI.
    "docs/CLI_CONTRACT.md": 2,
    # Procedimentos que verificam o clone: rodam o script dele, não o `getbrolls` do PATH.
    "AGENTS.md": 2,
    "CONTRIBUTING.md": 2,
    "docs/QUALITY.md": 1,
    ".github/PULL_REQUEST_TEMPLATE.md": 1,
    # Instrução para o agente do Gemini CLI, que roda a CLI pelo caminho da instalação.
    "GEMINI.md": 1,
    # Quem relata um problema roda o `doctor` da instalação que falhou: `getbrolls doctor` ou o script dela.
    ".github/ISSUE_TEMPLATE/bug_report.md": 1,
    ".github/ISSUE_TEMPLATE/question.md": 1,
    # O agente chega aqui pelo references/roteiro.md, num clone ou no plugin: o README mostra o script dela.
    "examples/plugins/exporter_example/README.md": 1,
    # Só existem no clone: o cabeçalho manda conferir com o script dele (e cita o `getbrolls doctor` do pacote).
    ".env.example": 1,
    ".env.example.pt-BR": 1,
}


def human_documents():
    paths = {path for pattern in HUMAN_DOC_GLOBS for path in ROOT.glob(pattern)}
    paths.update(ROOT / name for name in HUMAN_DOC_FILES)
    return sorted(path for path in paths if path.relative_to(ROOT).as_posix() not in NOT_HUMAN_DOCS)


def sections(path):
    """`(título, [(número, linha, é_código)])` de cada seção de um Markdown; `#` em bloco de código não é título."""
    found, title, lines, fenced = [], "", [], False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        heading = None if fenced else HEADING.match(line)
        if heading:
            found.append((title, lines))
            title, lines = heading.group(1), []
        else:
            lines.append((number, line, fenced))
    found.append((title, lines))
    return found


def lines_matching(path, pattern, ignore=None):
    text = path.read_text(encoding="utf-8")
    return [
        number
        for number, line in enumerate(text.splitlines(), 1)
        if pattern.search(line) and not (ignore and ignore.search(line))
    ]


class PatternTests(unittest.TestCase):
    """Os padrões reconhecem cada forma de chamar o script, e a forma do Windows não vira nome curto."""

    def test_the_install_script_is_recognized_through_any_python_and_any_path(self):
        for line in (
            "python3 scripts/gb.py doctor",
            "python scripts/gb.py doctor",
            "no Windows, `python scripts\\gb.py doctor`",
            "python .\\scripts\\gb.py doctor",
            "py -3 scripts\\gb.py doctor",
            "python3.12 scripts/gb.py doctor",
            "python3 ./scripts/gb.py doctor",
            "python3 /abs/scripts/gb.py doctor",
            'python3 "$GB_SKILL_DIR/scripts/gb.py" queue --action next --project "$GB_PROJECT"',
            'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor',
            'python "$env:GB_SKILL_DIR\\scripts\\gb.py" doctor',
            "python3 <caminho absoluto da instalação>/scripts/gb.py plugins --action install",
        ):
            with self.subTest(line=line):
                self.assertRegex(line, OLD_INVOCATION)
                self.assertNotRegex(line, SHORT_NAME)

    def test_short_names_cover_every_subcommand(self):
        for line in ("gb status --project x", "gb doctor", "gb x pasta_local", "`gb.py review` gera", "gb init-brief"):
            with self.subTest(line=line):
                self.assertRegex(line, SHORT_NAME)
        for line in ("bash scripts/gb.py.sh", "GB_HOME=/tmp/x", "um disco de 2 gb livre", "getbrolls doctor"):
            with self.subTest(line=line):
                self.assertNotRegex(line, SHORT_NAME)
                self.assertNotRegex(line, OLD_INVOCATION)

    def test_the_package_command_needs_a_subcommand(self):
        self.assertRegex("getbrolls setup --where", PACKAGE_COMMAND)
        self.assertRegex("`getbrolls doctor`", PACKAGE_COMMAND)
        for line in ("o getbrolls instala como comando", "/plugin install getbrolls@engenheirodevideo", "`getbrolls`"):
            with self.subTest(line=line):
                self.assertNotRegex(line, PACKAGE_COMMAND)


class HumanDocsNameTheCommandTests(unittest.TestCase):
    def test_the_documents_to_scan_are_found(self):
        names = {path.relative_to(ROOT).as_posix() for path in human_documents()}
        for expected in (*INSTALL_NOTES, "docs/SDK.md", "docs/PLUGIN_DEV_QUICKSTART.md", ".env.example"):
            self.assertIn(expected, names)
        self.assertNotIn("CHANGELOG.md", names)
        self.assertNotIn("SKILL.md", names)

    def test_the_old_invocation_stays_in_the_install_notes_and_checkout_procedures(self):
        problems = []
        for path in human_documents():
            relative = path.relative_to(ROOT).as_posix()
            if relative in INSTALL_NOTES:
                continue
            found = lines_matching(path, OLD_INVOCATION)
            if len(found) > OTHER_ALLOWED_LINES.get(relative, 0):
                problems.append(f"{relative}: linhas {found}")
        self.assertEqual([], problems, "os exemplos mostram `getbrolls <comando>`:\n" + "\n".join(problems))

    def test_each_entry_doc_has_one_install_note_that_names_the_package_command(self):
        for relative in INSTALL_NOTES:
            with self.subTest(relative=relative):
                checkout = CHECKOUT_SECTIONS[relative]
                outside, inside = [], {}
                for title, lines in sections(ROOT / relative):
                    found = [(number, line) for number, line, _ in lines if OLD_INVOCATION.search(line)]
                    if title in checkout:
                        inside[title] = found
                    else:
                        outside.extend(found)
                numbers = [number for number, _ in outside]
                self.assertEqual(1, len(outside), f"{relative}: uma nota de instalação, achou as linhas {numbers}")
                note = outside[0][1]
                self.assertIn("getbrolls", note)
                self.assertIn("python scripts/gb.py", note, "a nota diz o que muda no Windows")
                # Toda seção liberada existe e roda o script da instalação: nenhuma isenção sobra.
                self.assertEqual(sorted(checkout), sorted(title for title, found in inside.items() if found))

    def test_the_clone_install_steps_check_the_clone_with_its_own_script(self):
        # Depois do `install.sh`/`install.ps1`, o `getbrolls` do PATH não existe ou é outra instalação. Ele só
        # aparece na mesma linha que o script do clone, como a alternativa do pacote.
        installer_sections, problems = set(), []
        for path in human_documents():
            relative = path.relative_to(ROOT).as_posix()
            if path.suffix != ".md":
                continue
            for title, lines in sections(path):
                if not any(code and CLONE_INSTALLER.search(line) for _, line, code in lines):
                    continue
                installer_sections.add((relative, title))
                found = [
                    number
                    for number, line, _ in lines
                    if PACKAGE_COMMAND.search(line) and not OLD_INVOCATION.search(line)
                ]
                if found:
                    problems.append(f"{relative} ({title}): linhas {found}")
        self.assertEqual([], problems, "procedimento do clone com o comando do pacote:\n" + "\n".join(problems))
        for expected in INSTALLER_SECTIONS:
            self.assertIn(expected, installer_sections)

    def test_the_env_example_header_points_to_the_clone_doctor(self):
        for name in HUMAN_DOC_FILES:
            with self.subTest(name=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertRegex(text, r"`python3 scripts/gb\.py doctor` \([^)]*`getbrolls doctor`\)")

    def test_short_names_and_old_spellings_are_gone_from_the_human_docs(self):
        problems = []
        for path in human_documents():
            relative = path.relative_to(ROOT).as_posix()
            for label, pattern, ignore in (
                ("nome curto", SHORT_NAME, RELEASE_HISTORY_LINE),
                ("atalho sem namespace", SHORT_SLASH_COMMAND, RELEASE_HISTORY_LINE),
                ("get-brolls como nome do produto", PRODUCT_PROSE, DOC_IDENTIFIERS),
            ):
                found = lines_matching(path, pattern, ignore)
                if found:
                    problems.append(f"{relative}: {label}, linhas {found}")
        self.assertEqual([], problems, "\n".join(problems))


class ReportsNameTheInstallationUnderTestTests(unittest.TestCase):
    """Relato e avaliação descrevem a instalação que falhou ou que está em teste, não a que estiver no PATH."""

    def test_issue_templates_ask_for_the_doctor_of_the_installation_with_the_problem(self):
        for name in ("bug_report.md", "question.md"):
            with self.subTest(template=name):
                lines = (ROOT / ".github" / "ISSUE_TEMPLATE" / name).read_text(encoding="utf-8").splitlines()
                install = next(line for line in lines if line.startswith("- Instalação:"))
                self.assertIn("pacote", install)
                doctor = next(line for line in lines if "`getbrolls doctor`" in line)
                # A escolha é pela instalação usada, não por ter ou não o pacote na máquina.
                self.assertIn("da instalação", doctor)
                self.assertNotIn("sem o pacote instalado", doctor)
                self.assertRegex(doctor, OLD_INVOCATION)

    def test_the_eval_docs_collect_the_status_the_executor_runs(self):
        command = (ROOT / "commands" / "get-brolls-eval.md").read_text(encoding="utf-8")
        self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" status --project PROJETO', command)
        for relative in ("eval/README.md", "eval/runs/TEMPLATE.md"):
            with self.subTest(relative=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("status --project <projeto>", text)
                self.assertIn("instalação em teste", text)
                self.assertNotRegex(text, PACKAGE_COMMAND)


class ExamplePluginsInstallFromTheDataRootTests(unittest.TestCase):
    """Os exemplos vêm dentro da instalação: o caminho é o da pasta de dados, não um relativo à pasta atual."""

    def test_the_example_readmes_pass_the_data_root_to_the_cli(self):
        # `--source examples/...` só resolve na raiz de um clone; no pacote, os exemplos ficam em `_data/`.
        relative_to_cwd = re.compile(r"--(?:source|path) examples[/\\]")
        for readme in sorted((ROOT / "examples" / "plugins").glob("*/README.md")):
            ident = readme.parent.name
            with self.subTest(plugin=ident):
                text = readme.read_text(encoding="utf-8")
                self.assertNotRegex(text, relative_to_cwd)
                self.assertIn(f"<install.data_root>/examples/plugins/{ident}", text)
                self.assertIn("`install.data_root`", text)

    def test_the_data_root_the_doctor_shows_holds_the_examples(self):
        data_root = install_report.build_info()["data_root"]
        self.assertIsNotNone(data_root)
        for ident in ("banco_http", "exporter_example", "pasta_local"):
            self.assertTrue((Path(data_root) / "examples" / "plugins" / ident / "getbrolls-plugin.json").is_file())


class ProjectFilesNameTheBareSubcommandTests(unittest.TestCase):
    """O que o getbrolls grava no projeto cita o subcomando sem o prefixo de uma instalação.

    O projeto vive mais que a instalação que o criou, e a mesma pasta é aberta pelo pacote, pelo clone e
    pelo plugin: `getbrolls brief` não existe num clone, e `python3 scripts/gb.py` não existe no pacote.
    """

    def assert_names_no_installation(self, label, text):
        for name, pattern in (("pacote", PACKAGE_COMMAND), ("clone", OLD_INVOCATION), ("nome curto", SHORT_NAME)):
            with self.subTest(file=label, form=name):
                self.assertNotRegex(text, pattern)

    def test_init_brief_and_init_rules_write_bare_subcommands(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-brief", "--project", tmp)
            run_cli("init-rules", "--project", tmp)
            for name, command in (("BRIEF.md", "init-brief"), ("RULES.md", "init-rules")):
                text = (Path(tmp) / name).read_text(encoding="utf-8")
                self.assertIn(f"`{command} --project ./video-01` do getbrolls", text)
                self.assert_names_no_installation(name, text)

    def test_the_storyboard_notice_names_the_bare_subcommand(self):
        self.assertIn("<code>serve</code> do getbrolls", rendering.FILE_NOTICE)
        self.assert_names_no_installation("review.html", rendering.FILE_NOTICE)


class CodeMessagesNameTheCommandTests(unittest.TestCase):
    def sources(self):
        scripts, examples = ROOT / "scripts", ROOT / "examples"
        paths = [*scripts.rglob("*.py"), *scripts.rglob("*.sh"), *scripts.rglob("*.ps1"), *examples.rglob("*.py")]
        return sorted(path for path in paths if "__pycache__" not in path.parts)

    def test_messages_and_docstrings_use_the_product_name_and_the_plugin_namespace(self):
        problems = []
        for path in self.sources():
            relative = path.relative_to(ROOT).as_posix()
            patterns = [
                ("grafia antiga", OLD_PRODUCT_NAME),
                ("get-brolls como nome do produto", PRODUCT_PROSE),
                ("atalho sem namespace", SHORT_SLASH_COMMAND),
            ]
            if path.suffix == ".py":
                # Num script de shell, `gb.py setup` é o arquivo que ele de fato chama.
                patterns.append(("nome curto", SHORT_NAME))
            for label, pattern in patterns:
                found = lines_matching(path, pattern, STORYBOARD_BRAND_LABEL)
                if found:
                    problems.append(f"{relative}: {label}, linhas {found}")
        self.assertEqual([], problems, "\n".join(problems))

    def test_published_schemas_and_diagrams_name_the_product(self):
        paths = [*(ROOT / "schemas").glob("*.json"), *(ROOT / "assets").glob("*.svg")]
        self.assertGreaterEqual(len(paths), 17)
        offenders = [
            f"{path.relative_to(ROOT).as_posix()}: {label}"
            for path in paths
            for label, pattern in (("grafia antiga", OLD_PRODUCT_NAME), ("get-brolls na prosa", PRODUCT_PROSE))
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders)

    def test_product_prose_is_told_apart_from_the_identifiers(self):
        for line in (
            '"o get-brolls não segue links ali."',
            "reinstale o get-brolls.",
            "exige get-brolls {versão}",
            '"get-brolls (o fetch grava lá)"',
            "# Muda só em major do get-brolls.",
        ):
            with self.subTest(line=line):
                self.assertRegex(line, PRODUCT_PROSE)
        for line in (
            'ROOT / "skills" / "get-brolls" / "SKILL.md"',
            "`skills/get-brolls/SKILL.md`",
            "https://github.com/engenheirodevideo/get-brolls",
            '_frontmatter("delivery-origin", created, ["get-brolls", "entrega"])',
            '"tags: [get-brolls, credits]"',
            "`/getbrolls:get-brolls-brief`",
            '("getbrolls-plugins", "get-brolls-plugins")',
            'self.render("get-brolls.env")',
            "a skill continua `get-brolls`",
        ):
            with self.subTest(line=line):
                self.assertNotRegex(line, PRODUCT_PROSE)

    def test_generated_plugin_code_names_the_product(self):
        for name in ("PROVIDER", "ROUTE", "COMMAND", "EXPORTER"):
            with self.subTest(template=name):
                template = getattr(scaffold, name)
                self.assertIn("para o getbrolls (gerado por", template)
                self.assertNotRegex(template, OLD_PRODUCT_NAME)


if __name__ == "__main__":
    unittest.main()
