"""Operational diagnostics. Never log command arguments or tokens; tracebacks are stored redacted."""

import contextlib
import contextvars
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from .models import now

ACTIVE = contextvars.ContextVar("getbrolls_operation", default=None)


def _acquire_lock(stream, platform=None, windows=None):
    """Acquire a non-blocking, one-byte lock on Windows or a flock elsewhere."""
    platform = platform or os.name
    if platform == "nt":
        if windows is None:
            import msvcrt as windows

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write("\0")
            stream.flush()
        stream.seek(0)
        try:
            windows.locking(stream.fileno(), windows.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError from exc
        return
    import fcntl

    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_lock(stream, platform=None, windows=None):
    platform = platform or os.name
    if platform == "nt":
        if windows is None:
            import msvcrt as windows

        stream.seek(0)
        windows.locking(stream.fileno(), windows.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream, fcntl.LOCK_UN)


def record_warning(code, message):
    current = ACTIVE.get()
    if current is not None:
        current["warnings"].append({"code": code, "message": message})


def record_commit():
    current = ACTIVE.get()
    if current is not None:
        current["state_committed"] = True


def redact(text):
    value = str(text)
    for key in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "YOUTUBE_API_KEY"):
        secret = os.getenv(key)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r'https?://[^\s"<>]+', "[URL omitida]", value)
    return value[:1200]


STDERR_TAIL_MAX_CHARS = 600


def stderr_tail(stderr, limit=6):
    """Last `limit` non-empty stderr/detail lines, redacted, joined and capped.

    Shared truncation helper for CLI stderr/detail text (subprocess stderr, HTTP error
    bodies), so callers cannot drift apart on secrets, URLs or how much is surfaced.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    joined = " | ".join(redact(line) for line in lines[-limit:])
    return joined[:STDERR_TAIL_MAX_CHARS]


@contextlib.contextmanager
def project_lock(project):
    if not project:
        yield
        return
    root = Path(project).resolve() / "brolls"
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".command.lock").open("a+", encoding="utf-8") as lock:
        try:
            _acquire_lock(lock)
        except BlockingIOError:
            raise ValueError(
                "Outro comando está usando este projeto. Aguarde terminar antes de repetir."
            ) from None
        try:
            yield
        finally:
            _release_lock(lock)


# Comandos que só leem o projeto: sem trava exclusiva e sem criar a árvore.
# `brief` entra aqui porque só lê BRIEF.md, RULES.md e o manifesto já existente.
READ_ONLY_COMMANDS = ("status", "serve", "brief")
# (comando, ação) somente leitura, além dos comandos inteiros acima: `queue --action status`
# só consulta queue.json (mesmo contrato de `status`), nunca deve tomar a trava exclusiva.
READ_ONLY_ACTIONS = {("queue", "status")}


def audited(args, execute):
    started = time.monotonic()
    event = {
        "at": now(),
        "operation": args.command,
        "status": "running",
        "state_committed": False,
        "warnings": [],
    }
    token = ACTIVE.set(event)
    project = getattr(args, "project", None)
    read_only = args.command in READ_ONLY_COMMANDS or (args.command, getattr(args, "action", None)) in READ_ONLY_ACTIONS
    log = Path(project).resolve() / "brolls/diagnostics.jsonl" if project else None
    result = None
    failure = None
    try:
        with project_lock(None if read_only else project):
            result = execute(args)
            event["status"] = "success"
            if event["warnings"] and isinstance(result, dict):
                result = {**result, "warnings": event["warnings"]}
            return result
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
    ) as exc:
        event["status"] = "error"
        event["recovery_pending"] = bool(
            log and (log.parent / ".pending-transaction.json").exists()
        )
        # Diagnostics survive regardless of classification, redacted like everything else here.
        event["type"] = type(exc).__name__
        event["repr"] = redact(repr(exc))
        event["traceback"] = redact(traceback.format_exc())
        if isinstance(exc, (KeyError, TypeError, AttributeError)):
            # These are bug signatures, not user-fixable input problems; the traceback is what
            # a maintainer needs, not a RULES.md pointer.
            event["error_code"] = "INTERNAL_ERROR"
            event["message"] = (
                f"Erro interno inesperado (bug) [type: {exc.__class__!r}]. Reporte incluindo diagnostics.jsonl"
                + (f" ({log})." if log else ".")
            )
        else:
            from .http import ProviderError  # local: avoids a runtime<->http import cycle

            if isinstance(exc, ProviderError):
                event["error_code"] = "INVALID_DATA"
                event["message"] = redact(exc) + " Confira docs/RULES.md."
                current = ACTIVE.get()
                if current is not None:
                    current["warnings"].append(
                        {"code": "PROVIDER_ERROR", "message": redact(exc)}
                    )
                    event["warnings"] = current["warnings"]
            else:
                event["error_code"] = "IO_ERROR" if isinstance(exc, OSError) else "INVALID_DATA"
                event["message"] = redact(exc)
        failure = OperationError(
            {
                **event,
                "hint": "Se recovery_pending=true, o próximo comando retoma a gravação. Se state_committed=true e não houver pendência, execute review para regenerar a página. Caso contrário, corrija o erro e repita.",
                "log": str(log) if log else None,
            }
        )
        raise failure from None
    except KeyboardInterrupt:
        event["status"] = "interrupted"
        event["error_code"] = "INTERRUPTED"
        failure = OperationError(
            {
                **event,
                "message": "Operação interrompida. O próximo comando recuperará uma gravação pendente, se houver.",
                "log": str(log) if log else None,
            }
        )
        raise failure from None
    finally:
        event["duration_ms"] = round((time.monotonic() - started) * 1000)
        # Um comando somente leitura nunca cria a árvore do projeto só para logar.
        if log and (not read_only or log.parent.is_dir()):
            try:
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                warning = {
                    "code": "LOG_UNAVAILABLE",
                    "message": "Não foi possível gravar diagnostics.jsonl. Confira espaço e permissões.",
                }
                if failure is not None:
                    failure.payload.setdefault("warnings", []).append(warning)
                elif isinstance(result, dict):
                    result.setdefault("warnings", []).append(warning)
                else:
                    # `result` isn't a dict (e.g. None, or a non-mapping success value), so
                    # the warning has nowhere to live in the response; it must not vanish.
                    print(json.dumps(warning, ensure_ascii=False), file=sys.stderr)
        ACTIVE.reset(token)


def write_diagnostics_log(project, event):
    """Append one diagnostics event to <project>/brolls/diagnostics.jsonl; best-effort.

    Used by audited()'s own finally block and by cli.py's fallback handler for errors that
    happen outside audited() (e.g. before argument parsing finishes), so both paths share
    one envelope shape and one place that can fail to write without crashing the caller.
    """
    if not project:
        return None
    log = Path(project).resolve() / "brolls/diagnostics.jsonl"
    event = {"at": now(), **event}
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        return log
    except OSError:
        return None


class OperationError(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload.get("message", "Falha na operação."))
