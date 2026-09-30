"""Quem lê dados do pacote (regras, assets, schemas, plano de exemplo) segue `_paths`."""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import _paths as paths
from getbrolls import commands, media, review, rules, social, storyboard
from getbrolls.cli import build_parser
from getbrolls.errors import DataRootError, PrerequisiteError
from getbrolls.http import ProviderError
from getbrolls.sdk import exporters, scaffold, schemas


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _relocated_wheel(base):
    """Cópia dos dados num pacote falso, com marcas que só a cópia tem."""
    pkg = base / "pkg"
    data = pkg / "_data"
    _write(data / "MANIFEST", "".join(f"{entry}\n" for entry in paths.REQUIRED_DATA))
    for entry in paths.REQUIRED_DATA:
        (data / entry).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / entry, data / entry)
    rules_text = (data / "docs" / "RULES.md").read_text(encoding="utf-8")
    assert '"blocked_domains"' in rules_text
    _write(
        data / "docs" / "RULES.md",
        rules_text.replace('"blocked_domains"', '"blocked_domains": ["sentinela.example"], "x_old"', 1),
    )
    _write(
        data / "assets" / "review.css", "/*sentinela*/" + (data / "assets" / "review.css").read_text(encoding="utf-8")
    )
    _write(data / "docs" / "BRIEF.md", "# sentinela\n" + (data / "docs" / "BRIEF.md").read_text(encoding="utf-8"))
    schema_path = data / "schemas" / "brief.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    _write(schema_path, json.dumps({**schema, "$comment": "sentinela"}))
    _write(data / "assets" / "brand-logo.png", b"logo-sentinela")
    plan_path = data / "examples" / "plans" / "reels.plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["sentinela"] = True
    _write(plan_path, json.dumps(plan))
    return paths.detect(pkg)


class _Ledger:
    def __init__(self):
        self.data = {"project_id": "p1"}


class RelocatedDataRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.wheel = _relocated_wheel(self.tmp)
        patcher = patch.object(paths, "install", return_value=self.wheel)
        patcher.start()
        self.addCleanup(patcher.stop)
        schemas.load.cache_clear()
        self.addCleanup(schemas.load.cache_clear)

    def test_rules_layers_start_from_the_relocated_template(self):
        layers, _ = rules.rules_layers(self.tmp / "project")
        self.assertEqual(self.wheel.package_dir / "_data" / "docs" / "RULES.md", layers[0][0])

    def test_init_brief_copies_the_relocated_template(self):
        project = self.tmp / "project"
        args = build_parser().parse_args(["init-brief", "--project", str(project)])
        commands.execute(args)
        self.assertTrue((project / "BRIEF.md").read_text(encoding="utf-8").startswith("# sentinela"))

    def test_init_rules_reads_the_relocated_template(self):
        project = self.tmp / "project"
        args = build_parser().parse_args(["init-rules", "--project", str(project)])
        commands.execute(args)
        self.assertIn("sentinela.example", (project / "RULES.md").read_text(encoding="utf-8"))

    def test_review_page_carries_the_relocated_css(self):
        page = review.enhance("<style></style><body></body>", _Ledger(), [])
        self.assertIn("/*sentinela*/", page)

    def test_storyboard_logo_comes_from_the_relocated_assets(self):
        self.assertIn(base64.b64encode(b"logo-sentinela").decode("ascii"), storyboard.brand_logo())

    def test_schemas_load_from_the_relocated_data(self):
        self.assertEqual("sentinela", schemas.load("brief")["$comment"])

    def test_example_plan_comes_from_the_relocated_data(self):
        self.assertTrue(exporters.sample_plan()["sentinela"])


class MissingDataTests(unittest.TestCase):
    def setUp(self):
        unknown = paths.detect(Path(tempfile.gettempdir()) / "nada" / "getbrolls")
        patcher = patch.object(paths, "install", return_value=unknown)
        patcher.start()
        self.addCleanup(patcher.stop)
        schemas.load.cache_clear()
        self.addCleanup(schemas.load.cache_clear)

    def test_missing_data_is_a_prerequisite_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(DataRootError) as caught:
            rules.load_rules(tmp)
        self.assertIsInstance(caught.exception, PrerequisiteError)
        self.assertIn("reinstale", str(caught.exception).lower())

    def test_the_command_layer_lets_the_prerequisite_error_through(self):
        # O mapeamento para exit 4 chega com o tratamento de erros da CLI; aqui vale que o
        # tipo e a mensagem chegam intactos até lá, sem virar um ValueError qualquer.
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(["init-brief", "--project", tmp])
            with self.assertRaises(DataRootError) as caught:
                commands.execute(args)
        self.assertIn("reinstale", str(caught.exception).lower())

    def test_a_global_rules_file_does_not_hide_missing_data(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        with patch.dict(os.environ, {"GB_HOME": str(home)}), tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "RULES.md").write_text("sem bloco", encoding="utf-8")
            (home / "RULES.md").write_text("sem bloco", encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                rules.load_rules(tmp)
        self.assertNotIsInstance(caught.exception, DataRootError)

    def test_example_plan_without_data_is_a_data_root_error(self):
        with self.assertRaises(DataRootError):
            exporters.sample_plan()


class RuntimeDirTests(unittest.TestCase):
    def test_runtime_dir_supplies_ytdlp_and_playwright(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ytdlp = _write(base / (".venv/Scripts/yt-dlp.exe" if os.name == "nt" else ".venv/bin/yt-dlp"), "")
            bin_dir = base / ".tools" / "node_modules" / ".bin"
            playwright = _write(bin_dir / ("playwright-cli.cmd" if os.name == "nt" else "playwright-cli"), "")
            with patch.dict(os.environ, {"GB_RUNTIME_DIR": tmp}):
                env = {k: v for k, v in os.environ.items() if k not in ("GB_YTDLP_PATH", "GB_VENV_PATH")}
                with patch.dict(os.environ, env, clear=True):
                    self.assertEqual(ytdlp, social.local_ytdlp())
                    self.assertEqual(playwright, commands._local_playwright())  # pylint: disable=protected-access

    def test_an_explicit_root_keeps_its_own_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / ".tools" / "node_modules" / ".bin"
            playwright = _write(bin_dir / ("playwright-cli.cmd" if os.name == "nt" else "playwright-cli"), "")
            self.assertEqual(playwright, commands._local_playwright(tmp))  # pylint: disable=protected-access


class MissingToolTests(unittest.TestCase):
    def test_missing_ffmpeg_is_a_prerequisite_error(self):
        with self.assertRaises(PrerequisiteError) as caught:
            media.run(["ffmpeg-inexistente-getbrolls"])
        self.assertIn("doctor", str(caught.exception))

    def test_missing_ytdlp_is_both_provider_and_prerequisite_error(self):
        with (
            patch.object(social, "local_ytdlp", return_value=None),
            patch.object(social.shutil, "which", return_value=None),
            self.assertRaises(social.MissingToolError) as caught,
        ):
            social.command()
        self.assertIsInstance(caught.exception, ProviderError)
        self.assertIsInstance(caught.exception, PrerequisiteError)

    def test_checkout_keeps_the_exact_ytdlp_text(self):
        with (
            patch.object(social, "local_ytdlp", return_value=None),
            patch.object(social.shutil, "which", return_value=None),
            self.assertRaises(social.MissingToolError) as caught,
        ):
            social.command()
        self.assertIn("execute bash scripts/install.sh (ou install.ps1) na raiz da skill/plugin", str(caught.exception))
        self.assertIn("python3 scripts/gb.py doctor", str(caught.exception))


class WheelMessageTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        wheel = _relocated_wheel(base)
        patcher = patch.object(paths, "install", return_value=wheel)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_wheel_messages_name_getbrolls(self):
        with self.assertRaises(PrerequisiteError) as caught:
            media.run(["ffmpeg-inexistente-getbrolls"])
        self.assertIn("getbrolls doctor", str(caught.exception))
        self.assertNotIn("scripts/gb.py", str(caught.exception))
        with (
            patch.object(social, "local_ytdlp", return_value=None),
            patch.object(social.shutil, "which", return_value=None),
            self.assertRaises(social.MissingToolError) as tool,
        ):
            social.command()
        self.assertIn("getbrolls setup", str(tool.exception))
        self.assertNotIn("scripts/gb.py", str(tool.exception))
        self.assertIn("python -P -m getbrolls.instagram_pairs", social.doctor()["instagram"])


class ScaffoldTests(unittest.TestCase):
    def test_scaffold_readme_names_the_running_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(scaffold.new("demo_cmd", "command", parent=Path(tmp))["created"])
            readme = (folder / "README.md").read_text(encoding="utf-8")
            test = (folder / "tests" / "test_plugin.py").read_text(encoding="utf-8")
        self.assertIn(f"`{paths.cli_hint()} plugins --action check", readme)
        self.assertNotIn("__CLI__", readme)
        self.assertIn("roda com o Python do getbrolls", test)

    def test_the_scaffolded_test_finds_its_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(scaffold.new("demo_cmd", "command", parent=Path(tmp))["created"])
            done = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                cwd=folder,
                env={**os.environ, "PYTHONPATH": str(ROOT / "scripts"), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, done.returncode, done.stderr)


if __name__ == "__main__":
    unittest.main()
