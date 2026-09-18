import hashlib
import json
import math
from datetime import UTC, datetime


def now():
    return datetime.now(UTC).isoformat()


def candidate(provider, source_id, title, source_url=None):
    return {
        "schema_version": 1,
        "id": f"{provider}:{source_id}",
        "provider": provider,
        "source_id": str(source_id),
        "source_url": source_url,
        "title": title,
        "creator": {"name": None, "url": None},
        "query": None,
        "collected_at": now(),
        "match": {"kind": "literal", "reason": None},
        "media": {"duration_s": None, "width": None, "height": None, "fps": None},
        "preview": {
            "poster_path": None,
            "contact_sheet_path": None,
            "embed_url": None,
            "seek_mode": "unknown",
        },
        "segment": {"start_s": None, "end_s": None, "revision": 0},
        "rights": {
            "status": "unknown",
            "license_name": None,
            "license_url": None,
            "evidence": [],
            "attribution": None,
        },
        "acquisition": {"status": "unavailable", "method": None, "evidence": []},
        "approval": {"status": "pending", "by": None, "at": None, "revision": None},
        "output": {"path": None, "sha256": None, "verified": False},
        "state": "candidate",
        "errors": [],
    }


def signature(c):
    return hashlib.sha256(
        json.dumps(
            [
                c["id"],
                c["provider"],
                c["source_id"],
                c["source_url"],
                c.get("local_sha256"),
                c.get("local_start_s", 0),
                c["segment"],
                c.get("narration"),
                c.get("match", {}).get("reason"),
                c.get("asset_type", "video"),
                c.get("format", {}).get("target", "native"),
                c.get("preview_scope", "broll"),
                c.get("context_image_sha256"),
                c.get("full_preview_sha256"),
                c.get("title"),
                c.get("creator", {}).get("name"),
                c.get("captured_at"),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def id_stem(candidate_id):
    return hashlib.sha256(candidate_id.encode()).hexdigest()[:16]


def invalidate_approval(c, bump_revision=False):
    """Núcleo comum de invalidação: aprovação volta a pendente, review sai,
    output zera e o estado vira awaiting_approval. `bump_revision=True`
    incrementa `segment.revision` in place, sem tocar em start_s/end_s nem em
    `preview` — é o que `sync_formats` (rules.py) precisa. `set_segment`
    (abaixo) reconstrói `preview` e reescreve `segment` inteiro por conta
    própria, então chama isto com `bump_revision=False`.
    """
    c["approval"] = {"status": "pending", "by": None, "at": None, "revision": None}
    c.pop("review", None)
    c["output"] = {"path": None, "sha256": None, "verified": False}
    c["state"] = "awaiting_approval"
    if bump_revision:
        c["segment"]["revision"] += 1
    return c


def set_segment(c, start, end):
    if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end <= start:
        raise ValueError("Intervalo inválido: use segundos finitos, 0 <= início < fim.")
    duration = c["media"].get("duration_s")
    if duration and end > duration + 0.1:
        raise ValueError("Intervalo excede a duração do vídeo.")
    if (start, end) != (c["segment"]["start_s"], c["segment"]["end_s"]):
        c["preview"] = {k: v for k, v in c["preview"].items() if k in ("poster_url", "embed_url", "seek_mode")}
        invalidate_approval(c, bump_revision=False)
        c["segment"] = {
            "start_s": start,
            "end_s": end,
            "revision": c["segment"]["revision"] + 1,
        }
    return c


def approve(c, by, channel="storyboard", statement=None):
    """Registra a decisão humana já recebida, dizendo por onde ela chegou."""
    if c["segment"]["start_s"] is None and c.get("media", {}).get("kind") != "image":
        raise ValueError("Mostre e selecione um intervalo antes de aprovar.")
    if not by.strip():
        raise ValueError("Informe quem aprovou.")
    if channel not in ("chat", "storyboard"):
        raise ValueError("Canal de aprovação inválido: use chat ou storyboard.")
    if statement is not None and not isinstance(statement, str):
        raise ValueError("Frase de aprovação inválida.")
    if channel == "chat" and not (statement or "").strip():
        raise ValueError("Aprovação pelo chat exige --statement com a frase exata dita pela pessoa.")
    c["approval"] = {
        "status": "approved",
        "by": by,
        "at": now(),
        "revision": c["segment"]["revision"],
        "channel": channel,
        "statement": statement or None,
        "signature": signature(c),
    }
    c["state"] = "approved"
    return c


def require_fetch(c):
    if c["approval"]["status"] != "approved" or c["approval"].get("signature") != signature(c):
        raise ValueError("Aprovação humana ausente ou inválida para esta fonte e intervalo.")
    if c["rights"]["status"] != "permitted" or not c["rights"]["evidence"]:
        raise ValueError("Registre a autorização/condições de uso com permit --evidence antes de obter mídia.")
    if c["acquisition"]["status"] != "available":
        raise ValueError("Esta fonte é somente referência; forneça um original local autorizado.")
