"""Owner-only files and folders, and the delete that works on read-only trees.

`make_private` is the Windows counterpart of `chmod 0600/0700` (an ACL set through `icacls`); off Windows it
does nothing, the `chmod` already did it. `force_rmtree` removes a tree that has read-only entries (a git
clone on Windows) and never raises. Both log their work through `effects.op`, imported late: `effects`
imports this module to make the files and folders it creates private."""

# pylint: disable=cyclic-import,broad-exception-caught
# `effects` <-> `private_files`: `effects` imports this module at the top; the `effects.op` lines of
# `make_private` and `force_rmtree` need `effects` back, so those imports sit inside the functions.
# `broad-exception-caught`: `force_rmtree` never raises; what is left is reported by the op line.

import contextlib
import errno
import os
import shutil
import stat
import subprocess
import sys
import types
from pathlib import Path


def _is_windows():
    """Read at each call (not fixed at import), so a test can switch the Windows branch on."""
    return os.name == "nt"


# The principals CPython itself gives a folder made with `os.mkdir(path, 0o700)` on Windows since the fix of
# CVE-2024-4030, as well-known SIDs: OWNER RIGHTS (whoever owns the object: the account that created it),
# SYSTEM and the Administrators group. A SID is never looked up, so it cannot depend on the environment
# (`USERNAME`, `USERDOMAIN`) nor resolve to another account and lock the owner out.
_PRINCIPALS = ("*S-1-3-4", "*S-1-5-18", "*S-1-5-32-544")
_ICACLS = "icacls"  # the name in the log line; the process is `_icacls_exe()`
_ICACLS_TIMEOUT_S = 30
# `IO_REPARSE_TAG_SYMLINK` and `IO_REPARSE_TAG_MOUNT_POINT` (a junction); `stat` only has them on Windows.
_LINK_TAGS = (0xA000000C, 0xA0000003)


def _run_bound_at_import(real=None):
    """`subprocess.run` itself, with the globals it has now, at import (`Popen` above all).

    A test that mocks `subprocess.run` or `subprocess.Popen` (and counts calls, or checks every argv)
    never sees the `icacls` that a private file starts on Windows: this copy keeps the real ones. A `run`
    that is not a plain function (or that reads variables of an enclosing scope) is used as it is."""
    if real is None:
        # Copied at import so a test mocking subprocess never sees this child. `make_private` logs its op line,
        # except with record=False (the log handler and the salt): that launch stays unlogged, a line would recurse.
        # effect-exempt: make_private logs this child, except record=False (log, salt), unlogged as it would recurse
        real = subprocess.run
    if not isinstance(real, types.FunctionType) or real.__code__.co_freevars:
        return real
    run = types.FunctionType(real.__code__, dict(real.__globals__), real.__name__, real.__defaults__, real.__closure__)
    run.__kwdefaults__ = real.__kwdefaults__
    return run


_run_icacls = _run_bound_at_import()


def _icacls_exe():
    """`%SystemRoot%\\System32\\icacls.exe`, by its full path. A bare `icacls` is looked for in the current
    folder before System32 (a project folder could ship one), and `shutil.which` does the same on Windows."""
    path = Path(os.environ.get("SYSTEMROOT") or r"C:\Windows", "System32", "icacls.exe")
    # lenient: a missing tool is the logged failure of make_private, never a crash; nothing else depends on it
    if not path.is_file():
        raise OSError(errno.ENOENT, "icacls.exe not found in System32")
    return str(path)


def _owner_only_argv(path, folder):
    """`icacls <path> /grant:r <SID>:(F)… /inheritance:r /L`; a folder gets `(OI)(CI)(F)`, so what is made in
    it later is private too. The grant comes before `/inheritance:r`: should the grant fail, the object is
    never left with an empty ACL. `/L` acts on a link itself, never on what it points at."""
    rights = "(OI)(CI)(F)" if folder else "(F)"
    grants = [f"{principal}:{rights}" for principal in _PRINCIPALS]
    return [_icacls_exe(), os.fspath(path), "/grant:r", *grants, "/inheritance:r", "/L"]


def _owner_only_acl(name, folder):
    """What `icacls /restore` reads: the name (relative to the folder of the object), then the whole DACL in
    SDDL, in UTF-16 LE with no BOM and CRLF, as `icacls /save` writes it. The DACL is protected (`P`) and holds
    exactly the three SIDs of `_PRINCIPALS`, full access (`FA`), inheritable (`OICI`) on a folder."""
    flags = "OICI" if folder else ""
    aces = "".join(f"(A;{flags};FA;;;{principal.removeprefix('*')})" for principal in _PRINCIPALS)
    # `surrogatepass`: an NTFS name may hold a lone surrogate, and the text must name that same object
    return f"{name}\r\nD:PAI{aces}\r\n".encode("utf-16-le", "surrogatepass")


def _scratch_folder():
    """`%LOCALAPPDATA%\\Temp`, the temporary folder of the profile (the account, SYSTEM and Administrators only).

    Never `TEMP`/`TMP`: they may point at a folder other accounts can write (`C:\\Temp`), where one of them could
    rewrite the text `/restore` applies before `icacls` reads it."""
    base = os.environ.get("LOCALAPPDATA")
    folder = Path(base, "Temp") if base else None
    # lenient: no profile temporary folder is the logged failure of the restore step; the grant already ran
    if folder is None or not folder.is_absolute() or not folder.is_dir():
        raise OSError(errno.ENOENT, "no temporary folder in the profile")
    return folder


@contextlib.contextmanager
def _owner_only_steps(path):
    """The two `icacls` runs of `make_private`, one at a time and in order: the grant (`_owner_only_argv`),
    then `icacls <folder> /restore <file> /L`, which writes the DACL of `_owner_only_acl` over the whole ACL.

    The grant leaves in place any other explicit entry (the account that created the object, say) and
    `/restore` drops it, so only the three SIDs remain; it replaces the DACL in one write, never leaving it
    empty. A link (symbolic or junction) is refused before either runs. The file `/restore` reads is made
    only after the grant ran, in `_scratch_folder`, and is removed on the way out; with no such folder the
    restore is a failure (the grant stays)."""
    from . import effects  # local: effects imports this module

    info = os.lstat(path)
    # A symbolic link or a junction; any other reparse point (a OneDrive placeholder) is a file like the rest.
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in _LINK_TAGS:
        raise OSError(errno.EPERM, "not restricting a link")
    folder = stat.S_ISDIR(info.st_mode)
    grant = _owner_only_argv(path, folder)
    path = Path(path)

    with contextlib.ExitStack() as stack:

        def steps():
            yield grant
            acl = stack.enter_context(
                # pylint: disable-next=protected-access  # the scratch file exists for this caller only
                effects._scratch_file(
                    _owner_only_acl(path.name, folder), folder=_scratch_folder(), prefix="gb-acl-", suffix=".txt"
                )
            )
            yield [grant[0], os.fspath(path.parent), "/restore", acl, "/L"]

        yield steps()


def make_private(path, *, record=True):
    """The Windows counterpart of `chmod 0600/0700`: only the owner, SYSTEM and Administrators keep access to
    `path`, nothing is inherited and no other entry stays (the same ACL CPython gives `os.mkdir(path, 0o700)`).

    Called at creation, wherever the code asks for 0600 (file) or 0700 (folder), before any content is
    written. A no-op off Windows, where the `chmod` already did it, with no process and no log line. On
    Windows it runs `icacls` from System32 twice (`_owner_only_steps`), as a list, with no shell, under one
    `proc.run` in the usual `event=op` line (the target is the tool's name, never a path; `exit` is the one of
    the run that failed, or of the last one). A failure (no `icacls`, a link, a timeout, a non-zero exit) is
    that line with `outcome=error` and never raises: the command goes on with the ACL the object has then.

    `record=False` is for the log handler's own folder and file and for the log salt: they are made while a
    line is being written or redacted, and a line about them would recurse. They run the same steps; a
    failure there is silent.
    """
    if not _is_windows():
        return
    if not record:
        with contextlib.suppress(OSError, subprocess.SubprocessError), _owner_only_steps(path) as steps:
            for argv in steps:
                _run_icacls(argv, check=True, capture_output=True, timeout=_ICACLS_TIMEOUT_S)
        return
    from . import effects  # local: effects imports this module

    # The line of `effects.run`, with `_run_icacls` (bound at import) as the process.
    with (
        contextlib.suppress(OSError, subprocess.SubprocessError),
        effects.op("proc.run", [_ICACLS]) as handle,
        _owner_only_steps(path) as steps,
    ):
        returncode = None
        for argv in steps:
            try:
                returncode = _run_icacls(argv, check=True, capture_output=True, timeout=_ICACLS_TIMEOUT_S).returncode
            except subprocess.CalledProcessError as exc:
                handle.set(exit=exc.returncode)
                raise
        handle.set(exit=returncode)


def _ensure_private_file(path):
    """Create `path` (empty) if missing, then force 0600 regardless of umask.

    `touch()`'s own `mode=` is still masked by umask, so an explicit `chmod` is the
    only way to guarantee 0600 both on first creation and on a file left over from
    before this fix (a plain `diagnostics.jsonl` created 0644 by an older run).
    On Windows an empty file (new: the first line is written after this) gets the private ACL.
    """
    # effect-exempt: creates the append-only journal; diagnostics.jsonl is the audit log, the rest log their op
    path.touch(exist_ok=True)
    path.chmod(0o600)
    if path.stat().st_size == 0:
        make_private(path)


def _writable(target):
    """Liga a escrita do dono em `target` (e leitura/entrada, se for pasta); link não é tocado."""
    with contextlib.suppress(OSError):
        mode = target.lstat().st_mode
        if stat.S_ISLNK(mode):
            return
        extra = stat.S_IWUSR | (stat.S_IRUSR | stat.S_IXUSR if stat.S_ISDIR(mode) else 0)
        target.chmod(stat.S_IMODE(mode) | extra)


# effect-exempt(3): only compared against the func that rmtree retries; called inside force_rmtree's logged op
_REMOVE_FUNCS = (os.unlink, os.remove, os.rmdir)


def _removable(target):
    """Libera leitura+execução+escrita do dono numa pasta (0o700) ou escrita numa
    entrada comum (0o600); link não é tocado. Só precisa ser permissivo o
    suficiente para apagar — chamada apenas na retentativa do force_rmtree."""
    with contextlib.suppress(OSError):
        mode = target.lstat().st_mode
        if stat.S_ISLNK(mode):
            return
        target.chmod(0o700 if stat.S_ISDIR(mode) else 0o600)


def force_rmtree(path, *, target=None):
    """Apaga `path` inteiro mesmo com entrada somente-leitura; pasta ausente não é erro.

    O Git para Windows grava objetos (`.git/objects/**`) como somente-leitura, e o
    Windows não apaga arquivo somente-leitura — `rmtree(..., ignore_errors=True)`
    deixava um `.git` dentro do plugin instalado e `.install-*` acumulando. Na
    falha, libera a escrita da entrada (e da pasta de cima, se ela ainda é parte da
    árvore — nunca a pasta que CONTÉM `path`) e tenta de novo.

    Numa pasta sem leitura/execução (uma pasta comum plantada pelo dono do plugin,
    não um objeto git), o walk por fd do `rmtree` chama a retentativa com
    `func=os.open`/`os.scandir`/`os.lstat` (não `os.unlink`/`os.rmdir`/`os.remove`)
    — chamar `func(failed)` sem os argumentos certos levantaria `TypeError`. Nesse
    caso, depois de liberar a permissão, a subárvore é apagada por conta própria
    (`shutil.rmtree` recursivo) em vez de repetir a chamada original.

    Nunca levanta (guarda `Exception`, não só `OSError`): o que sobrar fica para a
    varredura seguinte (`sdk.staging._sweep_stale_staging`)."""
    from . import effects  # local: effects imports this module

    root = Path(path)
    # lenient: force_rmtree never raises; the caller reports what is left
    if not os.path.lexists(root):
        return
    # `target`: what the `fs.rmtree` line names (a temporary folder is named by its parent).
    # The op line is the truth about the delete: when anything is left, the block raises so the line is an
    # `error` (not `ok`), and the outer `suppress` keeps the function from ever raising.
    with contextlib.suppress(Exception), effects.op("fs.rmtree", root if target is None else target):

        def retry(func, failed, _exc):
            failed_path = Path(failed)
            if failed_path != root:
                _writable(failed_path.parent)
            _removable(failed_path)
            if func in _REMOVE_FUNCS:
                with contextlib.suppress(OSError):
                    func(failed)
            else:
                # effect-exempt: onexc callback, run only by the shutil.rmtree inside this fs.rmtree op line
                shutil.rmtree(failed_path, ignore_errors=True)

        failure = None
        try:
            if sys.version_info >= (3, 12):
                # O pylint infere a assinatura de `shutil.rmtree` pela stdlib do
                # interpretador que roda o lint, não pelo `sys.version_info` deste
                # ramo: `onexc` existe a partir do 3.12, mas o CI faz lint em
                # 3.11, que não conhece o kwarg. `**kwargs` monta fora da chamada
                # engana o pylint mas confunde o pyright (perde a distinção entre
                # os overloads de `onexc`/`onerror`), então a chamada fica direta
                # com a supressão na linha.
                shutil.rmtree(root, onexc=retry)  # pylint: disable=unexpected-keyword-arg  # pragma: no cover - 3.12+
            else:
                shutil.rmtree(root, onerror=retry)  # pylint: disable=deprecated-argument
        except Exception as exc:  # noqa: BLE001 - reported below, by what is left
            failure = exc
        # lenient: the check that decides the outcome; it never raises
        if os.path.lexists(root):
            raise failure or OSError(errno.ENOTEMPTY, "left behind after the delete", str(root))
