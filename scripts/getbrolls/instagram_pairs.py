#!/usr/bin/env python3
"""Baixa e mescla pares de configs curl de vídeo/áudio de Reels do Instagram.

Um navegador autorizado captura URLs diretas da CDN como arquivos de config curl, um
config somente vídeo e um config somente áudio. Este script baixa ou reaproveita as
partes, mescla com ffmpeg e verifica os streams/hash de áudio da saída.

Nunca imprime URLs assinadas da CDN. Lotes têm ritmo (pausa aleatória entre stems, teto
por execução) e param no primeiro HTTP 403/429 para nunca martelar uma conta enquanto a
CDN já está recusando.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

URL_RE = re.compile(r'^\s*url\s*=\s*"(.*)"\s*$')
OUTPUT_RE = re.compile(r'^\s*output\s*=\s*"(.*)"\s*$')
if __package__:
    from . import logs
    from .runtime import redact, stderr_tail
else:  # Executado diretamente como `python3 scripts/getbrolls/instagram_pairs.py`, per docs/GUIDE.md.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from getbrolls import logs
    from getbrolls.runtime import redact, stderr_tail

# Importing `logs` (above) registers a NullHandler on the shared `getbrolls` logger at
# module import time (see logs.py), which is what keeps Python's own "handler of last
# resort" from ever printing a bare record to stderr before `logs.configure()` runs (or
# when it never runs, e.g. this script invoked without --project). Verified in
# tests/test_logging_social.py: nothing reaches stderr from logging alone when
# unconfigured, at any level.
_log = logs.get("instagram_pairs")

HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
PACE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
# curl exit 22 com um destes códigos HTTP no stderr significa que a CDN está nos recusando.
BLOCKED_RE = re.compile(r"(?:error|returned error|HTTP/[\d.]+)\s*:?\s*(403|429)\b", re.IGNORECASE)
DEFAULT_PACE = "20-60"
DEFAULT_MAX_PER_RUN = 25
# Same shape `infer_output_for_stem` uses to recognize a "student" stem (`<username>_<rank>_<code>`).
_STUDENT_STEM_RE = re.compile(r"^(.+)_([0-9]{2})_(.+)$")


def _safe_item_id(stem: str) -> str:
    """A loggable per-item id derived from `stem`.

    Never the raw stem verbatim when it embeds a username (the `student` layout's
    `<username>_<rank>_<code>` shape) — usernames/handles must never reach the log, so
    only the trailing code survives. Any other stem shape is short and username-free
    already, so it is logged as-is.
    """
    match = _STUDENT_STEM_RE.match(stem)
    if match and not re.match(r"^[0-9]{2}$", match.group(1)):
        return match.group(3)
    return stem


class CollectError(Exception):
    """Erro de coleta com mensagem redigida, código de saída e sinal de cooldown."""

    def __init__(self, message, code: int = 1, *, cooldown: bool = False):
        super().__init__(message)
        self.message = redact(message)
        self.code = code
        self.cooldown = cooldown
        # Only set (to the HTTP status text) for a CDN-block cooldown error; used solely
        # by the `blocked` log line, never by control flow.
        self.http_status: str | None = None


def die(message: str, code: int = 1, *, cooldown: bool = False) -> NoReturn:
    """Imprime o erro e levanta CollectError com a mensagem redigida para os resumos do lote."""
    print(f"ERROR: {message}", file=sys.stderr)
    raise CollectError(message, code, cooldown=cooldown)


def parse_pace(value: str) -> tuple[int, int]:
    """`MIN-MAX` segundos entre stems, `N` para pausa fixa, `0` para desabilitar."""
    match = PACE_RE.match((value or "").strip())
    if not match:
        die(f"--pace deve ser MIN-MAX em segundos ou 0, recebido: {value!r}")
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) is not None else low
    if low > high:
        die(f"--pace: o mínimo excede o máximo: {value}")
    return low, high


def run(cmd: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    if not quiet:
        print("+ " + " ".join(sh_quote(x) for x in cmd), file=sys.stderr)
    try:
        return subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = stderr_tail(exc.stderr or "")
        die(f"{cmd[0]} falhou (exit {exc.returncode}): {tail}")
        raise  # pragma: no cover - die() sempre levanta


def sh_quote(value: str) -> str:
    if not value:
        return "''"
    if re.match(r"^[A-Za-z0-9_./:=@%+-]+$", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def _die_url_refused(reason: str, message: str, code: int = 1) -> NoReturn:
    """Like `die()`, plus a `media_url_refused` log line carrying only a fixed `reason`
    code — never the message/path, which can carry a config filename or address."""
    logs.event(_log, logging.WARNING, "media_url_refused", reason=reason)
    die(message, code)


def _curl_resolution(host: str, source: Path) -> str | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            _die_url_refused("dns_resolution_failed", f"não foi possível resolver o hostname do config curl: {source}")
        addresses = sorted({row[4][0] for row in rows})
        if not addresses:
            _die_url_refused("dns_no_address", f"hostname do config curl sem endereço: {source}")
        parsed = [ipaddress.ip_address(item) for item in addresses]
        if any(not item.is_global for item in parsed):
            _die_url_refused("private_address", f"hostname do config curl resolveu para endereço privado: {source}")
        selected = parsed[0]
        target = f"[{selected}]" if selected.version == 6 else str(selected)
        return f"{host}:443:{target}"
    if not address.is_global:
        _die_url_refused("private_address", f"a URL do config curl não pode apontar para endereço privado: {source}")
    return None


def validate_media_url(value: str, source: Path) -> tuple[str, str | None]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _die_url_refused("invalid_url", f"URL inválida no config curl: {source}")
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        _die_url_refused("scheme_or_host_invalid", f"a URL do config curl deve usar HTTPS público: {source}")
    if parsed.username is not None or parsed.password is not None:
        _die_url_refused("credentials_in_url", f"a URL do config curl não pode conter credenciais: {source}")
    if port not in (None, 443):
        _die_url_refused("nonstandard_port", f"a URL do config curl deve usar a porta HTTPS padrão: {source}")
    host = parsed.hostname.lower()
    if host.endswith("."):
        _die_url_refused("trailing_dot_host", f"o hostname do config curl não pode terminar com ponto: {source}")
    if host == "localhost" or host.endswith((".localhost", ".local")):
        _die_url_refused("local_host", f"a URL do config curl não pode apontar para host local: {source}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not HOST_RE.fullmatch(host):
            _die_url_refused("invalid_hostname", f"hostname inválido no config curl: {source}")
    return value, _curl_resolution(host, source)


def parse_curl_config(path: Path) -> dict[str, str | None]:
    text = path.read_text(encoding="utf-8", errors="replace")
    url: str | None = None
    output: str | None = None
    for line in text.splitlines():
        url_match = URL_RE.match(line)
        if url_match:
            url = url_match.group(1)
            continue
        output_match = OUTPUT_RE.match(line)
        if output_match:
            output = output_match.group(1)
    if not url:
        die(f"linha url ausente no config curl: {path}")
    url, curl_resolve = validate_media_url(url, path)
    return {"url": url, "output": output, "curl_resolve": curl_resolve}


def resolve_config_output(config_output: str | None, root: Path) -> Path | None:
    if not config_output:
        return None
    root = root.resolve()
    output = Path(config_output)
    candidate = output.resolve() if output.is_absolute() else (root / output).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        die("config output deve permanecer dentro de --config-output-root")
    return candidate


def infer_output_for_stem(stem: str, output_dir: Path, layout: str) -> Path:
    if not STEM_RE.fullmatch(stem) or ".." in stem:
        die(f"stem de config inseguro: {stem}")
    if layout == "flat":
        return output_dir / f"{stem}.mp4"
    student_match = re.match(r"^(.+)_([0-9]{2})_(.+)$", stem)
    is_student_stem = student_match is not None and not re.match(r"^[0-9]{2}$", student_match.group(1))
    if layout == "student" or (layout == "auto" and is_student_stem):
        if not student_match:
            die(f"layout=student exige stem '<username>_<rank>_<code>', recebido: {stem}")
        username, rank, code = student_match.groups()
        candidate = output_dir / username / f"{rank}_{code}.mp4"
    else:
        candidate = output_dir / f"{stem}.mp4"
    try:
        candidate.resolve().relative_to(output_dir.resolve())
    except ValueError:
        die(f"a saída do lote deve permanecer dentro de --output-dir: {stem}")
    return candidate


def pair_configs(config_dir: Path) -> list[tuple[str, Path, Path]]:
    video_configs = sorted(config_dir.glob("*_video.conf"))
    pairs: list[tuple[str, Path, Path]] = []
    for video_config in video_configs:
        stem = video_config.name[: -len("_video.conf")]
        audio_config = config_dir / f"{stem}_audio.conf"
        if not audio_config.exists():
            die(f"config de áudio ausente para {stem}: {audio_config}")
        pairs.append((stem, video_config, audio_config))
    if not pairs:
        die(f"nenhum *_video.conf encontrado em {config_dir}")
    return pairs


def ensure_tool(name: str) -> None:
    if shutil.which(name) is None:
        die(f"comando obrigatório não encontrado: {name}")


def _reused_part_is_valid(path: Path) -> bool:
    """Sanidade rápida via ffprobe numa parte reaproveitada; mídia corrompida/truncada não pode seguir em silêncio."""
    try:
        subprocess.run(
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


def download_or_reuse(
    *,
    cfg_path: Path,
    part_path: Path,
    config_output_root: Path,
    force_download: bool,
    prefer_config_output: bool,
    stage: str | None = None,
) -> str:
    started = time.monotonic()
    parsed = parse_curl_config(cfg_path)
    config_output = resolve_config_output(parsed["output"], config_output_root)

    if (
        prefer_config_output
        and not force_download
        and config_output
        and config_output.exists()
        and config_output.stat().st_size > 0
    ):
        if not _reused_part_is_valid(config_output):
            die(f"parte reutilizada está corrompida; use --force-download: {config_output}")
        part_path.parent.mkdir(parents=True, exist_ok=True)
        if config_output.resolve() != part_path.resolve():
            shutil.copy2(config_output, part_path)
        logs.event(_log, logging.INFO, "pair_reuse", stage=stage)
        return "reused-config-output"

    if part_path.exists() and part_path.stat().st_size > 0 and not force_download:
        if not _reused_part_is_valid(part_path):
            die(f"parte reutilizada está corrompida; use --force-download: {part_path}")
        logs.event(_log, logging.INFO, "pair_reuse", stage=stage)
        return "reused-part"

    part_path.parent.mkdir(parents=True, exist_ok=True)
    # Preserva partes existentes válidas até a transferência substituta ter sucesso.
    with tempfile.TemporaryDirectory(dir=part_path.parent) as stage_dir:
        pending = Path(stage_dir) / "download.part"
        cmd = [
            "curl",
            "--fail",
            "--proto",
            "=https",
            "--retry",
            "2",
            "--retry-delay",
            "5",
            "--connect-timeout",
            "20",
            "--max-time",
            "180",
            "--output",
            str(pending),
        ]
        if parsed["curl_resolve"]:
            cmd += ["--resolve", str(parsed["curl_resolve"])]
        cmd.append(str(parsed["url"]))
        print(f"+ curl <url-de {cfg_path}> --output {part_path}", file=sys.stderr)
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or b""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            blocked = BLOCKED_RE.search(stderr) if exc.returncode == 22 else None
            if blocked:
                _log_pair_stage(stage, started, status="error")
                message = (
                    f"CDN recusou o config {cfg_path} com HTTP {blocked.group(1)}; pare o lote, "
                    "aguarde o cooldown e recapture as URLs antes de tentar de novo"
                )
                # Inlined `die()`: only this branch needs the raised error to carry the
                # HTTP status separately (`http_status`), for the later `blocked` log line
                # in `_record_failure`, without changing the printed/raised message.
                print(f"ERROR: {message}", file=sys.stderr)
                error = CollectError(message, cooldown=True)
                error.http_status = blocked.group(1)
                raise error  # noqa: B904 - implicit context, matching die()'s own raise
            tail = stderr_tail(stderr)
            _log_pair_stage(stage, started, status="error")
            die(f"curl falhou para o config {cfg_path} (exit {exc.returncode}): {tail}")
        except subprocess.TimeoutExpired as exc:
            _log_pair_stage(stage, started, status="error")
            die(
                f"curl timed out after {exc.timeout}s para o config {cfg_path}; recapture URLs expiradas/proibidas e tente de novo"
            )
        except (subprocess.SubprocessError, OSError):
            _log_pair_stage(stage, started, status="error")
            die(f"curl falhou para o config {cfg_path}; recapture URLs expiradas/proibidas e tente de novo")
        if not pending.is_file() or pending.stat().st_size == 0:
            _log_pair_stage(stage, started, status="error")
            die(f"mídia vazia para o config {cfg_path}")
        size = pending.stat().st_size
        os.replace(pending, part_path)
    _log_pair_stage(stage, started, status="ok", size=size)
    return "downloaded"


def merge_parts(video_part: Path, audio_part: Path, output: Path, *, copy_streams: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
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
    run(cmd, quiet=True)


def ffprobe_json(path: Path) -> dict:
    result = run(
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
    with tempfile.TemporaryDirectory(prefix="getbrolls-ig-audiohash-") as tmp:
        audio = Path(tmp) / "audio.aac"
        run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy", str(audio)],
            quiet=True,
        )
        h = hashlib.sha256()
        with audio.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()


def verify_output(path: Path, *, compute_audio_hash: bool = True) -> dict:
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
    created = False
    try:
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            created = True
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    except FileExistsError:
        die(f"a saída já existe; nada foi sobrescrito: {destination}")
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def process_one(
    *,
    stem: str,
    video_config: Path,
    audio_config: Path,
    output: Path,
    parts_dir: Path,
    config_output_root: Path,
    force_download: bool,
    prefer_config_output: bool,
    copy_streams: bool,
    compute_audio_hash: bool = True,
) -> dict:
    if not STEM_RE.fullmatch(stem) or ".." in stem:
        die(f"stem de config inseguro: {stem}")
    if output.exists():
        die(f"a saída já existe; nada foi sobrescrito: {output}")
    safe_stem = stem
    video_part = parts_dir / f"{safe_stem}_video.mp4"
    audio_part = parts_dir / f"{safe_stem}_audio.mp4"
    video_action = download_or_reuse(
        cfg_path=video_config,
        part_path=video_part,
        config_output_root=config_output_root,
        force_download=force_download,
        prefer_config_output=prefer_config_output,
        stage="video",
    )
    audio_action = download_or_reuse(
        cfg_path=audio_config,
        part_path=audio_part,
        config_output_root=config_output_root,
        force_download=force_download,
        prefer_config_output=prefer_config_output,
        stage="audio",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as stage:
        pending = Path(stage) / "merged.mp4"
        merge_started = time.monotonic()
        try:
            merge_parts(video_part, audio_part, pending, copy_streams=copy_streams)
        except BaseException:
            _log_pair_stage("merge", merge_started, status="error")
            raise
        _log_pair_stage("merge", merge_started, status="ok", size=pending.stat().st_size if pending.exists() else None)
        validate_started = time.monotonic()
        try:
            verification = verify_output(pending, compute_audio_hash=compute_audio_hash)
        except BaseException:
            _log_pair_stage("validation", validate_started, status="error")
            raise
        _log_pair_stage("validation", validate_started, status="ok", size=verification.get("size"))
        publish_exclusive(pending, output)
    verification["output"] = str(output)
    verification.update(
        {
            "stem": stem,
            "video_config": str(video_config),
            "audio_config": str(audio_config),
            "video_part_action": video_action,
            "audio_part_action": audio_action,
        }
    )
    return verification


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Baixa/mescla pares de configs curl de vídeo+áudio de Reels do Instagram sem imprimir URLs assinadas.",
        epilog=(
            "Lotes grandes: use --pace MIN-MAX (padrão 20-60s) e --max-per-run (padrão 25) para dividir "
            "o lote em execuções menores e manter o ritmo. O resumo em --summary-json é sempre gravado de forma "
            "incremental, com ou sem --continue-on-error; a flag só muda o que acontece após uma falha comum: "
            "sem ela o lote é interrompido (403/429 sempre interrompe, com ou sem a flag); com ela, o lote segue "
            "para o próximo stem. "
            "Veja a seção Instagram (navegador/Playwright, dois streams e pares CDN) em docs/GUIDE.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--config-dir", type=Path, help="Diretório contendo pares *_video.conf e *_audio.conf.")
    group.add_argument("--video-config", type=Path, help="Config curl de vídeo único.")
    parser.add_argument("--audio-config", type=Path, help="Config curl de áudio único; obrigatório com --video-config.")
    parser.add_argument("--output", type=Path, help="Único mp4 de saída; obrigatório com --video-config.")
    parser.add_argument("--output-dir", type=Path, help="Diretório de saída do lote; obrigatório com --config-dir.")
    parser.add_argument(
        "--parts-dir",
        type=Path,
        default=Path("work/instagram_parts"),
        help="Diretório temporário/reaproveitado das partes de vídeo/áudio.",
    )
    parser.add_argument(
        "--config-output-root",
        type=Path,
        default=Path.cwd(),
        help="Raiz usada para resolver as linhas output= (relativas) dos configs curl.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="Raiz do projeto que guarda work/queue.json; usada para registrar o cooldown ao levar 403/429.",
    )
    parser.add_argument(
        "--layout",
        choices=["auto", "flat", "student"],
        default="auto",
        help="Layout da saída do lote. auto preserva pastas por usuário para stems '<username>_<rank>_<code>'.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Ignora arquivos de config output e partes existentes; baixa das URLs assinadas.",
    )
    parser.add_argument(
        "--no-prefer-config-output",
        action="store_true",
        help="Não reaproveita arquivos já apontados por output= nos configs curl.",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copia os streams de vídeo/áudio em vez de normalizar para h264 yuv420p + aac.",
    )
    parser.add_argument(
        "--fail-on-duplicate-audio",
        action="store_true",
        help="Falha o lote se duas saídas tiverem o mesmo hash SHA-256 do AAC extraído.",
    )
    parser.add_argument("--summary-json", type=Path, help="Grava o resumo em JSON; reescrito a cada stem.")
    parser.add_argument(
        "--pace",
        default=DEFAULT_PACE,
        help="Pausa aleatória em segundos entre stems, como MIN-MAX (padrão 20-60); 0 desabilita. Nunca antes do primeiro stem.",
    )
    parser.add_argument(
        "--max-per-run",
        type=int,
        default=DEFAULT_MAX_PER_RUN,
        help="Processa no máximo N stems por execução (padrão 25); o resto é registrado como pulado.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Registra um stem com falha e continua em vez de interromper o lote. HTTP 403/429 sempre interrompe o lote.",
    )
    return parser


def write_summary(path: Path | None, summary: dict) -> None:
    """Grava o resumo incremental; um OSError vira WARNING e não interrompe o lote."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"WARNING: não foi possível gravar --summary-json ({redact(exc)})", file=sys.stderr)


def _build_summary(total: int, results: list[dict], stopped_by: str | None) -> dict:
    counts = {"done": 0, "failed": 0, "skipped": 0}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "schema_version": 1,
        "count": total,
        **counts,
        "results": list(results),
        "stopped_by": stopped_by,
    }


def record_queue_cooldown(project: Path | None, reason: str) -> str | None:
    """Abre um cooldown em `<project>/work/queue.json`, sob a trava do projeto; None quando não registrado."""
    if project is None:
        print(
            "WARNING: cooldown não registrado (nenhum --project informado; use --project para gravar o cooldown em work/queue.json)",
            file=sys.stderr,
        )
        return None
    try:
        if __package__:
            from .queue import record_cooldown
            from .runtime import project_lock
        else:
            from getbrolls.queue import record_cooldown
            from getbrolls.runtime import project_lock
    except ImportError as exc:
        print(f"WARNING: cooldown não registrado (dependências indisponíveis: {redact(exc)})", file=sys.stderr)
        return None
    try:
        with project_lock(project):
            result = record_cooldown(project, "instagram", reason)
    except (OSError, ValueError) as exc:
        print(f"WARNING: cooldown não registrado ({redact(exc)})", file=sys.stderr)
        return None
    if not result:
        print(f"WARNING: cooldown não registrado (nenhum work/queue.json em {project})", file=sys.stderr)
        return None
    return result["until"]


def _skip(results: list[dict], stem: str, reason: str) -> None:
    results.append({"stem": stem, "status": "skipped", "reason": reason})


def _pause_before(stem: str, low: int, high: int) -> None:
    """Pausa aleatória para o lote nunca bater na CDN no ritmo de uma máquina."""
    pause = random.uniform(low, high)
    print(f"-- pacing: waiting {pause:.1f}s before {stem}", file=sys.stderr)
    logs.event(_log, logging.DEBUG, "pace", wait_s=round(pause, 1))
    time.sleep(pause)


def _collect_one(stem: str, video_config: Path, audio_config: Path, output: Path, args) -> dict:
    return process_one(
        stem=stem,
        video_config=video_config,
        audio_config=audio_config,
        output=output,
        parts_dir=args.parts_dir,
        config_output_root=args.config_output_root,
        force_download=args.force_download,
        prefer_config_output=args.prefer_config_output,
        copy_streams=args.copy,
        compute_audio_hash=args.fail_on_duplicate_audio,
    )


def _record_failure(
    results: list[dict], stem: str, exc: BaseException, args
) -> tuple[str | None, BaseException | None]:
    """Registra o stem com falha e diz se (e por que) o resto do lote para."""
    if isinstance(exc, CollectError):
        reason = exc.message
        cooldown = exc.cooldown
    else:
        # Uma exceção inesperada (não CollectError) não pode derrubar o resumo: registra
        # com o nome do tipo e trata como qualquer outra falha.
        reason = redact(f"{type(exc).__name__}: {exc}")
        cooldown = False
    results.append({"stem": stem, "status": "failed", "reason": reason})
    if cooldown:
        until = record_queue_cooldown(getattr(args, "project", None), reason)
        logs.event(
            _log,
            logging.WARNING,
            "blocked",
            http_status=getattr(exc, "http_status", None),
            cooldown_recorded=bool(until),
        )
        if until:
            print(f"-- cooldown recorded in queue.json until {until}", file=sys.stderr)
        return "cooldown", None
    if not args.continue_on_error:
        return "aborted", exc
    return None, None


def run_batch(pairs, *, output_for, args) -> dict:
    """Processa stems em ordem com ritmo, teto por execução, resumo incremental e detecção de bloqueio."""
    low, high = parse_pace(args.pace)
    if args.max_per_run < 1:
        die("--max-per-run deve ser pelo menos 1")
    results: list[dict] = []
    stop_reason: str | None = None
    stopped_by: str | None = None
    fatal: BaseException | None = None
    processed = 0

    def snapshot() -> dict:
        return _build_summary(len(pairs), results, stopped_by)

    try:
        # Valida que o caminho do resumo é gravável antes do primeiro stem.
        write_summary(args.summary_json, snapshot())
        for stem, video_config, audio_config in pairs:
            if stop_reason:
                _skip(results, stem, stop_reason)
                continue
            if processed >= args.max_per_run:
                stopped_by = stopped_by or "max-per-run"
                _skip(results, stem, "max-per-run")
                write_summary(args.summary_json, snapshot())
                continue
            if processed and high > 0:
                _pause_before(stem, low, high)
            output = output_for(stem)
            print(f"== {stem} -> {output} ==", file=sys.stderr)
            processed += 1
            item_id = _safe_item_id(stem)
            logs.event(_log, logging.INFO, "pair_item_start", shortcode=item_id)
            try:
                result = _collect_one(stem, video_config, audio_config, output, args)
            except Exception as exc:
                stop_reason, fatal = _record_failure(results, stem, exc, args)
                if stop_reason == "cooldown":
                    stopped_by = "cooldown"
                logs.event(_log, logging.INFO, "pair_item_end", shortcode=item_id, status="failed")
            else:
                results.append({**result, "status": "done"})
                logs.event(_log, logging.INFO, "pair_item_end", shortcode=item_id, status="done")
            write_summary(args.summary_json, snapshot())
    finally:
        write_summary(args.summary_json, snapshot())
    summary = snapshot()
    logs.event(
        _log,
        logging.INFO,
        "pair_batch_summary",
        count=summary["count"],
        done=summary["done"],
        failed=summary["failed"],
        skipped=summary["skipped"],
        stopped_by=stopped_by,
    )
    if fatal is not None:
        raise fatal
    return summary


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.project is not None:
        # Standalone entry point (also the shared entry when driven programmatically as
        # `getbrolls.instagram_pairs.main()`): only configure logging when the project
        # root is known and this run is never read-only (it always writes media). With
        # no --project, the logger stays unconfigured — safe per logs.py's own
        # NullHandler guard (see the module docstring above), no file/stderr side effects.
        logs.configure(str(args.project), read_only=False)
    ensure_tool("curl")
    ensure_tool("ffmpeg")
    ensure_tool("ffprobe")

    args.prefer_config_output = not args.no_prefer_config_output

    if args.video_config:
        if not args.audio_config or not args.output:
            die("--audio-config e --output são obrigatórios com --video-config")
        stem = args.video_config.name.removesuffix("_video.conf")
        pairs = [(stem, args.video_config, args.audio_config)]
        output_for = lambda _stem: args.output  # noqa: E731
    else:
        if not args.output_dir:
            die("--output-dir é obrigatório com --config-dir")
        pairs = pair_configs(args.config_dir)
        output_for = lambda stem: infer_output_for_stem(stem, args.output_dir, args.layout)  # noqa: E731

    try:
        summary = run_batch(pairs, output_for=output_for, args=args)
        results = [item for item in summary["results"] if item["status"] == "done"]

        if args.fail_on_duplicate_audio and len(results) > 1:
            seen: dict[str, str] = {}
            for item in results:
                h = item["audio_hash_sha256"]
                if h in seen:
                    die(f"hash de áudio duplicado detectado: {item['output']} e {seen[h]} compartilham {h}")
                seen[h] = item["output"]
    except CollectError as exc:
        print(f"-- lote interrompido: {exc.message}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - summary já foi gravado; sem traceback cru
        print(f"-- lote interrompido: {type(exc).__name__}: {redact(str(exc))}", file=sys.stderr)
        return 1

    stopped_by = summary.get("stopped_by")
    print(
        f"-- lote concluído: {summary['done']} feito(s), {summary['failed']} falha(s), "
        f"{summary['skipped']} pulado(s); parado por: {stopped_by or 'nada (lote completo)'}",
        file=sys.stderr,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    """Run the batch and release the log file when it ends, however it ends."""
    try:
        return _main(argv)
    finally:
        logs.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
