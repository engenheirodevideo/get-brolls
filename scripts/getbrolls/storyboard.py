"""Portable review page shared by the packaged case and CLI collections."""

from html import escape
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "assets"


def render_page(
    items,
    title="Storyboard da coleta",
    subtitle="Prévias, intervalos e fontes para revisar.",
    note="A seleção visual não concede permissão de uso. Confira as condições de cada fonte.",
):
    esc = lambda value: escape(str(value or ""), quote=True)
    templates = []
    gallery = []
    options = []

    def image(path, label):
        return (
            f'<img loading="lazy" src="{esc(path)}" alt="{esc(label)}">'
            if path
            else '<span class="placeholder">Sem prévia</span>'
        )

    for i, item in enumerate(items):
        name = esc(item["title"])
        time = esc(item.get("time"))
        narration = esc(item.get("narration"))
        presenter = item.get("presenter")
        left = (
            image(presenter, "Gravação — " + item["title"])
            if presenter
            else '<p class="empty">Gravação não fornecida para este quadro.</p>'
        )
        if item.get("gif"):
            left = f'<button class="gif-preview" data-gif="{esc(item["gif"])}" data-poster="{esc(presenter)}" aria-label="Assistir trecho: {esc(item["title"])}" aria-pressed="false">{left}<span>▶ Assistir trecho</span></button>'
        templates.append(
            f"""<template data-shot id="shot-{i}"><figure class="pane presenter"><figcaption><span>{esc(item.get("presenterLabel", "Gravação"))}</span><span>{time}</span></figcaption><div class="image-box">{left}</div></figure><section class="pane material"><figure class="pane"><figcaption><span>Fonte coletada</span></figcaption></figure>{item["content"]}</section><div class="caption-content">{f"<blockquote>“{narration}”</blockquote>" if narration else ""}</div></template>"""
        )
        thumbnail = (
            f'<img loading="lazy" src="{esc(presenter)}" data-still="{esc(presenter)}" alt="{name}">'
            if item.get("gif")
            else image(presenter or item.get("poster"), "Prévia — " + item["title"])
        )
        gallery.append(
            f'<button class="shot" data-index="{i}" aria-current="false"><div class="thumbs">{thumbnail}</div><h3>{name}</h3><div class="time">{time}</div></button>'
        )
        options.append(f'<option value="{i}">{name}</option>')
    logo = '<svg viewBox="0 0 48 48" aria-hidden="true"><path d="M7 16 7 6 18 13H30L41 6V16L45 24 35 35H13L3 24Z" fill="currentColor"/><path d="M18 20 13 24 18 28M30 20 35 24 30 28M22 28 26 20" fill="none" stroke="#191b18" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    board = (
        f"""<div class="tools" id="tools"><div class="selection"><button id="prev" aria-label="Quadro anterior">←</button><select id="select" aria-label="Escolher quadro">{"".join(options)}</select><button id="next" aria-label="Próximo quadro">→</button></div><div class="modes" role="group" aria-label="Visualização"><button data-mode="side" aria-pressed="true">Lado a lado</button><button data-mode="material" aria-pressed="false">Só material</button></div></div><div class="viewer" id="viewer"></div><div class="caption" id="caption" aria-live="polite"></div><div class="gallery-head"><h2>Trechos</h2></div><div class="gallery">{"".join(gallery)}</div>{"".join(templates)}"""
        if items
        else '<p class="empty-board">Nenhum quadro nesta coleta. Importe uma fonte e prepare a prévia para começar.</p>'
    )
    return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><link rel="icon" href="data:,"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Get B-rolls — {esc(title)}</title><style>{(ASSETS / "storyboard.css").read_text()}</style></head><body class="{"film-board" if any(x.get("gif") for x in items) else ""}"><header><div class="brand">{logo}<span>engenheiro<span>de vídeo<b>.</b></span></span></div><span class="edition">Get B-rolls / Storyboard</span></header><main><div class="intro"><div><h1>{esc(title)}</h1>{f'<p class="sub">{esc(subtitle)}</p>' if subtitle else ""}</div><span class="count" id="position" hidden></span></div>{board}{f"<footer>{esc(note)}</footer>" if note else ""}<noscript>Ative JavaScript para navegar entre os quadros.</noscript></main><script>{(ASSETS / "storyboard.js").read_text()}</script></body></html>'''
