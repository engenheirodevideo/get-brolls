"""Storyboard and credits generated from the canonical manifest."""

import hashlib, html, json
from pathlib import Path


def safe_preview_url(value):
    from .http import public_url

    if not isinstance(value, str):
        return None
    if value.startswith("https://"):
        return public_url(value)
    if value.startswith(("previews/", "clips/")) and ".." not in Path(value).parts:
        return value
    return None


def render(ledger):
    records = []
    story_items = []
    credits = [
        "---",
        "type: credits",
        "status: current",
        "created: " + __import__("datetime").date.today().isoformat(),
        "updated: " + __import__("datetime").date.today().isoformat(),
        "tags: [get-brolls, credits]",
        "---",
        "",
        "# Créditos da coleta",
        "",
    ]
    esc = lambda s: html.escape(str(s or ""))
    for c in ledger.data["items"]:
        p = safe_preview_url(
            c["preview"].get("poster_path") or c["preview"].get("poster_url")
        )
        out = safe_preview_url(c["output"]["path"])
        gif = safe_preview_url(c["preview"].get("gif_path"))
        sheet = safe_preview_url(c["preview"].get("contact_sheet_path"))
        source = safe_preview_url(c["source_url"])
        link = (
            f'<a target="_blank" rel="noopener noreferrer" href="{esc(source)}">Fonte original ↗</a>'
            if source
            else "Arquivo local"
        )
        content = f"<p>{link}</p><p>{esc(c['title'])}</p><p>Decisão de coleta: {esc(c.get('match', {}).get('reason') or 'Ainda não registrada')}</p><p>{esc(c['rights'].get('attribution'))}</p><p>Uso: {esc({'unknown': 'a confirmar', 'permitted': 'registrado pelo usuário', 'restricted': 'restrito'}.get(c['rights']['status'], c['rights']['status']))}</p>"
        context = safe_preview_url(c["preview"].get("context_path"))
        if context:
            content += f'<figure class="context-still"><img src="{esc(context)}" alt="Print da pessoa para contexto" loading="lazy"><figcaption>Pessoa / contexto</figcaption></figure>'
        if c.get("creator", {}).get("name"):
            content += f"<p>Autor: {esc(c['creator']['name'])}</p>"
        if c.get("captured_at"):
            content += f"<p>Capturado em: {esc(c['captured_at'])}</p>"
        if sheet:
            content += f'<p><a href="{esc(sheet)}" target="_blank" rel="noopener">Ver contact sheet</a></p>'
        if c["preview"].get("warning"):
            content += f'<p role="status">{esc(c["preview"]["warning"])}</p>'
        if not c.get("local_path"):
            content += "<p>Referência estática da fonte. GIF do trecho requer original local autorizado.</p>"
        from .models import signature

        records.append(
            {
                "id": c["id"],
                "signature": signature(c),
                "state": "pending",
                "title": c["title"],
                "segment": c["segment"],
                "asset_type": c.get("asset_type", "video"),
                "captured_at": c.get("captured_at"),
                "source": source,
                "narration": c.get("narration"),
                "collection_reason": c.get("match",{}).get("reason"),
                "creator": c.get("creator",{}).get("name"),
                "poster": p,
                "context_poster": context,
                "review": (
                    {**c.get("review", {}), "state": "approved"}
                    if c["approval"]["status"] == "approved"
                    and c["approval"].get("signature") == signature(c)
                    else {
                        **c.get("review", {}),
                        "state": c.get("review", {}).get("state", "pending")
                        if c.get("review", {}).get("signature") == signature(c)
                        else "pending",
                    }
                ),
                "reviewEpoch": hashlib.sha256(
                    json.dumps(
                        [c["approval"], c.get("review")], sort_keys=True
                    ).encode()
                ).hexdigest(),
            }
        )
        content += '<section class="review-panel"><h2>Revisar trecho</h2><label>Comentário ou sugestão<textarea data-comment rows="3" placeholder="O que precisa mudar?"></textarea></label><label>Outra fonte (opcional)<input data-suggestion type="url" placeholder="https://…"></label><div class="review-buttons"><button data-decision="approved">Aprovar</button><button data-decision="changes">Pedir ajuste</button><button data-decision="alternative">Outra fonte</button><button data-decision="pending">Desfazer</button></div><p data-review-status role="status"></p></section>'
        story_items.append(
            {
                "title": c["title"],
                "content": content,
                "presenter": p,
                "presenterLabel": "Prévia do trecho",
                "gif": gif,
                "poster": None,
                "time": f"{c['segment']['start_s']}–{c['segment']['end_s']} s"
                if c["segment"]["start_s"] is not None
                else "Imagem estática"
                if c.get("media", {}).get("kind") == "image"
                else "não definido",
                "status": c["state"],
                "narration": c.get("narration"),
            }
        )
        if out:
            credits += [
                f"## {c['id']}",
                f"- Arquivo: {out}",
                f"- Fonte: {c['source_url'] or 'original local'}",
                f"- Autor: {c['creator'].get('name') or 'não informado'}",
                f"- Licença: {c['rights'].get('license_name') or 'ver evidência'}",
                f"- Licença URL: {c['rights'].get('license_url') or 'não informada'}",
                f"- Evidência: {'; '.join(c['rights']['evidence'])}",
                "",
            ]
    from .storyboard import render_page
    from .review import enhance
    from .ledger import atomic_write

    atomic_write(
        ledger.root / "review.html", enhance(render_page(story_items), ledger, records)
    )
    atomic_write(ledger.root / "credits.md", "\n".join(credits))
    return str(ledger.root / "review.html")
