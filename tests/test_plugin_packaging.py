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
SETUP_COMMAND = ROOT / "commands" / "get-brolls-setup.md"


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


# Único trecho que pode divergir, e só nas linhas de mecânica de instalação.
DIVERGENT_SECTION = "Instalação e contexto"

# Mecânica de instalação: o que legitimamente muda entre clone-como-skill e plugin.
INSTALLATION_MARKERS = (
    "CLAUDE_PLUGIN_ROOT",
    "scripts/",
    ".env",
    "--env-file",
    "/plugin",
    'python3 "',
)


def skill_body(path):
    """Corpo do SKILL.md sem frontmatter e sem o comentário HTML de sincronia."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)
    return [line for line in text.splitlines() if not line.startswith("<!--")]


def normalize(line):
    """Reduz o espelho ao mesmo texto da raiz: prefixo do plugin e invocação explícita."""
    line = line.replace('"${CLAUDE_PLUGIN_ROOT}/', '"').replace("${CLAUDE_PLUGIN_ROOT}/", "")
    line = re.sub(r'`python3 "([^"`]+)"`', r"`\1`", line)
    # Só o fim da linha é ruído: a indentação faz parte do texto comparado.
    return line.rstrip()


def normalized_sections(path):
    """Linhas normalizadas por seção `##`, preservando a ordem do documento."""
    sections = {"": []}
    current = ""
    for line in skill_body(path):
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if not line.strip():
            continue
        # Linha que some ao normalizar é mantida: some como divergência, não em silêncio.
        sections[current].append(normalize(line))
    return sections


def editorial_only(lines):
    """Linhas da seção de instalação que não descrevem mecânica de instalação."""
    return [
        line
        for line in lines
        if not any(marker in line for marker in INSTALLATION_MARKERS)
    ]


class SetupCommandTests(unittest.TestCase):
    def test_setup_command_is_discoverable_and_complete(self):
        self.assertTrue(SETUP_COMMAND.is_file(), "commands/get-brolls-setup.md ausente")
        self.assertEqual("get-brolls-setup", frontmatter_field(SETUP_COMMAND, "name"))
        self.assertTrue(frontmatter_field(SETUP_COMMAND, "description"))
        body = SETUP_COMMAND.read_text(encoding="utf-8")
        for marker in (
            '"${CLAUDE_PLUGIN_ROOT}/scripts/install.sh" --check',
            '"${CLAUDE_PLUGIN_ROOT}/scripts/install.ps1"',
            '"${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor',
            "summary",
        ):
            self.assertIn(marker, body, f"passo ausente no comando de setup: {marker}")

    def test_plugin_manifest_needs_no_commands_key(self):
        data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertNotIn("commands", data)


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

    def test_mirror_body_matches_root_outside_the_installation_section(self):
        root = normalized_sections(ROOT_SKILL)
        mirror = normalized_sections(MIRROR_SKILL)
        self.assertEqual(
            list(root), list(mirror), "seções divergentes entre raiz e espelho"
        )
        self.assertTrue(
            root[DIVERGENT_SECTION] and mirror[DIVERGENT_SECTION],
            "seção de instalação ausente; a allowlist deixaria de proteger algo",
        )
        for section in root:
            if section == DIVERGENT_SECTION:
                continue
            self.assertEqual(
                root[section],
                mirror[section],
                f"espelho fora de sincronia na seção: {section or 'introdução'}",
            )

    def test_installation_section_may_diverge_only_on_installation_mechanics(self):
        root = normalized_sections(ROOT_SKILL)[DIVERGENT_SECTION]
        mirror = normalized_sections(MIRROR_SKILL)[DIVERGENT_SECTION]
        self.assertEqual(
            editorial_only(root),
            editorial_only(mirror),
            "seção de instalação divergindo fora da mecânica de instalação: "
            "só linhas sobre ${CLAUDE_PLUGIN_ROOT}, scripts/, .env, --env-file, "
            "/plugin ou python3 podem diferir entre raiz e espelho",
        )


if __name__ == "__main__":
    unittest.main()
