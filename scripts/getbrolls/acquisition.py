"""Private working sources for review; final clips remain approval-gated."""

import contextlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

from . import logs
from .ledger import digest
from .media import probe
from .models import id_stem
from .runtime import record_warning

INDEX_NAME = "index.json"

log = logs.get("acquisition")


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
        with contextlib.suppress(OSError):
            path.replace(path.with_name(path.name + ".bad"))
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


def _ensure_private_cache_dir(cache):
    """Create/reuse `.getbrolls-sources/` as 0700, same as `social.py`'s cache.

    `mkdir(exist_ok=True)` only applies `mode` to a directory it actually creates;
    an already-existing (looser) directory from before this fix would stay as-is
    without the explicit `chmod` below.
    """
    cache.mkdir(mode=0o700, exist_ok=True)
    cache.chmod(0o700)


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
            logs.event(log, logging.INFO, "source_cache", candidate=candidate_id, result="stale", reason="sha_mismatch")
            continue
        logs.event(log, logging.INFO, "source_cache", candidate=candidate_id, result="hit", reason="covers_range")
        return entry
    logs.event(log, logging.INFO, "source_cache", candidate=candidate_id, result="miss", reason="no_match")
    return None


def direct_media(candidate):
    """A fonte publica o arquivo direto (mp4/jpg) em vez de uma página para o yt-dlp?

    É o caso do acervo da NASA e dos bancos de imagem: `source_url` é a página do
    item, e mandá-la ao yt-dlp devolve "Unsupported URL". Quem tem `media_url` e não
    é rota de yt-dlp se lê pelo próprio arquivo.
    """
    return bool(candidate.get("media_url")) and (candidate.get("acquisition") or {}).get("method") != "yt-dlp"


def cache_direct_media(ledger, candidate, refresh=True):
    """Baixa uma vez o arquivo direto no cache privado e devolve o caminho local.

    Só mexe no cache: nada é gravado no candidato nem em `brolls/`, então `inspect`
    continua somente leitura sobre decisão, intervalo e direitos.
    """
    cache = ledger.root.parent / ".getbrolls-sources"
    _ensure_private_cache_dir(cache)
    reused = _reuse_from_index(cache, candidate["id"], 0, 0)
    if reused is not None:
        reused_path = Path(reused["path"])
        logs.event(
            log,
            logging.INFO,
            "source_materialized",
            candidate=candidate["id"],
            bytes=reused_path.stat().st_size if reused_path.is_file() else None,
            ms=0,
            kind="local",
        )
        return reused_path
    url = candidate.get("media_url")
    if refresh:
        from .providers import refresh as refresh_candidate

        url = (refresh_candidate(candidate) or {}).get("media_url") or url
    if not url:
        raise ValueError("Arquivo do provedor não está mais disponível.")
    from .http import download

    started = time.monotonic()
    with tempfile.TemporaryDirectory(dir=cache) as work:
        target = Path(work) / "source.bin"
        download(url, target)
        info = probe(target)
        sha = digest(target)
        final = cache / (id_stem(candidate["id"]) + "-" + sha + ".mp4")
        if not final.exists():
            os.replace(target, final)
            final.chmod(0o600)
        elif digest(final) != sha:
            logs.event(
                log,
                logging.WARNING,
                "source_cache",
                candidate=candidate["id"],
                result="stale",
                reason="digest_mismatch",
            )
            raise ValueError("Cache de mídia inconsistente; não foi sobrescrito.")
    logs.event(
        log,
        logging.INFO,
        "source_materialized",
        candidate=candidate["id"],
        bytes=final.stat().st_size,
        ms=round((time.monotonic() - started) * 1000),
        kind="remote",
    )
    index = _load_index(cache)
    entries = index.setdefault(candidate["id"], [])
    entries[:] = [e for e in entries if e.get("sha") != sha]
    entries.append(
        {
            "path": str(final.resolve()),
            "sha": sha,
            "start": 0,
            "duration": info["duration_s"],
        }
    )
    _save_index(cache, index)
    return final


def prepare_source(ledger, candidate, start, end, tolerant=False):
    """Deixa a mídia de trabalho pronta para [start, end] em tempo da fonte.

    `tolerant=True` aceita que o arquivo baixado seja mais curto do que o pedido — é
    o caso da varredura, que pede o vídeo inteiro e não pode falhar porque a fonte
    entregou alguns segundos a menos do que a duração anunciada. Quem chama com
    `tolerant` precisa reler `local_duration_s` antes de montar a grade.
    """
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
    _ensure_private_cache_dir(cache)
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
        reused_path = Path(reused["path"])
        logs.event(
            log,
            logging.INFO,
            "source_materialized",
            candidate=c["id"],
            bytes=reused_path.stat().st_size if reused_path.is_file() else None,
            ms=0,
            kind="local",
        )
        return
    started = time.monotonic()
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
        if end - offset > info["duration_s"] + 0.1 and not tolerant:
            raise ValueError("Original não contém o intervalo solicitado.")
        sha = digest(target)
        final = cache / (id_stem(c["id"]) + "-" + sha + ".mp4")
        if not final.exists():
            os.replace(target, final)
            final.chmod(0o600)
        elif digest(final) != sha:
            logs.event(
                log, logging.WARNING, "source_cache", candidate=c["id"], result="stale", reason="digest_mismatch"
            )
            raise ValueError("Cache de mídia inconsistente; não foi sobrescrito.")
    c.update(
        local_path=str(final.resolve()), local_sha256=sha, local_start_s=offset, local_duration_s=info["duration_s"]
    )
    c["media"].update(width=info["width"], height=info["height"], fps=info["fps"])
    logs.event(
        log,
        logging.INFO,
        "source_materialized",
        candidate=c["id"],
        bytes=final.stat().st_size,
        ms=round((time.monotonic() - started) * 1000),
        kind="remote",
    )
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
