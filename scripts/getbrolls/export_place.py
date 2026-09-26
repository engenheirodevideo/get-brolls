# pylint: disable=line-too-long  # tabela em markdown: cada linha da tabela não quebra sem virar outro valor
"""Mídia dentro de uma pasta de export: posta pelo core, por id lógico, sem nunca mudar a fonte.

| Fonte | Método |
|---|---|
| clipe (`brolls/clips/`) | hardlink, cópia como alternativa (`delivery.link_or_copy`, sem symlink e sem chmod: o `deliver` já congela a fonte) |
| mídia da pessoa (`aroll/`, `assets/`, biblioteca pessoal) | clone (`cp -c` no macOS, `cp --reflink=auto` no Linux) ou `shutil.copy2`; nunca hardlink |
| mídia de resolvedor de plugin | cópia pelo descritor, pela função `copy_plugin(source, dest)` que o chamador injeta |

Antes de pôr, a fonte é conferida de novo (`lstat`): mesmo `st_dev`/`st_ino`/tamanho
(e data, quando o plano a tem), arquivo regular e não link. Depois de pôr, confere de
novo a fonte e o destino (hardlink = mesmo inode; cópia = mesmo tamanho): se algo
mudou no meio, o destino sai e o export para com "mudou durante o export".
"""
# pylint: enable=line-too-long

import contextlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

from . import delivery

# Método que com certeza não duplica bytes em disco: não conta nos MB copiados do resumo.
# O clone (`cp -c`, `cp --reflink=auto`) conta o tamanho inteiro: num disco que não clona
# (exFAT, HFS+, SMB) o `cp` faz cópia completa e ainda sai com 0, então o resumo diz "até N MB".
SHARED_METHODS = ("hardlink",)


def check_requests(requests, media, sources):
    """Confere os pedidos de mídia do exporter contra o plano: devolve `[(media_id, dest)]` ou levanta.

    Cada id existe no mapa do core e está disponível; o sufixo do destino é a extensão
    da fonte (sem caixa: `.MOV` da câmera já virou `.mov` no plano); nenhum destino
    repete outro depois de `casefold`.
    """
    seen = {}
    checked = []
    for media_id, dest in requests:
        row = media.get(media_id)
        if row is None:
            raise ValueError(f"O exporter pediu a mídia {media_id!r}, que não está no plano.")
        if not row["available"] or media_id not in sources:
            raise ValueError(f"O exporter pediu a mídia {media_id!r}, que não está disponível.")
        if any(part in ("", ".", "..") for part in dest.split("/")):
            raise ValueError(f"Destino de mídia com trecho vazio, '.' ou '..': {dest!r}.")
        path = PurePosixPath(dest)
        if path.parts[:1] != ("assets",) or len(path.parts) < 2:  # noqa: PLR2004 - "assets/<nome>" at least
            raise ValueError(f"Destino de mídia fora de assets/: {dest!r}.")
        if path.suffix.casefold() != (row["ext"] or "").casefold():
            raise ValueError(f"Destino {dest!r} não tem a extensão da mídia {media_id!r} ({row['ext']}).")
        key = dest.casefold()
        if key in seen:
            raise ValueError(f"Dois pedidos de mídia no mesmo destino: {seen[key]!r} e {dest!r}.")
        seen[key] = dest
        checked.append((media_id, dest))
    return checked


def _changed(path):
    return ValueError(f"{Path(path).name} mudou durante o export: repita.")


def verify_source(source):
    """A fonte ainda é o arquivo visto no plano: regular, não link, mesmo `st_dev`/`st_ino`/tamanho.

    A data (`st_mtime_ns`) também conta quando o plano a registrou: mesmo tamanho com
    outra data é outro conteúdo.
    """
    path = Path(source["path"])
    try:
        info = path.lstat()
    except OSError:
        raise ValueError(f"{path.name} sumiu durante o export: repita.") from None
    same = (info.st_dev, info.st_ino, info.st_size) == (source["st_dev"], source["st_ino"], source["st_size"])
    mtime = source.get("st_mtime_ns")
    if mtime is not None and info.st_mtime_ns != mtime:
        same = False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not same:
        raise _changed(path)
    return info


def _clone_platform():
    """Onde clonar é possível: "darwin", o `cp` achado no Linux, ou None (Windows, `cp` ausente)."""
    if sys.platform == "darwin":
        return "/bin/cp"
    if sys.platform.startswith("linux"):
        return shutil.which("cp")
    return None


def can_clone():
    """True onde `clone_or_copy` tenta o clone (macOS; Linux com `cp`); no Windows é sempre cópia."""
    return _clone_platform() is not None


def _clone_command(src, dest):
    """(argv, método) do clone do sistema, ou None onde não há (Windows, `cp` ausente)."""
    cp = _clone_platform()
    if cp is None:
        return None
    if sys.platform == "darwin":
        return [cp, "-c", "--", str(src), str(dest)], "clone"
    return [cp, "--reflink=auto", "--", str(src), str(dest)], "reflink-auto"


def clone_or_copy(src, dest):
    """Clona quando o disco deixa (APFS, Btrfs, XFS); senão `shutil.copy2`. Nunca hardlink, nunca chmod.

    Sem tempo-limite: a cópia completa de um A-ROLL grande pode demorar, e matar o `cp`
    para refazer com `copy2` dobraria o tempo.
    """
    src, dest = Path(src), Path(dest)
    if not (src.is_absolute() and dest.is_absolute()):
        raise ValueError(f"Clone de mídia pede caminho absoluto: {src} → {dest}.")
    command = _clone_command(src, dest)
    if command is not None:
        argv, method = command
        try:
            done = subprocess.run(argv, check=False, capture_output=True)  # noqa: S603 - fixed cp argv, absolute paths after "--", never a shell  # pylint: disable=line-too-long
        except (OSError, subprocess.SubprocessError):
            done = None
        if done is not None and done.returncode == 0 and dest.is_file() and not dest.is_symlink():
            return method
        dest.unlink(missing_ok=True)
    shutil.copy2(src, dest)
    return "copy"


def _undo(dest):
    """Tira o destino recusado (só o nome no staging; num hardlink, a fonte fica intacta)."""
    with contextlib.suppress(OSError):
        dest.unlink(missing_ok=True)


def _confirm(source, dest, method):
    """Depois de pôr: a fonte ainda é a do plano e o destino é ela (mesmo inode ou mesmo tamanho)."""
    try:
        if method != "plugin":
            verify_source(source)
        placed = dest.lstat()
    except (OSError, ValueError):
        _undo(dest)
        raise _changed(source["path"]) from None
    if method == "hardlink":
        same = (placed.st_dev, placed.st_ino) == (source["st_dev"], source["st_ino"])
    else:
        same = stat.S_ISREG(placed.st_mode) and placed.st_size == source["st_size"]
    if not same:
        _undo(dest)
        raise _changed(source["path"])


def place(source, dest, copy_plugin=None):
    """Põe a fonte em `dest` (absoluto, dentro do staging) e devolve `(método, bytes copiados)`.

    `copy_plugin(source, dest)` recebe a linha inteira do plano (`path`, `st_dev`,
    `st_ino`, `st_size`, `store`); o que ela devolve é ignorado, e o destino tem de
    ficar com o tamanho do plano.
    """
    dest = Path(dest)
    if os.path.lexists(dest):
        raise ValueError(
            f"{dest.name} já existe no export em montagem: não sobrescrevo nem sigo link; repita o export."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    kind = source["method"]
    if kind == "plugin":
        if copy_plugin is None:
            raise ValueError("Mídia de resolvedor de plugin precisa da cópia pelo descritor do SDK.")
        copy_plugin(source, dest)
        _confirm(source, dest, "plugin")
        return "copy", source["st_size"]
    if kind not in ("hardlink", "clone"):
        raise ValueError(f"Método de mídia desconhecido: {kind!r}.")
    info = verify_source(source)
    if kind == "hardlink":
        method = delivery.link_or_copy(source["path"], dest, read_only=False, allow_symlink=False)
    else:
        method = clone_or_copy(source["path"], dest)
    _confirm(source, dest, method)
    return method, 0 if method in SHARED_METHODS else info.st_size


def same_as_before(source, marker_row):
    """False quando a fonte de uma mídia do export anterior mudou (inode, data ou tamanho): só para aviso."""
    try:
        info = os.lstat(source["path"])
    except OSError:
        return False
    return (info.st_ino, info.st_mtime_ns, info.st_size) == (
        marker_row.get("source_ino"),
        marker_row.get("source_mtime_ns"),
        marker_row.get("source_size"),
    )
