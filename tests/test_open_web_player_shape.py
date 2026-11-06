"""Player embutido reconhecido pela forma, sem nome de serviço: qualquer host público na 443, um script só.

Elemento personalizado com `player` no nome e/ou script com caminho de pacote de player; o link do vídeo
sai do script e só vale HTTPS público na 443. Tudo sintético e offline (`web_text.get_text` falso, DNS
sintético), com as páginas de `test_open_web_video`.
"""

import random
import socket
import string
import tempfile
import time
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_open_web_video import (
    ACCOUNT,
    CDN_HOST,
    EMBEDDED_PLAYER_PAGES,
    HLS,
    MEDIA_PLAYLIST,
    PAGE,
    PAGE_HTML,
    PLAYER,
    PLAYER_BODY,
    PLAYER_HOST,
    PLAYER_JS,
    PLAYER_TAG,
    VIDEO,
    fake_get_text,
    public_dns,
)

from getbrolls import page_video, providers, web_text
from getbrolls.cli import main
from getbrolls.errors import RouteRefusal
from getbrolls.http import ProviderError


class PlayerShapeTests(unittest.TestCase):
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

    def test_any_public_host_serves_the_player_and_the_video_without_a_name_list(self):
        # A forma decide, não o nome: outro host de script e outro host de mídia, ambos públicos na 443.
        script = f"https://js.outro.example/{ACCOUNT}/players/{PLAYER}/v4/player.js"
        media = f"https://midia.outro.example/{ACCOUNT}/{VIDEO}/main.m3u8"
        html = PAGE_HTML.replace(PLAYER_JS, script)
        body = PLAYER_BODY.replace(HLS.replace("/", "\\/"), media.replace("/", "\\/"))
        item, calls = self.resolve_with({PAGE: html, script: body, media: MEDIA_PLAYLIST})
        self.assertEqual(media, item["source_url"])
        self.assertEqual([PAGE, script, media], [c["url"] for c in calls])
        self.assertEqual(("js.outro.example",), calls[1]["allowed_hosts"])

    def test_a_player_script_off_https_443_or_on_a_private_host_is_not_read(self):
        for script in (
            PLAYER_JS.replace("https:", "http:"),
            PLAYER_JS.replace(PLAYER_HOST, PLAYER_HOST + ":8443"),
            PLAYER_JS.replace(PLAYER_HOST, "127.0.0.1"),
            PLAYER_JS.replace(PLAYER_HOST, "10.0.0.5"),
            PLAYER_JS.replace(PLAYER_HOST, "localhost"),
        ):
            with self.subTest(script=script):
                message, calls = self.refusal_with({PAGE: PAGE_HTML.replace(PLAYER_JS, script)})
                self.assertEqual([PAGE], [c["url"] for c in calls])
                self.assertIn(".m3u8", message)

    def test_a_video_link_off_https_443_or_on_a_private_host_is_not_taken(self):
        for media in (
            HLS.replace("https:", "http:"),
            HLS.replace(CDN_HOST, CDN_HOST + ":8443"),
            HLS.replace(CDN_HOST, "192.168.0.10"),
            HLS.replace(CDN_HOST, f"usuario@{CDN_HOST}"),
        ):
            with self.subTest(media=media):
                body = PLAYER_BODY.replace(HLS.replace("/", "\\/"), media.replace("/", "\\/"))
                message, calls = self.refusal_with({**EMBEDDED_PLAYER_PAGES, PLAYER_JS: body})
                self.assertEqual(2, len(calls))
                self.assertIn("player", message)
                self.assertIn(".m3u8", message)

    def test_only_one_player_script_is_read(self):
        second = PLAYER_JS.replace(PLAYER, "cccccccccccccccccccccccc")
        extra = f'<script src="{second}"></script><script src="https://cdn.example/js/embed-player.js"></script>'
        item, calls = self.resolve_with({**EMBEDDED_PLAYER_PAGES, PAGE: PAGE_HTML + extra})
        self.assertEqual(HLS, item["source_url"])
        self.assertEqual([PAGE, PLAYER_JS, HLS], [c["url"] for c in calls])

    def test_a_script_whose_file_name_says_player_is_a_player_without_an_element(self):
        script = "https://cdn.example/assets/js/embed-player.min.js"
        html = f'<html><script src="https://cdn.example/assets/js/app.js"></script><script src="{script}"></script>'
        item, calls = self.resolve_with({PAGE: html, script: PLAYER_BODY, HLS: MEDIA_PLAYLIST})
        self.assertEqual(f"embedded_player:{VIDEO}", item["id"])
        self.assertEqual([PAGE, script, HLS], [c["url"] for c in calls])

    def test_a_page_with_only_ordinary_scripts_is_not_a_player(self):
        html = (
            '<html><script src="https://cdn.example/js/app.js"></script>'
            '<link href="//cdn.example/x/players.css"><player id="aaaaaaaaaaaa"></player>'
        )
        get_text, calls = fake_get_text({PAGE: html})
        with patch.object(web_text, "get_text", side_effect=get_text), self.assertRaises(RouteRefusal) as caught:
            providers.resolve(PAGE)
        self.assertEqual([PAGE], [c["url"] for c in calls])
        self.assertIn("Fonte de URL não suportada", str(caught.exception))
        # `<player>` sem hífen não é elemento personalizado: a página não tem player embutido.
        self.assertEqual("no_video", caught.exception.suggested_route["kind"])

    def test_the_element_id_attribute_picks_the_matching_script(self):
        other_js = PLAYER_JS.replace(PLAYER, "cccccccccccccccccccccccc")
        for element in (
            f'<{PLAYER_TAG} data-player-id="{PLAYER}"></{PLAYER_TAG}>',
            f'<video-player player-id="{PLAYER}"></video-player>',
            f'<{PLAYER_TAG} data-id="x-{PLAYER}"></{PLAYER_TAG}>',
        ):
            with self.subTest(element=element):
                html = f'<script src="{other_js}"></script><script src="{PLAYER_JS}"></script>{element}'
                _, calls = self.resolve_with({**EMBEDDED_PLAYER_PAGES, PAGE: html})
                self.assertEqual(PLAYER_JS, calls[1]["url"])

    def test_an_mp4_link_comes_after_the_hls_one(self):
        mp4 = f"https://{CDN_HOST}/{ACCOUNT}/{VIDEO}/previa.mp4"
        script = '{"poster":"' + mp4 + '","hls":"' + HLS + '"}'
        self.assertEqual(
            [(HLS, VIDEO), (mp4, VIDEO)],
            page_video._media_list(script),  # pylint: disable=protected-access
        )


class ScriptSourceTests(unittest.TestCase):
    """Só o que a página carrega como script conta: `<script src>` ou o `.src = "…"` de um carregador."""

    def test_a_script_tag_and_a_loader_assignment_are_candidates(self):
        for html in (
            f'<script async src="{PLAYER_JS}"></script>',
            f"<script SRC='{PLAYER_JS}' defer></script>",
            PAGE_HTML,
            f'<script>var s=document.createElement("script");s.src = "{PLAYER_JS[len("https:") :]}";</script>',
        ):
            with self.subTest(html=html[:60]):
                player = page_video._pick_player(html)[1]  # pylint: disable=protected-access
                self.assertIsNotNone(player)
                self.assertEqual(PLAYER_JS, player[0] if player else None)

    def test_a_player_script_only_cited_in_text_is_not_read(self):
        for html in (
            f"<p>Veja {PLAYER_JS}</p>",
            f'<!-- {PLAYER_JS} --><script>var c={{"bundle":"{PLAYER_JS}"}};</script>',
            f'<a href="{PLAYER_JS}">baixar</a><img src="{PLAYER_JS}">',
            f'<script data-src="{PLAYER_JS}"></script>',
        ):
            with self.subTest(html=html[:60]):
                self.assertIsNone(page_video._pick_player(html)[1])  # pylint: disable=protected-access

    def test_a_script_src_off_https_or_off_443_is_not_a_candidate(self):
        for url in (
            PLAYER_JS.replace("https:", "http:"),
            PLAYER_JS.replace(PLAYER_HOST, PLAYER_HOST + ":8443"),
            PLAYER_JS.replace(PLAYER_HOST, "127.0.0.1"),
        ):
            with self.subTest(url=url):
                html = f'<script src="{url}"></script>'
                self.assertIsNone(page_video._pick_player(html)[1])  # pylint: disable=protected-access


GUESSED = "EMBEDDED_PLAYER_GUESSED"
NAME_ONLY_JS = "https://cdn.example/assets/js/embed-player.min.js"


class GuessedPlayerTests(unittest.TestCase):
    """Script achado só pelo nome do arquivo, sem elemento nem `/players/<id>/`: o link pode ser de exemplo."""

    def setUp(self):
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=public_dns))

    def warnings_of(self, pages):
        get_text, calls = fake_get_text(pages)
        with tempfile.TemporaryDirectory() as tmp, patch.object(web_text, "get_text", side_effect=get_text):
            result = main(["resolve", "--url", PAGE, "--project", tmp])
        return {w["code"]: w["message"] for w in result.get("warnings", [])}, calls

    def test_a_file_name_match_alone_warns_that_the_video_may_not_be_the_page_one(self):
        html = f'<html><script src="{NAME_ONLY_JS}"></script></html>'
        warnings, _ = self.warnings_of({PAGE: html, NAME_ONLY_JS: PLAYER_BODY, HLS: MEDIA_PLAYLIST})
        self.assertIn(GUESSED, warnings)
        self.assertIn("nome do arquivo", warnings[GUESSED])
        self.assertIn("prévia", warnings[GUESSED])

    def test_an_element_or_a_players_folder_is_not_a_guess(self):
        element_only = f'<{PLAYER_TAG} id="player-{PLAYER}"></{PLAYER_TAG}><script src="{NAME_ONLY_JS}"></script>'
        for pages in (
            EMBEDDED_PLAYER_PAGES,
            {PAGE: element_only, NAME_ONLY_JS: PLAYER_BODY, HLS: MEDIA_PLAYLIST},
            {PAGE: f'<script src="{PLAYER_JS}"></script>', PLAYER_JS: PLAYER_BODY, HLS: MEDIA_PLAYLIST},
        ):
            with self.subTest(page=pages[PAGE][:60]):
                warnings, _ = self.warnings_of(pages)
                self.assertNotIn(GUESSED, warnings)

    def test_a_players_folder_script_wins_over_an_earlier_file_name_match(self):
        html = f'<script src="{NAME_ONLY_JS}"></script><script src="{PLAYER_JS}"></script>'
        warnings, calls = self.warnings_of({PAGE: html, PLAYER_JS: PLAYER_BODY, HLS: MEDIA_PLAYLIST})
        self.assertEqual([PAGE, PLAYER_JS, HLS], [c["url"] for c in calls])
        self.assertNotIn(GUESSED, warnings)


if __name__ == "__main__":
    unittest.main()


class ElementIdScanTests(unittest.TestCase):
    """Os ids do elemento do player: na ordem, sem repetir, com custo linear no número de ids."""

    def test_ids_come_in_order_without_repeats(self):
        html = (
            f'<{PLAYER_TAG} id="player-{PLAYER}" data-player-id="{PLAYER}"></{PLAYER_TAG}>'
            f'<{PLAYER_TAG} data-id="x-{PLAYER}"></{PLAYER_TAG}>'
        )
        self.assertEqual((True, [f"player-{PLAYER}", PLAYER, "x-" + PLAYER]), page_video._element_ids(html))  # pylint: disable=protected-access

    def test_a_page_full_of_distinct_ids_is_scanned_in_bounded_time(self):
        # 64 elementos, 8 atributos cada, cada valor com 48 pedaços sorteados (semente fixa): ~14 mil ids diferentes.
        letters = random.Random(7)
        tags = []
        for _ in range(page_video.ELEMENT_LIMIT):
            values = ("-".join(letters.choices(string.ascii_letters, k=48)) for _ in range(8))
            attributes = " ".join(f'data-{name}-id="{value}"' for name, value in enumerate(values))
            tags.append(f"<{PLAYER_TAG} {attributes}></{PLAYER_TAG}>")
        html = "".join(tags)
        started = time.monotonic()
        element, ids = page_video._element_ids(html)  # pylint: disable=protected-access
        elapsed = time.monotonic() - started
        self.assertTrue(element)
        self.assertGreater(len(ids), 10_000)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLess(elapsed, 0.15)
