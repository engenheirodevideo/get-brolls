"""Voz de cada cena no export: duração real e trilha de áudio pelo ffprobe, e legenda por transcrição.

O A-ROLL gravado (`aroll/cNN[-take].<ext>`) manda na duração da cena. A legenda
palavra a palavra vem, nesta ordem:

1. de `aroll/<mesmo nome>.transcript.json`, ao lado do vídeo, no formato de palavras
   do HyperFrames (`[{"text", "start", "end"}]`, segundos relativos ao arquivo). O
   sidecar é da pessoa e sempre ganha quando é válido;
2. da transcrição de `analysis/media/<media_id>/transcript.json` da mesma mídia,
   achada pelo índice sem hashear o vídeo (a chave rápida tem que conferir);
3. da estimativa.

Qualquer problema numa fonte vira aviso e a cena passa para a próxima; o export
nunca cai por causa delas.
"""

import errno
import json
import math
import os
import stat
from pathlib import Path

from . import media

SIDECAR_SUFFIX = ".transcript.json"
SIDECAR_MAX_BYTES = 1024 * 1024
MAX_WORDS = 5000
# Teto de palavras de uma transcrição de `analysis/` (o arquivo pode ter até 16 MiB).
MAX_ANALYSIS_WORDS = 200_000
MAX_WORD_CHARS = 100
# Folga do fim da última palavra sobre a duração do arquivo (arredondamento do whisper).
END_TOLERANCE_S = 0.05


class ProbeMissingError(ValueError):
    """O ffprobe não está instalado (executável não achado): o problema é da ferramenta, não do arquivo."""


def probe_voice(path):
    """`{"duration_s", "width", "height", "has_audio"}` do vídeo de voz; `ValueError` quando o ffprobe não lê
    e `ProbeMissingError` quando não há ffprobe."""
    try:
        raw = media.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            op="probe_voice",
        )
    except FileNotFoundError as exc:
        raise ProbeMissingError("ffprobe não encontrado") from exc
    except ValueError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):  # `media.run` embrulha o executável ausente
            raise ProbeMissingError(str(exc)) from exc
        raise
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
    if type(value) not in (int, float):
        return None
    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        return None
    return seconds if math.isfinite(seconds) else None


def _word_problem(word, index, previous_start):
    """Motivo de uma palavra inválida (com o índice), ou None."""
    if not isinstance(word, dict):
        return f"item {index} não é objeto"
    text = word.get("text")
    if not isinstance(text, str) or not text.strip():
        return f"item {index} sem texto"
    if len(text) > MAX_WORD_CHARS or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):  # noqa: PLR2004 - control characters  # pylint: disable=line-too-long
        return f"item {index} com texto longo demais ou em mais de uma linha"
    start, end = _time(word.get("start")), _time(word.get("end"))
    if start is None or end is None or not 0 <= start < end:
        return f"item {index} com tempo inválido"
    if start < previous_start:
        return f"item {index} começa antes do anterior"
    return None


def _info_problem(info):
    """Motivo para recusar o sidecar pelo `stat` (link, hardlink, pasta, grande demais), ou None."""
    if stat.S_ISLNK(info.st_mode):
        return "é um link"
    if not stat.S_ISREG(info.st_mode):
        return "não é um arquivo"
    if info.st_nlink != 1:
        return "é um link"
    if info.st_size > SIDECAR_MAX_BYTES:
        return "passa de 1 MiB"
    return None


def _open(sidecar):
    """(fd, motivo): recusa pelo `lstat` e abre sem seguir link nem travar em FIFO."""
    try:
        problem = _info_problem(sidecar.lstat())
    except OSError:
        return None, "não consegui ler"
    if problem:
        return None, problem
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        return os.open(sidecar, flags), None
    except OSError as exc:
        return None, "é um link" if exc.errno == errno.ELOOP else "não consegui ler"


def _read_limited(fd):
    """Até `SIDECAR_MAX_BYTES + 1` bytes do fd (um a mais revela o arquivo grande demais)."""
    chunks, size = [], 0
    while size <= SIDECAR_MAX_BYTES:
        chunk = os.read(fd, SIDECAR_MAX_BYTES + 1 - size)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _read_bytes(sidecar):
    """(bytes, mtime_ns, motivo): confere pelo `fstat` o mesmo arquivo que lê."""
    fd, problem = _open(sidecar)
    if fd is None:
        return None, None, problem
    raw = mtime_ns = None
    try:
        info = os.fstat(fd)
        problem = _info_problem(info)
        if problem is None:
            raw, mtime_ns = _read_limited(fd), info.st_mtime_ns
    except OSError:
        problem = "não consegui ler"
    finally:
        os.close(fd)
    if raw is not None and len(raw) > SIDECAR_MAX_BYTES:
        problem = "passa de 1 MiB"
    return (None, None, problem) if problem else (raw, mtime_ns, None)


def _read(sidecar):
    """(lista crua, mtime_ns, motivo): o arquivo tem que ser regular, pequeno e JSON UTF-8."""
    raw, mtime_ns, problem = _read_bytes(sidecar)
    if problem or raw is None:
        return None, None, problem
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):  # UnicodeDecodeError e JSONDecodeError são ValueError; número enorme também
        return None, None, "não é JSON UTF-8"
    if not isinstance(data, list):
        return None, None, "não é uma lista de palavras"
    if len(data) > MAX_WORDS:
        return None, None, f"mais de {MAX_WORDS} palavras"
    return data, mtime_ns, None


def _validated(data, voice_duration_s):
    """(palavras, motivo) depois de conferir cada item e o fim contra a duração da voz."""
    previous = 0.0
    for index, word in enumerate(data):
        problem = _word_problem(word, index, previous)
        if problem:
            return None, problem
        if word["end"] > voice_duration_s + END_TOLERANCE_S:
            return None, "passa da duração do vídeo"
        previous = word["start"]
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


def _sidecar_words(voice_path, voice_duration_s, window, label):
    """(palavras globais ou None, avisos) pelo sidecar da pessoa; sem sidecar, `(None, [])`."""
    sidecar = sidecar_path(voice_path)
    relative = f"aroll/{sidecar.name}"
    if not sidecar.exists() and not sidecar.is_symlink():
        return None, []
    data, mtime_ns, problem = _read(sidecar)
    if problem is None and mtime_ns is not None:
        try:
            if mtime_ns < voice_path.stat().st_mtime_ns:
                return None, [f"{label}: transcrição mais velha que o A-ROLL: gere de novo ({relative})"]
        except OSError:
            problem = "não consegui ler"
    if problem is None:
        data, problem = _validated(data, voice_duration_s)
    if problem is not None or data is None:
        return None, [f"{label}: {relative} inválido ({problem}): legenda estimada"]
    if not data:
        return None, [f"{label}: transcrição vazia: legenda estimada ({relative})"]
    words, dropped = _shifted(data, *window)
    warnings = [f"{label}: {dropped} palavra(s) de {relative} passam do fim da cena"] if dropped else []
    return words, warnings


def _relative_to(voice_path, project):
    try:
        return Path(voice_path).resolve().relative_to(Path(project).expanduser().resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _analysis_transcript(voice_path, label, project):
    """(transcrição `done`/`done_partial` de `analysis/` ou None, avisos); nunca hasheia o vídeo."""
    from . import analysis
    from .analysis_contract import Invalid

    rel = _relative_to(voice_path, project)
    state, entry = analysis.match(project, rel) if rel is not None else ("absent", None)
    if state == "stale":
        return None, [f"{label}: analysis/ é de outra versão do A-ROLL: rode analysis --action register"]
    if entry is None:
        return None, []
    try:
        doc = analysis.read_component(project, entry["media_id"], "transcript")
    except (Invalid, ValueError) as exc:
        code = getattr(exc, "code", "ilegível")
        return None, [f"{label}: transcrição de analysis/ inválida ({code}): legenda estimada"]
    if doc is None or doc["status"] not in ("done", "done_partial"):
        return None, []
    return doc, [f"{label}: transcrição de analysis/ parcial"] if doc["status"] == "done_partial" else []


def _analysis_words(voice_path, voice_duration_s, window, label, project):
    """(palavras globais ou None, avisos) pela transcrição de `analysis/` da mesma mídia."""
    doc, warnings = _analysis_transcript(voice_path, label, project)
    if doc is None:
        return None, warnings
    if len(doc["words"]) > MAX_ANALYSIS_WORDS:
        return None, [*warnings, f"{label}: transcrição de analysis/ com mais de {MAX_ANALYSIS_WORDS} palavras"]
    # Palavra de duração zero (comum no whisper) não vira legenda: sai antes da conferência.
    spoken = [{k: w[k] for k in ("text", "start", "end")} for w in doc["words"] if w["end"] > w["start"]]
    data, problem = _validated(spoken, voice_duration_s)
    if problem is not None or data is None:
        return None, [*warnings, f"{label}: transcrição de analysis/ inválida ({problem}): legenda estimada"]
    if not data:
        return None, warnings
    words, dropped = _shifted(data, *window)
    if dropped:
        warnings.append(f"{label}: {dropped} palavra(s) da transcrição de analysis/ passam do fim da cena")
    return words, warnings


def timed_words(voice_path, voice_duration_s, window, label, project=None):
    """(palavras em tempo global ou None, avisos) para a voz que toca na cena.

    `window` = `(start_s, duration_s)` da cena no vídeo inteiro; `label` = id da cena;
    `project` = raiz do projeto, para achar a transcrição de `analysis/` (sem ele, só o
    sidecar vale). Sem nenhuma transcrição: `(None, [])` — a legenda é estimada, sem aviso.
    """
    voice_path = Path(voice_path)
    words, warnings = _sidecar_words(voice_path, voice_duration_s, window, label)
    if words is not None or project is None:
        return words, warnings
    found, more = _analysis_words(voice_path, voice_duration_s, window, label, project)
    return found, warnings + more
