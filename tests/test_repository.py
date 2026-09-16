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
