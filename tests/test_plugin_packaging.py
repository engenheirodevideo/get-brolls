import json
import re
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import __version__

PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = ROOT / ".claude-plugin" / "marketplace.json"
COMMANDS = ROOT / "commands"
SETUP_COMMAND = COMMANDS / "get-brolls-setup.md"
REFERENCES = ROOT / "references"
MIRROR_SKILL = ROOT / "skills" / "get-brolls" / "SKILL.md"
PLUGIN_NAME = "getbrolls"
LEGACY_PLUGIN_NAME = "get-brolls"
SKILL_NAME = "get-brolls"


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
        self.assertEqual(data["name"], PLUGIN_NAME)
        self.assertEqual(data["version"], __version__)
        for key in ("description", "author", "repository", "license"):
            self.assertIn(key, data)

    def test_marketplace_valid(self):
        data = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "engenheirodevideo")
        self.assertIn("name", data["owner"])
        entries = [p for p in data["plugins"] if p["name"] == PLUGIN_NAME]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], "./")
        self.assertEqual(entries[0].get("version"), __version__)

    def test_plugin_rename_keeps_the_skill_name(self):
        """O plugin virou `getbrolls`; quem instalou `get-brolls` migra pelo `renames`; a skill não muda."""
        plugin = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        marketplace = json.loads(MARKETPLACE_JSON.read_text(encoding="utf-8"))
        self.assertEqual([PLUGIN_NAME], [p["name"] for p in marketplace["plugins"]])
        self.assertEqual(plugin["name"], marketplace["plugins"][0]["name"])
        self.assertEqual({LEGACY_PLUGIN_NAME: PLUGIN_NAME}, marketplace.get("renames"))
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9._-]+", PLUGIN_NAME))
        for skill in (ROOT / "SKILL.md", MIRROR_SKILL):
            self.assertEqual(SKILL_NAME, frontmatter_field(skill, "name"), skill)
        self.assertTrue(MIRROR_SKILL.is_file())

    def test_docs_use_the_new_plugin_namespace(self):
        """Instalação e acionamento citam `getbrolls`; o nome antigo só sobrevive no histórico."""
        historical = {"CHANGELOG.md"}
        documents = [
            path
            for pattern in ("*.md", "docs/*.md", "commands/*.md", "references/*.md", "skills/**/*.md", "eval/README.md")
            for path in ROOT.glob(pattern)
            if path.name not in historical
        ]
        stale = re.compile(r"get-brolls@engenheirodevideo|/get-brolls:|cache/engenheirodevideo/get-brolls/")
        offenders = [
            str(path.relative_to(ROOT)) for path in documents if stale.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual([], offenders, "nome antigo do plugin em: " + ", ".join(offenders))
        for name in ("README.md", "README.en.md", "docs/GUIDE.md"):
            self.assertIn(f"/plugin install {PLUGIN_NAME}@engenheirodevideo", (ROOT / name).read_text(encoding="utf-8"))


class SetupCommandTests(unittest.TestCase):
    def test_setup_command_is_discoverable_and_complete(self):
        self.assertTrue(SETUP_COMMAND.is_file(), "commands/get-brolls-setup.md ausente")
        self.assertEqual("get-brolls-setup", frontmatter_field(SETUP_COMMAND, "name"))
        self.assertTrue(frontmatter_field(SETUP_COMMAND, "description"))
        body = SETUP_COMMAND.read_text(encoding="utf-8")
        for marker in (
            '"${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" setup --check',
            '"${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" setup\n',
            '"${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" doctor',
            "summary",
        ):
            self.assertIn(marker, body, f"passo ausente no comando de setup: {marker}")

    def test_setup_command_reads_the_whole_doctor_verdict(self):
        """O doctor sai 4 com pendências e o JSON em stdout: o comando não para ali."""
        body = SETUP_COMMAND.read_text(encoding="utf-8")
        for marker in ("código 4", "faltam itens", "`summary`", "não pare", "$GB_HOME/.env"):
            self.assertIn(marker, body, f"setup sem a leitura do código 4: {marker}")
        self.assertNotIn("pare no primeiro que falhar", body)

    def test_setup_command_uses_the_shared_runtime(self):
        """O runtime vive em $GB_HOME/runtime: o `/plugin update` não pede reinstalação."""
        body = SETUP_COMMAND.read_text(encoding="utf-8")
        self.assertIn("$GB_HOME/runtime", body)
        self.assertIn("sobrevive ao `/plugin update`", body)
        self.assertIn("rode de novo só se o `doctor` apontar", body)
        self.assertIn("$GB_HOME", frontmatter_field(SETUP_COMMAND, "description") or "")
        for stale in ("dentro da pasta do plugin", "repita `/get-brolls-setup` após cada", "install.sh"):
            self.assertNotIn(stale, body)

    def test_skill_environment_names_the_doctor_exit_code(self):
        for skill in (ROOT / "SKILL.md", ROOT / "skills" / "get-brolls" / "SKILL.md"):
            body = skill.read_text(encoding="utf-8")
            section = body.split("## Ambiente", 1)[1].split("\n## ", 1)[0]
            for marker in ("código 4", "`summary.missing`", "`ready`"):
                self.assertIn(marker, section, f"{skill.name}: {marker}")
            self.assertIn("getbrolls", body.split("## Passo 2", 1)[1].split("\n## ", 1)[0])

    def test_every_command_is_discoverable(self):
        """Todo comando do plugin traz name/description e roda pela raiz do plugin."""
        expected = {
            "get-brolls-setup",
            "get-brolls-brief",
            "get-brolls-eval",
            "get-brolls-status",
            "get-brolls-review",
        }
        found = set()
        for path in sorted(COMMANDS.glob("*.md")):
            name = frontmatter_field(path, "name")
            self.assertEqual(name, path.stem, f"{path.name}: name diverge do arquivo")
            self.assertTrue(frontmatter_field(path, "description"), f"{path.name} sem description")
            found.add(name)
        self.assertEqual(expected, found, "conjunto de comandos do plugin mudou")

    def test_status_command_repasses_the_ready_sentence(self):
        body = (COMMANDS / "get-brolls-status.md").read_text(encoding="utf-8")
        self.assertIn('"${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" status --project', body)
        self.assertIn("summary.do.for_human", body)
        self.assertIn("sem parafrasear", body)

    def test_review_command_serves_then_imports(self):
        body = (COMMANDS / "get-brolls-review.md").read_text(encoding="utf-8")
        for marker in (
            '"${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" review --project',
            '"${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" serve --background --project',
            '"${CLAUDE_PLUGIN_ROOT}/scripts/gb.py" import-review --by',
            "127.0.0.1:8767/review.html",
        ):
            self.assertIn(marker, body, f"passo ausente no comando de revisão: {marker}")
        self.assertNotIn("--file", body.split("import-review --by")[1].split("\n")[0])

    def test_plugin_manifest_needs_no_commands_key(self):
        data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        self.assertNotIn("commands", data)


class ReferencesTests(unittest.TestCase):
    """`references/` viaja com o plugin: é de onde o agente tira a copy pronta."""

    EXPECTED = (
        "templates-de-resposta.md",
        "glossario.md",
        "interview.md",
        "providers.md",
        "instagram.md",
        "rights.md",
    )

    def test_reference_files_ship_with_the_plugin(self):
        self.assertTrue(REFERENCES.is_dir(), "pasta references/ ausente")
        for name in self.EXPECTED:
            path = REFERENCES / name
            self.assertTrue(path.is_file(), f"references/{name} ausente")
            self.assertEqual("reference", frontmatter_field(path, "type"))

    def test_providers_reference_keeps_the_two_editorial_guards(self):
        body = (REFERENCES / "providers.md").read_text(encoding="utf-8")
        self.assertIn("Literal primeiro", body)
        self.assertIn("pedir stock explicitamente", body)

    def test_instagram_reference_keeps_the_two_stream_procedure(self):
        body = (REFERENCES / "instagram.md").read_text(encoding="utf-8")
        for marker in (
            "_video.conf",
            "_audio.conf",
            "instagram_pairs.py",
            "--fail-on-duplicate-audio",
            "wait_seconds",
            "--pace 20-60",
            "cooldown",
        ):
            self.assertIn(marker, body, f"procedimento do Instagram perdeu: {marker}")

    def test_every_reference_states_the_path_convention(self):
        """Cada reference é lida sozinha: a regra de caminho vai em cada uma."""
        for name in ("providers.md", "instagram.md", "rights.md"):
            body = (REFERENCES / name).read_text(encoding="utf-8")
            self.assertIn("caminho absoluto da instalação da skill", body, name)
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/scripts/gb.py", body, name)
            self.assertIn("--project", body, name)
            self.assertIn("No Windows, use `python`", body, name)

    def test_providers_reference_keeps_the_scan_fallback(self):
        body = (REFERENCES / "providers.md").read_text(encoding="utf-8")
        for marker in ("preview --scan", "GB_SCAN_MAX_SECONDS", "900", "--reference-only"):
            self.assertIn(marker, body, f"providers.md perdeu: {marker}")

    def test_rights_reference_covers_the_three_permit_routes(self):
        body = (REFERENCES / "rights.md").read_text(encoding="utf-8")
        for marker in ("--evidence", "--preset", "--declared-by", "--declaration-text"):
            self.assertIn(marker, body, f"rota de permit ausente: {marker}")

    def test_response_templates_cover_both_approval_routes(self):
        body = (REFERENCES / "templates-de-resposta.md").read_text(encoding="utf-8")
        self.assertIn("Salvar decisões", body)
        self.assertIn("aprovei todos", body)
        self.assertIn("--channel chat", body)


if __name__ == "__main__":
    unittest.main()
