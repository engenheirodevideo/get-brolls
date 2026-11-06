"""Descoberta, opt-in, pin de hash e isolamento de falha dos plugins."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _plugin_pins import pin_plugins

from getbrolls.http import ProviderError
from getbrolls.sdk import loader
from getbrolls.sdk.api import PluginApi
from getbrolls.sdk.manifest import read_manifest
from getbrolls.sdk.registry import Registry, get_registry, reset_registry

# A régua de linha longa não se aplica ao texto abaixo: são bytes literais de um
# plugin de mentira, usados por `.replace()`/hash em outros testes; reformatar
# mudaria o valor exato da string.
# pylint: disable=line-too-long
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
# pylint: enable=line-too-long

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
        # Carregar plugin liga `sys.dont_write_bytecode`; o valor volta ao fim do
        # teste para não mudar o comportamento dos testes seguintes.
        self.addCleanup(setattr, sys, "dont_write_bytecode", sys.dont_write_bytecode)
        reset_registry()
        self.addCleanup(reset_registry)

    # MANIFEST/PLUGIN_CODE nunca são mutados; servem só de fixture padrão compartilhada.
    def install(self, manifest=MANIFEST, code=PLUGIN_CODE):  # pylint: disable=dangerous-default-value
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

    def test_gb_plugins_only_filters_pinned_plugins(self):
        """`GB_PLUGINS` escolhe entre os habilitados com pin; nunca carrega sem pin."""
        folder = self.install()
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            row = loader.inventory()[0]
            self.assertEqual("disabled", row["status"])
            self.assertIn("enable", row["reason"])
            self.assertNotIn("demo", get_registry().provider_names())
        loader.enable("demo", confirm=True)
        reset_registry()
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            self.assertEqual("enabled", loader.inventory()[0]["status"])
            self.assertIn("demo", get_registry().provider_names())
        reset_registry()
        with patch.dict(os.environ, {"GB_PLUGINS": "off"}):
            self.assertEqual("disabled", loader.inventory()[0]["status"])
            self.assertNotIn("demo", get_registry().provider_names())
        reset_registry()
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# adulterado\n", encoding="utf-8")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            self.assertEqual("suspended", loader.inventory()[0]["status"])
            self.assertNotIn("demo", get_registry().provider_names())

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
        assert code != PLUGIN_CODE  # replace() sem alvo encontrado devolveria o original e esvaziaria o teste
        self.install(code=code)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("RuntimeError", reg.plugins["demo"]["reason"])
        self.assertNotIn("demo", reg.provider_names())
        self.assertIn("youtube", reg.provider_names())

    def test_undeclared_contribution_fails_the_plugin(self):
        self.install({**MANIFEST, "contributes": {"providers": ["demo"]}})
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("presets", reg.plugins["demo"]["reason"])

    def test_plugin_cannot_take_a_builtin_name(self):
        code = PLUGIN_CODE.replace('name = "demo"', 'name = "youtube"')
        assert code != PLUGIN_CODE  # replace() sem alvo encontrado devolveria o original e esvaziaria o teste
        self.install({**MANIFEST, "contributes": {"providers": ["youtube"], "presets": ["demo"]}}, code=code)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertEqual("core", reg.owner("provider", "youtube"))

    def test_system_exit_at_import_is_isolated_like_any_other_exception(self):
        """`sys.exit(0)` no import do plugin é `SystemExit`, não `Exception` —
        sem captura explícita ele atravessa o loader e derruba o processo com exit 0."""
        self.install(code="import sys\n\nsys.exit(0)\n")
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("SystemExit", reg.plugins["demo"]["reason"])
        self.assertNotIn("demo", reg.provider_names())
        self.assertIn("youtube", reg.provider_names())


class LoaderLoggingTests(LoaderTestCase):
    def test_load_and_failure_are_logged_without_secrets(self):
        self.install()
        pin_plugins("demo")
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
        pin_plugins("demo")
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

        api = PluginApi(read_manifest(self.install()), Registry())
        with patch.dict(os.environ, {"DEMO_TOKEN": "x", "OUTRA": "y"}):
            self.assertEqual("x", api.env("DEMO_TOKEN"))
            with self.assertRaises(ValueError):
                api.env("OUTRA")
        with self.assertRaises(ProviderError), self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            api.get_json("https://outro.example/api?token=segredo123")
        self.assertIn("event=plugin_request_refused", "\n".join(cm.output))
        with patch("getbrolls.sdk.api.get_json", return_value={"ok": True}) as fake:
            self.assertEqual({"ok": True}, api.get_json("https://demo.example/api", {"q": "a"}))
        fake.assert_called_once()

    def test_refused_host_message_never_echoes_the_url(self):
        """Cheap minor: quando `urlsplit` não acha host nenhum (URL sem esquema/netloc),
        a mensagem tem que dizer "-", nunca ecoar a URL crua (poderia carregar
        token/query sensível — `urlsplit` não valida isso, só não achou host)."""

        api = PluginApi(read_manifest(self.install()), Registry())
        url = "sem-host-nenhum?token=segredo123"
        with self.assertRaises(ProviderError) as caught:
            api.get_json(url)
        self.assertNotIn(url, str(caught.exception))
        self.assertNotIn("segredo123", str(caught.exception))

    def test_malformed_url_becomes_provider_error_not_a_raw_valueerror(self):
        """Cheap minor: `urlsplit` pode levantar ValueError pra URL malformada (ex.:
        IPv6 inválido) — isso tem que virar ProviderError, não vazar cru."""

        api = PluginApi(read_manifest(self.install()), Registry())
        with self.assertRaises(ProviderError):
            api.get_json("https://[::1/x")


class HashPinTamperTests(LoaderTestCase):
    """`.pyc` plantado não pode driblar o pin de hash."""

    def test_planted_pycache_bytecode_blocks_instead_of_running(self):
        folder = self.install()
        self.assertTrue(loader.enable("demo", confirm=True)["enabled"])
        self.assertIn("demo", get_registry().provider_names())

        pycache = folder / "__pycache__"
        pycache.mkdir()
        (pycache / "plugin.cpython-311.pyc").write_bytes(b"not real bytecode")

        reset_registry()
        # Bytecode ao lado da fonte deixa o plugin `invalid` (antes: `suspended`).
        self.assertEqual("invalid", loader.inventory()[0]["status"])
        self.assertNotIn("demo", get_registry().provider_names())
        self.assertIn("youtube", get_registry().provider_names())


class JunkFileDigestTests(LoaderTestCase):
    """`.DS_Store`/`git pull` num plugin não pode suspendê-lo."""

    def test_os_junk_and_vcs_dir_do_not_change_the_pinned_hash(self):
        folder = self.install()
        self.assertTrue(loader.enable("demo", confirm=True)["enabled"])

        (folder / ".DS_Store").write_bytes(b"lixo do Finder")
        (folder / "Thumbs.db").write_bytes(b"lixo do Explorer")
        (folder / "desktop.ini").write_text("[.ShellClassInfo]\n", encoding="utf-8")
        git = folder / ".git"
        git.mkdir()
        (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (git / "config").write_text("[core]\n", encoding="utf-8")

        reset_registry()
        self.assertEqual("enabled", loader.inventory()[0]["status"])
        self.assertIn("demo", get_registry().provider_names())

    def test_a_real_source_change_still_suspends(self):
        """Junk/VCS exclusion não pode virar uma brecha geral no pin de hash."""
        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "extra.py").write_text("# arquivo novo de verdade\n", encoding="utf-8")
        reset_registry()
        self.assertEqual("suspended", loader.inventory()[0]["status"])


class CorruptStateTests(LoaderTestCase):
    """`plugins.json` corrompido não pode derrubar os built-ins."""

    def _corrupt(self, raw_bytes):
        self.install()
        loader.state_path().write_bytes(raw_bytes)

    def _assert_isolated(self, raw_bytes):
        self._corrupt(raw_bytes)
        with self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            reg = get_registry()
        self.assertIn("youtube", reg.provider_names())
        self.assertIn("event=plugin_failed", "\n".join(cm.output))
        with self.assertRaises(ValueError) as ctx:
            loader.inventory()
        self.assertIn("plugins.json", str(ctx.exception))

    def test_bad_shaped_entry_does_not_take_down_builtins(self):
        self._assert_isolated(json.dumps({"enabled": {"demo": "x"}}).encode("utf-8"))

    def test_invalid_json_does_not_take_down_builtins(self):
        self._assert_isolated(b"{")

    def test_non_utf8_bytes_do_not_take_down_builtins(self):
        self._assert_isolated(b"\xff\xfe\x00\x01")


if __name__ == "__main__":
    unittest.main()
