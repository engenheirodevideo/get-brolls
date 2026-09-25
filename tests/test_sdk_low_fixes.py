"""Lows da revisão completa: B-15 (prévia do enable lista arquivos), B-16 (valor do
settings.json nunca aparece em mensagem), C L-1/L-4/L-10/L-12 (textos que apontam
o conserto certo)."""

import json
import os
import unittest
from unittest.mock import patch

from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import brief, providers
from getbrolls.commands import doctor_plugin_problems
from getbrolls.http import ProviderError
from getbrolls.sdk import loader
from getbrolls.sdk.registry import get_registry

CONFIG_SECRET = "cfg_secret_ABCDEF123"

CONFIG_LEAK_PLUGIN = """
from getbrolls.sdk import PluginError
from getbrolls.sdk.contracts import ProviderCapabilities


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True)

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        token = self.api.config()["token"]
        raise PluginError(f"A API recusou o token {token}; confira a conta.")

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


def register(api):
    api.provider(Fonte(api))
"""


class LowFixesTests(LoaderTestCase):
    def test_settings_json_value_never_reaches_the_message(self):
        self.install({**MANIFEST, "contributes": {"providers": ["demo"]}}, CONFIG_LEAK_PLUGIN)
        data = self.home / "plugin-data" / "demo"
        data.mkdir(parents=True)
        (data / "settings.json").write_text(json.dumps({"token": CONFIG_SECRET}), encoding="utf-8")
        pin_plugins("demo")
        with patch.dict(os.environ, {}), self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        self.assertIn("[REDACTED]", str(caught.exception))
        self.assertNotIn(CONFIG_SECRET, str(caught.exception))

    def test_enable_preview_lists_the_files_it_pins(self):
        self.install()
        preview = loader.enable("demo", confirm=False)
        self.assertEqual(
            {"count": 2, "names": ["getbrolls-plugin.json", "plugin.py"], "truncated": False},
            preview["plugin"]["files"],
        )

    def test_import_failure_is_not_blamed_on_register(self):
        self.install(code="import nao_existe_modulo_xyz\n")
        pin_plugins("demo")
        reason = get_registry().plugins["demo"]["reason"]
        self.assertIn("no import ou no register()", reason)

    def test_wrong_expect_says_it_may_be_a_typo(self):
        with self.assertRaises(ValueError) as caught:
            loader.check_expect("deadbeef", "0" * 64)
        self.assertIn("copiado errado", str(caught.exception))

    def test_brief_names_enable_for_a_disabled_plugin_source(self):
        self.install()
        hint = brief._plugin_source_hint("demo")
        self.assertIn("plugins --action enable --id demo", hint)

    def test_doctor_summary_does_not_point_back_to_the_preload_list(self):
        line = doctor_plugin_problems([{"id": "demo", "status": "failed"}]) or ""
        self.assertIn("plugins[]", line)
        self.assertNotIn("plugins --action list", line)


if __name__ == "__main__":
    unittest.main()
