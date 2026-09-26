"""Um manifesto gigante ou aninhado demais vira `invalid` naquela linha —
nunca INTERNAL_ERROR em `plugins list`, `doctor` ou `x --list`."""

import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_loader import LoaderTestCase

from getbrolls.sdk import loader, manifest


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

    def test_recursion_while_parsing_is_an_invalid_row(self):

        self.plant("fundo", "{}")
        with patch.object(manifest.json, "loads", side_effect=RecursionError()):
            row = loader.inventory()[0]
        self.assertEqual("invalid", row["status"])
        self.assertIn("aninhamento", row["reason"])


if __name__ == "__main__":
    unittest.main()
