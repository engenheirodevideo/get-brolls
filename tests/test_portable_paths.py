"""Caminhos da pasta pessoal na saída: `~` ou `$GB_HOME/…` onde o CLI_CONTRACT promete.

`client add|list|show` e `plugins list|install` não mostram o caminho absoluto da pasta
pessoal; `path` de componente do `roteiro check` é dado para máquina e fica absoluto
(documentado em docs/CLI_CONTRACT.md).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import CLI_ARGV
from test_sdk_loader import MANIFEST, PLUGIN_CODE

from getbrolls import capabilities, cli, commands
from getbrolls.sdk import loader


class PortablePathTests(unittest.TestCase):
    def setUp(self):
        self.user = Path(tempfile.mkdtemp(prefix="gb-user-")).resolve()
        self.addCleanup(shutil.rmtree, self.user, ignore_errors=True)
        self.home = self.user / ".getbrolls"
        self.env = {"HOME": str(self.user), "USERPROFILE": str(self.user), "GB_HOME": str(self.home)}

    def cli(self, *args, expect=0):
        return run_cli(*args, expect=expect, env=self.env)

    def assert_no_home(self, payload):
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(str(self.user), text)

    def test_client_add_list_show_hide_the_home(self):
        (self.user / "clientes").mkdir()
        added = self.cli("client", "--action", "add", "--slug", "acme", "--root", self.user / "clientes")
        # O `--root` digitado volta como digitado (mais o cliente); o resto da saída sem a pasta pessoal.
        self.assertEqual(str(self.user / "clientes" / "acme"), added["client"].pop("root"))
        self.assert_no_home(added)
        # `list` e `show` devolvem a pasta como registrada no `add` (dado da pessoa); o resto, sem a pasta pessoal.
        listed = self.cli("client", "--action", "list")
        self.assertEqual(str(self.user / "clientes" / "acme"), listed["clients"][0].pop("root"))
        self.assert_no_home(listed)
        shown = self.cli("client", "--action", "show", "--slug", "acme")
        self.assertEqual(str(self.user / "clientes" / "acme"), shown["client"].pop("root"))
        self.assert_no_home(shown)

    def test_plugins_install_and_list_hide_the_home(self):
        source = self.user / "src" / "demo"
        source.mkdir(parents=True)
        (source / "getbrolls-plugin.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (source / "plugin.py").write_text(PLUGIN_CODE, encoding="utf-8")
        preview = self.cli("plugins", "--action", "install", "--source", source)
        self.assertEqual("~/src/demo", preview["plugin"]["source"])
        self.assert_no_home(preview["plugin"])
        sha = preview["plugin"]["sha256"]
        done = self.cli("plugins", "--action", "install", "--source", source, "--yes", "--expect", sha)
        self.assert_no_home(done["plugin"])
        listed = self.cli("plugins", "--action", "list")
        self.assertEqual("$GB_HOME/plugins", listed["plugins_dir"])
        self.assert_no_home(listed["plugins"])
        # O plugins.json guarda o caminho de verdade: o update relê a origem dele.
        state = json.loads((self.home / "plugins.json").read_text(encoding="utf-8"))
        self.assertEqual(str(source), state["sources"]["demo"]["source"])


class TildeProjectTests(unittest.TestCase):
    """`--project '~/…'` com o `~` cru (entre aspas, `--project=~/…`, PowerShell) vale a pasta pessoal."""

    def test_project_with_tilde_is_expanded_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            user, cwd = base / "home", base / "cwd"
            cwd.mkdir()
            env = {**os.environ, "HOME": str(user), "USERPROFILE": str(user)}
            done = subprocess.run(
                [*CLI_ARGV, "init-rules", "--project", "~/v/p"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=cwd,
                check=False,
            )
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual(str(user / "v" / "p" / "RULES.md"), json.loads(done.stdout)["rules"])
            self.assertTrue((user / "v" / "p" / "brolls").is_dir())
            self.assertFalse((cwd / "~").exists())
            status = subprocess.run(
                [*CLI_ARGV, "status", "--project=~/v/p"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=cwd,
                check=False,
            )
            self.assertEqual(0, status.returncode, status.stderr)
            self.assertFalse((cwd / "~").exists())

    def test_fallback_diagnostics_expand_the_project_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp).resolve()
            with unittest.mock.patch.dict(os.environ, {"HOME": str(user), "USERPROFILE": str(user)}):
                for argv in (["gb", "status", "--project", "~/p"], ["gb", "status", "--project=~/p"]):
                    with self.subTest(argv=argv), unittest.mock.patch("sys.argv", argv):
                        self.assertEqual(str(user / "p"), cli._project_from_argv())  # pylint: disable=protected-access


class PluginsErrorTests(unittest.TestCase):
    """`plugins_error` do `capabilities` e do `doctor` sai sem o caminho da instalação."""

    def broken(self):
        home = loader.home_dir()
        return unittest.mock.patch.object(
            loader, "inventory", side_effect=ValueError(f"{home / 'plugins.json'} quebrado")
        ), unittest.mock.patch.object(loader, "entries", side_effect=ValueError(f"{home / 'plugins.json'} quebrado"))

    def test_capabilities_plugins_error_is_portable(self):
        inventory, _entries = self.broken()
        with inventory:
            manifest = capabilities.describe(cli.build_parser())
        self.assertEqual("$GB_HOME/plugins.json quebrado", manifest["plugins_error"])

    def test_doctor_plugins_error_is_portable(self):
        _inventory, entries = self.broken()
        result = {}
        with entries:
            commands._doctor_plugin_inventory(result, {"missing": [], "optional": []})  # pylint: disable=protected-access
        self.assertEqual("$GB_HOME/plugins.json quebrado", result["plugins_error"])


if __name__ == "__main__":
    unittest.main()
