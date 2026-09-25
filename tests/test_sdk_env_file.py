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

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
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

    def declare(self, *keys):
        folder = self.home / "plugins" / "banco_http"
        manifest = (folder / "getbrolls-plugin.json").read_text(encoding="utf-8")
        extra = "".join(f', "{key}"' for key in keys)
        (folder / "getbrolls-plugin.json").write_text(
            manifest.replace('"env": ["BANCO_HTTP_TOKEN"]', '"env": ["BANCO_HTTP_TOKEN"' + extra + "]"),
            encoding="utf-8",
        )

    def test_only_the_plugins_own_namespace_is_accepted(self):
        """Mesmo declaradas no manifesto, variáveis fora de BANCO_HTTP_ não entram pelo .env."""
        outro = self.home / "plugins" / "outro"
        shutil.copytree(self.home / "plugins" / "banco_http", outro)
        manifest = (outro / "getbrolls-plugin.json").read_text(encoding="utf-8")
        (outro / "getbrolls-plugin.json").write_text(
            manifest.replace('"id": "banco_http"', '"id": "outro"').replace("BANCO_HTTP_TOKEN", "OUTRO_TOKEN"),
            encoding="utf-8",
        )
        refused = ("HTTPS_PROXY", "SSLKEYLOGFILE", "NODE_OPTIONS", "OUTRO_TOKEN", "PYTHONPATH", "GB_LIBRARY_X")
        self.declare(*refused)
        self.assertEqual(frozenset({"BANCO_HTTP_TOKEN", "OUTRO_TOKEN"}), config.plugin_env_keys())
        for key in refused:
            if key == "OUTRO_TOKEN":
                continue
            with self.subTest(key=key):
                path = self.env_file(f"BANCO_HTTP_TOKEN=ok\n{key}=x\n")
                with patch.dict(os.environ, {}), self.assertRaises(ValueError) as caught:
                    config.load_env(path)
                self.assertIn(key, str(caught.exception))
                self.assertIn("espaço de nomes", str(caught.exception))
        path = self.env_file("BANCO_HTTP_TOKEN=ok\nOUTRO_TOKEN=y\n")
        with patch.dict(os.environ, {}):
            config.load_env(path)
            self.assertEqual("y", os.environ["OUTRO_TOKEN"])

    def test_key_of_a_removed_plugin_says_how_to_fix(self):
        pin_plugins("banco_http")
        shutil.rmtree(self.home / "plugins" / "banco_http")
        path = self.env_file("BANCO_HTTP_TOKEN=tk\n")
        with patch.dict(os.environ, {}), self.assertRaises(ValueError) as caught:
            config.load_env(path)
        message = str(caught.exception)
        self.assertIn("banco_http", message)
        self.assertIn("Tire a linha do .env", message)
        self.assertIn("reinstale", message)

    def test_enable_preview_warns_about_core_and_foreign_keys(self):
        from getbrolls.sdk import loader

        pasta = self.home / "plugins" / "pasta_local"
        shutil.copytree(EXAMPLES / "pasta_local", pasta)
        self.declare("GB_HOME", "PASTA_LOCAL_DIR", "HTTPS_PROXY")
        warnings = loader.enable("banco_http", confirm=False)["plugin"]["warnings"]
        joined = "\n".join(warnings)
        self.assertIn("GB_HOME, uma variável do core", joined)
        self.assertIn("PASTA_LOCAL_DIR, do espaço de nomes do plugin pasta_local", joined)
        self.assertIn("HTTPS_PROXY, fora do espaço de nomes BANCO_HTTP_", joined)
        self.assertNotIn("warnings", loader.enable("pasta_local", confirm=False)["plugin"])


if __name__ == "__main__":
    unittest.main()
