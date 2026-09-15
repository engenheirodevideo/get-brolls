"""Bounded HTTPS JSON transport. Cache is private and never part of reports."""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import time
import urllib.error
import urllib.parse
import urllib.request


class ProviderError(ValueError):
    pass


SECRET_NAMES = {
    "key",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "authorization",
    "signature",
    "sig",
}


def public_url(url):
    """Accept credential-free HTTPS references; drop signed URLs rather than break them."""
    if not isinstance(url, str):
        return None
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        return None
    try:
        if not ipaddress.ip_address(p.hostname).is_global:
            return None
    except ValueError:
        if p.hostname.lower() == "localhost" or p.hostname.lower().endswith(".local"):
            return None
    for key, _ in urllib.parse.parse_qsl(p.query):
        if key.lower() in SECRET_NAMES or key.lower().startswith(("x-amz-", "x-goog-")):
            return None
    return url


def _safe_network(url):
    p = urllib.parse.urlsplit(url)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or p.port not in (None, 443)
    ):
        raise ProviderError("HTTPS público obrigatório")
    try:
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ProviderError("Falha ao resolver provedor") from None
    if not addresses or any(
        not ipaddress.ip_address(row[4][0]).is_global for row in addresses
    ):
        raise ProviderError("Destino de rede não permitido")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("Redirecionamento de API não permitido")


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k.lower() not in SECRET_NAMES}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme == "http" and parsed.netloc == "images-assets.nasa.gov":
            value = urllib.parse.urlunsplit(parsed._replace(scheme="https"))
        return public_url(value)
    return value


def get_json(url, params=None, headers=None, cache_ttl=0):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    cache_root = Path(
        os.environ.get("GETBROLLS_CACHE_DIR", str(Path.home() / ".cache" / "getbrolls"))
    )
    cache_path = cache_root / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if (
        cache_ttl
        and cache_path.is_file()
        and time.time() - cache_path.stat().st_mtime < cache_ttl
    ):
        try:
            return json.loads(cache_path.read_text())
        except (ValueError, OSError):
            pass
    _safe_network(url)
    request_headers = {
        "User-Agent": "GetBrolls/2.0 (video research; contact: local operator)",
        "Accept": "application/json",
    }
    request_headers.update(headers or {})
    opener = urllib.request.build_opener(_NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(
                urllib.request.Request(url, headers=request_headers), timeout=30
            ) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ProviderError("Resposta excede limite de 8 MB")
                data = _scrub(json.loads(raw))
                if cache_ttl:
                    cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                    temp = cache_path.with_suffix(".tmp")
                    temp.write_text(json.dumps(data, ensure_ascii=False))
                    temp.chmod(0o600)
                    temp.replace(cache_path)
                return data
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code in (401, 403):
                raise ProviderError(
                    "Autenticação/permissão ou quota recusada pelo provedor (HTTP %s)"
                    % code
                ) from None
            if code == 429:
                # Do not sleep past a CLI budget or retry earlier than Retry-After.
                raise ProviderError(
                    "Quota atingida (HTTP 429); aguarde o limite do provedor"
                ) from None
            if code < 500 or attempt == 2:
                raise ProviderError("Provedor retornou HTTP %s" % code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise ProviderError(
                    "Provedor indisponível após três tentativas"
                ) from None
        except (ValueError, UnicodeError):
            raise ProviderError("Resposta JSON inválida do provedor") from None
        time.sleep(0.5 * (2**attempt))


def download(url, target, max_bytes=512 * 1024 * 1024):
    """Stream only public HTTPS to an exclusive file; remove partials on failure."""
    if not public_url(url):
        raise ProviderError("URL de mídia pública sem credenciais obrigatória")
    _safe_network(url)
    target = Path(target)
    if max_bytes <= 0:
        raise ProviderError("Limite de bytes inválido")
    created = False
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "GetBrolls/2.0"})
        with urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=30
        ) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ProviderError("Mídia excede limite de download")
            with target.open("xb") as output:
                created = True
                received = 0
                while True:
                    chunk = response.read(min(1024 * 1024, max_bytes - received + 1))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise ProviderError("Mídia excede limite de download")
                    output.write(chunk)
                if not received:
                    raise ProviderError("Mídia vazia")
                if length and received != int(length):
                    raise ProviderError("Download incompleto")
        return target
    except BaseException as error:
        if created:
            target.unlink(missing_ok=True)
        if not isinstance(error, Exception):
            raise
        if isinstance(error, ProviderError):
            raise
        raise ProviderError("Não foi possível obter o arquivo público") from None
