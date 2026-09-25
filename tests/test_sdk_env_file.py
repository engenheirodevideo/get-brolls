"""O `.env` aceita as chaves de `permissions.env` dos plugins instalados.

O README do `banco_http` e a orientação do `brief`/`status` mandam pôr o token no
`.env`; antes disso travava TODO comando com "variável desconhecida".
"""

import os
import shutil
import subprocess
import sys
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
            self.addCleanup(config.load_env, self.work / "nao-existe.env")
            # Chega ao plugin por api.env, nunca ao ambiente do processo.
            self.assertNotIn("BANCO_HTTP_TOKEN", os.environ)
            self.assertEqual("tk_teste_123", config.plugin_env_value("banco_http", "BANCO_HTTP_TOKEN"))
            self.assertEqual("tk_teste_123", api_for("banco_http", ["BANCO_HTTP_TOKEN"]).env("BANCO_HTTP_TOKEN"))
        out = run_cli("--env-file", str(path), "providers", env={"GB_HOME": str(self.home)})
        self.assertIn("banco_http", out)
        self.assertTrue(out["banco_http"]["configured"])

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
            self.addCleanup(config.load_env, self.work / "nao-existe.env")
            self.assertNotIn("OUTRO_TOKEN", os.environ)
            self.assertEqual("y", config.plugin_env_value("outro", "OUTRO_TOKEN"))

    def test_env_key_of_another_plugins_namespace_never_reads_as_configured(self):
        """Um plugin (`banco`) que declara como `env_key` a variável do espaço de nomes
        de outro (`BANCO_HTTP_TOKEN`, do `banco_http`) não aparece "configurado" com o
        valor que o `.env` guardou para o dono; o dono, sim."""
        import json

        squatter = self.home / "plugins" / "banco"
        squatter.mkdir(parents=True)
        (squatter / "getbrolls-plugin.json").write_text(
            json.dumps(
                {
                    "id": "banco",
                    "name": "Banco",
                    "version": "0.1.0",
                    "sdk_api": 1,
                    "requires_getbrolls": ">=2.5,<3",
                    "entry": "plugin.py",
                    "contributes": {"providers": ["banco"]},
                    "permissions": {"env": ["BANCO_HTTP_TOKEN"]},
                }
            ),
            encoding="utf-8",
        )
        (squatter / "plugin.py").write_text(
            "from getbrolls.sdk.contracts import ProviderCapabilities\n\n\n"
            "class Fonte:\n"
            '    name = "banco"\n'
            '    capabilities = ProviderCapabilities(search=True, env_key="BANCO_HTTP_TOKEN")\n\n'
            "    def search(self, query, limit, media):\n"
            "        return []\n\n"
            "    def resolve(self, url):\n"
            "        return None\n\n"
            "    def refresh(self, item):\n"
            "        return item\n\n\n"
            "def register(api):\n"
            "    api.provider(Fonte())\n",
            encoding="utf-8",
        )
        pin_plugins("banco_http", "banco")
        path = self.env_file("BANCO_HTTP_TOKEN=tk_teste_123\n")
        with patch.dict(os.environ, {}):
            os.environ.pop("BANCO_HTTP_TOKEN", None)
            config.load_env(path)
            self.addCleanup(config.load_env, self.work / "nao-existe.env")
            self.assertTrue(config.env_is_set("BANCO_HTTP_TOKEN", "banco_http"))
            self.assertFalse(config.env_is_set("BANCO_HTTP_TOKEN", "banco"))
            self.assertFalse(config.env_is_set("BANCO_HTTP_TOKEN"))
        out = run_cli("--env-file", str(path), "providers", env={"GB_HOME": str(self.home)})
        self.assertTrue(out["banco_http"]["configured"])
        self.assertEqual("banco", out["banco"]["plugin"])
        self.assertFalse(out["banco"]["configured"])

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
    "xdg": ["XDG_CONFIG_HOME"],
    "openssl": ["OPENSSL_CONF"],
}


def api_for(plugin_id, env):
    from getbrolls.sdk.api import PluginApi
    from getbrolls.sdk.registry import Registry

    return PluginApi({**MANIFEST, "id": plugin_id, "permissions": {"network": [], "env": env, "paths": []}}, Registry())


def child_sees(key):
    done = subprocess.run(
        [sys.executable, "-c", f"import os; print(os.environ.get({key!r}))"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return done.stdout.strip()


class PluginEnvNeverExportedTests(LoaderTestCase):
    """Variável do .env de um plugin fica num mapa do core: só `api.env` do plugin dono a
    lê. Nunca vai para o ambiente do processo nem para um subprocesso (git, ffmpeg,
    OpenSSL), então nem `XDG_CONFIG_HOME` nem `OPENSSL_CONF` mudam ferramenta nenhuma."""

    def setUp(self):
        super().setUp()
        self.work = Path(tempfile.mkdtemp(prefix="gb-env-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.addCleanup(config.load_env, self.work / "nao-existe.env")

    def plugin(self, plugin_id, env):
        manifest = {**MANIFEST, "id": plugin_id, "contributes": {}, "permissions": {"network": [], "env": env}}
        self.install(manifest, code="def register(api):\n    pass\n")

    def test_plugin_keys_stay_out_of_the_environment_and_children(self):
        for plugin_id, keys in TOOLCHAIN_CASES.items():
            self.plugin(plugin_id, keys)
        self.plugin("banco_http", ["BANCO_HTTP_TOKEN"])
        keys = [key for group in TOOLCHAIN_CASES.values() for key in group] + ["BANCO_HTTP_TOKEN"]
        path = self.work / ".env"
        path.write_text("".join(f"{key}=/valor/do/plugin\n" for key in keys), encoding="utf-8")
        cleared = dict.fromkeys(keys, "")
        with patch.dict(os.environ, cleared):
            for key in keys:
                os.environ.pop(key)
            config.load_env(path)
            for key in keys:
                with self.subTest(key=key):
                    self.assertNotIn(key, os.environ)
                    self.assertEqual("None", child_sees(key))
            for plugin_id, group in TOOLCHAIN_CASES.items():
                for key in group:
                    self.assertEqual("/valor/do/plugin", api_for(plugin_id, group).env(key))
                    # Outro plugin, mesmo pedindo a variável, não recebe o valor do .env.
                    self.assertIsNone(api_for("banco_http", ["BANCO_HTTP_TOKEN", key]).env(key))

    def test_real_environment_is_the_persons_choice(self):
        self.plugin("xdg", ["XDG_TOKEN"])
        with patch.dict(os.environ, {"XDG_TOKEN": "do-shell"}):
            config.load_env(self.work / "nao-existe.env")
            self.assertEqual("do-shell", api_for("xdg", ["XDG_TOKEN"]).env("XDG_TOKEN"))

    def test_shell_value_wins_over_env_file_like_core_keys(self):
        self.plugin("banco_http", ["BANCO_HTTP_TOKEN"])
        path = self.work / ".env"
        path.write_text("BANCO_HTTP_TOKEN=do-env\n", encoding="utf-8")
        api = api_for("banco_http", ["BANCO_HTTP_TOKEN"])
        with patch.dict(os.environ, {"BANCO_HTTP_TOKEN": "do-shell"}):
            config.load_env(path)
            self.assertEqual("do-shell", api.env("BANCO_HTTP_TOKEN"))
        with patch.dict(os.environ, {"BANCO_HTTP_TOKEN": ""}):
            # Vazio no shell conta como não definido: vale o do .env.
            self.assertEqual("do-env", api.env("BANCO_HTTP_TOKEN"))
        with patch.dict(os.environ, {}):
            os.environ.pop("BANCO_HTTP_TOKEN", None)
            self.assertEqual("do-env", api.env("BANCO_HTTP_TOKEN"))

    def test_toolchain_names_warn_in_the_preview_and_on_load(self):
        from getbrolls.sdk import loader

        self.plugin("xdg", ["XDG_CONFIG_HOME"])
        self.plugin("openssl", ["OPENSSL_CONF"])
        self.plugin("banco_http", ["BANCO_HTTP_TOKEN"])
        for plugin_id, key in (("xdg", "XDG_CONFIG_HOME"), ("openssl", "OPENSSL_CONF")):
            warnings = "\n".join(loader.enable(plugin_id, confirm=False)["plugin"]["warnings"])
            self.assertIn(f"{key}, uma variável que ferramentas do sistema leem", warnings)
        self.assertNotIn("warnings", loader.enable("banco_http", confirm=False)["plugin"])
        path = self.work / ".env"
        path.write_text("XDG_CONFIG_HOME=/x\n", encoding="utf-8")
        out = run_cli("--env-file", str(path), "providers", env={"GB_HOME": str(self.home)})
        self.assertIn("PLUGIN_ENV_TOOLCHAIN", [w["code"] for w in out.get("warnings", [])])


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
