"""`gb x`: comandos de plugin com namespace, só leitura do projeto e falha isolada."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.sdk import plugin_commands

COMMAND_MANIFEST = {**MANIFEST, "contributes": {"commands": ["contar", "quebra", "lista", "tenta"]}}

COMMAND_CODE = """
def contar(args, ctx):
    items = ctx.candidates()
    for item in items:
        item["approval"] = {"status": "approved"}
    return {"plugin": ctx.plugin_id, "candidatos": len(items), "args": args, "brief": ctx.brief()}


def quebra(args, ctx):
    raise RuntimeError("deu ruim no plugin")


def lista(args, ctx):
    return [1, 2]


def tenta(args, ctx):
    return {"ok": ctx.candidates()[0]["approval"]["status"]}


def register(api):
    api.command("contar", contar, "Conta os candidatos do projeto")
    api.command("quebra", quebra, "Sempre falha")
    api.command("lista", lista, "Devolve lista, não objeto")
    api.command("tenta", tenta, "Relê o projeto depois de outro comando")
"""


class PluginCommandCliTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.install(COMMAND_MANIFEST, code=COMMAND_CODE)
        self.project = Path(tempfile.mkdtemp(prefix="gb-project-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def env(self):
        return {"GB_HOME": str(self.home), "GB_PLUGINS": "demo"}

    def test_list_reads_the_manifest_without_running_code(self):
        broken = COMMAND_CODE + "\nraise SystemExit('não devia rodar')\n"
        self.install(COMMAND_MANIFEST, code=broken)
        out = run_cli("x", "--list", env=self.env())
        self.assertEqual(
            ["x demo contar", "x demo quebra", "x demo lista", "x demo tenta"], [row["run"] for row in out["commands"]]
        )

    def test_command_returns_json_and_reads_the_project_by_copy(self):
        run_cli("resolve", "--url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", project=self.project, env=self.env())
        out = run_cli("x", "demo", "contar", "--arg", "nome=Bruno", project=self.project, env=self.env())
        self.assertEqual("demo", out["plugin"])
        self.assertEqual({"plugin": "demo", "candidatos": 1, "args": {"nome": "Bruno"}, "brief": None}, out["result"])
        again = run_cli("x", "demo", "tenta", project=self.project, env=self.env())
        self.assertEqual({"ok": "pending"}, again["result"])

    def test_command_without_project_sees_nothing(self):
        out = run_cli("x", "demo", "contar", env=self.env())
        self.assertEqual(0, out["result"]["candidatos"])

    def test_read_only_command_does_not_create_the_project_tree(self):
        run_cli("x", "demo", "contar", project=self.project, env=self.env())
        self.assertFalse((self.project / "brolls").exists())

    def test_failing_command_is_exit_2_with_the_plugin_id(self):
        err = run_cli("x", "demo", "quebra", expect=2, env=self.env())
        self.assertIn("Plugin demo", err["error"])
        self.assertIn("RuntimeError", err["error"])
        self.assertNotEqual("INTERNAL_ERROR", err.get("error_code"))

    def test_non_object_result_is_refused(self):
        err = run_cli("x", "demo", "lista", expect=2, env=self.env())
        self.assertIn("objeto JSON", err["error"])

    def test_unknown_command_and_disabled_plugin_are_named(self):
        err = run_cli("x", "demo", "nao_existe", expect=2, env=self.env())
        self.assertIn("x --list", err["error"])
        err = run_cli("x", "demo", "contar", expect=2, env={"GB_HOME": str(self.home)})
        self.assertIn("disabled", err["error"])

    def test_bad_args_are_refused(self):
        for bad in (("--arg", "sem_igual"), ("--arg", "Chave=1"), ("--arg", "a=1", "--arg", "a=2")):
            with self.subTest(bad=bad):
                run_cli("x", "demo", "contar", *bad, expect=2, env=self.env())
        run_cli("x", expect=2, env=self.env())


class PluginCommandLoggingTests(LoaderTestCase):
    def args(self, name, *pairs):
        return SimpleNamespace(list=False, plugin_id="demo", plugin_command=name, project=None, arg=list(pairs))

    def test_success_and_failure_are_logged_without_values(self):
        self.install(COMMAND_MANIFEST, code=COMMAND_CODE)
        with (
            patch.dict(os.environ, {"GB_PLUGINS": "demo"}),
            self.assertLogs("getbrolls.sdk", level="INFO") as cm,
        ):
            plugin_commands.run(self.args("contar", "token=segredo"))
            with self.assertRaises(ValueError):
                plugin_commands.run(self.args("quebra"))
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_command", joined)
        self.assertIn("command=contar", joined)
        self.assertIn("args=1", joined)
        self.assertIn("event=plugin_call_failed", joined)
        self.assertNotIn("segredo", joined)
        self.assertNotIn("deu ruim", joined)

    def test_parse_pairs(self):
        self.assertEqual({"a": "1", "b": "x=y"}, plugin_commands.parse_pairs(["a=1", "b=x=y"]))
        self.assertEqual({}, plugin_commands.parse_pairs(None))


if __name__ == "__main__":
    unittest.main()
