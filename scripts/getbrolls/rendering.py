"""Storyboard and credits generated from the canonical manifest."""

import html
from pathlib import Path
from .review import review_epoch


def safe_preview_url(value):
    from .http import public_url

    if not isinstance(value, str):
        return None
    if value.startswith("https://"):
        return public_url(value)
    if value.startswith(("previews/", "clips/")) and ".." not in Path(value).parts:
        return value
    return None


# Três botões: "outra fonte" virou opção dentro de "Pedir ajuste" — o valor exportado
# (`alternative`) continua o mesmo, só o caminho até ele ficou mais curto.
REVIEW_PANEL = (
    '<section class="review-panel"><h2>Esse trecho serve?</h2>'
    '<div class="review-actions">'
    '<button class="approve" data-decision="approved" aria-pressed="false"'
    ' title="Marca o trecho como aprovado. Depois eu baixo ele pra sua pasta.">Aprovar</button>'
    '<button data-decision="changes" aria-pressed="false"'
    ' title="Você escreve o que mudar (outro pedaço do vídeo, ou outro vídeo) e eu refaço.">Pedir ajuste</button>'
    '<button data-decision="rejected" aria-pressed="false"'
    ' title="Descarta o trecho. Eu não baixo ele.">Reprovar</button>'
    "</div>"
    '<button class="comment-toggle" type="button" aria-expanded="false">Comentar</button>'
    '<div class="review-fields" hidden>'
    '<label class="want-other" hidden><input type="checkbox" data-alternative>'
    " Não é esse vídeo: procure outro</label>"
    '<label>O que mudar<textarea data-comment rows="3"'
    ' placeholder="Me conta em uma linha o que você queria…"></textarea></label>'
    '<label class="other-url" hidden>Achou outro vídeo? cole o link (opcional)'
    '<input data-suggestion type="url" placeholder="https://…"></label>'
    '<button class="confirm-review" type="button" hidden>Salvar decisão</button>'
    "</div>"
    '<p data-review-status class="feedback" role="status"></p></section>'
)


def timecode(seconds):
    """Seconds → MM:SS.ff, the Storyboard's interval notation."""
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


def segment_label(c):
    if c["segment"]["start_s"] is not None:
        return f"{timecode(c['segment']['start_s'])}–{timecode(c['segment']['end_s'])}"
    if c.get("media", {}).get("kind") == "image":
        return "Imagem parada"
    return "vídeo inteiro"


def source_domain(url):
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def source_card(c, source, sheet, poster, esc):
    """Fonte coletada as a card: thumbnail (sheet when it exists), identity and link."""
    # The card shows the poster; the full contact sheet follows inline below it.
    thumb = poster or sheet
    image = (
        f'<img class="source-thumbnail" src="{esc(thumb)}" alt="" loading="lazy">'
        if thumb
        else '<span class="source-thumbnail placeholder">sem imagem da fonte</span>'
    )
    usage = {
        "unknown": "ainda não conferido",
        "permitted": "você anotou que pode",
        "restricted": "uso restrito",
    }.get(c["rights"]["status"], c["rights"]["status"])
    # O id é um hash: fica no title, fora do lugar nobre do card.
    details = [
        f"<strong>{esc(c['title'])}</strong>",
        f"<code title=\"Identificador interno deste trecho\">{esc(c['id'])}</code>",
    ]
    if c["segment"]["start_s"] is not None:
        details.append(f"<p>{esc(cut_label(c))}</p>")
        duration = c.get("media", {}).get("duration_s")
        if duration:
            left = max(0.0, min(100.0, 100 * c["segment"]["start_s"] / duration))
            width = max(0.5, min(100.0 - left, 100 * (c["segment"]["end_s"] - c["segment"]["start_s"]) / duration))
            details.append(
                f'<span class="cut-position" role="img" aria-label="Posição do corte no vídeo">'
                f'<span style="left:{left:.2f}%;width:{width:.2f}%"></span></span>'
            )
    if c.get("creator", {}).get("name"):
        details.append(f"<p>Autor: {esc(c['creator']['name'])}</p>")
    if c.get("captured_at"):
        details.append(f"<p>Capturado em: {esc(c['captured_at'])}</p>")
    details.append(
        f"<p>Por que eu escolhi este: {esc(c.get('match', {}).get('reason') or 'ainda não registrei')}</p>"
    )
    if c["rights"].get("attribution"):
        details.append(f"<p>{esc(c['rights']['attribution'])}</p>")
    details.append(
        f"<p>Pode usar? {esc(usage)} — quem confere a licença da fonte é você,"
        " antes de publicar.</p>"
    )
    info = '<div class="source-link-info">'
    if source:
        info += f'<span class="source-domain">{esc(source_domain(source))}</span>'
    info += '<div class="source-details">' + "".join(details) + "</div>"
    info += (
        '<span class="source-link-label">Abrir fonte original ↗</span>'
        if source
        else '<span class="source-link-label">Gravação própria · sem fonte externa</span>'
    )
    info += "</div>"
    if source:
        return (
            f'<a class="source-link-card" href="{esc(source)}" target="_blank" rel="noopener noreferrer">'
            f"{image}{info}</a>"
        )
    return f'<div class="source-link-card">{image}{info}</div>'


def script_bubble(narration, esc):
    if not narration:
        return ""
    return (
        '<div class="caption-content review-script"><span class="script-label">Fala do roteiro</span>'
        f'<blockquote tabindex="0" role="region" aria-label="Fala do roteiro">“{esc(narration)}”</blockquote></div>'
    )


def format_seconds(value):
    """Seconds as a short pt-BR label: 7 → "7,0 s", 7.417 → "7,4 s"."""
    return f"{value:.1f}".replace(".", ",") + " s"


def cut_label(candidate):
    """Where the cut sits in the source: 'corte 0:59.0–1:05.0 de 2:02.4'."""
    from .media import clock

    seg = candidate["segment"]
    if seg["start_s"] is None:
        return "corte a definir"
    text = f"corte {clock(seg['start_s'])}–{clock(seg['end_s'])}"
    duration = candidate.get("media", {}).get("duration_s")
    if duration:
        text += f" de {clock(duration)}"
    return text


def contact_sheet_figure(candidate, sheet, esc):
    """Inline contact sheet with a per-cell time legend when ffmpeg drew no labels."""
    preview = candidate["preview"]
    times = preview.get("frame_times_s") or []
    grid = preview.get("sheet_grid") or []
    caption = (
        f"Os quadros do trecho ({len(times)})" if times else "Os quadros do trecho"
    )
    if len(grid) == 2:
        caption += f" · grade {grid[0]}×{grid[1]}"
    caption += " · " + cut_label(candidate)
    legend = ""
    if times and not preview.get("sheet_labels"):
        cells = " · ".join(
            f"{i + 1} = {format_seconds(t)}" for i, t in enumerate(times)
        )
        legend = f'<p class="sheet-legend">{esc(cells)}</p>'
    return (
        f'<figure class="contact-sheet"><a href="{esc(sheet)}" target="_blank" rel="noopener">'
        f'<img src="{esc(sheet)}" alt="Os quadros do trecho" loading="lazy"></a>'
        f"<figcaption>{esc(caption)} · abrir em tamanho real</figcaption>{legend}</figure>"
    )


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
        has_preview = bool(c["preview"].get("poster_path"))
        context = safe_preview_url(c["preview"].get("context_path"))
        content = source_card(c, source, sheet, p, esc)
        if context:
            content += f'<figure class="context-still"><img src="{esc(context)}" alt="Print da pessoa para contexto" loading="lazy"><figcaption>Você em cena (contexto)</figcaption></figure>'
        if sheet:
            content += contact_sheet_figure(c, sheet, esc)
        if c["preview"].get("warning"):
            content += f'<p role="status">{esc(c["preview"]["warning"])}</p>'
        if not c.get("local_path"):
            content += '<p class="source-note">Aqui só tenho a imagem da fonte: pra gerar o movimento eu precisaria do arquivo original no seu computador.</p>'
        content = f'<section class="review-source"><h2>Fonte coletada</h2>{content}</section>'
        content += script_bubble(c.get("narration"), esc)
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
                "reviewEpoch": review_epoch(c),
            }
        )
        content += REVIEW_PANEL
        story_items.append(
            {
                "title": c["title"],
                "content": content,
                "presenter": p,
                "presenterLabel": "Trecho do vídeo"
                if has_preview
                else "Imagem da fonte · sem prévia em movimento",
                "no_preview": not has_preview,
                "gif": gif,
                "poster": None,
                "time": segment_label(c),
                "status": c["state"],
                # A fala já está no painel de material como balão; nada a repetir.
                "narration": None,
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
