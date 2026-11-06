"""Ramos de erro do proxy de saída conferida: cabeçalho ruim, conexão que falha, túnel parado e erro interno.

Tudo offline: sockets locais (`socketpair` e 127.0.0.1) e dublês com `recv`/`connect` que falham.
"""

import socket
import unittest
from unittest.mock import MagicMock, patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_egress_proxy import answer_for, dial, exchange

from getbrolls import egress_proxy

CONNECT = b"CONNECT cdn.videos.example:443 HTTP/1.1\r\nHost: cdn.videos.example:443\r\n\r\n"


class _Feed:
    """Um cliente falso para `_read_head`: cada `recv` devolve o próximo item ou levanta a exceção dele."""

    def __init__(self, *items):
        self.items = list(items)
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, _size):
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class ReadHeadTests(unittest.TestCase):
    def assert_refused(self, sock, reason):
        with self.assertRaises(egress_proxy._RefusedError) as caught:  # pylint: disable=protected-access
            egress_proxy._read_head(sock)  # pylint: disable=protected-access
        self.assertEqual(reason, caught.exception.reason)
        self.assertEqual(400, caught.exception.status)

    def test_a_head_past_the_size_cap_is_refused_before_reading_more(self):
        block = b"X" * 4096
        sock = _Feed(block, block, block, AssertionError("leu além do teto"))
        self.assert_refused(sock, "head_too_large")
        self.assertEqual(1, len(sock.items))

    def test_a_head_past_the_total_deadline_is_refused_without_another_recv(self):
        clock = MagicMock()
        clock.monotonic.side_effect = [0.0, 1.0, egress_proxy.HEAD_TIMEOUT_S + 1.0]
        sock = _Feed(b"CONNECT cdn", AssertionError("leu depois do prazo"))
        with patch.object(egress_proxy, "time", clock):
            self.assert_refused(sock, "head_timeout")
        self.assertEqual([egress_proxy.HEAD_TIMEOUT_S - 1.0], sock.timeouts)

    def test_a_recv_timeout_is_a_head_timeout(self):
        self.assert_refused(_Feed(TimeoutError()), "head_timeout")

    def test_a_socket_error_while_reading_is_an_unreadable_head(self):
        self.assert_refused(_Feed(ConnectionResetError()), "head_unreadable")

    def test_a_client_that_closes_before_the_blank_line_is_an_unreadable_head(self):
        self.assert_refused(_Feed(b"CONNECT cdn.videos.example:443 HTTP/1.1\r\n", b""), "head_unreadable")

    def test_a_complete_head_returns_the_request_line_and_the_bytes_after_it(self):
        line, rest = egress_proxy._read_head(_Feed(CONNECT + b"\x16\x03"))  # pylint: disable=protected-access
        self.assertEqual("CONNECT cdn.videos.example:443 HTTP/1.1", line)
        self.assertEqual(b"\x16\x03", rest)


class _Upstream:
    """Um socket de destino falso: `connect` falha com `error` (ou conecta, sem `error`)."""

    def __init__(self, error=None):
        self.error = error
        self.closed = False
        self.connected_to = None

    def settimeout(self, _value):
        pass

    def connect(self, sockaddr):
        if self.error is not None:
            raise self.error
        self.connected_to = sockaddr

    def close(self):
        self.closed = True


class ConnectTests(unittest.TestCase):
    def connect_with(self, upstreams, rows):
        fake_socket = MagicMock()
        fake_socket.socket.side_effect = upstreams
        with patch.object(egress_proxy, "socket", fake_socket):
            return egress_proxy._connect("cdn.videos.example", rows)  # pylint: disable=protected-access

    def test_a_failed_address_is_closed_and_the_next_one_is_tried(self):
        first, second = _Upstream(ConnectionRefusedError()), _Upstream()
        rows = answer_for("93.184.216.34") + answer_for("93.184.216.35")
        self.assertIs(second, self.connect_with([first, second], rows))
        self.assertTrue(first.closed)
        self.assertFalse(second.closed)
        self.assertEqual(("93.184.216.35", 443), second.connected_to)

    def test_when_every_address_fails_the_last_error_goes_up(self):
        last = TimeoutError("timed out")
        upstreams = [_Upstream(ConnectionRefusedError()), _Upstream(last)]
        rows = answer_for("93.184.216.34") + answer_for("2606:2800:220:1:248:1893:25c8:1946")
        with self.assertRaises(TimeoutError) as caught:
            self.connect_with(upstreams, rows)
        self.assertIs(last, caught.exception)
        self.assertTrue(all(upstream.closed for upstream in upstreams))

    def test_no_address_at_all_is_an_os_error_without_any_socket(self):
        with self.assertRaises(OSError) as caught:
            self.connect_with([AssertionError("abriu socket sem endereço")], [])
        self.assertEqual("sem endereço", str(caught.exception))


class _Wrapped:
    """Um lado do túnel com `fileno` real (para o seletor) e `recv`/`sendall` controlados no teste."""

    def __init__(self, sock, recv_error=None):
        self.sock = sock
        self.recv_error = recv_error
        self.sent = []

    def fileno(self):
        return self.sock.fileno()

    def recv(self, size):
        if self.recv_error is not None:
            raise self.recv_error
        return self.sock.recv(size)

    def sendall(self, data):
        self.sent.append(data)


class RelayTests(unittest.TestCase):
    def pair(self):
        # effect-exempt: connected local pair that stands in for one side of the tunnel, no network
        near, far = socket.socketpair()
        self.addCleanup(near.close)
        self.addCleanup(far.close)
        return near, far

    def test_a_tunnel_with_no_traffic_closes_at_the_idle_timeout(self):
        client, _ = self.pair()
        upstream, _ = self.pair()
        with patch.object(egress_proxy, "IDLE_TIMEOUT_S", 0.05):
            egress_proxy._relay(client, upstream)  # pylint: disable=protected-access

    def test_a_read_error_on_either_side_ends_the_tunnel(self):
        client_near, client_far = self.pair()
        upstream_near, _ = self.pair()
        client_far.sendall(b"dados")
        client = _Wrapped(client_near, recv_error=ConnectionResetError())
        upstream = _Wrapped(upstream_near)
        with patch.object(egress_proxy, "IDLE_TIMEOUT_S", 5):
            egress_proxy._relay(client, upstream)  # pylint: disable=protected-access
        self.assertEqual([], upstream.sent)

    def test_bytes_from_one_side_reach_the_other_until_it_closes(self):
        client_near, client_far = self.pair()
        upstream_near, _ = self.pair()
        client_far.sendall(b"dados")
        client_far.shutdown(socket.SHUT_WR)
        upstream = _Wrapped(upstream_near)
        with patch.object(egress_proxy, "IDLE_TIMEOUT_S", 5):
            egress_proxy._relay(client_near, upstream)  # pylint: disable=protected-access
        self.assertEqual(b"dados", b"".join(upstream.sent))


def _closed_port():
    """Uma porta de 127.0.0.1 sem ninguém escutando (pega e solta na hora)."""
    # effect-exempt: loopback probe that only finds a free port, nothing is sent
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class HandlerTests(unittest.TestCase):
    def test_a_tunnel_over_the_limit_gets_503_and_a_log_line(self):
        with (
            patch.object(egress_proxy, "TUNNEL_LIMIT", 0),
            egress_proxy.GuardedProxy() as proxy,
            self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
        ):
            line = exchange(proxy.url, CONNECT)
        self.assertIn("503", line)
        self.assertIn("reason=too_many_tunnels", "\n".join(cm.output))

    def test_a_destination_that_refuses_the_connection_gets_503(self):
        rows = answer_for("127.0.0.1", _closed_port())
        with (
            patch.object(egress_proxy, "addresses", return_value=rows),
            egress_proxy.GuardedProxy() as proxy,
            self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
        ):
            line = exchange(proxy.url, CONNECT)
        self.assertIn("503", line)
        output = "\n".join(cm.output)
        self.assertIn("result=error", output)
        self.assertIn("reason=connect_failed", output)

    def test_an_unexpected_error_in_a_tunnel_is_one_log_line_with_its_type(self):
        with (
            patch.object(egress_proxy, "target", side_effect=RuntimeError("boom")),
            egress_proxy.GuardedProxy() as proxy,
            self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
            dial(proxy.url) as client,
        ):
            client.sendall(CONNECT)
            self.assertEqual(b"", client.recv(1024))
        output = "\n".join(cm.output)
        self.assertIn("reason=internal", output)
        self.assertIn("error=RuntimeError", output)
        self.assertNotIn("boom", output)


class GuardedProxyTests(unittest.TestCase):
    def test_leaving_without_entering_is_a_no_op(self):
        proxy = egress_proxy.GuardedProxy()
        proxy.__exit__(None, None, None)
        self.assertEqual("", proxy.url)


if __name__ == "__main__":
    unittest.main()
