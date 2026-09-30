"""Índice de marketplace (`getbrolls-marketplace.json`): formato estrito e validador do core."""

import copy
import json
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import _paths as gb_paths
from getbrolls.errors import UsageError
from getbrolls.sdk import marketplace_index as mi
from getbrolls.sdk import schemas
from getbrolls.sdk.jsonschema import check_schema, errors

COMMIT = "a" * 40
SHA = "b" * 64


def entry(**changes):
    base = {
        "id": "demo",
        "version": "0.1.0",
        "description": "Fonte de demonstração",
        "tier": "community",
        "source": {
            "type": "git",
            "repo": "https://example.com/org/demo.git",
            "ref": "main",
            "commit": COMMIT,
            "subdir": None,
        },
        "content_sha256": SHA,
        "sdk_api": 1,
        "requires_getbrolls": ">=2.6,<3",
        "permissions": {"network": ["demo.example"], "env": [], "paths": [], "project_write": []},
        "contributes": ["preset", "provider"],
        "requires": {"python": [], "binaries": [], "runtimes": {}, "services": []},
        "platforms": None,
        "license": None,
        "maintainers": ["Pessoa Exemplo"],
        "attestation": None,
        "yanked": False,
        "deprecated": None,
    }
    base.update(changes)
    return base


def index(*entries, **changes):
    base = {
        "schema": "getbrolls.marketplace_index/1",
        "name": "exemplo",
        "description": "Índice de teste",
        "plugins": list(entries) or [entry()],
        "renames": {},
    }
    base.update(changes)
    return base


def published():
    return json.loads((ROOT / "schemas" / "marketplace_index.schema.json").read_text(encoding="utf-8"))


def refused(raw):
    try:
        mi.validate_index(raw)
    except mi.MarketplaceIndexError:
        return True
    return False


MANIFEST = {
    "id": "demo",
    "name": "Demo",
    "description": "Fonte de demonstração",
    "version": "0.1.0",
    "sdk_api": 1,
    "requires_getbrolls": ">=2.6,<3",
    "entry": "plugin.py",
    "contributes": {"providers": ["demo"], "presets": ["demo"], "routes": []},
    "schema": {},
    "signed_fields": {},
    "permissions": {"network": ["demo.example"], "env": [], "paths": [], "project_write": []},
    "homepage": None,
    "license": None,
    "author": None,
    "keywords": [],
    "platforms": None,
    "requires": {"python": [], "binaries": [], "runtimes": {}, "services": []},
}


class IndexFormatTests(unittest.TestCase):
    def test_valid_index_round_trips(self):
        raw = index()
        parsed = mi.parse_index(json.dumps(raw).encode("utf-8"))
        self.assertEqual(raw["plugins"], parsed["plugins"])
        self.assertEqual("exemplo", parsed["name"])
        self.assertEqual(parsed, mi.validate_index(json.loads(json.dumps(parsed))))

    def test_unknown_top_level_and_entry_keys_are_refused(self):
        self.assertTrue(refused(index(generated_at="2026-01-01")))
        self.assertTrue(refused(index(entry(extra=1))))
        missing = entry()
        del missing["yanked"]
        self.assertTrue(refused(index(missing)))
        self.assertTrue(refused({k: v for k, v in index().items() if k != "schema"}))

    def test_schema_field_is_the_namespaced_family(self):
        self.assertTrue(refused(index(schema="getbrolls.marketplace/1")))
        self.assertTrue(refused(index(schema=1)))
        with self.assertRaises(ValueError) as ctx:
            mi.validate_index(index(schema="getbrolls.marketplace_index/2"))
        self.assertIn("versão mais nova", str(ctx.exception))

    def test_repo_forms(self):
        for repo in ("https://example.com/org/repo.git", "git@github.com:org/repo.git", "."):
            with self.subTest(repo=repo):
                self.assertFalse(refused(index(entry(source={**entry()["source"], "repo": repo}))))
        for repo in (
            "/abs/path",
            "C:\\repo",
            "file:///tmp/repo",
            "ext::sh -c touch% /tmp/x",
            "http://example.com/repo.git",
            "https://user:pass@example.com/repo.git",
            "https://token@example.com/repo.git",
            "https://example.com/repo.git?x=1",
            "https://example.com/repo.git#frag",
            "git@-oProxyCommand:repo",
            "..",
            "",
            "https://example.com/a b",
        ):
            with self.subTest(repo=repo):
                self.assertTrue(refused(index(entry(source={**entry()["source"], "repo": repo}))))

    def test_source_fields(self):
        self.assertTrue(refused(index(entry(source={**entry()["source"], "type": "folder"}))))
        self.assertTrue(refused(index(entry(source={**entry()["source"], "ref": "-x"}))))
        self.assertTrue(refused(index(entry(source={**entry()["source"], "subdir": "../x"}))))
        self.assertTrue(refused(index(entry(source={**entry()["source"], "subdir": "a/.GIT/b"}))))
        self.assertFalse(refused(index(entry(source={**entry()["source"], "ref": None, "subdir": "plugins/demo"}))))

    def test_commit_must_be_full_hex(self):
        for commit in ("a" * 39, "A" * 40, "g" * 40, "a" * 64, None):
            with self.subTest(commit=commit):
                self.assertTrue(refused(index(entry(source={**entry()["source"], "commit": commit}))))
        self.assertTrue(refused(index(entry(content_sha256="B" * 64))))

    def test_attestation_must_be_null(self):
        self.assertTrue(refused(index(entry(attestation={"sig": "x"}))))

    def test_contributes_must_be_sorted_singular_kinds(self):
        self.assertTrue(refused(index(entry(contributes=["provider", "preset"]))))
        self.assertTrue(refused(index(entry(contributes=["providers"]))))
        self.assertTrue(refused(index(entry(contributes=["engine"]))))
        self.assertTrue(refused(index(entry(contributes=["preset", "preset"]))))

    def test_tier_must_be_known(self):
        self.assertTrue(refused(index(entry(tier="gold"))))
        for tier in mi.TIERS:
            self.assertFalse(refused(index(entry(tier=tier))))

    def test_entry_fields_reuse_the_manifest_rules(self):
        self.assertTrue(refused(index(entry(id="core"))))
        self.assertTrue(refused(index(entry(id="projeto"))))
        self.assertTrue(refused(index(entry(version="1.0"))))
        self.assertTrue(refused(index(entry(sdk_api=True))))
        self.assertTrue(refused(index(entry(requires_getbrolls="qualquer"))))
        self.assertTrue(refused(index(entry(permissions={"shell": True}))))
        self.assertTrue(refused(index(entry(platforms=[]))))
        self.assertTrue(refused(index(entry(license="MIT; rm"))))
        self.assertTrue(refused(index(entry(yanked="no"))))
        self.assertTrue(refused(index(entry(maintainers="Pessoa"))))
        self.assertFalse(refused(index(entry(platforms=["linux", "darwin"], license="MIT"))))
        self.assertEqual(
            ["darwin", "linux"],
            mi.validate_index(index(entry(platforms=["linux", "darwin"])))["plugins"][0]["platforms"],
        )

    def test_deprecated_shape(self):
        self.assertFalse(refused(index(entry(deprecated={"reason": "use outro", "replacement": None}))))
        self.assertTrue(refused(index(entry(deprecated={"reason": "x"}))))
        self.assertTrue(refused(index(entry(deprecated=True))))

    def test_duplicate_ids_are_refused(self):
        self.assertTrue(refused(index(entry(), entry(version="0.2.0"))))

    def test_renames_must_point_to_existing_and_not_cycle(self):
        other = entry(id="novo")
        self.assertFalse(refused(index(entry(), other, renames={"velho": "novo"})))
        self.assertFalse(refused(index(entry(), other, renames={"antigo": "velho", "velho": "novo"})))
        self.assertTrue(refused(index(entry(), renames={"velho": "sumido"})))
        self.assertTrue(refused(index(entry(), renames={"a1": "b1", "b1": "a1"})))
        self.assertTrue(refused(index(entry(), other, renames={"demo": "novo"})))
        self.assertTrue(refused(index(entry(), renames={"Velho": "demo"})))

    def test_resolve_rename(self):
        raw = mi.validate_index(index(entry(), entry(id="novo"), renames={"antigo": "velho", "velho": "novo"}))
        self.assertEqual("novo", mi.resolve_rename(raw, "antigo"))
        self.assertEqual("novo", mi.resolve_rename(raw, "velho"))
        self.assertIsNone(mi.resolve_rename(raw, "demo"))

    def test_reserved_marketplace_name_refused(self):
        self.assertTrue(refused(index(name="engenheirodevideo")))
        for name in ("-x", "x-", "UPPER", "a" * 65, "com espaço", ""):
            with self.subTest(name=name):
                self.assertTrue(refused(index(name=name)))
        self.assertFalse(refused(index(name="a")))

    def test_oversized_or_deep_index_refused(self):
        with self.assertRaises(mi.MarketplaceIndexError):
            mi.parse_index(b" " * (mi.INDEX_MAX_BYTES + 1))
        deep = json.dumps(index(metadata={})).replace(
            '"metadata": {}', '"metadata": ' + '{"a": ' * 100 + "1" + "}" * 100
        )
        with self.assertRaises(mi.MarketplaceIndexError):
            mi.parse_index(deep.encode("utf-8"))
        with self.assertRaises(mi.MarketplaceIndexError):
            mi.parse_index(b"\xff\xfe")
        with self.assertRaises(mi.MarketplaceIndexError):
            mi.parse_index(b'{"schema": "getbrolls.marketplace_index/1", "schema": "x"}')
        with self.assertRaises(mi.MarketplaceIndexError):
            mi.parse_index(json.dumps(index()).replace('"sdk_api": 1', '"sdk_api": NaN').encode("utf-8"))
        with self.assertRaises(mi.MarketplaceIndexError):
            mi.parse_index(b"[]")


class EntryFromManifestTests(unittest.TestCase):
    def source(self):
        return {"type": "git", "repo": ".", "ref": "main", "commit": COMMIT, "subdir": "plugins/demo"}

    def test_entry_from_manifest_is_deterministic(self):
        first = mi.entry_from_manifest(
            MANIFEST, source=self.source(), content_sha256=SHA, tier="official", maintainers=["Pessoa"]
        )
        shuffled = dict(reversed(list(copy.deepcopy(MANIFEST).items())))
        second = mi.entry_from_manifest(
            shuffled,
            source=dict(reversed(list(self.source().items()))),
            content_sha256=SHA,
            tier="official",
            maintainers=["Pessoa"],
        )
        self.assertEqual(json.dumps(first), json.dumps(second))
        self.assertEqual(list(mi.ENTRY_KEYS), list(first))
        self.assertEqual(["preset", "provider"], first["contributes"])
        self.assertFalse(first["yanked"])
        self.assertIsNone(first["attestation"])
        self.assertEqual(first, mi.validate_entry(first, "demo"))
        self.assertEqual([], mi.manifest_mismatches(first, MANIFEST))
        self.assertEqual([], mi.manifest_warnings(first, MANIFEST))

    def test_manifest_mismatch_detects_permission_drift(self):
        made = mi.entry_from_manifest(
            MANIFEST, source=self.source(), content_sha256=SHA, tier="community", maintainers=[]
        )
        drifted = copy.deepcopy(MANIFEST)
        drifted["permissions"]["env"] = ["SECRET_TOKEN"]
        drifted["version"] = "0.2.0"
        drifted["contributes"]["routes"] = ["demo"]
        found = mi.manifest_mismatches(made, drifted)
        self.assertEqual(3, len(found))
        self.assertTrue(any(line.startswith("permissions") for line in found))
        self.assertTrue(any(line.startswith("version") for line in found))
        self.assertTrue(any(line.startswith("contributes") for line in found))

    def test_description_divergence_is_only_a_warning(self):
        made = mi.entry_from_manifest(
            MANIFEST, source=self.source(), content_sha256=SHA, tier="community", maintainers=[]
        )
        changed = {**MANIFEST, "description": "Outra descrição"}
        self.assertEqual([], mi.manifest_mismatches(made, changed))
        self.assertEqual(1, len(mi.manifest_warnings(made, changed)))
        self.assertTrue(mi.manifest_warnings(made, changed)[0].startswith("description"))

    def test_parse_plugin_ref(self):
        self.assertEqual(("demo", "exemplo"), mi.parse_plugin_ref("demo@exemplo"))
        for text in ("demo", "@exemplo", "demo@", "demo@a@b", "Demo@exemplo", "demo@EX", "demo@engenheirodevideo"):
            with self.subTest(text=text), self.assertRaises(UsageError):
                mi.parse_plugin_ref(text)


class PublishedSchemaTests(unittest.TestCase):
    def test_schema_is_registered_in_the_subset(self):
        check_schema(published())
        rel = "schemas/marketplace_index.schema.json"
        self.assertIn(rel, gb_paths.REQUIRED_DATA)
        manifest = (ROOT / "packaging" / "data_manifest.txt").read_text(encoding="utf-8").splitlines()
        self.assertIn(rel, manifest)
        self.assertIn("marketplace_index", schemas.NAMES)
        self.assertEqual(published(), schemas.load("marketplace_index"))
        self.assertEqual({"const": "getbrolls.marketplace_index/1"}, published()["properties"]["schema"])

    def test_schema_file_agrees_with_validator_on_fixtures(self):
        schema = published()
        good = [
            index(),
            index(entry(tier="official", license="MIT", platforms=["darwin"]), description=None),
            index(entry(source={**entry()["source"], "repo": ".", "ref": None, "subdir": "plugins/demo"})),
            index(
                entry(deprecated={"reason": "velho", "replacement": "novo"}),
                entry(id="novo"),
                renames={"velho2": "novo"},
                metadata={"livre": True},
            ),
        ]
        bad = [
            index(extra=1),
            index(entry(extra=1)),
            index(entry(tier="gold")),
            index(entry(attestation={})),
            index(entry(source={**entry()["source"], "commit": "a" * 12})),
            index(entry(source={**entry()["source"], "repo": "file:///x"})),
            index(entry(source={**entry()["source"], "repo": "/abs"})),
            index(entry(source={**entry()["source"], "repo": "https://u:p@example.com/x"})),
            index(entry(source={**entry()["source"], "type": "folder"})),
            index(entry(content_sha256="x")),
            index(entry(contributes=["providers"])),
            index(entry(yanked=None)),
            index(schema="getbrolls.other/1"),
            index(name="Maiúscula"),
            {k: v for k, v in index().items() if k != "plugins"},
        ]
        for raw in good:
            with self.subTest(good=raw):
                self.assertEqual([], errors(raw, schema))
                self.assertFalse(refused(raw))
        for raw in bad:
            with self.subTest(bad=raw):
                self.assertNotEqual([], errors(raw, schema))
                self.assertTrue(refused(raw))


if __name__ == "__main__":
    unittest.main()
