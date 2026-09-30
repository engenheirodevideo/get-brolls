import ast
import os
import re
import shutil
import subprocess
import unicodedata
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import bootstrap

# Caminho local de máquina: Unix (/Users, /home, com usuário maiúsculo ou não) e
# Windows (C:\Users\...), mais o marcador do worktree temporário do agente.
LOCAL_PATH_PATTERN = re.compile(
    r"/Users/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+|"
    r"/private/tmp/[A-Za-z0-9]|/home/[A-Za-z0-9._-]+/|claude-501"
)

# Extensões varridas pela procura de caminho local dentro do conteúdo rastreado.
LOCAL_PATH_SCAN_SUFFIXES = (
    ".md",
    ".py",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".txt",
    ".sh",
    ".ps1",
    ".canvas",
    ".svg",
    ".html",
    ".css",
    ".js",
    ".cfg",
    ".ini",
)


# Identificador de revisão interna (achado de ledger, rodada de conserto): não tem
# significado para quem lê o repositório público. O motivo técnico fica no texto;
# o id, não. `red[-]team` fica partido para o próprio padrão não casar consigo.
INTERNAL_REVIEW_ID_PATTERN = re.compile(
    r"\b(?:RT|BUG|B|M|I|D3|H4|C1|T2)-\d{1,2}\b(?![-\d])"
    r"|\bC [HML]-\d+\b|\bA [IM]\d\b|\bMinor \d+\b|\b[Ff]ix round \d\b"
    r"|\bred[-]team\b|\bFinding \d+\b|\bG\d{1,2}:(?!\d)"
    # Forma sem hífen (letra + 1 ou 2 dígitos): só logo depois de "(", aspas, "#", espaço ou
    # início da linha, e só antes de ":" ou ")" — "C3" em prosa, "H264", "M4A" e
    # "I/O" não casam; o "Checkpoint C3:" do SKILL.md também não.
    r"|(?:^|(?<=[\s(\"#]))(?<!Checkpoint )[BMIHLTDC]\d{1,2}(?=[:)])"
)
# Rodada/onda de revisão escrita por extenso, no começo de comentário ou docstring e
# seguida de ":" ou "(": só em código — na prosa (CHANGELOG, eval/), "rodada" e
# "onda" numeradas são histórico legítimo.
INTERNAL_REVIEW_ROUND_PATTERN = re.compile(
    r"(?:^\s*|(?<=[(\"])|(?<=#)\s?)(?:[Rr]ound|[Rr]odada|[Oo]nda|[Ww]ave|Task) \d+(?=:|\)| \()"
)

# Material de processo que apodrece: comparação com um branch específico (o
# comentário fica desatualizado assim que a branch vira a própria main) e o
# nome interno das frentes paralelas de trabalho. O motivo da supressão tem
# que ser atemporal, não amarrado a como o código chegou até aqui.
PROCESS_LANGUAGE_PATTERN = re.compile(r"origin/main|desta frente|novo nesta release")


def github_slug(heading):
    value = unicodedata.normalize("NFC", heading.strip().lower())
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return value.replace(" ", "-")


def _logical_units(text):
    """Linhas de bloco de código ```` ``` ```` ficam separadas (um comando por
    linha); parágrafos de prosa têm a própria quebra de linha suave (o
    `--yes`/`--expect` de uma frase que o autor embrulhou em três linhas)
    juntada de volta numa unidade só — o mesmo jeito que o Markdown renderiza
    um parágrafo. Sem isso, uma checagem "mesma linha" passaria por cima de
    uma frase em prosa onde `--yes` caiu numa linha e `--expect` (ou a falta
    dele) em outra."""
    units = []
    paragraph = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if paragraph:
                units.append(" ".join(paragraph))
                paragraph = []
            in_fence = not in_fence
            continue
        if in_fence:
            units.append(line)
            continue
        if not stripped:
            if paragraph:
                units.append(" ".join(paragraph))
                paragraph = []
            continue
        paragraph.append(stripped)
    if paragraph:
        units.append(" ".join(paragraph))
    return units


# Destinos que o hub de AGENTS.md precisa rotear: um por público/finalidade.
HUB_TARGETS = (
    "SKILL.md",
    "skills/get-brolls/SKILL.md",
    "agents/openai.yaml",
    "commands/get-brolls-setup.md",
    "GEMINI.md",
    "docs/GUIDE.md",
    "docs/QUALITY.md",
    "CONTRIBUTING.md",
    "docs/SECURITY.md",
    "CHANGELOG.md",
    "README.md",
    "README.en.md",
)

# Acionamento por agente: o hub nomeia o comando real de cada instalação.
HUB_INVOCATIONS = (
    "$get-brolls",
    "/get-brolls",
    "/plugin marketplace add engenheirodevideo/get-brolls",
    "/get-brolls:get-brolls",
    "/get-brolls-setup",
)


class AgentsHubTests(unittest.TestCase):
    def test_hub_links_every_entry_point_and_names_each_invocation(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        links = {raw.strip().strip("<>").partition("#")[0] for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", agents)}
        missing = [target for target in HUB_TARGETS if target not in links]
        self.assertEqual([], missing, "hub sem link para: " + ", ".join(missing))
        for marker in HUB_INVOCATIONS:
            self.assertIn(marker, agents, f"hub sem o acionamento: {marker}")

    def test_blind_test_eval_is_published_and_routed(self):
        for relative in (
            "eval/README.md",
            "eval/rubric.md",
            "eval/runs/TEMPLATE.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), f"ausente: {relative}")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        links = {raw.strip().strip("<>").partition("#")[0] for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", agents)}
        self.assertIn("eval/README.md", links, "hub sem link para a medição editorial")

    def test_agent_routers_point_to_the_hub(self):
        for name in ("CLAUDE.md", "GEMINI.md", "README.md", "README.en.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("(AGENTS.md)", text, f"{name} não referencia AGENTS.md")

    def test_routers_stay_thin_and_do_not_restate_the_hub(self):
        claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("(SKILL.md)", claude, "CLAUDE.md deve nomear o contrato de operação")
        self.assertLess(len(claude.splitlines()), 20, "CLAUDE.md deixou de ser roteador")


class RepositoryDocumentationTests(unittest.TestCase):
    def test_relative_markdown_links_and_anchors_resolve(self):
        documents = {path.resolve(): path.read_text(encoding="utf-8") for path in ROOT.glob("*.md")}
        anchors = {
            path: {github_slug(match) for match in re.findall(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE)}
            for path, text in documents.items()
        }
        problems = []
        for source, text in documents.items():
            for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                target = raw.strip().strip("<>")
                if "://" in target or target.startswith("mailto:"):
                    continue
                filename, separator, fragment = target.partition("#")
                destination = (source.parent / filename).resolve() if filename else source
                if not destination.is_file():
                    problems.append(f"{source.name}: arquivo ausente: {raw}")
                    continue
                if separator and destination in anchors and fragment not in anchors[destination]:
                    problems.append(f"{source.name}: âncora ausente: {raw}")
        self.assertEqual([], problems, "\n".join(problems))

    def test_official_repository_clone_is_documented(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://github.com/engenheirodevideo/get-brolls", readme)
        self.assertIn("git clone", readme)

    def test_readmes_have_reciprocal_language_switch_and_author_credit(self):
        portuguese = (ROOT / "README.md").read_text(encoding="utf-8")
        english = (ROOT / "README.en.md").read_text(encoding="utf-8")
        self.assertIn('href="README.en.md">English</a>', portuguese)
        self.assertIn('href="README.md">Português</a>', english)
        self.assertIn("<h1>GET B-ROLLS</h1>", portuguese)
        self.assertIn("<h1>GET B-ROLLS</h1>", english)
        for readme in (portuguese, english):
            self.assertIn("Bruno Moreira — Engenheiro de Vídeo", readme)
            self.assertIn("https://www.instagram.com/zbrunomoreira/", readme)

    def test_native_windows_entrypoints_are_present_and_documented(self):
        installer = ROOT / "scripts/install.ps1"
        playwright = ROOT / "scripts/playwright.ps1"
        self.assertTrue(installer.is_file())
        self.assertTrue(playwright.is_file())
        self.assertIn("'setup'", installer.read_text(encoding="utf-8"))
        self.assertIn("playwright-cli.cmd", playwright.read_text(encoding="utf-8"))
        self.assertIn("Invoke-Native", installer.read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("macOS e Windows", readme)
        self.assertIn("scripts/install.ps1", readme)

    def test_ci_runs_primary_matrix_on_macos_and_windows(self):
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertIn("macos-latest", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("./scripts/install.ps1\n", workflow)
        # A sintaxe dos scripts é conferida nos dois sistemas: `bash -n` no Unix,
        # `[scriptblock]::Create` no Windows.
        self.assertIn("bash -n", workflow)
        self.assertIn("[scriptblock]::Create", workflow)

    def test_ci_caches_pip_and_npm_and_checks_the_skill_mirror(self):
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertIn("cache: 'pip'", workflow)
        self.assertIn("cache: 'npm'", workflow)
        self.assertIn("gen_skill_mirror.py --check", workflow)

    def test_quality_stack_is_configured_and_runs_in_ci(self):
        # Lint e type check são parte do contrato de contribuição: config versionada,
        # ferramentas pinadas e um job próprio no CI.
        config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for marker in ("[tool.ruff]", "[tool.pyright]", "line-length = 120", 'pythonVersion = "3.11"'):
            self.assertIn(marker, config, marker)
        dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        self.assertRegex(dev, r"(?m)^ruff==\d+\.\d+")
        self.assertRegex(dev, r"(?m)^pyright==\d+\.\d+")
        runtime = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for tool in ("ruff", "pyright"):
            self.assertNotIn(tool, runtime, "ferramenta de dev não entra no runtime")
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertIn("\n  quality:\n", workflow)
        for step in ("ruff check .", "ruff format --check .", "pyright"):
            self.assertIn(step, workflow, step)

    def test_local_quality_commands_exist_for_both_platforms(self):
        shell = ROOT / "scripts/check.sh"
        powershell = ROOT / "scripts/check.ps1"
        self.assertTrue(shell.is_file())
        self.assertTrue(powershell.is_file())
        # No Windows o `bash` que existe é o do WSL/Git Bash, que nem sempre entende
        # um caminho `D:\...`: a sintaxe do shell é conferida pelo job Unix do CI.
        if os.name != "nt" and shutil.which("bash"):
            parsed = subprocess.run(
                ["bash", "-n", str(shell)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, parsed.returncode, parsed.stderr)
        text = shell.read_text(encoding="utf-8")
        for step in (
            "ruff check",
            "ruff format --check",
            "pyright",
            "gen_skill_mirror.py --check",
            "check_anchors.py",
            "unittest discover -s tests",
        ):
            self.assertIn(step, text, step)
        windows = powershell.read_text(encoding="utf-8")
        for step in (
            "ruff check",
            "ruff format --check",
            "pyright",
            "gen_skill_mirror.py",
            "check_anchors.py",
            "unittest",
        ):
            self.assertIn(step, windows, step)
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("scripts/check.sh", contributing)

    def test_delivery_has_no_parallel_artifact_or_reference_trees(self):
        # `references/` é a pasta oficial de copy do plugin desde a 2.4; o que segue
        # proibido é uma segunda árvore de entrega ou um `reference/` no singular.
        for name in ("artifacts", "dist", "reference", "broll", "instagram"):
            self.assertFalse((ROOT / name).exists(), name)
        names = {p.name for p in (ROOT / "references").iterdir() if p.is_file()}
        self.assertLessEqual({"glossario.md", "templates-de-resposta.md"}, names)
        self.assertTrue(all(name.endswith(".md") for name in names), names)
        scripts = ROOT / "scripts"
        self.assertEqual(
            ["getbrolls"],
            sorted(
                path.name
                for path in scripts.iterdir()
                if path.is_dir() and path.name != "__pycache__" and not path.name.startswith(".")
            ),
        )

    def test_public_source_has_no_legacy_product_identity(self):
        legacy = "auto" + "edit"
        problems = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".png"}:
                continue
            if legacy in path.name.lower() or legacy in path.read_text(encoding="utf-8", errors="ignore").lower():
                problems.append(str(path.relative_to(ROOT)))
        self.assertEqual([], problems)

    def test_security_documents_egress_and_absence_of_telemetry(self):
        security = (ROOT / "docs" / "SECURITY.md").read_text(encoding="utf-8")
        for marker in (
            "PyPI",
            "npm ci --ignore-scripts",
            "yt-dlp/curl",
            "Sem telemetria",
        ):
            self.assertIn(marker, security, marker)

    def test_plugin_install_update_yes_always_pairs_with_expect(self):
        """`--yes` sozinho não instala nada em
        `plugins --action install|update` (precisa de `--expect <sha256>` — ver
        `getbrolls.sdk.install._check_expect`); um exemplo documentado sem `--expect`
        é um comando que falha exatamente como escrito."""
        action_re = re.compile(r"--action\s+(install|update)\b")
        yes_re = re.compile(r"--yes\b")
        for relative in (
            "docs/SDK.md",
            "docs/GUIDE.md",
            "examples/plugins/banco_http/README.md",
            "examples/plugins/pasta_local/README.md",
        ):
            path = ROOT / relative
            for unit in _logical_units(path.read_text(encoding="utf-8")):
                if action_re.search(unit) and yes_re.search(unit):
                    self.assertIn("--expect", unit, f"{relative}: {unit!r}")

    def test_the_validated_python_range_is_named_once(self):
        # Os instaladores delegam ao `setup`: a faixa validada mora na falha do pip dele.
        shell = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        self.assertIn("validado em Python 3.11–3.13", bootstrap.PIP_VERSION_FAILED)
        for script in (shell, powershell):
            self.assertNotIn("validado em Python", script)
        self.assertIn("exit", shell)
        self.assertIn("throw", powershell)

    def test_installers_delegate_to_setup(self):
        shell = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        self.assertIn('scripts/gb.py" setup', shell)
        self.assertIn("setup_status=$?", shell)
        self.assertIn("'setup'", powershell)
        self.assertIn("$SetupStatus -ne 4", powershell)
        for script in (shell, powershell):
            self.assertNotIn("-m venv", script)
            self.assertNotIn("'venv'", script)
            self.assertNotIn("npm --cache", script)
            self.assertNotIn("'--cache'", script)

    def test_launchers_ask_setup_where(self):
        runtime = (ROOT / "scripts/getbrolls/tools/youtube/_runtime.sh").read_text(encoding="utf-8")
        self.assertIn("setup --where", runtime)
        self.assertIn("gb_where_executable venv", runtime)
        self.assertIn("in_use", runtime)
        self.assertIn("set -o pipefail", runtime)
        self.assertIn("--profile off", runtime)
        shell = (ROOT / "scripts/playwright.sh").read_text(encoding="utf-8")
        self.assertIn("gb_where_executable tools", shell)
        powershell = (ROOT / "scripts/playwright.ps1").read_text(encoding="utf-8")
        self.assertIn("'setup', '--where', 'tools'", powershell)
        self.assertIn("'--profile', 'off'", powershell)
        self.assertIn("in_use.executable", powershell)
        for script in (runtime, shell, powershell):
            self.assertNotIn(".tools/node_modules", script)
            self.assertNotIn(".tools\\node_modules", script)
            self.assertNotIn(".venv/bin/yt-dlp", script)

    def test_ci_points_the_runtime_outside_the_checkout(self):
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertEqual(2, workflow.count("GB_RUNTIME_DIR=$RUNNER_TEMP/gb-runtime"))
        self.assertNotIn("GB_RUNTIME_DIR: ${{ github.workspace }}", workflow)

    def test_powershell_installer_python_snippets_have_no_double_quotes(self):
        # Windows PowerShell 5.1 strips inner double quotes when passing
        # arguments to native executables, so `python -c '... "x" ...'`
        # reaches Python as `... x ...` and raises SyntaxError (issue #23).
        # Single quotes escaped as '' survive in both 5.1 and 7.

        powershell = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        snippets = re.findall(r"'-c',\s*'((?:[^']|'')*)'", powershell)
        snippets += re.findall(r"-c\s+'((?:[^']|'')*)'", powershell)
        self.assertGreaterEqual(len(snippets), 1, powershell)
        for snippet in snippets:
            self.assertNotIn('"', snippet, snippet)

    def test_readmes_require_the_whole_stack_before_use(self):
        for name, heading in (
            ("README.md", "### 0. Instale a stack inteira"),
            ("README.en.md", "### 0. Install the whole stack"),
        ):
            readme = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(heading, readme)
            self.assertLess(readme.index(heading), readme.index("### 1. "))
            self.assertIn("libfreetype", readme)
            self.assertIn("brew install python ffmpeg node git curl", readme)
            self.assertIn("winget install", readme)
            self.assertIn("contact_sheet.labels: true", readme)

    def test_installers_surface_the_doctor_exit_code(self):
        # O doctor sai 4 quando `summary.missing` não está vazio: o instalador repassa o
        # código e diz o que ele significa, em vez de morrer calado pelo `set -e`/`throw`.
        shell = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        self.assertIn("status=$?", shell)
        self.assertIn('exit "$status"', shell)
        for script in (shell, powershell):
            self.assertIn("doctor encontrou pendências", script)
            self.assertIn("faltam itens", script)
            self.assertIn("summary.missing", script)
        self.assertIn("exit 4", powershell)

    def test_installers_warn_about_missing_drawtext(self):
        for name in ("scripts/install.sh", "scripts/install.ps1"):
            script = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("drawtext", script)
            self.assertIn("libfreetype", script)

    def test_contribution_templates_are_present(self):
        bug = ROOT / ".github/ISSUE_TEMPLATE/bug_report.md"
        config = ROOT / ".github/ISSUE_TEMPLATE/config.yml"
        pull_request = ROOT / ".github/PULL_REQUEST_TEMPLATE.md"
        for path in (bug, config, pull_request):
            self.assertTrue(path.is_file(), str(path))
        report = bug.read_text(encoding="utf-8")
        for marker in ("Sistema operacional", "python3 --version", "gb.py doctor"):
            self.assertIn(marker, report, marker)
        self.assertIn("brolls/getbrolls.log", report)
        settings = config.read_text(encoding="utf-8")
        self.assertIn("blank_issues_enabled: true", settings)
        self.assertIn("/security/advisories/new", settings)
        for name, label in (("feature_request.md", "labels: enhancement"), ("question.md", "labels: question")):
            text = (ROOT / ".github/ISSUE_TEMPLATE" / name).read_text(encoding="utf-8")
            self.assertIn(label, text, name)
        template = pull_request.read_text(encoding="utf-8")
        for command in (
            "bash scripts/install.sh --check",
            "python3 scripts/gb.py doctor",
            "python3 -m unittest discover -s tests -v",
        ):
            self.assertIn(command, template, command)

    def test_no_internal_working_material_is_tracked(self):
        """Planos, discovery e estado de agente são material de trabalho, não
        parte da skill pública: ficam fora do repositório e do .gitignore para
        dentro. O que é rastreado também não pode citar caminho de máquina."""
        tracked = (
            subprocess.run(
                ["git", "ls-files", "-z"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            .stdout.decode("utf-8")
            .split("\0")
        )
        forbidden_prefixes = (
            "docs/superpowers/",
            "docs/discovery/",
            ".superpowers/",
            ".claude/",
            ".agents/",
            ".codex/",
            ".playwright-cli/",
        )
        for path in tracked:
            if path.startswith(forbidden_prefixes):
                self.fail(f"material interno rastreado: {path}")
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for prefix in forbidden_prefixes:
            self.assertIn(prefix, ignore, f".gitignore sem {prefix}")
        # Caminho real de máquina, não o exemplo `/Users/...` da regra em AGENTS.md.
        for path in tracked:
            if path == "tests/test_repository.py":
                continue  # o próprio padrão
            if not path or not path.endswith(LOCAL_PATH_SCAN_SUFFIXES):
                continue
            text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
            self.assertIsNone(LOCAL_PATH_PATTERN.search(text), f"caminho local em {path}")

    def test_local_path_pattern_catches_windows_and_uppercase_user_dirs(self):
        """Regressão do regex endurecido: cobre caminho do Windows e usuário
        com maiúscula, sem depender de `git ls-files` — a asserção é sobre o
        padrão em si. Exemplos sintéticos, não caminhos reais de máquina."""
        matching_samples = (
            r"C:\Users\Foo\projeto\notas.md",
            "/Users/Fulano/workspace/nota.md",
            "/home/Fulano/projetos/nota.md",
        )
        for sample in matching_samples:
            self.assertIsNotNone(LOCAL_PATH_PATTERN.search(sample), sample)
        for suffix in (".canvas", ".svg", ".html"):
            self.assertIn(suffix, LOCAL_PATH_SCAN_SUFFIXES, suffix)

    def test_tracked_text_has_no_internal_review_ids(self):
        """Comentário, docstring, nome de teste e documentação explicam o motivo
        técnico; id de revisão interna não entra no repositório público."""
        tracked = (
            subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
            .stdout.decode("utf-8")
            .split("\0")
        )
        problems = []
        for path in filter(None, tracked):
            try:
                text = (ROOT / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binário, ou apagado do worktree sem commit
            patterns = [INTERNAL_REVIEW_ID_PATTERN]
            if path.endswith(".py"):
                patterns.append(INTERNAL_REVIEW_ROUND_PATTERN)
            for number, line in enumerate(text.splitlines(), 1):
                match = next((m for pattern in patterns if (m := pattern.search(line))), None)
                if match:
                    problems.append(f"{path}:{number}: {match.group(0)}")
        self.assertEqual([], problems)

    def test_tracked_text_has_no_process_language(self):
        """Comentário rastreado explica o motivo técnico da supressão de forma
        atemporal: nada de comparar com `origin/main` (apodrece no próximo merge)
        nem de nomear a frente de trabalho ("desta frente", "novo nesta
        release") — quem lê a main não tem como saber o que isso significa."""
        tracked = (
            subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
            .stdout.decode("utf-8")
            .split("\0")
        )
        problems = []
        for path in filter(None, tracked):
            if path == "tests/test_repository.py":
                continue  # o próprio padrão
            try:
                text = (ROOT / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binário, ou apagado do worktree sem commit
            for number, line in enumerate(text.splitlines(), 1):
                match = PROCESS_LANGUAGE_PATTERN.search(line)
                if match:
                    problems.append(f"{path}:{number}: {match.group(0)}")
        self.assertEqual([], problems)

    def test_internal_review_id_pattern_is_precise(self):
        """Exemplos montados em partes, para este arquivo não casar consigo."""
        matching = (
            "RT" + "-07",
            "BUG" + "-14",
            "B" + "-06",
            "(C M" + "-7)",
            "A " + "I1",
            "Minor " + "9",
            "Fix " + "round 2",
            "red" + "-team",
            "Finding " + "3",
            "G" + "11: texto",
            "D3" + "-1",
            "mesmo conteúdo (B" + "1).",
            '    """B' + "2: a rota deixa",
            "# M" + "4: GIT_SSL_CAINFO",
            "rollback (M" + "2) poder",
            "texto I" + "3: fim",
        )
        for sample in matching:
            self.assertIsNotNone(INTERNAL_REVIEW_ID_PATTERN.search(sample), sample)
        for sample in (
            '    """Rodada' + " 3 (decisão): o gate",
            "# round" + " 3: a idade",
            "# Inclui as originais (round" + " 1) e",
            "Task" + " 10: texto",
            '"""Onda' + " 2: texto",
        ):
            self.assertIsNotNone(INTERNAL_REVIEW_ROUND_PATTERN.search(sample), sample)
        legitimate = (
            "B-roll",
            "b-rolls do beat",
            "2026-09-24",
            "insert-02",
            "H.264",
            "Checkpoint C3",
            "class Finding20NasaInvalidLinesTests",
            "(finding #25: duplicado)",
            "ISO-8601",
            "12:30:00",
            "mar, onda",
            "Antes do C3, rejeite",
            "**Checkpoint C3:** descreva",
            "vídeo em H264: ok",
            "áudio M4A (AAC)",
            "erro de I/O: disco",
            "(B-roll)",
        )
        for sample in legitimate:
            self.assertIsNone(INTERNAL_REVIEW_ID_PATTERN.search(sample), sample)
        for sample in (
            "mar, onda",
            '"""Fricção 1 da rodada 2: texto"""',
            "(onda 2 de correções)",
            "test_add_next_mark_status_round_trip",
        ):
            self.assertIsNone(INTERNAL_REVIEW_ROUND_PATTERN.search(sample), sample)

    def test_release_workflow_uses_gh_cli_and_the_pinned_checkout(self):
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        tests = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        pinned = re.search(r"actions/checkout@[0-9a-f]{40}", tests)
        self.assertIsNotNone(pinned, "test.yml sem actions/checkout fixado por SHA de 40 dígitos")
        assert pinned is not None
        checkout = pinned.group(0)
        self.assertIn(checkout, release)
        for marker in (
            "tags:",
            "contents: write",
            "ubuntu-latest",
            "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
            "gh release view",
            "--verify-tag",
            "--notes-file",
            "CHANGELOG.md",
            # O portão antes de publicar é o preflight, chamado contra a
            # árvore já checada (na tag push, é o próprio commit da tag):
            # versão coerente, docs e suíte offline ficam dentro dele
            # (tests/test_repository.py::PreflightTests confere o script em si).
            "bash scripts/preflight.sh",
            '--version "$VERSION"',
            "--prerelease",
        ):
            self.assertIn(marker, release, marker)
        # Disparo manual validaria um commit diferente da tag publicada;
        # só o push de tag pode acionar o release.
        self.assertNotIn("workflow_dispatch", release)
        # O comentário da versão acompanha a Action; o contrato é só o SHA de 40 dígitos.
        # Action que o test.yml também usa repete o mesmo SHA de lá.
        used = [line.split("uses:", 1)[1].strip() for line in release.splitlines() if "uses:" in line]
        for entry in used:
            action = re.sub(r"\s*#.*$", "", entry)
            self.assertRegex(action, r"@[0-9a-f]{40}$", f"{action} sem SHA de 40 dígitos")
            name = action.split("@", 1)[0]
            if f"{name}@" in tests:
                self.assertIn(action, tests, f"{action} fixado por SHA diferente do test.yml")
        self.assertIn("actions/attest-build-provenance", release)
        self.assertIn("id-token: write", release)

    def test_preflight_script_exists_and_contains_the_release_gates(self):
        """`scripts/preflight.sh` é o portão de fato: quem lê `release.yml`
        vê a chamada, mas os passos verificados (versão coerente e suíte
        offline) vivem no script, para rodar igual local e no CI."""
        preflight = ROOT / "scripts" / "preflight.sh"
        self.assertTrue(preflight.is_file())
        self.assertTrue(os.access(preflight, os.X_OK), "scripts/preflight.sh precisa do bit executável")
        text = preflight.read_text(encoding="utf-8")
        self.assertNotIn("\r", text, "scripts/preflight.sh deve usar LF, não CRLF")
        for marker in (
            "--ref",
            "--version",
            "__version__",
            "python3 -m unittest discover -s tests",
            "gen_skill_mirror.py --check",
            "check_anchors.py",
            "PREFLIGHT OK",
        ):
            self.assertIn(marker, text, marker)
        self.assertNotIn(
            "git archive",
            text,
            "preflight.sh não deve usar git archive: esconde paths export-ignore e não tem .git para git ls-files",
        )
        if os.name != "nt" and shutil.which("bash"):
            parsed = subprocess.run(
                ["bash", "-n", str(preflight)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, parsed.returncode, parsed.stderr)

    def test_python_text_io_declares_utf8_explicitly(self):
        problems = []
        for path in (ROOT / "scripts").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"read_text", "write_text"}:
                    continue
                if not any(keyword.arg == "encoding" for keyword in node.keywords):
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual([], problems, "I/O de texto sem UTF-8 explícito")

    def test_text_subprocesses_declare_utf8_explicitly(self):
        problems = []
        for path in (ROOT / "scripts").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "run":
                    continue
                keywords = {keyword.arg: keyword.value for keyword in node.keywords}
                text_mode = keywords.get("text") or keywords.get("universal_newlines")
                if not isinstance(text_mode, ast.Constant) or text_mode.value is not True:
                    continue
                if "encoding" not in keywords:
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual([], problems, "subprocesso textual sem UTF-8 explícito")


if __name__ == "__main__":
    unittest.main()
