"""Instagram curl configs: URL validation, DNS pinning, the config files and where each output goes.

Loaded as `getbrolls.instagram_urls`, never as a script: only `instagram_pairs` is the entry point.
"""

# pylint: disable=missing-function-docstring,raise-missing-from

from __future__ import annotations

import functools
import ipaddress
import logging
import re
import socket
import subprocess
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from . import effects, logs
from ._paths import strict_exists
from .instagram_common import STEM_RE, die

# The same logger as the entry module (`getbrolls.instagram_pairs`): the log lines keep their source name.
_log = logs.get("instagram_pairs")

URL_RE = re.compile(r'^\s*url\s*=\s*"(.*)"\s*$')
OUTPUT_RE = re.compile(r'^\s*output\s*=\s*"(.*)"\s*$')
HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


def _die_url_refused(reason: str, message: str, code: int = 1) -> NoReturn:
    """Like `die()`, plus a `media_url_refused` log line carrying only a fixed `reason`
    code — never the message/path, which can carry a config filename or address."""
    logs.event(_log, logging.WARNING, "media_url_refused", reason=reason)
    die(message, code)


# curl 7.59 passou a aceitar uma lista de endereços numa entrada `--resolve`. O mais antigo
# não recusa a lista: só avisa no modo verbose, pula a entrada e resolve pelo DNS, sem o pin.
CURL_ADDRESS_LIST = (7, 59)


@functools.cache
def _curl_takes_address_list() -> bool:
    """O curl do PATH aceita `host:443:a,b,[v6]` em `--resolve`? Sem versão legível, conta como antigo."""
    try:
        answer = effects.run(
            ["curl", "--version"],
            check=True,
            timeout=10,
            capture_output=True,
            text=True,
            errors="replace",
            encoding="utf-8",
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.match(r"curl (\d+)\.(\d+)", answer if isinstance(answer, str) else "")
    return bool(match) and (int(match.group(1)), int(match.group(2))) >= CURL_ADDRESS_LIST


def _curl_resolution(host: str, source: Path) -> str | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            _die_url_refused("dns_resolution_failed", f"não foi possível resolver o hostname do config curl: {source}")
        addresses = list(dict.fromkeys(row[4][0] for row in rows))
        if not addresses:
            _die_url_refused("dns_no_address", f"hostname do config curl sem endereço: {source}")
        parsed = [ipaddress.ip_address(item) for item in addresses]
        if any(not item.is_global for item in parsed):
            _die_url_refused("private_address", f"hostname do config curl resolveu para endereço privado: {source}")
        # Every checked address goes to `--resolve` (curl >= 7.59 takes a list), IPv4 first and
        # otherwise in resolver order: pinning only one could pick an IPv6 on an IPv4-only network.
        parsed.sort(key=lambda item: item.version)
        if len(parsed) > 1 and not _curl_takes_address_list():
            # An older curl would skip the whole list and resolve by DNS, unpinned: one address only.
            parsed = parsed[:1]
        targets = ",".join(f"[{item}]" if item.version == 6 else str(item) for item in parsed)  # noqa: PLR2004 - IPv6
        return f"{host}:443:{targets}"
    if not address.is_global:
        _die_url_refused("private_address", f"a URL do config curl não pode apontar para endereço privado: {source}")
    return None


def validate_media_url(value: str, source: Path) -> tuple[str, str | None]:
    """Valida a URL de mídia do config curl e devolve (url, host de origem)."""
    # Um NUL (ou outro controle) no texto da URL chegaria ao config do curl, que o lê como texto C e busca só o prefixo.
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):  # noqa: PLR2004 - C0 e DEL
        _die_url_refused("invalid_url", f"URL inválida no config curl: {source}")
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
    """Lê o config curl e devolve a URL e a saída declaradas."""
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


# What a quoted string of a curl config reads as an escape: the backslash and the quote, plus the controls curl names.
_CURL_CONFIG_ESCAPES = str.maketrans({"\\": "\\\\", '"': '\\"', "\t": "\\t", "\n": "\\n", "\r": "\\r", "\v": "\\v"})


def curl_config_input(url: str) -> bytes:
    """The `curl --config -` text that carries `url`: one `url = "..."` line, escaped so curl reads it back as is."""
    return f'url = "{url.translate(_CURL_CONFIG_ESCAPES)}"\n'.encode()


def resolve_config_output(config_output: str | None, root: Path) -> Path | None:
    """Resolve a saída do config dentro da raiz permitida."""
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
    """Devolve o caminho de saída da mídia para o stem, segundo o layout."""
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
    """Pareia os configs de vídeo e áudio da pasta por stem."""
    video_configs = sorted(config_dir.glob("*_video.conf"))
    pairs: list[tuple[str, Path, Path]] = []
    for video_config in video_configs:
        stem = video_config.name[: -len("_video.conf")]
        audio_config = config_dir / f"{stem}_audio.conf"
        if not strict_exists(audio_config):
            die(f"config de áudio ausente para {stem}: {audio_config}")
        pairs.append((stem, video_config, audio_config))
    if not pairs:
        die(f"nenhum *_video.conf encontrado em {config_dir}")
    return pairs
