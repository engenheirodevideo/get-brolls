"""`PluginError`: a única exceção de plugin cujo texto chega à pessoa (I2 da revisão final)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import ROOT
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import cli, providers
from getbrolls.http import ProviderError
from getbrolls.runtime import OperationError
from getbrolls.sdk import PluginError, guard
from getbrolls.sdk.scaffold import ROUTE

EXAMPLES = ROOT / "examples" / "plugins"
SECRET = "tok_live_ABCDEF123456"

PLUGIN = """
from getbrolls.sdk import PluginError, ProviderCapabilities


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True)

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        raise RAISED

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


def comando(args, ctx):
    raise RAISED


def register(api):
    api.provider(Fonte(api))
    api.command("ola", comando, "Diz olá")
"""

PLUGIN_MANIFEST = {
    **MANIFEST,
    "contributes": {"providers": ["demo"], "commands": ["ola"]},
    "permissions": {"network": ["demo.example"], "env": ["DEMO_TOKEN"]},
}


class PluginErrorSurfaceTests(unittest.TestCase):
    def test_plugin_error_is_public_and_a_provider_error(self):
        self.assertTrue(issubclass(PluginError, ProviderError))


class PluginErrorFlowTests(LoaderTestCase):
    def enable(self, raised):
        code = PLUGIN.replace("RAISED", raised)
        self.install(PLUGIN_MANIFEST, code=code)
        pin_plugins("demo")
        patcher = patch.dict(os.environ, {"GB_PLUGINS": "demo", "DEMO_TOKEN": SECRET})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plugin_error_text_reaches_search_sanitized(self):
        self.enable("PluginError(f\"Configure DEMO_TOKEN\\n(atual: {self.api.env('DEMO_TOKEN')})\" + 'x' * 400)")
        with self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        message = str(caught.exception)
        self.assertTrue(message.startswith("Plugin demo: Configure DEMO_TOKEN"), message)
        self.assertNotIn(SECRET, message)
        self.assertIn("[REDACTED]", message)
        self.assertNotIn("\n", message)
        self.assertLessEqual(len(message), len("Plugin demo: ") + guard.MESSAGE_MAX_CHARS)

    def test_other_exceptions_and_plugin_error_subclasses_show_only_the_type(self):
        for raised in ("ValueError('Configure DEMO_TOKEN')", "type('Sub', (PluginError,), {})('Configure DEMO_TOKEN')"):
            with self.subTest(raised=raised):
                from getbrolls.sdk.registry import reset_registry

                reset_registry()
                self.enable(raised)
                with self.assertRaises(ProviderError) as caught:
                    providers.search("demo", "mar", 1)
                self.assertNotIn("Configure", str(caught.exception))

    def test_command_plugin_error_text_reaches_gb_x(self):
        self.enable("PluginError('Faltou --arg pasta=...')")
        pin_plugins("demo")
        env = {"GB_HOME": str(self.home), "GB_PLUGINS": "demo"}
        err = run_cli("x", "demo", "ola", expect=2, env=env)
        self.assertIn("Plugin demo: Faltou --arg pasta=...", err["error"])


class ExampleGuidanceTests(LoaderTestCase):
    """As orientações dos exemplos chegam a quem usa (antes: só `(ValueError)`)."""

    def setUp(self):
        super().setUp()
        self.project = Path(tempfile.mkdtemp(prefix="gb-projeto-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.media = Path(tempfile.mkdtemp(prefix="gb-pasta-"))
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

    def copy_example(self, name, paths=None):
        target = self.home / "plugins" / name
        shutil.copytree(EXAMPLES / name, target)
        if paths is not None:
            manifest_path = target / "getbrolls-plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["permissions"]["paths"] = paths
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_banco_http_without_token_says_what_to_configure(self):
        self.copy_example("banco_http")
        pin_plugins("banco_http")
        with patch.dict(os.environ, {"GB_PLUGINS": "banco_http"}):
            os.environ.pop("BANCO_HTTP_TOKEN", None)
            with self.assertRaises(OperationError) as caught:
                cli.main(["search", "--project", str(self.project), "--provider", "banco_http", "--query", "praia"])
        self.assertIn("Configure BANCO_HTTP_TOKEN", str(caught.exception))

    def test_pasta_local_guidance_reaches_search_and_gb_x(self):
        self.copy_example("pasta_local", [str(self.media)])
        pin_plugins("pasta_local")
        with patch.dict(os.environ, {"GB_PLUGINS": "pasta_local"}):
            os.environ.pop("PASTA_LOCAL_DIR", None)
            with self.assertRaises(OperationError) as caught:
                cli.main(["search", "--project", str(self.project), "--provider", "pasta_local", "--query", "praia"])
        self.assertIn("Configure PASTA_LOCAL_DIR", str(caught.exception))
        pin_plugins("pasta_local")
        env = {"GB_HOME": str(self.home), "GB_PLUGINS": "pasta_local", "PASTA_LOCAL_DIR": str(self.media)}
        err = run_cli("x", "pasta_local", "recentes", "--arg", "limite=abc", project=self.project, expect=2, env=env)
        self.assertIn("--arg limite=N espera um número inteiro", err["error"])

    def test_scaffold_route_template_raises_plugin_error(self):
        self.assertIn('raise PluginError("Configure __ENV__', ROUTE)
        self.assertNotIn("raise ValueError", ROUTE)


if __name__ == "__main__":
    unittest.main()
