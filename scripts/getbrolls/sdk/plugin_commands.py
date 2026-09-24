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
from . import loader
from .contracts import NAME_RE, CommandContext
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
    """`--arg chave=valor` (repetível) → dict de texto.

    Nunca ecoa o par bruto nem o valor nas mensagens de erro — só a chave, e só
    depois que ela já passou na validação (a-z, 0-9, _). Um `--arg` sem `=`, ou
    com chave vazia/fora do charset, pode carregar um segredo colado (`token=x`
    digitado sem `--arg`, ou `=segredo`); a mensagem então descreve só o defeito
    do formato, nunca repete o texto recebido.
    """
    values = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError("--arg espera chave=valor; faltou '=' no --arg.")
        if not key:
            raise ValueError("--arg espera chave=valor; chave vazia no --arg.")
        if not ARG_KEY_RE.fullmatch(key):
            raise ValueError("--arg espera chave em minúsculas (a-z, 0-9, _) no --arg.")
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
    # Valida a forma de id/nome ANTES de tocar no registro — `get_registry()` monta
    # o registro de plugins habilitados na primeira consulta do processo (roda
    # `register()` de verdade). Um id/nome fora do charset (ex.: "../../etc") nunca
    # deveria sequer chegar a essa etapa; a mensagem nunca ecoa o valor bruto do argv.
    if not NAME_RE.fullmatch(plugin_id):
        raise ValueError("Plugin id inválido; rode `x --list` para ver os comandos disponíveis.")
    if not NAME_RE.fullmatch(name):
        raise ValueError("Nome de comando inválido; rode `x --list` para ver os comandos disponíveis.")
    values = parse_pairs(args.arg)
    registry = get_registry()
    spec = registry.command(plugin_id, name)
    if spec is None:
        raise ValueError(_missing(registry, plugin_id, name))
    project = Path(args.project).expanduser().resolve() if args.project else None
    started = time.monotonic()
    ctx = CommandContext(plugin_id, project)
    try:
        raw = spec.handler(dict(values), ctx)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - código de plugin é de terceiro: a falha (incl. SystemExit) vira erro do comando, não queda da CLI; KeyboardInterrupt continua propagando. Nunca chama str(exc)/repr(exc): um `__str__` hostil (ex.: levanta SystemExit(0) dentro do __str__) não pode escapar por uma f-string nem por traceback.format_exc() se este virar `__cause__` — por isso `from None` corta a cadeia (nunca `from exc`), e só o TIPO da exceção é seguro de repetir.
        logs.event(
            _log, logging.WARNING, "plugin_call_failed", plugin=plugin_id, command=name, error=type(exc).__name__
        )
        raise ValueError(f"Plugin {plugin_id}: o comando {name} falhou ({type(exc).__name__}).") from None
    try:
        # Round-trip de JSON separado da chamada do handler: aqui só se valida se o
        # retorno é serializável (gerador, set, NaN reprovam), sem misturar com a
        # falha do handler acima — mesma ideia de guard._normalize.
        payload = json.loads(json.dumps(raw, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise ValueError(f"Plugin {plugin_id}: o comando {name} tem que devolver um objeto JSON.") from None
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
