"""Pastas de export numeradas: `exports/<exporter>/001/`, `002/`… e `LATEST`.

Cada `gb export` grava uma pasta nova. O core nunca apaga, substitui nem mescla uma
pasta numerada: render, transcrição, Studio e edição à mão dentro dela são da pessoa.
A escrita acontece em `exports/<exporter>/.staging-<uuid8>/` (mesmo volume), com o
marcador do core como primeiro arquivo e o plano entregue ao exporter
(`getbrolls-plan.json`) logo depois; no fim, `os.rename` para o próximo número e
`LATEST` passa a apontar para ela. Falha no meio remove só o staging desta execução.
"""

import contextlib
import errno
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path, PurePosixPath

from . import delivery, export_place
from .sdk import loader

EXPORTS_DIR = "exports"
MARKER = ".getbrolls-export.json"
MARKER_ID = "getbrolls-export"
# O plano exato entregue ao exporter, gravado pelo core em toda pasta numerada.
PLAN_FILE = "getbrolls-plan.json"
LATEST = "LATEST"
STAGING_PREFIX = ".staging-"
NUMBER_RE = re.compile(r"[0-9]{3,}")
LATEST_TMP_RE = re.compile(r"LATEST\.tmp-[0-9a-f]{8}")
RESERVED_NAMES = (MARKER.casefold(), f"{MARKER}.tmp".casefold(), PLAN_FILE.casefold())
RENAME_ATTEMPTS = 5
# Pasta em uso (preview, antivírus, indexador): tenta o mesmo número de novo antes de desistir.
BUSY_ATTEMPTS = 3
BUSY_WAIT_S = 0.5
BUSY_MESSAGE = (
    "Não consegui renomear a pasta do export: outro programa está usando a pasta. "
    "Feche o preview ou o antivírus que está usando a pasta e rode de novo."
)
MARKER_MAX_BYTES = 1024 * 1024
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)


def folder_name(number):
    return f"{number:03d}"


def exporter_root(project, exporter):
    """`<projeto>/exports/<exporter>`, sem criar; `exports/` e a pasta do exporter nunca são link nem arquivo."""
    base = Path(project).expanduser().resolve() / EXPORTS_DIR
    root = base / exporter
    for path, label in ((base, "exports/"), (root, f"exports/{exporter}/")):
        if loader._is_link(path):
            raise ValueError(
                f"{label} é um link: o export só grava numa pasta de verdade do projeto. Troque o link por uma pasta e repita."
            )
        if path.exists() and not path.is_dir():
            raise ValueError(f"{label} é um arquivo, não uma pasta: renomeie esse arquivo e repita.")
    return root


def _latest_number(root):
    """Número em `LATEST` quando ele é um arquivo regular com um número; None no resto."""
    path = root / LATEST
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if NUMBER_RE.fullmatch(text) else None


def next_number(root):
    """Maior entre as pastas numeradas e o `LATEST` válido, mais um: nunca reaproveita um número."""
    numbers = [0]
    if root.is_dir():
        numbers += [int(p.name) for p in root.iterdir() if NUMBER_RE.fullmatch(p.name)]
    latest = _latest_number(root)
    if latest is not None:
        numbers.append(latest)
    return max(numbers) + 1


def _read_marker(path):
    """Marcador do core como dict, só se for arquivo regular pequeno com `"marker": "getbrolls-export"`."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MARKER_MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("marker") == MARKER_ID else None


def latest_marker(root):
    """Marcador `complete` da pasta que `LATEST` aponta; None sem export anterior legível."""
    number = _latest_number(root)
    if number is None:
        return None
    marker = _read_marker(root / folder_name(number) / MARKER)
    return marker if marker and marker.get("state") == "complete" else None


def changed_sources(marker, sources, project, number=None):
    """Avisos de fonte que mudou desde o export que `LATEST` aponta; nunca bloqueia.

    `number` é o número planejado do export novo, citado no aviso quando vem.
    """
    if not marker:
        return []
    project = Path(project).expanduser().resolve()
    by_id = {row.get("media_id"): row for row in (marker.get("media") or {}).values() if isinstance(row, dict)}
    now = f"o {folder_name(number)}" if number is not None else "este export"
    warnings = []
    for media_id, source in sources.items():
        before = by_id.get(media_id)
        if before is None or source.get("method") == "plugin" or export_place.same_as_before(source, before):
            continue
        path = Path(source["path"])
        shown = path.relative_to(project).as_posix() if path.is_relative_to(project) else path.name
        warnings.append(f"{shown} mudou depois do export {marker.get('number')}: {now} usa a versão atual")
    return warnings


def _remove_file(path, source):
    """Apaga um arquivo do staging; hardlink congelado (Windows) volta a ser somente-leitura na origem.

    O recongelamento roda mesmo quando o `unlink` falha de novo depois do degelo.
    """
    try:
        path.unlink()
    except PermissionError:
        try:
            delivery._thaw_unlink(path)
        finally:
            if source is not None:
                delivery._freeze(source, "hardlink")


def remove_staging(staging, sources_by_rel=None):
    """Remove um staging do core sem nunca deixar um clipe ligado com escrita; nunca segue link.

    O marcador sai por último, logo antes da pasta: se algo falhar no meio, o que sobra
    continua marcado e o próximo export o varre.
    """
    staging = Path(staging)
    sources_by_rel = sources_by_rel or {}
    marker = staging / MARKER
    for current, dirs, files in os.walk(staging, topdown=False):
        folder = Path(current)
        for name in files:
            path = folder / name
            if path == marker:
                continue
            _remove_file(path, sources_by_rel.get(path.relative_to(staging).as_posix()))
        for name in dirs:
            path = folder / name
            if path.is_symlink():
                path.unlink()
            else:
                path.rmdir()
    marker.unlink(missing_ok=True)
    staging.rmdir()


def _relative_path(base, relative):
    """`base/relative` para um relativo POSIX limpo (sem `..`, sem absoluto); None no resto."""
    if not isinstance(relative, str) or not relative.isprintable() or "\\" in relative:
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return None
    return base.joinpath(*pure.parts)


def _same_file(a, b):
    try:
        first, second = a.lstat(), b.lstat()
    except OSError:
        return False
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _recorded_sources(root, staging, marker):
    """`{destino: fonte absoluta}` dos hardlinks que o marcador do staging registrou.

    Só vale fonte dentro do projeto que ainda é o mesmo inode do arquivo no staging: um
    marcador editado à mão nunca faz a varredura congelar outro arquivo.
    """
    project = root.parent.parent
    found = {}
    media = marker.get("media")
    for dest, row in media.items() if isinstance(media, dict) else ():
        source = _relative_path(project, row.get("refreeze_source") if isinstance(row, dict) else None)
        placed = _relative_path(staging, dest)
        if source is not None and placed is not None and _same_file(source, placed):
            found[dest] = str(source)
    return found


def _reason(exc):
    """Motivo curto de um OSError, sem o caminho absoluto que `str(exc)` traz."""
    return exc.strerror or type(exc).__name__


def sweep_abandoned(root):
    """Remove `.staging-*` abandonado que tem o marcador do core e `LATEST.tmp-*` perdido; o resto fica, com aviso."""
    warnings = []
    if not root.is_dir():
        return warnings
    for path in sorted(root.iterdir()):
        shown = f"{EXPORTS_DIR}/{root.name}/{path.name}"
        if LATEST_TMP_RE.fullmatch(path.name):
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
            except OSError as exc:
                warnings.append(f"{shown} sobrou de um export anterior e não consegui apagar ({_reason(exc)})")
            continue
        if not path.name.startswith(STAGING_PREFIX):
            continue
        marker = None if loader._is_link(path) or not path.is_dir() else _read_marker(path / MARKER)
        if marker is None:
            warnings.append(f"{shown} não tem o marcador do get-brolls: ficou onde está (apague se for seu)")
            continue
        try:
            remove_staging(path, _recorded_sources(root, path, marker))
        except OSError as exc:
            warnings.append(
                f"{shown} é um export abandonado do get-brolls, mas não consegui apagar ({_reason(exc)}): "
                "ficou marcado, apague à mão quando puder"
            )
    return warnings


def _write_new(path, data):
    """Cria o arquivo (nunca sobrescreve, nunca segue link, nunca cria pasta) e grava tudo até o disco."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_BINARY, 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _utf8(text, where):
    """Bytes UTF-8 do texto; surrogate solto (transcrição quebrada) vira erro claro, nunca UnicodeEncodeError."""
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(
            f"{where} tem um caractere que não existe em UTF-8 (surrogate solto): corrija a origem do texto e repita."
        ) from None


def _inside(staging, relative):
    """Caminho dentro do staging; relativo POSIX sem `..` (o validador do SDK já confere o resto)."""
    pure = PurePosixPath(relative)
    # `isprintable()` recusa NUL, controle e surrogate solto (que o `os.open` não codifica).
    bad = pure.is_absolute() or ".." in pure.parts or "\\" in relative or not relative.isprintable()
    if bad or not pure.parts:
        raise ValueError(f"Caminho de export inválido: {relative!r}.")
    if len(pure.parts) == 1 and relative.casefold() in RESERVED_NAMES:
        raise ValueError(f"{relative!r} é um nome reservado do get-brolls na pasta do export: use outro nome.")
    return staging.joinpath(*pure.parts)


def _marker_bytes(base, state, number, media):
    # A identidade do marcador vem depois da base: `marker_base` nunca a sobrescreve.
    body = {**base, "marker": MARKER_ID, "state": state, "number": folder_name(number), "media": media}
    return _utf8(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "O marcador do export")


def _rewrite_marker(staging, base, state, number, media):
    """Troca o marcador de uma vez (temporário + `os.replace`); nunca recria o staging que sumiu."""
    tmp = staging / f"{MARKER}.tmp"
    _write_new(tmp, _marker_bytes(base, state, number, media))
    tmp.replace(staging / MARKER)


def _refreeze_source(root, source):
    """Caminho da fonte relativo ao projeto, para a varredura recongelar um hardlink; None fora do projeto."""
    if source["method"] != "hardlink":
        return None
    path, project = Path(source["path"]), root.parent.parent
    return path.relative_to(project).as_posix() if path.is_relative_to(project) else None


def _moved(wanted, number):
    """Aviso quando outra pasta ocupou o número planejado: o EXPORT.md cita o número antigo."""
    if number == wanted:
        return []
    old, new = folder_name(wanted), folder_name(number)
    return [
        f"a pasta {old} apareceu durante o export: este ficou em {new}; nos comandos do EXPORT.md, troque {old} por {new}"
    ]


def _taken(target):
    return target.exists() or target.is_symlink()


def _rename_free(staging, target):
    """True quando o staging virou `target`; False quando o número já está ocupado.

    Só colisão de verdade (`FileExistsError`, EEXIST, ENOTEMPTY ou o alvo agora existe)
    passa ao próximo número. Pasta em uso (`PermissionError`) tenta o mesmo número de
    novo e depois para com uma mensagem honesta; outro erro para com o motivo.
    """
    for _ in range(BUSY_ATTEMPTS):
        if _taken(target):
            return False
        try:
            staging.rename(target)
        except OSError as exc:
            if isinstance(exc, FileExistsError) or exc.errno in (errno.EEXIST, errno.ENOTEMPTY) or _taken(target):
                return False
            if not isinstance(exc, PermissionError):
                raise ValueError(f"Não consegui renomear a pasta do export ({_reason(exc)}): rode de novo.") from None
            time.sleep(BUSY_WAIT_S)
        else:
            return True
    raise ValueError(BUSY_MESSAGE)


def _promote(root, staging, number, marker):
    """Renomeia o staging para o próximo número livre (até 5 tentativas); devolve (número, avisos)."""
    base, media = marker
    wanted = number
    for _ in range(RENAME_ATTEMPTS):
        _rewrite_marker(staging, base, "complete", number, media)
        if _rename_free(staging, root / folder_name(number)):
            return number, _moved(wanted, number)
        number += 1
    raise ValueError(
        f"Não consegui criar a pasta do export: {RENAME_ATTEMPTS} números seguidos apareceram no meio. Repita."
    )


def _write_latest(root, number):
    """Aponta `LATEST` para o export novo; falha aqui é aviso (o export já está na pasta numerada)."""
    path = root / LATEST
    shown = f"{EXPORTS_DIR}/{root.name}/{LATEST}"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return [f"{shown} não é um arquivo: não mexi nele"]
    tmp = root / f"{LATEST}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        _write_new(tmp, (folder_name(number) + "\n").encode("utf-8"))
        tmp.replace(path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        warning = (
            f"não consegui atualizar {shown} ({_reason(exc)}): o export novo é o {folder_name(number)} "
            "e o LATEST ficou como estava: não mexi nele"
        )
        return [warning]
    return []


def write_export(root, number, content, marker_base, place):
    """Grava um export novo e devolve `{"number", "media", "copied_bytes", "warnings", "latest"}`.

    `latest` diz se `LATEST` passou a apontar para ele (falha ali é só aviso).

    `content` = `{"files": {relpath: texto}, "plan": texto | None, "placements": [(media_id, dest, fonte)]}`;
    `plan` vira `getbrolls-plan.json`, logo depois do marcador;
    `place(fonte, destino_absoluto) -> (método, bytes copiados)` põe cada mídia.
    """
    warnings = sweep_abandoned(root)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f"{STAGING_PREFIX}{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    sources_by_rel = {}
    try:
        _write_new(staging / MARKER, _marker_bytes(marker_base, "staging", number, {}))
        if content.get("plan") is not None:
            _write_new(staging / PLAN_FILE, _utf8(content["plan"], "O plano do export"))
        for relative, text in content["files"].items():
            target = _inside(staging, relative)
            data = _utf8(text, f"O texto de {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_new(target, data)
        media, placed, copied = {}, [], 0
        for media_id, dest, source in content["placements"]:
            target = _inside(staging, dest)
            refreeze = _refreeze_source(root, source)
            if source["method"] == "hardlink":
                sources_by_rel[dest] = source["path"]
                # Registrado antes de ligar: a varredura acha a fonte mesmo se este processo morrer.
                media[dest] = {"media_id": media_id, "refreeze_source": refreeze}
                _rewrite_marker(staging, marker_base, "staging", number, media)
            method, size = place(source, target)
            copied += size
            media[dest] = {
                "media_id": media_id, "method": method, "source_ino": source["st_ino"],
                "source_mtime_ns": source.get("st_mtime_ns"), "source_size": source["st_size"],
                "refreeze_source": refreeze,
            }  # fmt: skip
            placed.append({"media_id": media_id, "dest": dest, "method": method})
        number, moved = _promote(root, staging, number, (marker_base, media))
    finally:
        if staging.exists():
            # Se nem isso der, o staging fica com o marcador e o próximo export real o varre.
            with contextlib.suppress(OSError):
                remove_staging(staging, sources_by_rel)
    latest = _write_latest(root, number)
    warnings += moved + latest
    return {
        "number": folder_name(number), "media": placed, "copied_bytes": copied, "warnings": warnings,
        "latest": not latest,
    }  # fmt: skip
