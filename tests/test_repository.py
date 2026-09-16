import ast
import re
import unicodedata
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def github_slug(heading):
    value = unicodedata.normalize("NFC", heading.strip().lower())
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return value.replace(" ", "-")


# Destinos que o hub de AGENTS.md precisa rotear: um por público/finalidade.
HUB_TARGETS = (
    "SKILL.md",
    "skills/get-brolls/SKILL.md",
    "agents/openai.yaml",
    "commands/get-brolls-setup.md",
    "GEMINI.md",
    "GUIDE.md",
    "QUALITY.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
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
        links = {
            raw.strip().strip("<>").partition("#")[0]
            for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", agents)
        }
        missing = [target for target in HUB_TARGETS if target not in links]
        self.assertEqual([], missing, "hub sem link para: " + ", ".join(missing))
        for marker in HUB_INVOCATIONS:
            self.assertIn(marker, agents, f"hub sem o acionamento: {marker}")

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
        documents = {
            path.resolve(): path.read_text(encoding="utf-8")
            for path in ROOT.glob("*.md")
        }
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
            self.assertIn(
                "https://www.instagram.com/zbrunomoreira/", readme
            )

    def test_native_windows_entrypoints_are_present_and_documented(self):
        installer = ROOT / "scripts/install.ps1"
        playwright = ROOT / "scripts/playwright.ps1"
        self.assertTrue(installer.is_file())
        self.assertTrue(playwright.is_file())
        self.assertIn(
            ".venv\\Scripts\\python.exe", installer.read_text(encoding="utf-8")
        )
        self.assertIn(
            "playwright-cli.cmd", playwright.read_text(encoding="utf-8")
        )
        self.assertIn("Invoke-Native", installer.read_text(encoding="utf-8"))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("macOS e Windows", readme)
        self.assertIn("scripts/install.ps1", readme)

    def test_ci_runs_primary_matrix_on_macos_and_windows(self):
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertIn("macos-latest", workflow)
        self.assertIn("windows-latest", workflow)
        self.assertIn("./scripts/install.ps1\n", workflow)

    def test_delivery_has_no_parallel_artifact_or_reference_trees(self):
        for name in ("artifacts", "dist", "references", "reference", "broll", "instagram"):
            self.assertFalse((ROOT / name).exists(), name)
        scripts = ROOT / "scripts"
        self.assertEqual(
            ["getbrolls"],
            sorted(path.name for path in scripts.iterdir() if path.is_dir() and path.name != "__pycache__" and not path.name.startswith(".")),
        )

    def test_public_source_has_no_legacy_product_identity(self):
        legacy = "auto" + "edit"
        problems = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".png"}:
                continue
            if (
                legacy in path.name.lower()
                or legacy
                in path.read_text(encoding="utf-8", errors="ignore").lower()
            ):
                problems.append(str(path.relative_to(ROOT)))
        self.assertEqual([], problems)

    def test_security_documents_egress_and_absence_of_telemetry(self):
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        for marker in (
            "PyPI",
            "npm ci --ignore-scripts",
            "yt-dlp/curl",
            "Sem telemetria",
        ):
            self.assertIn(marker, security, marker)

    def test_installers_name_the_validated_python_range(self):
        shell = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        self.assertIn("validado em Python 3.11–3.13", shell)
        self.assertIn("validado em Python 3.11-3.13", powershell)
        self.assertIn("exit 1", shell)
        self.assertIn("throw", powershell)

    def test_contribution_templates_are_present(self):
        bug = ROOT / ".github/ISSUE_TEMPLATE/bug_report.md"
        config = ROOT / ".github/ISSUE_TEMPLATE/config.yml"
        pull_request = ROOT / ".github/PULL_REQUEST_TEMPLATE.md"
        for path in (bug, config, pull_request):
            self.assertTrue(path.is_file(), str(path))
        report = bug.read_text(encoding="utf-8")
        for marker in ("Sistema operacional", "python3 --version", "gb.py doctor"):
            self.assertIn(marker, report, marker)
        self.assertIn("blank_issues_enabled: true", config.read_text(encoding="utf-8"))
        template = pull_request.read_text(encoding="utf-8")
        for command in (
            "bash scripts/install.sh --check",
            "python3 scripts/gb.py doctor",
            "python3 -m unittest discover -s tests -v",
        ):
            self.assertIn(command, template, command)

    def test_release_workflow_uses_gh_cli_and_the_pinned_checkout(self):
        release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        tests = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        checkout = re.search(r"actions/checkout@[0-9a-f]{40}", tests).group(0)
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
        ):
            self.assertIn(marker, release, marker)
        self.assertEqual(
            [f"uses: {checkout} # v4"],
            [line.strip("- ").strip() for line in release.splitlines() if "uses:" in line],
            "release.yml deve usar apenas o checkout já fixado por SHA",
        )

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
