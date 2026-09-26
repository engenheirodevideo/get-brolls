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
from . import guard, loader
from .contracts import NAME_RE, CommandContext
from .registry import get_registry

_log = logs.get("sdk")

ARG_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
# Teto do JSON do resultado — medido na MESMA forma que a CLI de fato escreve
# (`cli.py` imprime com `indent=2, ensure_ascii=False`), não no compacto: um
# aninhamento estreito e profundo cabe em poucos KB compacto e explode para GBs
# quando indentado (cada nível de profundidade multiplica a indentação de toda
# linha abaixo dele — Ø(profundidade × nós), não linear). Só depois que o
# aninhamento já está limitado por MAX_RESULT_DEPTH essa medida fica barata.
MAX_RESULT_BYTES = 1024 * 1024
# Teto de aninhamento dict/list do resultado (níveis). Some com o teto de
# tamanho acima: sem ele, uma lista de listas de 1 elemento cada, 100_000 níveis
# funda, cabe em ~200 KB compacto (sob o teto de tamanho) mas vira ~20 GB
# indentado — já confirmado enchendo o disco antes desta correção.
MAX_RESULT_DEPTH = 64


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


def _isolate(action, plugin_id, name, build_message, keep_text=True):
    """Roda `action()` isolado do resto do processo, pelo mesmo `guard.isolated` de
    toda porta de entrada de plugin.

    Qualquer `BaseException` de código de terceiro — não só `Exception`/
    `SystemExit`, mas também uma classe custom que herde `BaseException`
    direto, e também `GeneratorExit` — vira `ValueError` com só o TIPO da exceção
    (via `guard.safe_type_name`, que nunca chama `__str__`/`__repr__`/`__name__`
    de metaclasse hostil do plugin). A exceção é `PluginError` (ou uma recusa do
    próprio core): aí o texto, já saneado por `guard.sanitize_text`, vai junto
    (`keep_text=False` desliga isso onde quem falha é uma operação do core, como o
    `json.dumps` do resultado). Só `KeyboardInterrupt` continua propagando. A
    exceção nova sai sem cadeia até a original: `traceback.format_exc()` (chamado
    depois em `runtime.audited()`) nunca alcança o `__str__` hostil dela.
    """

    def failed(failure):
        if keep_text and failure.text:
            return ValueError(guard.prefixed(plugin_id, failure.text))
        return ValueError(build_message(failure.type_name))

    return guard.isolated(plugin_id, action, log_fields={"command": name}, on_failure=failed)


def _depth_within_limit(value, limit):
    """`True` se o aninhamento dict/list de `value` não passa de `limit` níveis.

    Iterativo, com pilha explícita — nunca uma função recursiva Python: o
    próprio ponto desta checagem é recusar uma estrutura funda demais antes de
    fazer qualquer trabalho proporcional à profundidade dela (indentar, por
    exemplo); percorrê-la com recursão de novo derrotaria o propósito. `value`
    já é o resultado normalizado por um round-trip de JSON (só dict/list/str/
    int/float/bool/None), nunca o objeto original do plugin — a pilha só lê
    `.values()`/iteração de tipos embutidos, nunca um método de terceiro.
    """
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > limit:
            return False
        if isinstance(current, dict):
            stack.extend((v, depth + 1) for v in current.values())
        elif isinstance(current, list):
            stack.extend((v, depth + 1) for v in current)
    return True


def _within_result_limits(serialized, plugin_id, name):
    """`json.loads(serialized)` mais os tetos de tamanho/profundidade do
    resultado. Extraído de `run()` só para manter a complexidade dela sob
    controle — nenhuma lógica além da já descrita nos comentários de `run()`.
    """
    # Checagem barata pelo tamanho compacto primeiro: descarta um resultado já
    # grande de cara (muita largura) sem gastar em `json.loads`/indentação.
    if len(serialized.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError(
            f"Plugin {plugin_id}: o comando {name} devolveu um resultado grande demais "
            f"(> {MAX_RESULT_BYTES // (1024 * 1024)} MB de JSON)."
        )
    payload = json.loads(serialized)
    # Teto de aninhamento ANTES de qualquer coisa proporcional à profundidade:
    # indentar (abaixo, e depois de novo quando a CLI de fato imprime o
    # resultado com `indent=2`) custa Ø(profundidade × nós) — um aninhamento
    # estreito e profundo passa fácil pelo teto de tamanho compacto acima e só
    # explode quando indentado.
    if not _depth_within_limit(payload, MAX_RESULT_DEPTH):
        raise ValueError(
            f"Plugin {plugin_id}: o comando {name} devolveu um resultado com aninhamento "
            f"profundo demais (> {MAX_RESULT_DEPTH} níveis)."
        )
    if not isinstance(payload, dict):
        raise ValueError(f"Plugin {plugin_id}: o comando {name} tem que devolver um objeto JSON.")
    # Mede no formato que a CLI de fato escreve (`cli.py` imprime com
    # `indent=2, ensure_ascii=False`) — só chega aqui com aninhamento já
    # limitado acima, então o custo desta indentação é seguro de pagar.
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(rendered.encode("utf-8")) > MAX_RESULT_BYTES:
        raise ValueError(
            f"Plugin {plugin_id}: o comando {name} devolveu um resultado grande demais "
            f"(> {MAX_RESULT_BYTES // (1024 * 1024)} MB de JSON)."
        )
    return payload


def _missing(registry, plugin_id, name):
    row = registry.plugins.get(plugin_id)
    if row is None:
        return f"Plugin {plugin_id} não está instalado. Rode x --list para ver os comandos disponíveis."
    if row["status"] != "enabled":
        # Motivo sem o ponto final (a frase continua) e, fora de GB_PLUGINS, a dica
        # certa: ajustar a variável, não habilitar de novo.
        reason = guard.without_prefix(plugin_id, (row.get("reason") or "").strip()).rstrip(" .")
        detail = f": {reason}" if reason else ""
        hint = loader.status_hint(row, "Rode plugins --action list / doctor.")
        return f"Plugin {plugin_id} está {row['status']}{detail}. {hint}"
    return f"Plugin {plugin_id} não tem o comando {name}. Rode x --list para ver os comandos disponíveis."


def run(args):
    """`x --list` ou `x <plugin> <comando>`: roda o comando do plugin isolado e devolve o resultado."""
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
    raw = _isolate(
        lambda: spec.handler(dict(values), ctx),
        plugin_id,
        name,
        lambda type_name: f"Plugin {plugin_id}: o comando {name} falhou ({type_name}).",
    )
    # Round-trip de JSON separado da chamada do handler: aqui só se valida se o
    # retorno é serializável — gerador/set/NaN reprovam (`TypeError`/`ValueError`),
    # e um dict de terceiro com `.items()` hostil pode levantar qualquer coisa
    # (inclusive `SystemExit`/um `BaseException` custom) durante a própria
    # serialização, daqui mesmo isolamento de `_isolate` — nunca misturado com a
    # falha do handler acima (mesma ideia de `guard._normalize`).
    serialized = _isolate(
        lambda: json.dumps(raw, allow_nan=False),
        plugin_id,
        name,
        lambda type_name: f"Plugin {plugin_id}: o comando {name} tem que devolver um objeto JSON ({type_name}).",
        keep_text=False,
    )
    payload = _within_result_limits(serialized, plugin_id, name)
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
