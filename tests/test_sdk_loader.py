"""Descoberta, opt-in, pin de hash e isolamento de falha dos plugins."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.sdk import loader
from getbrolls.sdk.registry import get_registry, reset_registry

PLUGIN_CODE = """
from getbrolls.sdk.contracts import ProviderCapabilities


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True, url_hosts=("demo.example",))

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]

    def resolve(self, url):
        return self.api.candidate("demo", "1", "Demo", url)

    def refresh(self, item):
        return item


def register(api):
    api.provider(Fonte(api))
    api.preset("demo", "https://demo.example/licenca", "Demo — verifique a página da fonte: https://demo.example/licenca")
"""

MANIFEST = {
    "id": "demo",
    "name": "Demo",
    "version": "0.1.0",
    "sdk_api": 1,
    "requires_getbrolls": ">=2.5,<3",
    "entry": "plugin.py",
    "contributes": {"providers": ["demo"], "presets": ["demo"]},
    "permissions": {"network": ["demo.example"], "env": ["DEMO_TOKEN"]},
}


class LoaderTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gb-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        env = patch.dict(os.environ, {"GB_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GB_PLUGINS", None)
        reset_registry()
        self.addCleanup(reset_registry)

    def install(self, manifest=MANIFEST, code=PLUGIN_CODE):
        folder = self.home / "plugins" / manifest["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "getbrolls-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "plugin.py").write_text(code, encoding="utf-8")
        return folder


class DiscoveryTests(LoaderTestCase):
    def test_installed_plugin_is_disabled_until_enabled(self):
        self.install()
        self.assertEqual(["disabled"], [row["status"] for row in loader.inventory()])
        self.assertNotIn("demo", get_registry().provider_names())

    def test_enable_without_confirmation_only_previews(self):
        self.install()
        preview = loader.enable("demo", confirm=False)
        self.assertFalse(preview["enabled"])
        self.assertEqual(["DEMO_TOKEN"], preview["plugin"]["permissions"]["env"])
        self.assertIn("não é sandbox", preview["note"])
        self.assertFalse(loader.state_path().exists())

    def test_enabled_plugin_registers_provider_and_preset(self):
        self.install()
        self.assertTrue(loader.enable("demo", confirm=True)["enabled"])
        reg = get_registry()
        self.assertEqual("demo", reg.owner("provider", "demo"))
        self.assertEqual("demo", reg.owner("preset", "demo"))
        self.assertEqual("enabled", reg.plugins["demo"]["status"])

    def test_changed_content_suspends_until_enabled_again(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mudou\n", encoding="utf-8")
        reset_registry()
        self.assertEqual("suspended", loader.inventory()[0]["status"])
        self.assertNotIn("demo", get_registry().provider_names())

    def test_disable_is_idempotent(self):
        self.install()
        loader.enable("demo", confirm=True)
        loader.disable("demo")
        loader.disable("demo")
        self.assertEqual("disabled", loader.inventory()[0]["status"])

    def test_gb_plugins_env_overrides_state_without_pin(self):
        self.install()
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            self.assertEqual("enabled", loader.inventory()[0]["status"])
        with patch.dict(os.environ, {"GB_PLUGINS": "off"}):
            loader.enable("demo", confirm=True)
            self.assertEqual("disabled", loader.inventory()[0]["status"])

    def test_invalid_and_incompatible_plugins_are_listed_not_loaded(self):
        self.install({**MANIFEST, "id": "velho", "requires_getbrolls": ">=9"})
        broken = self.home / "plugins" / "quebrado"
        broken.mkdir(parents=True)
        (broken / "getbrolls-plugin.json").write_text("{", encoding="utf-8")
        statuses = {row["id"]: row["status"] for row in loader.inventory()}
        self.assertEqual("incompatible", statuses["velho"])
        self.assertEqual("invalid", statuses["quebrado"])


class FailureIsolationTests(LoaderTestCase):
    def test_exception_in_register_marks_failed_and_rolls_back(self):
        code = PLUGIN_CODE.replace("    api.preset(", "    raise RuntimeError('boom')\n    api.preset(")
        self.install(code=code)
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("RuntimeError", reg.plugins["demo"]["reason"])
        self.assertNotIn("demo", reg.provider_names())
        self.assertIn("youtube", reg.provider_names())

    def test_undeclared_contribution_fails_the_plugin(self):
        self.install({**MANIFEST, "contributes": {"providers": ["demo"]}})
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("presets", reg.plugins["demo"]["reason"])

    def test_plugin_cannot_take_a_builtin_name(self):
        code = PLUGIN_CODE.replace('name = "demo"', 'name = "youtube"')
        self.install({**MANIFEST, "contributes": {"providers": ["youtube"], "presets": ["demo"]}}, code=code)
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertEqual("core", reg.owner("provider", "youtube"))


class LoaderLoggingTests(LoaderTestCase):
    def test_load_and_failure_are_logged_without_secrets(self):
        self.install()
        with (
            patch.dict(os.environ, {"GB_PLUGINS": "demo", "DEMO_TOKEN": "segredo"}),
            self.assertLogs("getbrolls.sdk", level="DEBUG") as cm,
        ):
            get_registry()
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_loaded", joined)
        self.assertIn("plugin=demo", joined)
        self.assertNotIn("segredo", joined)

    def test_failed_plugin_logs_warning_with_error_class(self):
        self.install(code="def register(api):\n    raise RuntimeError('detalhe interno')\n")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}), self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            get_registry()
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_failed", joined)
        self.assertIn("error=RuntimeError", joined)

    def test_enable_and_disable_are_logged(self):
        self.install()
        with self.assertLogs("getbrolls.sdk", level="INFO") as cm:
            loader.enable("demo", confirm=True)
            loader.disable("demo")
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_enabled", joined)
        self.assertIn("event=plugin_disabled", joined)


class PluginApiTests(LoaderTestCase):
    def test_env_and_network_are_limited_to_declared_permissions(self):
        from getbrolls.http import ProviderError
        from getbrolls.sdk.api import PluginApi
        from getbrolls.sdk.manifest import read_manifest
        from getbrolls.sdk.registry import Registry

        api = PluginApi(read_manifest(self.install()), Registry())
        with patch.dict(os.environ, {"DEMO_TOKEN": "x", "OUTRA": "y"}):
            self.assertEqual("x", api.env("DEMO_TOKEN"))
            with self.assertRaises(ValueError):
                api.env("OUTRA")
        with self.assertRaises(ProviderError), self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            api.get_json("https://outro.example/api")
        self.assertIn("event=plugin_request_refused", "\n".join(cm.output))
        with patch("getbrolls.sdk.api.get_json", return_value={"ok": True}) as fake:
            self.assertEqual({"ok": True}, api.get_json("https://demo.example/api", {"q": "a"}))
        fake.assert_called_once()


if __name__ == "__main__":
    unittest.main()
