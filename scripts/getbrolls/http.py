"""Bounded HTTPS JSON transport. Cache is private and never part of reports."""

import email.utils
import hashlib
import http.client
import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .runtime import record_warning, redact, stderr_tail


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


def _network_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ProviderError("HTTPS público obrigatório")
    return p


def _safe_network(url):
    p = _network_url(url)
    try:
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ProviderError("Falha ao resolver provedor") from None
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ProviderError("Destino de rede não permitido")
    return addresses


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """Resolve once per request; connect to those IPs with normal hostname TLS."""

    def https_open(self, request):
        addresses = _safe_network(request.full_url)

        def connect_pinned(address, timeout=30, source_address=None):
            # Do not call create_connection(): it performs another DNS lookup.
            last_error = None
            for family, kind, protocol, _, sockaddr in addresses:
                sock = socket.socket(family, kind, protocol)
                try:
                    sock.settimeout(timeout)
                    if source_address:
                        sock.bind(source_address)
                    sock.connect(sockaddr)
                    return sock
                except OSError as error:
                    last_error = error
                    sock.close()
                except BaseException:
                    sock.close()
                    raise
            raise last_error or OSError("Nenhum endereço público disponível")

        def connection(host, **kwargs):
            conn = http.client.HTTPSConnection(host, **kwargs)
            # HTTPSConnection still performs certificate/hostname validation and
            # uses the original hostname for SNI; only TCP resolution is replaced.
            conn._create_connection = connect_pinned  # pyright: ignore[reportAttributeAccessIssue]
            return conn

        return self.do_open(connection, request, context=self._context)  # pyright: ignore[reportAttributeAccessIssue]


def _opener():
    # Environment proxies would bypass the checked destination. This transport
    # connects directly; redirects remain forbidden.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _PinnedHTTPSHandler(), _NoRedirect())


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


RETRY_AFTER_CAP_S = 60


def _retry_after_seconds(value, cap: int | None = RETRY_AFTER_CAP_S):
    """Retry-After as whole seconds (delta or HTTP-date), capped; None when absent/unparsable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    limit = (lambda n: n) if cap is None else (lambda n: min(n, cap))
    if text.isdigit():
        return limit(int(text))
    try:
        moment = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    delta = (moment - datetime.now(UTC)).total_seconds()
    return limit(max(0, int(delta + 0.999)))


def get_json(url, params=None, headers=None, cache_ttl=0):
    _network_url(url)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    cache_root = Path(os.environ.get("GETBROLLS_CACHE_DIR", str(Path.home() / ".cache" / "getbrolls")))
    cache_path = cache_root / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if cache_ttl and cache_path.is_file() and time.time() - cache_path.stat().st_mtime < cache_ttl:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            record_warning(
                "CACHE_UNAVAILABLE",
                f"Cache local ilegível ({type(error).__name__}); ignorado, buscando na fonte.",
            )
    request_headers = {
        "User-Agent": f"Get-Brolls/{__version__} (video research; contact: local operator)",
        "Accept": "application/json",
    }
    request_headers.update(headers or {})
    opener = _opener()
    waited_for_quota = False
    data = None
    for attempt in range(3):
        try:
            with opener.open(urllib.request.Request(url, headers=request_headers), timeout=30) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ProviderError("Resposta excede limite de 8 MB")
                data = _scrub(json.loads(raw))
                break
        except urllib.error.HTTPError as error:
            code = error.code
            retry_after = (getattr(error, "headers", None) or {}).get("Retry-After")
            body = b""
            try:
                body = error.read(300)
            except (OSError, ValueError):
                pass
            error.close()
            detail = stderr_tail(body.decode("utf-8", errors="replace")) if body else ""
            suffix = f": {detail}" if detail else ""
            if code in (401, 403):
                raise ProviderError(
                    f"Autenticação/permissão ou quota recusada pelo provedor (HTTP {code}){suffix}"
                ) from None
            if code == 429:
                # Honour a short Retry-After once; never sleep past the CLI budget.
                wait = _retry_after_seconds(retry_after, cap=None) if retry_after else None
                if wait is not None and wait <= RETRY_AFTER_CAP_S and not waited_for_quota:
                    waited_for_quota = True
                    time.sleep(wait)
                    continue
                if wait is not None:
                    raise ProviderError(
                        f"Quota atingida (HTTP 429); o provedor pede {wait} s de espera antes de repetir"
                    ) from None
                raise ProviderError("Quota atingida (HTTP 429); aguarde o limite do provedor") from None
            if code < 500 or attempt == 2:
                raise ProviderError(f"Provedor retornou HTTP {code}{suffix}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt == 2:
                raise ProviderError(
                    f"Provedor indisponível após três tentativas "
                    f"({type(error).__name__}: {getattr(error, 'reason', None) or error})"
                ) from None
        except ProviderError:
            raise
        except (ValueError, UnicodeError):
            raise ProviderError("Resposta JSON inválida do provedor") from None
        time.sleep(0.5 * (2**attempt))
    if data is None:
        # Defensive: every branch above should already raise before the loop is exhausted;
        # this guards against a future edit silently turning that into a bare None return.
        raise ProviderError("Provedor não respondeu com dados válidos após as tentativas.")
    # Cache write is not part of the network transaction: a full disk must not look like a
    # provider outage, and must not trigger a network retry.
    if cache_ttl and data is not None:
        try:
            cache_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp = cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temp.chmod(0o600)
            temp.replace(cache_path)
        except OSError:
            pass
    return data


def download(url, target, max_bytes=512 * 1024 * 1024):
    """Stream only public HTTPS to an exclusive file; remove partials on failure."""
    if not public_url(url):
        raise ProviderError("URL de mídia pública sem credenciais obrigatória")
    target = Path(target)
    if max_bytes <= 0:
        raise ProviderError("Limite de bytes inválido")
    created = False
    success = False
    try:
        request = urllib.request.Request(url, headers={"User-Agent": f"Get-Brolls/{__version__}"})
        with _opener().open(request, timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ProviderError("Mídia excede limite de download")
            try:
                output = target.open("xb")
            except OSError as error:
                raise ProviderError(
                    f"Falha ao gravar arquivo (errno {error.errno}): {error.filename or target}"
                ) from error
            with output:
                created = True
                received = 0
                while True:
                    chunk = response.read(min(1024 * 1024, max_bytes - received + 1))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise ProviderError("Mídia excede limite de download")
                    try:
                        output.write(chunk)
                    except OSError as error:
                        raise ProviderError(
                            f"Falha ao gravar arquivo (errno {error.errno}): {error.filename or target}"
                        ) from error
                if not received:
                    raise ProviderError("Mídia vazia")
                if length and received != int(length):
                    raise ProviderError("Download incompleto")
        success = True
        return target
    except ProviderError:
        raise
    except urllib.error.HTTPError as error:
        body = b""
        try:
            body = error.read(300)
        except (OSError, ValueError):
            pass
        error.close()
        detail = stderr_tail(body.decode("utf-8", errors="replace")) if body else ""
        suffix = f": {detail}" if detail else ""
        raise ProviderError(f"Provedor retornou HTTP {error.code} ao baixar mídia{suffix}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        reason = getattr(error, "reason", None) or error
        raise ProviderError(f"Falha de rede ao baixar mídia ({type(error).__name__}: {reason})") from None
    except Exception as error:
        raise ProviderError(
            f"Não foi possível obter o arquivo público ({type(error).__name__}: {redact(str(error))})"
        ) from error
    finally:
        # Runs for any exception, including BaseException (e.g. KeyboardInterrupt), which the
        # except clauses above deliberately do not catch.
        if created and not success:
            target.unlink(missing_ok=True)
