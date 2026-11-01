"""Pieces shared by the Instagram collector modules: stem and pace shapes, the batch error, `die` and `run`.

Loaded as `getbrolls.instagram_common`, never as a script: only `instagram_pairs` is the entry point.
"""

# pylint: disable=missing-function-docstring,raise-missing-from,unreachable

from __future__ import annotations

import re
import subprocess
import sys
from typing import NoReturn

from . import effects
from .runtime import redact, stderr_tail

STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
PACE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
# Same shape `infer_output_for_stem` uses to recognize a "student" stem (`<username>_<rank>_<code>`).
_STUDENT_STEM_RE = re.compile(r"^(.+)_([0-9]{2})_(.+)$")


def _safe_item_id(stem: str) -> str:
    """A loggable per-item id derived from `stem`.

    Never the raw stem verbatim when it embeds a username (the `student` layout's
    `<username>_<rank>_<code>` shape) — usernames/handles must never reach the log, so
    only the trailing code survives. Any other stem shape is short and username-free
    already, so it is logged as-is. A stem outside `STEM_RE` (the one `infer_output_for_stem`
    refuses, now as that item's failure) is logged as the fixed `unsafe-stem`, never raw.
    """
    if not STEM_RE.fullmatch(stem) or ".." in stem:
        return "unsafe-stem"
    match = _STUDENT_STEM_RE.match(stem)
    if match and not re.match(r"^[0-9]{2}$", match.group(1)):
        return match.group(3)
    return stem


class CollectError(Exception):
    """Erro de coleta com mensagem redigida, código de saída e sinais de parada.

    `cooldown`: limite de ritmo (429), o lote para e a fila ganha um cooldown. `refused`: a CDN
    recusou o acesso (401/403 sem `oe` vencido), o lote termina e a fila ganha uma parada, sem
    cooldown. Nenhum dos dois sinais é "espere e tudo volta".
    """

    # O código que o `event=op` dá à recusa (`logs.error_code`): coleta recusada, não bug.
    error_code = "INVALID_DATA"

    def __init__(self, message, code: int = 1, *, cooldown: bool = False, refused: bool = False, expired: bool = False):
        super().__init__(message)
        self.message = redact(message)
        self.code = code
        self.cooldown = cooldown
        self.refused = refused
        self.expired = expired
        # Only set (to the HTTP status text) for a CDN refusal or rate limit; used solely
        # by the `blocked` log line, never by control flow.
        self.http_status: str | None = None


def die(message: str, code: int = 1, *, cooldown: bool = False) -> NoReturn:
    """Imprime o erro e levanta CollectError com a mensagem redigida para os resumos do lote."""
    print(f"ERROR: {message}", file=sys.stderr)
    raise CollectError(message, code, cooldown=cooldown)


def parse_pace(value: str) -> tuple[int, int]:
    """`MIN-MAX` segundos entre stems, `N` para pausa fixa, `0` para desabilitar."""
    match = PACE_RE.match((value or "").strip())
    if not match:
        die(f"--pace deve ser MIN-MAX em segundos ou 0, recebido: {value!r}")
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) is not None else low
    if low > high:
        die(f"--pace: o mínimo excede o máximo: {value}")
    return low, high


def run(cmd: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    """Executa o comando (ecoando-o em stderr, salvo `quiet`) e devolve o processo concluído."""
    if not quiet:
        print("+ " + " ".join(sh_quote(x) for x in cmd), file=sys.stderr)
    try:
        return effects.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = stderr_tail(exc.stderr or "")
        die(f"{cmd[0]} falhou (exit {exc.returncode}): {tail}")
        raise  # pragma: no cover - die() sempre levanta


def sh_quote(value: str) -> str:
    """Devolve o valor escapado para aparecer seguro numa linha de shell."""
    if not value:
        return "''"
    if re.match(r"^[A-Za-z0-9_./:=@%+-]+$", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"
