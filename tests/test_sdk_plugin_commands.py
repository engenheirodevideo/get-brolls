"""`gb x`: comandos de plugin com namespace, só leitura do projeto e falha isolada."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _offline import OFFLINE_YTDLP
from _plugin_pins import pin_plugins
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
        pin_plugins("demo")
        return {"GB_HOME": str(self.home), "GB_PLUGINS": "demo"}

    def test_list_reads_the_manifest_without_running_code(self):
        broken = COMMAND_CODE + "\nraise SystemExit('não devia rodar')\n"
        self.install(COMMAND_MANIFEST, code=broken)
        out = run_cli("x", "--list", env=self.env())
        self.assertEqual(
            ["x demo contar", "x demo quebra", "x demo lista", "x demo tenta"], [row["run"] for row in out["commands"]]
        )

    def test_command_returns_json_and_reads_the_project_by_copy(self):
        run_cli(
            "resolve",
            "--url",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            project=self.project,
            env={**self.env(), **OFFLINE_YTDLP},
        )
        out = run_cli("x", "demo", "contar", "--arg", "nome=Pessoa Teste", project=self.project, env=self.env())
        self.assertEqual("demo", out["plugin"])
        self.assertEqual(
            {"plugin": "demo", "candidatos": 1, "args": {"nome": "Pessoa Teste"}, "brief": None}, out["result"]
        )
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
        run_cli("plugins", "--action", "disable", "--id", "demo", env={"GB_HOME": str(self.home)})
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
        pin_plugins("demo")
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
    """`--arg` inválido nunca ecoa o par bruto nem o valor —
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
    """Id/nome fora do charset `^[a-z][a-z0-9_]{1,31}$` são
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


ISOLATION_MANIFEST = {
    **MANIFEST,
    "id": "isola",
    "contributes": {
        "commands": [
            "hostil",
            "estoura",
            "gerador",
            "nan",
            "dict_exit",
            "dict_runtime",
            "meta_boom",
            "meta_leak",
            "escapa_base",
            "grande",
            "weird_name_exit",
            "weird_name_leak",
            "genexit",
            "fundo",
        ]
    },
}

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


class DictExitDuringDump(dict):
    def items(self):
        raise SystemExit(0)


def dict_exit(args, ctx):
    return DictExitDuringDump({"a": 1})


class DictRuntimeDuringDump(dict):
    def items(self):
        raise RuntimeError("SEGREDO2")


def dict_runtime(args, ctx):
    return DictRuntimeDuringDump({"a": 1})


class _MetaBoom(type):
    @property
    def __name__(cls):
        raise SystemExit(0)


class ExcMetaBoom(Exception, metaclass=_MetaBoom):
    pass


def meta_boom(args, ctx):
    raise ExcMetaBoom("nao-deveria-aparecer-meta-boom")


class _MetaLeak(type):
    @property
    def __name__(cls):
        return "SEGREDO3"


class ExcMetaLeak(Exception, metaclass=_MetaLeak):
    pass


def meta_leak(args, ctx):
    raise ExcMetaLeak("nao-deveria-aparecer-meta-leak")


class Fugitiva(BaseException):
    pass


def escapa_base(args, ctx):
    raise Fugitiva("nao-deveria-aparecer-escapa-base")


def grande(args, ctx):
    return {"itens": ["x" * 1000 for _ in range(2000)]}


class _WeirdNameStr(str):
    def __format__(self, spec):
        raise SystemExit(0)


ExcWeirdNameExit = type(_WeirdNameStr("BoomExit"), (Exception,), {})


def weird_name_exit(args, ctx):
    raise ExcWeirdNameExit("nao-deveria-aparecer-weird-name-exit")


class _WeirdNameLeakStr(str):
    def __format__(self, spec):
        return "SEGREDO7"


ExcWeirdNameLeak = type(_WeirdNameLeakStr("BoomLeak"), (Exception,), {})


def weird_name_leak(args, ctx):
    raise ExcWeirdNameLeak("nao-deveria-aparecer-weird-name-leak")


def genexit(args, ctx):
    raise GeneratorExit("nao-deveria-propagar-cru")


def fundo(args, ctx):
    value = "fim"
    for _ in range(200):
        value = [value]
    return {"aninhado": value}


def register(api):
    api.command("hostil", hostil, "Levanta exceção com __str__ hostil")
    api.command("estoura", estoura, "Sai via SystemExit")
    api.command("gerador", gerador, "Devolve gerador, não serializável")
    api.command("nan", nan, "Devolve NaN, não permitido em JSON")
    api.command("dict_exit", dict_exit, "Devolve dict cujo items() sai via SystemExit")
    api.command("dict_runtime", dict_runtime, "Devolve dict cujo items() levanta RuntimeError")
    api.command("meta_boom", meta_boom, "Levanta exceção cuja metaclasse __name__ sai via SystemExit")
    api.command("meta_leak", meta_leak, "Levanta exceção cuja metaclasse __name__ devolve texto arbitrário")
    api.command("escapa_base", escapa_base, "Levanta BaseException direto (não Exception, não SystemExit)")
    api.command("grande", grande, "Devolve um resultado maior que o teto de JSON")
    api.command(
        "weird_name_exit", weird_name_exit, "Nome de classe é subclasse de str cujo __format__ sai via SystemExit"
    )
    api.command(
        "weird_name_leak",
        weird_name_leak,
        "Nome de classe é subclasse de str cujo __format__ devolve texto arbitrário",
    )
    api.command("genexit", genexit, "Levanta GeneratorExit direto")
    api.command("fundo", fundo, "Devolve resultado com aninhamento além do teto de profundidade")
"""


class PluginFailureIsolationTests(LoaderTestCase):
    """`__str__` hostil, SystemExit e retorno não
    serializável (gerador, NaN) nunca escapam cru — isolados num plugin próprio
    (`isola`) para não mexer nas asserções já existentes de `COMMAND_MANIFEST`."""

    def setUp(self):
        super().setUp()
        self.install(ISOLATION_MANIFEST, code=ISOLATION_CODE)

    def args(self, name):
        return SimpleNamespace(list=False, plugin_id="isola", plugin_command=name, project=None, arg=None)

    def test_hostile_str_never_runs_and_message_carries_only_the_type(self):
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("hostil"))
        message = str(ctx.exception)
        self.assertIn("Plugin isola", message)
        self.assertIn("Hostil", message)
        self.assertNotIn("segredo-que-nao-deveria-aparecer", message)

    def test_system_exit_from_handler_is_isolated_and_logged(self):
        pin_plugins("isola")
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
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("gerador"))
        self.assertIn("objeto JSON", str(ctx.exception))

    def test_nan_result_is_refused_as_not_json(self):
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("nan"))
        self.assertIn("objeto JSON", str(ctx.exception))

    def test_dict_result_raising_system_exit_during_serialization_is_isolated(self):
        # O round-trip de JSON (não só a chamada do handler)
        # também precisa isolar BaseException de código de terceiro — aqui, o
        # `.items()` de um dict de terceiro rodando durante `json.dumps`.
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("dict_exit"))
        self.assertIn("objeto JSON", str(ctx.exception))

    def test_dict_result_raising_runtime_error_during_serialization_hides_the_message(self):
        # A mensagem nunca ecoa o texto da exceção de terceiro.
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("dict_runtime"))
        message = str(ctx.exception)
        self.assertIn("objeto JSON", message)
        self.assertNotIn("SEGREDO2", message)

    def test_metaclass_name_raising_system_exit_never_runs_the_hostile_property(self):
        # `type(exc).__name__` pode rodar código do plugin
        # (metaclasse com `__name__` como property); a leitura segura lê o
        # descritor cru de `type` — nunca invoca a property hostil, então o
        # `SystemExit` dela nunca dispara, e o nome verdadeiro (não a mensagem
        # do plugin) ainda é seguro de mostrar.
        pin_plugins("isola")
        with (
            patch.dict(os.environ, {"GB_PLUGINS": "isola"}),
            self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
            self.assertRaises(ValueError) as ctx,
        ):
            plugin_commands.run(self.args("meta_boom"))
        message = str(ctx.exception)
        self.assertIn("Plugin isola", message)
        self.assertIn("ExcMetaBoom", message)
        self.assertNotIn("nao-deveria-aparecer", message)
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_call_failed", joined)
        self.assertIn("error=ExcMetaBoom", joined)
        self.assertNotIn("nao-deveria-aparecer", joined)

    def test_metaclass_name_returning_arbitrary_text_is_not_trusted(self):
        # A leitura segura nunca invoca a property da
        # metaclasse — o texto arbitrário que ela devolveria ("SEGREDO3") nunca
        # chega à mensagem; o nome verdadeiro do tipo aparece em vez dele.
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("meta_leak"))
        message = str(ctx.exception)
        self.assertNotIn("SEGREDO3", message)
        self.assertIn("ExcMetaLeak", message)

    def test_base_exception_subclass_never_escapes_raw(self):
        # Uma classe que herda BaseException direto (não
        # Exception, não SystemExit) não pode escapar como traceback cru.
        pin_plugins("isola")
        with (
            patch.dict(os.environ, {"GB_PLUGINS": "isola"}),
            self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
            self.assertRaises(ValueError) as ctx,
        ):
            plugin_commands.run(self.args("escapa_base"))
        message = str(ctx.exception)
        self.assertIn("Plugin isola", message)
        self.assertIn("Fugitiva", message)
        self.assertNotIn("nao-deveria-aparecer", message)
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_call_failed", joined)
        self.assertIn("error=Fugitiva", joined)

    def test_oversized_result_is_refused(self):
        # Um resultado maior que o
        # teto de JSON é recusado, não escrito por inteiro em stdout/diagnostics
        # — e a mensagem é a exata (a conta antiga `1_000_000 // (1024*1024)`
        # dava "> 0 MB"; o teto agora é um múltiplo exato de 1024*1024).
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("grande"))
        self.assertEqual(
            "Plugin isola: o comando grande devolveu um resultado grande demais (> 1 MB de JSON).",
            str(ctx.exception),
        )

    def test_deeply_nested_result_is_refused_by_depth_not_size(self):
        # Aninhamento estreito e profundo (200 níveis)
        # cabe fácil sob o teto de tamanho compacto, mas explodiria ao ser
        # indentado (o que a CLI de fato escreve) — o teto de profundidade
        # recusa isso rápido, sem nunca montar a saída grande.
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("fundo"))
        self.assertEqual(
            "Plugin isola: o comando fundo devolveu um resultado com aninhamento profundo demais (> 64 níveis).",
            str(ctx.exception),
        )

    def test_class_name_that_is_a_str_subclass_raising_system_exit_falls_back(self):
        # `isinstance(name, str)` aceita uma SUBCLASSE de
        # str — CPython guarda essa instância como o nome real da classe. Uma
        # subclasse hostil sobrescrevendo __format__ não pode disparar (exit 0
        # silencioso) nem ser repetida: `type(name) is not str` cai no fallback
        # ANTES de qualquer formatação tocar no objeto hostil.
        pin_plugins("isola")
        with (
            patch.dict(os.environ, {"GB_PLUGINS": "isola"}),
            self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
            self.assertRaises(ValueError) as ctx,
        ):
            plugin_commands.run(self.args("weird_name_exit"))
        message = str(ctx.exception)
        self.assertIn("Plugin isola", message)
        self.assertIn("Exception", message)
        self.assertNotIn("nao-deveria-aparecer", message)
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_call_failed", joined)
        self.assertIn("error=Exception", joined)
        self.assertNotIn("nao-deveria-aparecer", joined)

    def test_class_name_that_is_a_str_subclass_returning_arbitrary_text_is_not_trusted(self):
        # Mesma proteção quando o __format__ hostil não
        # levanta, só devolve texto diferente ("SEGREDO7") — também nunca chega
        # à mensagem/log; o fallback seguro aparece em vez dele.
        pin_plugins("isola")
        with patch.dict(os.environ, {"GB_PLUGINS": "isola"}), self.assertRaises(ValueError) as ctx:
            plugin_commands.run(self.args("weird_name_leak"))
        message = str(ctx.exception)
        self.assertNotIn("SEGREDO7", message)
        self.assertIn("Exception", message)

    def test_generator_exit_from_handler_is_converted_not_propagated(self):
        # Só KeyboardInterrupt continua propagando;
        # GeneratorExit de um handler (que não é chamado como gerador aqui)
        # também vira ValueError, não um traceback cru.
        pin_plugins("isola")
        with (
            patch.dict(os.environ, {"GB_PLUGINS": "isola"}),
            self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
            self.assertRaises(ValueError) as ctx,
        ):
            plugin_commands.run(self.args("genexit"))
        message = str(ctx.exception)
        self.assertIn("Plugin isola", message)
        self.assertIn("GeneratorExit", message)
        self.assertNotIn("nao-deveria-propagar-cru", message)
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_call_failed", joined)
        self.assertIn("error=GeneratorExit", joined)


if __name__ == "__main__":
    unittest.main()
