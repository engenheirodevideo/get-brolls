"""Leitura de uma página, script ou playlist pública como texto, e a espiada no começo de um arquivo.

O transporte é o de `http`: IP de cada pedido conferido e fixado (`_PinnedHTTPSHandler`),
sem proxy do ambiente e sem cookie. A diferença é o redirecionamento: aqui ele é seguido,
até `TEXT_MAX_REDIRECTS` vezes, e cada destino passa de novo pelas mesmas regras antes de
conectar. A leitura tem teto de bytes e prazo total, não só o timeout do socket: no fim do
prazo, um timer derruba a conexão (`_Deadline`), em qualquer fase da resposta.
"""

import contextlib
import http.client
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NamedTuple

from . import __version__, effects, logs
from .errors import ProviderError
from .http import _host_of, _network_url, _PinnedHTTPSHandler, public_url

_logger = logs.get("http")


class _KeepRedirect(urllib.request.HTTPRedirectHandler):
    """Não segue redirecionamento: o 3xx volta como `HTTPError`, e `get_text` revalida o destino."""

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments  # assinatura fixa do urllib
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002, PLR0913, PLR0917
        return None


REDIRECT_CODES = (301, 302, 303, 307, 308)
TEXT_MAX_REDIRECTS = 3
TEXT_TIMEOUT_S = 15
# Prazo total de uma leitura de texto: o timeout do socket vale por operação, não pela resposta inteira.
TEXT_DEADLINE_S = 30
TEXT_CHUNK = 64 * 1024


def _text_target(url, allowed_hosts):
    """Confere o destino de `get_text` antes de conectar: HTTPS público, sem credencial, no allowlist."""
    host = (_host_of(url) or "-").lower()
    if not public_url(url):
        logs.event(_logger, logging.WARNING, "request_refused", host=host, reason="not_public_url")
        raise ProviderError("A página precisa ser HTTPS pública, sem credenciais e fora da rede local")
    if allowed_hosts is not None and host not in allowed_hosts:
        logs.event(_logger, logging.WARNING, "request_refused", host=host, reason="host_not_allowed")
        raise ProviderError("O endereço levou a um host fora dos permitidos para esta leitura")
    _network_url(url)


def _late():
    return ProviderError(
        f"A resposta passou do prazo de {TEXT_DEADLINE_S} s para chegar inteira; tente de novo mais tarde"
    )


class _Deadline:
    """Prazo de um pedido (`at`, em `time.monotonic`): quando vence, um timer derruba o socket com `shutdown`.

    O timeout do socket vale por operação, e o `read`/`readline` do `http.client` repetem o `recv`
    até juntar o bloco ou a linha: um servidor que manda um byte a cada 14 s seguraria cabeçalhos
    ou corpo sem fim. O `shutdown` acorda o `recv` parado em qualquer fase; o handshake TLS já tem
    prazo próprio (o timeout do socket vale para ele inteiro).
    """

    def __init__(self, at):
        self.at = at
        self._lock = threading.Lock()
        self._sockets = []
        self._fired = False
        self._timer = threading.Timer(self.remaining(), self._fire)
        self._timer.daemon = True

    def expired(self):
        """O prazo venceu (o timer já disparou ou o relógio passou de `at`)."""
        return self._fired or time.monotonic() >= self.at

    def remaining(self):
        """Segundos até `at`; zero quando já venceu."""
        return max(0.0, self.at - time.monotonic())

    def watch(self, sock):
        """Passa a vigiar `sock` (o `on_connect` do `_PinnedHTTPSHandler`); vencido o prazo, derruba já."""
        with self._lock:
            self._sockets.append(sock)
            fired = self._fired
        if fired:
            _shutdown(sock)

    def _fire(self):
        with self._lock:
            self._fired = True
            sockets = list(self._sockets)
        for sock in sockets:
            _shutdown(sock)

    def __enter__(self):
        self._timer.start()
        return self

    def __exit__(self, *_exc):
        self._timer.cancel()
        with self._lock:
            self._sockets.clear()


def _shutdown(sock):
    """Derruba a conexão: o `recv` parado na outra thread volta com fim de arquivo ou erro.

    Socket já fechado levanta `OSError` (nada a derrubar). No `SSLSocket`, a leitura seguinte à
    derrubada levanta `ValueError`, que `_read_body` e `_text_once` também tratam como prazo vencido.
    """
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


def _read_body(response, max_bytes, deadline, head_only=False) -> bytes:
    """Corpo da resposta em blocos, com teto de `max_bytes` e o prazo total de `deadline` (`_Deadline`).

    Um bloco cortado pelo prazo (o timer derrubou o socket) não passa por resposta inteira: termina
    na recusa de prazo. Com `head_only` (o `peek`), lê só até `max_bytes` e para, sem recusar.
    """
    too_big = ProviderError(f"A página passa do limite de leitura de {max_bytes // 1024} KB")
    length = response.headers.get("Content-Length")
    if not head_only and length and length.isdigit() and int(length) > max_bytes:
        raise too_big
    wanted = max_bytes if head_only else max_bytes + 1
    chunks, size = [], 0
    while size < wanted:
        try:
            chunk = response.read(min(TEXT_CHUNK, wanted - size))
        except (OSError, ValueError, http.client.HTTPException):
            if deadline.expired():
                raise _late() from None
            raise
        if deadline.expired():
            raise _late()
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    raw = b"".join(chunks)
    if not head_only and len(raw) > max_bytes:
        raise too_big
    return raw


def _decode(raw, charset):
    """O corpo como texto no charset da resposta; charset desconhecido ou hostil (`idna`, que recusa o
    `errors="replace"` com `UnicodeError`) cai para UTF-8 com substituição."""
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except (LookupError, UnicodeError):
        return raw.decode("utf-8", errors="replace")


class _Answer(NamedTuple):
    """Uma resposta de `_text_once`: corpo, tipo e charset, ou o destino de um redirecionamento."""

    body: bytes
    content_type: str
    charset: str | None
    location: str | None


def _request(url, max_bytes, head_only):
    """O pedido GET; com `head_only`, só os primeiros `max_bytes` (`Range`)."""
    headers = {
        "User-Agent": f"Get-Brolls/{__version__} (video research; contact: local operator)",
        "Accept": "*/*" if head_only else "text/html,application/javascript,text/javascript,*/*;q=0.5",
    }
    if head_only:
        headers["Range"] = f"bytes=0-{max_bytes - 1}"
    # O opener confere o destino em `https_open` (`_safe_network`), antes de conectar.
    return urllib.request.Request(url, headers=headers)  # noqa: S310


def _text_once(url, max_bytes, deadline_at, head_only=False) -> _Answer:
    """Um GET: o corpo (até `max_bytes`) ou o destino quando a resposta é um redirecionamento.

    `deadline_at` é o fim do prazo da leitura inteira, redirecionamentos incluídos. Com `head_only`,
    pede só os primeiros `max_bytes` (`Range`) e lê só eles.
    """
    started = time.monotonic()
    deadline = _Deadline(deadline_at)
    # Sem `HTTPCookieProcessor`: nenhum cookie é guardado nem enviado; sem proxy do ambiente.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _PinnedHTTPSHandler(on_connect=deadline.watch), _KeepRedirect()
    )
    line = {"host": _host_of(url), "op": "peek" if head_only else "text", "status": None, "bytes": None}
    try:
        with (
            deadline,
            effects.op("net.get", url) as fetching,
            opener.open(
                _request(url, max_bytes, head_only), timeout=max(0.001, min(TEXT_TIMEOUT_S, deadline.remaining()))
            ) as response,
        ):
            line["status"] = getattr(response, "status", None)
            raw = _read_body(response, max_bytes, deadline, head_only)
            line["bytes"] = len(raw)
            fetching.set(bytes=len(raw))
            content_type = (response.headers.get_content_type() or "").lower()
            return _Answer(raw, content_type, response.headers.get_content_charset(), None)
    except urllib.error.HTTPError as error:
        line["status"] = error.code
        location = (getattr(error, "headers", None) or {}).get("Location")
        error.close()
        if deadline.expired():
            # O timer derrubou a conexão no meio dos cabeçalhos: o que chegou não é a resposta inteira.
            raise _late() from None
        if error.code in REDIRECT_CODES and location:
            return _Answer(b"", "", None, urllib.parse.urljoin(url, location.strip()))
        raise ProviderError(f"A página respondeu HTTP {error.code}") from None
    except ProviderError:
        raise
    except ValueError:
        if deadline.expired():
            raise _late() from None
        raise
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        if deadline.expired():
            raise _late() from None
        raise ProviderError(f"Não foi possível abrir a página ({type(error).__name__})") from None
    finally:
        line["ms"] = round((time.monotonic() - started) * 1000)
        logs.event(_logger, logging.INFO, "request", **line, cache="off", attempt=1)


def _follow(url, max_bytes, allowed_hosts, head_only=False) -> tuple[str, _Answer]:
    """Segue até `TEXT_MAX_REDIRECTS` redirecionamentos, conferindo cada destino antes de conectar."""
    if max_bytes <= 0:
        raise ProviderError("Limite de bytes inválido")
    allowed = None if allowed_hosts is None else tuple(h.lower() for h in allowed_hosts)
    deadline_at = time.monotonic() + TEXT_DEADLINE_S
    current = url
    for _hop in range(TEXT_MAX_REDIRECTS + 1):
        _text_target(current, allowed)
        answer = _text_once(current, max_bytes, deadline_at, head_only)
        if answer.location is None:
            return current, answer
        logs.event(_logger, logging.INFO, "redirect", host=_host_of(current), to_host=_host_of(answer.location))
        current = answer.location
    logs.event(_logger, logging.WARNING, "request_refused", host=_host_of(url), reason="too_many_redirects")
    raise ProviderError(f"A página passou de {TEXT_MAX_REDIRECTS} redirecionamentos")


def get_text(url, max_bytes, allowed_hosts=None, *, encoding=None) -> tuple[str, str]:
    """Lê uma página ou script público como texto; devolve `(url_final, texto)`.

    Com `encoding`, o corpo é lido nele, e o charset que a resposta declara não conta (a playlist
    é lida pelos bytes, como o downloader nativo do yt-dlp a lê).

    Teto de `max_bytes`, prazo total de `TEXT_DEADLINE_S` (cabeçalhos e corpo, redirecionamentos
    incluídos), sem cookie e sem proxy, com o IP de cada pedido conferido (`_PinnedHTTPSHandler`).
    Redirecionamento é seguido até
    `TEXT_MAX_REDIRECTS` vezes, e cada destino passa de novo pelas mesmas regras; com
    `allowed_hosts`, nenhum pedido sai para um host fora dele, nem por redirecionamento.
    """
    final, answer = _follow(url, max_bytes, allowed_hosts)
    return final, _decode(answer.body, encoding or answer.charset)


def peek(url, size, allowed_hosts=None) -> tuple[str, str, bytes]:
    """Os primeiros `size` bytes de um arquivo público: `(url_final, content_type, bytes)`.

    Mesmas travas de `get_text` (IP conferido, redirecionamento revalidado, sem cookie); pede
    só o começo (`Range: bytes=0-…`) e lê no máximo `size` bytes, mesmo que o servidor mande tudo.
    """
    final, answer = _follow(url, size, allowed_hosts, head_only=True)
    return final, answer.content_type, answer.body
