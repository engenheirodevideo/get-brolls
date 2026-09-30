"""Varredura de `entrega/`: apaga só o que o gerador escreveu e saiu do plano.

Separado de `delivery` para caber num módulo só o que decide "isto é nosso": os nomes
que o gerador usa, o symlink que aponta para uma pasta de clipes do projeto e a
confinação física dentro de `entrega/` antes de qualquer `unlink`/`rmdir`.
"""

import logging
import re
import stat
from collections import namedtuple
from pathlib import Path

from . import logs
from .runtime import record_warning

log = logs.get("delivery")

INDEX = "README.md"
# Só o que o gerador escreve pode ser apagado quando vira órfão.
GENERATED = re.compile(r"^(ORIGEM(-\d+)?\.md|contact-sheet(-\d+)?\.[a-z0-9]+)$")
BEAT_DIR_RE = re.compile(r"^\d{2}-[a-z0-9-]*$")


def thaw_unlink(path):
    """Apaga o que este gerador congelou, inclusive no Windows.

    `_freeze` tira o bit de escrita; no Windows isso vira o atributo somente-leitura
    e `unlink` levanta `PermissionError`. Devolver a escrita antes é o único jeito de
    `deliver` regenerar `entrega/` depois de um beat renomeado.
    """
    path = Path(path)
    try:
        path.unlink()
        return
    except PermissionError:
        pass
    path.chmod(stat.S_IWRITE | stat.S_IREAD)
    path.unlink()


def _symlink_targets_brolls(path, brolls_root):
    """Um symlink só é nosso quando aponta para um clipe dentro do `brolls/` deste projeto.

    `brolls_root` é uma pasta ou uma lista delas: no layout 1 o clipe mora em `broll/`,
    irmã de `brolls/`, e o link para lá também é do gerador. Um link que a pessoa criou
    (para a própria mídia, um atalho, outra pasta) não bate com isso e não pode ser
    tratado como órfão do gerador.
    """
    roots = (brolls_root,) if isinstance(brolls_root, (str, Path)) else tuple(brolls_root or ())
    try:
        target = path.resolve()
        reals = [Path(root).resolve() for root in roots]
    except OSError:
        return False
    return any(target == real or real in target.parents for real in reals)


def _ours(rel, path, owned, brolls_root):
    """Só é órfão o que este gerador escreveu; o resto é da pessoa e fica.

    Reconhecemos três assinaturas: um caminho que o próprio manifesto registra em
    `c["delivery"]`, os nomes que o gerador usa (`README.md`, `ORIGEM*.md`,
    `contact-sheet*.*` e a mídia, que repete o nome da pasta do beat) e, só quando o
    nome já bateu com uma dessas assinaturas, um symlink que resolve para dentro de
    `brolls/`. Um bilhete — ou um link — que a pessoa deixou dentro da pasta não casa
    com nada disso e é preservado.
    """
    if rel in owned or rel == INDEX:
        return True
    name = path.name
    parent = path.parent.name
    name_matches = bool(GENERATED.match(name)) or (
        bool(BEAT_DIR_RE.match(parent)) and re.fullmatch(re.escape(parent) + r"(-\d+)?\.[A-Za-z0-9]+", name) is not None
    )
    if not name_matches:
        return False
    if path.is_symlink():
        return _symlink_targets_brolls(path, brolls_root)
    return True


def _confined(path, real_root):
    """A exclusão só é segura quando o local real de `path` está dentro de `real_root`.

    `entrega/` em si já foi recusada como symlink em `build_delivery`, mas uma pasta de
    beat marcada à mão como link ainda poderia levar `rglob` para fora do projeto — esta
    checagem confirma o caminho físico antes de qualquer `unlink`/`rmdir`.
    """
    try:
        parent_real = path.parent.resolve()
    except OSError:
        return False
    return parent_real == real_root or real_root in parent_real.parents


_SweepContext = namedtuple("_SweepContext", "expected dry_run owned brolls_root real_root")


def _sweep_empty_directory(path, rel, ctx):
    """One orphaned-beat-directory candidate for `_sweep`: `"removed"`, `"kept"`, or
    `None` (not a candidate at all — same gate `_sweep` used to apply inline).

    A symlinked "directory" needs its own removal path: `rmdir` on a symlink to an
    empty directory raises `NotADirectoryError` on POSIX, and blindly removing it
    could take someone else's folder with it. It is only `unlink()`'d (never
    `rmdir()`'d) when the same rules that already cover a symlinked FILE — `_ours`
    and `_symlink_targets_brolls` — recognize it as this generator's own; otherwise
    it is foreign and stays. A real empty directory that fails to `rmdir`
    (permissions, a race) is left in place and reported as a warning, never an
    aborted delivery.
    """
    if not (
        BEAT_DIR_RE.match(path.name)
        and not any(path.iterdir())
        and rel not in ctx.expected
        and _confined(path, ctx.real_root)
    ):
        return None
    if path.is_symlink():
        if not _ours(rel, path, ctx.owned, ctx.brolls_root):
            return "kept"
        if not ctx.dry_run:
            thaw_unlink(path)
        return "removed"
    if not ctx.dry_run:
        try:
            path.rmdir()
        except OSError as exc:
            record_warning(
                "DELIVERY_SWEEP_RMDIR_FAILED",
                f"Não consegui apagar a pasta vazia {path} ({exc}); ela continua em entrega/.",
            )
            return None
    return "removed"


def sweep(root, expected, dry_run, owned=(), brolls_root=None):
    """Apaga só o que este gerador escreveu e que deixou de existir no plano."""
    removed, kept = [], []
    owned = set(owned)
    if not root.is_dir():
        return removed, kept
    ctx = _SweepContext(expected, dry_run, owned, brolls_root, root.resolve())
    for path in sorted(root.rglob("*"), reverse=True):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            outcome = _sweep_empty_directory(path, rel, ctx)
            if outcome == "removed":
                removed.append(rel)
            elif outcome == "kept":
                kept.append(rel)
            continue
        if rel in expected:
            continue
        if _ours(rel, path, owned, brolls_root) and _confined(path, ctx.real_root):
            if not dry_run:
                thaw_unlink(path)
            removed.append(rel)
        else:
            kept.append(rel)
    logs.event(log, logging.INFO, "sweep", removed=len(removed), kept_foreign=len(kept))
    return removed, kept
