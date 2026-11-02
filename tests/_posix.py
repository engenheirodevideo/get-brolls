"""POSIX-only names that the serve tests simulate on every OS.

Windows' `os` has no `WNOHANG` and its `signal` has no `SIGKILL`: a test that simulates
the POSIX path reads them through `posix_names()`, which puts them in place for the block
(with the real values where they exist). `without_posix_names()` does the opposite, as on
Windows, to prove the Windows path never reaches them.
"""

import contextlib
import os
import signal
from unittest import mock

WNOHANG = getattr(os, "WNOHANG", 1)
SIGKILL = getattr(signal, "SIGKILL", 9)
_NAMES = ((os, "WNOHANG", WNOHANG), (signal, "SIGKILL", SIGKILL))


@contextlib.contextmanager
def posix_names():
    """`os.WNOHANG` and `signal.SIGKILL` exist for the block, on any OS."""
    with contextlib.ExitStack() as stack:
        for module, name, value in _NAMES:
            stack.enter_context(mock.patch.object(module, name, value, create=True))
        yield


@contextlib.contextmanager
def without_posix_names():
    """`os.WNOHANG` and `signal.SIGKILL` are gone for the block, as on Windows."""
    saved = [(module, name, getattr(module, name)) for module, name, _ in _NAMES if hasattr(module, name)]
    for module, name, _value in saved:
        delattr(module, name)
    try:
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)


@contextlib.contextmanager
def without_nofollow():
    """`os.O_NOFOLLOW` is gone for the block (it never existed on Windows); where it is missing already, nothing
    changes."""
    saved = getattr(os, "O_NOFOLLOW", None)
    if saved is not None:
        delattr(os, "O_NOFOLLOW")
    try:
        yield
    finally:
        if saved is not None:
            os.O_NOFOLLOW = saved


@contextlib.contextmanager
def open_follows_links():
    """`os.open` follows a link at the last name for the block, as `CreateFile` does on Windows.

    There `O_NOFOLLOW` is missing and `O_EXCL` does not stop a dangling link, so a link planted at the
    target of a write is written through. A POSIX `O_EXCL` refuses any link on its own; this keeps the
    path a precheck has to cover."""
    real_open = os.open

    def follow(path, flags, mode=0o777, **kwargs):
        if "dir_fd" in kwargs:
            return real_open(path, flags, mode, **kwargs)
        return real_open(os.path.realpath(path), flags, mode)

    with mock.patch.object(os, "open", follow):
        yield
