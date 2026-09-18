"""Todas as fontes de versão do repositório concordam com `__version__`.

Onze fontes (as duas ocorrências do `package-lock.json` contam separado):
`scripts/getbrolls/__init__.py`, `package.json`, `package-lock.json` (raiz e
`packages[""]`), `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
`SKILL.md`, `skills/get-brolls/SKILL.md`, `README.md`, `README.en.md` e
`docs/QUALITY.md`. As listas de "Atualizações" dos READMEs e o corpo do
CHANGELOG são prosa humana e ficam fora — quem escreve nelas é
`scripts/bump_version.py`, não este teste.
"""

import json
import re
import subprocess
import sys
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT

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

    def test_bump_version_check_passes_for_current_version(self):
        result = subprocess.run(
            [sys.executable, str(BUMP_VERSION), __version__, "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
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
        )
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
