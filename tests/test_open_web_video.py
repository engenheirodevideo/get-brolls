"""Link direto de vídeo (.m3u8/.mp4) e leitor de página com player embutido, sem rede.

Toda página, player e endereço daqui é sintético: conta 00000000-…, player `a…a`, vídeo `b…b`.
A rede é trocada no limite certo: `web_text.get_text` nos testes do leitor, o socket nos do transporte.
"""

import io
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_sdk_plugin_providers import PluginTestCase

from getbrolls import acquisition, http, page_video, providers, queue, social, stream_check, web_text
from getbrolls.cli import main
from getbrolls.errors import RouteRefusal
from getbrolls.http import ProviderError

ACCOUNT = "00000000-0000-0000-0000-000000000000"
PLAYER = "aaaaaaaaaaaaaaaaaaaaaaaa"
VIDEO = "bbbbbbbbbbbbbbbbbbbbbbbb"
PAGE = "https://pagina.example/oferta?utm_source=bio#topo"
REFERER = "https://pagina.example/oferta"
# Player sintético: nenhum nome de serviço real; o leitor acha o player pela forma, não pelo nome.
PLAYER_TAG = "site-player"
PLAYER_HOST = "static.player.example"
CDN_HOST = "cdn.example"
PLAYER_JS = f"https://{PLAYER_HOST}/{ACCOUNT}/players/{PLAYER}/v4/player.js"
HLS = f"https://{CDN_HOST}/{ACCOUNT}/{VIDEO}/main.m3u8"
PUBLIC_IP = "93.184.216.34"
# Os `KEYFORMAT` de DRM de CDM (Widevine e PlayReady pelo UUID do sistema).
WIDEVINE = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"
PLAYREADY = "urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95"

PAGE_HTML = f"""<!doctype html><html><body>
<h1>Oferta</h1>
<{PLAYER_TAG} id="player-{PLAYER}" style="display:block;margin:0 auto"></{PLAYER_TAG}>
<script type="text/javascript">var s=document.createElement("script");
s.src="{PLAYER_JS}",s.async=!0,document.head.appendChild(s);</script>
</body></html>"""

PLAYER_BODY = (
    '!function(){var c={"id":"'
    + PLAYER
    + '","drm":false,"video":{"hls":"'
    + HLS.replace("/", "\\/")
    + '"}};window.sitePlayer=c}();'
)

# Playlist HLS de mídia sintética: init, um segmento relativo e um absoluto, tudo HTTPS público.
MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:6
#EXT-X-MAP:URI="init.mp4"
#EXT-X-KEY:METHOD=AES-128,URI="https://chaves.example/k1",IV=0x00000000000000000000000000000001
#EXTINF:6.0,
seg0.ts
#EXTINF:6.0,
https://cdn2.videos.example/seg1.ts
#EXT-X-ENDLIST
"""

# A página, o player e o HLS que ele cita: o `resolve` de uma página com player embutido lê os três
# (o HLS pelo `stream_check`).
EMBEDDED_PLAYER_PAGES = {PAGE: PAGE_HTML, PLAYER_JS: PLAYER_BODY, HLS: MEDIA_PLAYLIST}


def public_dns(*_args, **_kwargs):
    """Resposta de DNS sintética: um endereço público só."""
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 443))]


def fake_get_text(pages):
    """`web_text.get_text` de mentira: devolve o corpo da URL pedida e anota cada pedido."""
    calls = []

    def get_text(url, max_bytes, allowed_hosts=None, *, encoding=None):
        calls.append({"url": url, "max_bytes": max_bytes, "allowed_hosts": allowed_hosts, "encoding": encoding})
        if url not in pages:
            raise AssertionError(f"pedido inesperado: {url}")
        answer = pages[url]
        # Uma tupla `(url_final, corpo)` simula um redirecionamento já seguido e conferido.
        return answer if isinstance(answer, tuple) else (url, answer)

    return get_text, calls


class DirectVideoRouteTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))
        self.reader = self.enterContext(
            patch.object(web_text, "get_text", side_effect=AssertionError("link direto não abre página"))
        )

    def test_a_public_m3u8_becomes_a_yt_dlp_candidate(self):
        url = "https://videos.example/curso/aula/main.m3u8"
        item = providers.resolve(url)
        self.assertEqual("direct_stream", item["provider"])
        self.assertTrue(item["id"].startswith("direct_stream:"))
        self.assertEqual(url, item["source_url"])
        self.assertEqual("yt-dlp", item["acquisition"]["method"])
        self.assertEqual("available", item["acquisition"]["status"])
        self.assertNotIn("referer", item["acquisition"])
        self.assertEqual("unknown", item["rights"]["status"])
        self.assertIsNone(item["rights"]["license_name"])
        self.assertIn("videos.example", item["title"])
        self.assertEqual("candidate", item["state"])
        self.assertFalse(acquisition.direct_media(item))

    def test_suffix_is_case_insensitive_and_the_query_is_kept(self):
        url = "https://videos.example/arquivo/CLIPE.MP4?v=2"
        item = providers.resolve(url)
        self.assertEqual("direct_stream", item["provider"])
        self.assertEqual(url, item["source_url"])

    def test_the_same_link_gives_the_same_id(self):
        url = "https://videos.example/a.mp4"
        self.assertEqual(providers.resolve(url)["id"], providers.resolve(url)["id"])
        self.assertNotEqual(providers.resolve(url)["id"], providers.resolve("https://videos.example/b.mp4")["id"])

    def test_private_loopback_metadata_and_credentials_are_refused(self):
        for url in (
            "http://videos.example/a.mp4",
            "https://usuario:senha@videos.example/a.mp4",
            "https://127.0.0.1/a.mp4",
            "https://localhost/a.m3u8",
            "https://10.0.0.5/a.m3u8",
            "https://192.168.0.10/a.mp4",
            "https://169.254.169.254/latest/main.m3u8",
            "https://[::1]/a.mp4",
            "file:///etc/a.mp4",
        ):
            with self.subTest(url=url), self.assertRaises(ProviderError):
                providers.resolve(url)
        self.reader.assert_not_called()

    def test_a_host_that_resolves_to_a_private_address_is_refused(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.5", 443))]
        with (
            patch.object(socket, "getaddrinfo", return_value=private),
            self.assertRaisesRegex(ProviderError, "rede privada ou local"),
        ):
            providers.resolve("https://interno.example/a.m3u8")

    def test_the_route_is_listed_honestly(self):
        caps = providers.capabilities()
        self.assertTrue(caps["direct_stream"]["resolve_url"])
        self.assertFalse(caps["direct_stream"]["search"])
        self.assertEqual("yt-dlp", caps["direct_stream"]["transport"])
        self.assertTrue(caps["embedded_player"]["resolve_url"])
        self.assertFalse(caps["embedded_player"]["search"])
        self.assertIn("yt-dlp", caps["embedded_player"]["transport"])
        with self.assertRaisesRegex(ProviderError, "Busca indisponível"):
            providers.search("direct_stream", "aula", 1)


class EmbeddedPlayerReaderTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))

    def resolve_with(self, pages, url=PAGE):
        get_text, calls = fake_get_text(pages)
        with patch.object(web_text, "get_text", side_effect=get_text):
            return providers.resolve(url), calls

    def refusal_with(self, pages, url=PAGE):
        get_text, calls = fake_get_text(pages)
        with patch.object(web_text, "get_text", side_effect=get_text), self.assertRaises(ProviderError) as caught:
            providers.resolve(url)
        return str(caught.exception), calls

    def test_page_with_a_embedded_player_resolves_to_its_hls_with_the_page_as_referer(self):
        item, calls = self.resolve_with(EMBEDDED_PLAYER_PAGES)
        self.assertEqual("embedded_player", item["provider"])
        self.assertEqual(f"embedded_player:{VIDEO}", item["id"])
        self.assertEqual(HLS, item["source_url"])
        self.assertEqual("yt-dlp", item["acquisition"]["method"])
        self.assertEqual(REFERER, item["acquisition"]["referer"])
        self.assertEqual(REFERER, item["creator"]["url"])
        self.assertEqual("unknown", item["rights"]["status"])
        self.assertEqual([PAGE, PLAYER_JS, HLS], [c["url"] for c in calls])
        self.assertEqual(page_video.PAGE_MAX_BYTES, calls[0]["max_bytes"])
        self.assertEqual(page_video.PLAYER_MAX_BYTES, calls[1]["max_bytes"])
        self.assertEqual((PLAYER_HOST,), calls[1]["allowed_hosts"])

    def test_minified_drm_flag_off_is_not_drm(self):
        body = PLAYER_BODY.replace('"drm":false', "drm:!1")
        item, _ = self.resolve_with({**EMBEDDED_PLAYER_PAGES, PLAYER_JS: body})
        self.assertEqual(HLS, item["source_url"])

    def test_player_js_behind_userinfo_is_ignored(self):
        html = PAGE_HTML.replace(f"https://{PLAYER_HOST}/", f"https://{PLAYER_HOST}@outro.example/")
        _, calls = self.refusal_with({PAGE: html})
        self.assertEqual([PAGE], [c["url"] for c in calls])

    def test_the_player_drm_flag_does_not_refuse_the_stream(self):
        # A chave `drm` do player não decide a recusa: o HLS é tentado como o navegador o toca.
        for flag in ('"drm":true', "drm:!0", "'drm': true"):
            with self.subTest(flag=flag):
                body = PLAYER_BODY.replace('"drm":false', flag)
                item, calls = self.resolve_with({**EMBEDDED_PLAYER_PAGES, PLAYER_JS: body})
                self.assertEqual(HLS, item["source_url"])
                self.assertEqual(HLS, calls[-1]["url"])

    def test_page_without_a_player_tells_how_to_find_the_direct_link(self):
        message, calls = self.refusal_with({PAGE: "<html><body><video src='blob:x'></video></body></html>"})
        self.assertEqual(1, len(calls))
        self.assertIn("Fonte de URL não suportada", message)
        self.assertIn(".m3u8", message)
        self.assertIn(".mp4", message)
        self.assertIn("DevTools", message)
        self.assertIn("Network", message)
        self.assertIn("m3u8", message)

    def test_an_unreachable_page_keeps_the_refusal_and_the_hint(self):
        def unreachable(url, max_bytes, allowed_hosts=None):
            raise ProviderError("Falha ao resolver provedor")

        with patch.object(web_text, "get_text", side_effect=unreachable), self.assertRaises(ProviderError) as caught:
            providers.resolve(PAGE)
        self.assertIn("Fonte de URL não suportada", str(caught.exception))
        self.assertIn("DevTools", str(caught.exception))

    def test_the_log_line_names_only_the_host(self):
        get_text, _ = fake_get_text(EMBEDDED_PLAYER_PAGES)
        with (
            patch.object(web_text, "get_text", side_effect=get_text),
            self.assertLogs("getbrolls.page_video", level="INFO") as cm,
        ):
            providers.resolve(PAGE)
        joined = "\n".join(cm.output)
        self.assertIn("event=page_video", joined)
        self.assertIn("host=pagina.example", joined)
        self.assertIn("result=found", joined)
        for secret in (ACCOUNT, PLAYER, VIDEO, "utm_source", "/oferta"):
            self.assertNotIn(secret, joined)

    def test_cdm_drm_in_the_playlist_is_refused_with_a_route_and_a_reason(self):
        widevine = f'#EXT-X-KEY:METHOD=SAMPLE-AES,URI="data:text/plain;base64,AAAA",KEYFORMAT="{WIDEVINE}"'
        playlist = MEDIA_PLAYLIST.replace("#EXT-X-TARGETDURATION:6", widevine + "\n#EXT-X-TARGETDURATION:6")
        get_text, _ = fake_get_text({**EMBEDDED_PLAYER_PAGES, HLS: playlist})
        with (
            patch.object(web_text, "get_text", side_effect=get_text),
            self.assertLogs("getbrolls.stream_check", level="WARNING") as cm,
            self.assertRaises(RouteRefusal) as caught,
        ):
            providers.resolve(PAGE)
        self.assertIn("reason=drm", "\n".join(cm.output))
        self.assertIn("não contorna", str(caught.exception))
        route = caught.exception.suggested_route
        self.assertEqual("drm", route["kind"])
        self.assertIsNone(route["next_command"])
        self.assertTrue(any("YouTube" in hint for hint in route["alternatives"]))


class WireSocket:
    """Socket de mentira: o HTTP do urllib roda de verdade, cada conexão lê a próxima resposta da fila."""

    def __init__(self, wire):
        self.wire = wire

    def settimeout(self, value):
        pass

    def setsockopt(self, *args):
        pass

    def connect(self, address):
        self.wire["connections"].append(address)

    def sendall(self, data):
        self.wire["requests"].append(data)

    def makefile(self, *args):
        return io.BytesIO(self.wire["responses"].pop(0))

    def shutdown(self, how):
        self.wire["shutdowns"].append(how)

    def close(self):
        pass


def fake_media_run(args, *, op=None):
    """`media.run` de mentira: o FFmpeg "escreve" o arquivo de saída (o último argumento) e não devolve nada."""
    del op
    if args[0] == "ffmpeg" and args[-1] != "-":
        Path(args[-1]).write_bytes(b"x")
    return ""


def response(body, status="200 OK", headers=()):
    """Resposta HTTP crua com `Content-Length` certo."""
    head = [f"HTTP/1.1 {status}", f"Content-Length: {len(body)}", *headers]
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


class GetTextTransportTests(unittest.TestCase):
    def exercise(self, responses, answers=None):
        wire = {"connections": [], "requests": [], "responses": list(responses), "lookups": [], "shutdowns": []}
        answers = answers or {}

        def resolve(host, port, *args, **kwargs):
            wire["lookups"].append(host)
            ip = answers.get(host, PUBLIC_IP)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]

        def wrap(context, sock, *, server_hostname=None, **kwargs):
            return sock

        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=resolve))
        self.enterContext(patch.object(socket, "socket", side_effect=lambda *a, **k: WireSocket(wire)))
        self.enterContext(patch.object(ssl.SSLContext, "wrap_socket", wrap))
        return wire

    def test_reads_a_public_page_as_text(self):
        wire = self.exercise([response(b"<html>ok</html>", headers=["Content-Type: text/html"])])
        final, text = web_text.get_text("https://pagina.example/oferta", 1024)
        self.assertEqual(("https://pagina.example/oferta", "<html>ok</html>"), (final, text))
        self.assertEqual([(PUBLIC_IP, 443)], wire["connections"])

    def test_a_body_over_the_cap_is_refused(self):
        self.exercise([b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + b"x" * 2048])
        with self.assertRaisesRegex(ProviderError, "limite"):
            web_text.get_text("https://pagina.example/grande", 1024)

    def test_a_declared_length_over_the_cap_is_refused_before_reading(self):
        self.exercise([response(b"x" * 2048)])
        with self.assertRaisesRegex(ProviderError, "limite"):
            web_text.get_text("https://pagina.example/grande", 1024)

    def test_redirect_to_a_private_ip_is_refused_without_connecting(self):
        wire = self.exercise([response(b"", "302 Found", ["Location: https://10.0.0.1/admin"])])
        with self.assertRaises(ProviderError):
            web_text.get_text("https://pagina.example/curto", 1024)
        self.assertEqual([(PUBLIC_IP, 443)], wire["connections"])

    def test_redirect_to_a_name_that_resolves_private_is_refused(self):
        wire = self.exercise(
            [response(b"", "301 Moved", ["Location: https://interno.example/x"])],
            answers={"interno.example": "10.0.0.7"},
        )
        with self.assertRaisesRegex(ProviderError, "Destino de rede"):
            web_text.get_text("https://pagina.example/curto", 1024)
        self.assertEqual([(PUBLIC_IP, 443)], wire["connections"])
        self.assertEqual(["pagina.example", "interno.example"], wire["lookups"])

    def test_a_public_redirect_is_followed_and_revalidated_without_cookies(self):
        wire = self.exercise(
            [
                response(b"", "302 Found", ["Location: /final", "Set-Cookie: sessao=abc"]),
                response(b"<html>final</html>"),
            ]
        )
        final, text = web_text.get_text("https://pagina.example/curto", 1024)
        self.assertEqual(("https://pagina.example/final", "<html>final</html>"), (final, text))
        self.assertEqual(["pagina.example", "pagina.example"], wire["lookups"])
        self.assertNotIn(b"Cookie:", wire["requests"][1])

    def test_a_redirect_to_plain_http_is_refused(self):
        self.exercise([response(b"", "302 Found", ["Location: http://pagina.example/final"])])
        with self.assertRaises(ProviderError):
            web_text.get_text("https://pagina.example/curto", 1024)

    def test_a_redirect_outside_the_allowlist_is_refused(self):
        wire = self.exercise([response(b"", "302 Found", ["Location: https://outro.example/player.js"])])
        with self.assertRaisesRegex(ProviderError, "fora"):
            web_text.get_text(PLAYER_JS, 1024, allowed_hosts=(PLAYER_HOST,))
        self.assertEqual(1, len(wire["connections"]))

    def test_too_many_redirects_are_refused(self):
        hop = response(b"", "302 Found", ["Location: /de-novo"])
        self.exercise([hop] * 10)
        with self.assertRaisesRegex(ProviderError, "redirecionamentos"):
            web_text.get_text("https://pagina.example/curto", 1024)

    def test_an_http_error_names_the_status(self):
        self.exercise([response(b"nao", "404 Not Found")])
        with self.assertRaisesRegex(ProviderError, "HTTP 404"):
            web_text.get_text("https://pagina.example/sumiu", 1024)

    def test_an_oversized_page_is_bounded_through_resolve(self):
        self.exercise([b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + b"x" * 4096])
        with (
            patch.object(page_video, "PAGE_MAX_BYTES", 1024),
            self.assertRaisesRegex(ProviderError, "Fonte de URL não suportada"),
        ):
            providers.resolve("https://pagina.example/grande")


class RefererReachesYtDlpTests(unittest.TestCase):
    def test_download_segment_passes_the_referer_before_the_url(self):
        seen = []

        def run(arguments, timeout=180, *, op=None):
            seen.append(arguments)
            Path(arguments[arguments.index("-o") + 1]).write_bytes(b"x")
            return "", []

        get_text, _ = fake_get_text({HLS: MEDIA_PLAYLIST})
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(socket, "getaddrinfo", side_effect=public_dns),
            patch.object(web_text, "get_text", side_effect=get_text),
            patch.object(social, "run", side_effect=run),
            patch("getbrolls.media.run", side_effect=fake_media_run),
        ):
            social.download_segment(HLS, Path(tmp) / "clip.mp4", 0, 2, referer=REFERER)
            social.download_segment(HLS, Path(tmp) / "outro.mp4", 0, 2)
        self.assertEqual(2, len(seen))
        with_referer, without = seen[0], seen[1]
        self.assertEqual(["--referer", REFERER], with_referer[with_referer.index("--referer") :][:2])
        self.assertLess(with_referer.index("--referer"), with_referer.index("--"))
        self.assertEqual(HLS, with_referer[-1])
        self.assertNotIn("--referer", without)

    def test_a_referer_that_is_not_public_is_refused(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(socket, "getaddrinfo", side_effect=public_dns),
            patch.object(social, "run", side_effect=AssertionError("yt-dlp não roda")),
            self.assertRaises(ProviderError),
        ):
            social.download_segment(HLS, Path(tmp) / "clip.mp4", 0, 2, referer="https://127.0.0.1/x")

    def test_probe_remote_passes_the_referer(self):
        seen = []

        def run(arguments, timeout=180, *, op=None):
            seen.append(arguments)
            return "{}", []

        get_text, _ = fake_get_text({HLS: MEDIA_PLAYLIST})
        with (
            patch.object(socket, "getaddrinfo", side_effect=public_dns),
            patch.object(web_text, "get_text", side_effect=get_text),
            patch.object(social, "run", side_effect=run),
        ):
            social.probe_remote(HLS, referer=REFERER)
        self.assertTrue(all("--referer" in args for args in seen))

    def test_preview_of_an_embedded_player_candidate_sends_the_page_as_referer(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg required")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "synthetic.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=160x90:rate=10:duration=2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ],
                check=True,
            )
            get_text, _ = fake_get_text(EMBEDDED_PLAYER_PAGES)
            seen = []

            def download(url, target, start, end, referer=None):
                seen.append((url, referer))
                shutil.copyfile(source, target)
                return target

            with (
                patch.object(socket, "getaddrinfo", side_effect=public_dns),
                patch.object(web_text, "get_text", side_effect=get_text),
                patch.object(social, "download_segment", side_effect=download),
            ):
                c = main(["resolve", "--url", PAGE, "--project", str(root)])
                self.assertEqual(f"embedded_player:{VIDEO}", c["id"])
                main(["preview", "--candidate", c["id"], "--start", "0", "--end", "2", "--project", str(root)])
            self.assertEqual([(HLS, REFERER)], seen)


class InspectReadsTheHlsWithTheRefererTests(unittest.TestCase):
    def inspect(self, argv_tail, root):
        get_text, _ = fake_get_text(EMBEDDED_PLAYER_PAGES)
        seen = []

        def probe_remote(url, langs=None, cache=None, referer=None):
            seen.append((url, referer))
            return {"duration_s": 30.0, "chapters": [], "subtitles": {}, "subtitle_langs": [], "description": ""}

        with (
            patch.object(socket, "getaddrinfo", side_effect=public_dns),
            patch.object(web_text, "get_text", side_effect=get_text),
            patch.object(social, "probe_remote", side_effect=probe_remote),
        ):
            main(["inspect", *argv_tail, "--query", "oferta", "--project", str(root)])
        return seen

    def test_inspect_by_page_url_reads_the_hls_not_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([(HLS, REFERER)], self.inspect(["--url", PAGE], Path(tmp)))

    def test_inspect_by_candidate_keeps_the_referer(self):
        with tempfile.TemporaryDirectory() as tmp:
            get_text, _ = fake_get_text(EMBEDDED_PLAYER_PAGES)
            with (
                patch.object(socket, "getaddrinfo", side_effect=public_dns),
                patch.object(web_text, "get_text", side_effect=get_text),
            ):
                c = main(["resolve", "--url", PAGE, "--project", tmp])
            self.assertEqual([(HLS, REFERER)], self.inspect(["--candidate", c["id"]], Path(tmp)))

    def test_inspect_of_a_direct_link_has_no_referer(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "https://videos.example/aula.m3u8"
            self.assertEqual([(url, None)], self.inspect(["--url", url], Path(tmp)))


class QueueDoesNotOpenPagesTests(unittest.TestCase):
    def test_queue_add_refuses_an_unknown_page_without_reading_it(self):
        with (
            patch.object(web_text, "get_text", side_effect=AssertionError("a fila não abre página")),
            self.assertRaisesRegex(ValueError, "Fonte de URL não suportada"),
        ):
            queue.add(queue.empty_state(), "instagram", [PAGE])

    def test_resolve_without_reading_pages_still_takes_a_direct_link(self):
        with (
            patch.object(web_text, "get_text", side_effect=AssertionError("sem leitura de página")),
            patch.object(socket, "getaddrinfo", side_effect=public_dns),
        ):
            self.assertEqual(
                "direct_stream", providers.resolve("https://videos.example/a.mp4", read_pages=False)["provider"]
            )


class PluginPrecedenceTests(PluginTestCase):
    def test_a_plugin_host_still_wins_over_the_direct_route(self):
        self.enable()
        with (
            patch.object(web_text, "get_text", side_effect=AssertionError("página não deve ser aberta")),
            patch.object(socket, "getaddrinfo", side_effect=AssertionError("sem DNS para host de plugin")),
        ):
            item = providers.resolve("https://demo.example/v/1.mp4")
        self.assertEqual("demo:1", item["id"])
        self.assertEqual("demo", item["provider"])

    def test_a_plugin_named_after_the_route_still_does_not_take_unknown_hosts(self):
        self.enable()
        with patch.object(socket, "getaddrinfo", side_effect=public_dns):
            item = providers.resolve("https://videos.example/a.mp4")
        self.assertEqual("direct_stream", item["provider"])


STREAM = "https://videos.example/curso/main.m3u8"
MASTER_PLAYLIST = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="pt",URI="audio/index.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
https://videos.example/curso/360/index.m3u8
"""
MP4_HEAD = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"


def dns_by_host(private_hosts):
    """DNS sintético: os hosts listados respondem com IP privado, os outros com IP público."""

    def resolve(host, *_args, **_kwargs):
        ip = "10.0.0.9" if host in private_hosts else PUBLIC_IP
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))]

    return resolve


class StreamCheckTests(unittest.TestCase):
    """O que o yt-dlp/ffmpeg vai buscar sozinho (variante, segmento, chave, map) é conferido antes."""

    def setUp(self):
        self.dns = self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))
        self.enterContext(patch.object(web_text, "peek", side_effect=AssertionError("HLS não é espiado como MP4")))

    def vet_with(self, pages, url=STREAM):
        get_text, calls = fake_get_text(pages)
        with patch.object(web_text, "get_text", side_effect=get_text):
            return stream_check.vet(url), calls

    def refusal_with(self, pages, url=STREAM):
        get_text, calls = fake_get_text(pages)
        with patch.object(web_text, "get_text", side_effect=get_text), self.assertRaises(ProviderError) as caught:
            stream_check.vet(url)
        return str(caught.exception), calls

    def test_a_media_playlist_with_public_uris_passes(self):
        final, calls = self.vet_with({STREAM: MEDIA_PLAYLIST})
        self.assertEqual(STREAM, final)
        self.assertEqual([STREAM], [c["url"] for c in calls])
        self.assertEqual(stream_check.PLAYLIST_MAX_BYTES, calls[0]["max_bytes"])

    def test_a_master_reads_one_level_of_variants_and_renditions(self):
        variants = {
            "https://videos.example/curso/720/index.m3u8": MEDIA_PLAYLIST,
            "https://videos.example/curso/audio/index.m3u8": MEDIA_PLAYLIST,
            "https://videos.example/curso/360/index.m3u8": MEDIA_PLAYLIST,
        }
        final, calls = self.vet_with({STREAM: MASTER_PLAYLIST, **variants})
        self.assertEqual(STREAM, final)
        self.assertEqual([STREAM, *variants], [c["url"] for c in calls])

    def test_the_final_url_after_a_redirect_is_what_yt_dlp_gets(self):
        moved = "https://outra-cdn.example/v/main.m3u8"
        final, _ = self.vet_with({STREAM: (moved, MEDIA_PLAYLIST)})
        self.assertEqual(moved, final)

    def test_variants_resolve_against_the_final_url(self):
        moved = "https://outra-cdn.example/v/main.m3u8"
        pages = {
            STREAM: (moved, MASTER_PLAYLIST),
            "https://outra-cdn.example/v/720/index.m3u8": MEDIA_PLAYLIST,
            "https://outra-cdn.example/v/audio/index.m3u8": MEDIA_PLAYLIST,
            "https://videos.example/curso/360/index.m3u8": MEDIA_PLAYLIST,
        }
        final, calls = self.vet_with(pages)
        self.assertEqual(moved, final)
        self.assertEqual(4, len(calls))

    def test_any_uri_off_public_https_443_is_refused(self):
        for line in (
            "http://127.0.0.1:18082/internal/admin/action.ts",
            "https://127.0.0.1/seg.ts",
            "https://10.0.0.5/seg.ts",
            "https://169.254.169.254/latest/meta-data",
            "https://[::1]/seg.ts",
            "https://localhost/seg.ts",
            "https://videos.example:8443/seg.ts",
            "https://user:pw@videos.example/seg.ts",
            "http://videos.example/seg.ts",
            "file:///etc/passwd",
            "data:video/mp2t;base64,AAAA",
            "https://0177.0.0.1/seg.ts",
        ):
            with self.subTest(line=line):
                body = MEDIA_PLAYLIST.replace("seg0.ts", line)
                message, _ = self.refusal_with({STREAM: body})
                self.assertIn("rede", message)

    def test_key_map_and_rendition_uris_are_checked_too(self):
        for old, new in (
            ('URI="init.mp4"', 'URI="http://127.0.0.1/init.mp4"'),
            ('URI="https://chaves.example/k1"', 'URI="https://192.168.0.1/k1"'),
            ('URI="init.mp4"', "URI=http://127.0.0.1/sem-aspas.mp4"),
        ):
            with self.subTest(new=new):
                message, _ = self.refusal_with({STREAM: MEDIA_PLAYLIST.replace(old, new)})
                self.assertIn("rede", message)
        master = MASTER_PLAYLIST.replace('URI="audio/index.m3u8"', 'URI="http://10.0.0.1/audio.m3u8"')
        message, calls = self.refusal_with({STREAM: master})
        self.assertIn("rede", message)
        self.assertEqual([STREAM], [c["url"] for c in calls])

    def test_a_segment_host_that_resolves_private_is_refused(self):
        self.dns.side_effect = dns_by_host({"cdn2.videos.example"})
        message, _ = self.refusal_with({STREAM: MEDIA_PLAYLIST})
        self.assertIn("rede", message)

    def test_a_hostile_variant_is_refused(self):
        hostile = MEDIA_PLAYLIST.replace("seg0.ts", "http://127.0.0.1:18082/internal/admin/action.ts")
        pages = {
            STREAM: MASTER_PLAYLIST,
            "https://videos.example/curso/720/index.m3u8": MEDIA_PLAYLIST,
            "https://videos.example/curso/audio/index.m3u8": hostile,
            "https://videos.example/curso/360/index.m3u8": MEDIA_PLAYLIST,
        }
        message, _ = self.refusal_with(pages)
        self.assertIn("rede", message)

    def test_a_variant_that_is_itself_a_master_is_refused(self):
        pages = {
            STREAM: MASTER_PLAYLIST,
            "https://videos.example/curso/720/index.m3u8": MASTER_PLAYLIST,
            "https://videos.example/curso/audio/index.m3u8": MEDIA_PLAYLIST,
            "https://videos.example/curso/360/index.m3u8": MEDIA_PLAYLIST,
        }
        message, _ = self.refusal_with(pages)
        self.assertIn("variante", message)

    def test_too_many_variants_or_hosts_are_refused_without_reading_them(self):
        lines = ["#EXTM3U"]
        for n in range(stream_check.VARIANT_LIMIT + 1):
            lines += ["#EXT-X-STREAM-INF:BANDWIDTH=1", f"v{n}/index.m3u8"]
        message, calls = self.refusal_with({STREAM: "\n".join(lines)})
        self.assertIn("variantes", message)
        self.assertEqual(1, len(calls))
        segments = ["#EXTM3U"] + [f"#EXTINF:1,\nhttps://h{n}.example/s.ts" for n in range(stream_check.HOST_LIMIT + 1)]
        message, _ = self.refusal_with({STREAM: "\n".join(segments)})
        self.assertIn("hosts", message)

    def test_a_body_that_is_not_a_playlist_is_refused(self):
        message, _ = self.refusal_with({STREAM: "<html><iframe src='http://127.0.0.1/'></iframe></html>"})
        self.assertIn("HLS", message)

    def test_only_cdm_keyformats_are_refused_as_drm(self):
        for key in (
            '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://chave",KEYFORMAT="com.apple.streamingkeydelivery"',
            '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://chave"',
            f'#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES-CTR,URI="data:text/plain;base64,AAAA",KEYFORMAT="{WIDEVINE}"',
            '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="https://chaves.example/p",KEYFORMAT="com.microsoft.playready"',
            f'#EXT-X-KEY:METHOD=SAMPLE-AES,URI="https://chaves.example/p",KEYFORMAT="{PLAYREADY}"',
        ):
            with self.subTest(key=key):
                body = MEDIA_PLAYLIST.replace("#EXT-X-TARGETDURATION:6", key + "\n#EXT-X-TARGETDURATION:6")
                message, _ = self.refusal_with({STREAM: body})
                self.assertIn("DRM", message)
                self.assertIn("não contorna", message)

    def test_aes_128_and_keys_without_a_cdm_format_are_attempted(self):
        for key in (
            '#EXT-X-KEY:METHOD=AES-128,URI="https://chaves.example/k2"',
            '#EXT-X-KEY:METHOD=AES-128,URI="https://chaves.example/k2",KEYFORMAT="identity"',
            '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="https://chaves.example/k3"',
            '#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="https://chaves.example/w"',
        ):
            with self.subTest(key=key):
                body = MEDIA_PLAYLIST.replace("#EXT-X-TARGETDURATION:6", key + "\n#EXT-X-TARGETDURATION:6")
                final, _ = self.vet_with({STREAM: body})
                self.assertEqual(STREAM, final)

    def test_an_unreadable_playlist_says_what_to_do(self):
        def unreadable(url, max_bytes, allowed_hosts=None, *, encoding=None):
            raise ProviderError("A página respondeu HTTP 403")

        with patch.object(web_text, "get_text", side_effect=unreadable), self.assertRaises(ProviderError) as caught:
            stream_check.vet(STREAM)
        self.assertIn("HTTP 403", str(caught.exception))
        self.assertIn("resolve --file", str(caught.exception))

    def test_the_refusal_log_names_only_the_host(self):
        body = MEDIA_PLAYLIST.replace("seg0.ts", "http://127.0.0.1:18082/internal/admin/action.ts")
        get_text, _ = fake_get_text({STREAM: body})
        with (
            patch.object(web_text, "get_text", side_effect=get_text),
            self.assertLogs("getbrolls.stream_check", level="WARNING") as cm,
            self.assertRaises(ProviderError),
        ):
            stream_check.vet(STREAM)
        joined = "\n".join(cm.output)
        self.assertIn("event=stream_check", joined)
        self.assertIn("host=videos.example", joined)
        self.assertIn("reason=uri_not_public", joined)
        for secret in ("/curso", "internal", "18082", "action.ts"):
            self.assertNotIn(secret, joined)


class Mp4CheckTests(unittest.TestCase):
    """O `.mp4` é espiado: só segue o que é MP4 de fato, depois dos redirecionamentos conferidos."""

    URL = "https://videos.example/a.mp4"

    def setUp(self):
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))
        self.enterContext(patch.object(web_text, "get_text", side_effect=AssertionError("MP4 não é lido como texto")))

    def test_a_real_mp4_passes_with_the_final_url(self):
        moved = "https://cdn.videos.example/a.mp4"
        with patch.object(web_text, "peek", return_value=(moved, "video/mp4", MP4_HEAD)) as peek:
            self.assertEqual(moved, stream_check.vet(self.URL))
        peek.assert_called_once_with(self.URL, stream_check.PEEK_BYTES)

    def test_an_octet_stream_mp4_passes(self):
        with patch.object(web_text, "peek", return_value=(self.URL, "application/octet-stream", MP4_HEAD)):
            self.assertEqual(self.URL, stream_check.vet(self.URL))

    def test_something_else_behind_an_mp4_name_is_refused(self):
        for content_type, head in (
            ("text/html", b"<html><iframe src='http://127.0.0.1/'>"),
            ("application/vnd.apple.mpegurl", b"#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1/s.ts"),
            ("video/mp4", b"#EXTM3U\n"),
            ("application/octet-stream", b"<?xml version='1.0'?><MPD>"),
        ):
            with (
                self.subTest(content_type=content_type),
                patch.object(web_text, "peek", return_value=(self.URL, content_type, head)),
                self.assertRaisesRegex(ProviderError, "MP4"),
            ):
                stream_check.vet(self.URL)

    def test_an_unreachable_mp4_says_what_to_do(self):
        with (
            patch.object(web_text, "peek", side_effect=ProviderError(http.PRIVATE_DESTINATION)),
            self.assertRaisesRegex(ProviderError, "resolve --file"),
        ):
            stream_check.vet(self.URL)


class YtDlpGetsOnlyVettedStreamsTests(unittest.TestCase):
    def run_download(self, url, pages):
        seen = []

        def run(arguments, timeout=180, *, op=None):
            seen.append(arguments)
            Path(arguments[arguments.index("-o") + 1]).write_bytes(b"x")
            return "", []

        get_text, _ = fake_get_text(pages)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(socket, "getaddrinfo", side_effect=public_dns),
            patch.object(web_text, "get_text", side_effect=get_text),
            patch.object(social, "run", side_effect=run),
            patch("getbrolls.media.run", side_effect=fake_media_run),
        ):
            social.download_segment(url, Path(tmp) / "clip.mp4", 0, 2)
        return seen

    def test_a_hostile_playlist_never_reaches_yt_dlp(self):
        body = MEDIA_PLAYLIST.replace("seg0.ts", "http://127.0.0.1:18082/internal/admin/action.ts")
        for call in (
            lambda: self.run_download(STREAM, {STREAM: body}),
            lambda: social.probe_remote(STREAM),
        ):
            get_text, _ = fake_get_text({STREAM: body})
            with (
                self.subTest(call=call),
                patch.object(socket, "getaddrinfo", side_effect=public_dns),
                patch.object(web_text, "get_text", side_effect=get_text),
                patch.object(social, "run", side_effect=AssertionError("yt-dlp não roda")),
                self.assertRaisesRegex(ProviderError, "rede"),
            ):
                call()

    def test_yt_dlp_gets_the_vetted_final_url(self):
        moved = "https://outra-cdn.example/v/main.m3u8"
        seen = self.run_download(STREAM, {STREAM: (moved, MEDIA_PLAYLIST)})
        self.assertEqual(moved, seen[0][-1])

    def test_a_youtube_link_is_not_vetted_as_a_stream(self):
        url = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
        with patch.object(stream_check, "vet", side_effect=AssertionError("YouTube não passa pelo stream_check")):
            seen = self.run_download(url, {})
        self.assertEqual(url, seen[0][-1])


class PageReaderHardeningTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))

    def resolve_with(self, pages):
        get_text, _ = fake_get_text(pages)
        with patch.object(web_text, "get_text", side_effect=get_text):
            return providers.resolve(PAGE)

    def test_a_hostile_page_at_the_size_cap_is_read_in_bounded_time(self):
        filler = f"<{PLAYER_TAG} " * (page_video.PAGE_MAX_BYTES // len(f"<{PLAYER_TAG} "))
        html = f'<script src="{PLAYER_JS}"></script>' + filler[: page_video.PAGE_MAX_BYTES - 200]
        started = time.monotonic()
        item = self.resolve_with({**EMBEDDED_PLAYER_PAGES, PAGE: html})
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(HLS, item["source_url"])

    def test_the_element_id_still_picks_the_matching_player(self):
        other = "cccccccccccccccccccccccc"
        other_js = PLAYER_JS.replace(PLAYER, other)
        html = (
            f'<script src="{other_js}"></script><script src="{PLAYER_JS}"></script>'
            f'<{PLAYER_TAG} class="x" data-a="1" id="player-{PLAYER}"></{PLAYER_TAG}>'
        )
        get_text, calls = fake_get_text({**EMBEDDED_PLAYER_PAGES, PAGE: html})
        with patch.object(web_text, "get_text", side_effect=get_text):
            providers.resolve(PAGE)
        self.assertEqual(PLAYER_JS, calls[1]["url"])

    def test_drm_flag_spellings_only_reach_the_log(self):
        for flag in ('"drm":"true"', "drm:1", 'drm:{widevine:"x"}', '"drm": {"fairplay":1}', "isDrm:true", '"drm":"1"'):
            with self.subTest(flag=flag):
                body = PLAYER_BODY.replace('"drm":false', flag)
                get_text, _ = fake_get_text({**EMBEDDED_PLAYER_PAGES, PLAYER_JS: body})
                with (
                    patch.object(web_text, "get_text", side_effect=get_text),
                    self.assertLogs("getbrolls.page_video", level="INFO") as cm,
                ):
                    self.assertEqual(HLS, providers.resolve(PAGE)["source_url"])
                self.assertIn("drm_flag=true", "\n".join(cm.output).lower())

    def test_drm_off_spellings_are_not_drm(self):
        for flag in ('"drm":false', "drm:!1", "drm:0", "drm:{}", "drm:null", '"drm":"false"', "drmReady:true"):
            with self.subTest(flag=flag):
                body = PLAYER_BODY.replace('"drm":false', flag)
                self.assertEqual(HLS, self.resolve_with({**EMBEDDED_PLAYER_PAGES, PLAYER_JS: body})["source_url"])


class NumericHostTests(unittest.TestCase):
    def test_non_canonical_numeric_hosts_are_refused(self):
        for host in ("0177.0.0.1", "0x7f.0.0.1", "2130706433", "127.1", "0x7f000001", "127.0.0.1.", "93.184.216.034"):
            url = f"https://{host}/a.mp4"
            with self.subTest(host=host):
                self.assertIsNone(http.public_url(url))
                with (
                    patch.object(socket, "getaddrinfo", side_effect=public_dns),
                    self.assertRaises(ProviderError),
                ):
                    providers.resolve(url)
                with self.assertRaises(ProviderError):
                    http.public_destination(url)

    def test_canonical_ips_and_names_with_numbers_still_pass(self):
        for url in (
            f"https://{PUBLIC_IP}/a.mp4",
            "https://0x1.videos.example/a.mp4",
            "https://123.videos.example/a.mp4",
            "https://videos.example./a.mp4",
        ):
            with self.subTest(url=url):
                self.assertEqual(url, http.public_url(url))


class GetTextDeadlineAndPeekTests(unittest.TestCase):
    exercise = GetTextTransportTests.exercise

    def test_a_slow_body_past_the_total_deadline_is_refused(self):
        wire = self.exercise([response(b"x" * (3 * web_text.TEXT_CHUNK))])
        with patch.object(web_text, "TEXT_DEADLINE_S", 0), self.assertRaisesRegex(ProviderError, "prazo"):
            web_text.get_text("https://pagina.example/lenta", 4 * web_text.TEXT_CHUNK)
        self.assertEqual([socket.SHUT_RDWR], wire["shutdowns"])

    def test_a_body_in_many_chunks_is_read_whole(self):
        body = b"y" * (3 * web_text.TEXT_CHUNK + 7)
        self.exercise([response(body)])
        _, text = web_text.get_text("https://pagina.example/longa", 4 * web_text.TEXT_CHUNK)
        self.assertEqual(len(body), len(text))

    def test_peek_reads_only_the_head_after_revalidated_redirects(self):
        wire = self.exercise(
            [
                response(b"", "302 Found", ["Location: https://cdn.videos.example/a.mp4"]),
                response(MP4_HEAD + b"z" * 4096, "206 Partial Content", ["Content-Type: video/mp4"]),
            ]
        )
        final, content_type, head = web_text.peek("https://videos.example/a.mp4", 64)
        self.assertEqual("https://cdn.videos.example/a.mp4", final)
        self.assertEqual("video/mp4", content_type)
        self.assertEqual(MP4_HEAD + b"z" * (64 - len(MP4_HEAD)), head)
        self.assertIn(b"Range: bytes=0-63", wire["requests"][1])
        self.assertEqual(["videos.example", "cdn.videos.example"], wire["lookups"])

    def test_peek_refuses_a_redirect_to_a_private_name(self):
        wire = self.exercise(
            [response(b"", "302 Found", ["Location: https://interno.example/a.mp4"])],
            answers={"interno.example": "10.0.0.7"},
        )
        with self.assertRaisesRegex(ProviderError, "Destino de rede"):
            web_text.peek("https://videos.example/a.mp4", 64)
        self.assertEqual(1, len(wire["connections"]))


if __name__ == "__main__":
    unittest.main()
