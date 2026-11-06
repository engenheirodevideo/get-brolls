"""Instagram queue records: the stop and the cooldown written to `work/queue.json`, and the read before a request.

Loaded as `getbrolls.instagram_queue`, never as a script: only `instagram_pairs` is the entry point.
"""

# pylint: disable=missing-function-docstring,raise-missing-from

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from . import labels
from .runtime import redact

if TYPE_CHECKING:
    from pathlib import Path

# Seconds the collector waits for the project lock to write a refusal into queue.json before it
# gives up and says the refusal was not recorded.
RECORD_LOCK_WAIT_S = 5.0


class QueueNotRecordedError(Exception):
    """A refusal or cooldown that the next run must see was not written to `work/queue.json`.

    `cause` says why, for the person: no errno, no machine path."""

    def __init__(self, not_recorded: str, cause: str):
        super().__init__(not_recorded)
        self.cause = cause


class QueueRecordBusyError(QueueNotRecordedError):
    """The project lock stayed busy for `RECORD_LOCK_WAIT_S`: the refusal was not recorded."""

    def __init__(self, not_recorded: str):
        super().__init__(not_recorded, "projeto ocupado por outro comando")


def _record_in_queue(project: Path | None, reason: str, function: str, not_recorded: str, what: str, **extra: str):
    """Grava `function` de `queue.py` em `<project>/work/queue.json`, sob a trava do projeto.

    Devolve o instante registrado (`until` do cooldown, `at` da parada) ou None quando nada foi
    gravado. `not_recorded` abre os avisos ("cooldown não registrado") e `what` diz o que se grava.
    """
    if project is None:
        print(
            f"WARNING: {not_recorded} (nenhum --project informado; use --project para gravar {what} "
            "em work/queue.json)",
            file=sys.stderr,
        )
        return None
    try:
        from .errors import LockedError
        from .queue import record_cooldown, record_refusal
        from .runtime import project_lock
    except ImportError as exc:
        print(f"WARNING: {not_recorded} (dependências indisponíveis: {redact(exc)})", file=sys.stderr)
        return None
    try:
        with project_lock(project, wait_s=RECORD_LOCK_WAIT_S):
            result = {"record_cooldown": record_cooldown, "record_refusal": record_refusal}[function](
                project, "instagram", reason, **extra
            )
    except LockedError as exc:
        print(f"WARNING: {not_recorded} ({redact(exc)})", file=sys.stderr)
        raise QueueRecordBusyError(not_recorded) from exc
    except OSError as exc:
        # Disco cheio, pasta só de leitura, arquivo preso por outro programa no Windows: a fila não
        # ganhou a parada nem o cooldown e a próxima execução pediria de novo, então o lote falha.
        cause = labels.os_message(exc, project=project)
        print(f"WARNING: {not_recorded}: {cause}", file=sys.stderr)
        raise QueueNotRecordedError(not_recorded, "falha ao gravar o arquivo") from exc
    except ValueError as exc:
        # queue.json inválido: a próxima execução recusa ler a fila e não pede nada.
        print(f"WARNING: {not_recorded} ({redact(exc)})", file=sys.stderr)
        return None
    if not result:
        print(f"WARNING: {not_recorded} (nenhum work/queue.json em {project})", file=sys.stderr)
        return None
    return result.get("until") or result.get("at")


def record_queue_cooldown(project: Path | None, reason: str) -> str | None:
    """Abre um cooldown em `<project>/work/queue.json`, sob a trava do projeto; None quando não registrado."""
    return _record_in_queue(project, reason, "record_cooldown", "cooldown não registrado", "o cooldown")


def record_queue_refusal(project: Path | None, reason: str, reason_class: str) -> str | None:
    """Registra a parada (sem cooldown) em `<project>/work/queue.json`; None quando não registrada."""
    return _record_in_queue(
        project, reason, "record_refusal", "parada não registrada", "a parada", reason_class=reason_class
    )


def _queue_hold(args) -> tuple[str, str] | None:
    """What keeps the batch from asking the CDN, read from `work/queue.json` (read-only): `(kind, message)` or None.

    `kind` is "aborted" for a recorded stop, an unreadable queue or a missing queue module (without
    knowing, no request goes out) and "cooldown" for a rate limit still running. Without `--project`
    there is no queue to read: the stop is a record of the project, and the collector without one
    stays outside it (the "parada não registrada" warning already says so).
    """
    project = getattr(args, "project", None)
    if project is None:
        return None
    try:
        from .queue import hold_of

        stopped, cooldown_until = hold_of(project, "instagram")
    except ImportError as exc:
        return "aborted", f"fila não consultada (dependências indisponíveis: {redact(exc)}); sem ela o lote não sai"
    except (OSError, ValueError) as exc:
        return "aborted", f"fila do projeto ilegível ({redact(exc)}); sem ela o lote não sai"
    if stopped:
        return "aborted", (
            f"fila do instagram parada em {stopped['at']} por recusa de acesso; esperar não autoriza retomar. "
            "Diagnostique com a pessoa; só a confirmação dela, no chat, libera (queue --action resume)"
        )
    if cooldown_until:
        return "cooldown", (
            f"fila do instagram em cooldown até {cooldown_until}; o limite de ritmo ainda vale. "
            "Aguarde esse horário e volte ao mesmo escopo; se o bloqueio voltar, pare"
        )
    return None
