"""Ida e volta por família (casa): o que o escritor grava vale o que o schema declara e valida contra ele.

Famílias deste grupo: trusted_profiles, profile_toml, capabilities, runtime_marker, plugins_state,
marketplaces, marketplace_index, plugin_manifest, client, clients, recipe, components, rules_block.
Para cada uma, o teste grava com o escritor real, confere que a versão gravada é `schemas.version(nome)`
e que o documento passa em `jsonschema.errors(doc, schemas.load(nome))`. Onde a família lê, o teste
confere também a recusa limpa de uma versão mais nova e a leitura do arquivo sem versão (o de 2.5).
"""

import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _schema_latency
from test_recipe_store import RecipeCase

from getbrolls import _paths, capabilities, cli, clients, commands, profile, recipes, rules, runtime_parts, versioning
from getbrolls.errors import UsageError
from getbrolls.sdk import contracts, jsonschema, loader, marketplace, marketplace_index, scaffold, schemas
from getbrolls.sdk.manifest import ManifestError, compatibility_problem, read_manifest

PIN = {"version": "0.1.0", "sha256": "a" * 64, "digest": loader.DIGEST_FORMAT, "permissions": {}}
RECORD = {
    "source": "https://example.com/m.git",
    "ref": None,
    "commit": "b" * 40,
    "index_sha256": "c" * 64,
    "added_at": "2026-10-24T10:00:00+00:00",
    "updated_at": "2026-10-24T10:00:00+00:00",
}


def _family_version(value: str) -> int:
    found = re.fullmatch(r"getbrolls\.[a-z_]+/([1-9][0-9]*)", value)
    assert found is not None, value
    return int(found.group(1))


class _HomeCase(unittest.TestCase):
    """Uma pasta pessoal temporária por teste."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with  # limpo no addCleanup
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.home = self.tmp / "gbhome"
        self.home.mkdir()
        environ = {k: v for k, v in os.environ.items() if k not in {*profile.FIELDS.values(), "GB_PROFILE"}}
        environ["GB_HOME"] = str(self.home)
        patcher = patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        profile.reset_state()
        self.addCleanup(profile.reset_state)

    def assert_valid(self, doc, name):
        self.assertEqual([], jsonschema.errors(doc, schemas.load(name)), name)


class TrustedProfilesRoundTrip(_HomeCase):
    def trust(self):
        toml = self.tmp / "ws" / profile.PROFILE_NAME
        toml.parent.mkdir()
        toml.write_text('cache_dir = "c"\n', encoding="utf-8")
        toml.chmod(0o644)
        sha = hashlib.sha256(toml.read_bytes()).hexdigest()
        located = profile.Located(toml, "flag", False, (toml,))
        profile.trust(toml, located=located, yes=True, expect=sha)
        return toml, located

    def test_the_store_stamps_the_schema_version_and_validates(self):
        self.trust()
        stored = json.loads((self.home / profile.TRUST_FILE).read_text(encoding="utf-8"))
        self.assertEqual(schemas.version("trusted_profiles"), stored["schema_version"])
        self.assert_valid(stored, "trusted_profiles")
        self.assertEqual(stored["profiles"], profile.read_store(self.home)["profiles"])

    def test_a_newer_or_unversioned_store_is_the_store_corrupt_refusal(self):
        self.trust()
        path = self.home / profile.TRUST_FILE
        stored = json.loads(path.read_text(encoding="utf-8"))
        for doc in (
            {**stored, "schema_version": schemas.version("trusted_profiles") + 1},
            {"profiles": stored["profiles"]},
        ):
            path.write_text(json.dumps(doc), encoding="utf-8")
            with self.subTest(doc=sorted(doc)), self.assertRaises(UsageError) as caught:
                profile.read_store(self.home)
            self.assertEqual("store_corrupt", getattr(caught.exception, "reason", None))
            self.assertIn(profile.TRUST_FILE, str(caught.exception))

    def test_a_store_outside_the_schema_is_corrupt(self):
        self.trust()
        path = self.home / profile.TRUST_FILE
        stored = json.loads(path.read_text(encoding="utf-8"))
        key = next(iter(stored["profiles"]))
        stored["profiles"][key]["trusted_at"] = 17
        path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaises(UsageError) as caught:
            profile.read_store(self.home)
        self.assertEqual("store_corrupt", getattr(caught.exception, "reason", None))

    def test_a_large_store_validates_within_the_budget(self):
        entry = {"sha256": "a" * 64, "trusted_at": "2026-10-24T10:00:00+00:00"}
        doc = {
            "schema_version": schemas.version("trusted_profiles"),
            "profiles": {f"/w/{n}/{profile.PROFILE_NAME}": dict(entry) for n in range(_schema_latency.DEFAULT_ITEMS)},
        }
        _schema_latency.assert_validates_within(self, doc, schemas.load("trusted_profiles"))


class ProfileTomlRoundTrip(_HomeCase):
    def write(self, text):
        path = self.tmp / profile.PROFILE_NAME
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)
        return path

    def test_the_declared_version_loads_and_the_document_validates(self):
        version = schemas.version("profile_toml")
        # `tools.*` precisa ser absoluto no sistema que roda: `/x` no POSIX, `D:\\x` no Windows (lá `/x` não é).
        ffmpeg = str(Path(os.sep + "x").resolve())
        loaded = profile.load(
            self.write(f"schema_version = {version}\ncache_dir = \"c\"\n[tools]\nffmpeg = '{ffmpeg}'\n")
        )
        self.assertIn("GB_CACHE_DIR", loaded.values)
        self.assertEqual(ffmpeg, loaded.values["GB_FFMPEG_PATH"])
        self.assert_valid({"schema_version": version, "cache_dir": "c", "tools": {"ffmpeg": ffmpeg}}, "profile_toml")

    def test_a_profile_without_a_version_still_loads(self):
        self.assertIn("GB_CACHE_DIR", profile.load(self.write('cache_dir = "c"\n')).values)

    def test_a_newer_profile_is_the_existing_usage_refusal(self):
        newer = schemas.version("profile_toml") + 1
        with self.assertRaisesRegex(UsageError, "schema_version do getbrolls.toml"):
            profile.load(self.write(f"schema_version = {newer}\n"))


class CapabilitiesRoundTrip(unittest.TestCase):
    def test_the_manifest_stamps_the_schema_version_and_validates(self):
        doc = capabilities.describe(cli.build_parser())
        self.assertEqual(schemas.version("capabilities"), doc["schema_version"])
        self.assertEqual([], jsonschema.errors(json.loads(json.dumps(doc)), schemas.load("capabilities")))

    def test_the_output_schema_stays_open_for_new_keys(self):
        doc = json.loads(json.dumps(capabilities.describe(cli.build_parser())))
        doc["a_key_from_a_later_version"] = {"x": 1}
        self.assertEqual([], jsonschema.errors(doc, schemas.load("capabilities")))


class RuntimeMarkerRoundTrip(_HomeCase):
    def test_the_marker_stamps_the_schema_version_and_validates(self):
        for part, status in (("venv", "building"), ("tools", "ready")):
            with self.subTest(part=part):
                written = runtime_parts.write_marker(
                    part, self.tmp / "rt", status, {"upgraded": {"yt-dlp": "1", "at": "x"}}
                )
                marker = json.loads(written.read_text(encoding="utf-8"))
                self.assertEqual(schemas.version("runtime_marker"), marker["schema"])
                self.assertEqual(_paths.MARKER_SCHEMA_VERSION, marker["schema"])
                self.assertEqual([], jsonschema.errors(marker, schemas.load("runtime_marker")))
                self.assertTrue(_paths.marker_supported(marker))


class PluginsStateRoundTrip(_HomeCase):
    def test_the_state_stamps_the_family_and_validates(self):
        loader.write_state({"enabled": {"demo": dict(PIN)}})
        stored = json.loads(loader.state_path().read_text(encoding="utf-8"))
        self.assertEqual(versioning.schema_name("plugins", schemas.version("plugins_state")), stored["schema"])
        self.assert_valid(stored, "plugins_state")
        self.assertEqual({"demo": PIN}, loader.read_state()["enabled"])

    def test_a_state_of_2_5_without_the_field_still_reads(self):
        loader.state_path().write_text(json.dumps({"enabled": {"demo": PIN}}), encoding="utf-8")
        self.assertEqual({"demo": PIN}, loader.read_state()["enabled"])

    def test_a_newer_state_is_refused_without_touching_the_file(self):
        newer = versioning.schema_name("plugins", schemas.version("plugins_state") + 1)
        text = json.dumps({"schema": newer, "enabled": {}})
        loader.state_path().write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "versão mais nova do getbrolls"):
            loader.read_state()
        self.assertEqual(text, loader.state_path().read_text(encoding="utf-8"))

    def test_a_pin_outside_the_schema_is_an_invalid_state(self):
        loader.state_path().write_text(json.dumps({"enabled": {"demo": {**PIN, "digest": "dois"}}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "plugins.json inválido"):
            loader.read_state()

    def test_the_digest_enum_is_the_pin_format_of_the_code(self):
        pin = schemas.load("plugins_state")["properties"]["enabled"]["additionalProperties"]
        self.assertEqual([loader.DIGEST_FORMAT], pin["properties"]["digest"]["enum"])

    def test_a_large_state_validates_within_the_budget(self):
        doc = {
            "schema": versioning.schema_name("plugins", schemas.version("plugins_state")),
            "enabled": {f"p{n}": dict(PIN) for n in range(_schema_latency.DEFAULT_ITEMS)},
        }
        _schema_latency.assert_validates_within(self, doc, schemas.load("plugins_state"))


class MarketplacesRoundTrip(_HomeCase):
    def test_the_state_stamps_the_family_and_validates(self):
        # O escritor real do estado; o `marketplace-add` público passa pelo git, fora do alcance do teste.
        marketplace._write_state({"marketplaces": {"loja": dict(RECORD)}})  # pylint: disable=protected-access
        stored = json.loads(marketplace.state_path().read_text(encoding="utf-8"))
        self.assertEqual(schemas.version("marketplaces"), _family_version(stored["schema"]))
        self.assert_valid(stored, "marketplaces")
        self.assertEqual(stored, marketplace.read_state())
        self.assert_valid(marketplace._empty_state(), "marketplaces")  # pylint: disable=protected-access

    def test_a_newer_state_is_refused(self):
        newer = versioning.schema_name("marketplaces", schemas.version("marketplaces") + 1)
        marketplace.state_path().write_text(json.dumps({"schema": newer, "marketplaces": {}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "versão mais nova do getbrolls"):
            marketplace.read_state()


class MarketplaceIndexRoundTrip(unittest.TestCase):
    def test_an_entry_from_a_manifest_validates_inside_an_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            scaffold.new("demo_plugin", "provider", tmp)
            manifest = read_manifest(Path(tmp) / "demo_plugin")
        entry = marketplace_index.entry_from_manifest(
            manifest,
            source={"repo": "https://example.com/p.git", "commit": "d" * 40},
            content_sha256="e" * 64,
            tier="community",
            maintainers=["alguém"],
        )
        index = {
            "schema": versioning.schema_name(marketplace_index.FAMILY, schemas.version("marketplace_index")),
            "name": "loja",
            "plugins": [entry],
            "renames": {},
        }
        self.assertEqual([], jsonschema.errors(index, schemas.load("marketplace_index")))
        validated = marketplace_index.validate_index(index)
        self.assertEqual(index["schema"], validated["schema"])
        self.assertEqual([entry], validated["plugins"])

    def test_a_newer_index_is_refused(self):
        newer = versioning.schema_name(marketplace_index.FAMILY, schemas.version("marketplace_index") + 1)
        with self.assertRaisesRegex(ValueError, "versão mais nova do getbrolls"):
            marketplace_index.validate_index({"schema": newer, "name": "loja", "plugins": [], "renames": {}})

    def test_the_entry_api_floor_is_the_one_the_schema_declares(self):
        entry_schema = schemas.load("marketplace_index")["properties"]["plugins"]["items"]
        self.assertEqual(1, entry_schema["properties"]["sdk_api"]["minimum"])


class PluginManifestRoundTrip(unittest.TestCase):
    def test_the_scaffold_writes_the_schema_api_and_the_manifest_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            scaffold.new("demo_plugin", "route", tmp)
            raw = json.loads((Path(tmp) / "demo_plugin" / "getbrolls-plugin.json").read_text(encoding="utf-8"))
            manifest = read_manifest(Path(tmp) / "demo_plugin")
        self.assertEqual(schemas.version("plugin_manifest"), raw["sdk_api"])
        self.assertEqual(contracts.SDK_API, raw["sdk_api"])
        self.assertEqual([], jsonschema.errors(raw, schemas.load("plugin_manifest")))
        self.assertIsNone(compatibility_problem(manifest))

    def test_a_newer_api_is_the_existing_incompatible_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            scaffold.new("demo_plugin", "provider", tmp)
            path = Path(tmp) / "demo_plugin" / "getbrolls-plugin.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["sdk_api"] = schemas.version("plugin_manifest") + 1
            raw["a_field_of_the_next_api"] = True
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "campo desconhecido"):
                read_manifest(Path(tmp) / "demo_plugin")
            del raw["a_field_of_the_next_api"]
            path.write_text(json.dumps(raw), encoding="utf-8")
            manifest = read_manifest(Path(tmp) / "demo_plugin")
        problem = compatibility_problem(manifest)
        assert problem is not None
        self.assertIn("não é suportado", problem)


class ClientsRoundTrip(_HomeCase):
    def test_the_registry_and_the_client_file_stamp_their_families_and_validate(self):
        root = self.tmp / "Clientes"
        root.mkdir()
        clients.add("acme", str(root), name="Acme")
        registry = json.loads(clients.registry_path().read_text(encoding="utf-8"))
        client = json.loads((root / "acme" / clients.CLIENT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(schemas.version("clients"), _family_version(registry["schema"]))
        self.assertEqual(schemas.version("client"), _family_version(client["schema"]))
        self.assert_valid(registry, "clients")
        self.assert_valid(client, "client")
        self.assert_valid(clients.load_registry(), "clients")

    def test_a_registry_without_the_field_still_reads(self):
        root = self.tmp / "Clientes"
        root.mkdir()
        clients.add("acme", str(root))
        registry = json.loads(clients.registry_path().read_text(encoding="utf-8"))
        del registry["schema"]
        clients.registry_path().write_text(json.dumps(registry), encoding="utf-8")
        self.assertEqual(["acme"], [row["slug"] for row in clients.load_registry()["clients"]])


class RecipesRoundTrip(RecipeCase):
    def test_a_sealed_version_stamps_both_families_and_validates(self):
        self.seal()
        folder = self.slug_dir() / "1"
        recipe = json.loads((folder / recipes.RECIPE_FILE).read_text(encoding="utf-8"))
        components = json.loads((folder / recipes.COMPONENTS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(schemas.version("recipe"), _family_version(recipe["schema"]))
        self.assertEqual(schemas.version("components"), _family_version(components["schema"]))
        self.assertEqual([], jsonschema.errors(recipe, schemas.load("recipe")))
        self.assertEqual([], jsonschema.errors(components, schemas.load("components")))

    def test_a_newer_recipe_is_refused(self):
        self.seal()
        recipe = json.loads((self.slug_dir() / "1" / recipes.RECIPE_FILE).read_text(encoding="utf-8"))
        recipe["schema"] = versioning.schema_name(recipes.FAMILY, schemas.version("recipe") + 1)
        with self.assertRaisesRegex(ValueError, "versão mais nova do getbrolls"):
            recipes.validate_recipe(recipe)


class RulesBlockRoundTrip(_HomeCase):
    @staticmethod
    def block(text):
        return json.loads(re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)[0])

    def test_the_rewritten_block_carries_the_schema_version_and_validates(self):
        template = _paths.data_path("docs", "RULES.md").read_text(encoding="utf-8")
        text, _ = commands.rules_from_flags(template, "user_declaration", "Fulana", "Eu respondo.", "reels")
        block = self.block(text)
        self.assertEqual(schemas.version("rules_block"), block["version"])
        self.assert_valid(block, "rules_block")

    def test_the_seeded_project_rules_validate_and_load(self):
        project = self.tmp / "projeto"
        project.mkdir()
        written = rules.seed_project_rules(project, "horizontal")
        assert written is not None
        block = self.block(written.read_text(encoding="utf-8"))
        self.assertEqual(schemas.version("rules_block"), block["version"])
        self.assert_valid(block, "rules_block")
        loaded = rules.load_rules(project)
        self.assertEqual(schemas.version("rules_block"), loaded["version"])

    def test_a_project_block_without_a_version_reads_as_the_first(self):
        project = self.tmp / "projeto"
        project.mkdir()
        data = self.block(_paths.data_path("docs", "RULES.md").read_text(encoding="utf-8"))
        del data["version"]
        (project / "RULES.md").write_text("```json\n" + json.dumps(data) + "\n```\n", encoding="utf-8")
        with patch.object(rules, "home_dir", return_value=self.tmp / "sem-global"):
            loaded = rules.load_rules(project)
        self.assertEqual(1, loaded["version"])
        self.assert_valid({key: loaded[key] for key in data}, "rules_block")


if __name__ == "__main__":
    unittest.main()
