"""The advisory file locks: one byte on Windows, `flock` elsewhere, and the two ways to hold one.

`exclusive_lock` waits and raises `LockedError` when the lock stays busy; `try_lock` is quiet and yields whether
it got the lock (the installation log takes it around every line, so it never logs). A busy lock is reported
by `lock_busy`, which writes the `lock_busy` line through `logs`, imported late: `logs` imports this module."""

# pylint: disable=import-error,cyclic-import,broad-exception-caught
# `import-error`: `fcntl`/`msvcrt` are imported by platform inside `_acquire_lock`/`_release_lock`.
# `cyclic-import`: `logs` <-> `file_locks`; the `logs` import in `lock_busy` sits inside the function.
# `broad-exception-caught`: logging a busy lock must never break the command.

import contextlib
import errno
import os
import time
from pathlib import Path

from . import labels
from .errors import LockedError


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
            windows.locking(stream.fileno(), windows.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]
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
        windows.locking(stream.fileno(), windows.LK_UNLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]
        return
    import fcntl

    fcntl.flock(stream, fcntl.LOCK_UN)


_LOCK_POLL_S = 0.05


_LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)


def _open_lock_file(path):
    refusal = ValueError(f"{path.name} em {labels.shown(path.parent)} é um link; apague o link e repita o comando.")
    # lenient: link refusal where O_NOFOLLOW is missing; the open below raises on a locked folder
    if not getattr(os, "O_NOFOLLOW", 0) and path.is_symlink():
        raise refusal
    try:
        # effect-exempt: lock file of exclusive_lock/try_lock; the log takes try_lock per line, logging here recurses
        fd = os.open(path, _LOCK_FLAGS, 0o644)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise refusal from None
        raise
    # effect-exempt: lock file of exclusive_lock/try_lock; the log takes try_lock per line, logging here recurses
    return os.fdopen(fd, "a+", encoding="utf-8", newline="\n")


def lock_busy(name, started, error):
    """Registra `lock_busy` (WARNING, com o `error_code` do erro) e devolve `error` para levantar.

    Diz qual trava estava ocupada e quanto se esperou por ela: só o nome do arquivo de trava
    (`.plugins.lock`, `.command.lock`…), nunca o caminho. `started` é o `time.monotonic()` de
    quando a espera começou. O log nunca derruba o comando.
    """
    try:
        from . import logs  # local: logs imports this module

        waited_ms = round((time.monotonic() - started) * 1000)
        logs.refusal(logs.get("runtime"), "lock_busy", error, lock=name, waited_ms=waited_ms)
    except Exception:  # noqa: BLE001, S110 - logging must never break a command
        pass
    return error


@contextlib.contextmanager
def exclusive_lock(path, busy_message, wait_s=0.0):
    """Trava exclusiva no arquivo `path` (criado se faltar); a pasta tem que existir.

    O arquivo é aberto sem seguir link (`O_NOFOLLOW`; onde não existe, um `lstat` antes):
    uma trava trocada por link nunca cria nem trava um arquivo fora do lugar.
    Tenta de novo por até `wait_s` segundos; depois, `LockedError(busy_message)` (código `LOCKED`).
    """
    with _open_lock_file(Path(path)) as lock:
        started = time.monotonic()
        deadline = started + wait_s
        while True:
            try:
                _acquire_lock(lock)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise lock_busy(Path(path).name, started, LockedError(busy_message)) from None
                time.sleep(_LOCK_POLL_S)
        try:
            yield
        finally:
            _release_lock(lock)


_QUIET_LOCK_POLL_S = 0.005


@contextlib.contextmanager
def try_lock(path, wait_s):
    """Trava exclusiva em `path`, como `exclusive_lock`, mas quieta: rende `False` em vez de levantar.

    `False` quando a trava segue ocupada depois de `wait_s` segundos ou não abre (link no
    lugar, pasta sem permissão); `True` com ela tomada. Nunca loga: o próprio log do app a
    toma em volta de cada linha do log da instalação, e logar aqui seria recursão.
    """
    try:
        stream = _open_lock_file(Path(path))
    except (OSError, ValueError):
        stream = None
    if stream is None:
        yield False
        return
    with stream:
        held = _acquire_within(stream, wait_s)
        try:
            yield held
        finally:
            if held:
                with contextlib.suppress(OSError):
                    _release_lock(stream)


def _acquire_within(stream, wait_s):
    deadline = time.monotonic() + wait_s
    while True:
        try:
            _acquire_lock(stream)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(_QUIET_LOCK_POLL_S)
        except OSError:
            return False
