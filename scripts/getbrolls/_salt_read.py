"""Reading the log salt (`effects` creates and repairs it): the file name, how a salt is told from a partial
or a malformed text, how a Windows sharing violation is waited out, and what a failed read means.

What a Windows sharing violation looks like depends on the call. `os.replace`, `os.link`, `os.unlink` and
`os.stat` raise a `PermissionError` with a `winerror` (5, 32, 33). CPython's `os.open` goes through the C
runtime (`_wopen`), which turns the same violations into a plain `EACCES` and the error carries no
`winerror`: an open refused by another process's handle cannot be told from a real denial, so on Windows
it is read again a few times (`sharing`) and, if it does not pass, taken for a denial.

Nothing here writes: the writes, the repair protocol and the policy of what to do when it fails stay in
`effects.py`, which is where the side-effect guard looks.
"""

import errno
import os
import re
import stat
import time

WINDOWS = os.name == "nt"
SALT_FILE = ".log-salt"
SALT_HEX = re.compile(r"[0-9a-f]{64}")
SALT_PARTIAL = re.compile(r"[0-9a-f]{0,63}")
SALT_READ_TRIES = 25
SALT_READ_WAIT_S = 0.01
# Windows refuses to open or rename a file another process is renaming or reading (a sharing violation, a
# `PermissionError`): the salt's open and set-aside try this many times, this far apart, before giving up.
SHARING_TRIES = 5
SHARING_WAIT_S = 0.02
# ERROR_ACCESS_DENIED (also what a file pending delete answers), ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION.
SHARING_WINERRORS = frozenset({5, 32, 33})
# Of those, the two that always mean "another process has it right now": worth waiting for the whole budget.
# An access denied (5) is retried a handful of times and then taken for what it is, a permission.
PATIENT_WINERRORS = frozenset({32, 33})


def unattributed(exc):
    """Is `exc` the `EACCES` that `os.open` raises on Windows with no `winerror`: a sharing violation, a lock
    violation or a real denial, the call does not say which?"""
    return (
        WINDOWS
        and isinstance(exc, PermissionError)
        and getattr(exc, "winerror", None) is None
        and exc.errno == errno.EACCES
    )


def sharing(exc):
    """Is `exc` a `PermissionError` that may be another process holding the file: a `winerror` of
    `SHARING_WINERRORS`, or on Windows an `unattributed` one?"""
    return isinstance(exc, PermissionError) and (
        getattr(exc, "winerror", None) in SHARING_WINERRORS or unattributed(exc)
    )


def patient(exc):
    """Is `exc` a sharing or lock violation (32, 33): worth reading again for as long as the budget lasts?"""
    return isinstance(exc, PermissionError) and getattr(exc, "winerror", None) in PATIENT_WINERRORS


def while_shared(operation, *args):
    """`operation(*args)`, tried again after `SHARING_WAIT_S` when it raises a Windows sharing violation
    (`sharing`: another process holds or renames the file), up to `SHARING_TRIES` tries; the last error is
    raised as it came. Any other `PermissionError` (a real `EACCES` on POSIX) is raised at once."""
    for _attempt in range(SHARING_TRIES - 1):
        try:
            return operation(*args)
        except PermissionError as exc:
            if not sharing(exc):
                raise
            time.sleep(SHARING_WAIT_S)
    return operation(*args)


def open_salt(path):
    """The descriptor of the salt file for reading, or `None` when it is a link (never followed)."""
    if stat.S_ISLNK(os.lstat(path).st_mode):
        return None
    return os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))


def read_salt_once(path):
    """`(state, text)` of one read of `path`: `text` is what the file holds when `state` is None."""
    try:
        fd = while_shared(open_salt, path)
        if fd is None:
            return "link", None
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return ("link" if exc.errno in (errno.ELOOP, errno.EMLINK) else "unreadable"), None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_uid != os.geteuid()):
            return "link", None
        return None, os.read(fd, 256).decode("ascii", "replace").strip()
    except OSError:
        return "unreadable", None
    finally:
        os.close(fd)


def inspect_salt(path, tries=SALT_READ_TRIES):
    """`(state, salt)` of the salt file `path`, never following a link; never raises.

    `state`: `ok`, `missing`, `link` (a link, not a regular file, or a file of another user),
    `unreadable` or `malformed`. An empty or short hex text is a salt still being written by
    another process (`_create_in_place`): read again a few times before calling it malformed."""
    attempt = 1
    while True:  # every pass returns or reads again: at least one read, at most `tries`
        state, text = read_salt_once(path)
        if state is not None or text is None:
            return state or "unreadable", None
        if SALT_HEX.fullmatch(text):
            return "ok", bytes.fromhex(text)
        if not SALT_PARTIAL.fullmatch(text) or attempt >= tries:
            return "malformed", None
        attempt += 1
        time.sleep(SALT_READ_WAIT_S)


def why_unreadable(path):
    """`(kind, errno)` for a salt that read as `unreadable`, from opening it once more: `busy` (a sharing or
    lock violation, another process has it), `denied` (a permission that no wait changes), `link` (it became
    one) or `again` (it vanished mid-rename, a descriptor or interrupt error, it opens now: it was passing, or
    it was refused the way `os.open` reports a sharing violation on Windows: `unattributed`, read again a few times)."""
    try:
        fd = open_salt(path)
    except PermissionError as exc:
        if patient(exc):
            return "busy", exc.errno
        return ("again" if unattributed(exc) else "denied"), exc.errno
    except OSError as exc:
        return ("link" if exc.errno in (errno.ELOOP, errno.EMLINK) else "again"), exc.errno
    if fd is None:
        return "link", None
    os.close(fd)
    return "again", None
