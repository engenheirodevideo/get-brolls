"""Um manifesto gigante ou aninhado demais vira `invalid` naquela linha —
nunca INTERNAL_ERROR em `plugins list`, `doctor` ou `x --list`."""

import json
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.sdk import loader, manifest
from getbrolls.sdk.registry import get_registry, reset_registry


class ManifestBoundsTests(LoaderTestCase):
    def plant(self, name, text):
        folder = self.home / "plugins" / name
        folder.mkdir(parents=True)
        (folder / "getbrolls-plugin.json").write_text(text, encoding="utf-8")
        (folder / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")

    def test_deep_and_huge_manifests_are_invalid_rows(self):
        self.plant("fundo", "[" * 30000 + "]" * 30000)
        self.plant("enorme", "[" * 200000 + "]" * 200000)
        rows = {row["id"]: row for row in loader.inventory()}
        # Abaixo de 64 KB: conforme a versão do Python vira RecursionError ou só
        # "não é objeto" — nos dois casos, uma linha `invalid`.
        self.assertEqual("invalid", rows["fundo"]["status"])
        self.assertEqual("invalid", rows["enorme"]["status"])
        self.assertIn("KB", rows["enorme"]["reason"])

    def test_cli_listing_commands_survive(self):
        self.plant("fundo", "[" * 200000 + "]" * 200000)
        env = {"GB_HOME": str(self.home)}
        listed = run_cli("plugins", "--action", "list", env=env)
        self.assertEqual("invalid", listed["plugins"][0]["status"])
        self.assertIn("youtube", run_cli("providers", env=env))
        run_cli("doctor", env=env)
        self.assertEqual({"commands": []}, run_cli("x", "--list", env=env))

    def test_huge_requires_getbrolls_number_is_a_manifest_error(self):
        with self.assertRaises(manifest.ManifestError):
            manifest.satisfies("2.6.0", ">=" + "9" * 5000)
        with self.assertRaises(manifest.ManifestError):
            manifest.satisfies("2.6.0", ">=2.6,<1" + "0" * 9)

    def test_huge_requires_getbrolls_number_leaves_the_other_plugins_alone(self):
        self.plant("enorme", json.dumps({**MANIFEST, "id": "enorme", "requires_getbrolls": ">=" + "9" * 5000}))
        self.install()
        loader.enable("demo", confirm=True)
        reset_registry()
        rows = {row["id"]: row for row in loader.inventory()}
        self.assertEqual("invalid", rows["enorme"]["status"])
        self.assertIn("requires_getbrolls", rows["enorme"]["reason"])
        self.assertEqual("enabled", rows["demo"]["status"])
        self.assertIn("demo", get_registry().provider_names())
        env = {"GB_HOME": str(self.home)}
        listed = run_cli("plugins", "--action", "list", env=env)
        self.assertEqual({"demo": "enabled", "enorme": "invalid"}, {p["id"]: p["status"] for p in listed["plugins"]})
        self.assertTrue(run_cli("plugins", "--action", "remove", "--id", "enorme", "--yes", env=env)["removed"])

    def test_recursion_while_parsing_is_an_invalid_row(self):

        self.plant("fundo", "{}")
        with patch.object(manifest.json, "loads", side_effect=RecursionError()):
            row = loader.inventory()[0]
        self.assertEqual("invalid", row["status"])
        self.assertIn("aninhamento", row["reason"])


if __name__ == "__main__":
    unittest.main()
