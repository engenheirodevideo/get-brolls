"""`analysis/`: índice das mídias do projeto por conteúdo e os arquivos de análise de cada uma.

Cada mídia registrada ganha um `media_id` (os 16 primeiros hex do sha256 dos bytes) e
uma pasta `analysis/media/<media_id>/` com `media.json` (do core) e os componentes
(`transcript`, `scenes`, ...), gravados pelo core ou por plugins com permissão. O
`analysis/index.json` liga caminho, papel, `media_id` e uma chave rápida
(`quick_key`: tamanho, mtime em ns e sha256 de uma amostra de 1 MiB do início + 1 MiB
do fim) que diz, sem hashear o arquivo inteiro, se a mídia ainda é a mesma.

Regras de integridade:

- Gravação nunca segue link: desce `analysis/`, `media/` e `<media_id>/` componente
  por componente sem seguir link (o mesmo cuidado do leitor, `analysis_contract`) e
  troca o arquivo de uma vez (temporário + `replace`). Pasta que é link é recusada.
- Toda gravação passa pelo validador (`analysis_contract.check_doc`) e pela trava
  própria `analysis/.lock`, nunca pela trava do projeto: o hash completo de uma mídia
  grande roda fora de qualquer trava, e só a troca dos arquivos fica dentro dela.
- `media.json` é do core: só `ensure_media` o grava.
"""

import contextlib
import errno
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path

from . import analysis_contract as contract
from . import ledger, media, runtime, versioning, vocab
from .errors import PrerequisiteError
from .models import now
from .sdk import files

ANALYSIS_DIR = "analysis"
INDEX_FILE = "index.json"
MARKERS_FILE = "markers.json"
MEDIA_DIR = "media"
LOCK_FILE = ".lock"
SAMPLE_BYTES = 1024 * 1024
LOCK_WAIT_S = 2.0
# Abaixo disso o hash é rápido e o registro não escreve progresso no stderr.
PROGRESS_MIN_BYTES = 64 * 1024 * 1024
BUSY_MESSAGE = "Outra gravação em analysis/ está em andamento; repita em instantes."
CORE_PRODUCER = "getbrolls"
MEDIA_ID_RE = re.compile(r"[0-9a-f]{16}")
# Papel inferido pela pasta; fora delas, o papel vem de `--role`.
ROLE_BY_FOLDER = (
    ("aroll/", "aroll"),
    ("broll/", "broll"),
    ("brolls/clips/", "broll"),
    ("assets/musica/", "music"),
    ("assets/sfx/", "sfx"),
)
# Campos do cabeçalho que o core sempre define ao gravar um componente.
_FORCED = ("schema", "media_id", "producer", "created", "time_unit")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_DIR_FLAGS = os.O_RDONLY | _NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
_READ_FLAGS = os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | _BINARY
_NEW_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY


def _root(project) -> Path:
    return Path(project).expanduser().resolve()


def _version():
    from . import __version__

    return __version__


def _link_refusal(rel: str) -> ValueError:
    return ValueError(f"{rel} é um link ou não é uma pasta comum; analysis/ só grava em pastas comuns do projeto.")


# -- gravação sem seguir link --------------------------------------------------


def _dir_fd_available() -> bool:
    return all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.rename, os.unlink))


def _open_child(parent_fd: int, name: str, rel: str, create: bool) -> int:
    if create:
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, 0o755, dir_fd=parent_fd)
    try:
        child = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise _link_refusal(rel) from None
        raise
    if not stat.S_ISDIR(os.fstat(child).st_mode):
        os.close(child)
        raise _link_refusal(rel)
    return child


def _write_with_dir_fd(root: Path, parts: tuple[str, ...], data: bytes) -> None:
    fd = os.open(root, _DIR_FLAGS)
    try:
        walked = []
        for name in parts[:-1]:
            walked.append(name)
            child = _open_child(fd, name, "/".join(walked), create=True)
            os.close(fd)
            fd = child
        temp = f".{parts[-1]}.{os.getpid()}.tmp"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=fd)
        out = os.open(temp, _NEW_FLAGS, 0o644, dir_fd=fd)
        try:
            with os.fdopen(out, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(out)
        finally:
            os.close(out)
        try:
            # `rename` do POSIX troca o destino de uma vez (e troca um link, sem segui-lo).
            os.rename(temp, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(temp, dir_fd=fd)
            raise
    finally:
        os.close(fd)


def _checked_dir(path: Path, rel: str, create: bool) -> tuple[int, int]:
    if create:
        with contextlib.suppress(FileExistsError):
            path.mkdir()
    info = os.lstat(path)
    if files.is_link(path) or not stat.S_ISDIR(info.st_mode):
        raise _link_refusal(rel)
    return info.st_dev, info.st_ino


def _write_by_lstat(root: Path, parts: tuple[str, ...], data: bytes) -> None:
    """Sem `dir_fd` (Windows): `lstat` de cada pasta, gravação e conferência de identidade."""
    seen = []
    current = root
    for index, name in enumerate(parts[:-1]):
        current = current / name
        seen.append((current, _checked_dir(current, "/".join(parts[: index + 1]), create=True)))
    leaf = current / parts[-1]
    temp = leaf.with_name(f".{parts[-1]}.{os.getpid()}.tmp")
    temp.unlink(missing_ok=True)
    out = os.open(temp, _NEW_FLAGS, 0o644)
    try:
        with os.fdopen(out, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(out)
    finally:
        os.close(out)
    try:
        for path, identity in seen:
            if _checked_dir(path, path.name, create=False) != identity:
                raise _link_refusal(path.name)
        temp.replace(leaf)
    finally:
        temp.unlink(missing_ok=True)


def _write_json(project: Path, rel: str, doc: dict) -> Path:
    """Grava `doc` em `project/rel` de uma vez, sem seguir link em nenhuma pasta do caminho."""
    parts = contract.relative(rel)
    data = (json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > contract.MAX_FILE:
        raise ValueError(f"{rel} passaria de {contract.MAX_FILE // (1024 * 1024)} MiB; nada foi gravado.")
    writer = _write_with_dir_fd if _dir_fd_available() else _write_by_lstat
    writer(project, parts, data)
    return project.joinpath(*parts)


def _ensure_analysis_dir(project: Path) -> Path:
    folder = project / ANALYSIS_DIR
    _checked_dir(folder, ANALYSIS_DIR, create=True)
    return folder


@contextlib.contextmanager
def analysis_lock(project):
    """Trava exclusiva `analysis/.lock` (cria `analysis/` se faltar); ocupada vira `ValueError`."""
    folder = _ensure_analysis_dir(_root(project))
    lock = folder / LOCK_FILE
    if files.is_link(lock):
        raise _link_refusal(f"{ANALYSIS_DIR}/{LOCK_FILE}")
    with runtime.exclusive_lock(lock, BUSY_MESSAGE, wait_s=LOCK_WAIT_S):
        yield


# -- leitura -----------------------------------------------------------------


def _problem_text(rel: str, problems: list[dict]) -> str:
    listed = "; ".join(f"{p['code']} em {p['where']}" for p in problems[:5])
    return f"{rel} fora do contrato ({listed})"


def _read_doc(project: Path, rel: str, name: str) -> dict | None:
    """O documento validado em `rel`, ou `None` quando o arquivo não existe; `contract.Invalid` quando não serve."""
    try:
        doc = contract.read_json(project, rel)
    except contract.Invalid as exc:
        if exc.code == "FILE_UNAVAILABLE" and not os.path.lexists(project / rel):
            return None
        raise
    problems = contract.check_doc(doc, name)
    if problems:
        raise contract.Invalid(problems[0]["code"], f"{rel}: {problems[0]['where']}")
    return doc


def _read_index(project: Path) -> dict | None:
    rel = f"{ANALYSIS_DIR}/{INDEX_FILE}"
    try:
        return _read_doc(project, rel, "analysis_index")
    except contract.Invalid as exc:
        if exc.code in ("UNSAFE_LINK", "UNSAFE_PATH"):
            raise _link_refusal(ANALYSIS_DIR) from None
        raise ValueError(f"{rel} não dá para usar ({exc}); restaure uma cópia válida ou apague o arquivo.") from None


def _new_index() -> dict:
    return versioning.stamp_schema({"updated": now(), "media": []}, "analysis_index")


def _write_index(project: Path, index: dict) -> None:
    index = {**index, "updated": now(), "media": sorted(index["media"], key=lambda e: e["path"])}
    problems = contract.check_doc(index, "analysis_index")
    if problems:
        raise ValueError(_problem_text(f"{ANALYSIS_DIR}/{INDEX_FILE}", problems))
    _write_json(project, f"{ANALYSIS_DIR}/{INDEX_FILE}", index)


# -- a mídia no disco ----------------------------------------------------------


def _media_file(project: Path, rel: str) -> tuple[Path, os.stat_result]:
    """Caminho e `lstat` da mídia `rel`: relativa, dentro do projeto, arquivo comum, sem link no caminho."""
    try:
        parts = contract.relative(rel)
    except contract.Invalid:
        raise ValueError(
            f"--path {rel!r} tem que ser relativo ao projeto, com /, sem . nem .. (ex.: aroll/c01.mp4)."
        ) from None
    current = project
    for name in parts:
        current = current / name
        try:
            info = os.lstat(current)
        except OSError:
            raise ValueError(f"{rel} não existe no projeto.") from None
        if files.is_link(current):
            raise ValueError(f"{rel} passa por um link; registre a mídia pelo caminho real, dentro do projeto.")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{rel} não é um arquivo comum.")
    return current, info


def _read_at(fd: int, offset: int, count: int) -> bytes:
    os.lseek(fd, offset, os.SEEK_SET)
    chunks, left = [], count
    while left > 0:
        chunk = os.read(fd, min(left, SAMPLE_BYTES))
        if not chunk:
            break
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def quick_key(path: Path, info: os.stat_result, rel: str) -> dict:
    """`{size, mtime_ns, sample_sha256}` do arquivo, lendo só a amostra (início + fim)."""
    try:
        fd = os.open(path, _READ_FLAGS)
    except OSError:
        raise ValueError(f"Não consegui ler {rel}.") from None
    try:
        now_info = os.fstat(fd)
        if (now_info.st_dev, now_info.st_ino) != (info.st_dev, info.st_ino) or not stat.S_ISREG(now_info.st_mode):
            raise ValueError(f"{rel} mudou durante a leitura; repita.")
        size = now_info.st_size
        head = _read_at(fd, 0, min(size, SAMPLE_BYTES))
        tail_start = max(len(head), size - SAMPLE_BYTES)
        tail = _read_at(fd, tail_start, size - tail_start)
    except OSError:
        raise ValueError(f"Não consegui ler {rel}.") from None
    finally:
        os.close(fd)
    return {
        "size": size,
        "mtime_ns": now_info.st_mtime_ns,
        "sample_sha256": hashlib.sha256(head + tail).hexdigest(),
    }


def media_id_for_bytes(path) -> str:
    """Os 16 primeiros hex do sha256 dos bytes: a mesma derivação do `asset_id` do Nômade."""
    return ledger.digest(path)[:16]


def _same_content(key: dict, other: dict) -> bool:
    return key["size"] == other["size"] and key["sample_sha256"] == other["sample_sha256"]


def _inferred_role(rel: str) -> str | None:
    return next((role for folder, role in ROLE_BY_FOLDER if rel.startswith(folder)), None)


def _fps(value) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,9}/[1-9][0-9]{0,9}", value):
        return None
    return value


def _positive(value) -> int | None:
    return value if type(value) is int and value >= 1 else None


def _codec(stream) -> str | None:
    name = stream.get("codec_name") if stream else None
    return name[:64] if isinstance(name, str) and name else None


def _duration(data, video) -> float | None:
    raw = (data.get("format") or {}).get("duration") or (video or {}).get("duration")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return round(value, 3) if math.isfinite(value) and value >= 0 else None


_EMPTY_FACTS = {
    "duration_s": None,
    "fps": None,
    "width": None,
    "height": None,
    "has_audio": None,
    "video_codec": None,
    "audio_codec": None,
}


def _probe(path: Path) -> tuple[str, str | None, dict]:
    """(status, reason, dados técnicos) pelo ffprobe; sem ffprobe, `unavailable` com o motivo."""
    try:
        raw = media.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], op="analysis_probe"
        )
        data = json.loads(raw)
        streams = [s for s in data.get("streams") or [] if isinstance(s, dict)]
    except PrerequisiteError:
        return "unavailable", "ffprobe não encontrado; instale o FFmpeg e registre de novo.", dict(_EMPTY_FACTS)
    except (ValueError, AttributeError, TypeError):
        return "failed", "o ffprobe não conseguiu ler o arquivo.", dict(_EMPTY_FACTS)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    facts = {
        "duration_s": _duration(data, video),
        "fps": _fps((video or {}).get("avg_frame_rate")),
        "width": _positive((video or {}).get("width")),
        "height": _positive((video or {}).get("height")),
        "has_audio": audio is not None,
        "video_codec": _codec(video),
        "audio_codec": _codec(audio),
    }
    return "done", None, facts


def _media_doc(rel, sha, key, role, probed) -> dict:
    """O `media.json` validado da mídia (o core é o único produtor dele)."""
    status, reason, facts = probed
    doc = versioning.stamp_schema(
        {
            "media_id": sha[:16],
            "status": status,
            "reason": reason,
            "producer": {"tool": CORE_PRODUCER, "model": None, "version": _version()},
            "created": now(),
            "time_unit": "s",
            "path": rel,
            "sha256": sha,
            "size_bytes": key["size"],
            "quick_key": key,
            "role": role,
            **facts,
        },
        "media",
    )
    problems = contract.check_doc(doc, "media")
    if problems:
        raise ValueError(_problem_text(_media_rel(sha[:16], "media"), problems))
    return doc


def _media_rel(media_id: str, name: str) -> str:
    return f"{ANALYSIS_DIR}/{MEDIA_DIR}/{media_id}/{name}.json"


def _check_media_id(media_id) -> str:
    if not isinstance(media_id, str) or not MEDIA_ID_RE.fullmatch(media_id):
        raise ValueError("media_id tem que ter 16 caracteres hexadecimais minúsculos (ex.: 9f86d081884c7d65).")
    return media_id


def _entry(index: dict | None, *, path=None, media_id=None) -> dict | None:
    for entry in (index or {}).get("media", []):
        if (path is not None and entry["path"] == path) or (media_id is not None and entry["media_id"] == media_id):
            return entry
    return None


def _renew(project: Path, rel: str, key: dict, role: str) -> dict:
    """Mesmo tamanho e mesma amostra: só atualiza mtime e papel, sem hash completo."""
    with analysis_lock(project):
        index = _read_index(project) or _new_index()
        entry = _entry(index, path=rel)
        if entry is None or not _same_content(key, entry["quick_key"]):
            return {}
        entry["quick_key"], entry["role"] = key, role
        try:
            doc = _read_doc(project, _media_rel(entry["media_id"], "media"), "media")
        except contract.Invalid:
            doc = None
        if doc is not None:
            _write_json(project, _media_rel(entry["media_id"], "media"), {**doc, "quick_key": key, "role": role})
        _write_index(project, index)
        return dict(entry)


def _full_hash(path: Path, info: os.stat_result, rel: str, progress) -> str:
    if progress is not None:
        progress(rel, info.st_size)
    try:
        sha = ledger.digest(path)
    except OSError:
        raise ValueError(f"Não consegui ler {rel}.") from None
    after = os.lstat(path)
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
    ):
        raise ValueError(f"{rel} mudou enquanto era lido; repita o registro quando a cópia terminar.")
    return sha


def _place(project: Path, index: dict, doc: dict) -> dict:
    """Entrada do índice para o `media.json` novo; mesma mídia viva em outro caminho é recusada.

    Os componentes já gravados para o mesmo `media_id` continuam valendo (a mídia é a
    mesma, só o caminho mudou); a entrada antiga do caminho, se era outra mídia, sai.
    """
    rel, media_id = doc["path"], doc["media_id"]
    twin = _entry(index, media_id=media_id)
    if twin is not None and twin["path"] != rel and os.path.lexists(project / twin["path"]):
        raise ValueError(
            f"{rel} tem os mesmos bytes de {twin['path']}, já registrada; registre um arquivo só ou apague a cópia."
        )
    components = dict(twin["components"]) if twin else {}
    components["media"] = doc["status"]
    index["media"] = [e for e in index["media"] if e["path"] != rel and e["media_id"] != media_id]
    entry = {"media_id": media_id, "path": rel, "role": doc["role"], "quick_key": doc["quick_key"]}
    entry["components"] = components
    index["media"].append(entry)
    return entry


def ensure_media(project, rel, role=None, *, probe=True, progress=None) -> dict:
    """Registra a mídia `rel` no índice (hash só quando a chave rápida não bate) e devolve a entrada.

    A entrada devolvida traz `hashed` (se o sha256 inteiro foi calculado agora). O hash
    completo e o ffprobe rodam fora da trava; `progress(rel, bytes)` é chamado antes do hash.
    """
    project = _root(project)
    path, info = _media_file(project, rel)
    key = quick_key(path, info, rel)
    index = _read_index(project)
    known = _entry(index, path=rel)
    role = role or _inferred_role(rel) or (known["role"] if known else None)
    if role is not None and role not in vocab.MEDIA_ROLES:
        raise ValueError(f"Papel {role!r} desconhecido; use um de: {', '.join(vocab.MEDIA_ROLES)}.")
    if role is None:
        raise ValueError(
            f"Não sei o papel de {rel} pela pasta; passe --role (aroll, broll, footage, music, sfx, narration, "
            "title, animation ou unknown)."
        )
    if known is not None and _same_content(key, known["quick_key"]):
        if known["quick_key"]["mtime_ns"] == key["mtime_ns"] and known["role"] == role:
            return {**known, "hashed": False}
        renewed = _renew(project, rel, key, role)
        if renewed:
            return {**renewed, "hashed": False}
    sha = _full_hash(path, info, rel, progress)
    unprobed = ("not_run_by_this_script", "registrado sem ffprobe.", dict(_EMPTY_FACTS))
    doc = _media_doc(rel, sha, key, role, _probe(path) if probe else unprobed)
    with analysis_lock(project):
        index = _read_index(project) or _new_index()
        entry = _place(project, index, doc)
        _write_json(project, _media_rel(entry["media_id"], "media"), doc)
        _write_index(project, index)
    return {**entry, "hashed": True}


def match(project, rel) -> tuple[str, dict | None]:
    """(`"fresh"` | `"stale"` | `"absent"`, entrada) de `rel`, sem nunca hashear o arquivo inteiro.

    `stale`: há entrada, mas o arquivo mudou (ou sumiu) desde o registro.
    """
    project = _root(project)
    try:
        index = _read_index(project)
    except ValueError:
        return "absent", None
    entry = _entry(index, path=rel)
    if entry is None:
        return "absent", None
    try:
        path, info = _media_file(project, rel)
    except ValueError:
        return "stale", entry
    saved = entry["quick_key"]
    if (info.st_size, info.st_mtime_ns) == (saved["size"], saved["mtime_ns"]):
        return "fresh", entry
    try:
        key = quick_key(path, info, rel)
    except ValueError:
        return "stale", entry
    return ("fresh" if _same_content(key, saved) else "stale"), entry


def lookup(project, rel) -> dict | None:
    """A entrada do índice de `rel` quando a chave rápida ainda confere; nunca hashea o arquivo inteiro."""
    state, entry = match(project, rel)
    return entry if state == "fresh" else None


def read_component(project, media_id, name) -> dict | None:
    """O componente `name` da mídia, validado; `None` sem arquivo; `contract.Invalid` quando não serve."""
    if name not in contract.COMPONENTS:
        raise ValueError(f"Componente de análise desconhecido: {name}.")
    rel = _media_rel(_check_media_id(media_id), name)
    doc = _read_doc(_root(project), rel, name)
    if doc is not None and doc["media_id"] != media_id:
        raise contract.Invalid("MEDIA_ID_MISMATCH", f"{rel}: $.media_id")
    return doc


def _plain(doc, label: str):
    """Cópia só com tipos de JSON, pelas regras do leitor (NaN, segredo, caminho absoluto recusados)."""
    try:
        text = json.dumps(doc, allow_nan=True)
        return contract.parse(('{"value":' + text + "}").encode("utf-8"), label)["value"]
    except contract.Invalid as exc:
        raise ValueError(f"{label} recusado ({exc}).") from None
    except (TypeError, ValueError, RecursionError):
        raise ValueError(f"{label} tem que ser um objeto JSON.") from None


def write_component(project, media_id, name, doc, *, producer) -> Path:
    """Grava `analysis/media/<media_id>/<name>.json`: cabeçalho forçado, validado, troca atômica.

    O core define `schema`, `media_id`, `producer`, `created` e `time_unit`. A mídia tem
    que estar no índice, e `media.json` não passa por aqui (só `ensure_media` o grava).
    """
    if name == "media":
        raise ValueError("media.json é do core: registre a mídia com analysis --action register.")
    if name not in contract.COMPONENTS:
        raise ValueError(f"Componente de análise desconhecido: {name}.")
    rel = _media_rel(_check_media_id(media_id), name)
    body = _plain(doc, rel)
    if not isinstance(body, dict):
        raise ValueError(f"{rel} tem que ser um objeto JSON.")
    header = {"media_id": media_id, "producer": dict(producer), "created": now(), "time_unit": "s"}
    full = versioning.stamp_schema({**{k: v for k, v in body.items() if k not in _FORCED}, **header}, name)
    problems = contract.check_doc(full, name)
    if problems:
        raise ValueError(_problem_text(rel, problems))
    project = _root(project)
    with analysis_lock(project):
        index = _read_index(project)
        entry = _entry(index, media_id=media_id)
        if index is None or entry is None:
            raise ValueError(
                f"media_id {media_id} não está em analysis/index.json; rode analysis --action register --path "
                "<mídia> antes."
            )
        written = _write_json(project, rel, full)
        entry["components"][name] = full["status"]
        _write_index(project, index)
    return written


def _marker_order(item):
    return item["media_id"], item["start"], item["id"]


def write_markers(project, producer, markers) -> Path:
    """Troca os marcadores de `producer["tool"]` em `analysis/markers.json`, mantendo os dos outros."""
    rel = f"{ANALYSIS_DIR}/{MARKERS_FILE}"
    items = _plain(markers, rel)
    if not isinstance(items, list) or any(not isinstance(m, dict) for m in items):
        raise ValueError(f"{rel}: os marcadores têm que ser uma lista de objetos.")
    tool = producer["tool"]
    fresh = [{**item, "producer": dict(producer)} for item in items]
    project = _root(project)
    with analysis_lock(project):
        index = _read_index(project)
        for item in fresh:
            if _entry(index, media_id=item.get("media_id")) is None:
                raise ValueError(
                    f"{rel}: media_id {item.get('media_id')!r} não está em analysis/index.json; rode analysis "
                    "--action register --path <mídia> antes."
                )
        try:
            current = _read_doc(project, rel, "markers")
        except contract.Invalid as exc:
            raise ValueError(
                f"{rel} não dá para usar ({exc}); restaure uma cópia válida ou apague o arquivo."
            ) from None
        kept = [m for m in (current or {}).get("markers", []) if m["producer"]["tool"] != tool]
        doc = versioning.stamp_schema(
            {"updated": now(), "time_unit": "s", "markers": sorted(kept + fresh, key=_marker_order)}, "markers"
        )
        problems = contract.check_doc(doc, "markers")
        if problems:
            raise ValueError(_problem_text(rel, problems))
        return _write_json(project, rel, doc)


# -- conferência e listagem ----------------------------------------------------


def _check_file(project: Path, rel: str, name: str, problems: list, media_id=None) -> dict | None:
    try:
        doc = contract.read_json(project, rel)
    except contract.Invalid as exc:
        problems.append({"path": rel, "code": exc.code, "where": exc.where})
        return None
    found = contract.check_doc(doc, name)
    problems.extend({"path": rel, **p} for p in found)
    if not found and media_id is not None and doc.get("media_id") != media_id:
        problems.append({"path": rel, "code": "MEDIA_ID_MISMATCH", "where": "$.media_id"})
    return None if found else doc


def _scan(folder: Path, rel: str, problems: list) -> list[os.DirEntry]:
    """Entradas de `folder` sem seguir link; link e item fora do lugar viram problema."""
    with os.scandir(folder) as found:
        entries = sorted(found, key=lambda e: e.name)
    kept = []
    for entry in entries:
        if entry.name == LOCK_FILE or (entry.name.startswith(".") and entry.name.endswith(".tmp")):
            continue
        if files.is_link(Path(entry.path)):
            problems.append({"path": f"{rel}/{entry.name}", "code": "UNSAFE_LINK", "where": "$"})
            continue
        kept.append(entry)
    return kept


def _check_media_dir(project: Path, folder: Path, problems: list) -> list[str]:
    checked = []
    base = f"{ANALYSIS_DIR}/{MEDIA_DIR}"
    for entry in _scan(folder, base, problems):
        rel = f"{base}/{entry.name}"
        if not entry.is_dir(follow_symlinks=False) or not MEDIA_ID_RE.fullmatch(entry.name):
            problems.append({"path": rel, "code": "UNKNOWN_FILE", "where": "$"})
            continue
        for item in _scan(Path(entry.path), rel, problems):
            name = item.name.removesuffix(".json")
            item_rel = f"{rel}/{item.name}"
            if not item.name.endswith(".json") or name not in contract.COMPONENTS:
                problems.append({"path": item_rel, "code": "UNKNOWN_FILE", "where": "$"})
                continue
            checked.append(item_rel)
            _check_file(project, item_rel, name, problems, media_id=entry.name)
    return checked


def check_all(project) -> dict:
    """Confere todo arquivo de `analysis/` sem gravar nada: `{"ok", "files", "problems"}`."""
    project = _root(project)
    folder = project / ANALYSIS_DIR
    problems: list[dict] = []
    checked: list[str] = []
    if not os.path.lexists(folder):
        return {"ok": True, "files": [], "problems": []}
    if files.is_link(folder) or not folder.is_dir():
        return {"ok": False, "files": [], "problems": [{"path": ANALYSIS_DIR, "code": "UNSAFE_LINK", "where": "$"}]}
    index = None
    for entry in _scan(folder, ANALYSIS_DIR, problems):
        rel = f"{ANALYSIS_DIR}/{entry.name}"
        if entry.name == INDEX_FILE:
            checked.append(rel)
            index = _check_file(project, rel, "analysis_index", problems)
        elif entry.name == MARKERS_FILE:
            checked.append(rel)
            _check_file(project, rel, "markers", problems)
        elif entry.name == MEDIA_DIR and entry.is_dir(follow_symlinks=False):
            checked.extend(_check_media_dir(project, Path(entry.path), problems))
        else:
            problems.append({"path": rel, "code": "UNKNOWN_FILE", "where": "$"})
    problems.extend(
        {"path": f"{ANALYSIS_DIR}/{INDEX_FILE}", "code": "MISSING_FILE", "where": f"$.media[{position}]"}
        for position, item in enumerate((index or {}).get("media", []))
        for name in item["components"]
        if _media_rel(item["media_id"], name) not in checked
    )
    return {"ok": not problems, "files": checked, "problems": problems}


def listing(project) -> list[dict]:
    """As mídias do índice com o estado de cada uma (`fresh`, `stale`), sem hashear nem gravar."""
    project = _root(project)
    index = _read_index(project)
    rows = []
    for entry in (index or {}).get("media", []):
        state, _ = match(project, entry["path"])
        rows.append(
            {
                "media_id": entry["media_id"],
                "path": entry["path"],
                "role": entry["role"],
                "components": entry["components"],
                "state": state,
            }
        )
    return rows


# -- comando -------------------------------------------------------------------


def _progress(rel: str, size: int) -> None:
    """Uma linha JSON no stderr antes do hash de uma mídia grande (o stdout fica só com o resultado)."""
    if size < PROGRESS_MIN_BYTES:
        return
    line = {
        "progress": "analysis_register",
        "path": rel,
        "size_bytes": size,
        "message": f"Calculando o sha256 de {rel} ({size / (1024 * 1024):.0f} MiB); pode levar alguns minutos.",
    }
    sys.stderr.write(json.dumps(line, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def run(args) -> dict:
    """`analysis --action list|check|register`: nenhuma saída traz caminho absoluto."""
    project = _root(args.project)
    if args.action == "list":
        rows = listing(project)
        line = (
            f"{len(rows)} mídia(s) em analysis/index.json."
            if rows
            else "Nenhuma mídia registrada; rode analysis --action register --path aroll/c01.mp4."
        )
        return {"media": rows, "summary": {"line": line}}
    if args.action == "check":
        report = check_all(project)
        line = (
            f"analysis/ confere: {len(report['files'])} arquivo(s)."
            if report["ok"]
            else f"analysis/ tem {len(report['problems'])} problema(s); veja problems."
        )
        return {**report, "summary": {"line": line}}
    if not getattr(args, "path", None):
        raise ValueError("analysis --action register precisa de --path com a mídia, relativo ao projeto.")
    entry = ensure_media(project, args.path, getattr(args, "role", None), progress=_progress)
    verb = "registrada" if entry["hashed"] else "já estava registrada"
    return {
        **entry,
        "summary": {"line": f"{entry['path']} {verb} como {entry['media_id']} ({entry['role']})."},
    }
