"""Ping de identidade do servidor do Storyboard contra um servidor HTTP local de verdade:
porta inteira válida, redirecionamento nunca seguido, resposta de erro fechada, teto do
corpo e loopback sem passar pelo proxy do ambiente."""

import gc
import json
import os
import socket
import threading
import unittest
import urllib.error
import warnings
from contextlib import contextmanager
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _override import override
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import serve


@contextmanager
def _ping_server(location=None, session="sessao", status=302, padding=0):
    """Servidor local que responde o ping com `session`, ou redireciona (ou erra) com `status`; conta os pedidos.

    `padding` acrescenta um campo com esse número de caracteres ao JSON da resposta (corpo válido, só maior)."""
    hits = []

    class Handler(BaseHTTPRequestHandler):
        @override
        def do_GET(self):  # pylint: disable=invalid-name  # nome fixo do `BaseHTTPRequestHandler`
            hits.append(self.path)
            if location is not None:
                self.send_response(status)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            answer = {"session": session, "pad": "x" * padding} if padding else {"session": session}
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 - assinatura fixa  # pylint: disable=redefined-builtin
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


class PingHardeningTests(unittest.TestCase):
    """O ping só aceita porta inteira válida e nunca segue redirecionamento."""

    def test_a_real_local_server_with_an_int_port_still_answers(self):
        with _ping_server() as (port, hits):
            self.assertTrue(serve._ping(port, "sessao", timeout=2.0))
        self.assertEqual(["/__ping"], hits)

    def test_a_port_that_is_not_an_int_is_refused_without_contacting_the_server(self):
        with _ping_server() as (port, hits):
            for bad in (str(port), float(port), f"{port}/../", True, False, None, [port]):
                with self.subTest(port=bad):
                    self.assertFalse(serve._ping(bad, "sessao", timeout=2.0))
        self.assertEqual([], hits)

    def test_a_port_out_of_range_is_refused(self):
        with patch.object(serve.effects, "op", side_effect=AssertionError("nada devia ser aberto")):
            for bad in (0, -1, 65536, 70000, True, "x", 8767.0, "1@evil.example"):
                with self.subTest(port=bad):
                    self.assertFalse(serve._ping(bad, "sessao", timeout=0.5))

    def test_a_redirect_is_not_followed(self):
        with _ping_server() as (target_port, target_hits):
            location = f"http://127.0.0.1:{target_port}/__ping"
            with _ping_server(location=location) as (port, hits):
                self.assertFalse(serve._ping(port, "sessao", timeout=2.0))
            self.assertEqual(["/__ping"], hits)
        self.assertEqual([], target_hits)

    def test_an_http_error_answer_leaves_no_unclosed_socket(self):
        # 3xx (não seguido) e 4xx viram `HTTPError`, que segura o socket: sem `close()` o 3.14 avisa `ResourceWarning`.
        for status in (301, 404):
            with self.subTest(status=status), _ping_server(location="/outro", status=status) as (port, _hits):
                with warnings.catch_warnings(record=True) as caught:
                    # "always" + `record`: com "error" o aviso estoura no `__del__` e o Python o engole em silêncio.
                    warnings.simplefilter("always", ResourceWarning)
                    self.assertFalse(serve._ping(port, "sessao", timeout=2.0))
                    gc.collect()
                self.assertEqual([], [str(w.message) for w in caught if w.category is ResourceWarning])

    def test_an_http_error_is_closed_and_gives_false(self):
        err = urllib.error.HTTPError("http://127.0.0.1:1/__ping", 404, "x", Message(), None)
        with patch.object(err, "close") as closer, patch.object(serve, "_open_ping", side_effect=err):
            self.assertFalse(serve._ping(8000, "sessao"))
        closer.assert_called_once_with()


def _dead_port():
    """Uma porta local sem ninguém escutando (aberta e fechada na hora)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class PingBodyAndProxyTests(unittest.TestCase):
    """O corpo do ping tem teto, e o ping de loopback nunca passa por um proxy do ambiente."""

    def test_a_ping_body_over_the_cap_is_not_ours_even_when_it_is_valid_json(self):
        with _ping_server(padding=8192) as (port, hits):
            self.assertFalse(serve._ping(port, "sessao", timeout=2.0))
        self.assertEqual(["/__ping"], hits)

    def test_a_ping_body_under_the_cap_still_answers(self):
        with _ping_server(padding=1024) as (port, _hits):
            self.assertTrue(serve._ping(port, "sessao", timeout=2.0))

    def test_the_loopback_ping_ignores_http_proxy_from_the_environment(self):
        # Um proxy que responde como o servidor da sessão: se o ping passasse por ele, uma porta morta viraria "nossa".
        with _ping_server() as (proxy_port, proxy_hits):
            proxy = f"http://127.0.0.1:{proxy_port}"
            environ = {k: v for k, v in os.environ.items() if k.lower() not in ("no_proxy", "http_proxy", "all_proxy")}
            environ.update({"HTTP_PROXY": proxy, "http_proxy": proxy, "ALL_PROXY": proxy, "all_proxy": proxy})
            with patch.dict(os.environ, environ, clear=True):
                self.assertFalse(serve._ping(_dead_port(), "sessao", timeout=2.0))
        self.assertEqual([], proxy_hits)

    def test_the_loopback_ping_still_reaches_a_live_server_with_a_proxy_set(self):
        with _ping_server() as (proxy_port, proxy_hits), _ping_server() as (port, hits):
            proxy = f"http://127.0.0.1:{proxy_port}"
            environ = {k: v for k, v in os.environ.items() if k.lower() != "no_proxy"}
            environ.update({"HTTP_PROXY": proxy, "http_proxy": proxy})
            with patch.dict(os.environ, environ, clear=True):
                self.assertTrue(serve._ping(port, "sessao", timeout=2.0))
        self.assertEqual([], proxy_hits)
        self.assertEqual(["/__ping"], hits)


if __name__ == "__main__":
    unittest.main()
