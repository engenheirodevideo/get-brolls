"""C H-1: o `.env` aceita as chaves de `permissions.env` dos plugins instalados.

O README do `banco_http` e a orientação do `brief`/`status` mandam pôr o token no
`.env`; antes disso travava TODO comando com "variável desconhecida".
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _cli import run_cli
from _paths import ROOT
from _plugin_pins import pin_plugins
from test_sdk_loader import LoaderTestCase

from getbrolls import config

EXAMPLES = ROOT / "examples" / "plugins"


class PluginEnvKeysTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.work = Path(tempfile.mkdtemp(prefix="gb-env-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        shutil.copytree(EXAMPLES / "banco_http", self.home / "plugins" / "banco_http")

    def env_file(self, text):
        path = self.work / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_banco_http_token_in_env_file_is_accepted(self):
        """Fluxo do README: instalar/habilitar e pôr BANCO_HTTP_TOKEN no .env."""
        pin_plugins("banco_http")
        path = self.env_file("BANCO_HTTP_TOKEN=tk_teste_123\nGB_LIBRARY=off\n")
        with patch.dict(os.environ, {}):
            os.environ.pop("BANCO_HTTP_TOKEN", None)
            config.load_env(path)
            self.assertEqual("tk_teste_123", os.environ["BANCO_HTTP_TOKEN"])
        out = run_cli("--env-file", str(path), "providers", env={"GB_HOME": str(self.home)})
        self.assertIn("banco_http", out)

    def test_installed_but_disabled_plugin_key_is_accepted_too(self):
        path = self.env_file("BANCO_HTTP_TOKEN=tk_teste_123\n")
        out = run_cli("--env-file", str(path), "providers", env={"GB_HOME": str(self.home)})
        self.assertIn("youtube", out)

    def test_unknown_key_still_errors(self):
        path = self.env_file("BANCO_HTTP_TOKEN=x\nNAO_DECLARADA=1\n")
        with patch.dict(os.environ, {}), self.assertRaises(ValueError) as caught:
            config.load_env(path)
        self.assertIn("variável desconhecida na linha 2: NAO_DECLARADA", str(caught.exception))
        err = run_cli("--env-file", str(path), "providers", expect=2, env={"GB_HOME": str(self.home)})
        self.assertIn("NAO_DECLARADA", err["error"])

    def test_plugin_cannot_open_core_or_process_keys(self):
        folder = self.home / "plugins" / "banco_http"
        manifest = (folder / "getbrolls-plugin.json").read_text(encoding="utf-8")
        (folder / "getbrolls-plugin.json").write_text(
            manifest.replace('"env": ["BANCO_HTTP_TOKEN"]', '"env": ["BANCO_HTTP_TOKEN", "PYTHONPATH", "LD_PRELOAD"]'),
            encoding="utf-8",
        )
        self.assertEqual(frozenset({"BANCO_HTTP_TOKEN"}), config.plugin_env_keys())
        path = self.env_file("PYTHONPATH=/nao/existe\n")
        with patch.dict(os.environ, {}), self.assertRaises(ValueError):
            config.load_env(path)


if __name__ == "__main__":
    unittest.main()
