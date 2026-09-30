"""`plugins --action new` + `getbrolls.sdk.testing`: o plugin gerado passa no próprio teste e no check."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Any só aparece citado em cast("Any", ...); o pyright resolve a string, o pylint não.
from typing import Any, cast  # pylint: disable=unused-import

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT
from test_sdk_loader import LoaderTestCase

from getbrolls.sdk import CommandSpec, ProviderCapabilities, RouteResult, testing


class _Provider:
    name = "demo"
    capabilities = ProviderCapabilities(search=True)

    def search(self, query, limit, media):
        return []

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


class _Route:
    name = "demo"
    stage = "fetch"

    def prepare(self, item, workdir):
        return RouteResult(workdir / "v.mp4")


class ContractCheckTests(unittest.TestCase):
    def test_good_contributions_pass(self):
        testing.check_provider(_Provider())
        testing.check_route(_Route())
        testing.check_command(CommandSpec("ola", "Diz olá", lambda args, ctx: {}))

    def test_each_break_has_a_clear_message(self):
        class SemRefresh:
            name = "demo"
            capabilities = ProviderCapabilities(search=True)

            def search(self, query, limit, media):
                return []

            def resolve(self, url):
                return None

        class EstagioErrado(_Route):
            stage = "sempre"

        class PrepareCurto(_Route):
            # Assinatura curta de propósito: é o próprio contrato quebrado que o
            # teste espera que check_route detecte.
            def prepare(self, item):  # pylint: disable=arguments-differ
                return None

        cases = (
            (testing.check_provider, SemRefresh(), "refresh"),
            (testing.check_route, EstagioErrado(), "stage"),
            (testing.check_route, PrepareCurto(), "prepare(item, workdir)"),
            (testing.check_command, CommandSpec("ola", "Diz olá", cast("Any", lambda args: {})), "(args, ctx)"),
            (testing.check_command, CommandSpec("Ola Mundo", "x", lambda args, ctx: {}), "name"),
            (testing.check_command, object(), "api.command"),
        )
        for check, value, fragment in cases:
            with self.subTest(fragment=fragment), self.assertRaises(AssertionError) as caught:
                check(value)
            self.assertIn(fragment, str(caught.exception))


class ScaffoldTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.parent = Path(tempfile.mkdtemp(prefix="gb-new-"))
        self.addCleanup(shutil.rmtree, self.parent, ignore_errors=True)

    def env(self):
        return {"GB_HOME": str(self.home)}

    def run_generated_test(self, folder):
        env = {
            **os.environ,
            "GB_HOME": str(self.home),
            "PYTHONPATH": str(ROOT / "scripts"),
            "PYTHONDONTWRITEBYTECODE": "1",
            # A saída é lida como UTF-8: no Windows, sem isto o filho escreve em cp1252.
            "PYTHONIOENCODING": "utf-8",
        }
        return subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(folder / "tests")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )

    def test_every_kind_passes_its_own_test_and_the_check(self):
        for kind in ("provider", "route", "command"):
            plugin_id = f"meu_{kind}"
            with self.subTest(kind=kind):
                out = run_cli(
                    "plugins",
                    "--action",
                    "new",
                    "--id",
                    plugin_id,
                    "--kind",
                    kind,
                    "--path",
                    self.parent,
                    env=self.env(),
                )
                folder = Path(out["created"])
                self.assertEqual(self.parent.resolve() / plugin_id, folder)
                done = self.run_generated_test(folder)
                self.assertEqual(0, done.returncode, done.stderr)
                self.assertIn("OK", done.stderr)
                self.assertFalse(list(folder.rglob("__pycache__")))
                checked = run_cli("plugins", "--action", "check", "--path", folder, env=self.env())
                self.assertTrue(checked["ok"])
                expected = {"provider": "providers", "route": "routes", "command": "commands"}[kind]
                self.assertTrue(checked["contracts"][expected])

    def test_generated_plugin_installs_and_its_command_runs(self):
        out = run_cli(
            "plugins", "--action", "new", "--id", "meu_cmd", "--kind", "command", "--path", self.parent, env=self.env()
        )
        preview = run_cli("plugins", "--action", "install", "--source", out["created"], env=self.env())
        run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            out["created"],
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        result = run_cli("x", "meu_cmd", "resumo", env=self.env())
        self.assertEqual({"plugin": "meu_cmd", "candidatos": 0, "args": {}}, result["result"])

    def test_bad_input_and_existing_folder_are_refused(self):
        run_cli("plugins", "--action", "new", "--id", "Ruim", "--kind", "provider", expect=1, env=self.env())
        run_cli("plugins", "--action", "new", "--id", "core", "--kind", "provider", expect=1, env=self.env())
        run_cli("plugins", "--action", "new", "--id", "sem_tipo", expect=1, env=self.env())
        run_cli("plugins", "--action", "new", "--id", "dup", "--kind", "command", "--path", self.parent, env=self.env())
        err = run_cli(
            "plugins",
            "--action",
            "new",
            "--id",
            "dup",
            "--kind",
            "command",
            "--path",
            self.parent,
            expect=1,
            env=self.env(),
        )
        self.assertIn("já existe", err["error"])

    def test_check_reports_a_broken_contract_as_a_plugin_error(self):
        out = run_cli(
            "plugins", "--action", "new", "--id", "meu_route", "--kind", "route", "--path", self.parent, env=self.env()
        )
        plugin = Path(out["created"]) / "plugin.py"
        plugin.write_text(
            plugin.read_text(encoding="utf-8").replace("def prepare(self, item, workdir):", "def prepare(self, item):"),
            encoding="utf-8",
        )
        err = run_cli("plugins", "--action", "check", "--path", out["created"], expect=1, env=self.env())
        self.assertIn("Plugin meu_route: contrato", err["error"])
        self.assertIn("prepare(item, workdir)", err["error"])


if __name__ == "__main__":
    unittest.main()
