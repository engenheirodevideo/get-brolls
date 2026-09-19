"""Bounded HTTPS JSON transport. Cache is private and never part of reports."""

import contextlib
import email.utils
import hashlib
import http.client
import ipaddress
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, logs
from .config import cache_root
from .runtime import record_warning, redact, stderr_tail

_logger = logs.get("http")


def _host_of(url):
    """Hostname only, for logging; never the path or query string."""
    try:
        return urllib.parse.urlsplit(url).hostname or "-"
    except ValueError:
        return "-"


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


# Characters RFC 3986 lets a URL path carry unescaped. "%" joins them so a path that
# is already percent-encoded is recognised as fine and never encoded a second time.
PATH_SAFE = "/~:@!$&'()*+,;=-._"
_PATH_OK = re.compile("[A-Za-z0-9" + re.escape(PATH_SAFE + "%") + "]*")


def encoded_url(url):
    """Percent-encode the path of `url`; scheme, host and query are left untouched.

    The NASA archive publishes ids with spaces in them, so its file URLs arrive with
    raw spaces in the path. `http.client` refuses those outright ("URL can't contain
    control characters"), which turned a valid item into a dead end at download time.
    """
    if not isinstance(url, str):
        return url
    parts = urllib.parse.urlsplit(url)
    if _PATH_OK.fullmatch(parts.path):
        return url
    return urllib.parse.urlunsplit(parts._replace(path=urllib.parse.quote(parts.path, safe=PATH_SAFE + "%")))


def _network_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        logs.event(_logger, logging.WARNING, "request_refused", host=p.hostname or "-", reason="invalid_target")
        raise ProviderError("HTTPS público obrigatório")
    return p


def _safe_network(url):
    p = _network_url(url)
    try:
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        logs.event(_logger, logging.WARNING, "request_refused", host=p.hostname, reason="dns_resolution_failed")
        raise ProviderError("Falha ao resolver provedor") from None
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        logs.event(_logger, logging.WARNING, "request_refused", host=p.hostname, reason="private_address")
        raise ProviderError("Destino de rede não permitido")
    return addresses


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """Resolve once per request; connect to those IPs with normal hostname TLS."""

    def https_open(self, request):
        addresses = _safe_network(request.full_url)

        def connect_pinned(address, timeout=30, source_address=None):  # noqa: ARG001 - matches `_create_connection`'s positional callback signature; `address` is intentionally ignored in favor of the pre-resolved `addresses`
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
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002, PLR0913 - overrides `HTTPRedirectHandler`'s fixed signature
        logs.event(_logger, logging.WARNING, "request_refused", host=_host_of(req.full_url), reason="redirect_refused")
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

# `get_json` tenta 3 vezes (`for attempt in range(3)`); o índice da última tentativa
# (0-based) é quando parar de tentar de novo e propagar o erro.
LAST_ATTEMPT_INDEX = 2


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


def get_json(url, params=None, headers=None, cache_ttl=0):  # noqa: C901, PLR0912, PLR0915 - existing size; request/cache/retry/error handling for one endpoint call
    _network_url(url)
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    host = _host_of(url)
    cache_path = cache_root() / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if cache_ttl and cache_path.is_file() and time.time() - cache_path.stat().st_mtime < cache_ttl:
        cache_started = time.monotonic()
        try:
            text = cache_path.read_text(encoding="utf-8")
            data = json.loads(text)
            logs.event(
                _logger,
                logging.DEBUG,
                "request",
                host=host,
                op="json",
                status=None,
                bytes=len(text),
                ms=round((time.monotonic() - cache_started) * 1000),
                cache="hit",
                attempt=0,
            )
            return data
        except (ValueError, OSError) as error:
            record_warning(
                "CACHE_UNAVAILABLE",
                f"Cache local ilegível ({type(error).__name__}); ignorado, buscando na fonte.",
            )
    cache_mode = "miss" if cache_ttl else "off"
    request_headers = {
        "User-Agent": f"Get-Brolls/{__version__} (video research; contact: local operator)",
        "Accept": "application/json",
    }
    request_headers.update(headers or {})
    opener = _opener()
    waited_for_quota = False
    data = None
    status_code = None
    raw_len = 0
    started = time.monotonic()
    for attempt in range(3):
        try:
            with opener.open(
                urllib.request.Request(url, headers=request_headers),  # noqa: S310 - opener guards via `_safe_network` in `https_open`
                timeout=30,
            ) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ProviderError("Resposta excede limite de 8 MB")
                status_code = getattr(response, "status", None)
                raw_len = len(raw)
                data = _scrub(json.loads(raw))
                break
        except urllib.error.HTTPError as error:
            code = error.code
            retry_after = (getattr(error, "headers", None) or {}).get("Retry-After")
            body = b""
            with contextlib.suppress(OSError, ValueError):
                body = error.read(300)
            error.close()
            detail = stderr_tail(body.decode("utf-8", errors="replace")) if body else ""
            suffix = f": {detail}" if detail else ""
            if code in (401, 403):
                logs.event(
                    _logger,
                    logging.WARNING,
                    "request",
                    host=host,
                    op="json",
                    status=code,
                    bytes=None,
                    ms=round((time.monotonic() - started) * 1000),
                    cache=cache_mode,
                    attempt=attempt + 1,
                )
                raise ProviderError(
                    f"Autenticação/permissão ou quota recusada pelo provedor (HTTP {code}){suffix}"
                ) from None
            if code == 429:  # noqa: PLR2004 - HTTP 429 Too Many Requests
                # Honour a short Retry-After once; never sleep past the CLI budget.
                wait = _retry_after_seconds(retry_after, cap=None) if retry_after else None
                if wait is not None and wait <= RETRY_AFTER_CAP_S and not waited_for_quota:
                    waited_for_quota = True
                    logs.event(
                        _logger, logging.WARNING, "retry", host=host, attempt=attempt + 1, wait_s=wait, reason=429
                    )
                    time.sleep(wait)
                    continue
                logs.event(
                    _logger,
                    logging.WARNING,
                    "request",
                    host=host,
                    op="json",
                    status=code,
                    bytes=None,
                    ms=round((time.monotonic() - started) * 1000),
                    cache=cache_mode,
                    attempt=attempt + 1,
                )
                if wait is not None:
                    raise ProviderError(
                        f"Quota atingida (HTTP 429); o provedor pede {wait} s de espera antes de repetir"
                    ) from None
                raise ProviderError("Quota atingida (HTTP 429); aguarde o limite do provedor") from None
            if code < 500 or attempt == LAST_ATTEMPT_INDEX:  # noqa: PLR2004 - 500, first of the provider-side 5xx statuses
                logs.event(
                    _logger,
                    logging.WARNING,
                    "request",
                    host=host,
                    op="json",
                    status=code,
                    bytes=None,
                    ms=round((time.monotonic() - started) * 1000),
                    cache=cache_mode,
                    attempt=attempt + 1,
                )
                raise ProviderError(f"Provedor retornou HTTP {code}{suffix}") from None
            logs.event(
                _logger,
                logging.WARNING,
                "retry",
                host=host,
                attempt=attempt + 1,
                wait_s=round(0.5 * (2**attempt), 1),
                reason=code,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt == LAST_ATTEMPT_INDEX:
                logs.event(
                    _logger,
                    logging.WARNING,
                    "request",
                    host=host,
                    op="json",
                    status=None,
                    bytes=None,
                    ms=round((time.monotonic() - started) * 1000),
                    cache=cache_mode,
                    attempt=attempt + 1,
                )
                raise ProviderError(
                    f"Provedor indisponível após três tentativas "
                    f"({type(error).__name__}: {getattr(error, 'reason', None) or error})"
                ) from None
            logs.event(
                _logger,
                logging.WARNING,
                "retry",
                host=host,
                attempt=attempt + 1,
                wait_s=round(0.5 * (2**attempt), 1),
                reason=type(error).__name__,
            )
        except ProviderError:
            raise
        except (ValueError, UnicodeError):
            logs.event(
                _logger,
                logging.WARNING,
                "request",
                host=host,
                op="json",
                status=None,
                bytes=None,
                ms=round((time.monotonic() - started) * 1000),
                cache=cache_mode,
                attempt=attempt + 1,
            )
            raise ProviderError("Resposta JSON inválida do provedor") from None
        time.sleep(0.5 * (2**attempt))
    if data is None:
        # Defensive: every branch above should already raise before the loop is exhausted;
        # this guards against a future edit silently turning that into a bare None return.
        raise ProviderError("Provedor não respondeu com dados válidos após as tentativas.")
    logs.event(
        _logger,
        logging.INFO,
        "request",
        host=host,
        op="json",
        status=status_code,
        bytes=raw_len,
        ms=round((time.monotonic() - started) * 1000),
        cache=cache_mode,
        attempt=attempt + 1,
    )
    # Cache write is not part of the network transaction: a full disk must not look like a
    # provider outage, and must not trigger a network retry.
    if cache_ttl and data is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temp = cache_path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temp.chmod(0o600)
            temp.replace(cache_path)
        except OSError:
            # Not mirrored via record_warning (that channel is for the read-side
            # CACHE_UNAVAILABLE case above); logged directly so a full-disk
            # condition on the write side is still visible in getbrolls.log.
            logs.event(_logger, logging.WARNING, "cache_write_failed", host=host, reason="write_error")
    return data


def download(url, target, max_bytes=512 * 1024 * 1024):  # noqa: C901, PLR0912, PLR0915 - existing size; streaming download with cleanup on every failure path
    """Stream only public HTTPS to an exclusive file; remove partials on failure."""
    if not public_url(url):
        logs.event(_logger, logging.WARNING, "request_refused", host=_host_of(url), reason="not_public_url")
        raise ProviderError("URL de mídia pública sem credenciais obrigatória")
    host = _host_of(url)
    # Defensive for every host, not only NASA: a path with a space (or any other
    # character outside RFC 3986) would otherwise reach http.client and be refused.
    url = encoded_url(url)
    target = Path(target)
    if max_bytes <= 0:
        raise ProviderError("Limite de bytes inválido")

    def too_big(size=None):
        """Diz o tamanho e o teto, em MB, e o que fazer — não manda ler RULES.md.

        O teto de download não vem das regras editoriais: é limite de transporte.
        Mandar a pessoa abrir `docs/RULES.md` a fazia procurar um ajuste que não
        existe lá, e a mensagem não dizia nem quanto o arquivo tinha.
        """
        cap = max_bytes / (1024 * 1024)
        actual = f"{size / (1024 * 1024):.1f} MB" if size else "tamanho acima do teto"
        logs.event(_logger, logging.WARNING, "download_aborted", host=host, reason="size_cap", limit_mb=round(cap))
        return ProviderError(
            f"Mídia excede limite de download: o arquivo tem {actual} e o teto desta "
            f"coleta é {cap:.0f} MB. Escolha um trecho menor com `preview --start/--end` "
            "antes do `fetch`, ou use uma variante de resolução mais baixa da mesma fonte."
        )

    created = False
    success = False
    started = time.monotonic()
    status_code = None
    received_bytes = 0
    try:
        request = urllib.request.Request(  # noqa: S310 - opener guards via `_safe_network` in `https_open`
            url, headers={"User-Agent": f"Get-Brolls/{__version__}"}
        )
        with _opener().open(request, timeout=30) as response:
            status_code = getattr(response, "status", None)
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise too_big(int(length))
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
                        raise too_big(received)
                    try:
                        output.write(chunk)
                    except OSError as error:
                        raise ProviderError(
                            f"Falha ao gravar arquivo (errno {error.errno}): {error.filename or target}"
                        ) from error
                received_bytes = received
                if not received:
                    raise ProviderError("Mídia vazia")
                if length and received != int(length):
                    raise ProviderError("Download incompleto")
        success = True
        logs.event(
            _logger,
            logging.INFO,
            "request",
            host=host,
            op="download",
            status=status_code,
            bytes=received_bytes,
            ms=round((time.monotonic() - started) * 1000),
            cache="off",
            attempt=1,
        )
        return target
    except ProviderError:
        raise
    except urllib.error.HTTPError as error:
        body = b""
        with contextlib.suppress(OSError, ValueError):
            body = error.read(300)
        error.close()
        detail = stderr_tail(body.decode("utf-8", errors="replace")) if body else ""
        suffix = f": {detail}" if detail else ""
        logs.event(
            _logger,
            logging.WARNING,
            "request",
            host=host,
            op="download",
            status=error.code,
            bytes=None,
            ms=round((time.monotonic() - started) * 1000),
            cache="off",
            attempt=1,
        )
        raise ProviderError(f"Provedor retornou HTTP {error.code} ao baixar mídia{suffix}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        reason = getattr(error, "reason", None) or error
        logs.event(
            _logger,
            logging.WARNING,
            "request",
            host=host,
            op="download",
            status=None,
            bytes=None,
            ms=round((time.monotonic() - started) * 1000),
            cache="off",
            attempt=1,
        )
        raise ProviderError(f"Falha de rede ao baixar mídia ({type(error).__name__}: {reason})") from None
    except (http.client.HTTPException, OSError, ValueError) as error:
        # Only real transport/IO/malformed-response failures land here (broken
        # connections, TLS errors, a non-numeric Content-Length...). Programming
        # bugs (KeyError/TypeError/AttributeError) are deliberately NOT caught: they
        # must propagate so runtime.audited() reports them as INTERNAL_ERROR instead
        # of being misclassified as a provider/network problem.
        logs.event(
            _logger,
            logging.WARNING,
            "request",
            host=host,
            op="download",
            status=None,
            bytes=None,
            ms=round((time.monotonic() - started) * 1000),
            cache="off",
            attempt=1,
        )
        raise ProviderError(
            f"Não foi possível obter o arquivo público ({type(error).__name__}: {redact(str(error))})"
        ) from error
    finally:
        # Runs for any exception, including BaseException (e.g. KeyboardInterrupt), which the
        # except clauses above deliberately do not catch.
        if created and not success:
            target.unlink(missing_ok=True)
