"""Leitura única de JSON persistido: teto de tamanho e de profundidade, NaN/Infinity e surrogates recusados.

Módulo base (só a stdlib). Toda falha é `JsonReadError` (um `ValueError`), com `reason` fixo e sem o
conteúdo do arquivo; quem lê traduz para a própria recusa (`INVALID_DATA`, `store_corrupt`, `None`).
Assim um arquivo hostil nunca vira `INTERNAL_ERROR` nem passa adiante com um `NaN` dentro.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# Teto padrão: folga larga para uma fila real (dezenas de milhares de itens). O pior caso, 16 MiB de
# listas vazias, ainda custa centenas de MiB de memória; quem lê arquivo pequeno passa o próprio teto.
MAX_BYTES = 16 * 1024 * 1024
# Nenhum arquivo do getbrolls passa de poucos níveis; 64 fica longe do limite de recursão do `json`.
MAX_DEPTH = 64

REASONS = ("too_large", "encoding", "syntax", "non_finite", "too_deep", "not_regular")
# `O_NONBLOCK` deixa o `open` de um FIFO voltar na hora (sem escritor ele travaria); num arquivo comum não muda nada.
# O Windows não tem a flag, e lá abrir uma pasta é `PermissionError` (o `_open` a devolve como `not_regular`);
# `O_BINARY` só existe lá.
# `O_NOCTTY`: abrir um terminal no caminho do estado não o torna o terminal de controle antes da recusa.
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOCTTY", 0)
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class JsonReadError(ValueError):
    """Documento JSON persistido recusado; `reason` é um dos `REASONS`."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"JSON recusado ({reason}).")
        self.reason = reason


_END = object()
# Metade de um par UTF-16 sem a outra: o único `str` que o `json` produz e o UTF-8 não codifica.
_SURROGATE = re.compile("[\ud800-\udfff]")


class _NonFiniteError(ValueError):
    """Sinal interno: `NaN`, `Infinity` ou um número que só cabe como infinito."""


def _refuse_constant(_name: str) -> float:
    """`parse_constant` do `json`: `NaN`, `Infinity` e `-Infinity` são recusados."""
    raise _NonFiniteError


def _finite_float(text: str) -> float:
    """`parse_float` do `json`: `1e400` vira infinito no `float`, e é recusado como o `Infinity`."""
    value = float(text)
    if not math.isfinite(value):
        raise _NonFiniteError
    return value


def _encodable(text: str) -> bool:
    """`text` codifica em UTF-8: um escape `\\uD800` sozinho decodifica, mas quebra quem imprime."""
    return text.isascii() or _SURROGATE.search(text) is None


def _children(value: object) -> Iterator[object] | None:
    """Os filhos de uma lista ou de um objeto (chaves e valores); `None` para um escalar."""
    if isinstance(value, dict):
        return chain.from_iterable(value.items())
    if isinstance(value, list):
        return iter(value)
    return None


def _refusal(value: object, limit: int, *, lone_surrogates: bool = False) -> str | None:
    """`too_deep` acima de `limit` níveis, `encoding` para string que não vira UTF-8, senão `None`.

    `lone_surrogates` dispensa só a checagem de `encoding` (a profundidade continua valendo).

    Sem recursão, e a pilha guarda um iterador por nível aberto (no máximo `limit`), não um item por nó."""
    if isinstance(value, str):
        return None if lone_surrogates or _encodable(value) else "encoding"
    root = _children(value)
    if root is None:
        return None
    stack = [root]
    while stack:
        child = next(stack[-1], _END)
        if child is _END:
            stack.pop()
        elif isinstance(child, str):
            if not lone_surrogates and not _encodable(child):
                return "encoding"
        else:
            nested = _children(child)
            if nested is not None:
                if len(stack) >= limit:
                    return "too_deep"
                stack.append(nested)
    return None


def loads(
    raw: bytes, *, max_bytes: int | None = None, max_depth: int = MAX_DEPTH, allow_lone_surrogates: bool = False
) -> object:
    """Decodifica `raw` (UTF-8 estrito) com os tetos; qualquer falha é `JsonReadError`.

    `allow_lone_surrogates` aceita escapes `\\udcXX` sozinhos: só para quem os grava de propósito
    (o marcador, que serializa um caminho POSIX com bytes fora do UTF-8); os demais arquivos recusam."""
    limit = MAX_BYTES if max_bytes is None else max_bytes
    if len(raw) > limit:
        raise JsonReadError("too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise JsonReadError("encoding") from None
    try:
        value = json.loads(text, parse_constant=_refuse_constant, parse_float=_finite_float)
    except _NonFiniteError:
        raise JsonReadError("non_finite") from None
    except RecursionError:
        raise JsonReadError("too_deep") from None
    except MemoryError:
        raise JsonReadError("too_large") from None
    except ValueError:  # JSONDecodeError e o teto de dígitos de `int`
        raise JsonReadError("syntax") from None
    reason = _refusal(value, max_depth, lone_surrogates=allow_lone_surrogates)
    if reason is not None:
        raise JsonReadError(reason)
    return value


def _is_dir(path: Path) -> bool:
    """`path` é uma pasta agora (seguindo link, como o `open`); erro de `stat` conta como não."""
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    # lenient: only reclassifies an EACCES already raised; a path that does not stat keeps that PermissionError
    except OSError:
        return False


def _open(path: Path) -> int:
    """O descritor de `path`; uma pasta que o sistema recusa abrir (`EACCES` no Windows) é `not_regular`.

    Sem isso a mesma pasta seria "não é um arquivo comum" no POSIX e "sem permissão" no Windows. Um
    arquivo comum sem acesso continua `PermissionError`: o `stat` só reclassifica o que é pasta."""
    try:
        return os.open(path, _OPEN_FLAGS)
    except PermissionError:
        if _is_dir(path):
            raise JsonReadError("not_regular") from None
        raise


def read(
    path: Path, *, max_bytes: int | None = None, max_depth: int = MAX_DEPTH, allow_lone_surrogates: bool = False
) -> object:
    """Lê no máximo o teto + 1 byte de `path` e decodifica com `loads`; `OSError` passa para quem chamou.

    Só arquivo comum é lido: FIFO, pasta ou dispositivo é `not_regular`. O tipo vem do `fstat` do descritor já
    aberto (sem janela entre checar e ler), e a abertura não bloqueia num FIFO sem escritor. Uma pasta que o
    sistema nem deixa abrir (o Windows) também é `not_regular`, com a mesma mensagem em todo sistema."""
    limit = MAX_BYTES if max_bytes is None else max_bytes
    fd = _open(Path(path))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise JsonReadError("not_regular")
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with stream:
        raw = stream.read(limit + 1)
    return loads(raw, max_bytes=limit, max_depth=max_depth, allow_lone_surrogates=allow_lone_surrogates)
