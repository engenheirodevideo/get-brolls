"""Sem plugin instalado, `providers` e `doctor` saem com os mesmos campos do 2.5.0.

As listas abaixo foram tiradas do código da tag 2.5.0 (`providers.capabilities()` e
o ramo `doctor` de `commands.execute`): nenhuma chave de plugin (`plugin`, `route`,
`plugins`) aparece para quem não instalou nada.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from test_sdk_install import InstallTestCase, write_plugin
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.sdk import install as install_mod
from getbrolls.sdk import loader

# `providers.capabilities()` do 2.5.0: fontes nesta ordem, estes campos por fonte.
V250_PROVIDERS = ["youtube", "instagram", "tiktok", "pexels", "pixabay", "commons", "nasa", "local"]
V250_PROVIDER_FIELDS = {
    "search",
    "resolve_url",
    "account_library",
    "embed",
    "seek",
    "download",
    "transport",
    "configured",
    "env_key",
}
# `doctor` (sem --live) do 2.5.0.
V250_DOCTOR_FIELDS = {
    "summary",
    "contact_sheet",
    "get_brolls",
    "preview",
    "python",
    "tool_paths",
    "executables",
    "resolved",
    "providers",
    "social",
}
V250_DOCTOR_SUMMARY_FIELDS = {"ok", "missing", "optional"}


class NoPluginGoldenTests(unittest.TestCase):
    def setUp(self):
        home = Path(tempfile.mkdtemp(prefix="gb-home-vazio-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        self.env = {"GB_HOME": str(home)}

    def assert_v250_providers(self, providers):
        self.assertEqual(V250_PROVIDERS, list(providers))
        for name, row in providers.items():
            with self.subTest(provider=name):
                self.assertEqual(V250_PROVIDER_FIELDS, set(row))

    def test_providers_has_exactly_the_250_fields(self):
        self.assert_v250_providers(run_cli("providers", env=self.env))

    def test_doctor_has_exactly_the_250_fields(self):
        doctor = run_cli("doctor", env=self.env)
        self.assertEqual(V250_DOCTOR_FIELDS, set(doctor))
        self.assertEqual(V250_DOCTOR_SUMMARY_FIELDS, set(doctor["summary"]))
        self.assert_v250_providers(doctor["providers"])


class PluginsListSelectionTests(LoaderTestCase):
    def test_selection_names_where_the_session_choice_comes_from(self):
        self.install()
        loader.enable("demo", confirm=True)
        env = {"GB_HOME": str(self.home)}
        self.assertEqual("plugins.json", run_cli("plugins", "--action", "list", env=env)["selection"])
        for value in ("demo", "off"):
            with self.subTest(GB_PLUGINS=value):
                listed = run_cli("plugins", "--action", "list", env={**env, "GB_PLUGINS": value})
                self.assertEqual("GB_PLUGINS", listed["selection"])
        switched_off = run_cli("plugins", "--action", "list", env={**env, "GB_PLUGINS": "off"})["plugins"]
        self.assertEqual([("demo", "disabled")], [(row["id"], row["status"]) for row in switched_off])


class UpdateKeepsTheIdTests(InstallTestCase):
    def test_update_refuses_a_source_that_now_carries_another_id(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        before = (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8")
        pinned = self.state()["enabled"]["demo"]["sha256"]

        write_plugin(source, {**MANIFEST, "id": "outro"})
        with self.assertRaises(ValueError) as caught:
            install_mod.update("demo", confirm=False)
        self.assertIn("agora traz o plugin outro", str(caught.exception))
        self.assertIn("nada foi trocado", str(caught.exception))
        self.assertEqual(before, (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8"))
        self.assertEqual(pinned, self.state()["enabled"]["demo"]["sha256"])
        self.assertEqual([], self.leftover_staging())
        self.assertFalse((self.home / "plugins" / "outro").exists())


if __name__ == "__main__":
    unittest.main()
