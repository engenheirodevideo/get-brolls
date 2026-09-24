"""`gb x`: comandos que plugins habilitados contribuem, cada um no espaço do plugin.

Comando de plugin só lê o projeto (por cópias, via `CommandContext`) e devolve um
objeto JSON; nunca escreve no ledger. Falha do plugin vira `ValueError` com o id
dele — exit 2 na CLI, não erro interno.
"""

import json
import logging
import re
import time
from pathlib import Path

from .. import logs
from ..runtime import redact
from . import loader
from .contracts import CommandContext
from .registry import get_registry

_log = logs.get("sdk")

ARG_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def listing():
    """Comandos declarados pelos plugins habilitados — lido do manifesto, sem executar código."""
    return [
        {"plugin": row["id"], "command": name, "run": f"x {row['id']} {name}"}
        for row, _, manifest in loader.entries()
        if manifest and row["status"] == "enabled"
        for name in manifest["contributes"]["commands"]
    ]


def parse_pairs(pairs):
    """`--arg chave=valor` (repetível) → dict de texto; chave repetida é erro, não sobrescrita."""
    values = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not ARG_KEY_RE.fullmatch(key):
            raise ValueError(f"--arg espera chave=valor com chave em minúsculas (a-z, 0-9, _): {key or pair!r}.")
        if key in values:
            raise ValueError(f"--arg {key} repetido; passe cada chave uma vez.")
        values[key] = value
    return values


def _missing(registry, plugin_id, name):
    row = registry.plugins.get(plugin_id)
    if row is None:
        return f"Plugin {plugin_id} não está instalado. Rode x --list para ver os comandos disponíveis."
    if row["status"] != "enabled":
        detail = f": {row['reason']}" if row["reason"] else ""
        return f"Plugin {plugin_id} está {row['status']}{detail}. Rode plugins --action list / doctor."
    return f"Plugin {plugin_id} não tem o comando {name}. Rode x --list para ver os comandos disponíveis."


def run(args):
    if args.list:
        if args.plugin_id or args.plugin_command:
            raise ValueError("Use x --list sozinho, ou x <plugin> <comando>.")
        return {"commands": listing()}
    if not args.plugin_id or not args.plugin_command:
        raise ValueError("Informe o plugin e o comando: x <plugin> <comando> [--project P] [--arg chave=valor].")
    plugin_id, name = args.plugin_id, args.plugin_command
    values = parse_pairs(args.arg)
    registry = get_registry()
    spec = registry.command(plugin_id, name)
    if spec is None:
        raise ValueError(_missing(registry, plugin_id, name))
    project = Path(args.project).expanduser().resolve() if args.project else None
    started = time.monotonic()
    try:
        payload = json.loads(
            json.dumps(spec.handler(dict(values), CommandContext(plugin_id, project)), allow_nan=False)
        )
    except (
        Exception,
        SystemExit,
    ) as exc:  # código de plugin é de terceiro: a falha (incl. SystemExit) vira erro do comando, não queda da CLI; KeyboardInterrupt continua propagando
        logs.event(
            _log, logging.WARNING, "plugin_call_failed", plugin=plugin_id, command=name, error=type(exc).__name__
        )
        raise ValueError(
            f"Plugin {plugin_id}: o comando {name} falhou ({type(exc).__name__}: {redact(str(exc))})."
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Plugin {plugin_id}: o comando {name} tem que devolver um objeto JSON.")
    logs.event(
        _log,
        logging.INFO,
        "plugin_command",
        plugin=plugin_id,
        command=name,
        args=len(values),
        ms=round((time.monotonic() - started) * 1000),
    )
    return {"plugin": plugin_id, "command": name, "result": payload}
