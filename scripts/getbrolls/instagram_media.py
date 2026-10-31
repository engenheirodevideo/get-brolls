"""Instagram media: the signed-URL download, the ffmpeg merge, the ffprobe checks and the exclusive publish.

Loaded as `getbrolls.instagram_media`, never as a script: only `instagram_pairs` is the entry point.
"""

# pylint: disable=missing-function-docstring,raise-missing-from,too-many-arguments,too-many-locals
# pylint: disable=too-many-statements,broad-exception-caught

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import effects, instagram_common, labels, logs
from .instagram_common import CollectError, die
from .instagram_urls import curl_config_input, parse_curl_config, resolve_config_output
from .runtime import stderr_tail

# The same logger as the entry module (`getbrolls.instagram_pairs`): the log lines keep their source name.
_log = logs.get("instagram_pairs")

# curl exit 22 com um destes códigos HTTP no stderr significa que a CDN está nos recusando.
BLOCKED_RE = re.compile(r"(?:error|returned error|HTTP/[\d.]+)\s*:?\s*(401|403|429)\b", re.IGNORECASE)
# O `oe` da URL assinada da CDN: o instante em que ela deixa de valer, em segundos Unix, em hexadecimal.
_OE_RE = re.compile(r"^[0-9A-Fa-f]{1,16}$")
OE_SKEW_S = 300  # relógio local adiantado: um `oe` vencido há menos disso ainda pode ser uma recusa de verdade


def ensure_tool(name: str) -> None:
    """Encerra com erro se a ferramenta externa não estiver no PATH."""
    if shutil.which(name) is None:
        die(f"comando obrigatório não encontrado: {name}")


def _reused_part_is_valid(path: Path) -> bool:
    """Sanidade rápida via ffprobe numa parte reaproveitada; mídia corrompida/truncada não segue em silêncio."""
    try:
        effects.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.SubprocessError, OSError):
        return False


def _log_pair_stage(stage: str | None, started: float, *, status: str, size: int | None = None) -> None:
    logs.event(
        _log,
        logging.INFO if status == "ok" else logging.WARNING,
        "pair_stage",
        stage=stage,
        ms=round((time.monotonic() - started) * 1000),
        status=status,
        bytes=size,
    )


def signed_url_expired(url: str, now: float | None = None) -> bool:
    """True só quando a URL assinada traz um `oe` legível, único, não nulo e vencido há mais de `OE_SKEW_S`.

    Sem `oe`, com um `oe` ilegível, repetido ou zero, ou vencido há pouco, a expiração não está confirmada:
    quem chama trata o 403 como recusa e para para diagnosticar, em vez de supor que bastava recapturar.
    """
    try:
        values = parse_qs(urlsplit(url).query, keep_blank_values=True).get("oe", [])
    except ValueError:
        return False
    if len(values) != 1 or not _OE_RE.match(values[0]):
        return False
    stamp = int(values[0], 16)
    return stamp > 0 and stamp + OE_SKEW_S < (time.time() if now is None else now)


def _cdn_error(status: str, where: str, url: str) -> CollectError:
    """O erro de um curl 22 com 401/403/429, já impresso; a mensagem nunca leva a URL."""
    if status == "429":
        message = (
            f"CDN limitou o ritmo do config {where} com HTTP 429; pare o lote, aguarde o cooldown e "
            "volte ao mesmo escopo antes de tentar de novo"
        )
        error = CollectError(message, cooldown=True)
    elif status == "403" and signed_url_expired(url):
        # Sem o número do status: a fila lê "403" como recusa, e aqui o motivo é outro.
        message = (
            f"URL assinada expirada (oe no passado) no config {where}; "
            "recapture pela mesma rota autorizada antes de tentar de novo"
        )
        error = CollectError(message, expired=True)
    else:
        message = (
            f"CDN recusou o config {where} com HTTP {status}; pare o lote e diagnostique a causa com a "
            "pessoa (sessão, desafio, bloqueio). Esperar não autoriza retomar"
        )
        error = CollectError(message, refused=True)
    # Inlined `die()`: the raised error also carries the HTTP status (`http_status`) for the
    # later `blocked` log line in `_record_failure`, without changing the printed message.
    print(f"ERROR: {message}", file=sys.stderr)
    error.http_status = status
    return error


def _reuse_existing(
    *,
    config_output: Path | None,
    part_path: Path,
    force_download: bool,
    prefer_config_output: bool,
    stage: str | None,
) -> str | None:
    """The action name when an earlier output or `.part` already holds the media, else None (download it)."""
    if (
        prefer_config_output
        and not force_download
        and config_output
        # lenient: reuse of a previous output; absent means download again
        and config_output.exists()
        and config_output.stat().st_size > 0
    ):
        if not _reused_part_is_valid(config_output):
            die(f"parte reutilizada está corrompida; use --force-download: {config_output}")
        effects.mkdir(part_path.parent, parents=True, exist_ok=True)
        if config_output.resolve() != part_path.resolve():
            effects.copy_file(config_output, part_path, metadata=True)
        logs.event(_log, logging.INFO, "pair_reuse", stage=stage)
        return "reused-config-output"

    # lenient: reuse of a .part; absent means download again
    if part_path.exists() and part_path.stat().st_size > 0 and not force_download:
        if not _reused_part_is_valid(part_path):
            die(f"parte reutilizada está corrompida; use --force-download: {part_path}")
        logs.event(_log, logging.INFO, "pair_reuse", stage=stage)
        return "reused-part"
    return None


def _fetch_part(  # noqa: PLR0913 - the config, the part and the log context of one transfer
    parsed: dict[str, str | None], *, cfg_path: Path, part_path: Path, where: str, stage: str | None, started: float
) -> int:
    """Run curl for one config into `part_path`; the size of the downloaded part."""
    effects.mkdir(part_path.parent, parents=True, exist_ok=True)
    # Preserva partes existentes válidas até a transferência substituta ter sucesso.
    with effects.temp_dir(folder=part_path.parent) as stage_dir:
        pending = Path(stage_dir) / "download.part"
        cmd = [
            "curl",
            "-q",  # first option: no ~/.curlrc (a proxy, a header or a netrc of the person's) in this transfer
            "--fail",
            "--proto",
            "=https",
            "--connect-timeout",
            "20",
            "--max-time",
            "180",
            "--output",
            str(pending),
        ]
        if parsed["curl_resolve"]:
            cmd += ["--resolve", str(parsed["curl_resolve"])]
        # The signed URL travels on stdin, never in the argument list (`ps` shows every argument).
        cmd += ["--config", "-"]
        print(f"+ curl <url-de {cfg_path}> --output {part_path}", file=sys.stderr)
        try:
            effects.run(cmd, check=True, capture_output=True, timeout=600, input=curl_config_input(str(parsed["url"])))
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or b""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            curl_fail = exc.returncode == 22  # noqa: PLR2004 - curl --fail; ver comentário no topo do arquivo
            blocked = BLOCKED_RE.search(stderr) if curl_fail else None
            if blocked:
                _log_pair_stage(stage, started, status="error")
                raise _cdn_error(blocked.group(1), where, str(parsed["url"]))  # noqa: B904 - as die() does
            tail = stderr_tail(stderr)
            _log_pair_stage(stage, started, status="error")
            die(f"curl falhou para o config {where} (exit {exc.returncode}): {tail}")
        except subprocess.TimeoutExpired as exc:
            _log_pair_stage(stage, started, status="error")
            die(
                f"curl timed out after {exc.timeout}s para o config {where}; recapture URLs "
                "expiradas/proibidas e tente de novo"
            )
        except (subprocess.SubprocessError, OSError):
            _log_pair_stage(stage, started, status="error")
            die(f"curl falhou para o config {where}; recapture URLs expiradas/proibidas e tente de novo")
        # lenient: post-condition of our own curl write into the pending file
        if not pending.is_file() or pending.stat().st_size == 0:
            _log_pair_stage(stage, started, status="error")
            die(f"mídia vazia para o config {where}")
        size = pending.stat().st_size
        effects.replace(pending, part_path)
    return size


def download_or_reuse(  # noqa: PLR0913 - existing size; one branch per source combination
    *,
    cfg_path: Path,
    part_path: Path,
    config_output_root: Path,
    force_download: bool,
    prefer_config_output: bool,
    stage: str | None = None,
) -> str:
    """Baixa a mídia do config curl ou reaproveita a parte já baixada."""
    started = time.monotonic()
    where = labels.shown(cfg_path)  # in the messages: the folder by its label, so the prose after it survives
    parsed = parse_curl_config(cfg_path)
    config_output = resolve_config_output(parsed["output"], config_output_root)
    reused = _reuse_existing(
        config_output=config_output,
        part_path=part_path,
        force_download=force_download,
        prefer_config_output=prefer_config_output,
        stage=stage,
    )
    if reused is not None:
        return reused
    size = _fetch_part(parsed, cfg_path=cfg_path, part_path=part_path, where=where, stage=stage, started=started)
    _log_pair_stage(stage, started, status="ok", size=size)
    return "downloaded"


def merge_parts(video_part: Path, audio_part: Path, output: Path, *, copy_streams: bool) -> None:
    """Junta as partes de vídeo e áudio num único arquivo com ffmpeg."""
    effects.mkdir(output.parent, parents=True, exist_ok=True)
    if copy_streams:
        cmd = [
            "ffmpeg",
            "-n",
            "-v",
            "error",
            "-i",
            str(video_part),
            "-i",
            str(audio_part),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(output),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-n",
            "-v",
            "error",
            "-i",
            str(video_part),
            "-i",
            str(audio_part),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
    instagram_common.run(cmd, quiet=True)


def ffprobe_json(path: Path) -> dict:
    """Devolve a saída JSON do ffprobe para o arquivo."""
    result = instagram_common.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        quiet=True,
    )
    return json.loads(result.stdout)


def audio_hash(path: Path) -> str:
    """Devolve o hash do fluxo de áudio copiado do arquivo."""
    with effects.temp_dir(prefix="getbrolls-ig-audiohash-") as tmp:
        audio = Path(tmp) / "audio.aac"
        instagram_common.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", str(audio)],
            quiet=True,
        )
        h = hashlib.sha256()
        with audio.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()


def verify_output(path: Path, *, compute_audio_hash: bool = True) -> dict:
    """Confere com ffprobe os fluxos do arquivo final e devolve o resumo."""
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not video_streams:
        die(f"a saída não tem stream de vídeo: {path}")
    if not audio_streams:
        die(f"a saída não tem stream de áudio: {path}")
    v0 = video_streams[0]
    a0 = audio_streams[0]
    return {
        "output": str(path),
        "size": int(data.get("format", {}).get("size", 0)),
        "duration": float(data.get("format", {}).get("duration", 0.0)),
        "video_codec": v0.get("codec_name"),
        "pix_fmt": v0.get("pix_fmt"),
        "width": v0.get("width"),
        "height": v0.get("height"),
        "audio_codec": a0.get("codec_name"),
        # Só é calculado com --fail-on-duplicate-audio (remux + SHA-256 não é grátis);
        # a chave permanece presente para o schema do resumo ser estável nos dois casos.
        "audio_hash_sha256": audio_hash(path) if compute_audio_hash else None,
    }


def publish_exclusive(source: Path, destination: Path) -> None:
    """Copia a origem para o destino sem sobrescrever um arquivo existente."""
    created = False
    try:
        with (
            effects.op("fs.copy", destination, fallback=(FileExistsError,)),
            source.open("rb") as incoming,
            destination.open("xb") as outgoing,
        ):
            created = True
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except FileExistsError:
        die(f"a saída já existe; nada foi sobrescrito: {destination}")
    except BaseException:
        if created:
            effects.unlink(destination, missing_ok=True)
        raise
