"""Endurecimento do leitor de página aberta.

O prazo total de `web_text` contra um servidor que manda um byte por vez, os prefixos NAT64
como destino não público e os padrões do leitor de player embutido só em ASCII. Tudo offline: `socketpair`
no lugar da rede, DNS sintético.
"""

import socket
import ssl
import threading
import time
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_open_web_video import PAGE, PLAYER, PLAYER_JS, PLAYER_TAG, fake_get_text, public_dns

from getbrolls import http, providers, web_text
from getbrolls.http import ProviderError


class TrickleSocket(socket.socket):
    """Uma ponta de `socketpair` no lugar do socket TCP: já está ligada, e o HTTP do urllib roda de verdade."""

    def connect(self, address):
        pass

    def setsockopt(self, *args):
        pass


class TotalDeadlineOnTheWireTests(unittest.TestCase):
    """Um servidor que manda um byte por vez, sempre dentro do timeout do socket, não segura a leitura além do prazo."""

    def serve(self, head, trickle, tail=b""):
        """Uma conversa: depois do pedido, `head` de uma vez, `trickle` um byte a cada 0,1 s, `tail` de uma vez."""
        self.serve_all([(head, trickle, tail)])

    def serve_all(self, conversations):
        """Uma conexão por conversa, na ordem em que o cliente abre os sockets."""
        done = threading.Event()
        self.addCleanup(done.set)
        clients = []
        for head, trickle, tail in conversations:
            client, server = socket.socketpair()
            self.addCleanup(server.close)
            clients.append(TrickleSocket(fileno=client.detach()))

            def talk(server=server, head=head, trickle=trickle, tail=tail):
                try:
                    server.recv(65536)  # o pedido; a resposta só começa depois dele, como num servidor de verdade
                    server.sendall(head)
                    for byte in trickle:
                        if done.wait(0.1):
                            return
                        server.sendall(bytes([byte]))
                    server.sendall(tail)
                except OSError:
                    return

            threading.Thread(target=talk, daemon=True).start()
        sockets = iter(clients)

        def wrap(context, sock, *, server_hostname=None, **kwargs):
            return sock

        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))
        self.enterContext(patch.object(socket, "socket", side_effect=lambda *a, **k: next(sockets)))
        self.enterContext(patch.object(ssl.SSLContext, "wrap_socket", wrap))
        self.enterContext(patch.object(web_text, "TEXT_TIMEOUT_S", 1))
        self.enterContext(patch.object(web_text, "TEXT_DEADLINE_S", 0.6))

    def assert_cut_at_the_deadline(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ProviderError, "prazo"):
            web_text.get_text("https://pagina.example/lenta", 1024)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_a_body_sent_one_byte_at_a_time_is_cut_at_the_deadline(self):
        self.serve(b"HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n", b"x" * 200)
        self.assert_cut_at_the_deadline()

    def test_a_body_without_length_is_not_taken_as_complete_when_cut(self):
        self.serve(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n", b"x" * 200)
        self.assert_cut_at_the_deadline()

    def test_headers_sent_one_byte_at_a_time_are_cut_at_the_deadline(self):
        self.serve(b"HTTP/1.1 200 OK\r\n", b"X-Lento: " + b"y" * 200)
        self.assert_cut_at_the_deadline()

    def test_the_deadline_spans_the_redirects(self):
        # Cada salto leva 0,4 s, dentro do prazo de 0,6 s; os dois juntos passam dele.
        self.serve_all(
            [
                (b"HTTP/1.1 302 Found\r\nX-A: ", b"abcd", b"\r\nLocation: /final\r\nContent-Length: 0\r\n\r\n"),
                (b"HTTP/1.1 200 OK\r\nX-B: ", b"abcd", b"\r\nContent-Length: 2\r\n\r\nok"),
            ]
        )
        self.assert_cut_at_the_deadline()

    def test_a_redirect_cut_in_the_headers_is_not_followed(self):
        # O prazo corta os cabeçalhos do 302: a resposta truncada não vira redirecionamento nem erro HTTP.
        self.serve(b"HTTP/1.1 302 Found\r\nX-A: ", b"a" * 200, b"\r\nLocation: /final\r\n\r\n")
        self.assert_cut_at_the_deadline()

    def test_a_prompt_answer_still_reads_whole(self):
        self.serve(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", b"")
        self.assertEqual(
            ("https://pagina.example/lenta", "ok"), web_text.get_text("https://pagina.example/lenta", 1024)
        )


class Nat64Tests(unittest.TestCase):
    """O prefixo NAT64 (RFC 6052 e RFC 8215) leva a um IPv4 qualquer, inclusive interno: não é destino público."""

    ADDRESSES = ("64:ff9b::7f00:1", "64:ff9b::a00:1", "64:ff9b:1::a00:1", "64:ff9b::5db8:d822")

    def test_a_nat64_literal_is_not_a_public_url(self):
        for ip in self.ADDRESSES:
            with self.subTest(ip=ip):
                self.assertIsNone(http.public_url(f"https://[{ip}]/a.mp4"))

    def test_a_name_that_resolves_to_nat64_is_refused(self):
        for ip in self.ADDRESSES:
            answer = [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443, 0, 0))]
            with (
                self.subTest(ip=ip),
                patch.object(socket, "getaddrinfo", return_value=answer),
                self.assertRaisesRegex(ProviderError, "Destino de rede"),
            ):
                http.public_destination("https://videos.example/a.mp4")

    def test_an_ordinary_global_ipv6_still_passes(self):
        self.assertEqual(
            "https://[2001:4860:4860::8888]/a.mp4", http.public_url("https://[2001:4860:4860::8888]/a.mp4")
        )


class AsciiOnlyPatternsTests(unittest.TestCase):
    """`ſ` (U+017F) e `K` (U+212A) casam `s` e `k` com `IGNORECASE`; os padrões do leitor aceitam só ASCII."""

    def setUp(self):
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))

    def test_look_alike_letters_do_not_make_a_player(self):
        for script in (
            PLAYER_JS.replace("//static", "//\u017ftatic"),
            PLAYER_JS.replace(PLAYER, PLAYER[:-1] + "\u212a"),
        ):
            html = f'<{PLAYER_TAG} id="player-{PLAYER}"></{PLAYER_TAG}><script src="{script}"></script>'
            get_text, calls = fake_get_text({PAGE: html})
            with self.subTest(script=script):
                with (
                    patch.object(web_text, "get_text", side_effect=get_text),
                    self.assertRaisesRegex(ProviderError, "m3u8"),
                ):
                    providers.resolve(PAGE)
                self.assertEqual([PAGE], [call["url"] for call in calls])


if __name__ == "__main__":
    unittest.main()
