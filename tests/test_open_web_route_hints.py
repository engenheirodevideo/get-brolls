"""Rota aberta: o `suggested_route` da recusa, o vídeo alternativo do
player embutido, o `--referer` e a auditoria.

O `resolve` que não tem rota diz ao agente o próximo comando (link direto achado na página, player
embutido que o getbrolls resolve) ou por que não há (DRM de CDM), com dicas em pt-BR. Também aqui:
a playlist lida só em `\\r\\n`/`\\r`/`\\n`, as linhas do downloader nativo do yt-dlp,
porta inválida, charset hostil e IPv6 que embute IPv4. Tudo offline: `web_text.get_text`
falso e DNS sintético.
"""

import json
import pickle
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_open_web_video import (
    EMBEDDED_PLAYER_PAGES,
    HLS,
    MEDIA_PLAYLIST,
    PAGE,
    PAGE_HTML,
    PLAYER,
    PLAYER_BODY,
    PLAYER_JS,
    PLAYER_TAG,
    REFERER,
    STREAM,
    GetTextTransportTests,
    fake_get_text,
    public_dns,
    response,
)

from getbrolls import _paths, http, providers, queue, route_hint, stream_check, web_text
from getbrolls.cli import main
from getbrolls.errors import RouteRefusal
from getbrolls.http import ProviderError
from getbrolls.runtime import OperationError

DIRECT = "https://cdn.videos.example/aulas/abertura/master.m3u8"
YOUTUBE_ID = "aaaaaaaaaaa"


def page(body):
    return f"<!doctype html><html><body><h1>Oferta</h1>{body}</body></html>"


class _Offline(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))

    def route_of(self, html, url=PAGE):
        get_text, calls = fake_get_text({url: html})
        with patch.object(web_text, "get_text", side_effect=get_text), self.assertRaises(RouteRefusal) as caught:
            providers.resolve(url)
        self.assertEqual(1, len(calls), "só a página é lida")
        return caught.exception.suggested_route

    def cli_failure(self, argv, pages):
        get_text, _ = fake_get_text(pages)
        with patch.object(web_text, "get_text", side_effect=get_text), self.assertRaises(OperationError) as caught:
            main(argv)
        return caught.exception.payload


class SuggestedRouteTests(_Offline):
    def test_a_direct_link_on_the_page_becomes_the_next_command_with_the_page_as_referer(self):
        for html in (
            page(f'<video><source src="{DIRECT}" type="application/x-mpegURL"></video>'),
            page('<script>var c={"src":"' + DIRECT.replace("/", "\\/") + '"};</script>'),
            page(f"<script>load('{DIRECT[len('https:') :]}')</script>"),
        ):
            with self.subTest(html=html[-80:]):
                route = self.route_of(html)
                self.assertEqual("direct_link", route["kind"])
                self.assertEqual(["resolve", "--url", DIRECT, "--referer", REFERER], route["next_command"])
                self.assertTrue(route["reason"])

    def test_plain_http_and_signed_links_are_not_offered(self):
        html = page(
            '<a href="http://cdn.videos.example/a.m3u8">x</a>'
            '<a href="https://cdn.videos.example/b.mp4?token=abc">y</a>'
            '<a href="https://127.0.0.1/c.m3u8">z</a>'
        )
        route = self.route_of(html)
        self.assertEqual("no_video", route["kind"])
        self.assertIsNone(route["next_command"])

    def test_an_embedded_player_that_getbrolls_resolves_becomes_the_next_command(self):
        for src, url in (
            (f"https://www.youtube.com/embed/{YOUTUBE_ID}?rel=0", f"https://www.youtube.com/watch?v={YOUTUBE_ID}"),
            (f"//m.youtube.com/embed/{YOUTUBE_ID}", f"https://www.youtube.com/watch?v={YOUTUBE_ID}"),
            ("https://www.instagram.com/reel/CcccCCCC123/embed/", "https://www.instagram.com/reel/CcccCCCC123/"),
            # O host de embed que o próprio getbrolls grava em `embed_url` é do YouTube (`url_hosts`).
            (
                f"https://www.youtube-nocookie.com/embed/{YOUTUBE_ID}",
                f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
            ),
        ):
            with self.subTest(src=src):
                route = self.route_of(page(f'<iframe width="560" src="{src}" allowfullscreen></iframe>'))
                self.assertEqual("player_embed", route["kind"])
                self.assertEqual(["resolve", "--url", url], route["next_command"])
                self.assertEqual(providers.resolve(url)["source_url"], url)

    def test_a_player_iframe_of_a_host_no_source_serves_gets_a_neutral_hint(self):
        # Nenhum nome de serviço: um host que nenhuma fonte atende (pelos `url_hosts` do registro) só ganha dica.
        for src in (
            "https://player.example/video/123456789",
            "https://embed.cdn.example/e/00000000",
            "//videos.example/iframe/abc",
        ):
            with self.subTest(src=src):
                route = self.route_of(page(f'<iframe src="{src}"></iframe>'))
                self.assertEqual("player_embed", route["kind"])
                self.assertIsNone(route["next_command"])
                self.assertIn("outro site", route["reason"])
                self.assertIn("DevTools", route["alternatives"][0])

    def test_an_iframe_that_does_not_look_like_a_player_is_not_a_route(self):
        route = self.route_of(page('<iframe src="https://mapas.example/lugar?id=1"></iframe>'))
        self.assertEqual("no_video", route["kind"])

    def test_a_source_that_does_not_read_its_embed_url_gets_hints_only(self):
        tiktok = self.route_of(page('<iframe src="https://www.tiktok.com/embed/v2/7000000000000000000"></iframe>'))
        self.assertEqual("player_embed", tiktok["kind"])
        self.assertIsNone(tiktok["next_command"])
        self.assertIn("tiktok", tiktok["reason"])
        self.assertIn("@usuario/video/ID", tiktok["alternatives"][0])

    def test_the_iframe_host_is_looked_up_in_the_registry_not_in_a_list(self):
        # A pergunta vai a `source_for_host` (o registro): um host de plugin vira dica com o nome da fonte, e
        # um subdomínio vale pelo domínio que a fonte declara.
        asked = []

        def source_for_host(host):
            asked.append(host)
            return "fonte_plugin" if host == "videos.plugin.example" else None

        html = page('<iframe src="https://cdn.videos.plugin.example/v/1"></iframe>')
        route = route_hint.for_page(PAGE, html, source_for_host=source_for_host)
        self.assertEqual("player_embed", route["kind"])
        self.assertIsNone(route["next_command"])
        self.assertIn("fonte_plugin", route["reason"])
        self.assertIn("fonte_plugin", route["alternatives"][0])
        self.assertEqual(["cdn.videos.plugin.example", "videos.plugin.example"], asked)
        self.assertEqual("player_embed", route_hint.for_page(PAGE, html)["kind"])
        self.assertIn("outro site", route_hint.for_page(PAGE, html)["reason"])

    def test_an_embedded_player_tag_without_its_script_says_so(self):
        route = self.route_of(page(f'<{PLAYER_TAG} id="player-{PLAYER}"></{PLAYER_TAG}>'))
        self.assertEqual("embedded_player", route["kind"])
        self.assertIn("DevTools", route["alternatives"][0])

    def test_a_page_with_nothing_points_to_devtools_and_search(self):
        route = self.route_of(page("<video src='blob:x'></video>"))
        self.assertEqual("no_video", route["kind"])
        self.assertIsNone(route["next_command"])
        joined = " ".join(route["alternatives"])
        self.assertIn("DevTools > Network", joined)
        self.assertIn("m3u8", joined)
        self.assertIn("search --provider", joined)

    def test_a_embedded_player_without_the_hls_in_its_script_keeps_the_direct_link_first(self):
        # O script do player cita o vídeo só em `http://`, que o getbrolls não lê.
        body = PLAYER_BODY.replace("https:", "http:")
        html = PAGE_HTML + f'<a href="{DIRECT}">baixar</a>'
        get_text, _ = fake_get_text({PAGE: html, PLAYER_JS: body})
        with patch.object(web_text, "get_text", side_effect=get_text), self.assertRaises(RouteRefusal) as caught:
            providers.resolve(PAGE)
        self.assertEqual("direct_link", caught.exception.suggested_route["kind"])

    def test_an_unreadable_page_still_carries_a_route(self):
        with (
            patch.object(web_text, "get_text", side_effect=ProviderError("A página respondeu HTTP 403")),
            self.assertRaises(RouteRefusal) as caught,
        ):
            providers.resolve(PAGE)
        self.assertEqual("page_unreadable", caught.exception.suggested_route["kind"])

    def test_the_queue_refuses_without_reading_and_still_suggests(self):
        with (
            patch.object(web_text, "get_text", side_effect=AssertionError("a fila não abre página")),
            self.assertRaises(RouteRefusal) as caught,
        ):
            queue.add(queue.empty_state(), "instagram", [PAGE])
        self.assertEqual("unknown_host", caught.exception.suggested_route["kind"])

    def test_the_route_survives_pickle(self):
        error = RouteRefusal("x", suggested_route=route_hint.drm())
        again = pickle.loads(pickle.dumps(error))  # noqa: S301 - objeto criado no próprio teste
        self.assertEqual(error.suggested_route, again.suggested_route)
        self.assertEqual("x", str(again))

    def test_a_hostile_page_at_the_size_cap_is_scanned_in_bounded_time(self):
        filler = ("<iframe " + "//a" * 40 + " ") * 8000
        html = (filler + "x" * 2_000_000)[: 2 * 1024 * 1024]
        started = time.monotonic()
        route = route_hint.for_page(PAGE, html)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual("no_video", route["kind"])


class IframeScanTests(_Offline):
    """O `<iframe>` lido pelo `src` dele (nunca pelo `data-src`), com custo linear no número de iframes."""

    def test_the_youtube_embed_host_resolves_as_youtube(self):
        item = providers.resolve(f"https://www.youtube-nocookie.com/embed/{YOUTUBE_ID}")
        self.assertEqual("youtube", item["provider"])
        self.assertEqual(f"https://www.youtube.com/watch?v={YOUTUBE_ID}", item["source_url"])

    def test_a_data_src_is_not_the_iframe_address(self):
        # Carregamento preguiçoso: o `data-src` é só um dado da página; o endereço do iframe é o `src`.
        lazy = f'<iframe data-src="https://player.example/video/1" src="https://www.youtube.com/embed/{YOUTUBE_ID}">'
        route = route_hint.for_page(PAGE, page(lazy), source_for_host=providers._source_for_host)  # pylint: disable=protected-access
        self.assertEqual(["resolve", "--url", f"https://www.youtube.com/watch?v={YOUTUBE_ID}"], route["next_command"])
        only_data = page(f'<iframe data-src="https://www.youtube.com/embed/{YOUTUBE_ID}"></iframe>')
        self.assertEqual([], route_hint.iframe_sources(only_data))
        self.assertEqual("no_video", self.route_of(only_data)["kind"])

    def test_a_host_longer_than_a_dns_name_is_never_looked_up(self):
        asked = []

        def source_for_host(host):
            asked.append(host)

        long_host = ".".join(["a"] * 1000) + ".example"
        route = route_hint.for_page(
            PAGE, page(f'<iframe src="https://{long_host}/player/1"></iframe>'), source_for_host=source_for_host
        )
        self.assertEqual([], asked)
        self.assertEqual("player_embed", route["kind"])
        self.assertIsNone(route["next_command"])

    def test_the_same_iframe_host_is_looked_up_once_per_page(self):
        asked = []

        def source_for_host(host):
            asked.append(host)

        html = page('<iframe src="https://cdn.player.example/video/1"></iframe>' * 50)
        route_hint.for_page(PAGE, html, source_for_host=source_for_host)
        self.assertEqual(["cdn.player.example", "player.example"], asked)

    def test_many_iframes_of_deep_hosts_are_scanned_in_bounded_time(self):
        # 256 iframes, cada um com um host distinto de 1000 rótulos: a busca por sufixo não pode ser quadrática.
        hosts = [".".join([f"{index:x}"] + ["b"] * 1000) for index in range(route_hint.SCAN_LIMIT)]
        html = page("".join(f'<iframe src="https://{host}/player/1"></iframe>' for host in hosts))
        calls = []
        started = time.monotonic()
        route_hint.for_page(PAGE, html, source_for_host=lambda host: calls.append(len(host)))
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual([], [size for size in calls if size > route_hint.HOST_MAX])


class SuggestedLinkShapeTests(_Offline):
    """O link sugerido não leva metacaractere de shell nem porta fora da 443."""

    def test_a_link_with_shell_metacharacters_is_not_offered(self):
        for bad in (
            "https://evil.example/`touch${IFS}pwn`;id&x=1|y.m3u8",
            "https://evil.example/a$(id).m3u8",
            "https://evil.example/a;b.mp4",
            "https://evil.example/a|b.m3u8",
            "https://evil.example/a&b.m3u8",
            "https://evil.example/a!b*c.m3u8",
            "https://evil.example/{a,b}.m3u8",
        ):
            with self.subTest(bad=bad):
                self.assertEqual([], route_hint.direct_links(f'<a href="{bad}">x</a>'))

    def test_a_clean_link_after_a_hostile_one_is_still_offered(self):
        html = page(f'<a href="https://evil.example/a;id.m3u8">x</a><source src="{DIRECT}">')
        self.assertEqual(["resolve", "--url", DIRECT, "--referer", REFERER], self.route_of(html)["next_command"])

    def test_only_port_443_is_offered(self):
        self.assertEqual([], route_hint.direct_links('<a href="https://h.example:8443/e.m3u8">x</a>'))
        self.assertEqual([], route_hint.direct_links('<a href="https://h.example:99999/e.m3u8">x</a>'))
        self.assertEqual(
            ["https://h.example:443/e.m3u8"], route_hint.direct_links('<a href="https://h.example:443/e.m3u8">x</a>')
        )


class SuggestedRouteInTheCliTests(_Offline):
    def test_the_error_json_carries_a_ready_command_and_keeps_the_error_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = page(f'<source src="{DIRECT}">')
            payload = self.cli_failure(["resolve", "--url", PAGE, "--project", tmp], {PAGE: html})
            self.assertEqual("INVALID_DATA", payload["error_code"])
            route = payload["suggested_route"]
            self.assertEqual({"kind", "reason", "next_command", "alternatives"}, set(route))
            prefix = _paths.cli_command()
            command = route["next_command"]
            self.assertEqual(prefix, command[: len(prefix)])
            self.assertEqual(
                ["resolve", "--project", tmp, "--url", DIRECT, "--referer", REFERER], command[len(prefix) :]
            )
            # O comando sugerido roda de verdade e registra o link com a página como referer.
            item = main(command[len(prefix) :])
            self.assertEqual("direct_stream", item["provider"])
            diagnostics = (Path(tmp) / "brolls" / "diagnostics.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(DIRECT, diagnostics)
            self.assertNotIn("suggested_route", diagnostics)
            manifest = json.loads((Path(tmp) / "brolls" / "manifest.json").read_text(encoding="utf-8"))
            saved = next(c for c in manifest["items"] if c["id"] == item["id"])
            self.assertEqual(REFERER, saved["acquisition"]["referer"])

    def test_the_log_never_carries_the_link_found_on_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "brolls").mkdir()
            html = page(f'<source src="{DIRECT}">')
            self.cli_failure(["resolve", "--url", PAGE, "--project", tmp], {PAGE: html})
            joined = "\n".join(
                (Path(tmp) / "brolls" / name).read_text(encoding="utf-8")
                for name in ("getbrolls.log", "diagnostics.jsonl")
            )
            self.assertIn("pagina.example", joined)
            self.assertNotIn("cdn.videos.example", joined)
            self.assertNotIn("/aulas/abertura", joined)
            self.assertNotIn("/oferta", joined)


class RefererFlagTests(_Offline):
    def test_the_referer_goes_with_the_direct_link_without_query(self):
        item = providers.resolve(DIRECT, referer=PAGE)
        self.assertEqual(REFERER, item["acquisition"]["referer"])

    def test_the_referer_is_refused_where_it_does_not_apply(self):
        for url, referer in (
            (f"https://www.youtube.com/watch?v={YOUTUBE_ID}", PAGE),
            (DIRECT, "https://127.0.0.1/pagina"),
            (DIRECT, "http://pagina.example/"),
        ):
            with self.subTest(url=url, referer=referer), self.assertRaises(ProviderError):
                providers.resolve(url, referer=referer)

    def test_the_referer_needs_url_in_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "a.mp4"
            clip.write_bytes(b"x")
            with self.assertRaises(OperationError) as caught:
                main(["resolve", "--file", str(clip), "--referer", PAGE, "--project", tmp])
            self.assertIn("--referer", caught.exception.payload["message"])


class EmbeddedPlayerDecoyTests(_Offline):
    def resolve_cli(self, player_body):
        get_text, _ = fake_get_text({**EMBEDDED_PLAYER_PAGES, PLAYER_JS: player_body})
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_text, "get_text", side_effect=get_text):
            return main(["resolve", "--url", PAGE, "--project", tmp])

    def test_an_anti_download_signal_is_said_in_the_answer(self):
        for flag in ('"antiDownload":true', "anti_download:!0", '"alternativeVideo":"https://cdn.outro.example/x"'):
            with self.subTest(flag=flag):
                body = PLAYER_BODY.replace('"drm":false', '"drm":false,' + flag)
                result = self.resolve_cli(body)
                codes = [w["code"] for w in result.get("warnings", [])]
                self.assertIn("EMBEDDED_PLAYER_DECOY_POSSIBLE", codes)
                message = next(
                    w["message"] for w in result["warnings"] if w["code"] == "EMBEDDED_PLAYER_DECOY_POSSIBLE"
                )
                self.assertIn("vídeo alternativo", message)

    def test_several_renditions_in_one_player_are_no_signal(self):
        # Só a chave do player conta: um player que lista várias rendições não é sinal de vídeo alternativo.
        other = HLS.replace("bbbbbbbbbbbbbbbbbbbbbbbb", "cccccccccccccccccccccccc")
        body = PLAYER_BODY.replace("}};", '},"backup":"' + other + '"};')
        result = self.resolve_cli(body)
        self.assertNotIn("EMBEDDED_PLAYER_DECOY_POSSIBLE", [w["code"] for w in result.get("warnings", [])])

    def test_a_plain_player_has_no_notice(self):
        for body in (PLAYER_BODY, PLAYER_BODY.replace('"drm":false', '"drm":false,"antiDownload":false')):
            with self.subTest(body=body[-60:]):
                result = self.resolve_cli(body)
                self.assertNotIn("EMBEDDED_PLAYER_DECOY_POSSIBLE", [w["code"] for w in result.get("warnings", [])])


class PlaylistParserDifferentialTests(unittest.TestCase):
    """O que o downloader nativo do yt-dlp lê como uma linha só é conferido como uma, ou a playlist é recusada."""

    def setUp(self):
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))

    def vet(self, body):
        get_text, _ = fake_get_text({STREAM: body})
        with patch.object(web_text, "get_text", side_effect=get_text):
            return stream_check.vet(STREAM)

    def assert_refused(self, body):
        with self.assertRaises(ProviderError) as caught:
            self.vet(body)
        self.assertNotIsInstance(caught.exception, RouteRefusal)
        return str(caught.exception)

    def test_unicode_and_control_separators_in_a_segment_are_refused(self):
        for separator in ("\x0b", "\x0c", "\x1c", "\x1e", "\x85", " ", " ", "\x00", "\x7f"):
            with self.subTest(separator=repr(separator)):
                line = f"https://pub.example{separator}@169.254.169.254/latest/meta-data"
                message = self.assert_refused(MEDIA_PLAYLIST.replace("seg0.ts", line))
                self.assertIn("controle", message)

    def test_unicode_and_control_separators_in_a_key_uri_are_refused(self):
        for separator in ("\x0b", "\x85", " "):
            with self.subTest(separator=repr(separator)):
                key = f'URI="https://pub.example{separator}@127.0.0.1:8080/k"'
                self.assert_refused(MEDIA_PLAYLIST.replace('URI="https://chaves.example/k1"', key))

    def test_a_repeated_uri_or_an_unclosed_quote_is_refused(self):
        for old, new in (
            ('URI="https://chaves.example/k1"', 'URI="https://chaves.example/k1",URI="https://127.0.0.1/k"'),
            ('URI="https://chaves.example/k1"', 'URI="https://chaves.example/k1'),
            ('URI="init.mp4"', 'URI="init.mp4",BYTERANGE="1@0",uri=x.mp4'),
            ('URI="init.mp4"', 'URI="init mp4"'),
        ):
            with self.subTest(new=new):
                self.assert_refused(MEDIA_PLAYLIST.replace(old, new))

    def test_crlf_cr_and_tabs_still_read_as_usual(self):
        for body in (
            MEDIA_PLAYLIST.replace("\n", "\r\n"),
            MEDIA_PLAYLIST.replace("\n", "\r"),
            MEDIA_PLAYLIST.replace("seg0.ts", "\tseg0.ts  "),
        ):
            with self.subTest(body=repr(body[:20])):
                self.assertEqual(STREAM, self.vet(body))

    def test_a_cr_split_uri_is_checked_as_two_lines(self):
        # O yt-dlp (`splitlines()`) também quebra no `\r`: a segunda metade é outra linha, conferida por conta própria.
        self.assert_refused(MEDIA_PLAYLIST.replace("seg0.ts", "seg0.ts\rhttps://127.0.0.1/x.ts"))


class InvalidPortTests(unittest.TestCase):
    """Porta que não é número ou passa de 65535 vira recusa com mensagem, não `ValueError` cru."""

    def test_an_invalid_port_is_a_provider_refusal(self):
        for url in ("https://videos.example:99999/a.m3u8", "https://videos.example:abc/a.mp4"):
            with (
                self.subTest(url=url),
                patch.object(socket, "getaddrinfo", side_effect=AssertionError("sem DNS")),
                self.assertRaises(ProviderError),
            ):
                providers.resolve(url)


class HostileCharsetTests(unittest.TestCase):
    """`charset=idna` (que recusa `errors="replace"`) cai para UTF-8 em vez de derrubar a leitura."""

    exercise = GetTextTransportTests.exercise

    def test_a_hostile_charset_falls_back_to_utf8(self):
        for charset in ("idna", "nao-existe"):
            with self.subTest(charset=charset):
                self.exercise([response(b"\xff abc", headers=[f"Content-Type: text/html; charset={charset}"])])
                final, text = web_text.get_text("https://pagina.example/oferta", 1024)
                self.assertEqual("https://pagina.example/oferta", final)
                self.assertIn("abc", text)

    def test_an_odd_codec_never_breaks_the_read(self):
        # `punycode` decodifica sem erro, mas o texto que sai muda entre versões do Python: só não pode levantar.
        self.exercise([response(b"\xff abc", headers=["Content-Type: text/html; charset=punycode"])])
        self.assertIsInstance(web_text.get_text("https://pagina.example/oferta", 1024)[1], str)


class EmbeddedIpv4Tests(unittest.TestCase):
    """IPv6 compatível com IPv4 (`::a.b.c.d`) e SIIT (`::ffff:0:a.b.c.d`) não são destino público."""

    ADDRESSES = ("::7f00:1", "::a9fe:a9fe", "::ffff:0:7f00:1", "::ffff:0:a00:1", "::5db8:d822")

    def test_embedded_ipv4_literals_are_not_public(self):
        for ip in self.ADDRESSES:
            with self.subTest(ip=ip):
                self.assertIsNone(http.public_url(f"https://[{ip}]/a.mp4"))

    def test_a_name_that_resolves_to_them_is_refused(self):
        for ip in self.ADDRESSES:
            answer = [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443, 0, 0))]
            with (
                self.subTest(ip=ip),
                patch.object(socket, "getaddrinfo", return_value=answer),
                self.assertRaisesRegex(ProviderError, "Destino de rede"),
            ):
                http.public_destination("https://videos.example/a.mp4")

    def test_global_ipv6_still_passes(self):
        self.assertIsNotNone(http.public_url("https://[2606:4700:4700::1111]/a.mp4"))


if __name__ == "__main__":
    unittest.main()
