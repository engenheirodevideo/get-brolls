"""Operational diagnostics. Never log command arguments, tokens or raw tracebacks."""

import contextlib
import contextvars
import json
import os
import re
import time
from pathlib import Path
from .models import now

ACTIVE = contextvars.ContextVar("getbrolls_operation", default=None)


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


@contextlib.contextmanager
def project_lock(project):
    if not project:
        yield
        return
    root = Path(project).resolve() / "brolls"
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".command.lock").open("a+") as lock:
        import fcntl

        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "Outro comando está usando este projeto. Aguarde terminar antes de repetir."
            ) from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


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
    log = Path(project).resolve() / "brolls/diagnostics.jsonl" if project else None
    result = None
    failure = None
    try:
        with project_lock(project):
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
        event["error_code"] = "IO_ERROR" if isinstance(exc, OSError) else "INVALID_DATA"
        event["message"] = (
            redact(exc)
            if not isinstance(exc, (KeyError, TypeError))
            else "Dados incompatíveis. Confira RULES.md, manifest.json e o arquivo de revisão."
        )
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
        if log:
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
        ACTIVE.reset(token)


class OperationError(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload.get("message", "Falha na operação."))
