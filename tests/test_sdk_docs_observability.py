"""Documentação e observabilidade do SDK."""

import dataclasses
import os
import re
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT
from _plugin_pins import pin_plugins
from test_sdk_loader import LoaderTestCase
from test_sdk_routes_contracts import ROUTE_MANIFEST, route_code

from getbrolls.commands import PLUGIN_PROBLEM_STATUSES
from getbrolls.media import SNIFFED_IMAGE_SUFFIXES
from getbrolls.sdk.api import PluginApi
from getbrolls.sdk.contracts import AnalysisAccess, ProviderCapabilities
from getbrolls.sdk.registry import get_registry

SDK_DOC = ROOT / "docs" / "SDK.md"
# `logs.event(_log, logging.X, "nome"…)`, `logs.refusal(_log, "nome"…)` e `logs.timed(_log, "nome"…)`.
_EVENT_CALL = re.compile(r'logs\.(?:event|refusal|timed)\(\s*\w+,\s*(?:logging\.[A-Z]+,\s*)?"([a-z_]+)"')
_PLUGIN_FAMILIES = ("plugin_", "marketplace_", "git_")


def sdk_log_events():
    """Todo evento de plugin, marketplace e fonte git que o código do SDK registra."""
    found = set()
    for path in (ROOT / "scripts" / "getbrolls" / "sdk").rglob("*.py"):
        found.update(_EVENT_CALL.findall(path.read_text(encoding="utf-8")))
    return {name for name in found if name.startswith(_PLUGIN_FAMILIES)}


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
            "declaração auditável, não um sandbox",  # project_write roda in-process
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertNotIn("URL assinada dentro do JSON some.", text)

    def test_sdk_doc_lists_every_plugin_marketplace_and_git_log_event(self):
        """Evento novo de plugin, marketplace ou fonte git só entra no código junto com o nome no SDK.md."""
        text = SDK_DOC.read_text(encoding="utf-8")
        events = sdk_log_events()
        self.assertGreater(len(events), 30)
        self.assertIn("$GB_HOME/logs/getbrolls.log", text)
        for name in sorted(events):
            with self.subTest(event=name):
                self.assertIn(f"`{name}`", text)

    def test_sdk_doc_follows_the_code(self):
        """A lista vem do código, não de uma frase do próprio documento: extensão de
        foto aceita, campo de `ProviderCapabilities`, método de `PluginApi` e status
        de plugin — o que mudar no código e não no SDK.md quebra aqui."""

        text = SDK_DOC.read_text(encoding="utf-8")
        expected = [f"`{suffix}`" for suffix in SNIFFED_IMAGE_SUFFIXES]
        expected += [f"| `{field.name}` |" for field in dataclasses.fields(ProviderCapabilities)]
        expected += [f"`api.{name}" for name in dir(PluginApi) if not name.startswith("_")]
        expected += [
            f"`ctx.analysis.{name}("
            for name in dir(AnalysisAccess)
            if not name.startswith("_") and callable(getattr(AnalysisAccess, name))
        ]
        expected += [f"`{status}`" for status in ("disabled", "enabled", *PLUGIN_PROBLEM_STATUSES)]
        for marker in expected:
            with self.subTest(marker=marker):
                self.assertIn(marker, text)


if __name__ == "__main__":
    unittest.main()
