"""`entrega/`: nomes de pasta e a escada link/cópia que põe cada arquivo no lugar.

Nome seguro de pasta de beat (`beat_dir_name`) e `link_or_copy`: hardlink, symlink e
cópia, nessa ordem, sem sobrescrever o que a pessoa editou. O plano e a rodada de
`deliver` ficam em `delivery`; o texto de `ORIGEM.md` e do `README.md`, em `delivery_render`.
"""

import contextlib
import errno
import functools
import os
import re
import stat
import unicodedata
from pathlib import Path

from . import delivery_sweep, effects, labels
from ._paths import strict_exists
from .runtime import record_warning

# Nome de pasta que ainda cabe num Finder/Explorer sem virar reticências.
MAX_NAME = 60


def slug(text):
    """Texto humano vira pedaço de caminho: ASCII, minúsculo, só letras/números/hífen."""
    normal = unicodedata.normalize("NFKD", str(text or ""))
    ascii_only = normal.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_only)).strip("-")


def _safe(value, field):
    """Recusa qualquer coisa que tentaria sair da pasta de entrega."""
    text = str(value or "")
    if ".." in text or "/" in text or "\\" in text or os.sep in text:
        raise ValueError(
            f'Valor inválido para o nome da pasta de entrega ({field}): "{text}". '
            "Ids de beat usam só letras minúsculas, números e hífen; corrija o BRIEF.md "
            "ou o --shot do candidato."
        )
    return text


def beat_dir_name(nn, beat_id, target):
    """`NN-<beat.id>-<slug(alvo)>`: estável entre execuções e seguro como caminho."""
    _safe(beat_id, "id do beat")
    _safe(target, "alvo do beat")
    prefix = f"{int(nn):02d}"
    parts = [prefix, slug(beat_id), slug(target)]
    name = "-".join(p for p in parts if p)
    name = re.sub(r"-+", "-", name).strip("-")[:MAX_NAME].rstrip("-")
    if not name or name in (".", "..") or not delivery_sweep.BEAT_DIR_RE.match(name):
        raise ValueError(
            f'Não consegui montar um nome de pasta seguro para o beat "{beat_id}". '
            "Use um id com letras minúsculas, números e hífen."
        )
    return name


class _CompareError(Exception):
    """Raised when a comparison could not complete because of an OSError.

    Kept distinct from a real content difference: `link_or_copy` must never report
    "parece edição sua" when it simply failed to read one of the two files.
    """


def _same_file(a, b):
    try:
        return os.path.samestat(Path(a).stat(), Path(b).stat())
    except OSError as exc:
        raise _CompareError(labels.reason(exc)) from exc


def _same_bytes(a, b, block=1024 * 1024):
    """Compara em blocos, nunca carregando um vídeo inteiro na memória."""
    try:
        if Path(a).stat().st_size != Path(b).stat().st_size:
            return False
        with Path(a).open("rb") as left, Path(b).open("rb") as right:
            while True:
                chunk = left.read(block)
                if chunk != right.read(block):
                    return False
                if not chunk:
                    return True
    except OSError as exc:
        raise _CompareError(labels.reason(exc)) from exc


def _compared(compare, dest, src):
    """Run one comparison; an I/O failure is reported as such, never as a user edit."""
    try:
        return compare(dest, src)
    except _CompareError as exc:
        raise ValueError(
            f"Não consegui comparar {labels.shown(dest)} com o arquivo coletado ({exc}): confira "
            "permissão ou disponibilidade do arquivo e rode `deliver` de novo."
        ) from exc


def copies_forced():
    """`GB_DELIVERY_COPY=1`: cópias independentes para quem quer editar em `entrega/`."""
    return (os.environ.get("GB_DELIVERY_COPY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _freeze(path, method):
    """Tira a escrita só de quem compartilha bytes com o original.

    Hardlink e symlink são o mesmo arquivo com outro nome: editar ali editaria
    `brolls/`. Uma cópia é independente — congelá-la quebraria a promessa de
    `GB_DELIVERY_COPY=1`, que existe justamente para quem quer editar em `entrega/`.

    Uma falha aqui fica não fatal (o resto da entrega continua), mas nunca some: vai
    para `record_warning`, o mesmo canal de aviso que o resto do projeto usa, para que
    a promessa de "somente-leitura" não fique silenciosamente falsa.
    """
    if method not in ("hardlink", "symlink"):
        return
    try:
        mode = Path(path).stat().st_mode
        Path(path).chmod(mode & ~0o222)
    except OSError as exc:
        record_warning(
            "DELIVERY_FREEZE_FAILED",
            f"Não consegui deixar {labels.shown(path)} somente-leitura ({labels.reason(exc)}): o arquivo entregue "
            "compartilha o inode com o original em brolls/, mas ficou editável.",
        )


def _writable(path, shown=None):
    """Devolve a escrita do dono a uma cópia: `copy2` herda o modo do clipe congelado.

    Uma cópia é independente (é a promessa de `GB_DELIVERY_COPY=1`); nascer
    somente-leitura só porque o original em `brolls/` estava congelado a deixaria
    impossível de editar. A falha vira aviso, como em `_freeze`, nomeando `shown` (o
    nome final, quando `path` ainda é o `.part`).
    """
    try:
        mode = Path(path).stat().st_mode
        if not mode & stat.S_IWUSR:
            Path(path).chmod(mode | stat.S_IWUSR)
    except OSError as exc:
        record_warning(
            "DELIVERY_COPY_READ_ONLY",
            f"Não consegui deixar a cópia {labels.shown(shown or path)} editável ({labels.reason(exc)}): "
            "ela é independente do "
            "original em brolls/, mas ficou somente-leitura.",
        )


def _single_name(path):
    """`path` é o único nome do arquivo: uma cópia de verdade, não outro nome de um clipe de `brolls/`."""
    try:
        return Path(path).stat().st_nlink == 1
    # lenient: a file it cannot stat is not proven to be a single name, so it is not made editable
    except OSError:
        return False


def _copy_whole(src, dest, editable=True, linked=None):
    """Copia `src` para `dest` por um `<dest>.part` ao lado, e só o arquivo inteiro ganha o nome.

    Uma cópia cortada no meio (Ctrl+C, disco cheio) deixava um arquivo truncado com o
    nome final, e a rodada seguinte o tomava por edição da pessoa. Qualquer falha
    apaga o `.part`; uma sobra de rodada morta à força sai antes da cópia nova.
    `editable=False` (o `export`) deixa o modo que o `copy2` trouxe. `linked` é o clipe
    de que `dest` ainda é hardlink: o `.part` toma o lugar dele (`_take_name`).
    """
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    # lenient(2): leftover .part of a killed run; if its folder is locked, copy2 below raises the EACCES
    if part.is_symlink() or part.exists():
        delivery_sweep.thaw_unlink(part)
    try:
        effects.copy_file(src, part, metadata=True)
        if editable:
            _writable(part, shown=dest)
        _take_name(part, dest, linked)
    except BaseException:
        with contextlib.suppress(OSError):
            # lenient(2): cleanup of our own .part; must not mask the copy error
            if part.is_symlink() or part.exists():
                delivery_sweep.thaw_unlink(part)
        raise


def _take_name(part, dest, linked):
    """`part` vira `dest`; no POSIX a troca substitui de uma vez o hardlink que estava ali.

    No Windows a troca recusa um destino somente-leitura (o link congelado de `linked`):
    só então o nome sai, sem destravar o clipe, e a troca é refeita.
    """
    with effects.op("fs.replace", dest) as first:
        try:
            part.replace(dest)
            return
        except PermissionError:
            # lenient: inside except PermissionError; re-raises the original error
            if linked is None or not dest.exists():
                raise
            # Só agora se sabe que é o caso do Windows (o link congelado no lugar): a tentativa foi
            # recusada como esperado, e a troca é refeita depois de o nome sair.
            first.skip(fallback="eacces")
    delivery_sweep.unlink_keeping_mode(dest, linked)
    effects.replace(part, dest)


def _copy_over_link(src, dest, editable):
    """Troca o hardlink `dest` de `src` por uma cópia, sem tirar o link antes de a cópia estar inteira.

    Apagar primeiro deixava o item sem arquivo em `entrega/` (e o manifesto dizendo
    "hardlink") quando a cópia falhava no meio. O disco cheio é conflito do item.
    """
    try:
        _copy_whole(src, dest, editable=editable, linked=src)
    except OSError as exc:
        raise ValueError(
            f"Não consegui trocar {labels.shown(dest)} por uma cópia independente ({labels.reason(exc)}): "
            "confira espaço e "
            "permissão de escrita na pasta do projeto e rode `deliver` de novo."
        ) from exc


def _links_elsewhere(src, dest):
    """`dest` é um link que existe e aponta para outro arquivo que não `src`: pode ser obra da pessoa.

    Link para o próprio `src` (o `forced` o troca por cópia) e link quebrado não são. Um link para
    outro clipe de `brolls/` (revisão antiga) passa aqui também; quem decide é `Pristine`, em `_place_media`.
    """
    # lenient: a link whose target cannot be stat'ed (dangling, loop, locked folder) is ours to replace
    if not dest.is_symlink() or not strict_exists(dest):
        return False
    try:
        return dest.resolve() != Path(src).resolve()
    except OSError:
        return False


def _refuse_foreign_link(src, dest):
    """Levanta o erro de edição quando `dest` é um link da pessoa para outro arquivo; `_place_media` o refina."""
    if _links_elsewhere(src, dest):
        raise _edited(dest)


def _settle_existing(src, dest, read_only, forced, editable):
    """O que fazer com um `dest` que já existe: o método que ele já é, ou `None` quando saiu.

    Link para `src` continua sendo o mesmo arquivo, a não ser com `forced` (cópia
    pedida): aí a cópia nasce no lugar dele (o hardlink só sai quando ela está inteira,
    e o método já é `"copy"`). Cópia com os mesmos bytes fica — e,
    com `editable`, volta a ser editável se uma versão antiga a deixou somente-leitura.
    Conteúdo diferente é obra da pessoa e o erro nomeia o arquivo.
    """
    # lenient: a link in place of the delivered file is judged below; strict_exists after it sees the rest
    if dest.is_symlink():
        try:
            if dest.resolve() == src.resolve() and not forced:
                return "symlink"
        except OSError:
            pass
        _refuse_foreign_link(src, dest)
        delivery_sweep.thaw_unlink(dest)
        return None
    if not strict_exists(dest):
        return None
    if _compared(_same_file, dest, src):
        if not forced:
            if read_only:
                _freeze(dest, "hardlink")
            return "hardlink"
        # Mesmo inode do clipe: só um nome a menos, o original não muda.
        _copy_over_link(src, dest, editable)
        return "copy"
    if _compared(_same_bytes, dest, src):
        # A versão antiga copiava o modo do clipe congelado (0444). Só um arquivo de nome
        # único é cópia de verdade: outro nome de um clipe congelado continua congelado.
        if editable and _single_name(dest):
            _writable(dest)
        return "copy"
    raise _edited(dest)


class EditedError(ValueError):
    """`dest` já existe com conteúdo diferente do arquivo coletado (talvez edição da pessoa)."""


def _edited(dest):
    """O erro de um `dest` que a pessoa mudou: o mesmo texto na rodada real e no ensaio."""
    return EditedError(
        f"{labels.shown(dest)} já existe com conteúdo diferente do arquivo coletado: parece edição "
        "sua e eu não sobrescrevo. Renomeie ou apague esse arquivo e rode `deliver` "
        "de novo."
    )


def refuse_if_edited(src, dest):
    """O veredito de `link_or_copy` sobre um `dest` que já existe, só lendo (para o ensaio).

    Link ou cópia com os mesmos bytes passam, e um link quebrado seria refeito. Um link para
    outro arquivo e o resto levantam o mesmo erro da rodada real.
    """
    src, dest = Path(src), Path(dest)
    # lenient: link test; strict_exists on the same line raises for a locked folder
    if dest.is_symlink():
        _refuse_foreign_link(src, dest)
        return
    if not strict_exists(dest):
        return
    if _compared(_same_file, dest, src) or _compared(_same_bytes, dest, src):
        return
    raise _edited(dest)


# Um link que não deu (outro volume, sistema sem link, sem privilégio, limite de links) cai no método
# seguinte: no `event=op` é `outcome=skipped` com o motivo (`fallback=exdev`). Outro erro (EIO, EROFS)
# sai como erro, mesmo que a cópia depois dê certo. A cópia é o último.
_LINK_FALLBACK = (
    NotImplementedError,
    AttributeError,
    errno.EXDEV,
    errno.EPERM,
    errno.EMLINK,
    errno.ENOTSUP,
    errno.EOPNOTSUPP,
    errno.ENOSYS,
    errno.EACCES,
)


def link_or_copy(src, dest, read_only=False, allow_symlink=True, *, editable_copy=True):
    """Liga `dest` a `src` pelo jeito mais barato que o sistema aceitar.

    Hardlink primeiro (não ocupa disco e não quebra ao mover a pasta de dentro),
    symlink depois (no Windows pode faltar privilégio) e cópia por último. Devolve o
    método usado. Se `dest` já é o mesmo arquivo, não faz nada; se é um arquivo comum
    com conteúdo diferente, é obra da pessoa e o erro nomeia o arquivo.
    `allow_symlink=False` pula o symlink (hardlink ou cópia): quem monta um projeto
    que vai para outra máquina não pode depender de link para fora da pasta.
    Com `GB_DELIVERY_COPY=1`, um `dest` que ainda é link para `src` (de uma entrega
    anterior) sai e vira cópia: editar ali editaria o original. Toda cópia de `entrega/`
    nasce editável; `editable_copy=False` (o `export`) deixa o modo como o `copy2` trouxe.
    """
    src, dest = Path(src), Path(dest)
    forced = copies_forced()
    copy = functools.partial(_copy_whole, editable=editable_copy)
    methods = (
        (("copy", copy),)
        if forced
        else (
            ("hardlink", functools.partial(effects.link, fallback=_LINK_FALLBACK)),
            *(
                (("symlink", functools.partial(effects.link, symbolic=True, fallback=_LINK_FALLBACK)),)
                if allow_symlink
                else ()
            ),
            ("copy", copy),
        )
    )
    settled = _settle_existing(src, dest, read_only, forced, editable_copy)
    if settled is not None:
        return settled
    effects.mkdir(dest.parent, parents=True, exist_ok=True)
    for method, make in methods:
        try:
            make(str(src), str(dest))
            if read_only:
                _freeze(dest, method)
            return method
        except (OSError, NotImplementedError, AttributeError):
            # lenient(2): cleanup after a failed link/copy; must not mask that error
            if dest.is_symlink() or dest.exists():
                delivery_sweep.thaw_unlink(dest)
    raise OSError(
        f"Não consegui ligar nem copiar {labels.shown(src)} para {labels.shown(dest)}. Confira espaço e permissão "
        "de escrita na pasta do projeto."
    )
