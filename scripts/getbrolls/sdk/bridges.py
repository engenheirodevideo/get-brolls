"""Rodar uma ponte de ambiente isolada e conferir o que ela devolveu (`package`, `readback`).

A ponte é um par de funções puras (spec §4.4): recebe cópias e devolve texto. Nada aqui grava:
quem grava `<ambiente>/` no pacote e `return/` na volta é o comando do core. O que sai daqui só
tem tipos do core.
"""

import json
import logging
from pathlib import PurePosixPath

from .. import logs
from ..errors import PrerequisiteError
from . import guard, loader, official, official_pins, schemas
from .contracts import NAME_RE
from .files import bad_file_name
from .jsonschema import errors

_log = logs.get("sdk")

FILE_EXTENSIONS = (".md", ".json", ".txt")  # texto de intenção, nunca código (.js/.html ficam de fora)
FILES_MAX = 50
FILE_MAX_BYTES = 256 * 1024
TOTAL_MAX_BYTES = 1024 * 1024
PATH_MAX_SEGMENTS = 6
NOTES_MAX = 50
ENV_DETAIL_MAX_BYTES = 16 * 1024
READBACK_KEYS = ("state", "artifacts", "renders", "scenes", "problems", "env_detail")


class BridgeResultError(ValueError):
    """O resultado da ponte quebra uma regra; o texto é do core e diz qual."""


def _json_copy(value, what):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise BridgeResultError(f"{what} não é JSON válido.") from None


def _check_file(path, text, env):
    if type(path) is not str or type(text) is not str:
        raise BridgeResultError("files é um dict de caminho (texto) para conteúdo (texto).")
    segments = path.split("/")
    if len(segments) > PATH_MAX_SEGMENTS or segments[0] != env or len(segments) < 2:  # noqa: PLR2004 - "<env>/<nome>"
        raise BridgeResultError(f"files: {guard.plain_line(path, limit=120)!r} tem que ficar em {env}/, até 6 níveis.")
    if any(bad_file_name(segment) for segment in segments):
        raise BridgeResultError(f"files: {guard.plain_line(path, limit=120)!r} tem um nome inválido.")
    if PurePosixPath(path).suffix not in FILE_EXTENSIONS:
        raise BridgeResultError(f"files: {path!r} não é {', '.join(FILE_EXTENSIONS)}.")
    if "\x00" in text:
        raise BridgeResultError(f"files: {path!r} tem o caractere NUL.")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise BridgeResultError(f"files: {path!r} não é UTF-8 válido.") from None
    if size > FILE_MAX_BYTES:
        raise BridgeResultError(f"files: {path!r} passa de {FILE_MAX_BYTES // 1024} KiB.")
    return size


def check_package(result, env) -> dict:
    """`{"files": {caminho: texto}, "notes": (...)}` conferido: só .md/.json/.txt em `<env>/`, até
    50 arquivos, 256 KiB cada e 1 MiB no total, com nomes POSIX relativos."""
    result = _json_copy(result, "O resultado de package")
    if type(result) is not dict or set(result) - {"files", "notes"} or type(result.get("files")) is not dict:
        raise BridgeResultError('package devolve {"files": {...}, "notes": [...]}.')
    files, notes = result["files"], result.get("notes", [])
    if len(files) > FILES_MAX:
        raise BridgeResultError(f"files passa de {FILES_MAX} arquivos.")
    if len({path.casefold() for path in files}) != len(files):
        raise BridgeResultError("files tem dois caminhos que só diferem na caixa.")
    total = sum(_check_file(path, text, env) for path, text in files.items())
    if total > TOTAL_MAX_BYTES:
        raise BridgeResultError(f"files passa de {TOTAL_MAX_BYTES // 1024} KiB no total.")
    if type(notes) is not list or len(notes) > NOTES_MAX or any(type(note) is not str for note in notes):
        raise BridgeResultError(f"notes é uma lista de até {NOTES_MAX} textos.")
    return {"files": files, "notes": tuple(guard.plain_line(note) for note in notes)}


def _readback_schema():
    whole = schemas.load("handoff_return")
    return {
        "type": "object",
        "required": list(READBACK_KEYS),
        "additionalProperties": False,
        "properties": {key: whole["properties"][key] for key in READBACK_KEYS},
    }


def check_readback(result) -> dict:
    """O que `readback` devolve, conferido pelos mesmos nós do `schemas/handoff_return.schema.json`
    (estado fechado, até 50 itens, problemas de até 300 caracteres) e `env_detail` até 16 KiB."""
    result = _json_copy(result, "O resultado de readback")
    found = errors(result, _readback_schema())
    if found:
        raise BridgeResultError(f"readback fora do contrato: {found[0]}")
    if len(json.dumps(result["env_detail"], ensure_ascii=False).encode("utf-8")) > ENV_DETAIL_MAX_BYTES:
        raise BridgeResultError(f"env_detail passa de {ENV_DETAIL_MAX_BYTES // 1024} KiB.")
    return result


def _bridge(registry, env):
    if type(env) is not str or not NAME_RE.fullmatch(env):
        raise ValueError("Ambiente inválido; use o id do ambiente (a-z, 0-9, _).")
    if registry.environment(env) is None and env in official_pins.PINS:
        row = official.load(registry, env)
        if row["status"] != "enabled":
            raise PrerequisiteError(f"A ponte {env} está {row['status']}: {loader.status_hint(row, row['reason'])}")
    spec = registry.environment(env)
    owner = registry.owner("environment", env)
    if spec is None or owner is None or (registry.plugins.get(owner) or {}).get("status") != "enabled":
        raise ValueError(f"O ambiente {env} não está disponível agora; rode plugins --action list para ver por quê.")
    return spec, owner


def _copied(owner, env, step, value):
    """Cópia JSON do que a ponte devolveu, também isolada: um `items()`/`__iter__` de subclasse do plugin
    pode levantar qualquer coisa (inclusive `SystemExit`) quando o `json.dumps` o percorre."""

    def failed(failure):
        if failure.type_name in ("TypeError", "ValueError", "RecursionError"):
            logs.event(_log, logging.WARNING, "plugin_bridge_refused", plugin=owner, environment=env, step=step)
            return ValueError(
                f"Plugin {owner}: {step} devolveu um resultado fora das regras: "
                f"O resultado de {step} não é JSON válido."
            )
        return ValueError(f"Plugin {owner}: {step} falhou ({failure.type_name}).")

    return guard.isolated(
        owner,
        lambda: json.loads(json.dumps(value, allow_nan=False)),
        log_fields={"environment": env, "step": step},
        on_failure=failed,
    )


def _isolated(owner, env, step, function, argument):
    """`function(cópia de argument)` isolado: exceção do plugin vira `ValueError` com `Plugin <id>: …`.
    O resultado volta já copiado (só tipos do core), com a cópia isolada do mesmo jeito."""
    result = guard.isolated(
        owner,
        function,
        _json_copy(argument, f"A entrada de {step}"),
        log_fields={"environment": env, "step": step},
        on_failure=lambda failure: ValueError(
            guard.prefixed(owner, failure.text)
            if failure.text
            else f"Plugin {owner}: {step} falhou ({failure.type_name})."
        ),
    )
    return _copied(owner, env, step, result)


def _checked(owner, env, step, check):
    """`check()` do resultado; fora das regras vira `ValueError` com `Plugin <id>: …` e uma linha no log."""
    try:
        checked = check()
    except BridgeResultError as exc:
        logs.event(_log, logging.WARNING, "plugin_bridge_refused", plugin=owner, environment=env, step=step)
        raise ValueError(f"Plugin {owner}: {step} devolveu um resultado fora das regras: {exc}") from None
    logs.event(_log, logging.INFO, "plugin_bridge", plugin=owner, environment=env, step=step)
    return checked


def run_package(registry, env, handoff) -> dict:
    """`package(handoff)` da ponte `env`, isolado e conferido."""
    spec, owner = _bridge(registry, env)
    result = _isolated(owner, env, "package", spec.package, handoff)
    return _checked(owner, env, "package", lambda: check_package(result, env))


def run_readback(registry, env, view) -> dict:
    """`readback(view)` da ponte `env`, isolado e conferido."""
    spec, owner = _bridge(registry, env)
    result = _isolated(owner, env, "readback", spec.readback, view)
    return _checked(owner, env, "readback", lambda: check_readback(result))
