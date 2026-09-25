"""Documentação e observabilidade do SDK."""

import os
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT
from _plugin_pins import pin_plugins
from test_sdk_loader import LoaderTestCase
from test_sdk_routes_contracts import ROUTE_MANIFEST, route_code

from getbrolls.sdk.registry import get_registry

SDK_DOC = ROOT / "docs" / "SDK.md"


class PluginLoadedLogTests(LoaderTestCase):
    def test_plugin_loaded_counts_routes_and_commands(self):
        self.install(ROUTE_MANIFEST, code=route_code())
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}), self.assertLogs("getbrolls.sdk", level="INFO") as cm:
            get_registry()
        line = next(entry for entry in cm.output if "event=plugin_loaded" in entry)
        self.assertIn("routes=1", line)
        self.assertIn("commands=1", line)


class SdkDocTests(unittest.TestCase):
    """Cada comportamento do SDK que o autor de plugin precisa saber está escrito em SDK.md."""

    def test_sdk_doc_covers_plugin_behaviors(self):
        text = SDK_DOC.read_text(encoding="utf-8")
        for marker in (
            "keep_signed=True",  # download_url assinado (Envato)
            "PluginError",  # a única exceção cujo texto chega à pessoa
            "nunca vai para o cache",  # resposta de plugin fora do cache em disco
            "C:\\",  # raízes recusadas
            "pasta de controle de versão aninhada",
            "dont_write_bytecode",
            "tempo limite",  # sem timeout nas chamadas de plugin
            "GIT_SSL_CAINFO",
            "route_consumed_at",
            "preview.route_stage",
            "rights.evidence",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertNotIn("URL assinada dentro do JSON some.", text)

    def test_sdk_doc_follows_the_code(self):
        """A lista vem do código, não de uma frase do próprio documento: extensão de
        foto aceita, campo de `ProviderCapabilities`, método de `PluginApi` e status
        de plugin — o que mudar no código e não no SDK.md quebra aqui."""
        import dataclasses

        from getbrolls.commands import PLUGIN_PROBLEM_STATUSES
        from getbrolls.media import SNIFFED_IMAGE_SUFFIXES
        from getbrolls.sdk.api import PluginApi
        from getbrolls.sdk.contracts import ProviderCapabilities

        text = SDK_DOC.read_text(encoding="utf-8")
        expected = [f"`{suffix}`" for suffix in SNIFFED_IMAGE_SUFFIXES]
        expected += [f"| `{field.name}` |" for field in dataclasses.fields(ProviderCapabilities)]
        expected += [f"`api.{name}" for name in dir(PluginApi) if not name.startswith("_")]
        expected += [f"`{status}`" for status in ("disabled", "enabled", *PLUGIN_PROBLEM_STATUSES)]
        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
