"""Exporter HyperFrames do get-brolls: um roteiro revisado vira um projeto HyperFrames editável.

Registra dois contratos do SDK (experimentais): o exporter `hyperframes` e o
resolvedor `hyperframes_media`, que acha `[SFX: x]`/`[MUSICA: x]` no acervo do
`media-use` (só lê `manifest.jsonl`; nunca escreve em `.media/` nem usa rede).

`generate(plan)` é pura: recebe o plano de export do core (um dict) e devolve os
arquivos de texto do projeto (`index.html`, uma sub-composição por cena, legendas,
`hyperframes.json`, `meta.json`, `package.json`, `EXPORT.md`) e os pedidos de mídia
por id lógico. Nunca abre arquivo, nunca usa rede, nunca lê relógio nem sorteia: o
mesmo plano dá os mesmos bytes. Todo texto do plano é dado não confiável e sai
escapado para HTML, JS, JSON ou Markdown.

Regras do HyperFrames seguidas aqui: mídia só em `assets/…` relativo à raiz; raiz com
tempo global e `data-duration` total; sub-composição com tudo dentro de `<template>`,
tempo local e `data-hf-media-start-basis="local"` em toda mídia aninhada; vídeo mudo
com o som num `<audio id>` na raiz; uma timeline pausada por composição; legendas num
host `data-track-kind="captions"` com `const TRANSCRIPT` e trava final por grupo;
volume no tempo só por `data-automation`.
"""

import html
import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import cast
from urllib.parse import unquote

from getbrolls.sdk import ExportResult, MediaRequest, PluginError, ResolverHit

HYPERFRAMES_VERSION = "0.8.73"
GSAP_URL = "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"
FPS = 30
NPX = f"npx --yes hyperframes@{HYPERFRAMES_VERSION}"
# (largura, altura, data-resolution) por aspecto do roteiro.
CANVAS = {"9:16": (1080, 1920, "portrait"), "16:9": (1920, 1080, "landscape")}
# Centro vertical da cartela (terço superior), da legenda e do LETTERING, por aspecto.
CARD_Y = {"9:16": 560, "16:9": 300}
CAPTION_Y = {"9:16": 1080, "16:9": 900}
SPLIT_CAPTION_Y = 960
LETTERING_Y = {"9:16": 1500, "16:9": 620}
CAPTION_MAX_WIDTH = {"9:16": 900, "16:9": 1600}
MIN_LETTERING_S = 1.0
# Camadas: cartela 2 e LETTERING 5 dentro da cena; legenda acima de tudo.
CARD_Z, LETTERING_Z, CAPTIONS_Z = 2, 5, 10
# Trilhos (só exibição no Studio): cenas, legendas, voz, música e SFX alternados.
TRACK_SCENES, TRACK_CAPTIONS, TRACK_VOICE, TRACK_MUSIC, TRACK_SFX = 0, 1, 2, 3, 4
MUSIC_BASE, MUSIC_DUCK, SFX_VOLUME = 0.5, 0.125, 0.35
ATTACK_S, RELEASE_S, MERGE_GAP_S = 0.15, 0.4, 0.6
MAX_AUTOMATION_POINTS = 512
MAX_NOTES = 50
NO_FFPROBE = (
    "ffprobe não encontrado: instale FFmpeg/ffprobe ou aponte GB_FFMPEG_PATH/GB_FFPROBE_PATH; "
    "verifique python3 scripts/gb.py doctor; as durações ficaram estimadas"
)
# O Chrome do Studio/render não toca estes formatos: pendência de conversão.
UNPLAYABLE_AUDIO = (".aif", ".aiff", ".ogg", ".m4a")
BG, FG = "#0b0b0b", "#ffffff"
# Agrupamento da legenda (roda no navegador): grupos de 2 a 5 palavras; quebra em pausa
# (0,15 s), pontuação e no limite de 5. Pausa longa ou cartela encerram o trecho, e uma
# palavra que sobrar sozinha no fim do trecho junta-se ao grupo anterior (ou leva a última
# palavra dele): grupo de uma palavra só quando o trecho inteiro tem uma palavra.
CAPTION_GROUPING_JS = """function groupWords(words, cardWindows) {
          const PAUSE = 0.8;
          const inside = (t) => cardWindows.some((w) => t >= w[0] && t < w[1]);
          const between = (a, b) => cardWindows.some((w) => w[0] >= a && w[0] < b);
          const groups = [];
          let current = [];
          let runStart = 0;
          let last = null;
          const flush = () => {
            if (current.length) groups.push(current);
            current = [];
          };
          const closeRun = () => {
            flush();
            const tail = groups[groups.length - 1];
            if (groups.length - runStart >= 2 && tail.length === 1) {
              const previous = groups[groups.length - 2];
              if (previous.length >= 3) tail.unshift(previous.pop());
              else previous.push(groups.pop()[0]);
            }
            runStart = groups.length;
          };
          words.forEach((word) => {
            if (inside(word.start)) {
              closeRun();
              last = null;
              return;
            }
            if (last && (word.start - last.end >= PAUSE || between(last.end, word.start))) closeRun();
            else if (current.length >= 5 || (current.length >= 2 && word.start - last.end >= 0.15)) flush();
            current.push(word);
            last = word;
            if (/[.!?,;:]$/.test(word.text) && current.length >= 2) flush();
          });
          closeRun();
          const result = groups.map((g) => ({ text: g.map((w) => w.text).join(" "), start: g[0].start, end: g[g.length - 1].end }));
          result.forEach((group, index) => {
            if (index + 1 < result.length) group.end = Math.min(group.end, result[index + 1].start);
          });
          return result;
        }"""
_MARKDOWN = re.compile(r"([\\`*_\[\]()<>!|%~=#${}])")
# Surrogate solto (vira U+FFFD) e NUL (sai): o core só grava UTF-8 válido e sem NUL.
_UNSAFE_TEXT = re.compile(r"[\ud800-\udfff]|\x00")


# --- escape ---------------------------------------------------------------------


def esc(text):
    """Texto do plano dentro de HTML (conteúdo ou atributo entre aspas)."""
    return html.escape(str(text), quote=True)


def js(value):
    """Valor JSON seguro dentro de `<script>`: sem `</`, `<!--` nem separador de linha do JS."""
    raw = json.dumps(value, ensure_ascii=False)
    return raw.replace("</", "<\\/").replace("<!--", "<\\!--").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def md(text):
    """Texto do plano numa linha de Markdown, sem virar link, imagem, ênfase ou tabela."""
    line = " ".join(str(text).split())
    line = _MARKDOWN.sub(r"\\\1", line)
    return re.sub(r"(?i)\bwww\.", "www\\.", line.replace("://", "\\://"))


def comment(text):
    """Texto dentro de `<!-- … -->`: sem `--` e sem `>` que fechem o comentário."""
    body = esc(" ".join(str(text).split()))
    while "--" in body:  # `---` vira `- --` numa passada só
        body = body.replace("--", "- -")
    return body


def num(value):
    """Segundos com até 3 casas, sem notação científica: `9.6`, `0`, `12.345`."""
    text = f"{round(float(value), 3):.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def secs(value):
    """Segundos para a pessoa, em pt-BR: `3,2 s`."""
    return f"{float(value):.1f}".replace(".", ",") + " s"


def slug(text, fallback="midia"):
    """`[a-z0-9-]` a partir de qualquer texto (sem acento, espaço vira hífen)."""
    plain = unicodedata.normalize("NFKD", str(text))
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:40].strip("-") or fallback


def _clean(value):
    """Cópia do plano com todo texto (chave ou valor) sem surrogate solto nem NUL."""
    if isinstance(value, str):
        return _UNSAFE_TEXT.sub(lambda found: "" if found.group() == "\x00" else "\ufffd", value)
    if isinstance(value, dict):
        return {_clean(key): _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


# --- contexto -------------------------------------------------------------------


class _Export:
    """Estado de uma geração: plano, tela, destinos de mídia pedidos e notas."""

    def __init__(self, plan):
        self.plan = plan
        self.aspect = plan["meta"]["aspecto"] if plan["meta"]["aspecto"] in CANVAS else "9:16"
        self.width, self.height, self.resolution = CANVAS[self.aspect]
        self.media = plan["media"]
        self.dests = {}
        self.order = []
        self.notes = []
        self.pending = []
        self.comps = []
        self.blocks = []

    def note(self, text):
        self.notes.append(text)
        self.pend(text)

    def pend(self, text, *covered_by):
        """Pendência do EXPORT.md, a menos que um aviso do core já trate dela (começa por `covered_by`)."""
        if not any(warning.startswith(covered_by or (text,)) for warning in self.plan["warnings"]):
            self.pending.append(text)

    def usable(self, media_id):
        row = self.media.get(media_id) if media_id else None
        return row if row and row["available"] else None

    def dest(self, media_id, stem):
        """Destino `assets/<pasta>/<nome>.<ext>` da mídia; o mesmo id sempre no mesmo destino."""
        if media_id in self.dests:
            return self.dests[media_id]
        row = self.media[media_id]
        prefix, _, rest = media_id.partition(":")
        if prefix == "clip":
            folder = "clips"
        elif prefix == "aroll":
            folder = "aroll"
        else:
            parts = rest.split(":")
            folder = slug(parts[-2]) if len(parts) > 1 else "midia"
        base = f"assets/{folder}/{slug(stem)}"
        taken = {d.casefold() for d in self.dests.values()}
        candidate, n = f"{base}{row['ext']}", 2
        while candidate.casefold() in taken:
            candidate, n = f"{base}-{n}{row['ext']}", n + 1
        self.dests[media_id] = candidate
        self.order.append(media_id)
        return candidate

    def requests(self):
        return [{"media_id": m, "dest": self.dests[m]} for m in self.order]


def _presenter_title(export, media_id, scene_id):
    """O que fazer com o A-ROLL que falta: gravar, trocar o arquivo ilegível ou o link."""
    row = export.media.get(media_id or "") or {}
    name = _name_of(media_id) if media_id else scene_id
    if row.get("problem") == "no_ffprobe":
        return "FFPROBE NÃO ENCONTRADO: instale o FFmpeg e exporte de novo"
    if row.get("problem") == "unreadable":
        return f"NÃO CONSEGUI LER aroll/{name}{row.get('ext') or '.*'}"
    if row.get("problem") == "link":
        return f"aroll/{name}{row.get('ext') or ''} É UM LINK: troque pelo arquivo"
    return f"GRAVAR aroll/{name}.*"


def _name_of(media_id):
    """Parte legível do id: `aroll:c01-t2` → `c01-t2`; `asset:sfx:minha%20trilha` → `minha trilha`."""
    return unquote(media_id.rsplit(":", 1)[-1])


# --- cena -----------------------------------------------------------------------


class _Scene:
    """Uma sub-composição: elementos com tempo local e a timeline da cena."""

    def __init__(self, export, scene):
        self.export = export
        self.scene = scene
        self.cid = f"scene-{scene['id']}"
        self.duration = scene["duration_s"]
        self.elements = []
        self.tweens = []
        self.counter = 0

    def new_id(self, key):
        self.counter += 1
        return f"{self.cid}-{key}{self.counter}"

    def regions(self):
        """{vaga: (x, y, w, h)} — SPLIT: `a` em cima/esquerda, `b` embaixo/direita; senão `main`."""
        w, h = self.export.width, self.export.height
        if self.scene["layout"]["kind"] != "SPLIT":
            return {"main": (0, 0, w, h)}
        if self.export.aspect == "9:16":
            return {"a": (0, 0, w, h // 2), "b": (0, h // 2, w, h // 2)}
        return {"a": (0, 0, w // 2, h), "b": (w // 2, 0, w // 2, h)}

    def zoom(self, target, start, duration):
        """Escala lenta 1 → 1,04: nada fica parado mais de 3 s (`sweep_static`)."""
        self.tweens.append(
            f'tl.fromTo("#{target}", {{ scale: 1 }}, {{ scale: 1.04, duration: {num(duration)}, ease: "none" }}, {num(start)});'
        )

    def card(self, slot, region, lines, window):
        """Cartela: fundo escuro na vaga e bloco de texto no terço superior do quadro (ou da metade)."""
        start, duration = window
        x, y, w, h = region
        center = CARD_Y[self.export.aspect] if slot == "main" else y + h // 3
        card_id = self.new_id(f"{slot}-card")
        rows = "".join(f'<p class="{cls}">{esc(text)}</p>' for cls, text in lines if text)
        self.elements.append(
            f'<div id="{card_id}" class="clip card" data-start="{num(start)}" data-duration="{num(duration)}" '
            f'data-track-index="1" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;">'
            f'<div id="{card_id}-text" class="card-text" data-layout-allow-overflow '
            f'style="top:{center - y - 260}px;">{rows}</div></div>'
        )
        self.zoom(f"{card_id}-text", start, duration)

    def visual(self, slot, region, media_id, window, contain=False):
        """Vídeo mudo ou imagem numa vaga, dentro de um wrapper sem tempo que dá a escala lenta."""
        start, duration = window
        row = self.export.media[media_id]
        x, y, w, h = region
        src = self.export.dest(media_id, self.stem(slot, media_id))
        zoom_id = self.new_id(f"{slot}-z")
        fit = "contain" if contain else "cover"
        timing = f'data-start="{num(start)}" data-duration="{num(duration)}"'
        if row["kind"] == "image":
            inner = (
                f'<img id="{zoom_id}-img" class="clip" src="{esc(src)}" alt="" {timing} data-track-index="0" '
                f'style="object-fit:{fit};">'
            )
        else:
            inner = (
                f'<video id="{zoom_id}-video" src="{esc(src)}" muted playsinline {timing} data-media-start="0" '
                f'data-hf-media-start-basis="local" data-track-index="0" style="object-fit:{fit};"></video>'
            )
        # A escala lenta vai num miolo sem tempo: a vaga (overflow: hidden) fica do tamanho da metade.
        self.elements.append(
            f'<div id="{zoom_id}" class="slot" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;">'
            f'<div id="{zoom_id}-in" class="slot-zoom" data-layout-allow-overflow>{inner}</div></div>'
        )
        self.zoom(f"{zoom_id}-in", start, duration)

    def stem(self, slot, media_id):
        """Nome do arquivo em `assets/`: clipe = `<cena>-<vaga>` (o destino ganha `-2`, `-3`…); resto = o nome."""
        return f"{self.scene['id']}-{slot}" if media_id.startswith("clip:") else _name_of(media_id)

    # vagas

    def broll(self, slot, region):
        label = self.scene["id"]
        chain = [m for m in [slot["media_id"], *slot["extra_media_ids"]] if self.export.usable(m)]
        cursor = 0.0
        for media_id in chain:
            if cursor >= self.duration:
                break
            length = self.export.media[media_id]["duration_s"] or self.duration - cursor
            span = min(length, self.duration - cursor)
            self.visual(slot["slot"], region, media_id, (cursor, span))
            cursor = round(cursor + span, 3)
        if cursor < self.duration:
            if chain:
                self.export.note(f"{label}: b-roll cobre {secs(cursor)} de {secs(self.duration)}")
            else:
                self.export.pend(
                    f'{label}: b-roll sem clipe — alvo "{slot["text"]}": `brief --beat {slot["beat_id"]} --project <projeto>`'
                )
            lines = [("card-title", f"B-ROLL: {slot['text']}")]
            self.card(slot["slot"], region, lines, (cursor, round(self.duration - cursor, 3)))

    def presenter(self, slot, region):
        label = self.scene["id"]
        media_id = slot["media_id"]
        row = self.export.usable(media_id)
        if row is None:
            self.card(slot["slot"], region, self.missing_presenter(slot), (0, self.duration))
            return
        span = min(row["duration_s"] or self.duration, self.duration)
        self.visual(slot["slot"], region, media_id, (0, span))
        if span < self.duration:
            side = "lado " + slot["slot"] if slot["slot"] in ("a", "b") else "A-ROLL"
            self.export.note(f"{label}: {side} termina em {secs(span)} de {secs(self.duration)}")
            self.card(slot["slot"], region, [("card-title", slot["text"])], (span, round(self.duration - span, 3)))

    def missing_presenter(self, slot):
        title = _presenter_title(self.export, slot["media_id"], self.scene["id"])
        return [("card-title", title), ("card-prompt", slot["prompt"]), ("card-speech", self.scene["speech_clean"])]

    def brand(self, slot, region):
        if self.export.usable(slot["media_id"]):
            self.visual(slot["slot"], region, slot["media_id"], (0, self.duration), contain=True)
            return
        row = self.export.media.get(slot["media_id"] or "") or {}
        if row.get("problem") == "missing" and row.get("expected"):
            label = self.scene["id"]
            self.export.pend(f"{label}: MARCA pendente — ponha o arquivo em {row['expected']}", f"{label}: marca")
        self.card(slot["slot"], region, [("card-title", f"MARCA: {slot['text']}")], (0, self.duration))

    def lettering(self, layer):
        start = max(0.0, round(layer["at_s"] - self.scene["start_s"], 3))
        if self.duration - start < MIN_LETTERING_S:
            start = max(0.0, round(self.duration - MIN_LETTERING_S, 3))
        duration = round(self.duration - start, 3)
        style = f" lettering--{slug(layer['name'])}" if layer["name"] else ""
        el_id = self.new_id("lettering")
        self.elements.append(
            f'<div id="{el_id}" class="clip lettering{style}" data-start="{num(start)}" data-duration="{num(duration)}" '
            f'data-track-index="2" style="top:{LETTERING_Y[self.export.aspect] - 90}px;">'
            f'<div id="{el_id}-text" class="lettering-text">{esc(layer["text"])}</div></div>'
        )
        self.tweens.append(
            f'tl.fromTo("#{el_id}-text", {{ opacity: 0, y: 40 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: "power2.out" }}, {num(start)});'
        )

    def build(self):
        regions = self.regions()
        for slot in self.scene["layout"]["slots"]:
            region = regions[slot["slot"]]
            role = slot["role"]
            if role == "broll":
                self.broll(slot, region)
            elif role == "presenter":
                self.presenter(slot, region)
            elif role == "brand":
                self.brand(slot, region)
            else:
                self.card(slot["slot"], region, [("card-title", slot["text"])], (0, self.duration))
        for layer in self.scene["layers"]:
            if layer["kind"] == "LETTERING":
                self.lettering(layer)
        for ext in self.scene["extensions"]:
            if ext["plugin"] == "hyperframes":
                local = round(ext["at_s"] - self.scene["start_s"], 3)
                args = " ".join(ext["args"])
                self.elements.append(f"<!-- hyperframes:{comment(ext['name'])} {comment(args)} em {num(local)}s -->")
                self.export.blocks.append((self.scene["id"], ext["name"], args, ext["at_s"]))
        return self.html()

    def html(self):
        w, h = self.export.width, self.export.height
        body = "\n      ".join(self.elements)
        tweens = "\n        ".join(self.tweens)
        return f"""<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8">
  </head>
  <body>
    <template id="{self.cid}-template">
      <style>
        #root {{ position: absolute; inset: 0; overflow: hidden; background: {BG}; }}
        .slot {{ position: absolute; overflow: hidden; }}
        .slot-zoom {{ position: absolute; inset: 0; }}
        .slot video, .slot img {{ position: absolute; left: 0; top: 0; width: 100%; height: 100%; }}
        .card {{ position: absolute; overflow: hidden; background: {BG}; z-index: {CARD_Z}; }}
        .card-text {{ position: absolute; left: 6%; width: 88%; height: 520px; display: flex; flex-direction: column;
          align-items: center; justify-content: center; text-align: center; color: {FG}; font-family: Inter, sans-serif; }}
        .card-title {{ margin: 0 0 24px; font-size: 64px; font-weight: 800; line-height: 1.1; }}
        .card-prompt {{ margin: 0 0 16px; font-size: 40px; font-weight: 600; line-height: 1.2; }}
        .card-speech {{ margin: 0; font-size: 36px; font-weight: 400; line-height: 1.3; }}
        .lettering {{ position: absolute; left: 0; width: 100%; height: 180px; z-index: {LETTERING_Z}; }}
        .lettering-text {{ display: flex; align-items: center; justify-content: center; width: 100%; height: 100%;
          color: {FG}; font-family: Inter, sans-serif; font-size: 88px; font-weight: 900; text-align: center;
          text-shadow: 0 6px 24px rgba(0, 0, 0, 0.9); }}
      </style>
      <div id="root" data-composition-id="{self.cid}" data-start="0" data-duration="{num(self.duration)}" data-width="{w}" data-height="{h}">
      {body}
      </div>
      <script>
        const tl = gsap.timeline({{ paused: true }});
        {tweens}
        window.__timelines["{self.cid}"] = tl;
      </script>
    </template>
  </body>
</html>
"""


# --- raiz, áudio e legendas ---------------------------------------------------------


def _voice_windows(export):
    """Janelas globais `[início, fim)` em que há voz de verdade (disponível e com trilha de áudio)."""
    windows = []
    for scene in export.plan["scenes"]:
        voice = scene["voice_media_ids"][0] if scene["voice_media_ids"] else None
        row = export.usable(voice)
        if row and row["has_audio"]:
            windows.append((scene["start_s"], round(scene["start_s"] + scene["duration_s"], 3)))
    return windows


def ducking(start, length, windows):
    """Pontos da faixa de volume (tempo local da música): base, abaixa sob a voz, volta depois.

    Janelas a menos de `MERGE_GAP_S` (mais que ataque + soltura) viram uma só: as rampas
    nunca se cruzam. Se a soltura não cabe antes do fim, o volume fica baixo até o fim."""
    spans = []
    for w_start, w_end in sorted(windows):
        s, e = max(0.0, w_start - start), min(length, w_end - start)
        if e <= s:
            continue
        if spans and s - spans[-1][1] < MERGE_GAP_S:
            spans[-1] = (spans[-1][0], max(spans[-1][1], e))
        else:
            spans.append((s, e))
    points = [(0.0, MUSIC_BASE)]
    for s, e in spans:
        points += [(max(0.0, s - ATTACK_S), MUSIC_BASE), (s, MUSIC_DUCK), (e, MUSIC_DUCK)]
        points.append((length, MUSIC_DUCK) if e + RELEASE_S >= length else (e + RELEASE_S, MUSIC_BASE))
    clean = []
    for when, value in sorted(points, key=lambda p: p[0]):
        t = round(when, 3)
        if clean and clean[-1][0] == t:
            clean[-1] = (t, value)
        else:
            clean.append((t, value))
    return [{"t": t, "v": v} for t, v in clean[:MAX_AUTOMATION_POINTS]]


def _missing_voice(export, scene, voice):
    """Pendência da voz que falta: A-ROLL do apresentador (a cartela diz o que gravar) ou narração."""
    sid = scene["id"]
    if (export.media.get(voice) or {}).get("problem") == "no_ffprobe":
        # A ferramenta falta, não o arquivo: uma pendência só para o export inteiro.
        export.pend(NO_FFPROBE, NO_FFPROBE)
        return
    if any(s["role"] == "presenter" and s["media_id"] == voice for s in scene["layout"]["slots"]):
        title = _presenter_title(export, voice, sid)
        export.pend(
            f"{sid}: A-ROLL pendente — {title}",
            f"{sid}: A-ROLL não gravado",
            f"{sid}: não consegui ler aroll/",
            f"{sid}: aroll/",
        )
        return
    expected = export.media[voice]["expected"] or f"aroll/{_name_of(voice)}.*"
    export.pend(f"{sid}: fala sem narração gravada — grave {expected}", f"{sid}: fala sem narração gravada")


def _voice_audio(export):
    """`<audio>` da voz de cada cena: só a voz que toca, disponível e com trilha de áudio."""
    lines = []
    for scene in export.plan["scenes"]:
        voice = scene["voice_media_ids"][0] if scene["voice_media_ids"] else None
        row = export.usable(voice)
        if row is None:
            if voice and scene["words"] > 0:
                _missing_voice(export, scene, voice)
            continue
        if not row["has_audio"]:
            continue
        src = export.dest(voice, _name_of(voice))
        span = min(row["duration_s"] or scene["duration_s"], scene["duration_s"])
        lines.append(
            f'<audio id="voice-{scene["id"]}" src="{esc(src)}" data-start="{num(scene["start_s"])}" '
            f'data-duration="{num(span)}" data-media-start="0" data-track-index="{TRACK_VOICE}" data-volume="1"></audio>'
        )
    return lines


def _music_audio(export, layers):
    """`<audio>` de cada MUSICA: do `at_s` até a próxima MUSICA ou o fim, com a faixa de ducking."""
    total = export.plan["total_s"]
    music = [(scene, layer) for scene, layer in layers if layer["kind"] == "MUSICA"]
    windows = _voice_windows(export)
    lines = []
    for index, (scene, layer) in enumerate(music, start=1):
        row = export.usable(layer["media_id"])
        end = music[index][1]["at_s"] if index < len(music) else total
        length = round(end - layer["at_s"], 3)
        if row is None:
            export.note(f'{scene["id"]}: MUSICA "{layer["name"]}" pendente')
            continue
        if length <= 0:
            continue  # outra MUSICA começa no mesmo instante: vale a seguinte
        src = export.dest(layer["media_id"], _name_of(layer["media_id"]))
        lane = {"version": 1, "lanes": [{"target": "volume", "points": ducking(layer["at_s"], length, windows)}]}
        lines.append(
            f'<audio id="music-{index}" src="{esc(src)}" data-start="{num(layer["at_s"])}" data-duration="{num(length)}" '
            f'data-media-start="0" data-track-index="{TRACK_MUSIC}" data-volume="{MUSIC_BASE}" '
            f'data-automation="{esc(json.dumps(lane, separators=(",", ":")))}"></audio>'
        )
    return lines


def _sfx_and_comps(export, layers):
    """`<audio>` de cada SFX em trilhos alternados e o comentário de cada COMP (ligado depois, na skill)."""
    total = export.plan["total_s"]
    lines, count = [], 0
    for scene, layer in layers:
        if layer["kind"] == "COMP":
            lines.append(f'<!-- getbrolls COMP "{comment(layer["name"])}" em {num(layer["at_s"])}s ({scene["id"]}) -->')
            export.comps.append((scene["id"], layer["name"], layer["at_s"]))
            continue
        if layer["kind"] != "SFX":
            continue
        if export.usable(layer["media_id"]) is None:
            export.note(f'{scene["id"]}: SFX "{layer["name"]}" pendente')
            continue
        if layer["at_s"] >= total:
            export.note(f'{scene["id"]}: SFX "{layer["name"]}" cai no fim do vídeo e ficou de fora')
            continue
        count += 1
        src = export.dest(layer["media_id"], _name_of(layer["media_id"]))
        lines.append(
            f'<audio id="sfx-{scene["id"]}-{count}" src="{esc(src)}" data-start="{num(layer["at_s"])}" '
            f'data-track-index="{TRACK_SFX + (count - 1) % 2}" data-volume="{SFX_VOLUME}"></audio>'
        )
    return lines


def _audio(export):
    """`<audio>` da raiz, sempre com id e tempo global: voz, música e SFX (e os COMP como comentário)."""
    layers = [(scene, layer) for scene in export.plan["scenes"] for layer in scene["layers"]]
    return _voice_audio(export) + _music_audio(export, layers) + _sfx_and_comps(export, layers)


def _transcript(plan):
    """Palavras do vídeo inteiro em tempo global: sidecar onde há, fala espalhada por igual no resto."""
    words = []
    for scene in plan["scenes"]:
        if scene["words_timed"]:
            words += [{"text": w["text"], "start": w["start"], "end": w["end"]} for w in scene["words_timed"]]
            continue
        tokens = scene["speech_clean"].split()
        if not tokens:
            continue
        step = scene["duration_s"] / len(tokens)
        for index, token in enumerate(tokens):
            start = round(scene["start_s"] + index * step, 3)
            words.append({"text": token, "start": start, "end": round(start + step, 3)})
    return words


def _windows(plan, predicate):
    return [
        [scene["start_s"], round(scene["start_s"] + scene["duration_s"], 3)]
        for scene in plan["scenes"]
        if predicate(scene)
    ]


def _captions(export):
    plan = export.plan
    split = _windows(plan, lambda s: s["layout"]["kind"] == "SPLIT") if export.aspect == "9:16" else []
    cards = _windows(
        plan, lambda s: s["layout"]["slots"][0]["slot"] == "main" and s["layout"]["slots"][0]["role"] == "card"
    )
    w, h = export.width, export.height
    return f"""<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="utf-8">
  </head>
  <body>
    <template id="captions-template">
      <style>
        #root {{ position: absolute; inset: 0; pointer-events: none; z-index: {CAPTIONS_Z}; }}
        .captions-group {{ position: absolute; z-index: {CAPTIONS_Z}; left: 0; width: 100%; height: 220px; padding: 0 {(w - CAPTION_MAX_WIDTH[export.aspect]) // 2}px;
          box-sizing: border-box; display: flex; align-items: center; justify-content: center; text-align: center;
          color: {FG}; font-family: Inter, sans-serif; font-size: 64px; font-weight: 800; line-height: 1.1;
          text-shadow: 0 4px 16px rgba(0, 0, 0, 0.85); opacity: 0; }}
      </style>
      <div id="root" data-composition-id="captions" data-start="0" data-duration="{num(plan["total_s"])}" data-width="{w}" data-height="{h}">
        <div id="captions-layer"></div>
      </div>
      <script>
        const TRANSCRIPT = {js(_transcript(plan))};
        const SPLIT_WINDOWS = {js(split)};
        const CARD_WINDOWS = {js(cards)};
        const BASE_Y = {CAPTION_Y[export.aspect]};
        const SPLIT_Y = {SPLIT_CAPTION_Y};
        const MAX_WIDTH = {CAPTION_MAX_WIDTH[export.aspect]};
        const inside = (t, windows) => windows.some((w) => t >= w[0] && t < w[1]);
        {CAPTION_GROUPING_JS}
        const groups = groupWords(TRANSCRIPT, CARD_WINDOWS);
        const layer = document.getElementById("captions-layer");
        const tl = gsap.timeline({{ paused: true }});
        groups.forEach((group, index) => {{
          const el = document.createElement("div");
          el.id = "captions-g" + index;
          el.className = "captions-group";
          el.textContent = group.text;
          const y = inside(group.start, SPLIT_WINDOWS) ? SPLIT_Y : BASE_Y;
          el.style.top = y - 110 + "px";
          const fit = window.__hyperframes && window.__hyperframes.fitTextFontSize
            ? window.__hyperframes.fitTextFontSize(group.text, {{ fontFamily: "Inter", fontWeight: 800, maxWidth: MAX_WIDTH }})
            : null;
          if (fit && fit.fontSize) el.style.fontSize = fit.fontSize + "px";
          layer.appendChild(el);
          tl.fromTo(el, {{ opacity: 0, y: 12 }}, {{ opacity: 1, y: 0, duration: 0.12, ease: "power2.out" }}, group.start);
          tl.set(el, {{ opacity: 0, visibility: "hidden" }}, group.end);
        }});
        window.__timelines["captions"] = tl;
      </script>
    </template>
  </body>
</html>
"""


def _index(export, scenes_html):
    plan = export.plan
    w, h = export.width, export.height
    total = num(plan["total_s"])
    hosts = [
        f'<div id="el-scene-{s["id"]}" class="clip" data-composition-id="scene-{s["id"]}" '
        f'data-composition-src="compositions/scene-{s["id"]}.html" data-start="{num(s["start_s"])}" '
        f'data-duration="{num(s["duration_s"])}" data-track-index="{TRACK_SCENES}" data-width="{w}" data-height="{h}"></div>'
        for s in plan["scenes"]
        if s["id"] in scenes_html
    ]
    if plan["meta"]["legenda"]:
        hosts.append(
            f'<div id="el-captions" class="clip" data-composition-id="captions" data-composition-src="compositions/captions.html" '
            f'data-track-kind="captions" data-start="0" data-duration="{total}" data-track-index="{TRACK_CAPTIONS}" '
            f'data-width="{w}" data-height="{h}"></div>'
        )
    body = "\n      ".join(hosts + _audio(export))
    # A legenda fica acima das cartelas das cenas.
    captions_css = f"      #el-captions {{ z-index: {CAPTIONS_Z}; }}\n" if plan["meta"]["legenda"] else ""
    return f"""<!doctype html>
<html lang="pt-BR" data-resolution="{export.resolution}">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width={w}, height={h}">
    <title>{esc(plan["meta"]["tema"])}</title>
    <script src="{GSAP_URL}"></script>
    <style>
      html, body {{ margin: 0; width: {w}px; height: {h}px; overflow: hidden; background: {BG}; }}
      #root {{ position: relative; width: 100%; height: 100%; overflow: hidden; background: {BG}; }}
{captions_css}    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-fps="{FPS}" data-width="{w}" data-height="{h}">
      {body}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""


# --- arquivos do projeto ------------------------------------------------------------


def _number(plan):
    return plan["out_dir"].rsplit("/", 1)[-1]


def _project_files(plan):
    theme = slug(plan["meta"]["tema"], fallback="video")
    number = _number(plan)
    exporter = plan["exporter"]
    config = {
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        "media": {"autoProxy": True},
    }
    meta = {"id": f"getbrolls-{theme}", "name": plan["meta"]["tema"], "createdAt": plan["generated_at"]}
    package = {
        "name": f"getbrolls-{theme}-{number}",
        "private": True,
        "scripts": {
            "preview": f"{NPX} preview .",
            "check": f"{NPX} check . --json",
            "render": f"{NPX} render . -o ../../../renders/{exporter}-{number}.mp4",
        },
    }
    dump = lambda value: json.dumps(value, ensure_ascii=False, indent=2) + "\n"  # noqa: E731 - one-line formatter shared by three files
    return {"hyperframes.json": dump(config), "meta.json": dump(meta), "package.json": dump(package)}


def _media_cell(export, scene):
    ids = [m for slot in scene["layout"]["slots"] for m in [slot["media_id"], *slot["extra_media_ids"]] if m]
    ids += [m for m in scene["voice_media_ids"] if m]
    missing = [m for m in dict.fromkeys(ids) if not export.usable(m)]
    return "ok" if not missing else "faltando: " + ", ".join(md(_name_of(m)) for m in missing)


def _credit(row):
    """Crédito numa linha de Markdown: o core manda texto cru de toda fonte, e o escape é sempre daqui."""
    return md(row["credit"])


def _export_md(export):
    plan = export.plan
    out = plan["out_dir"]
    timing = {"estimate": "estimado", "aroll": "A-ROLL real", "mixed": "misto (A-ROLL real + estimativa)"}[
        plan["timing"]
    ]
    lines = [
        f"# Export HyperFrames — {md(plan['meta']['tema'])}",
        "",
        (
            f"Gerado pelo get-brolls {md(plan['getbrolls_version'])} em {md(plan['generated_at'])}. "
            f"Versão testada da CLI HyperFrames: {HYPERFRAMES_VERSION}."
        ),
        "",
        "| Campo | Valor |",
        "|---|---|",
        f"| Tema | {md(plan['meta']['tema'])} |",
        f"| Aspecto | {md(plan['meta']['aspecto'])} ({export.width}×{export.height}) |",
        f"| Duração | {secs(plan['total_s'])} |",
        f"| Tempo | {timing} |",
        f"| Pasta | `{out}` |",
        "",
        "## Cenas",
        "",
        "| Cena | Início | Duração | Tempo | Legenda | Layout | Mídia |",
        "|---|---|---|---|---|---|---|",
    ]
    for scene in plan["scenes"]:
        source = "A-ROLL" if scene["duration_source"] == "aroll" else "estimado"
        caption = "transcrição" if scene["words_source"] == "transcript" else "estimada"
        lines.append(
            f"| {scene['id']} — {md(scene['title'])} | {secs(scene['start_s'])} | {secs(scene['duration_s'])} | "
            f"{source} | {caption} | {md(scene['layout']['kind'])} | {_media_cell(export, scene)} |"
        )
    pending = [md(w) for w in plan["warnings"]] + [md(p) for p in dict.fromkeys(export.pending)]
    for media_id in export.order:
        ext = export.media[media_id]["ext"]
        if ext in UNPLAYABLE_AUDIO:
            pending.append(
                f"`{export.dests[media_id]}` está em {md(ext)}, que o Chrome do Studio não toca: converta para .wav ou .mp3"
            )
    lines += ["", "## Pendências", ""]
    lines += [f"- {p}" for p in dict.fromkeys(pending)] or ["- Nenhuma."]
    lines += ["", "## Créditos", ""]
    credit_lines = [
        f"- `{export.dests[m]}`: {_credit(export.media[m])}" for m in export.order if export.media[m]["credit"]
    ]
    lines += credit_lines or ["- Nenhum crédito registrado."]
    lines += [
        "",
        "## Próximos passos",
        "",
        "Rode sempre da raiz do projeto (a pasta do `ROTEIRO.md`), sem trocar de pasta:",
        "",
        "```bash",
        f"{NPX} lint {out} --json",
        f"{NPX} check {out} --json   # 1ª vez baixa GSAP (jsdelivr) e a fonte Inter (Google Fonts)",
        f"{NPX} preview {out}",
        "mkdir -p renders",
        f'{NPX} render {out} --quality draft -o "$PWD/renders/{plan["exporter"]}-{_number(plan)}-rascunho.mp4"',
        "```",
        "",
        "- Render **fora** do export: sempre `-o` para `renders/` do projeto.",
        "- Legendas mais precisas: gere `aroll/<cena>.transcript.json` (comando em `references/roteiro.md`) e rode `gb export` de novo; sai uma pasta nova.",
        "- Acabamento (motion, blocos, `COMP`) acontece na pasta numerada que você escolher, com a skill `/hyperframes`. Um novo `gb export` cria a próxima pasta e **nunca** mexe nesta; `LATEST` aponta a mais nova criada pelo core, não a que você está editando. Pastas antigas são suas: apague quando quiser.",
        "- `assets/clips/` compartilha o arquivo com `brolls/clips/`: não edite esses clipes no lugar.",
        "- Take contínuo (uma gravação para o vídeo todo) não é suportado na v1: grave um arquivo por cena em `aroll/`.",
    ]
    if export.blocks or export.comps:
        lines += ["", "## Blocos e composições", ""]
        for scene_id, name, args, at_s in export.blocks:
            extra = f" (args: {md(args)})" if args else ""
            lines.append(
                f"- {scene_id}: `{NPX} add {slug(name, fallback='bloco')} --dir {out}` em {secs(at_s)}{extra}; ligue com a skill `/hyperframes-registry`."
            )
        for scene_id, name, at_s in export.comps:
            lines.append(f'- {scene_id}: COMP "{md(name)}" em {secs(at_s)}: ligue com a skill `/hyperframes`.')
    return "\n".join(lines) + "\n"


# Valores de enum que este exporter trata. Chave nova no plano é ignorada; valor novo
# de enum é "não suportado" (política de evolução do plano, docs/SDK.md).
SUPPORTED = {
    "layout": ("A-ROLL", "BROLL", "SPLIT", "FULL", "UGC"),
    "vaga": ("a", "b", "main"),
    "papel": ("broll", "presenter", "card", "brand"),
    "camada": ("LETTERING", "SFX", "MUSICA", "COMP"),
    "mídia": ("video", "image", "audio"),
}


def _check_supported(plan):
    """`PluginError` no primeiro valor de enum que esta versão do exporter não conhece."""
    found = [("mídia", row["kind"]) for row in plan["media"].values()]
    for scene in plan["scenes"]:
        found.append(("layout", scene["layout"]["kind"]))
        for slot in scene["layout"]["slots"]:
            found += [("vaga", slot["slot"]), ("papel", slot["role"])]
        found += [("camada", layer["kind"]) for layer in scene["layers"]]
    for field, value in found:
        if value not in SUPPORTED[field]:
            shown = str(value)[:40]
            raise PluginError(
                f'{field} "{shown}" não é suportado por esta versão do exporter hyperframes: atualize o plugin.'
            )


def generate(plan):
    """`{"files": {caminho: texto}, "media": [{"media_id", "dest"}], "notes": [texto]}` a partir do plano."""
    plan = cast("dict", _clean(plan))
    _check_supported(plan)
    export = _Export(plan)
    scenes = {}
    for scene in plan["scenes"]:
        scenes[scene["id"]] = _Scene(export, scene).build()
    files = {"index.html": _index(export, scenes)}
    for scene_id, text in scenes.items():
        files[f"compositions/scene-{scene_id}.html"] = text
    if plan["meta"]["legenda"]:
        files["compositions/captions.html"] = _captions(export)
    files.update(_project_files(plan))
    files["EXPORT.md"] = _export_md(export)
    return {"files": files, "media": export.requests(), "notes": list(dict.fromkeys(export.notes))[:MAX_NOTES]}


def export(plan, options):
    """O exporter `hyperframes`: `generate` embrulhado nos tipos do SDK. `options` é reservado (`{"args": {}}`)."""
    del options  # reservado para --arg k=v numa versão futura
    result = generate(plan)
    media = [MediaRequest(row["media_id"], row["dest"]) for row in result["media"]]
    return ExportResult(files=result["files"], media=media, notes=result["notes"])


# --- resolvedor do acervo media-use ----------------------------------------------------

# Tipo do roteiro → tipo do media-use. Sem "marca" nesta versão.
MEDIA_TYPES = {"sfx": "sfx", "musica": "bgm"}
LEDGER = "manifest.jsonl"
SENTINEL = ".hf-complete"
LICENSE_MAX_CHARS = 500
DEFAULT_PATHS = ("~/.media",)
LEDGER_MAX_BYTES = 16 * 1024 * 1024
# FIFO não trava a abertura; link no último pedaço é recusado (onde existe O_NOFOLLOW).
_LEDGER_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


def _normal(value):
    return " ".join(str(value or "").strip().lower().split())


def _read_capped(fd):
    """Bytes de `fd` até `LEDGER_MAX_BYTES`, ou `None` quando passa do teto (sem ler o resto)."""
    chunks, size = [], 0
    while size <= LEDGER_MAX_BYTES:
        chunk = os.read(fd, min(1024 * 1024, LEDGER_MAX_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return None if size > LEDGER_MAX_BYTES else b"".join(chunks)


def _before_open(ledger):
    """Motivo para não abrir o ledger (link, pasta, FIFO), `""` quando é arquivo comum, `None` sem arquivo.
    A pasta é vista antes de abrir: no Windows, abrir uma pasta dá `PermissionError`."""
    try:
        seen = os.lstat(ledger)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        return f"não pôde ser aberto ({type(exc).__name__})"
    if stat.S_ISLNK(seen.st_mode):
        return "é um link"
    return "" if stat.S_ISREG(seen.st_mode) else "não é um arquivo comum"


def _read_ledger(ledger, shown):
    """Bytes do `manifest.jsonl`, ou `None` sem arquivo. Link, FIFO, pasta, arquivo grande
    demais ou erro de leitura viram `PluginError` com `shown` (nunca o caminho absoluto):
    o acervo pode vir de um clone, e o export não pode travar nem encher a memória."""

    def refuse(text):
        raise PluginError(f"{shown} {text}; corrija o acervo do media-use.")

    problem = _before_open(ledger)
    if problem is None:
        return None
    if problem:
        refuse(problem)
    try:
        fd = os.open(ledger, _LEDGER_FLAGS)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        refuse(f"não pôde ser aberto ({type(exc).__name__})")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            refuse("não é um arquivo comum")
        raw = _read_capped(fd)
        if raw is None:
            refuse(f"passa de {LEDGER_MAX_BYTES // (1024 * 1024)} MB")
        return raw
    except OSError as exc:
        refuse(f"não pôde ser lido ({type(exc).__name__})")
    finally:
        os.close(fd)


def _records(ledger, shown):
    """Registros do `manifest.jsonl` (objeto por linha); linha ruim é pulada. Arquivo fora de UTF-8 é
    `PluginError`: pular calado faria o nome cair no acervo global sem ninguém saber."""
    raw = _read_ledger(ledger, shown)
    try:
        lines = raw.decode("utf-8").splitlines() if raw else []
    except UnicodeDecodeError:
        raise PluginError(f"{shown} não está em UTF-8; corrija o acervo do media-use.") from None
    found = []
    for line in lines:
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):  # JSON aninhado demais também é linha ruim
            continue
        if isinstance(record, dict):
            found.append(record)
    return found


def _level_matches(records, media_type, name):
    """Registros do tipo pedido no primeiro nível que casa: `id` → `entity` → `prompt`/`library_key`."""
    key = _normal(name)
    typed = [r for r in records if r.get("type") == media_type]

    def prompt_or_key(record):
        provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
        return key in (_normal(provenance.get("prompt")), _normal(provenance.get("library_key")))

    for matches in (
        lambda r: _normal(r.get("id")) == key,
        lambda r: _normal(r.get("entity")) == key,
        prompt_or_key,
    ):
        found = [r for r in typed if matches(r)]
        if found:
            return found
    return []


def _same_file(record):
    """Quem é o arquivo do registro: `sha` no acervo global; no projeto, que não tem `sha`, o `path`."""
    return str(record.get("sha") or record.get("cached_path") or record.get("path") or record.get("id"))


def _pick(records, media_type, name):
    found = _level_matches(records, media_type, name)
    if len({_same_file(r) for r in found}) > 1:
        ids = ", ".join(sorted(str(r.get("id")) for r in found))
        raise PluginError(f'"{name}" é ambíguo no media-use: {ids}. Deixe um só registro com esse nome no acervo.')
    return found[0] if found else None


def _license(record):
    """Uma linha informativa, crua (o core prefixa; o exporter escapa): origem, provedor e descrição."""
    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
    parts = [f"media-use {record.get('source') or 'sem origem'}"]
    if provenance.get("provider"):
        parts.append(f"provider {provenance['provider']}")
    if record.get("description"):
        parts.append(" ".join(str(record["description"]).split())[:200])
    return "; ".join(parts)[:LICENSE_MAX_CHARS]


def _allowed_roots():
    """Raízes de `permissions.paths` do próprio manifesto: projeto fora delas nem é consultado."""
    try:
        manifest = json.loads(Path(__file__).with_name("getbrolls-plugin.json").read_text(encoding="utf-8"))
        paths = manifest["permissions"]["paths"]
    except (OSError, ValueError, KeyError, TypeError):
        paths = DEFAULT_PATHS
    return [Path(p).expanduser().resolve() for p in paths if isinstance(p, str)]


class MediaUseResolver:
    """`resolve(kind, name)`: projetos do `media-use` (`media_projects` no settings.json) e depois o acervo global."""

    def __init__(self, api):
        self.api = api

    def _projects(self):
        raw = self.api.config().get("media_projects") or []
        roots = _allowed_roots()
        projects = []
        for entry in raw if isinstance(raw, list) else []:
            try:
                folder = Path(entry).expanduser() if isinstance(entry, str) else None
                real = folder.resolve() if folder is not None and folder.is_absolute() else None
            except (OSError, RuntimeError, ValueError):
                # Entrada que nem vira caminho (NUL, `~outro` sem pasta pessoal): pula só ela.
                real = None
            if folder is not None and real is not None and any(real.is_relative_to(root) for root in roots):
                projects.append(folder)
        return projects

    def _from_project(self, folder, media_type, name):
        record = _pick(
            _records(folder / ".media" / LEDGER, f"projeto {folder.name}: .media/{LEDGER}"), media_type, name
        )
        relative = str((record or {}).get("path") or "")
        if not record or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            return None
        path = folder / relative
        return ResolverHit(str(path), _license(record)) if path.is_file() else None

    def _from_global(self, media_type, name):
        store = Path.home() / ".media"
        records = [r for r in _records(store / LEDGER, f"~/.media/{LEDGER}") if r.get("reusable") is True]
        record = _pick(records, media_type, name)
        cached = Path(str((record or {}).get("cached_path") or ""))
        if not record or not cached.is_absolute() or not (cached.parent / SENTINEL).is_file() or not cached.is_file():
            return None
        return ResolverHit(str(cached), _license(record))

    def __call__(self, kind, name):
        media_type = MEDIA_TYPES.get(kind)
        if media_type is None:
            return None
        for folder in self._projects():
            hit = self._from_project(folder, media_type, name)
            if hit is not None:
                return hit
        return self._from_global(media_type, name)


def register(api):
    api.exporter(
        "hyperframes",
        export,
        "Projeto HyperFrames editável a partir do roteiro revisado (pasta numerada em exports/hyperframes/)",
    )
    api.resolver("hyperframes_media", MediaUseResolver(api), ["sfx", "musica"])
