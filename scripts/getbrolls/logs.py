"""Structured logging foundation: a private, rotating `getbrolls.log` per project.

One `key=value` line per event, grep-friendly. Stdout is never touched by this
module. The file handler is opt-out via `GB_LOG_LEVEL=off`; an optional stderr
mirror exists only for interactive debugging (`GB_LOG_STDERR=1`) and always
prints before the CLI's own JSON error envelope, which stays the last stderr
line.

A command with no project that changes the installation (plugins, marketplaces,
clients, templates, the trusted profile, `setup`) writes the same lines, with the
same handler and redaction, to `$GB_HOME/logs/getbrolls.log`. The rule mirrors the
project log: only a command that writes creates the file; one that only reads never
does. Nothing is created before the first line: then a missing `$GB_HOME` and `logs/`
are created 0700, and a `logs/` that is a link, not a folder or someone else's is
refused (the line is dropped, the command goes on).

Each line opens the file without following a link, appends and closes it again, so no
handle is held between lines. The installation log is shared by commands that take
different locks (or none): its size check and rotation run under its own lock file.

`configure()` is the only entry point library modules should not call
directly; call it once, early, from the CLI process. Everywhere else, get a
logger with `logs.get(__name__.rsplit(".", 1)[-1])` and log through
`logs.event()`, `logs.timed()` or, for a refusal, `raise logs.refusal(...)`.
"""

# pylint: disable=broad-exception-caught,too-few-public-methods,no-else-raise,cyclic-import
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0). Os ciclos
# (getbrolls.logs <-> getbrolls.labels, getbrolls.logs <-> getbrolls.file_locks) têm a volta dentro de
# função (`labels` acha a pasta do log, `file_locks.lock_busy` escreve a linha `lock_busy`): quebram em
# tempo de execução e ficam como pares aceitos em `tests/import_rules/`.

import contextlib
import contextvars
import errno
import json
import logging
import os
import re
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import _paths, error_codes, file_locks, labels, private_files, redaction
from .errors import Refusal

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: no flock, the project log keeps its old rotation
    fcntl = None

_ROOT_NAME = "getbrolls"
_TRUTHY = {"1", "true", "yes", "on"}
_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}
# Control characters (ESC, NUL, backspace, ...) force quoting too, even with no
# space/quote/`=` in the value: unquoted they reach the log raw and a later
# `cat`/`less -R` of the file would render them as terminal control codes.
_UNQUOTED_SAFE = re.compile(r'[\s"\'=]|[\x00-\x1f\x7f]')
_MAX_VALUE_CHARS = 500
# Teto de uma mensagem livre do log (o `msg` de um registro). A ordem é sempre redigir (segredos, depois
# pastas) e só então cortar: um corte antes deixaria o começo de um caminho ou de um token sem redigir.
# Acima de `labels.INPUT_MAX` o texto nem é redigido: sai como `<texto omitido: N caracteres>`.
MAX_MESSAGE_CHARS = 4000
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
LOG_FILENAME = "getbrolls.log"
HOME_LOG_DIR = "logs"
# Lock file next to the installation log (and the project log where there is no `flock`, Windows),
# held only around one line's size check, rotation and append. Its name never matches `getbrolls.log.<n>`.
ROTATION_LOCK = ".getbrolls.log.lock"
ROTATION_LOCK_WAIT_S = 2.0
_FLOCK_POLL_S = 0.002
_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
# Above every real level: with no handler attached, `isEnabledFor` answers False and
# `event()` returns before rendering or redacting anything.
_SILENT = logging.CRITICAL + 1
_OPEN_FLAGS = (
    os.O_WRONLY
    | os.O_APPEND
    | os.O_CREAT
    | getattr(os, "O_NOFOLLOW", 0)  # a link in the file's place is refused, never followed
    | getattr(os, "O_NONBLOCK", 0)  # a FIFO in its place fails at once instead of hanging the command
    | getattr(os, "O_BINARY", 0)  # Windows: the text layer above writes the newline, the fd must not touch it
)

# `signature`: what `configure` last set up (None once shut down). `target`: the file the
# last `configure` chose for the command (None when it writes none); it outlives
# `shutdown` so the last-resort error handler still points at the right file.
# `project`: the project folder of the last `configure` (None without one), which
# `effects` makes the paths it logs relative to; it also outlives `shutdown`.
# `command`: the CLI command of this process (`set_command`), for lines written outside
# `runtime.audited` (the threads of the `serve` server); cleared by `shutdown`.
_state: dict = {"signature": None, "target": None, "project": None, "command": None}

# What a refusal of a `ValueError` logs as `error_code` inside `refused_as` (see there).
_KEEP = object()
# Codes a refusal of the core that is not a `ValueError` may declare as its `error_code` attribute.
DOMAIN_CODES = frozenset({"INVALID_DATA", "IO_ERROR", "LOCKED", "PREREQUISITE_MISSING", "USAGE_ERROR"})
_REFUSAL_CODE: contextvars.ContextVar = contextvars.ContextVar("getbrolls_refusal_code", default=_KEEP)

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


def _refused_place(path, why):
    return OSError(errno.EPERM, f"getbrolls.log refused: {why}", str(path))


def _private_folder(folder):
    """Make `folder` (`$GB_HOME/logs`) a real folder of this user at 0700, creating it when missing.

    A missing `$GB_HOME` is created 0700 first: the log may be the first thing to touch
    it, and a parent created on the way by `mkdir(parents=True)` would get the umask
    default. A `logs/` that already exists is never trusted as it is: a link, something
    that is not a folder or (POSIX) a folder of another user raises; a wider mode of a
    folder of this user is tightened to 0700. Never follows a link (`O_NOFOLLOW`; on
    Windows, an `lstat`).
    """
    made = not folder.parent.is_dir()  # lenient: only a folder made here gets the private ACL on Windows
    # effect-exempt: getbrolls.log handler itself; an op line from inside it would recurse into it
    folder.parent.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    if made:
        private_files.make_private(folder.parent, record=False)  # no line: the handler cannot log itself
    with contextlib.suppress(FileExistsError):
        # effect-exempt: getbrolls.log handler itself; an op line from inside it would recurse into it
        folder.mkdir(mode=_PRIVATE_DIR_MODE)
        private_files.make_private(folder, record=False)
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        info = os.lstat(folder)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            raise _refused_place(folder, "not a real folder")
        return
    try:
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise _refused_place(folder, "not a real folder") from None
        raise
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid():
            raise _refused_place(folder, "folder of another user")
        if stat.S_IMODE(info.st_mode) != _PRIVATE_DIR_MODE:
            os.fchmod(fd, _PRIVATE_DIR_MODE)
    finally:
        os.close(fd)


def _open_private(path):
    """Text stream appending to `path`, created 0600; a link, a FIFO or any non-regular file raises.

    Opened with `O_NOFOLLOW` (on Windows, an `lstat` first), so a link planted in the file's
    place is never followed. A file of this user with a wider mode goes back to 0600.
    """
    # lenient: link refusal where O_NOFOLLOW is missing; the open below raises on a locked folder
    if not getattr(os, "O_NOFOLLOW", 0) and Path(path).is_symlink():
        raise _refused_place(path, "link")
    # effect-exempt: getbrolls.log handler itself; an op line from inside it would recurse into it
    fd = os.open(path, _OPEN_FLAGS, _PRIVATE_FILE_MODE)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _refused_place(path, "not a regular file")
        if os.name != "nt" and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) != _PRIVATE_FILE_MODE:
            os.fchmod(fd, _PRIVATE_FILE_MODE)
        if info.st_size == 0:  # new (or still empty): the private ACL on Windows, before the first line
            private_files.make_private(path, record=False)
        # effect-exempt: getbrolls.log handler itself; an op line from inside it would recurse into it
        return os.fdopen(fd, "a", encoding="utf-8", newline="\n")
    except BaseException:
        os.close(fd)
        raise


def folder_lock(folder, wait_s):
    """`flock` on the folder `folder` itself for another module (the log salt of `effects`), waiting up
    to `wait_s`; yields whether it is held. Where there is no `flock` (Windows), yields False at once:
    the caller goes on without it. Never creates a file."""
    if fcntl is None:
        return contextlib.nullcontext(False)
    return _folder_lock(folder, wait_s)


@contextlib.contextmanager
def _folder_lock(folder, wait_s):
    """POSIX: exclusive `flock` on the folder `folder` itself, waiting up to `wait_s`; yields whether
    the caller may rotate. No file is created.

    Only a lock that stays busy for `wait_s` yields False (the line is appended without
    rotating). A folder that does not open for the lock, or a `flock` that fails for another
    reason (no locks on NFS, `ENOTSUP` on SMB), yields True: the rotation goes on as it did
    before this lock existed, and the log never grows without bound."""
    try:
        fd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        yield True
        return
    try:
        yield _flock_within(fd, wait_s)
    finally:
        os.close(fd)


def _flock_within(fd, wait_s):
    """`flock(fd)` within `wait_s`: False only when it stays busy; True when held or unusable here."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # pyright: ignore[reportOptionalMemberAccess]
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(_FLOCK_POLL_S)
        except OSError:
            return True


def _rotate(path, backups):
    """`getbrolls.log` → `.1` → … → `.<backups>`, dropping the oldest. Renames never follow a link.

    The active file moves first, to a staging name: when it cannot move (on Windows,
    another program has it open), nothing else moves and the line goes on to the
    current file, so a failed rotation never drops a line nor shifts the backups.
    """
    active = Path(path)
    staged = active.with_name(f"{active.name}.rotating")
    try:
        active.replace(staged)  # effect-exempt: rotation of getbrolls.log by its own handler; logging here recurses
    except OSError:
        return
    for index in range(backups - 1, 0, -1):
        with contextlib.suppress(OSError):
            # effect-exempt: rotation of getbrolls.log by its own handler; logging here recurses
            active.with_name(f"{active.name}.{index}").replace(active.with_name(f"{active.name}.{index + 1}"))
    with contextlib.suppress(OSError):
        # effect-exempt: rotation of getbrolls.log by its own handler; logging here recurses
        staged.replace(active.with_name(f"{active.name}.1"))


class _PrivateFileHandler(logging.FileHandler):
    """Private (0600), rotating `getbrolls.log` that never keeps the file open.

    Each record creates the folder if needed, opens the file without following a link,
    appends one line and closes it again: no handle is held between records, so another
    process can rotate the file (Windows refuses to rename an open file) and a rotation
    by another process is followed at the next record instead of writing on into the
    renamed backup. With `private_home` (the installation log, shared by commands that
    take different locks or none), the folder goes through `_private_folder` and the
    size check, the rotation and the append run under `ROTATION_LOCK`; when the lock
    stays busy the line is appended without rotating.

    The project log is not serialized by the project lock: the Storyboard server of `serve`,
    `analysis --action register`, `init` and `migrate` write it without holding that lock.
    On POSIX each line takes a `flock` on the log's folder itself (`brolls/`; no lock file
    is created) around the size check, the rotation and the append, so two writers never
    rotate at the same time nor drop a line; a lock that stays busy for
    `ROTATION_LOCK_WAIT_S` appends without rotating. A folder where `flock` does not work
    (no locks on NFS, `ENOTSUP` on SMB) rotates without the lock, as before it existed.
    Windows has no `flock`: there the project log takes `ROTATION_LOCK`
    (`brolls/.getbrolls.log.lock`, a name no backup `getbrolls.log.<n>` can have) the same
    way the installation log does.
    """

    def __init__(self, path, *, max_bytes, backup_count, private_home=False):
        super().__init__(str(path), encoding="utf-8", delay=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.private_home = private_home

    def _open(self):
        return _open_private(self.baseFilename)

    def _rotation_lock(self, folder):
        """The lock around one line: the folder's `flock` (project log) or `ROTATION_LOCK` (installation)."""
        if not self.private_home and fcntl is not None:
            return _folder_lock(folder, ROTATION_LOCK_WAIT_S)
        # The installation log, and the project log where there is no `flock` (Windows).
        return file_locks.try_lock(folder / ROTATION_LOCK, ROTATION_LOCK_WAIT_S)

    def _full(self, stream, line):
        size = os.fstat(stream.fileno()).st_size
        return self.max_bytes > 0 and size > 0 and size + len(line.encode("utf-8", "replace")) >= self.max_bytes

    def emit(self, record):
        try:
            line = self.format(record) + self.terminator
            folder = Path(self.baseFilename).parent
            if self.private_home:
                _private_folder(folder)
            else:
                # effect-exempt: getbrolls.log handler itself; an op line from inside it would recurse into it
                folder.mkdir(parents=True, exist_ok=True)
            with self._rotation_lock(folder) as may_rotate:
                stream = self._open()
                try:
                    if may_rotate and self.backup_count > 0 and self._full(stream, line):
                        stream.close()
                        _rotate(self.baseFilename, self.backup_count)
                        stream = self._open()
                    stream.write(line)
                finally:
                    stream.close()
        except Exception:  # noqa: BLE001 - logging must never break a command
            self.handleError(record)


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
        message = labels.bounded(message)
        with contextlib.suppress(Exception):
            message = redaction.scrub_home(labels.text(redaction.redact(message, limit=None)))
            message = labels.outside(message)
        record.msg = message[:MAX_MESSAGE_CHARS]
        record.args = ()
        return True


class _LineFormatter(logging.Formatter):
    def format(self, record):
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        stamp = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
        return f"{stamp} level={record.levelname} logger={record.name} {record.getMessage()}"


def _format_value(value):
    """One log field's value: `-` for None, redacted, quoted when it needs to be,
    capped at 500 chars.

    Redaction runs BEFORE the length cut: a secret that starts before char 500 and
    ends after it must not survive as a visible prefix once the tail is cut off.
    """
    if value is None:
        return "-"
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - logging must never break a command
        text = "<unrepr>"
    # Redact the whole value (secrets, then folders), then cut: never the other way around.
    text = labels.bounded(text)
    with contextlib.suppress(Exception):  # logging must never break a command
        text = labels.outside(redaction.scrub_home(labels.text(redaction.redact(text, limit=None))))
    if len(text) > _MAX_VALUE_CHARS:
        text = text[:_MAX_VALUE_CHARS]
    if text == "" or _UNQUOTED_SAFE.search(text):
        # `text` is a `str` here, which `json.dumps` always encodes (lone surrogates too).
        text = json.dumps(text, ensure_ascii=False)
    return text


def render_fields(event_name, fields):
    """Deterministic `event=<name> k=v ...` line; insertion order of `fields` is preserved."""
    parts = [f"event={_format_value(event_name)}"]
    for key, value in fields.items():
        parts.append(f"{key}={_format_value(value)}")
    return " ".join(parts)


def event(logger, level, event_name, **fields):
    """Log one `event=<event_name> k=v ...` line at `level`; never raises.

    Every line that measures a time carries `duration_ms` at the end: a line with `ms` (the old
    name, kept as an alias for this release only) and one with `waited_ms` (how long a lock was
    waited for: `lock_busy`, `library_lock`) get it with the same value. `wait_s` (a planned
    pause before a retry) is not a measured time and stays as it is.
    """
    try:
        if not logger.isEnabledFor(level):
            return
        if "duration_ms" not in fields:
            for alias in ("ms", "waited_ms"):
                if alias in fields:
                    fields["duration_ms"] = fields[alias]
                    break
        logger.log(level, render_fields(event_name, fields))
    except Exception:  # noqa: BLE001, S110 - logging must never break a command
        pass


def outcome_for(exc):
    """`refused` when `exc` is the operation saying no (a `ValueError`, or a refusal of the core that
    declares its own `error_code`); `error` for an I/O error, a failed external tool, Ctrl-C or a bug.
    An exception class may say its own `outcome` (`error` or `refused`), which wins."""
    own = getattr(type(exc), "outcome", None)
    if own in ("error", "refused"):
        return own
    if isinstance(exc, ValueError) or getattr(type(exc), "error_code", None) in DOMAIN_CODES:
        return "refused"
    return "error"


def error_code(exc):
    """The `error_code` the CLI gives `exc` if it reaches the JSON error envelope as it is; never raises.

    `error_codes.error_code_for`, with the override of `refused_as` for a `ValueError` (the
    caller turns it into another error or handles it); `INTERRUPTED` for Ctrl-C. An external
    tool that failed or timed out (`subprocess.SubprocessError`) is an operation error,
    `INVALID_DATA`, the code its callers give it once they turn it into a `ProviderError`; a
    refusal of the core that is not a `ValueError` says its own code in `error_code` (the
    `UnsafeFileError` of `sdk.safe_copy`, the `CollectError` of `instagram_pairs`…). Anything
    else is what `runtime.audited` treats as a bug: `INTERNAL_ERROR`. The same code `refusal`,
    `timed` and the `event=op` lines log.
    """
    try:
        if isinstance(exc, KeyboardInterrupt):
            return "INTERRUPTED"
        if isinstance(exc, subprocess.SubprocessError):
            return "INVALID_DATA"
        own = getattr(type(exc), "error_code", None)
        if own in DOMAIN_CODES:
            return own
        if not isinstance(exc, (ValueError, OSError, OverflowError)):
            return "INTERNAL_ERROR"
        return _with_override(exc, error_codes.error_code_for(exc))
    except Exception:  # noqa: BLE001 - logging must never break a command
        return None


def _with_override(exc, code):
    """`code`, ou o que `refused_as` manda para um `ValueError`. O `Refusal` fica de fora do `USAGE_ERROR`:
    `runtime.usage_errors` o deixa passar, e ele sai como `INVALID_DATA`, não como erro de uso."""
    override = _REFUSAL_CODE.get()
    if override is _KEEP or not isinstance(exc, ValueError):
        return code
    if override == "USAGE_ERROR" and isinstance(exc, Refusal):
        return code
    return override


def refusal(logger, event_name, exc, **fields):
    """Log one WARNING `event_name` for a refusal and return `exc`, ready to be raised.

    `error_code` is the code the CLI will give `exc` (`error_codes.error_code_for`), so the
    log line and the JSON error envelope never disagree. A caller that turns the error
    into another one, or handles it and goes on, says so with `refused_as` around the
    call. Pass a stable, low-cardinality `reason` and ids or counts in `fields`; never
    the message of `exc`, which can quote a path, a name or something the person typed.
    Never raises.
    """
    code = None
    with contextlib.suppress(Exception):
        code = _with_override(exc, error_codes.error_code_for(exc))
    event(logger, logging.WARNING, event_name, **fields, error_code=code)
    return exc


@contextlib.contextmanager
def refused_as(code):
    """Inside, `refusal` of a `ValueError` logs `code` as its `error_code`, not the exception's own.

    For a caller that decides what the CLI makes of every `ValueError` raised in the block:
    `"USAGE_ERROR"` when it re-raises them as `UsageError` (`runtime.usage_errors`), `None`
    when it catches them and the command goes on (`handled`), so no line names a code the
    CLI never gives. An `OSError` keeps its own code. Nested blocks: the innermost wins.
    """
    token = _REFUSAL_CODE.set(code)
    try:
        yield
    finally:
        _REFUSAL_CODE.reset(token)


def handled():
    """`refused_as(None)`: the caller catches every `ValueError` raised inside and goes on."""
    return refused_as(None)


# What `timed` writes itself: a caller's field with one of these names is refused.
TIMED_FIELDS = frozenset({"ms", "duration_ms", "outcome", "error", "error_code"})


@contextlib.contextmanager
def timed(logger, event_name, **fields):
    """Log one INFO line on success and one WARNING line on failure, both timed.

    Success: `ms=<duration>`, the caller's fields, `outcome=ok` and `duration_ms`.
    Failure: `ms`, `error=<ExceptionClass>`, the caller's fields, `outcome` (`refused`
    for a `ValueError`, `error` otherwise), `error_code` (`error_code`, the code of the
    CLI envelope) and `duration_ms`. `ms` is the alias of `duration_ms` for this release.
    Re-raises the original exception unchanged. Negligible overhead when the logger's
    effective level filters both. A field it writes itself (`TIMED_FIELDS`) is refused with a
    `ValueError` before the block runs: two values for one key would be a `TypeError` later."""
    clash = sorted(TIMED_FIELDS & set(fields))
    if clash:
        raise ValueError(f"logs.timed grava {', '.join(clash)} por conta própria; use outro nome de campo.")
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        event(
            logger,
            logging.WARNING,
            event_name,
            ms=elapsed,
            error=type(exc).__name__,
            **fields,
            outcome=outcome_for(exc),
            error_code=error_code(exc),
            duration_ms=elapsed,
        )
        raise
    else:
        elapsed = round((time.monotonic() - started) * 1000)
        event(logger, logging.INFO, event_name, ms=elapsed, **fields, outcome="ok", duration_ms=elapsed)


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
    _root.setLevel(_SILENT)
    _state["signature"] = None


def set_command(name):
    """The CLI command this process runs: the `command` of a line no `runtime.audited` covers."""
    _state["command"] = name


def current_command():
    """What `set_command` set (None outside the CLI, or after `shutdown`); never raises."""
    return _state.get("command")


def shutdown():
    """Flush and close every handler at the end of a command. Never raises.

    The file is never held open between lines anyway; this detaches the handlers so
    nothing writes after the command returned. The next `configure()` re-attaches.
    The file chosen for the command stays known to `log_path`.
    """
    with contextlib.suppress(Exception):
        _degrade()
    _state["command"] = None


def configure(project, *, read_only, installation=False):
    """Set up (once) the `getbrolls` logger hierarchy for this process.

    The file goes to the project (`brolls/getbrolls.log`) when there is one and the
    command is not `read_only`. `installation=True` says the command changes the
    installation instead (see `runtime.writes_installation`): with no project file in
    use, the file is `$GB_HOME/logs/getbrolls.log`. A command that only reads gets no
    file at all, in either place. The choice is what `log_path` reports.

    Idempotent: calling this twice with the same project/settings/read_only is a
    no-op. Never raises: any failure (bad permissions, a locked rotation file on
    Windows, a full disk) degrades to a NullHandler and the command proceeds, with
    one warning line on stderr saying there is no log; a failure to write one line
    later only drops that line.
    """
    logging.raiseExceptions = False
    try:
        _configure(project, read_only=read_only, installation=installation)
    except Exception as exc:  # noqa: BLE001 - logging setup must never break a command; degrade to a NullHandler
        _degrade()
        _say_setup_failed(exc)


def _say_setup_failed(exc):
    """One line on stderr (outside the JSON on stdout): the command runs, but with no getbrolls.log.

    Only the exception class: its message can carry a path. Never raises."""
    with contextlib.suppress(Exception):
        sys.stderr.write(
            f"getbrolls: aviso: não consegui preparar o getbrolls.log ({type(exc).__name__}); "
            "o comando segue sem registro de auditoria.\n"
        )


def enable_project(project):
    """Turn the project file on for the rest of this command, now that its tree exists.

    `init` and `migrate` of a folder with no `brolls/` start with the file off, so the
    log cannot create `brolls/` before the command has validated everything. Once they
    have created the tree they call this, and their own events and the closing
    `command_end` line land in it. Same level and stderr settings as `configure`;
    a no-op when the file is already on. Never raises.
    """
    configure(project, read_only=False)


def _add_file_handler(path, level, redacting, *, private_home=False):
    """Attach the private rotating handler for `path` to the root logger.

    Nothing is created here: the folder and the file come with the first line (see
    `_PrivateFileHandler`), so a command that logs nothing leaves nothing behind.
    """
    handler = _PrivateFileHandler(path, max_bytes=MAX_BYTES, backup_count=BACKUP_COUNT, private_home=private_home)
    handler.setLevel(level)
    handler.setFormatter(_LineFormatter())
    handler.addFilter(redacting)
    _root.addHandler(handler)


def _configure(project, *, read_only, installation):
    _state["target"] = None
    _state["project"] = None
    raw_level = (os.environ.get("GB_LOG_LEVEL") or "INFO").strip().upper()
    stderr_on = (os.environ.get("GB_LOG_STDERR") or "").strip().lower() in _TRUTHY
    project_path = Path(project).resolve() if project else None
    _state["project"] = project_path
    project_file = project_path / "brolls" / LOG_FILENAME if project_path is not None and not read_only else None
    home_file = home_log_path() if installation and project_file is None else None
    # The same choice, whatever the level, is what `log_path` (the error's `app_log`) reports.
    _state["target"] = project_file or home_file

    signature = (
        str(project_path) if project_path else None,
        raw_level,
        stderr_on,
        bool(read_only),
        str(home_file) if home_file else None,
    )
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
    if level is not None and project_file is not None:
        _add_file_handler(project_file, level, redacting)
        added = True
    elif level is not None and home_file is not None:
        # Private folder: this log records what the person did to the whole installation.
        _add_file_handler(home_file, level, redacting, private_home=True)
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
        _root.setLevel(_SILENT)

    _state["signature"] = signature


def project_root():
    """The project folder the last `configure` was given (resolved), or None; never raises."""
    return _state.get("project")


def home_log_path():
    """`$GB_HOME/logs/getbrolls.log`: the installation-level app log, whether or not it exists yet."""
    return _paths.gb_home() / HOME_LOG_DIR / LOG_FILENAME


def log_path(project):
    """Absolute path of the app log to point at in an error (`app_log`), or None when it doesn't exist.

    It is the file `configure` chose for the command, even after `shutdown`: the
    project's `brolls/getbrolls.log`, or the installation's `$GB_HOME/logs/getbrolls.log`
    for a command that changes the installation, `--project` or not. A command that
    writes no file (or a process that never configured) points at the project's log,
    or with no project at the installation's. Never raises: a file it cannot check
    (no permission), a link, or an installation log in a refused `logs/` count as
    missing.
    """
    try:
        project_file = Path(project).resolve() / "brolls" / LOG_FILENAME if project else None
        home_file = home_log_path()
    except (RuntimeError, OSError, ValueError):  # no HOME to resolve `~/.getbrolls` from, or a broken path
        return None
    chosen = _state.get("target")
    if chosen is None or chosen not in (project_file, home_file):
        # Nothing chosen for this project and home (a choice left by an earlier command
        # elsewhere in this process does not count).
        chosen = project_file or home_file
    return chosen if _is_log_file(chosen, private_home=chosen == home_file) else None


def _is_log_file(path, *, private_home):
    """A regular file (never through a link), in a folder `_PrivateFileHandler` would write to; never raises."""
    try:
        if private_home:
            folder = os.lstat(path.parent)
            if not stat.S_ISDIR(folder.st_mode) or (os.name != "nt" and folder.st_uid != os.geteuid()):
                return False
        return stat.S_ISREG(os.lstat(path).st_mode)
    # lenient: documented never-raise check: a log it cannot see is not written (absent)
    except (OSError, ValueError):
        return False
