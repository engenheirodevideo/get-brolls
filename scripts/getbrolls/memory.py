"""Explicit editorial reference history, separate from media permission."""

import json, os
from .models import signature, now


def remember(ledger, c, decision, reason, by):
    if not reason.strip() or not by.strip():
        raise ValueError("Referência exige motivo e responsável.")
    if decision == "approved" and (
        c["approval"]["status"] != "approved" or c["approval"].get("signature") != signature(c)
    ):
        raise ValueError("Aprove este insert antes de guardá-lo como referência positiva.")
    path = ledger.root / "references.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
    entry = {
        "id": c["id"],
        "signature": signature(c),
        "source_url": c["source_url"],
        "title": c["title"],
        "asset_type": c.get("asset_type", "video"),
        "format": c.get("format", {}),
        "narration": c.get("narration"),
        "decision": decision,
        "reason": reason,
        "by": by,
        "at": now(),
    }
    data["items"].append(entry)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    ledger.save("remember", c)
    return entry
