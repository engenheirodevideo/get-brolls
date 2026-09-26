"""Abrir e copiar arquivo da pessoa sem seguir link: o core usa, o plugin nunca chama.

`api.local_file` (rota de plugin) e a cópia de mídia achada por um resolvedor passam
por aqui. Tudo é conferido pelo DESCRITOR aberto (`fstat`), não pelo caminho: um link
trocado entre a conferência e a abertura não é seguido, uma FIFO não trava e o teto
é contado nos bytes de fato lidos.

As funções levantam `UnsafeFileError` com um `reason` curto; quem chama escreve a
mensagem para a pessoa (cada uso nomeia o arquivo do seu jeito).
"""

import os
import stat
from pathlib import Path

from .files import is_link

CHUNK_BYTES = 1024 * 1024

# Motivos de `UnsafeFileError`.
OPEN_FAILED = "open"
NOT_REGULAR = "not_regular"
LINKED = "linked"  # mais de um nome no disco (hardlink): pode ser um arquivo de fora da raiz
TOO_BIG = "too_big"
TARGET_EXISTS = "exists"
TARGET_FAILED = "create"
CHANGED = "changed"
OUTSIDE = "outside"


class UnsafeFileError(Exception):
    """Recusa do core ao abrir, conferir ou copiar; `type_name` é o tipo do `OSError` de origem."""

    def __init__(self, reason, type_name=None):
        super().__init__(reason)
        self.reason = reason
        self.type_name = type_name


_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


def _check_fd(fd, single_link):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise UnsafeFileError(NOT_REGULAR)
    if single_link and info.st_nlink != 1:
        raise UnsafeFileError(LINKED)
    return info


def open_regular(path, *, single_link=True):
    """`(fd, stat)` de um arquivo regular aberto só para leitura, sem seguir link.

    `O_NOFOLLOW` recusa um link no último componente e `O_NONBLOCK` não deixa uma
    FIFO travar; o tipo é conferido no próprio descritor. Com `single_link`, um
    arquivo com mais de um nome (`st_nlink != 1`) é recusado: um hardlink dentro da
    raiz pode apontar para um arquivo de fora dela. No Windows, onde `O_NOFOLLOW`
    não existe, um link simbólico ou junction é recusado antes de abrir. Quem recebe
    o descritor fecha; numa recusa ele já sai fechado.
    """
    if not hasattr(os, "O_NOFOLLOW") and is_link(Path(path)):
        raise UnsafeFileError(OPEN_FAILED, "OSError")
    try:
        fd = os.open(path, _READ_FLAGS)
    except OSError as exc:
        raise UnsafeFileError(OPEN_FAILED, type(exc).__name__) from None
    try:
        info = _check_fd(fd, single_link)
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def _same(a, b):
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def within_roots(path, roots):
    """`path` (já resolvido) fica dentro de alguma raiz? Comparado pelo arquivo de
    verdade (dispositivo + inode de cada pasta acima dele), não pelo texto: num disco
    que não diferencia caixa, `.../SONS/a.wav` fica dentro da raiz `.../Sons`."""
    parents = []
    for parent in Path(path).parents:
        try:
            parents.append(parent.stat())
        except OSError:
            continue
    for root in roots:
        try:
            root_info = Path(root).stat()
        except OSError:
            continue
        if any(_same(root_info, parent) for parent in parents):
            return True
    return False


def still_under(path, roots, fd):
    """Depois de abrir `path`: o arquivo que o descritor segura ainda é o que o
    caminho aponta, e o caminho, resolvido de novo, ainda fica dentro de uma raiz.

    `O_NOFOLLOW` só vale para o último pedaço do caminho; uma pasta do meio trocada
    por um link entre a conferência da raiz e a abertura levaria a um arquivo de
    fora. Aqui isso vira `OUTSIDE` (o caminho agora sai da raiz) ou `CHANGED` (o
    caminho aponta para outro arquivo que não o aberto)."""
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise UnsafeFileError(CHANGED) from None
    if not within_roots(resolved, roots):
        raise UnsafeFileError(OUTSIDE)
    try:
        held = os.stat(fd)  # noqa: PTH116 - stat do descritor aberto (o mesmo que fstat)
        current = resolved.stat()
        again = Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise UnsafeFileError(CHANGED) from None
    if not _same(held, current) or again != resolved:
        raise UnsafeFileError(CHANGED)
    return resolved


def open_under(path, roots, *, single_link=True):
    """`open_regular` + `still_under`: `(fd, stat)` de um arquivo que, aberto, ainda
    fica dentro de uma das `roots`. Numa recusa o descritor já sai fechado."""
    fd, info = open_regular(path, single_link=single_link)
    try:
        still_under(path, roots, fd)
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def copy_from_fd(fd, target, cap, *, mode=0o600):
    """Copia do descritor já conferido para `target`, criado exclusivo e sem seguir
    link, com teto nos bytes de fato lidos; apaga o destino parcial se falhar."""
    target = Path(target)
    try:
        target_fd = os.open(target, _WRITE_FLAGS, mode)
    except FileExistsError:
        raise UnsafeFileError(TARGET_EXISTS) from None
    except OSError as exc:
        raise UnsafeFileError(TARGET_FAILED, type(exc).__name__) from None
    copied = 0
    try:
        with os.fdopen(target_fd, "wb") as out:
            while chunk := os.read(fd, CHUNK_BYTES):
                copied += len(chunk)
                if copied > cap:
                    raise UnsafeFileError(TOO_BIG)
                out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return copied


def recheck(fd_or_path, st_dev, st_ino, st_size, roots=None):
    """Confere, na hora da cópia, que o arquivo ainda é o mesmo que foi achado antes.

    Com um caminho, abre de novo e devolve `(fd, stat)` — quem chama copia desse
    descritor e o fecha; com `roots`, a abertura é a de `open_under` (o caminho
    ainda fica dentro de uma raiz). Com um descritor, só confere e devolve
    `(fd, stat)`, sem fechá-lo. Arquivo trocado (outro `st_dev`/`st_ino`), com outro
    tamanho, que deixou de ser regular ou ganhou outro nome no disco é recusado.
    """
    if type(fd_or_path) is int:
        fd, info = fd_or_path, _check_fd(fd_or_path, single_link=True)
        owned = False
    elif roots is not None:
        fd, info = open_under(fd_or_path, roots)
        owned = True
    else:
        fd, info = open_regular(fd_or_path)
        owned = True
    if (info.st_dev, info.st_ino, info.st_size) != (st_dev, st_ino, st_size):
        if owned:
            os.close(fd)
        raise UnsafeFileError(CHANGED)
    return fd, info
