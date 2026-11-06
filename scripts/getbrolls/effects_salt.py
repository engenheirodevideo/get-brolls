"""The key of the HMAC that hashes names in the log: one random salt per installation.

It is created the first time a line needs it, repaired when malformed, and never logged. Everything here runs
while a log line is being redacted, so it uses the raw `os` calls and the unlogged `make_private`: an `event=op`
line from inside would recurse into the salt. The file is read by `_salt_read`; `effects` hashes with `_salt`
and `message_key`, and keeps the `doctor` probes (`salt_state`)."""

# pylint: disable=broad-exception-caught
# The salt never breaks a command: an unusable one is warned about once and the process keeps a key of its own.

import contextlib
import logging
import os
import secrets
import time
import uuid

from . import _paths, _salt_read, file_locks, logs, private_files

# The same logger `logs.get("effects")` gives, taken directly: it keeps the name `getbrolls.effects`.
_logger = logging.getLogger("getbrolls.effects")

# The key of the HMAC that hashes names: random, one per installation, 0600 in `$GB_HOME`,
# created the first time a line needs it and never logged. Without it a dictionary of client
# names or titles would reverse a plain sha256 of 32 bits.
SALT_FILE = _salt_read.SALT_FILE
_SHARING_TRIES = _salt_read.SHARING_TRIES
_while_shared = _salt_read.while_shared
_inspect_salt = _salt_read.inspect_salt
# How long `_settle_salt` rereads a salt another process is repairing before it uses one of its own (about: a pass
# that started just before the end finishes), and how far apart the rereads are.
_SALT_SETTLE_S = 5.0
_SALT_SETTLE_WAIT_S = 0.05
# Lock file of the salt repair where there is no `flock` (Windows); its name never matches `.log-salt*`.
SALT_REPAIR_LOCK = ".getbrolls-salt.lock"
# How long a repairer waits for another one that holds the folder (then POSIX repairs on its own; Windows reads again).
_SALT_LOCK_WAIT_S = 2.0
# A `.tmp` of a salt whose unlink failed is swept by the next repair once it is this old.
_SALT_TEMP_STALE_S = 60.0
_salts: dict[str, bytes] = {}
# Keys of this process only, for messages, where no salt was there yet (`message_key`): never written.
_message_keys: dict[str, bytes] = {}
# `($GB_HOME, reason)`s this process already warned about (one WARNING each).
_salt_warned: set[tuple[str, str]] = set()


def _new_salt_file(path):
    """A temporary file next to `path` with a fresh salt, 0600; returns its path."""
    temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    private_files.make_private(temp, record=False)  # silent: a line here would recurse into the salt (`_set_aside`)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as stream:
        stream.write(secrets.token_hex(32) + "\n")
    return temp


def _create_in_place(path):
    """Where there are no hardlinks (exFAT, some SMB shares): `O_CREAT|O_EXCL` on the final name,
    so only one process creates it; a reader that comes in the middle reads again."""
    with contextlib.suppress(FileExistsError):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        private_files.make_private(path, record=False)  # silent, as in `_new_salt_file`
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as stream:
            stream.write(secrets.token_hex(32) + "\n")


def _create_salt(path, *, swap=False, wait_s=_SALT_LOCK_WAIT_S):
    """Write a new salt to `path` (0600) and return `(state, salt)` of what ended up there.

    A new file is a temporary one linked into place, so nobody reads half a salt, and the first
    process to link wins for everybody; with no hardlinks, `_create_in_place`. A malformed file
    (`swap`) is repaired under a `flock` on its folder (POSIX; `SALT_REPAIR_LOCK` without it), waited
    for up to `wait_s`: the first repairer moves it aside (`_set_aside`) and creates a new one by that
    same first-wins path, and every repairer then rereads the one file that persisted, never a salt of
    its own. A lock not taken (still busy after the wait, or on Windows a lock file that does not open or lock):
    POSIX repairs anyway; Windows (a rename moves whatever is at `path`) leaves the file alone and returns
    `("malformed", None)` for the caller to read again, so with a lock file that cannot be used it never repairs."""
    existed = path.parent.is_dir()  # lenient: only a folder made here gets the ACL, not a chosen GB_HOME
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not existed:
        private_files.make_private(path.parent, record=False)  # silent too
    if not swap:
        return _link_new_salt(path)
    # POSIX: `flock` on `$GB_HOME` itself; without it (Windows), a lock file of its own there, the way
    # the project log's rotation does it. A lock still busy after the wait: POSIX repairs anyway; on Windows
    # only the holder may move the file, and a repairer without the lock reads again.
    if logs.fcntl is not None:
        lock = logs.folder_lock(path.parent, wait_s)
    else:
        lock = file_locks.try_lock(path.parent / SALT_REPAIR_LOCK, wait_s)
    with lock as held:
        state, found = _inspect_salt(path)
        if state != "malformed":
            # Another repairer already put a good salt in place (or it is gone): use that one.
            return (state, found) if state == "ok" else _link_new_salt(path)
        if not held and logs.fcntl is None:
            # Windows, lock not taken: another repairer holds it (or it does not open). A rename moves whatever
            # is at `path`, so only the holder may move it aside; this one reads again (`_settle_salt`).
            return state, found
        _set_aside(path)
        return _link_new_salt(path)


def _link_new_salt(path):
    """Link a fresh temporary salt to `path`, first wins; `(state, salt)` of what is there. A sharing or
    lock violation goes up (`_settle_salt` reads again); any other refusal to link falls back to creating
    the file in place."""
    temp = _new_salt_file(path)
    try:
        try:
            path.hardlink_to(temp)
        except FileExistsError:
            pass
        except OSError as exc:
            if _salt_read.patient(exc):
                raise
            _create_in_place(path)
    finally:
        with contextlib.suppress(OSError):
            _while_shared(temp.unlink)
    return _inspect_salt(path)


def _set_aside(path):
    """Move the malformed salt out of the way under a name of its own, then drop it (and old leftovers).

    Another repairer that moved it first leaves nothing to move (`FileNotFoundError`, ignored). The
    file is removed with the raw `unlink`, not `effects.unlink`, on purpose: this runs while a line
    is being redacted, and logging it would recurse into the salt it is replacing.

    A rename moves whatever is at `path`, so the file is inspected again before EVERY attempt, not once
    before a retry loop: a repairer that won while this one waited out a sharing violation (or missed the
    lock) has put a good salt there, and that one is left alone (the caller then reads it). Only the
    instructions between the last inspect and the rename remain open, which no portable call closes."""
    aside = path.with_name(f"{path.name}.bad-{uuid.uuid4().hex}")
    attempt = 1
    while True:  # every pass returns, breaks or raises: at most `_SHARING_TRIES` renames
        if _inspect_salt(path, tries=3)[0] != "malformed":
            return
        try:
            path.replace(aside)
            break
        except FileNotFoundError:
            break
        except PermissionError as exc:
            if not _salt_read.sharing(exc) or attempt >= _SHARING_TRIES:
                raise
            attempt += 1
            time.sleep(_salt_read.SHARING_WAIT_S)
    # This one, any left by a repairer that died midway and any `.tmp` whose unlink failed (best effort).
    for leftover in path.parent.glob(f"{path.name}.*"):
        with contextlib.suppress(OSError):
            if ".bad-" in leftover.name or (
                leftover.name.endswith(".tmp") and time.time() - leftover.stat().st_mtime > _SALT_TEMP_STALE_S
            ):
                leftover.unlink()  # effect-exempt: leftover of the salt repair; logging it would recurse into the salt


def _warn_salt(key, reason, last=("", None)):
    """One WARNING per process, `$GB_HOME` and reason: the hashes of this process will not match other runs.
    `last` is `(what failed, errno)` of the failure that ended it, when there was one."""
    if (key, reason) in _salt_warned:
        return
    _salt_warned.add((key, reason))
    fields = {"last": last[0]} if last[0] else {}
    if last[1] is not None:
        fields["errno"] = last[1]
    logs.event(_logger, logging.WARNING, "log_salt_unavailable", reason=reason, **fields)


_AGAIN = "again"  # a pass settled nothing: read again
_NOTHING = ("", None)  # `last` of a pass that failed at nothing
# What a failure that repeats ends as: the true reason, not `busy` (which is for a wait that outlasts the budget).
_GIVES_UP_AS = {"create": "readonly", "unreadable": "unreadable"}


def _settle_create(path, state, wait_s):
    """One creation (`missing`) or repair (`malformed`) at `path`: `(salt, reason, last)`, `reason` being
    `_AGAIN` when a sharing or lock violation (`busy`) or a refused access (`create`) may be passing."""
    try:
        state, found = _create_salt(path, swap=state == "malformed", wait_s=wait_s)
    except OSError as exc:
        last = ("busy" if _salt_read.patient(exc) else "create", exc.errno)
        return None, (_AGAIN if _salt_read.sharing(exc) else "readonly"), last
    if state == "ok":
        return found, "ok", _NOTHING
    if state in ("link", "missing"):
        return None, "nolink" if state == "missing" else "link", _NOTHING
    return None, _AGAIN, (state, None)  # unreadable or malformed again: read what persisted


def _settle_unreadable(path):
    """`(salt, reason, last)` of a salt that read as `unreadable`: reread when it is passing, else the true reason."""
    kind, number = _salt_read.why_unreadable(path)
    if kind == "link":
        return None, "link", _NOTHING
    if kind == "denied":
        return None, "unreadable", ("unreadable", number)
    return None, _AGAIN, ("busy" if kind == "busy" else "unreadable", number)


def _settle_once(key, path, wait_s):
    """One pass of `_settle_salt`: `(salt, reason, last)`."""
    state, found = _inspect_salt(path)
    if state == "ok":
        return found, "ok", _NOTHING
    if state == "link":
        return None, "link", _NOTHING
    if state == "unreadable":
        return _settle_unreadable(path)
    if state == "malformed":
        _warn_salt(key, "malformed")
    return _settle_create(path, state, wait_s)  # `missing` or `malformed`


def _settle_salt(path):
    """`(salt, reason, last)`: the salt that persisted at `path`, or `(None, reason, last)` when this process
    must use one of its own: `link`, `readonly` (nothing can be written), `unreadable` (it cannot be read),
    `nolink` or `busy` (the budget `_SALT_SETTLE_S` ran out); `last` says what failed last.

    A transient failure (a sharing or lock violation, a file caught mid-rename, a repair lost to another
    process) is never a reason to invent a salt: the file is read again, so every repairer ends with the same
    one, for as long as the budget lasts (the deadline is checked before each pass, which waits for the lock
    no longer than what is left). A failure that is not a wait on another process (`last` other than `busy`)
    gives up as its true reason when it repeats `_SHARING_TRIES` passes in a row."""
    key = str(path)
    deadline = time.monotonic() + _SALT_SETTLE_S
    last, same = _NOTHING, 0
    while (left := deadline - time.monotonic()) > 0:
        found, reason, now = _settle_once(key, path, min(_SALT_LOCK_WAIT_S, left))
        if reason != _AGAIN:
            return found, reason, now
        same = same + 1 if now == last else 1
        last = now
        if now[0] != "busy" and same >= _SHARING_TRIES:
            return None, _GIVES_UP_AS.get(now[0], "busy"), now
        time.sleep(_SALT_SETTLE_WAIT_S)
    return None, "busy", last


def _salt():
    """This installation's salt (cached per `$GB_HOME`).

    A missing salt is created; a malformed one is warned about and replaced. When the file is a
    link (never followed), the folder is read-only, nothing could be written or read, or the file stayed
    busy for the whole settle budget, this process uses a salt of its own (a name is never hashed without
    a secret) and warns, once for each of `link`, `readonly`, `unreadable`, `nolink`, `busy` and
    `malformed`."""
    try:
        path = _paths.gb_home() / SALT_FILE
    except (RuntimeError, OSError, ValueError):
        path = None
    key = str(path)
    if key in _salts:
        return _salts[key]
    found, reason, last = None, "readonly", _NOTHING
    if path is not None:
        found, reason, last = _settle_salt(path)
    if found is None:
        _warn_salt(key, reason, last)
    _salts[key] = found or secrets.token_bytes(32)
    return _salts[key]


def message_key():
    """The key of the hashes in a message, a log line's outside path or the diagnostics: this installation's
    salt when it is already there (read, never created); without it, a key of this process only, never
    written and never warned about. Naming a file in a message writes nothing (`status` on a locked folder)."""
    try:
        path = _paths.gb_home() / SALT_FILE
    except (RuntimeError, OSError, ValueError):
        path = None
    key = str(path)
    if key not in _salts and key not in _message_keys:
        state, found = _inspect_salt(path) if path is not None else ("missing", None)
        if state == "ok" and found is not None:
            _salts[key] = found
        else:
            _message_keys[key] = secrets.token_bytes(32)
    return _salts.get(key) or _message_keys[key]
