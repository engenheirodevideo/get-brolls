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
from test_sdk_loader import MANIFEST, LoaderTestCase

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
        self.assertIn("HTTPS_PROXY, uma variável que ferramentas do sistema leem", joined)
        # Do lado do pasta_local, o aviso é o de invasão do próprio espaço de nomes.
        self.assertEqual(
            ["O plugin instalado banco_http pede PASTA_LOCAL_DIR"],
            [w.split(",")[0] for w in loader.enable("pasta_local", confirm=False)["plugin"]["warnings"]],
        )


TOOLCHAIN_CASES = {
    "git_ssh": ["GIT_SSH_COMMAND"],
    "dyld_insert": ["DYLD_INSERT_LIBRARIES"],
    "ssl_cert": ["SSL_CERT_FILE", "SSL_CERT_DIR"],
    "ld_library": ["LD_LIBRARY_PATH"],
    "node_extra": ["NODE_EXTRA_CA_CERTS"],
    "bash_env": ["BASH_ENV"],
    "curl_ca": ["CURL_CA_BUNDLE"],
    "requests_ca": ["REQUESTS_CA_BUNDLE"],
}


class ToolchainKeysTests(LoaderTestCase):
    """Variável que uma ferramenta do sistema lê nunca sai do .env para um plugin — nem
    quando o id do plugin faz dela parte do próprio espaço de nomes."""

    def setUp(self):
        super().setUp()
        self.work = Path(tempfile.mkdtemp(prefix="gb-env-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def plugin(self, plugin_id, env):
        manifest = {**MANIFEST, "id": plugin_id, "contributes": {}, "permissions": {"network": [], "env": env}}
        self.install(manifest, code="def register(api):\n    pass\n")

    def test_underscored_ids_never_get_toolchain_keys(self):
        for plugin_id, keys in TOOLCHAIN_CASES.items():
            self.plugin(plugin_id, keys)
        self.plugin("banco_http", ["BANCO_HTTP_TOKEN"])
        accepted = config.plugin_env_keys()
        # O plugin comum continua recebendo a sua; nenhuma variável de ferramenta passa.
        self.assertEqual(frozenset({"BANCO_HTTP_TOKEN"}), accepted)
        for plugin_id, keys in TOOLCHAIN_CASES.items():
            for key in keys:
                with self.subTest(key=key):
                    self.assertNotIn(key, accepted)
                    path = self.work / ".env"
                    path.write_text(f"{key}=x\n", encoding="utf-8")
                    with patch.dict(os.environ, {}), self.assertRaises(ValueError) as caught:
                        config.load_env(path)
                    self.assertIn(plugin_id, str(caught.exception))

    def test_preview_warns_about_toolchain_keys(self):
        from getbrolls.sdk import loader

        self.plugin("git_ssh", ["GIT_SSH_COMMAND"])
        self.plugin("banco_http", ["BANCO_HTTP_TOKEN"])
        warnings = "\n".join(loader.enable("git_ssh", confirm=False)["plugin"]["warnings"])
        self.assertIn("GIT_SSH_COMMAND, uma variável que ferramentas do sistema leem", warnings)
        self.assertNotIn("warnings", loader.enable("banco_http", confirm=False)["plugin"])


class NamespaceSquattingTests(LoaderTestCase):
    """Outro plugin instalado que pede uma variável do espaço de nomes deste gera aviso."""

    def plugin(self, plugin_id, env):
        manifest = {**MANIFEST, "id": plugin_id, "contributes": {}, "permissions": {"network": [], "env": env}}
        self.install(manifest, code="def register(api):\n    pass\n")

    def test_squatter_installed_first(self):
        from getbrolls.sdk import loader

        self.plugin("banco", ["BANCO_HTTP_TOKEN"])
        self.plugin("banco_http", ["BANCO_HTTP_TOKEN"])
        warnings = "\n".join(loader.enable("banco_http", confirm=False)["plugin"]["warnings"])
        self.assertIn("O plugin instalado banco pede BANCO_HTTP_TOKEN", warnings)

    def test_squatter_arriving_after(self):
        from getbrolls.sdk import loader

        self.plugin("banco_http", ["BANCO_HTTP_TOKEN"])
        loader.enable("banco_http", confirm=True)
        self.plugin("banco", ["BANCO_HTTP_TOKEN"])
        warnings = "\n".join(loader.enable("banco", confirm=False)["plugin"]["warnings"])
        self.assertIn("BANCO_HTTP_TOKEN, do espaço de nomes do plugin banco_http", warnings)
        again = "\n".join(loader.enable("banco_http", confirm=False)["plugin"]["warnings"])
        self.assertIn("O plugin instalado banco pede BANCO_HTTP_TOKEN", again)


if __name__ == "__main__":
    unittest.main()
