"""Uma marca de gravação por ação da superfície da CLI (§2): nem ação sem marca, nem marca sem ação.

A tabela de `write_marks` é a terceira cópia do que cada ação grava (as outras duas são as tabelas de
`runtime` e `sdk.cli`). Aqui ela é conferida nos dois sentidos: tudo o que o `runtime` trata como só
leitura é `L`, e todo `L` é só leitura no `runtime`. A única saída é a lista `_PROJECT_READ_ONLY` abaixo,
com o motivo de cada linha.
"""

import argparse
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import cli, commands, runtime, write_marks
from getbrolls.sdk.cli import WRITING_ACTIONS

# Ação que o `runtime` trata como só leitura (sem a trava do projeto) e que mesmo assim grava, com o motivo.
# O `runtime` olha o projeto; a marca olha tudo. Cada linha daqui tem de ser mesmo não-`L` e mesmo do `runtime`.
_PROJECT_READ_ONLY = {
    ("setup", None): "instala o runtime em $GB_HOME/runtime; nunca toca um projeto",
    ("x", None): "o comando do plugin grava o próprio resultado em analysis/ (trava própria), nunca em brolls/",
    ("profile", "trust"): "grava $GB_HOME/trusted-profiles.json; nunca um projeto",
    ("profile", "untrust"): "grava $GB_HOME/trusted-profiles.json; nunca um projeto",
    ("serve", "--background"): "grava brolls/.serve.pid e .serve.log; `serve` em primeiro plano só serve arquivos",
    ("serve", "--stop"): "apaga brolls/.serve.pid e encerra o servidor",
}
# Flags de modo que tornam o comando um ensaio ou uma consulta (marca `L`) sem a ação ser do `runtime`.
_DRY_RUN = ("--dry-run",)


def _runtime_read_only(key):
    command, _action = key
    return command in runtime.READ_ONLY_COMMANDS or key in runtime.READ_ONLY_ACTIONS


def _args(key):
    """Os argumentos que a ação teria na CLI, só o que `runtime.writes_installation` lê."""
    command, action = key
    values = {"command": command, "action": action, "yes": True, "legacy": None}
    if action is not None and action.startswith("--"):
        values["action"] = None
        values[action.lstrip("-").replace("-", "_")] = True
    return argparse.Namespace(**values)


class WriteMarkTests(unittest.TestCase):
    def test_every_action_of_the_surface_has_exactly_one_mark(self):
        surface = write_marks.actions(cli.build_parser())
        self.assertEqual(sorted(surface, key=str), sorted(write_marks.MARKS, key=str))

    def test_marks_use_the_six_levels(self):
        self.assertLessEqual(set(write_marks.MARKS.values()), set(write_marks.LEVELS))

    def test_every_mode_flag_is_a_real_option_of_its_command(self):
        sub = next(a for a in cli.build_parser()._actions if isinstance(a, argparse._SubParsersAction))  # pylint: disable=protected-access
        for command, flags in write_marks.MODE_FLAGS.items():
            options = {o for a in sub.choices[command]._actions for o in a.option_strings}  # pylint: disable=protected-access
            self.assertLessEqual(set(flags), options, command)


class AgainstTheRuntimeTests(unittest.TestCase):
    def test_everything_the_runtime_reads_only_is_marked_l(self):
        for key, level in write_marks.MARKS.items():
            if _runtime_read_only(key) and key not in _PROJECT_READ_ONLY and key[1] not in _DRY_RUN:
                self.assertEqual("L", level, key)
        for key in runtime.READ_ONLY_ACTIONS:
            self.assertEqual("L", write_marks.MARKS[key], key)

    def test_every_l_mark_is_read_only_in_the_runtime(self):
        for key, level in write_marks.MARKS.items():
            if level == "L" and key[1] not in _DRY_RUN:
                self.assertTrue(_runtime_read_only(key), key)

    def test_the_exceptions_are_real_and_explained(self):
        for key, reason in _PROJECT_READ_ONLY.items():
            self.assertTrue(reason, key)
            self.assertTrue(_runtime_read_only(key), key)
            self.assertNotEqual("L", write_marks.MARKS[key], key)

    def test_setup_modes_follow_the_installation_write(self):
        for flag in ("--check", "--where"):
            self.assertEqual("L", write_marks.MARKS[("setup", flag)])
            self.assertFalse(runtime.writes_installation(_args(("setup", flag))))
        self.assertTrue(runtime.writes_installation(argparse.Namespace(command="setup", check=False, where=None)))

    def test_dry_run_is_l_only_where_the_command_runs_nothing_when_asked_not_to(self):
        for command, flags in write_marks.MODE_FLAGS.items():
            if "--dry-run" in flags:
                self.assertEqual("L", write_marks.MARKS[(command, "--dry-run")], command)
        # `deliver` e `search` ensaiam pelo `runtime`; o `export` volta antes de gravar (`export._run`).
        self.assertLessEqual({"deliver", "search"}, set(commands.DRY_RUN_COMMANDS))
        self.assertIn("--dry-run", write_marks.MODE_FLAGS["export"])

    def test_installation_writers_are_never_l(self):
        for key, level in write_marks.MARKS.items():
            if runtime.writes_installation(_args(key)):
                self.assertNotEqual("L", level, key)
            if level == "L":
                self.assertFalse(runtime.writes_installation(_args(key)), key)
        for command, action in runtime.INSTALLATION_WRITES:
            self.assertNotEqual("L", write_marks.MARKS[(command, action)])

    def test_plugin_actions_agree_with_the_sdk_writing_actions(self):
        for (command, action), level in write_marks.MARKS.items():
            if command != "plugins":
                continue
            if action in WRITING_ACTIONS:
                self.assertNotEqual("L", level, action)
            elif action not in ("new", "check"):  # `new` grava na pasta de --path, não em $GB_HOME
                self.assertEqual("L", level, action)
        self.assertNotEqual("L", write_marks.MARKS[("plugins", "new")])
        # `check` roda o `register()` do plugin: não toma a trava dos plugins, mas também não é só leitura.
        self.assertNotIn("check", WRITING_ACTIONS)
        self.assertEqual("G", write_marks.MARKS[("plugins", "check")])
        self.assertNotIn(("plugins", "check"), runtime.READ_ONLY_ACTIONS)


def tree(*roots):
    return sorted(str(p) for root in roots for p in Path(root).rglob("*"))


class ReadOnlyIsReadOnlyTests(unittest.TestCase):
    """O que o `runtime` diz que só lê (e a marca `L`) realmente não deixa nada para trás."""

    def test_providers_and_plugins_list_and_check_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, plugin = Path(tmp, "home"), Path(tmp, "plugins")
            home.mkdir()
            plugin.mkdir()
            env = {"GB_HOME": str(home)}
            run_cli("plugins", "--action", "new", "--id", "demo", "--kind", "command", "--path", plugin, env=env)
            before = tree(home, plugin)
            run_cli("providers", env=env)
            run_cli("plugins", "--action", "list", env=env)
            run_cli("plugins", "--action", "check", "--path", plugin / "demo", env=env)
            self.assertEqual(before, tree(home, plugin))
