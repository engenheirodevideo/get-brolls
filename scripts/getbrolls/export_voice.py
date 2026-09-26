"""Voz de cada cena no export: duração real e trilha de áudio pelo ffprobe, e legenda por sidecar.

O A-ROLL gravado (`aroll/cNN[-take].<ext>`) manda na duração da cena. A legenda
palavra a palavra vem de `aroll/<mesmo nome>.transcript.json`, ao lado do vídeo, no
formato de palavras do HyperFrames (`[{"text", "start", "end"}]`, segundos relativos
ao arquivo). O sidecar é da pessoa: qualquer problema vira aviso e a cena volta para a
legenda estimada; o export nunca cai por causa dele.
"""

import json
import math
import stat
from pathlib import Path

from . import media

SIDECAR_SUFFIX = ".transcript.json"
SIDECAR_MAX_BYTES = 1024 * 1024
MAX_WORDS = 5000
MAX_WORD_CHARS = 100
# Folga do fim da última palavra sobre a duração do arquivo (arredondamento do whisper).
END_TOLERANCE_S = 0.05


def probe_voice(path):
    """`{"duration_s", "width", "height", "has_audio"}` do vídeo de voz; `ValueError` quando o ffprobe não lê."""
    raw = media.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        op="probe_voice",
    )
    try:
        data = json.loads(raw)
        streams = data.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if video is None:
            raise ValueError("arquivo sem vídeo")
        duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise ValueError("resposta do ffprobe ilegível") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duração ilegível")
    return {
        "duration_s": round(duration, 3),
        "width": video.get("width"),
        "height": video.get("height"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def sidecar_path(voice_path):
    """`aroll/c03.mov` → `aroll/c03.transcript.json` (mesmo nome, com take e lado)."""
    voice_path = Path(voice_path)
    return voice_path.with_name(voice_path.stem + SIDECAR_SUFFIX)


def _time(value):
    """Segundos de um item do sidecar, ou None: só número finito (bool e texto não contam)."""
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


def _word_problem(word, index, previous_start):
    """Motivo de uma palavra inválida (com o índice), ou None."""
    if not isinstance(word, dict):
        return f"item {index} não é objeto"
    text = word.get("text")
    if not isinstance(text, str) or not text.strip():
        return f"item {index} sem texto"
    if len(text) > MAX_WORD_CHARS or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):  # noqa: PLR2004 - control characters
        return f"item {index} com texto longo demais ou em mais de uma linha"
    start, end = _time(word.get("start")), _time(word.get("end"))
    if start is None or end is None or not 0 <= start < end:
        return f"item {index} com tempo inválido"
    if start < previous_start:
        return f"item {index} começa antes do anterior"
    return None


def _file_problem(sidecar):
    """Motivo para não ler o sidecar (link, pasta, grande demais), ou None."""
    try:
        info = sidecar.lstat()
    except OSError:
        return "não consegui ler"
    if stat.S_ISLNK(info.st_mode):
        return "é um link"
    if not stat.S_ISREG(info.st_mode):
        return "não é um arquivo"
    if info.st_size > SIDECAR_MAX_BYTES:
        return "passa de 1 MiB"
    return None


def _read(sidecar):
    """(lista crua, motivo): o arquivo tem que ser regular, pequeno e JSON UTF-8."""
    problem = _file_problem(sidecar)
    if problem:
        return None, problem
    try:
        data = json.loads(sidecar.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "não é JSON UTF-8"
    if not isinstance(data, list):
        return None, "não é uma lista de palavras"
    if len(data) > MAX_WORDS:
        return None, f"mais de {MAX_WORDS} palavras"
    return data, None


def _validated(data, voice_duration_s):
    """(palavras, motivo) depois de conferir cada item e o fim contra a duração da voz."""
    previous = 0.0
    for index, word in enumerate(data):
        problem = _word_problem(word, index, previous)
        if problem:
            return None, problem
        previous = word["start"]
    if data and data[-1]["end"] > voice_duration_s + END_TOLERANCE_S:
        return None, "passa da duração do vídeo"
    return data, None


def _shifted(words, start_s, duration_s):
    """Palavras em tempo global, cortadas à janela `[start_s, start_s + duration_s)`; e quantas caíram fora."""
    limit = round(start_s + duration_s, 3)
    kept, dropped = [], 0
    for word in words:
        start = round(word["start"] + start_s, 3)
        end = min(round(word["end"] + start_s, 3), limit)
        if start >= limit or end <= start:
            dropped += 1
            continue
        kept.append({"text": word["text"].strip(), "start": start, "end": end})
    return kept, dropped


def timed_words(voice_path, voice_duration_s, window, label):
    """(palavras em tempo global ou None, avisos) para a voz que toca na cena.

    `window` = `(start_s, duration_s)` da cena no vídeo inteiro; `label` = id da cena.
    Sem sidecar: `(None, [])` — a legenda é estimada, sem aviso.
    """
    voice_path = Path(voice_path)
    sidecar = sidecar_path(voice_path)
    relative = f"aroll/{sidecar.name}"
    if not sidecar.exists() and not sidecar.is_symlink():
        return None, []
    data, problem = _read(sidecar)
    if problem is None:
        try:
            if sidecar.stat().st_mtime_ns < voice_path.stat().st_mtime_ns:
                return None, [f"{label}: transcrição mais velha que o A-ROLL: gere de novo ({relative})"]
        except OSError:
            problem = "não consegui ler"
    if problem is None:
        data, problem = _validated(data, voice_duration_s)
    if problem is not None or data is None:
        return None, [f"{label}: {relative} inválido ({problem}): legenda estimada"]
    words, dropped = _shifted(data, *window)
    warnings = [f"{label}: {dropped} palavra(s) de {relative} passam do fim da cena"] if dropped else []
    return words, warnings
