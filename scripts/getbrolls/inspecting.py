"""Ler o que a fonte já conta sobre si antes de pedir mídia: onde olhar, e por quê.

Legendas, capítulos e os tempos escritos na descrição dizem, de graça, onde cada
assunto acontece no vídeo. Aqui isso vira janelas candidatas pontuadas contra a
frase do usuário — só biblioteca padrão, nada de rede e nada gravado.
"""

import re
import unicodedata

# Janela de legenda: junta falas vizinhas até esse tanto de segundos.
MAX_SUBTITLE_WINDOW_S = 12.0
# Buraco máximo entre duas falas para elas contarem como a mesma janela.
MAX_CUE_GAP_S = 1.5
# Sem fim conhecido (capítulo aberto, tempo escrito na descrição), use isto.
DEFAULT_WINDOW_S = 12.0

_TIME_RE = re.compile(
    r"(?:(?P<h>\d{1,3}):)?(?P<m>\d{1,2}):(?P<s>\d{2})(?:[.,](?P<ms>\d{1,3}))?"
)
_CUE_RE = re.compile(
    r"^(?P<start>(?:\d{1,3}:)?\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(?P<end>(?:\d{1,3}:)?\d{1,2}:\d{2}[.,]\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]*>")
# "00:10 chegada da poeira" / "1:05 — céu laranja": tempo no começo da linha.
_DESCRIPTION_RE = re.compile(
    r"^\s*\[?((?:\d{1,3}:)?\d{1,2}:\d{2})\]?\s*[-–—:.)]?\s*(?P<text>.+?)\s*$"
)


def _seconds(stamp):
    match = _TIME_RE.fullmatch(stamp.strip())
    if not match:
        return None
    hours = int(match.group("h") or 0)
    ms = (match.group("ms") or "0").ljust(3, "0")
    return hours * 3600 + int(match.group("m")) * 60 + int(match.group("s")) + int(ms) / 1000


def parse_vtt(text):
    """Cues `{start_s, end_s, text}` de um WebVTT; lixo vira lista vazia, não exceção."""
    cues = []
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        match = _CUE_RE.match(lines[index].strip())
        index += 1
        if not match:
            continue
        start = _seconds(match.group("start"))
        end = _seconds(match.group("end"))
        body = []
        while index < len(lines) and lines[index].strip():
            body.append(_TAG_RE.sub("", lines[index]).strip())
            index += 1
        spoken = " ".join(part for part in body if part).strip()
        if start is None or end is None or end <= start or not spoken:
            continue
        # Legenda automática repete a linha anterior para simular rolagem.
        if cues and cues[-1]["text"] == spoken:
            cues[-1]["end_s"] = end
            continue
        cues.append({"start_s": start, "end_s": end, "text": spoken})
    return cues


def tokens(text):
    """Palavras normalizadas: sem acento, sem caixa, sem pontuação."""
    flat = unicodedata.normalize("NFKD", str(text or ""))
    flat = "".join(ch for ch in flat if not unicodedata.combining(ch)).lower()
    return [word for word in re.split(r"[^0-9a-z]+", flat) if word]


def score(query, text):
    """Fração das palavras da frase do usuário que aparecem na janela (0 sem frase)."""
    wanted = set(tokens(query))
    if not wanted:
        return 0.0
    found = wanted & set(tokens(text))
    return round(len(found) / len(wanted), 4)


def description_timestamps(description):
    """Marcações `mm:ss` escritas na descrição, com o texto que as acompanha."""
    marks = []
    for line in str(description or "").splitlines():
        match = _DESCRIPTION_RE.match(line)
        if not match:
            continue
        start = _seconds(match.group(1))
        text = match.group("text").strip()
        if start is None or not text:
            continue
        marks.append({"start_s": start, "text": text})
    return marks


def _subtitle_windows(cues):
    windows = []
    for cue in cues:
        if (
            windows
            and cue["start_s"] - windows[-1]["end_s"] <= MAX_CUE_GAP_S
            and cue["end_s"] - windows[-1]["start_s"] <= MAX_SUBTITLE_WINDOW_S
        ):
            windows[-1]["end_s"] = cue["end_s"]
            windows[-1]["text"] += " " + cue["text"]
            continue
        windows.append(dict(cue))
    return windows


def candidate_windows(probe, query=None, max_windows=3):
    """Trechos que valem olhar, do mais parecido com a frase para o menos.

    `probe` é o retorno de `social.probe_remote`. Sem frase, ninguém pontua: a ordem
    passa a ser cronológica. Nada aqui inventa intervalo além da duração conhecida.
    """
    duration = probe.get("duration_s")
    raw = []
    for language in sorted((probe.get("subtitles") or {})):
        cues = (probe["subtitles"][language] or {}).get("cues") or []
        for window in _subtitle_windows(cues):
            raw.append({**window, "source": "subtitle"})
    for chapter in probe.get("chapters") or []:
        start = chapter.get("start_s")
        end = chapter.get("end_s")
        if start is None:
            continue
        raw.append({
            "start_s": float(start),
            "end_s": float(end if end is not None else start + DEFAULT_WINDOW_S),
            "text": chapter.get("title") or "",
            "source": "chapter",
        })
    marks = description_timestamps(probe.get("description"))
    for position, mark in enumerate(marks):
        following = marks[position + 1]["start_s"] if position + 1 < len(marks) else None
        end = min(x for x in (following, mark["start_s"] + DEFAULT_WINDOW_S) if x is not None)
        raw.append({
            "start_s": mark["start_s"],
            "end_s": end,
            "text": mark["text"],
            "source": "description_timestamp",
        })
    windows = []
    seen = set()
    for window in raw:
        start = max(0.0, float(window["start_s"]))
        end = float(window["end_s"])
        if duration:
            start = min(start, float(duration))
            end = min(end, float(duration))
        if end <= start or not (window.get("text") or "").strip():
            continue
        key = (round(start, 3), round(end, 3), window["source"])
        if key in seen:
            continue
        seen.add(key)
        windows.append({
            "start_s": round(start, 3),
            "end_s": round(end, 3),
            "text": window["text"].strip(),
            "source": window["source"],
            "score": score(query, window["text"]),
        })
    windows.sort(key=lambda w: (-w["score"], w["start_s"]))
    limit = max(1, int(max_windows or 1))
    return windows[:limit]
