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
from .runtime import record_warning, redact, secret_name, stderr_tail

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


def _normalize_key(name):
    """`name` em minúsculas, sem "-"/"_"/espaço/qualquer separador — para comparar
    `access_token`, `accessToken` e `X-Api-Key` como o MESMO nome. A primeira
    versão do M1 só casava snake_case exato e deixava passar `accessToken`,
    `x-api-key`, `aws_secret_access_key`, `x-amz-security-token`, `jwt` e
    `credentials` (achado do review): formato real de API não é sempre
    snake_case."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# M1 (fix round 2, achado do review): nome de chave de JSON descartado só no
# scrub estrito (`_scrub(strict=True)`, só o caminho de plugin), além de
# `SECRET_NAMES` acima (que já vale para todo mundo). Cada item aqui é usado com
# `str.endswith` sobre `_normalize_key(k)`, o que cobre IGUALDADE exata (o nome
# normalizado bate o marcador inteiro, ex.: `jwt`, `secret`, `credentials`) E
# nome composto de verdade (`aws_secret_access_key` normalizado termina em
# `secretaccesskey`; `x-api-key` termina em `apikey`; `x-amz-security-token`
# termina em `securitytoken`). Uma lista explícita, não `runtime.SECRET_NAME_RE`
# (o regex amplo usado para nome de QUERY de URL, em `_secret_query_name`): esse
# regex casa qualquer nome que só TERMINE em "key"/"token"/"policy" sozinho, o
# que derrubava chave de paginação ou id de plugin sem ser credencial nenhuma
# (`next_page_token`, `page_token`, `continuation_token`, `sort_key`,
# `cursor_key`, `cursor`...). Por isso nenhum marcador aqui é um sufixo genérico
# como "key"/"token" isolado — só combinações fortes de credencial.
STRICT_JSON_SECRET_KEY_MARKERS = (
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "authtoken",
    "sessiontoken",
    "securitytoken",
    "bearertoken",
    "apikey",
    "apitoken",
    "apisecret",
    "clientsecret",
    "secretkey",
    "secretaccesskey",
    "privatekey",
    "signingkey",
    "encryptionkey",
    "password",
    "passwd",
    "credentials",
    "jwt",
    "secret",
)


# Nomes curtos demais para valer como sufixo (`pwd` acabaria em qualquer coisa que
# termine assim; `hmac` em `sha256hmac`, legítimo ou não): só o nome normalizado
# INTEIRO conta (re-review, observação 1).
STRICT_JSON_SECRET_EXACT = frozenset({"pwd", "hmac"})


def _strict_secret_key(name):
    if not isinstance(name, str):
        return False
    normalized = _normalize_key(name)
    return normalized in STRICT_JSON_SECRET_EXACT or normalized.endswith(STRICT_JSON_SECRET_KEY_MARKERS)


def public_url(url, allow_signed=False):
    """Accept credential-free HTTPS references; drop signed URLs rather than break them.

    `allow_signed=True` skips only the signed-query filter (a plugin route downloading
    a presigned file); scheme, userinfo and private/loopback hosts are still refused.
    """
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
    signed = not allow_signed and any(_secret_query_name(key) for key, _ in urllib.parse.parse_qsl(p.query))
    return None if signed else url


def _secret_query_name(key):
    """Nome de query que carrega credencial: os nomes exatos de sempre, os prefixos
    de assinatura S3/GCS e qualquer nome que termine numa palavra secreta
    (`password`, `hmac`, `jwt`, `client_secret`, `auth_token`, Akamai `__token__`/
    `hdnts`/`hdnea`, CloudFront `Policy`/`Key-Pair-Id` — ver `runtime.SECRET_NAME_RE`).
    URL pública de fonte embutida (Pexels, Pixabay, Commons, NASA, YouTube) não
    usa nenhum desses nomes, então continua passando igual."""
    lowered = key.lower()
    return lowered in SECRET_NAMES or lowered.startswith(("x-amz-", "x-goog-")) or secret_name(key)


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
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002, PLR0913, PLR0917 - overrides `HTTPRedirectHandler`'s fixed signature
        logs.event(_logger, logging.WARNING, "request_refused", host=_host_of(req.full_url), reason="redirect_refused")
        raise ProviderError("Redirecionamento de API não permitido")


_EMBEDDED_URL = re.compile(r"(?i)https?://[^\s\"'<>]+")


def _scrub(value, keep_signed=False, strict=False):
    """Limpa a resposta JSON: chave de segredo some, URL assinada/insegura vira `None`.

    `strict=True` (só o caminho de plugin, `PluginApi.get_json`) vai além, sem mudar
    nada para os built-ins: casa o esquema sem diferenciar maiúsculas (`HTTPS://`),
    troca por "[URL omitida]" uma URL assinada que venha no meio de um texto e
    descarta toda chave de JSON cujo nome normalizado (`_normalize_key`: minúsculo,
    sem "-"/"_"/espaço) bate ou termina num marcador forte de credencial
    (`STRICT_JSON_SECRET_KEY_MARKERS`: `refresh_token`/`refreshToken`,
    `client_secret`, `x-api-key`, `aws_secret_access_key`, `password`...), não só
    os nomes exatos de `SECRET_NAMES`. Chave de paginação/id (`next_page_token`,
    `sort_key`, `cursor_key`, `cursor`...) não é credencial e sobrevive (M1)."""
    if isinstance(value, dict):
        return {
            k: _scrub(v, keep_signed, strict)
            for k, v in value.items()
            if k.lower() not in SECRET_NAMES and not (strict and _strict_secret_key(k))
        }
    if isinstance(value, list):
        return [_scrub(v, keep_signed, strict) for v in value]
    if not isinstance(value, str):
        return value
    prefix = value[:8].lower() if strict else value[:8]
    if prefix.startswith(("http://", "https://")):
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme == "http" and parsed.netloc == "images-assets.nasa.gov":
            value = urllib.parse.urlunsplit(parsed._replace(scheme="https"))
        return public_url(value, allow_signed=keep_signed)
    if strict and not keep_signed:
        return _EMBEDDED_URL.sub(lambda m: m.group(0) if public_url(m.group(0)) else "[URL omitida]", value)
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


def get_json(url, params=None, headers=None, cache_ttl=0, keep_signed=False, quiet_errors=False, strict_scrub=False):  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917 - existing size; request/cache/retry/error handling for one endpoint call; quiet_errors/strict_scrub are caller-facing knobs set only by the SDK, not incidental complexity
    """`quiet_errors=True` drops the HTTPError response body from the message (the
    SDK sets this on every plugin call; built-in providers never set it, so their
    error messages are unchanged even when they pass `headers`, e.g. Pexels'
    Authorization). `keep_signed=True` implies it: a signed URL kept in the
    response is exactly the kind of call whose error body might reflect it back.
    `strict_scrub=True` (also SDK-only) applies `_scrub(strict=True)`.
    """
    if keep_signed and cache_ttl:
        raise ProviderError("keep_signed exige cache desligado (cache_ttl=0): URL assinada não vai para o disco.")
    quiet_errors = quiet_errors or keep_signed
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
                data = _scrub(json.loads(raw), keep_signed, strict_scrub)
                break
        except urllib.error.HTTPError as error:
            code = error.code
            retry_after = (getattr(error, "headers", None) or {}).get("Retry-After")
            body = b""
            with contextlib.suppress(OSError, ValueError):
                body = error.read(300)
            error.close()
            detail = ""
            if body and not quiet_errors:
                # `quiet_errors` (set only by the SDK, never by a built-in provider):
                # an authenticated/presigned plugin request's error body can otherwise
                # echo back part of the credential (e.g. a fake key in a 403 body), so
                # it never reaches the message in that case. Built-in providers keep
                # their exact previous message, even the ones that pass `headers`
                # (e.g. Pexels' Authorization) — "sem plugins, saída idêntica".
                detail = stderr_tail(body.decode("utf-8", errors="replace"))
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


DOWNLOAD_MAX_BYTES = 512 * 1024 * 1024


def download(url, target, max_bytes=DOWNLOAD_MAX_BYTES, headers=None, allow_signed=False):  # noqa: C901, PLR0912, PLR0915 - existing size; streaming download with cleanup on every failure path
    """Stream only public HTTPS to an exclusive file; remove partials on failure.

    `headers` (e.g. a plugin's Authorization) go only into the request: never into a
    log line or an error message. `allow_signed` is forwarded to `public_url`.
    """
    if not public_url(url, allow_signed=allow_signed):
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
    header_error = None
    try:
        request_headers = {"User-Agent": f"Get-Brolls/{__version__}"}
        request_headers.update(headers or {})
        request = urllib.request.Request(  # noqa: S310 - opener guards via `_safe_network` in `https_open`
            url, headers=request_headers
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
    except ProviderError:
        raise
    except urllib.error.HTTPError as error:
        body = b""
        with contextlib.suppress(OSError, ValueError):
            body = error.read(300)
        error.close()
        detail = ""
        if body and not headers and not allow_signed:
            # Only when the request carried no plugin header and was not a signed URL:
            # an authenticated/presigned request's error body can otherwise echo back
            # part of the credential (e.g. a fake AWSAccessKeyId in a bucket's 403), so
            # it never reaches the message in that case.
            detail = stderr_tail(body.decode("utf-8", errors="replace"))
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
    except ValueError as error:
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
        if headers:
            # A header name/value that reached `http.client` was rejected (e.g. CR/LF
            # injection past a caller who skipped the SDK's own validator); `str(error)`
            # from the stdlib echoes the raw value, so only the exception type name
            # crosses this boundary. Recorded here and raised only after the whole
            # try/except/finally below (`header_error`), not with `raise ... from
            # None` right here: that alone still leaves `__context__` set to `error`,
            # so `repr(exc.__context__)` would still carry the header value.
            header_error = type(error).__name__
        else:
            raise ProviderError(
                f"Não foi possível obter o arquivo público ({type(error).__name__}: {redact(str(error))})"
            ) from error
    except (http.client.HTTPException, OSError) as error:
        # Only real transport/IO failures land here (broken connections, TLS errors...).
        # Programming bugs (KeyError/TypeError/AttributeError) are deliberately NOT
        # caught: they must propagate so runtime.audited() reports them as
        # INTERNAL_ERROR instead of being misclassified as a provider/network problem.
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
    if header_error is not None:
        # Raised only here, after the try/except/finally above has fully exited: no
        # exception is "in flight" at this point, so `__context__` is None too, not
        # just `__cause__` — see the comment on the `except ValueError` branch.
        raise ProviderError(f"Falha ao enviar cabeçalhos ao provedor ({header_error})") from None
    return target
