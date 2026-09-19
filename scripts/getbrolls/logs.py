"""Structured logging foundation: a private, rotating `getbrolls.log` per project.

One `key=value` line per event, grep-friendly. Stdout is never touched by this
module. The file handler is opt-out via `GB_LOG_LEVEL=off`; an optional stderr
mirror exists only for interactive debugging (`GB_LOG_STDERR=1`) and always
prints before the CLI's own JSON error envelope, which stays the last stderr
line.

`configure()` is the only entry point library modules should not call
directly; call it once, early, from the CLI process. Everywhere else, get a
logger with `logs.get(__name__.rsplit(".", 1)[-1])` and log through
`logs.event()` or `logs.timed()`.
"""

import contextlib
import json
import logging
import logging.handlers
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import runtime

_ROOT_NAME = "getbrolls"
_TRUTHY = {"1", "true", "yes", "on"}
_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
_UNQUOTED_SAFE = re.compile(r'[\s"\'=]')
_MAX_VALUE_CHARS = 500
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
LOG_FILENAME = "getbrolls.log"

_state: dict[str, tuple[str | None, str, bool, bool] | None] = {"signature": None}

# A library logger with no handler anywhere up the chain would otherwise fall
# back to Python's own "handler of last resort" and print straight to stderr —
# exactly what this module exists to prevent. This default is replaced by
# configure() as soon as it runs; it only guards the window before that.
_root = logging.getLogger(_ROOT_NAME)
_root.addHandler(logging.NullHandler())
_root.propagate = False


def get(name):
    """Child logger under the shared `getbrolls` hierarchy, e.g. `getbrolls.media`."""
    return logging.getLogger(f"{_ROOT_NAME}.{name}")


def _chmod_private(path):
    if os.name != "nt":
        with contextlib.suppress(OSError):
            Path(path).chmod(0o600)


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that keeps the active file (and its backups) at 0600."""

    def _open(self):
        stream = super()._open()
        _chmod_private(self.baseFilename)
        return stream

    def doRollover(self):  # noqa: N802 - nome exigido pela API de RotatingFileHandler
        super().doRollover()
        for index in range(1, self.backupCount + 1):
            candidate = f"{self.baseFilename}.{index}"
            if Path(candidate).exists():
                _chmod_private(candidate)


class RedactingFilter(logging.Filter):
    """Scrub secrets and the home directory from every record before it is emitted.

    Runs on the fully rendered message (message + args), not just `record.msg`,
    so lazy `%s`-style callers are covered too. Never raises: a formatting
    failure degrades to a best-effort string rather than breaking the command.
    """

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - logging must never break a command
            message = str(record.msg)
        with contextlib.suppress(Exception):
            message = runtime.scrub_home(runtime.redact(message))
        record.msg = message
        record.args = ()
        return True


class _LineFormatter(logging.Formatter):
    def format(self, record):
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        stamp = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
        return f"{stamp} level={record.levelname} logger={record.name} {record.getMessage()}"


def _format_value(value):
    """One log field's value: `-` for None, quoted when it needs to be, capped at 500 chars."""
    if value is None:
        return "-"
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - logging must never break a command
        text = "<unrepr>"
    if len(text) > _MAX_VALUE_CHARS:
        text = text[:_MAX_VALUE_CHARS]
    if text == "" or _UNQUOTED_SAFE.search(text):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:  # noqa: BLE001 - logging must never break a command
            text = '"' + text.replace('"', '\\"') + '"'
    return text


def render_fields(event_name, fields):
    """Deterministic `event=<name> k=v ...` line; insertion order of `fields` is preserved."""
    parts = [f"event={_format_value(event_name)}"]
    for key, value in fields.items():
        parts.append(f"{key}={_format_value(value)}")
    return " ".join(parts)


def event(logger, level, event_name, **fields):
    """Log one `event=<event_name> k=v ...` line at `level`; never raises."""
    try:
        if not logger.isEnabledFor(level):
            return
        logger.log(level, render_fields(event_name, fields))
    except Exception:  # noqa: BLE001 - logging must never break a command
        pass


@contextlib.contextmanager
def timed(logger, event_name, **fields):
    """Log one INFO line with `ms=<duration>` on success, one WARNING line with
    `ms` and `error=<ExceptionClass>` on failure. Re-raises the original exception
    unchanged. Negligible overhead when the logger's effective level filters both."""
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        event(
            logger,
            logging.WARNING,
            event_name,
            ms=round((time.monotonic() - started) * 1000),
            error=type(exc).__name__,
            **fields,
        )
        raise
    else:
        event(logger, logging.INFO, event_name, ms=round((time.monotonic() - started) * 1000), **fields)


def _degrade():
    """Best-effort fallback: no handlers beyond a NullHandler, nothing raises past here."""
    for handler in list(_root.handlers):
        _root.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()
    for existing_filter in list(_root.filters):
        _root.removeFilter(existing_filter)
    _root.addHandler(logging.NullHandler())
    _root.propagate = False
    _state["signature"] = None


def shutdown():
    """Flush and close every handler at the end of a command. Never raises.

    The log file lives inside the project folder: leaving it open would keep a
    handle on it after the command returned, which on Windows stops the person (or a
    test) from moving or deleting that folder. The next `configure()` re-attaches.
    """
    with contextlib.suppress(Exception):
        _degrade()


def configure(project, *, read_only):
    """Set up (once) the `getbrolls` logger hierarchy for this process.

    Idempotent: calling this twice with the same project/settings/read_only is a
    no-op. Never raises: any failure (bad permissions, a locked rotation file on
    Windows, a full disk) degrades to a NullHandler and the command proceeds.
    """
    logging.raiseExceptions = False
    try:
        _configure(project, read_only=read_only)
    except Exception:  # noqa: BLE001 - logging setup must never break a command; degrade to a NullHandler instead
        _degrade()


def _configure(project, *, read_only):
    raw_level = (os.environ.get("GB_LOG_LEVEL") or "INFO").strip().upper()
    stderr_on = (os.environ.get("GB_LOG_STDERR") or "").strip().lower() in _TRUTHY
    project_path = Path(project).resolve() if project else None

    signature = (str(project_path) if project_path else None, raw_level, stderr_on, bool(read_only))
    if _state.get("signature") == signature:
        return

    for handler in list(_root.handlers):
        _root.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()
    for existing_filter in list(_root.filters):
        _root.removeFilter(existing_filter)
    _root.propagate = False
    _root.setLevel(logging.DEBUG)
    # A logger-level filter only sees records created on that very logger, not the
    # ones that propagate up from `getbrolls.<module>` children. Redaction therefore
    # lives on every HANDLER, which sees all records; the logger-level copy only
    # covers records logged directly on the package logger.
    redacting = RedactingFilter()
    _root.addFilter(redacting)

    # An invalid GB_LOG_LEVEL is a user-input problem, not a logging-setup
    # failure: `config.settings()` (called right after this, inside the
    # audited() command) is the one place that turns it into the same
    # ValueError shape as every other invalid setting. Here we only need to
    # not crash, so an unrecognised value degrades to "no logging" instead.
    level = None if raw_level == "OFF" else _LEVELS.get(raw_level)

    added = False
    if level is not None and project_path is not None and not read_only:
        brolls = project_path / "brolls"
        brolls.mkdir(parents=True, exist_ok=True)
        handler = _PrivateRotatingFileHandler(
            str(brolls / LOG_FILENAME),
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        handler.setLevel(level)
        handler.setFormatter(_LineFormatter())
        handler.addFilter(redacting)
        _root.addHandler(handler)
        added = True

    if level is not None and stderr_on:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setLevel(level)
        stream_handler.setFormatter(_LineFormatter())
        stream_handler.addFilter(redacting)
        _root.addHandler(stream_handler)
        added = True

    if not added:
        _root.addHandler(logging.NullHandler())

    _state["signature"] = signature


def log_path(project):
    """Absolute path to this project's `getbrolls.log`, or None when it doesn't exist."""
    if not project:
        return None
    path = Path(project).resolve() / "brolls" / LOG_FILENAME
    return path if path.is_file() else None
