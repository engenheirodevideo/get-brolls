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
    caminho aponta para outro arquivo que não o aberto). É a conferência de
    `open_under` onde `os.open` não aceita `dir_fd` (Windows); ela ainda deixa uma
    janela para quem troca a pasta mais de uma vez durante a conferência."""
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


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
_WALK_FLAGS = _DIR_FLAGS | getattr(os, "O_NOFOLLOW", 0)
# Descer da raiz pasta por pasta exige `os.open`/`os.stat` com `dir_fd` e as flags
# `O_DIRECTORY`/`O_NOFOLLOW` (POSIX). Sem isso (Windows), vale `still_under`.
_BY_COMPONENT = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


# Pastas acima da raiz (e a própria raiz): abertas só para busca quando o sistema deixa
# (`O_PATH` no Linux, `O_SEARCH` no macOS), então uma pasta com permissão só de passagem
# (`--x`) no caminho continua valendo; sem isso, leitura.
_SEARCH_FLAGS = (
    (getattr(os, "O_PATH", 0) or getattr(os, "O_SEARCH", 0) or os.O_RDONLY)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


def _open_at(name, flags, dir_fd=None):
    """`os.open` de `name` relativo à pasta `dir_fd` (ou do caminho `name`, sem ela)."""
    return os.open(name, flags, dir_fd=dir_fd)


def _root_and_parts(path, roots):
    """`(raiz, stat da raiz, partes do caminho abaixo dela)` para `path`.

    Resolve só a pasta de `path` (um link no último pedaço continua recusado na
    abertura, como no `O_NOFOLLOW`) e escolhe a raiz que a contém comparando
    dispositivo + inode de cada pasta acima dele, como `within_roots`. A raiz é lida
    com `lstat`: ela já foi resolvida no registro, então uma raiz que agora é um link
    (trocada depois) não vale e o caminho fica `OUTSIDE`."""
    target = Path(path)
    try:
        resolved = target.parent.resolve(strict=True) / target.name
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafeFileError(OPEN_FAILED, type(exc).__name__) from None
    ancestors = []
    for parent in resolved.parents:
        try:
            ancestors.append((parent, parent.stat()))
        except OSError:
            continue
    for root in roots:
        try:
            root_info = os.lstat(root)
        except OSError:
            continue
        if stat.S_ISLNK(root_info.st_mode):
            continue
        for parent, info in ancestors:
            if _same(root_info, info):
                return root, root_info, resolved.relative_to(parent).parts
    raise UnsafeFileError(OUTSIDE)


def _refused_folder(dir_fd, name, exc):
    """Recusa de uma pasta (a raiz ou uma do meio) que não abriu: um link no lugar dela é
    `OUTSIDE` (o caminho sairia da raiz por ali); qualquer outra falha é `OPEN_FAILED`."""
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return UnsafeFileError(OPEN_FAILED, type(exc).__name__)
    if stat.S_ISLNK(info.st_mode):
        return UnsafeFileError(OUTSIDE)
    return UnsafeFileError(OPEN_FAILED, type(exc).__name__)


def _open_folder_at(dir_fd, name):
    """Descritor da pasta `name` dentro de `dir_fd` (ou do caminho `name`, sem ela),
    com `O_DIRECTORY|O_NOFOLLOW`."""
    try:
        return _open_at(name, _WALK_FLAGS, dir_fd)
    except OSError as exc:
        raise _refused_folder(dir_fd, name, exc) from None


def _refused_root_part(dir_fd, name, exc):
    """Recusa de uma pasta do caminho da raiz que não abriu: link, pasta que sumiu ou algo
    que não é pasta é `OUTSIDE` (o caminho já resolvido mudou); o resto é `OPEN_FAILED`."""
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        return UnsafeFileError(OUTSIDE)
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return UnsafeFileError(OPEN_FAILED, type(exc).__name__)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return UnsafeFileError(OUTSIDE)
    return UnsafeFileError(OPEN_FAILED, type(exc).__name__)


def open_root(root):
    """Descritor da pasta `root`, descendo da âncora (`/` ou a unidade) um nome por vez.

    `root` é um caminho já resolvido (sem link por construção): cada nome é aberto com
    `O_DIRECTORY|O_NOFOLLOW` relativo à pasta anterior, então uma pasta acima da raiz,
    ou a própria raiz, trocada por link depois de resolvida é `OUTSIDE` — como um nome
    que sumiu ou deixou de ser pasta. Só no POSIX (`dir_fd`); quem recebe o descritor
    fecha, e numa recusa as pastas abertas no caminho já saem fechadas.

    Args:
        root: caminho absoluto e já resolvido da raiz.

    Returns:
        O descritor da raiz.

    Raises:
        UnsafeFileError: `OUTSIDE` (link, nome que sumiu, não é pasta, caminho relativo)
            ou `OPEN_FAILED` (outra falha ao abrir, com o tipo do erro).
    """
    path = Path(root)
    if not path.is_absolute():
        raise UnsafeFileError(OUTSIDE)
    try:
        dir_fd = _open_at(path.anchor, _SEARCH_FLAGS)
    except OSError as exc:
        raise UnsafeFileError(OPEN_FAILED, type(exc).__name__) from None
    try:
        for name in path.parts[1:]:
            try:
                child_fd = _open_at(name, _SEARCH_FLAGS, dir_fd)
            except OSError as exc:
                raise _refused_root_part(dir_fd, name, exc) from None
            os.close(dir_fd)
            dir_fd = child_fd
    except BaseException:
        os.close(dir_fd)
        raise
    return dir_fd


def _open_file_at(dir_fd, name):
    """Descritor do arquivo `name` dentro de `dir_fd`, com as flags de `open_regular`."""
    try:
        return _open_at(name, _READ_FLAGS, dir_fd)
    except OSError as exc:
        raise UnsafeFileError(OPEN_FAILED, type(exc).__name__) from None


def _open_by_component(path, roots, single_link):
    """`open_under` no POSIX: desce da raiz uma pasta por vez, pelo descritor.

    A raiz é aberta por `open_root`, descendo da âncora um nome por vez sem seguir link
    (ela já foi resolvida: se ela ou uma pasta acima dela agora é um link, é `OUTSIDE`),
    e conferida pelo descritor (mesmo dispositivo + inode do `lstat` da raiz escolhida);
    cada pasta do meio é aberta com `O_DIRECTORY|O_NOFOLLOW` relativa à anterior, e o
    arquivo com as flags de `open_regular` relativas à última. Nenhum link é seguido
    depois da escolha da raiz: trocar uma pasta do meio, a raiz ou uma pasta acima dela
    por um link — uma vez ou várias, antes ou durante a abertura — nunca leva a um
    arquivo de fora dela. Toda pasta aberta na descida é fechada; numa recusa o
    arquivo também sai fechado."""
    root, root_info, parts = _root_and_parts(path, roots)
    dir_fd = open_root(root)
    try:
        if not _same(os.stat(dir_fd), root_info):  # noqa: PTH116 - stat do descritor aberto (o mesmo que fstat)
            raise UnsafeFileError(CHANGED)
        for name in parts[:-1]:
            child_fd = _open_folder_at(dir_fd, name)
            os.close(dir_fd)
            dir_fd = child_fd
        fd = _open_file_at(dir_fd, parts[-1])
    finally:
        os.close(dir_fd)
    try:
        info = _check_fd(fd, single_link)
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def open_under(path, roots, *, single_link=True):
    """`(fd, stat)` de um arquivo regular dentro de uma das `roots`, sem seguir link.

    No POSIX, a abertura desce da raiz pasta por pasta (`_open_by_component`): o
    arquivo aberto está dentro da raiz por construção, não por uma conferência feita
    depois. Onde `os.open` não aceita `dir_fd` (Windows), é `open_regular` +
    `still_under`. Numa recusa o descritor já sai fechado."""
    if _BY_COMPONENT:
        return _open_by_component(path, roots, single_link)
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
