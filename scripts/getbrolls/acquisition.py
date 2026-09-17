"""Private working sources for review; final clips remain approval-gated."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .ledger import digest
from .media import probe
from .runtime import record_warning

INDEX_NAME = "index.json"


def _load_index(cache):
    path = cache / INDEX_NAME
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        record_warning(
            "SOURCE_INDEX_UNREADABLE",
            f"Índice de fontes ilegível ({type(error).__name__}); tratado como vazio.",
        )
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        record_warning(
            "SOURCE_INDEX_UNREADABLE",
            "Índice de fontes corrompido (JSON inválido); renomeado para .bad e tratado como vazio.",
        )
        try:
            path.replace(path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return {}
    return data if isinstance(data, dict) else {}


def _save_index(cache, index):
    """Atomic write via a unique temp file (mkstemp, not a fixed name shared by every writer)."""
    if not isinstance(index, dict):
        raise ValueError("Índice de fontes inválido: esperado objeto {candidato: [entradas]}.")
    path = cache / INDEX_NAME
    fd, temp_name = tempfile.mkstemp(dir=str(cache), prefix=".index-", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(index, ensure_ascii=False))
        temp.chmod(0o600)
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


def _covers(entry, start, end):
    entry_start = entry.get("start")
    entry_duration = entry.get("duration")
    if entry_start is None or entry_duration is None:
        return False
    try:
        return entry_start <= start and end <= entry_start + entry_duration + 0.05
    except TypeError:
        return False


def _reuse_from_index(cache, candidate_id, start, end):
    """A prior segment for this candidate — any of them, not just the last one — that already
    covers [start, end]; None when nothing qualifies or the file no longer checks out."""
    index = _load_index(cache)
    for entry in index.get(candidate_id, []):
        if not isinstance(entry, dict) or not _covers(entry, start, end):
            continue
        path = Path(entry.get("path") or "")
        if not path.is_file():
            continue
        if digest(path) != entry.get("sha"):
            record_warning(
                "SOURCE_CACHE_STALE",
                f"Cache de fonte para {candidate_id} tem sha divergente; ignorado, não reutilizado.",
            )
            continue
        return entry
    return None


def prepare_source(ledger, candidate, start, end):
    c = candidate
    remote = c["provider"] != "local"
    if not remote:
        return
    path = c.get("local_path")
    if path and Path(path).is_file():
        if digest(path) != c["local_sha256"]:
            raise ValueError("Fonte de trabalho alterada; importe novamente antes de revisar.")
        offset = c.get("local_start_s", 0)
        duration = c.get("local_duration_s")
        if duration is not None and start >= offset and end <= offset + duration + 0.05:
            return
    cache = ledger.root.parent / ".getbrolls-sources"
    cache.mkdir(exist_ok=True)
    reused = _reuse_from_index(cache, c["id"], start, end)
    if reused is not None:
        info = probe(reused["path"])
        c.update(
            local_path=str(Path(reused["path"]).resolve()),
            local_sha256=reused["sha"],
            local_start_s=reused["start"],
            local_duration_s=reused["duration"],
        )
        c["media"].update(width=info["width"], height=info["height"], fps=info["fps"])
        return
    with tempfile.TemporaryDirectory(dir=cache) as work:
        target = Path(work) / "source.mp4"
        if c["acquisition"].get("method") == "yt-dlp":
            from .social import download_segment

            download_segment(c["source_url"], target, start, end)
            offset = start
        elif c["acquisition"].get("method") == "https":
            from .http import download
            from .providers import refresh

            fresh = refresh(c)
            if not fresh.get("media_url"):
                raise ValueError("Arquivo do provedor não está mais disponível.")
            download(fresh["media_url"], target)
            offset = 0
        else:
            method = c["acquisition"].get("method")
            raise ValueError(f"Esta fonte requer importação do original local (method={method!r}).")
        info = probe(target)
        if end - offset > info["duration_s"] + 0.1:
            raise ValueError("Original não contém o intervalo solicitado.")
        sha = digest(target)
        final = cache / (hashlib.sha256(c["id"].encode()).hexdigest()[:16] + "-" + sha + ".mp4")
        if not final.exists():
            os.replace(target, final)
        elif digest(final) != sha:
            raise ValueError("Cache de mídia inconsistente; não foi sobrescrito.")
    c.update(
        local_path=str(final.resolve()), local_sha256=sha, local_start_s=offset, local_duration_s=info["duration_s"]
    )
    c["media"].update(width=info["width"], height=info["height"], fps=info["fps"])
    index = _load_index(cache)
    entries = index.setdefault(c["id"], [])
    entries[:] = [e for e in entries if e.get("sha") != sha]
    entries.append(
        {
            "path": str(final.resolve()),
            "sha": sha,
            "start": offset,
            "duration": info["duration_s"],
        }
    )
    _save_index(cache, index)
