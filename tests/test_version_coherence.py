"""Todas as fontes de versão do repositório concordam com `__version__`.

Fontes (as duas ocorrências do `package-lock.json` contam separado):
`scripts/getbrolls/__init__.py`, `package.json`, `package-lock.json` (raiz e
`packages[""]`), `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
`SKILL.md`, `skills/get-brolls/SKILL.md`, `README.md`, `README.en.md`,
`docs/QUALITY.md` e o `$id` de cada `schemas/*.schema.json`. As listas de
"Atualizações" dos READMEs e o corpo do CHANGELOG são prosa humana e ficam
fora — quem escreve nelas é `scripts/bump_version.py`, não este teste.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

# isort: split
# `_paths` acima põe `scripts/` em sys.path; só depois dele o script do bump importa.
import bump_version

from getbrolls import __version__

BUMP_VERSION = ROOT / "scripts" / "bump_version.py"


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _json(relative: str):
    return json.loads(_text(relative))


class VersionCoherenceTest(unittest.TestCase):
    def test_package_json(self):
        self.assertEqual(_json("package.json")["version"], __version__)

    def test_package_lock_json_root(self):
        self.assertEqual(_json("package-lock.json")["version"], __version__)

    def test_package_lock_json_packages_root_entry(self):
        data = _json("package-lock.json")
        self.assertEqual(data["packages"][""]["version"], __version__)

    def test_plugin_json(self):
        data = _json(".claude-plugin/plugin.json")
        self.assertEqual(data["version"], __version__)

    def test_marketplace_json(self):
        data = _json(".claude-plugin/marketplace.json")
        self.assertEqual(data["plugins"][0]["version"], __version__)

    def test_skill_md_metadata_version(self):
        match = re.search(r'metadata:\s*\n\s*version:\s*"(\d+\.\d+\.\d+)"', _text("SKILL.md"))
        self.assertIsNotNone(match, "metadata.version não encontrado em SKILL.md")
        assert match is not None
        self.assertEqual(match.group(1), __version__)

    def test_skill_mirror_md_metadata_version(self):
        text = _text("skills/get-brolls/SKILL.md")
        match = re.search(r'metadata:\s*\n\s*version:\s*"(\d+\.\d+\.\d+)"', text)
        self.assertIsNotNone(match, "metadata.version não encontrado no espelho")
        assert match is not None
        self.assertEqual(match.group(1), __version__)

    def test_readme_badge(self):
        text = _text("README.md")
        url_match = re.search(r"badge/version-(\d+\.\d+\.\d+)-blue", text)
        alt_match = re.search(r'alt="Vers[ãa]o (\d+\.\d+\.\d+)"', text)
        self.assertIsNotNone(url_match, "badge de versão não encontrado em README.md")
        assert url_match is not None
        self.assertIsNotNone(alt_match, "alt de versão não encontrado em README.md")
        assert alt_match is not None
        self.assertEqual(url_match.group(1), __version__)
        self.assertEqual(alt_match.group(1), __version__)

    def test_readme_en_badge(self):
        text = _text("README.en.md")
        url_match = re.search(r"badge/version-(\d+\.\d+\.\d+)-blue", text)
        alt_match = re.search(r'alt="Version (\d+\.\d+\.\d+)"', text)
        self.assertIsNotNone(url_match, "badge de versão não encontrado em README.en.md")
        assert url_match is not None
        self.assertIsNotNone(alt_match, "alt de versão não encontrado em README.en.md")
        assert alt_match is not None
        self.assertEqual(url_match.group(1), __version__)
        self.assertEqual(alt_match.group(1), __version__)

    def test_quality_md_title(self):
        text = _text("docs/QUALITY.md")
        match = re.search(r"^# Qualidade e evidências — GET B-ROLLS (\d+\.\d+\.\d+)$", text, re.MULTILINE)
        self.assertIsNotNone(match, "título de versão não encontrado em docs/QUALITY.md")
        assert match is not None
        self.assertEqual(match.group(1), __version__)

    def test_manual_md_version(self):
        text = _text("docs/MANUAL.md")
        match = re.search(r"^Versão (\d+\.\d+\.\d+)\.", text, re.MULTILINE)
        self.assertIsNotNone(match, "linha de versão não encontrada em docs/MANUAL.md")
        assert match is not None
        self.assertEqual(match.group(1), __version__)

    def test_every_published_schema_id_points_to_the_release_tag(self):
        schemas = sorted((ROOT / "schemas").glob("*.schema.json"))
        self.assertGreaterEqual(len(schemas), 3)
        for path in schemas:
            with self.subTest(schema=path.name):
                schema = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    f"{bump_version.SCHEMA_ID_BASE}/v{__version__}/schemas/{path.name}",
                    schema["$id"],
                )

    def test_bump_version_check_passes_for_current_version(self):
        result = subprocess.run(
            [sys.executable, str(BUMP_VERSION), __version__, "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"bump_version.py --check deveria sair 0 na versão atual.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_bump_version_check_fails_for_a_different_version(self):
        result = subprocess.run(
            [sys.executable, str(BUMP_VERSION), "0.0.1", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 1)


class BumpVersionSchemaIdTests(unittest.TestCase):
    """O bump cobre todo `schemas/*.schema.json` por glob, inclusive schema que ainda vai chegar."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-bump-schemas-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "schemas").mkdir()
        self.write("brief.schema.json", "https://raw.githubusercontent.com/engenheirodevideo/get-brolls/main")
        # Host antigo e schema novo: o bump reescreve os dois para a constante.
        self.write("marketplace_index.schema.json", "https://raw.githubusercontent.com/outro/repo/v1.0.0")

    def write(self, name, base):
        text = '{\n  "title": "x",\n  "$id": "' + base + "/schemas/" + name + '",\n  "type": "object"\n}\n'
        (self.root / "schemas" / name).write_text(text, encoding="utf-8")

    def names(self):
        return [t.name for t in bump_version.schema_targets(self.root)]

    def test_targets_follow_the_glob(self):
        self.assertEqual(["schemas/brief.schema.json", "schemas/marketplace_index.schema.json"], self.names())
        self.assertIn("schemas/export_plan.schema.json", [t.name for t in bump_version.targets(ROOT)])
        self.assertEqual("skills/get-brolls/SKILL.md", bump_version.targets(ROOT)[-1].name)

    def test_write_points_every_schema_at_the_tag_and_keeps_the_layout(self):
        targets = bump_version.schema_targets(self.root)
        self.assertFalse(any(t.check(self.root, "9.9.9") for t in targets))
        for target in targets:
            target.write(self.root, "9.9.9", "2026-01-01")
        self.assertTrue(all(t.check(self.root, "9.9.9") for t in targets))
        text = (self.root / "schemas" / "marketplace_index.schema.json").read_text(encoding="utf-8")
        expected = f"{bump_version.SCHEMA_ID_BASE}/v9.9.9/schemas/marketplace_index.schema.json"
        self.assertEqual('{\n  "title": "x",\n  "$id": "' + expected + '",\n  "type": "object"\n}\n', text)

    def test_schema_without_id_fails_the_check_and_the_write(self):
        (self.root / "schemas" / "sem_id.schema.json").write_text('{"type": "object"}\n', encoding="utf-8")
        target = next(t for t in bump_version.schema_targets(self.root) if "sem_id" in t.name)
        self.assertFalse(target.check(self.root, "9.9.9"))
        with self.assertRaisesRegex(ValueError, "sem_id"):
            target.write(self.root, "9.9.9", "2026-01-01")


if __name__ == "__main__":
    unittest.main()
