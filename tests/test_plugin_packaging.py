import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from getbrolls import __version__

PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
ROOT_SKILL = ROOT / "SKILL.md"
MIRROR_SKILL = ROOT / "skills" / "get-brolls" / "SKILL.md"


def frontmatter_field(path, field):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} sem frontmatter"
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith(field + ":"):
            return stripped.split(":", 1)[1].strip().strip('"')
    return None


class PluginManifestTests(unittest.TestCase):
    def test_plugin_manifest_valid(self):
        data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "get-brolls")
        self.assertEqual(data["version"], __version__)
        for key in ("description", "author", "repository", "license"):
            self.assertIn(key, data)

    def test_marketplace_valid(self):
        data = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "engenheirodevideo")
        self.assertIn("name", data["owner"])
        entries = [p for p in data["plugins"] if p["name"] == "get-brolls"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], "./")
        self.assertEqual(entries[0].get("version"), __version__)


class SkillMirrorTests(unittest.TestCase):
    def test_description_identical(self):
        root_desc = frontmatter_field(ROOT_SKILL, "description")
        mirror_desc = frontmatter_field(MIRROR_SKILL, "description")
        self.assertIsNotNone(root_desc)
        self.assertEqual(root_desc, mirror_desc)

    def test_versions_match_package(self):
        self.assertEqual(frontmatter_field(ROOT_SKILL, "version"), __version__)
        self.assertEqual(frontmatter_field(MIRROR_SKILL, "version"), __version__)

    def test_mirror_plugin_root_targets_exist(self):
        body = MIRROR_SKILL.read_text(encoding="utf-8")
        refs = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./\-]+)", body)
        self.assertTrue(refs, "espelho sem referências ${CLAUDE_PLUGIN_ROOT}")
        for ref in refs:
            target = ROOT / ref.split("#", 1)[0]
            self.assertTrue(target.exists(), f"alvo inexistente: {ref}")

    def test_mirror_keeps_operational_rules(self):
        body = MIRROR_SKILL.read_text(encoding="utf-8")
        for marker in (
            "import-review --by",
            "Não se autoaprove",
            "--fail-on-duplicate-audio",
            "permit --evidence",
            "nunca publique URLs assinadas",
        ):
            self.assertIn(marker, body, f"regra operacional ausente no espelho: {marker}")


if __name__ == "__main__":
    unittest.main()
