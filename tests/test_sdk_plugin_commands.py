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


class ArgParsingNeverEchoesTheValueTests(unittest.TestCase):
    """Fix round 1, item 2: `--arg` inválido nunca ecoa o par bruto nem o valor —
    um `--arg` digitado errado (sem `--arg`, ou como `=valor`) pode carregar um
    segredo colado onde a chave deveria estar."""

    def test_missing_equals_sign_does_not_echo_the_pair(self):
        with self.assertRaises(ValueError) as ctx:
            plugin_commands.parse_pairs(["s3cr3t"])
        message = str(ctx.exception)
        self.assertNotIn("s3cr3t", message)
        self.assertIn("faltou", message)

    def test_empty_key_does_not_echo_the_value(self):
        with self.assertRaises(ValueError) as ctx:
            plugin_commands.parse_pairs(["=s3cr3t"])
        message = str(ctx.exception)
        self.assertNotIn("s3cr3t", message)
        self.assertIn("chave vazia", message)

    def test_invalid_key_charset_does_not_echo_the_value(self):
        with self.assertRaises(ValueError) as ctx:
            plugin_commands.parse_pairs(["Chave=s3cr3t"])
        self.assertNotIn("s3cr3t", str(ctx.exception))


class PluginNameValidationTests(unittest.TestCase):
    """Fix round 1, item 3: id/nome fora do charset `^[a-z][a-z0-9_]{1,31}$` são
    recusados ANTES de `get_registry()` — que monta o registro de plugins
    habilitados e roda `register()` de verdade na primeira consulta do processo."""

    def args(self, plugin_id, command):
        return SimpleNamespace(list=False, plugin_id=plugin_id, plugin_command=command, project=None, arg=None)

    def test_invalid_plugin_id_never_touches_the_registry(self):
        with patch("getbrolls.sdk.plugin_commands.get_registry") as mocked, self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("../../etc", "foo"))
        mocked.assert_not_called()
        message = str(ctx.exception)
        self.assertIn("id inválido", message)
        self.assertNotIn("../../etc", message)

    def test_invalid_command_name_never_touches_the_registry(self):
        with patch("getbrolls.sdk.plugin_commands.get_registry") as mocked, self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("demo", "../../../etc/passwd"))
        mocked.assert_not_called()
        message = str(ctx.exception)
        self.assertIn("Nome de comando inválido", message)
        self.assertNotIn("passwd", message)


ISOLATION_MANIFEST = {**MANIFEST, "id": "isola", "contributes": {"commands": ["hostil", "estoura", "gerador", "nan"]}}

ISOLATION_CODE = """
class Hostil(Exception):
    def __str__(self):
        raise SystemExit(0)


def hostil(args, ctx):
    raise Hostil("segredo-que-nao-deveria-aparecer")


def estoura(args, ctx):
    raise SystemExit("saiu-do-plugin")


def gerador(args, ctx):
    return (x for x in [1, 2])


def nan(args, ctx):
    return {"valor": float("nan")}


def register(api):
    api.command("hostil", hostil, "Levanta exceção com __str__ hostil")
    api.command("estoura", estoura, "Sai via SystemExit")
    api.command("gerador", gerador, "Devolve gerador, não serializável")
    api.command("nan", nan, "Devolve NaN, não permitido em JSON")
"""


class PluginFailureIsolationTests(LoaderTestCase):
    """Fix round 1, items 1/4/5: `__str__` hostil, SystemExit e retorno não
    serializável (gerador, NaN) nunca escapam cru — isolados num plugin próprio
    (`isola`) para não mexer nas asserções já existentes de `COMMAND_MANIFEST`."""

    def setUp(self):
        super().setUp()
        self.install(ISOLATION_MANIFEST, code=ISOLATION_CODE)

    def args(self, name):
        return SimpleNamespace(list=False, plugin_id="isola", plugin_command=name, project=None, arg=None)

    def test_hostile_str_never_runs_and_message_carries_only_the_type(self):
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("hostil"))
        message = str(ctx.exception)
        self.assertIn("Plugin isola", message)
        self.assertIn("Hostil", message)
        self.assertNotIn("segredo-que-nao-deveria-aparecer", message)

    def test_system_exit_from_handler_is_isolated_and_logged(self):
        with (
            patch.dict(os.environ, {"GB_PLUGINS": "isola"}),
            self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
            self.assertRaises(ValueError) as ctx,
        ):
            plugin_commands.run(self.args("estoura"))
        self.assertIn("SystemExit", str(ctx.exception))
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_call_failed", joined)
        self.assertIn("error=SystemExit", joined)
        self.assertNotIn("saiu-do-plugin", joined)

    def test_generator_result_is_refused_as_not_json(self):
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("gerador"))
        self.assertIn("objeto JSON", str(ctx.exception))

    def test_nan_result_is_refused_as_not_json(self):
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("nan"))
        self.assertIn("objeto JSON", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
