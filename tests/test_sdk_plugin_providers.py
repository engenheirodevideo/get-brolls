"""Plugin entra no fluxo comum, mas nunca decide o que é humano."""

import os
import unittest
from unittest.mock import patch

from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase  # noqa: F401  (MANIFEST reexportado)

from getbrolls import presets, providers
from getbrolls.http import ProviderError

GREEDY = PLUGIN_CODE.replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    '        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    '        item["approval"] = {"status": "approved", "by": "plugin", "at": "x", "revision": 0}\n'
    '        item["rights"]["status"] = "permitted"\n'
    '        item["state"] = "approved"\n'
    '        item["campo_solto"] = 1\n'
    '        item["media_url"] = "http://inseguro.example/v.mp4"\n'
    "        return [item]",
)


class PluginTestCase(LoaderTestCase):
    def enable(self, code=PLUGIN_CODE):
        self.install(code=code)
        patcher = patch.dict(os.environ, {"GB_PLUGINS": "demo"})
        patcher.start()
        self.addCleanup(patcher.stop)


class PluginProviderTests(PluginTestCase):
    def test_plugin_search_goes_through_the_common_facade(self):
        self.enable()
        items = providers.search("demo", "mar", 3)
        self.assertEqual(["demo:1"], [i["id"] for i in items])
        self.assertEqual("mar", items[0]["query"])
        self.assertEqual("demo", providers.capabilities()["demo"]["plugin"])

    def test_plugin_cannot_approve_permit_or_add_fields(self):
        self.enable(GREEDY)
        with self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            item = providers.search("demo", "mar", 1)[0]
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_candidate_sanitized", joined)
        self.assertIn("campo_solto", joined)
        self.assertEqual("pending", item["approval"]["status"])
        self.assertEqual("unknown", item["rights"]["status"])
        self.assertEqual("candidate", item["state"])
        self.assertNotIn("campo_solto", item)
        self.assertIsNone(item["media_url"])

    def test_plugin_exception_becomes_provider_error_with_plugin_id(self):
        self.enable(
            PLUGIN_CODE.replace(
                "    def search(self, query, limit, media):\n",
                "    def search(self, query, limit, media):\n        raise KeyError('x')\n",
            )
        )
        with self.assertRaises(ProviderError) as caught, self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            providers.search("demo", "mar", 1)
        self.assertIn("Plugin demo", str(caught.exception))
        self.assertIn("event=plugin_call_failed", "\n".join(cm.output))
        self.assertIn("error=KeyError", "\n".join(cm.output))

    def test_resolve_routes_plugin_hosts_and_keeps_builtin_errors(self):
        self.enable()
        self.assertEqual("demo:1", providers.resolve("https://demo.example/v/1")["id"])
        with self.assertRaises(ProviderError) as caught:
            providers.resolve("https://desconhecido.example/v/1")
        self.assertIn("Fonte de URL não suportada", str(caught.exception))

    def test_refresh_only_takes_media_url_from_plugin(self):
        self.enable()
        item = providers.search("demo", "mar", 1)[0]
        item["approval"]["status"] = "approved"
        with patch.object(
            type(providers._registry().provider("demo")),
            "refresh",
            lambda self, it: {**it, "media_url": "https://demo.example/novo.mp4", "title": "trocado"},
        ):
            fresh = providers.refresh(item)
        self.assertEqual("https://demo.example/novo.mp4", fresh["media_url"])
        self.assertEqual(item["title"], fresh["title"])
        self.assertEqual("approved", fresh["approval"]["status"])

    def test_disabled_plugin_items_refresh_as_unchanged_copies(self):
        item = providers.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        item["provider"] = "demo"
        self.assertEqual(item, providers.refresh(item))

    def test_plugin_preset_is_listed_from_the_manifest_and_served(self):
        self.enable()
        self.assertIn("demo", presets.names())
        self.assertIn("verifique a página da fonte", presets.get("demo")["text"])

    def test_cli_preset_choices_include_enabled_plugin(self):
        self.enable()
        from getbrolls.cli import build_parser

        args = build_parser().parse_args(["permit", "--project", ".", "--candidate", "x", "--preset", "demo"])
        self.assertEqual("demo", args.preset)


if __name__ == "__main__":
    unittest.main()
