"""Pastas de export numeradas: `exports/<exporter>/001/`, `002/`… e `LATEST`.

Cada `gb export` grava uma pasta nova. O core nunca apaga, substitui nem mescla uma
pasta numerada: render, transcrição, Studio e edição à mão dentro dela são da pessoa.
A escrita acontece em `exports/<exporter>/.staging-<uuid8>/` (mesmo volume), com o
marcador do core como primeiro arquivo; no fim, `os.rename` para o próximo número e
`LATEST` passa a apontar para ela. Falha no meio remove só o staging desta execução.
"""

import contextlib
import json
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath

from . import delivery, export_place
from .sdk import loader

EXPORTS_DIR = "exports"
MARKER = ".getbrolls-export.json"
MARKER_ID = "getbrolls-export"
LATEST = "LATEST"
STAGING_PREFIX = ".staging-"
NUMBER_RE = re.compile(r"[0-9]{3,}")
RENAME_ATTEMPTS = 5
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


def changed_sources(marker, sources, project):
    """Avisos de fonte que mudou desde o export que `LATEST` aponta; nunca bloqueia."""
    if not marker:
        return []
    project = Path(project).expanduser().resolve()
    by_id = {row.get("media_id"): row for row in (marker.get("media") or {}).values() if isinstance(row, dict)}
    warnings = []
    for media_id, source in sources.items():
        before = by_id.get(media_id)
        if before is None or source.get("method") == "plugin" or export_place.same_as_before(source, before):
            continue
        path = Path(source["path"])
        shown = path.relative_to(project).as_posix() if path.is_relative_to(project) else path.name
        warnings.append(f"{shown} mudou depois do export {marker.get('number')}: este export usa a versão atual")
    return warnings


def _remove_file(path, source):
    """Apaga um arquivo do staging; hardlink congelado (Windows) volta a ser somente-leitura na origem."""
    try:
        path.unlink()
    except PermissionError:
        delivery._thaw_unlink(path)
        if source is not None:
            delivery._freeze(source, "hardlink")


def remove_staging(staging, sources_by_rel=None):
    """Remove um staging do core sem nunca deixar um clipe ligado com escrita; nunca segue link."""
    sources_by_rel = sources_by_rel or {}
    for current, dirs, files in os.walk(staging, topdown=False):
        folder = Path(current)
        for name in files:
            path = folder / name
            _remove_file(path, sources_by_rel.get(path.relative_to(staging).as_posix()))
        for name in dirs:
            path = folder / name
            if path.is_symlink():
                path.unlink()
            else:
                path.rmdir()
    Path(staging).rmdir()


def sweep_abandoned(root):
    """Remove `.staging-*` abandonado que tem o marcador do core; o resto fica, com aviso."""
    warnings = []
    if not root.is_dir():
        return warnings
    for path in sorted(root.iterdir()):
        if not path.name.startswith(STAGING_PREFIX):
            continue
        if loader._is_link(path) or not path.is_dir() or _read_marker(path / MARKER) is None:
            warnings.append(
                f"{EXPORTS_DIR}/{root.name}/{path.name} não tem o marcador do get-brolls: ficou onde está (apague se for seu)"
            )
            continue
        remove_staging(path)
    return warnings


def _write_new(path, data):
    """Cria o arquivo (nunca sobrescreve nem segue link) e grava todos os bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_BINARY, 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


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
    return staging.joinpath(*pure.parts)


def _marker_bytes(base, state, number, media):
    body = {"marker": MARKER_ID, "state": state, **base, "number": folder_name(number), "media": media}
    return _utf8(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "O marcador do export")


def _replace_marker(staging, base, number, media):
    tmp = staging / f"{MARKER}.tmp"
    _write_new(tmp, _marker_bytes(base, "complete", number, media))
    tmp.replace(staging / MARKER)


def _moved(wanted, number):
    """Aviso quando outra pasta ocupou o número planejado: o EXPORT.md cita o número antigo."""
    if number == wanted:
        return []
    old, new = folder_name(wanted), folder_name(number)
    return [
        f"a pasta {old} apareceu durante o export: este ficou em {new}; nos comandos do EXPORT.md, troque {old} por {new}"
    ]


def _promote(root, staging, number, marker):
    """Renomeia o staging para o próximo número livre (até 5 tentativas); devolve (número, avisos)."""
    base, media = marker
    wanted = number
    for _ in range(RENAME_ATTEMPTS):
        target = root / folder_name(number)
        _replace_marker(staging, base, number, media)
        if not target.exists() and not target.is_symlink():
            try:
                staging.rename(target)
            except OSError:
                pass
            else:
                return number, _moved(wanted, number)
        number += 1
    raise ValueError(
        f"Não consegui criar a pasta do export: {RENAME_ATTEMPTS} números seguidos apareceram no meio. Repita."
    )


def _write_latest(root, number):
    path = root / LATEST
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return [f"{EXPORTS_DIR}/{root.name}/{LATEST} não é um arquivo: não mexi nele"]
    tmp = root / f"{LATEST}.tmp-{uuid.uuid4().hex[:8]}"
    _write_new(tmp, (folder_name(number) + "\n").encode("utf-8"))
    tmp.replace(path)
    return []


def write_export(root, number, content, marker_base, place):
    """Grava um export novo e devolve `{"number", "media", "copied_bytes", "warnings"}`.

    `content` = `{"files": {relpath: texto}, "placements": [(media_id, dest, fonte)]}`;
    `place(fonte, destino_absoluto) -> (método, bytes copiados)` põe cada mídia.
    """
    warnings = sweep_abandoned(root)
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f"{STAGING_PREFIX}{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    sources_by_rel = {}
    try:
        _write_new(staging / MARKER, _marker_bytes(marker_base, "staging", number, {}))
        for relative, text in content["files"].items():
            _write_new(_inside(staging, relative), _utf8(text, f"O texto de {relative}"))
        media, placed, copied = {}, [], 0
        for media_id, dest, source in content["placements"]:
            target = _inside(staging, dest)
            if source["method"] == "hardlink":
                sources_by_rel[dest] = source["path"]
            method, size = place(source, target)
            copied += size
            media[dest] = {
                "media_id": media_id, "method": method, "source_ino": source["st_ino"],
                "source_mtime_ns": source.get("st_mtime_ns"), "source_size": source["st_size"],
            }  # fmt: skip
            placed.append({"media_id": media_id, "dest": dest, "method": method})
        number, moved = _promote(root, staging, number, (marker_base, media))
    finally:
        if staging.exists():
            # Se nem isso der, o staging fica com o marcador e o próximo export real o varre.
            with contextlib.suppress(OSError):
                remove_staging(staging, sources_by_rel)
    warnings += moved + _write_latest(root, number)
    return {"number": folder_name(number), "media": placed, "copied_bytes": copied, "warnings": warnings}
