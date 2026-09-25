"""Documentação e observabilidade do SDK (Minor 7, 12, 14 e RT-13 da onda final)."""

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
    """Cada comportamento novo desta onda está escrito em SDK.md."""

    def test_sdk_doc_covers_the_final_wave(self):
        text = SDK_DOC.read_text(encoding="utf-8")
        for marker in (
            "keep_signed=True",  # Minor 7: download_url assinado (Envato)
            "PluginError",  # I2
            "nunca vai para o cache",  # RT-09
            "C:\\",  # Minor 1: raízes recusadas
            "pasta de controle de versão aninhada",  # Minor 3 / RT-12
            "dont_write_bytecode",  # nota do red-team
            "tempo limite",  # RT-13: sem timeout nas chamadas de plugin
            "GIT_SSL_CAINFO",  # Minor 12
            "route_consumed_at",  # RT-07
            "preview.route_stage",  # Minor 8
            "rights.evidence",  # RT-04
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertNotIn("URL assinada dentro do JSON some.", text)


if __name__ == "__main__":
    unittest.main()
