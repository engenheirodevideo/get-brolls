"""Rota aberta: o que ainda passava ao lado do proxy de saída conferida.

Protocolo fora da família HTTP (`rtmp`, `rtmps`, `ftp`), que o FFmpeg abre direto, sem o proxy.
Host que o `idna` recusa e colchete sem fechar viram recusa limpa, sem traceback. O
cabeçalho do proxy tem prazo total. Barra invertida entre aspas e o charset declarado da
playlist, lidos de outro jeito pelo FFmpeg. Multicast IPv6 e `5f00::/16` não são públicos.
Tudo offline: só sockets em 127.0.0.1, DNS sintético e ferramentas reais contra o loopback.
"""

import contextlib
import io
import json
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_egress_proxy import PUBLIC_IP, Listener, answer_for, dial, exchange
from test_open_web_video import (
    MEDIA_PLAYLIST,
    STREAM,
    GetTextTransportTests,
    fake_get_text,
    fake_media_run,
    response,
)

from getbrolls import effects, egress_proxy, providers, social, stream_check, web_text
from getbrolls import http as gb_http
from getbrolls.http import ProviderError

YT_DLP = shutil.which("yt-dlp")
FFMPEG = shutil.which("ffmpeg")


class OpenWebArgvTests(unittest.TestCase):
    """No link aberto, o seletor só aceita formato HTTP/HLS (o resto do argv: `test_open_web_local_ffmpeg`)."""

    def capture(self, url, pages):
        seen = []

        def fake_run(argv, **_kwargs):
            seen.append(argv)
            if "-o" in argv:
                Path(argv[argv.index("-o") + 1]).write_bytes(b"x")
            return subprocess.CompletedProcess(argv, 0, "{}", "")

        get_text, _ = fake_get_text(pages)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(socket, "getaddrinfo", return_value=answer_for(PUBLIC_IP)),
            patch.object(web_text, "get_text", side_effect=get_text),
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch.object(effects, "run", side_effect=fake_run),
            patch("getbrolls.media.run", side_effect=fake_media_run),
        ):
            social.download_segment(url, Path(tmp) / "clip.mp4", 0, 2)
        self.assertEqual(1, len(seen))
        return seen[0]

    def test_every_format_alternative_of_an_open_web_link_requires_an_http_or_hls_protocol(self):
        argv = self.capture(STREAM, {STREAM: MEDIA_PLAYLIST})
        selector = argv[argv.index("-f") + 1]
        for alternative in selector.split("/"):
            for part in alternative.split("+"):
                with self.subTest(part=part):
                    self.assertIn(social.HTTP_PROTOCOL_FILTER, part)

    def test_a_youtube_link_keeps_its_selector_as_before(self):
        argv = self.capture("https://www.youtube.com/watch?v=aaaaaaaaaaa", {})
        self.assertNotIn("protocol", argv[argv.index("-f") + 1])


@unittest.skipUnless(YT_DLP and FFMPEG, "yt-dlp and FFmpeg required")
class NonHttpFormatsNeverConnectTests(unittest.TestCase):
    """Com as ferramentas reais: um formato `rtmp`/`rtmps`/`ftp` para o loopback não recebe conexão nenhuma."""

    def download(self, fmt):
        listener = Listener(self)
        fmt["url"] = fmt["url"].format(port=listener.port)
        info = {
            "id": "x",
            "title": "x",
            "extractor": "generic",
            "extractor_key": "Generic",
            "webpage_url": "https://cdn.videos.example/v/x.mp4",
            "formats": [{"format_id": "0", "ext": "mp4", "vcodec": "h264", "acodec": "aac", **fmt}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.info.json"
            path.write_text(json.dumps(info), encoding="utf-8")
            arguments = social.segment_arguments(0, 2, Path(tmp) / "out.mp4", open_web=True)
            base = [str(YT_DLP), "--ignore-config", "--socket-timeout", "5", "--retries", "0"]
            with (
                patch.object(social, "command", return_value=base),
                social._egress(True),
                self.assertRaises(ProviderError),
            ):
                social.run([*arguments, "--load-info-json", str(path)], timeout=60, op="segment")
        time.sleep(0.3)
        return listener.accepted

    def test_rtmp_rtmps_and_ftp_formats_are_never_selected(self):
        for scheme in ("rtmp", "rtmps", "ftp"):
            with self.subTest(scheme=scheme):
                self.assertEqual(0, self.download({"url": scheme + "://127.0.0.1:{port}/live/x.mp4"}))

    def test_a_non_http_url_disguised_as_hls_never_connects(self):
        # Mesmo que o formato passe no seletor, o downloader nativo não abre `rtmp` nem `ftp`: a segunda trava.
        for scheme in ("rtmp", "ftp"):
            with self.subTest(scheme=scheme):
                fmt = {"url": scheme + "://127.0.0.1:{port}/live/x.m3u8", "protocol": "m3u8"}
                self.assertEqual(0, self.download(fmt))


class BadHostTests(unittest.TestCase):
    """Host com rótulo vazio ou longo e colchete sem fechar: recusa `ProviderError`, sem DNS e sem traceback."""

    BAD_URLS = (
        "https://a..b.example/x.m3u8",
        "https://" + "a" * 64 + ".example/x.m3u8",
        "https://[::1/x.m3u8",
        "https://[1:2:3]/x.m3u8",
    )

    def test_the_transport_refuses_them_before_dns(self):
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("sem DNS")):
            for url in self.BAD_URLS:
                with self.subTest(url=url), self.assertRaises(ProviderError) as caught:
                    gb_http.public_destination(url)
                self.assertNotIn("idna", str(caught.exception))

    def test_an_idna_failure_inside_getaddrinfo_is_a_refusal_too(self):
        with (
            patch.object(socket, "getaddrinfo", side_effect=UnicodeError("encoding with 'idna' codec failed")),
            self.assertRaises(ProviderError) as caught,
        ):
            gb_http.public_destination("https://videos.example/x.m3u8")
        self.assertNotIn("idna", str(caught.exception))

    def test_resolve_answers_a_provider_refusal(self):
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("sem DNS")):
            for url in self.BAD_URLS[:2]:
                with self.subTest(url=url), self.assertRaises(ProviderError):
                    providers.resolve(url)

    def test_public_url_rejects_a_broken_bracket_instead_of_raising(self):
        for url in ("https://[::1/x.mp4", "https://[zz]/x.mp4"):
            with self.subTest(url=url):
                self.assertIsNone(gb_http.public_url(url))

    def test_the_proxy_refuses_them_with_a_reply_and_no_traceback(self):
        stderr = io.StringIO()
        with (
            egress_proxy.GuardedProxy() as proxy,
            patch.object(socket, "getaddrinfo", side_effect=UnicodeError("encoding with 'idna' codec failed")),
            contextlib.redirect_stderr(stderr),
            self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
        ):
            for host in ("a..b.example", "a" * 64 + ".example", "idna.example"):
                with self.subTest(host=host):
                    self.assertRegex(exchange(proxy.url, f"CONNECT {host}:443 HTTP/1.1\r\n\r\n".encode()), r" 40[03] ")
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(3, "\n".join(cm.output).count("reason=bad_target"))

    def test_a_playlist_entry_with_a_broken_bracket_is_not_public(self):
        playlist = MEDIA_PLAYLIST.replace("seg0.ts", "https://[::1/s.ts")
        get_text, _ = fake_get_text({STREAM: playlist})
        with (
            patch.object(socket, "getaddrinfo", return_value=answer_for(PUBLIC_IP)),
            patch.object(web_text, "get_text", side_effect=get_text),
            self.assertLogs("getbrolls.stream_check", level="WARNING") as cm,
            self.assertRaises(ProviderError),
        ):
            stream_check.vet(STREAM)
        self.assertIn("reason=uri_not_public", "\n".join(cm.output))


class HeadDeadlineTests(unittest.TestCase):
    """Um cliente que manda um byte por vez, sempre dentro do timeout de cada `recv`, não segura a vaga."""

    def test_a_trickled_head_is_cut_at_the_total_deadline(self):
        head = b"CONNECT cdn.videos.example:443 HTTP/1.1\r\nX-Pad: " + b"a" * 40
        with (
            patch.object(egress_proxy, "HEAD_TIMEOUT_S", 0.6),
            egress_proxy.GuardedProxy() as proxy,
            self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
            dial(proxy.url) as client,
        ):
            elapsed = _trickle_until_answered(client, head)
        self.assertIsNotNone(elapsed, "o proxy segurou o cabeçalho além do prazo total")
        self.assertLess(elapsed or 0.0, 2.0)
        self.assertIn("reason=head_timeout", "\n".join(cm.output))


def _trickle_until_answered(client, data, gap=0.1):
    """Manda `data` um byte a cada `gap` s; os segundos até o proxy responder ou fechar, `None` se nunca."""
    started = time.monotonic()
    client.settimeout(gap)
    for byte in data:
        try:
            client.sendall(bytes([byte]))
            client.recv(1024)  # resposta (o 400) ou fim de arquivo: o proxy cortou
        except TimeoutError:
            continue
        except OSError:
            pass
        return time.monotonic() - started
    return None


class PlaylistParserDifferentialTests(unittest.TestCase):
    """O que o FFmpeg leria diferente (barra invertida entre aspas, charset declarado) não passa."""

    def test_a_backslash_inside_a_quoted_value_is_refused(self):
        for tag in (
            '#EXT-X-MAP:URI="https:\\/\\/127.0.0.1/i.mp4"',
            '#EXT-X-MAP:URI="init.mp4\\",URI=x"',
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a\\b",URI="audio/index.m3u8"',
        ):
            playlist = MEDIA_PLAYLIST.replace('#EXT-X-MAP:URI="init.mp4"', tag)
            get_text, _ = fake_get_text({STREAM: playlist})
            with (
                self.subTest(tag=tag),
                patch.object(socket, "getaddrinfo", return_value=answer_for(PUBLIC_IP)),
                patch.object(web_text, "get_text", side_effect=get_text),
                self.assertLogs("getbrolls.stream_check", level="WARNING") as cm,
                self.assertRaises(ProviderError),
            ):
                stream_check.vet(STREAM)
            self.assertIn("reason=malformed_playlist", "\n".join(cm.output))


class NativeDownloaderAttributeReaderTests(unittest.TestCase):
    """S01: quem lê a playlist de verdade é o downloader nativo do yt-dlp, que acha `URI=` com `[A-Z0-9-]+=`."""

    HIDDEN = "https://127.0.0.1/segredo"
    MAP = '#EXT-X-MAP:URI="init.mp4"'
    KEY = '#EXT-X-KEY:METHOD=AES-128,URI="https://chaves.example/k1",IV=0x00000000000000000000000000000001'
    TAGS = (
        f'#EXT-X-KEY:METHOD=AES-128,X_URI="{HIDDEN}"',
        f'#EXT-X-KEY:METHOD=AES-128,_URI="{HIDDEN}"',
        f'#EXT-X-MAP:X_URI="{HIDDEN}"',
        f'#EXT-X-MAP:_URI="{HIDDEN}"',
        '#EXT-X-KEY:METHOD=AES-128,X_URI="https://chaves.example/k"',
        '#EXT-X-MAP:_URI="https://chaves.example/init.mp4"',
        f"#EXT-X-MAP:X_URI={HIDDEN}",
    )

    def vet_with(self, tag, marker):
        playlist = MEDIA_PLAYLIST.replace(marker, tag)
        get_text, _ = fake_get_text({STREAM: playlist})
        with (
            patch.object(socket, "getaddrinfo", return_value=answer_for(PUBLIC_IP)),
            patch.object(web_text, "get_text", side_effect=get_text),
            self.assertLogs("getbrolls.stream_check", level="WARNING") as cm,
            self.assertRaises(ProviderError),
        ):
            stream_check.vet(STREAM)
        return "\n".join(cm.output)

    def test_an_attribute_name_outside_the_native_reader_alphabet_is_refused(self):
        for tag in self.TAGS:
            marker = self.MAP if tag.startswith("#EXT-X-MAP") else self.KEY
            with self.subTest(tag=tag):
                self.assertIn("reason=malformed_playlist", self.vet_with(tag, marker))

    def test_what_the_native_reader_finds_must_be_what_was_vetted(self):
        for tag in (
            '#EXT-X-MAP:URI="init.mp4",FOO=bar"URI=https://127.0.0.1/i.mp4',
            '#EXT-X-MAP:URI="init.mp4",A_URI=x',
        ):
            with self.subTest(tag=tag):
                self.assertIn("reason=malformed_playlist", self.vet_with(tag, self.MAP))

    def test_an_ordinary_playlist_still_passes(self):
        get_text, _ = fake_get_text({STREAM: MEDIA_PLAYLIST})
        with (
            patch.object(socket, "getaddrinfo", return_value=answer_for(PUBLIC_IP)),
            patch.object(web_text, "get_text", side_effect=get_text),
        ):
            self.assertEqual(STREAM, stream_check.vet(STREAM))


class PlaylistBytesTests(GetTextTransportTests):
    """A playlist é lida pelos bytes, como o yt-dlp lê; o charset declarado na resposta não muda o texto."""

    def test_a_declared_charset_does_not_change_how_the_playlist_reads(self):
        for charset in ("utf-16", "utf-7", "cp037"):
            with self.subTest(charset=charset):
                headers = [f"Content-Type: application/vnd.apple.mpegurl; charset={charset}"]
                self.exercise([response(MEDIA_PLAYLIST.encode("ascii"), headers=headers)])
                self.assertEqual(STREAM, stream_check.vet(STREAM))

    def test_a_private_segment_is_refused_whatever_the_declared_charset(self):
        body = MEDIA_PLAYLIST.replace("seg0.ts", "https://127.0.0.1/s.ts").encode("ascii")
        for charset in ("utf-16", "cp037"):
            with self.subTest(charset=charset), self.assertRaises(ProviderError):
                headers = [f"Content-Type: application/vnd.apple.mpegurl; charset={charset}"]
                self.exercise([response(body, headers=headers)])
                stream_check.vet(STREAM)

    def test_get_text_with_an_encoding_ignores_the_declared_charset(self):
        self.exercise([response("ação".encode(), headers=["Content-Type: text/plain; charset=utf-16"])])
        self.assertEqual("ação", web_text.get_text("https://pagina.example/a", 1024, encoding="utf-8")[1])


class MulticastTests(unittest.TestCase):
    """Multicast (IPv4 e IPv6) e o bloco SRv6 `5f00::/16` não contam como destino público."""

    def test_multicast_and_srv6_are_not_global(self):
        for ip in ("ff02::1", "ff0e::1", "224.0.0.1", "239.255.255.250", "5f00::1"):
            with self.subTest(ip=ip):
                self.assertFalse(gb_http.public_address(ip))

    def test_ordinary_public_addresses_still_are(self):
        for ip in (PUBLIC_IP, "2606:4700:4700::1111"):
            with self.subTest(ip=ip):
                self.assertTrue(gb_http.public_address(ip))


if __name__ == "__main__":
    unittest.main()
