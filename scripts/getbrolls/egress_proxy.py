"""Proxy HTTP CONNECT local que confere cada conexão do yt-dlp na rota `open_web`
(`direct_stream` e `embedded_player`).

O `stream_check` lê a playlist antes, mas o yt-dlp busca de novo por conta própria: segue
redirecionamento, pede segmento, chave e variante, e resolve o DNS outra vez. Com este proxy no
meio (`--proxy` do yt-dlp e `http_proxy`/`https_proxy` no ambiente dele), toda conexão HTTP passa
por aqui antes de sair: só `CONNECT host:443`, com o host resolvido aqui mesmo e todos os endereços
públicos (`http.public_address`, a mesma regra do transporte interno). A conexão vai para o IP
conferido, sem segunda consulta de DNS. `http://` (pedido em claro) e outra porta são recusados. O
TLS continua de ponta a ponta entre o yt-dlp e o servidor.

O proxy só vê HTTP; `rtmp`, `rtmps`, `ftp` e o `httpproxy://` do HLS do FFmpeg o ignorariam. Por isso
quem chama o yt-dlp (`social.run`) limita o seletor a formato HTTP/HLS e tira o FFmpeg do yt-dlp (o
download é nativo); o FFmpeg só corta depois o arquivo local, sem rede (`social.cut_local`).

Escuta só em `127.0.0.1`, numa porta efêmera, enquanto o comando roda. Um processo local que
achasse a porta só alcançaria o que ele já alcança sozinho: HTTPS público na 443.
"""

import contextlib
import logging
import re
import selectors
import socket
import socketserver
import sys
import threading
import time
from typing import cast

from . import effects, logs
from .errors import ProviderError
from .http import InvalidTargetError, _safe_network

_log = logs.get("egress_proxy")

ALLOWED_PORT = 443
HEAD_MAX_BYTES = 8 * 1024
HEAD_TIMEOUT_S = 10
CONNECT_TIMEOUT_S = 15
# Sem tráfego em nenhum dos lados por este tempo, o túnel fecha (o yt-dlp usa `--socket-timeout 20`).
IDLE_TIMEOUT_S = 60
TUNNEL_LIMIT = 16
RELAY_CHUNK = 64 * 1024

# `CONNECT host:porta HTTP/1.x`: nome DNS em ASCII ou IPv6 entre colchetes, porta decimal.
_CONNECT = re.compile(
    r"CONNECT (\[[0-9A-Fa-f:.]{2,45}\]|[A-Za-z0-9.-]{1,253}):([0-9]{1,5}) HTTP/1\.[01]",
    re.ASCII,
)


class _RefusedError(Exception):
    """Destino recusado: `reason` vai para o log e `status` para a resposta."""

    def __init__(self, reason, status=403):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _reply(sock, status):
    text = {200: "Connection established", 403: "Forbidden", 400: "Bad Request", 503: "Service Unavailable"}
    with contextlib.suppress(OSError):
        sock.sendall(f"HTTP/1.1 {status} {text.get(status, 'Error')}\r\nContent-Length: 0\r\n\r\n".encode("ascii"))


def _read_head(sock):
    """`(linha_do_pedido, bytes_que_vieram_depois_do_cabeçalho)`, com teto de tamanho e prazo total.

    O prazo vale para o cabeçalho inteiro, não para cada `recv`: um cliente que manda um byte por
    vez, sempre dentro do timeout, seguraria a vaga sem fim.
    """
    deadline = time.monotonic() + HEAD_TIMEOUT_S
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > HEAD_MAX_BYTES:
            raise _RefusedError("head_too_large", 400)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _RefusedError("head_timeout", 400)
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            raise _RefusedError("head_timeout", 400) from None
        except OSError:
            raise _RefusedError("head_unreadable", 400) from None
        if not chunk:
            raise _RefusedError("head_unreadable", 400)
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head.split(b"\r\n", 1)[0].decode("latin-1"), rest


def target(request_line):
    """`(host, porta)` de um `CONNECT`; pedido em claro (`GET http://…`) e outra porta levantam `_RefusedError`."""
    found = _CONNECT.fullmatch(request_line)
    if not found:
        raise _RefusedError("not_connect" if not request_line.startswith("CONNECT ") else "bad_target")
    host, port = found.group(1), int(found.group(2))
    if port != ALLOWED_PORT:
        raise _RefusedError("port_not_allowed")
    return host, port


def addresses(host):
    """Os endereços conferidos de `host` (todos públicos), pela mesma regra do transporte interno."""
    try:
        return _safe_network(f"https://{host}/")
    except InvalidTargetError:
        # Nome que não é destino (rótulo vazio ou longo, `idna` recusado): nada foi consultado.
        raise _RefusedError("bad_target", 400) from None
    except ProviderError:
        raise _RefusedError("not_public") from None


def _connect(host, rows):
    """Conecta no primeiro endereço conferido que responde; nenhuma nova consulta de DNS (`net.get`, só o host)."""
    last_error = None
    with effects.op("net.get", f"https://{host}/"):
        for family, kind, protocol, _, sockaddr in rows:
            upstream = socket.socket(family, kind, protocol)
            try:
                upstream.settimeout(CONNECT_TIMEOUT_S)
                upstream.connect(sockaddr)
                return upstream
            except OSError as error:
                last_error = error
                upstream.close()
        raise last_error or OSError("sem endereço")


def _relay(client, upstream):
    """Copia bytes nos dois sentidos até um lado fechar ou o túnel ficar parado `IDLE_TIMEOUT_S`."""
    with selectors.DefaultSelector() as selector:
        selector.register(client, selectors.EVENT_READ, upstream)
        selector.register(upstream, selectors.EVENT_READ, client)
        while True:
            ready = selector.select(IDLE_TIMEOUT_S)
            if not ready:
                return
            for key, _ in ready:
                source = cast("socket.socket", key.fileobj)
                try:
                    data = source.recv(RELAY_CHUNK)
                    if not data:
                        return
                    key.data.sendall(data)
                except OSError:
                    return


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        server = cast("_Server", self.server)
        client = self.request
        if not server.slots.acquire(blocking=False):
            logs.event(_log, logging.WARNING, "egress", host="-", result="refused", reason="too_many_tunnels")
            _reply(client, 503)
            return
        try:
            self._tunnel(client)
        finally:
            server.slots.release()

    def _tunnel(self, client):
        host = "-"
        try:
            line, rest = _read_head(client)
            host, _port = target(line)
            rows = addresses(host)
        except _RefusedError as refused:
            # A linha antes da resposta: quem recebe o 403 já encontra a recusa no log.
            logs.event(_log, logging.WARNING, "egress", host=host, result="refused", reason=refused.reason)
            _reply(client, refused.status)
            return
        try:
            upstream = _connect(host, rows)
        except OSError:
            logs.event(_log, logging.WARNING, "egress", host=host, result="error", reason="connect_failed")
            _reply(client, 503)
            return
        with upstream:
            logs.event(_log, logging.INFO, "egress", host=host, result="ok")
            _reply(client, 200)
            client.settimeout(None)
            upstream.settimeout(None)
            if rest:
                with contextlib.suppress(OSError):
                    upstream.sendall(rest)
            _relay(client, upstream)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.slots = threading.BoundedSemaphore(TUNNEL_LIMIT)

    def handle_error(self, request, client_address):
        """Erro inesperado num túnel: uma linha de log com o tipo, nunca o traceback no stderr da CLI."""
        _ = request, client_address
        kind = type(sys.exc_info()[1]).__name__
        logs.event(_log, logging.WARNING, "egress", host="-", result="error", reason="internal", error=kind)


class GuardedProxy:
    """`with GuardedProxy() as proxy:` sobe o proxy; `proxy.url` é o `http://127.0.0.1:<porta>` do `--proxy`."""

    def __init__(self):
        self._server = None
        self._thread = None
        self.url = ""

    def __enter__(self):
        with effects.op("server.start", "127.0.0.1") as starting:
            self._server = _Server()
            port = self._server.server_address[1]
            starting.target = f"127.0.0.1:{port}"
            starting.set(port=port)
        self.url = f"http://127.0.0.1:{port}"
        server = self._server
        self._thread = threading.Thread(
            target=effects.serve, args=(server,), kwargs={"address": f"127.0.0.1:{port}"}, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = self._thread = None


PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
BYPASS_VARIABLES = ("no_proxy", "NO_PROXY")


def environment(base, proxy_url):
    """O ambiente do yt-dlp com tudo apontando para o proxy e nenhuma exceção.

    `no_proxy` sai: com ele, o yt-dlp conectaria direto nos hosts listados, fora da conferência.
    """
    env = {key: value for key, value in base.items() if key not in BYPASS_VARIABLES}
    for key in PROXY_VARIABLES:
        env[key] = proxy_url
    return env
