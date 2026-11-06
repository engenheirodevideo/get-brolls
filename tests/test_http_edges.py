"""http.py: transporte preso a IPs públicos, respostas ruins, downloads truncados e Retry-After."""

import email.message
import errno
import io
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
import _logs
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import http
from getbrolls.http import ProviderError

PUBLIC = "93.184.216.34"
OTHER_PUBLIC = "93.184.216.35"


def address(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


class FakeResponse:
    def __init__(self, body, headers=None):
        self._body = body
        self._pos = 0
        self.status = 200
        self.headers = headers or {}

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._body)
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class ScriptedOpener:
    """Cada `open` consome um passo: exceção é levantada, resposta é devolvida."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0

    def open(self, *args, **kwargs):
        self.calls += 1
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def http_error(code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.example.org/v1", code, "erro", headers, io.BytesIO(b""))


class FakeSocket:
    """Socket que falha em `connect` para os IPs de `failing` e registra o que aconteceu."""

    failing: ClassVar[tuple[str, ...]] = ()
    created: ClassVar[list] = []

    def __init__(self, family, kind, protocol):
        self.target = None
        self.bound = None
        self.timeout = None
        self.closed = False
        FakeSocket.created.append(self)

    def settimeout(self, timeout):
        self.timeout = timeout

    def bind(self, source):
        self.bound = source

    def connect(self, sockaddr):
        self.target = sockaddr[0]
        if sockaddr[0] in FakeSocket.failing:
            raise OSError(errno.ECONNREFUSED, "recusado")

    def close(self):
        self.closed = True


class SmallHelperTests(unittest.TestCase):
    def test_host_of_gives_the_hostname_only_and_a_dash_for_a_malformed_url(self):
        self.assertEqual("api.example.org", http._host_of("https://api.example.org/v1?key=segredo"))
        self.assertEqual("-", http._host_of("https://[::1"))

    def test_strict_secret_key_ignores_names_that_are_not_text(self):
        self.assertFalse(http._strict_secret_key(5))
        self.assertTrue(http._strict_secret_key("client_secret"))
        self.assertFalse(http._strict_secret_key("next_page_token"))

    def test_public_url_accepts_a_global_ip_literal_and_refuses_a_private_one(self):
        self.assertEqual(f"https://{PUBLIC}/a", http.public_url(f"https://{PUBLIC}/a"))
        self.assertIsNone(http.public_url("https://10.0.0.1/a"))

    def test_retry_after_without_a_value_is_none(self):
        self.assertIsNone(http._retry_after_seconds(None))
        self.assertIsNone(http._retry_after_seconds(""))
        self.assertIsNone(http._retry_after_seconds("   "))
        self.assertIsNone(http._retry_after_seconds("quando der"))

    def test_retry_after_in_seconds_is_capped_unless_the_cap_is_off(self):
        self.assertEqual(7, http._retry_after_seconds("7"))
        self.assertEqual(http.RETRY_AFTER_CAP_S, http._retry_after_seconds("9999"))
        self.assertEqual(9999, http._retry_after_seconds("9999", cap=None))

    def test_retry_after_as_an_http_date_without_a_zone_is_read_as_utc(self):
        # `-0000` é a forma da RFC 2822 para "fuso desconhecido": o datetime sai sem tzinfo.
        self.assertEqual(http.RETRY_AFTER_CAP_S, http._retry_after_seconds("Wed, 21 Oct 2099 07:28:00 -0000"))
        self.assertEqual(0, http._retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT"))


class RetryAfterRoundingTests(unittest.TestCase):
    """An HTTP-date Retry-After waits whole seconds, rounded UP: never retry before the date."""

    DATE = "Wed, 21 Oct 2099 07:28:00 GMT"
    MOMENT = datetime(2099, 10, 21, 7, 28, 0, tzinfo=UTC)

    def seconds_with_now(self, now):
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return now if tz is None else now.astimezone(tz)

        with patch.object(http, "datetime", Frozen):
            return http._retry_after_seconds(self.DATE)

    def test_a_fraction_of_a_second_left_counts_as_a_whole_second(self):
        cases = {2.4: 3, 2.0: 2, 0.001: 1, 0.0: 0, -5.0: 0, 59.5: 60}
        for left, expected in cases.items():
            with self.subTest(left=left):
                self.assertEqual(expected, self.seconds_with_now(self.MOMENT - timedelta(seconds=left)))


class DownloadRenditionTests(unittest.TestCase):
    def test_a_rendition_over_the_cap_logs_the_fallback_and_the_next_one_is_used(self):
        fresh = {
            "media_url": "https://images.example.org/orig.tif?sig=segredo",
            "media_url_fallbacks": ["https://images.example.org/large.jpg", "https://images.example.org/small.jpg"],
        }
        tried = []

        def download(url, target, **kwargs):
            del target, kwargs
            tried.append(url)
            if len(tried) == 1:
                raise http.DownloadTooLargeError("acima do teto")

        with (
            patch.object(http, "download", side_effect=download),
            self.assertLogs("getbrolls.http", level="INFO") as cm,
        ):
            used = http.download_rendition(fresh, Path("alvo.jpg"))
        self.assertEqual("https://images.example.org/large.jpg", used)
        self.assertEqual(fresh["media_url_fallbacks"][:1], tried[1:])
        events = _logs.events(cm.output, "download_fallback")
        self.assertEqual([("images.example.org", "size_cap")], [(e["host"], e["reason"]) for e in events])
        self.assertNotIn("segredo", "\n".join(cm.output))

    def test_the_smallest_rendition_over_the_cap_raises_after_one_fallback_line_per_larger_one(self):
        fresh = {"media_url": "https://a.example.org/1", "media_url_fallbacks": ["https://b.example.org/2"]}
        with (
            patch.object(http, "download", side_effect=http.DownloadTooLargeError("acima do teto")) as downloaded,
            self.assertLogs("getbrolls.http", level="INFO") as cm,
            self.assertRaises(http.DownloadTooLargeError),
        ):
            http.download_rendition(fresh, Path("alvo.jpg"))
        self.assertEqual(2, downloaded.call_count)
        self.assertEqual(["a.example.org"], [e["host"] for e in _logs.events(cm.output, "download_fallback")])


class SafeNetworkTests(unittest.TestCase):
    def test_a_host_that_does_not_resolve_is_refused_and_logged_without_the_path(self):
        with (
            patch.object(http.socket, "getaddrinfo", side_effect=socket.gaierror("sem DNS")),
            self.assertLogs("getbrolls.http", level="WARNING") as cm,
            self.assertRaisesRegex(ProviderError, "resolver"),
        ):
            http._safe_network("https://api.example.org/v1?key=segredo")
        event = _logs.events(cm.output, "request_refused")[0]
        self.assertEqual(("api.example.org", "dns_resolution_failed"), (event["host"], event["reason"]))
        self.assertNotIn("segredo", "\n".join(cm.output))


class PinnedConnectionTests(unittest.TestCase):
    """O handler conecta só aos IPs que ele mesmo validou, sem uma segunda consulta de DNS."""

    def setUp(self):
        FakeSocket.created = []
        FakeSocket.failing = ()
        self.addCleanup(setattr, FakeSocket, "failing", ())

    def connect(self, addresses, **kwargs):
        handler = http._PinnedHTTPSHandler()
        request = urllib.request.Request("https://example.org/")
        captured = {}

        def do_open(factory, req, **_):
            captured["conn"] = factory(req.host, timeout=5)
            return object()

        handler.do_open = do_open  # pyright: ignore[reportAttributeAccessIssue]
        with patch.object(http.socket, "getaddrinfo", return_value=addresses):
            handler.https_open(request)
        with patch.object(http.socket, "socket", FakeSocket):
            return captured["conn"]._create_connection(("example.org", 443), 5, **kwargs)

    def test_the_first_reachable_public_address_wins_and_failed_sockets_are_closed(self):
        FakeSocket.failing = (PUBLIC,)
        sock = self.connect([address(PUBLIC), address(OTHER_PUBLIC)])
        self.assertEqual(OTHER_PUBLIC, sock.target)
        self.assertEqual([True, False], [s.closed for s in FakeSocket.created])
        self.assertEqual(5, sock.timeout)

    def test_every_address_failing_raises_the_last_error(self):
        FakeSocket.failing = (PUBLIC, OTHER_PUBLIC)
        with self.assertRaises(OSError) as raised:
            self.connect([address(PUBLIC), address(OTHER_PUBLIC)])
        self.assertEqual(errno.ECONNREFUSED, raised.exception.errno)
        self.assertTrue(all(s.closed for s in FakeSocket.created))

    def test_an_interrupt_while_connecting_closes_the_socket_and_propagates(self):
        with patch.object(FakeSocket, "connect", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.connect([address(PUBLIC)])
        self.assertTrue(FakeSocket.created[0].closed)

    def test_a_source_address_is_bound_before_connecting(self):
        sock = self.connect([address(PUBLIC)], source_address=("0.0.0.0", 0))  # noqa: S104
        self.assertEqual(("0.0.0.0", 0), sock.bound)  # noqa: S104
        self.assertEqual(PUBLIC, sock.target)

    def test_a_private_address_among_the_answers_refuses_the_whole_request(self):
        handler = http._PinnedHTTPSHandler()
        request = urllib.request.Request("https://example.org/")
        with (
            patch.object(http.socket, "getaddrinfo", return_value=[address(PUBLIC), address("10.0.0.5")]),
            self.assertRaisesRegex(ProviderError, "Destino de rede"),
        ):
            handler.https_open(request)


class GetJsonEdgeTests(unittest.TestCase):
    URL = "https://api.example.org/v1/search"

    def setUp(self):
        self.opener = ScriptedOpener()
        self.sleep = MagicMock()

    def get(self, *steps, **kwargs):
        self.opener = ScriptedOpener(*steps)
        with patch.object(http, "_opener", return_value=self.opener), patch.object(http.time, "sleep") as sleep:
            self.sleep = sleep
            return http.get_json(self.URL, **kwargs)

    def test_a_response_over_8_mb_is_refused(self):
        with self.assertRaisesRegex(ProviderError, "8 MB"):
            self.get(FakeResponse(b"x" * (8 * 1024 * 1024 + 1)))

    def test_a_body_that_is_not_json_is_refused_without_retrying_and_is_logged(self):
        with (
            self.assertLogs("getbrolls.http", level="WARNING") as cm,
            self.assertRaisesRegex(ProviderError, "JSON inválida"),
        ):
            self.get(FakeResponse(b"<html>nao e json</html>"))
        self.assertEqual(1, self.opener.calls)
        event = _logs.events(cm.output, "request")[0]
        self.assertEqual(("json", None, "1"), (event["op"], event["status"], event["attempt"]))

    def test_a_429_with_retry_after_on_the_last_attempt_ends_without_data(self):
        steps = (http_error(500), http_error(500), http_error(429, retry_after="1"))
        with self.assertRaisesRegex(ProviderError, "não respondeu"):
            self.get(*steps)
        self.assertEqual(3, self.opener.calls)
        self.assertEqual([1], [call.args[0] for call in self.sleep.call_args_list][-1:])


class DownloadEdgeTests(unittest.TestCase):
    URL = "https://videos.example.org/clip.mp4"

    def setUp(self):
        # O ciclo de vida cobre o teste inteiro; a limpeza é feita via addCleanup.
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.target = Path(tmp.name) / "clip.mp4"

    def download(self, *steps, **kwargs):
        opener = ScriptedOpener(*steps)
        with patch.object(http, "_opener", return_value=opener):
            return http.download(self.URL, self.target, **kwargs)

    def test_an_empty_body_is_refused_and_leaves_no_file(self):
        with self.assertRaisesRegex(ProviderError, "Mídia vazia"):
            self.download(FakeResponse(b""))
        self.assertFalse(self.target.exists())

    def test_a_body_shorter_than_content_length_is_refused_and_leaves_no_file(self):
        with self.assertRaisesRegex(ProviderError, "Download incompleto"):
            self.download(FakeResponse(b"12345", headers={"Content-Length": "10"}))
        self.assertFalse(self.target.exists())

    def test_a_non_positive_byte_limit_is_refused_before_any_request(self):
        with (
            self.assertRaisesRegex(ProviderError, "Limite de bytes inválido"),
            patch.object(http, "_opener", side_effect=AssertionError("não devia abrir a rede")),
        ):
            http.download(self.URL, self.target, max_bytes=0)
        self.assertFalse(self.target.exists())

    def test_a_value_error_from_the_transport_without_headers_names_its_type_and_message(self):
        with self.assertRaisesRegex(ProviderError, r"arquivo público \(ValueError: url quebrada\)"):
            self.download(ValueError("url quebrada"))
        self.assertFalse(self.target.exists())

    def test_a_write_failure_while_streaming_names_the_errno_and_the_file(self):
        class FullDisk:
            def write(self, chunk):
                raise OSError(errno.ENOSPC, "sem espaço", "/disco/clip.mp4")

        # Outside every known folder, only the name is left: never the machine path.
        # Outside every known folder, neither the folder nor the name: a hash and the extension.
        with self.assertRaisesRegex(
            ProviderError, rf"errno {errno.ENOSPC}\): <pasta fora do projeto>/#[0-9a-f]{{8}}\.mp4$"
        ):
            http._download_write_body(FakeResponse(b"dados"), FullDisk(), self.target, 1024, lambda size=None: None)

    def test_a_body_over_the_cap_stops_at_the_cap_and_raises_the_size_error(self):
        raised = []

        def too_big(size=None):
            raised.append(size)
            return http.DownloadTooLargeError("grande demais")

        with self.assertRaises(http.DownloadTooLargeError):
            http._download_write_body(FakeResponse(b"x" * 50), io.BytesIO(), self.target, 10, too_big)
        self.assertEqual([11], raised)


if __name__ == "__main__":
    unittest.main()
