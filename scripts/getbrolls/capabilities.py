"""`capabilities`: manifesto JSON dos comandos, derivado do parser e das tabelas do runtime.

Nada aqui é escrito à mão por comando: nomes, flags, `choices`, defaults e ajuda vêm do
`argparse`; o que só lê vem de `runtime.READ_ONLY_*`; os códigos de saída, de `cli.EXIT_CODES`
e `runtime.ERROR_EXIT`. Os comandos de plugin saem do manifesto, sem rodar código de plugin.
"""

# pylint: disable=cyclic-import
# `cyclic-import` vem de getbrolls.capabilities <-> getbrolls.commands: o `commands` importa este
# módulo dentro de `_execute_toolchain`, que só roda depois de tudo carregado.
import argparse

from . import __version__, _paths, cli, runtime
from .sdk import loader, marketplace

SCHEMA_VERSION = 1
ERROR_CODES = (
    "INVALID_DATA",
    "IO_ERROR",
    "LOCKED",
    "USAGE_ERROR",
    "INTERNAL_ERROR",
    "PREREQUISITE_MISSING",
    "INTERRUPTED",
)


def _json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def _value_type(action):
    if action.nargs == 0:
        return "flag"
    if action.type is int:
        return "int"
    if action.type is float:
        return "float"
    return "string"


def _option(action):
    hidden = action.help is argparse.SUPPRESS
    choices = None if action.choices is None else sorted(str(choice) for choice in action.choices)
    default = None if action.default is argparse.SUPPRESS else _json_safe(action.default)
    return {
        "flags": sorted(action.option_strings),
        "dest": action.dest,
        "required": bool(action.option_strings and action.required),
        "takes_value": action.nargs != 0,
        "repeatable": isinstance(action, argparse._AppendAction),  # pylint: disable=protected-access
        "choices": choices,
        "default": default,
        "type": _value_type(action),
        "help": None if hidden else action.help,
        "hidden": hidden,
    }


def _described_actions(parser):
    skipped = (argparse._HelpAction, cli._VersionAction, argparse._SubParsersAction)  # pylint: disable=protected-access
    return [a for a in parser._actions if not isinstance(a, skipped)]  # pylint: disable=protected-access


def _read_only(name):
    if name in runtime.READ_ONLY_COMMANDS:
        return True
    return "by_action" if any(command == name for command, _ in runtime.READ_ONLY_ACTIONS) else False


def _command(name, parser):
    actions = _described_actions(parser)
    return {
        "name": name,
        "summary": cli.SUMMARIES[name],
        "requires_project": any("--project" in a.option_strings and a.required for a in actions),
        "read_only": _read_only(name),
        "read_only_actions": sorted(action for command, action in runtime.READ_ONLY_ACTIONS if command == name),
        "options": [_option(a) for a in actions if a.option_strings],
        "positionals": [_option(a) for a in actions if not a.option_strings],
    }


# Estados de plugin que não são problema: habilitado ou desligado de propósito.
_PLUGIN_OK_STATUSES = ("enabled", "disabled")


def _plugin_rows():
    """`(comandos, problemas, erro)`, lidos do manifesto, sem rodar código de plugin.

    Comandos: só de plugins habilitados. Problemas: todo plugin que não está habilitado nem
    desligado (inválido, com falha, suspenso…), com o motivo — o agente vê por que um comando
    esperado não apareceu.
    """
    try:
        inventory = loader.inventory()
    except ValueError as exc:
        return [], [], str(exc)
    commands = [
        {"plugin": row["id"], "command": command, "status": row["status"], "argv": ["x", row["id"], command]}
        for row in inventory
        if row["status"] == "enabled"
        for command in row.get("contributes", {}).get("commands", [])
    ]
    problems = [
        {"plugin": row["id"], "status": row["status"], "reason": runtime.scrub_home(str(row.get("reason") or ""))}
        for row in inventory
        if row["status"] not in _PLUGIN_OK_STATUSES
    ]
    return (
        sorted(commands, key=lambda row: (row["plugin"], row["command"])),
        sorted(problems, key=lambda row: (row["plugin"], row["status"])),
        None,
    )


def _invocation():
    """Como chamar a CLI desta instalação, sem caminho da máquina; `module` só fora do checkout."""
    module = None if _paths.origin() == "checkout" else " ".join(_paths.module_invocation())
    return {"argv": _paths.portable_cli_argv(), "module": module}


def describe(parser):
    """O manifesto de capacidades desta instalação, em ordem estável."""
    subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices  # pylint: disable=protected-access
    plugin_commands, plugins_problems, plugins_error = _plugin_rows()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": "getbrolls",
        "version": __version__,
        "prog": parser.prog,
        "invocation": _invocation(),
        "output": {"stdout": "JSON do resultado", "stderr": "JSON do erro, com error_code"},
        "global_options": [_option(a) for a in _described_actions(parser) if a.option_strings],
        "commands": [_command(name, subparsers[name]) for name in sorted(subparsers)],
        "exit_codes": [{"code": code, "name": name, "meaning": meaning} for code, name, meaning in cli.EXIT_CODES],
        "error_codes": sorted(
            ({"code": code, "exit": runtime.exit_code_for(code)} for code in ERROR_CODES), key=lambda row: row["code"]
        ),
        "plugin_commands": plugin_commands,
        "plugins_problems": plugins_problems,
    }
    if plugins_error:
        manifest["plugins_error"] = plugins_error
    try:
        manifest["marketplaces"] = marketplace.summary()
    except ValueError as exc:
        manifest["marketplaces"] = []
        # Sem caminho da máquina: o estado é sempre `$GB_HOME/marketplaces.json`.
        manifest["marketplaces_error"] = marketplace.portable_text(exc)
    return manifest
