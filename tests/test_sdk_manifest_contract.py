"""Contrato congelado do manifesto em `sdk_api` 1: campos opcionais, `metadata`,
`requires`, `platforms`, `permissions.project_write` e `engines` aposentado."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT
from test_sdk_loader import MANIFEST, LoaderTestCase
from test_sdk_manifest import BASE, write_plugin

from getbrolls.sdk import install as install_mod
from getbrolls.sdk import loader, manifest
from getbrolls.sdk.contracts import SDK_API
from getbrolls.sdk.manifest import ManifestError, read_manifest

FULL = {
    **BASE,
    "homepage": "https://example.com/acme",
    "license": "MIT OR Apache-2.0",
    "author": "Acme Filmes",
    "keywords": ["acervo", "stock-video"],
    "platforms": ["linux", "darwin"],
    "requires": {
        "python": ["psycopg[binary]>=3.1", "requests"],
        "binaries": ["node", "ffprobe"],
        "runtimes": {"node": ">=18", "python": ">=3.11"},
        "services": ["postgres"],
    },
    "metadata": {"hub": {"card": {"color": "#fff", "nested": [1, 2, {"deep": None}]}}},
}


def _read(raw):
    with tempfile.TemporaryDirectory() as tmp:
        return read_manifest(write_plugin(tmp, raw))


class FrozenFieldsTests(unittest.TestCase):
    def test_top_level_fields_are_frozen_for_sdk_api_1(self):
        self.assertEqual(1, SDK_API)
        self.assertEqual(
            frozenset(
                {
                    "id",
                    "name",
                    "description",
                    "version",
                    "sdk_api",
                    "requires_getbrolls",
                    "entry",
                    "contributes",
                    "permissions",
                    "schema",
                    "signed_fields",
                    "engines",
                    "homepage",
                    "license",
                    "author",
                    "keywords",
                    "platforms",
                    "requires",
                    "metadata",
                }
            ),
            manifest.TOP_LEVEL,
        )
        self.assertEqual(("schema", "signed_fields"), manifest.FUTURE_FIELDS)

    def test_permission_keys_are_frozen_for_sdk_api_1(self):
        self.assertEqual(("network", "env", "paths", "project_write"), manifest.PERMISSION_KEYS)
        self.assertEqual(("analysis",), manifest.PROJECT_WRITE_AREAS)
        self.assertEqual(
            {"network": [], "env": [], "paths": [], "project_write": []}, manifest.validate_permissions("x", {})
        )

    def test_unknown_top_level_field_is_still_an_error(self):
        with self.assertRaises(ManifestError) as caught:
            _read({**BASE, "icon": "x.png"})
        self.assertIn("campo desconhecido no manifesto: icon", str(caught.exception))


class OptionalFieldsTests(unittest.TestCase):
    def test_optional_fields_are_accepted_and_normalized(self):
        data = _read(FULL)
        self.assertEqual("https://example.com/acme", data["homepage"])
        self.assertEqual("MIT OR Apache-2.0", data["license"])
        self.assertEqual("Acme Filmes", data["author"])
        self.assertEqual(["acervo", "stock-video"], data["keywords"])
        self.assertEqual(["darwin", "linux"], data["platforms"])
        self.assertEqual(
            {
                "python": ["psycopg[binary]>=3.1", "requests"],
                "binaries": ["node", "ffprobe"],
                "runtimes": {"node": ">=18", "python": ">=3.11"},
                "services": ["postgres"],
            },
            data["requires"],
        )

    def test_absent_optional_fields_have_stable_defaults(self):
        data = _read(BASE)
        self.assertIsNone(data["homepage"])
        self.assertIsNone(data["license"])
        self.assertIsNone(data["author"])
        self.assertEqual([], data["keywords"])
        self.assertIsNone(data["platforms"])  # ausente: todas as plataformas
        self.assertEqual({"python": [], "binaries": [], "runtimes": {}, "services": []}, data["requires"])
        self.assertEqual([], data["permissions"]["project_write"])

    def test_metadata_is_accepted_and_ignored(self):
        data = _read(FULL)
        self.assertNotIn("metadata", data)
        self.assertEqual("acme_drive", _read({**BASE, "metadata": {}})["id"])

    def test_metadata_must_be_an_object(self):
        for bad in ([], "x", 1, None):
            with self.subTest(bad=bad), self.assertRaises(ManifestError) as caught:
                _read({**BASE, "metadata": bad})
            self.assertIn("metadata tem que ser um objeto", str(caught.exception))

    def test_engines_with_content_points_to_requires(self):
        with self.assertRaises(ManifestError) as caught:
            _read({**BASE, "engines": {"hyperframes": ">=0.8"}})
        self.assertIn("engines foi substituído por requires (requires.runtimes)", str(caught.exception))
        self.assertEqual("acme_drive", _read({**BASE, "engines": {}})["id"])

    def test_homepage_refuses_http_and_credentials(self):
        for bad in (
            "http://example.com",
            "https://user:pw@example.com/x",
            "https://token@example.com/x",
            "https://",
            "https://exa mple.com",
            "ftp://example.com",
            "https://example.com/" + "a" * 2048,
            7,
        ):
            with self.subTest(bad=bad), self.assertRaises(ManifestError) as caught:
                _read({**BASE, "homepage": bad})
            self.assertIn("homepage", str(caught.exception))

    def test_license_author_and_keywords_are_bounded(self):
        cases = (
            ("license", "MIT; rm -rf"),
            ("license", ""),
            ("license", "x" * 129),
            ("author", ""),
            ("author", "a" * 121),
            ("author", ["Acme"]),
            ("keywords", ["Maiuscula"]),
            ("keywords", ["dup", "dup"]),
            ("keywords", [f"k{i}" for i in range(11)]),
            ("keywords", "acervo"),
        )
        for field, bad in cases:
            with self.subTest(field=field, bad=bad), self.assertRaises(ManifestError) as caught:
                _read({**BASE, field: bad})
            self.assertIn(field, str(caught.exception))

    def test_license_helper_accepts_null_for_index_entries(self):
        self.assertIsNone(manifest.validate_license("x", None))
        self.assertEqual("MIT", manifest.validate_license("x", "MIT"))
        with self.assertRaises(ManifestError):
            manifest.validate_license("x", 3)


class RequiresTests(unittest.TestCase):
    def test_requires_rejects_unknown_keys_bad_requirements_and_paths_in_binaries(self):
        cases = (
            {"npm": ["x"]},
            {"python": "psycopg"},
            {"python": ["psycopg>=3.1; os_name=='nt'"]},
            {"python": ["git+https://example.com/x.git"]},
            {"python": ["../local"]},
            {"python": ["psycopg~=3.1"]},
            {"python": ["dup", "dup"]},
            {"python": [f"pkg{i}" for i in range(21)]},
            {"binaries": ["/usr/bin/node"]},
            {"binaries": ["bin\\node"]},
            {"binaries": ["../node"]},
            {"binaries": [""]},
            {"runtimes": ["node"]},
            {"runtimes": {"node": "18"}},
            {"runtimes": {"Node!": ">=18"}},
            {"services": ["Postgres SQL"]},
            {"services": [f"s{i}" for i in range(11)]},
            [],
        )
        for bad in cases:
            with self.subTest(bad=bad), self.assertRaises(ManifestError) as caught:
                _read({**BASE, "requires": bad})
            self.assertIn("requires", str(caught.exception))

    def test_requirement_name_strips_extras_and_version(self):
        self.assertEqual("psycopg", manifest.requirement_name("psycopg[binary]>=3.1"))
        self.assertEqual("requests", manifest.requirement_name("requests"))
        self.assertEqual("ruamel.yaml", manifest.requirement_name("ruamel.yaml>=0.17,<1"))

    def test_validate_requires_is_public_and_normalizes(self):
        self.assertEqual(
            {"python": [], "binaries": ["node"], "runtimes": {}, "services": []},
            manifest.validate_requires("x", {"binaries": ["node"]}),
        )


class PlatformsTests(unittest.TestCase):
    def test_platforms_must_be_known_unique_and_non_empty(self):
        for bad in ([], ["darwin", "darwin"], ["macos"], ["win32"], "linux", [None]):
            with self.subTest(bad=bad), self.assertRaises(ManifestError) as caught:
                _read({**BASE, "platforms": bad})
            self.assertIn("platforms", str(caught.exception))
        self.assertEqual(
            ["darwin", "linux", "windows"], manifest.validate_platforms("x", ["windows", "linux", "darwin"])
        )

    def test_current_platform_names_the_three_systems(self):
        for raw, expected in (("darwin", "darwin"), ("linux", "linux"), ("win32", "windows"), ("cygwin", "windows")):
            with self.subTest(raw=raw), patch("sys.platform", raw):
                self.assertEqual(expected, manifest.current_platform())

    def test_absent_platforms_is_compatible_everywhere(self):
        with patch.object(manifest, "current_platform", return_value="freebsd"):
            self.assertIsNone(manifest.compatibility_problem(_read(BASE)))


class ProjectWriteTests(unittest.TestCase):
    def test_project_write_accepts_only_analysis(self):
        data = _read({**BASE, "permissions": {**BASE["permissions"], "project_write": ["analysis"]}})
        self.assertEqual(["analysis"], data["permissions"]["project_write"])
        for bad in (["brolls"], ["analysis", "analysis"], "analysis", [1], ["../analysis"]):
            with self.subTest(bad=bad), self.assertRaises(ManifestError) as caught:
                _read({**BASE, "permissions": {"project_write": bad}})
            self.assertIn("project_write", str(caught.exception))

    def test_unknown_permission_key_is_refused(self):
        with self.assertRaises(ManifestError) as caught:
            _read({**BASE, "permissions": {"network": [], "write": ["x"]}})
        self.assertIn("permissions só aceita network, env, paths e project_write", str(caught.exception))


class WrongPlatformTests(LoaderTestCase):
    def test_wrong_platform_is_incompatible(self):
        other = "windows" if manifest.current_platform() != "windows" else "linux"
        self.install({**MANIFEST, "platforms": [other]})
        rows = {row["id"]: row for row in loader.inventory()}
        self.assertEqual("incompatible", rows["demo"]["status"])
        self.assertIn(other, rows["demo"]["reason"])

        source = Path(tempfile.mkdtemp(prefix="gb-src-"))
        self.addCleanup(shutil.rmtree, source, ignore_errors=True)
        folder = source / "demo_platform"
        folder.mkdir()
        (folder / "getbrolls-plugin.json").write_text(
            json.dumps({**MANIFEST, "id": "demo_platform", "contributes": {}, "platforms": [other]}), encoding="utf-8"
        )
        (folder / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            install_mod.install(str(folder), confirm=False)
        self.assertIn("platforms", str(caught.exception))


class ExampleManifestTests(unittest.TestCase):
    def test_existing_example_manifests_still_validate(self):
        folders = sorted(p.parent for p in (ROOT / "examples" / "plugins").glob("*/getbrolls-plugin.json"))
        self.assertTrue(folders)
        for folder in folders:
            with self.subTest(plugin=folder.name):
                data = read_manifest(folder)
                self.assertIsNone(manifest.compatibility_problem(data))


if __name__ == "__main__":
    unittest.main()
