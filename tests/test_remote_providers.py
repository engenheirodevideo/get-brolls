"""Synthetic offline API fixtures; no secrets and no network calls."""

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import http, providers


class ProvidersTests(unittest.TestCase):
    def test_social_links_downloadable_and_tracking_removed(self):
        for url in (
            "https://www.youtube.com/watch?v=abcdefghijk&utm_source=test",
            "https://instagram.com/reel/ABC123/?igsh=hello",
            "https://www.tiktok.com/@bruno/video/123456789",
        ):
            item = providers.resolve(url)
            self.assertEqual(item["state"], "candidate")
            self.assertEqual(item["acquisition"]["status"], "available")
            self.assertNotIn("utm_source", item["source_url"])
            self.assertNotIn("igsh=", item["source_url"])

    def test_reject_lookalikes_bad_ids_private_and_credentials(self):
        for url in (
            "https://youtube.com.evil.test/watch?v=abcdefghijk",
            "https://youtube.com/watch?v=bad",
            "https://127.0.0.1/",
            "https://instagram.com/reel/ABC/?access_token=secret",
            "https://user:pass@youtube.com/watch?v=abcdefghijk",
        ):
            with self.assertRaises(ValueError):
                providers.resolve(url)

    @patch.dict(os.environ, {"PEXELS_API_KEY": "fixture-key"})
    @patch.object(providers, "get_json")
    def test_pexels_prefers_full_hd_and_keeps_unknown_rights(self, get):
        get.return_value = {
            "videos": [
                {
                    "id": 1,
                    "url": "https://www.pexels.com/video/a-1/",
                    "duration": 12,
                    "image": "https://images.pexels.com/a.jpg",
                    "user": {"name": "Fixture"},
                    "video_files": [
                        {
                            "file_type": "video/mp4",
                            "width": 3840,
                            "height": 2160,
                            "link": "https://videos.pexels.com/4k.mp4",
                        },
                        {
                            "file_type": "video/mp4",
                            "width": 1920,
                            "height": 1080,
                            "link": "https://videos.pexels.com/hd.mp4",
                        },
                    ],
                }
            ]
        }
        item = providers.search("pexels", "laboratory", 1)[0]
        self.assertEqual(item["media_url"], "https://videos.pexels.com/hd.mp4")
        self.assertEqual(item["media"]["height"], 1080)
        self.assertEqual(item["rights"]["status"], "unknown")
        self.assertEqual(item["match"]["kind"], "illustrative")
        self.assertEqual(get.call_args.args[0], "https://api.pexels.com/v1/videos/search")
        self.assertNotIn("fixture-key", json.dumps(item))

    @patch.dict(os.environ, {"PIXABAY_API_KEY": "fixture-key"})
    @patch.object(providers, "get_json", return_value={"hits": []})
    def test_pixabay_requests_day_cache(self, get):
        self.assertEqual(providers.search("pixabay", "laboratory", 1), [])
        self.assertEqual(get.call_args.kwargs["cache_ttl"], 86400)

    @patch.object(providers, "get_json")
    def test_commons_honours_the_media_filter_and_strips_author_html(self, get):
        get.return_value = {
            "query": {
                "pages": {
                    "1": {
                        "pageid": 1,
                        "title": "File:Fixture.webm",
                        "imageinfo": [
                            {
                                "mime": "video/webm",
                                "url": "https://upload.wikimedia.org/fixture.webm",
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Fixture.webm",
                                "extmetadata": {
                                    "Artist": {"value": '<a href="x">Name</a>'},
                                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                },
                            }
                        ],
                    },
                    "2": {
                        "pageid": 2,
                        "title": "Image",
                        "imageinfo": [{"mime": "image/jpeg"}],
                    },
                }
            }
        }
        rows = providers.search("commons", "science", 3, media="video")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["creator"]["name"], "Name")
        self.assertIsNone(rows[0]["rights"]["license_url"])
        # Sem filtro, a foto do acervo também é candidato legítimo: era essa a rota
        # que faltava para um beat de imagem estática.
        self.assertEqual(2, len(providers.search("commons", "science", 3)))

    @patch.object(providers, "get_json")
    def test_nasa_enriches_asset_and_preserves_third_party_creator(self, get):
        get.side_effect = [
            {
                "collection": {
                    "items": [
                        {
                            "data": [
                                {
                                    "nasa_id": "fixture",
                                    "title": "Space",
                                    "media_type": "video",
                                    "secondary_creator": "Third Party",
                                }
                            ]
                        }
                    ]
                }
            },
            {"collection": {"items": [{"href": "https://images-assets.nasa.gov/fixture~medium.mp4"}]}},
        ]
        item = providers.search("nasa", "space", 1)[0]
        self.assertEqual(item["creator"]["name"], "Third Party")
        self.assertEqual(item["rights"]["status"], "unknown")
        self.assertTrue(item["media_url"].endswith(".mp4"))

    @patch.object(providers, "get_json")
    def test_a_nasa_details_page_resolves_as_an_image_candidate(self, get):
        """`search --provider nasa` existia e a página do item era recusada: sem rota."""
        get.side_effect = [
            {
                "collection": {
                    "items": [
                        {
                            "data": [
                                {
                                    "nasa_id": "as11-40-5903",
                                    "title": "Apollo 11",
                                    "media_type": "image",
                                    "center": "JSC",
                                }
                            ],
                            "links": [{"rel": "preview", "href": "https://images-assets.nasa.gov/a~thumb.jpg"}],
                        }
                    ]
                }
            },
            {
                "collection": {
                    "items": [
                        {"href": "https://images-assets.nasa.gov/as11-40-5903~orig.jpg"},
                        {"href": "https://images-assets.nasa.gov/metadata.json"},
                    ]
                }
            },
        ]
        item = providers.resolve("https://images.nasa.gov/details/as11-40-5903")
        self.assertEqual("nasa", item["provider"])
        self.assertEqual("image", item["media"]["kind"])
        self.assertEqual("image", item["asset_type"])
        self.assertEqual("https", item["acquisition"]["method"])
        self.assertTrue(item["media_url"].endswith(".jpg"))
        self.assertEqual("JSC", item["creator"]["name"])
        # Nenhuma licença é inventada: continua "unknown" até alguém registrar `permit`.
        self.assertEqual("unknown", item["rights"]["status"])

    @patch.object(providers, "get_json")
    def test_an_unknown_nasa_id_says_so_instead_of_inventing_a_candidate(self, get):
        get.return_value = {"collection": {"items": []}}
        with self.assertRaises(providers.ProviderError) as caught:
            providers.resolve("https://images.nasa.gov/details/nao-existe")
        self.assertIn("nao-existe", str(caught.exception))

    def test_a_nasa_url_without_an_item_id_is_refused_with_the_shape_to_use(self):
        with self.assertRaises(providers.ProviderError) as caught:
            providers.resolve("https://images.nasa.gov/search?q=apollo")
        self.assertIn("images.nasa.gov/details/", str(caught.exception))

    def test_an_unsupported_url_points_at_the_bank_search(self):
        with self.assertRaises(providers.ProviderError) as caught:
            providers.resolve("https://example.org/algum-video")
        self.assertIn("search --provider", str(caught.exception))

    @patch.object(providers, "get_json")
    def test_refreshing_a_nasa_image_asks_for_the_image_not_an_mp4(self, get):
        item = providers.candidate("nasa", "as11", "Apollo", "https://images.nasa.gov/details/as11")
        item["media"]["kind"] = "image"
        get.return_value = {"collection": {"items": [{"href": "https://images-assets.nasa.gov/as11~orig.jpg"}]}}
        self.assertTrue(providers.refresh(item)["media_url"].endswith(".jpg"))

    @patch.dict(os.environ, {"PEXELS_API_KEY": "fixture-key"})
    @patch.object(providers, "get_json")
    def test_refresh_keeps_approval_and_segment(self, get):
        original = providers.candidate("pexels", "1", "Fixture", "https://www.pexels.com/video/a-1/")
        original["approval"]["status"] = "approved"
        original["segment"]["start_s"] = 5
        get.return_value = {
            "id": 1,
            "video_files": [
                {
                    "file_type": "video/mp4",
                    "width": 1920,
                    "height": 1080,
                    "link": "https://videos.pexels.com/new.mp4",
                }
            ],
        }
        result = providers.refresh(original)
        self.assertEqual(result["approval"], original["approval"])
        self.assertEqual(result["segment"], original["segment"])
        self.assertEqual(result["media_url"], "https://videos.pexels.com/new.mp4")

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_key_and_unsupported_search(self):
        with self.assertRaisesRegex(ValueError, "PEXELS_API_KEY"):
            providers.search("pexels", "science")
        with self.assertRaisesRegex(ValueError, "indisponível"):
            providers.search("instagram", "science")


class HTTPTests(unittest.TestCase):
    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_interrupted_download_cleans_partial_and_can_retry(self, builder, safe):
        response = MagicMock()
        response.headers = {}
        response.read.side_effect = [b"partial", KeyboardInterrupt()]
        builder.return_value.open.return_value.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "clip.part"
            with self.assertRaises(KeyboardInterrupt):
                http.download("https://example.org/video.mp4", target)
            self.assertFalse(target.exists())
            response.read.side_effect = [b"complete", b""]
            http.download("https://example.org/video.mp4", target)
            self.assertEqual(target.read_bytes(), b"complete")

    def test_signed_url_is_not_publishable(self):
        self.assertIsNone(http.public_url("https://cdn.example.org/video.mp4?X-Amz-Signature=secret"))
        self.assertIsNone(http.public_url("https://cdn.example.org/video.mp4?key=secret"))

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_cache_uses_redacted_payload_and_makes_no_second_request(self, builder, safe):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"items": [], "url": "https://example.org/?key=secret", "key": "secret"}'
        )
        builder.return_value.open.return_value = response
        with (
            tempfile.TemporaryDirectory() as cache,
            patch.dict(os.environ, {"GETBROLLS_CACHE_DIR": cache}),
        ):
            first = http.get_json("https://example.org/api", {"key": "secret"}, cache_ttl=86400)
            second = http.get_json("https://example.org/api", {"key": "secret"}, cache_ttl=86400)
            self.assertEqual(first, second)
            self.assertEqual(builder.return_value.open.call_count, 1)
            self.assertNotIn("secret", next(Path(cache).iterdir()).read_text(encoding="utf-8"))

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_auth_failure_not_retried_and_no_secret_in_error(self, builder, safe):
        error_response = urllib.error.HTTPError("https://example.org/?key=secret", 403, "secret", cast("Any", {}), None)
        self.addCleanup(error_response.close)
        builder.return_value.open.side_effect = error_response
        with self.assertRaises(http.ProviderError) as error:
            http.get_json("https://example.org/", {"key": "secret"})
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(builder.return_value.open.call_count, 1)

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_download_stream_limit_removes_partial(self, builder, safe):
        response = MagicMock()
        response.headers = {}
        response.read.side_effect = [b"12345"]
        builder.return_value.open.return_value.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "clip.part"
            with self.assertRaisesRegex(http.ProviderError, "limite"):
                http.download("https://example.org/video.mp4", target, max_bytes=4)
            self.assertFalse(target.exists())

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_download_never_overwrites_existing(self, builder, safe):
        response = MagicMock()
        response.headers = {}
        builder.return_value.open.return_value.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "clip.mp4"
            target.write_bytes(b"original")
            with self.assertRaises(http.ProviderError):
                http.download("https://example.org/video.mp4", target)
            self.assertEqual(target.read_bytes(), b"original")

    @patch.object(http.time, "sleep")
    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_server_failure_bounded_to_three_attempts(self, builder, safe, sleep):
        error_response = urllib.error.HTTPError("https://example.org/", 503, "unavailable", cast("Any", {}), None)
        self.addCleanup(error_response.close)
        builder.return_value.open.side_effect = error_response
        with self.assertRaises(http.ProviderError):
            http.get_json("https://example.org/")
        self.assertEqual(builder.return_value.open.call_count, 3)


COMMONS_FILE = {
    "query": {
        "pages": {
            "123": {
                "pageid": 123,
                "title": "File:Apollo 11 Launch.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/apollo.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Apollo_11_Launch.jpg",
                        "thumburl": "https://upload.wikimedia.org/thumb.jpg",
                        "mime": "image/jpeg",
                        "width": 2000,
                        "height": 1500,
                        "extmetadata": {
                            "Artist": {"value": "<a href='#'>NASA</a>"},
                            "LicenseShortName": {"value": "Public domain"},
                            "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/mark/1.0/"},
                        },
                    }
                ],
            }
        }
    }
}


class CommonsFilePageTests(unittest.TestCase):
    """Quem já tem o link do arquivo no Commons precisa conseguir registrá-lo."""

    @patch.object(providers, "get_json", return_value=COMMONS_FILE)
    def test_a_file_page_becomes_an_image_candidate(self, fetched):
        item = providers.resolve("https://commons.wikimedia.org/wiki/File:Apollo_11_Launch.jpg")
        self.assertEqual("commons", item["provider"])
        self.assertEqual("image", item["media"]["kind"])
        self.assertEqual("image", item.get("asset_type"))
        self.assertEqual("NASA", item["creator"]["name"])
        self.assertEqual("Public domain", item["rights"]["license_name"])
        self.assertEqual("https://upload.wikimedia.org/apollo.jpg", item["media_url"])
        # O underscore da URL vira espaço, como a API espera no `titles`.
        self.assertEqual("File:Apollo 11 Launch.jpg", fetched.call_args[0][1]["titles"])

    @patch.object(providers, "get_json", return_value=COMMONS_FILE)
    def test_a_video_file_keeps_the_video_kind(self, _fetched):
        payload = json.loads(json.dumps(COMMONS_FILE))
        payload["query"]["pages"]["123"]["imageinfo"][0]["mime"] = "video/webm"
        with patch.object(providers, "get_json", return_value=payload):
            item = providers.resolve("https://commons.wikimedia.org/wiki/File:Apollo_11_Launch.webm")
        self.assertEqual("video", item["media"]["kind"])
        self.assertNotIn("asset_type", item)

    @patch.object(providers, "get_json", return_value={"query": {"pages": {"-1": {"missing": ""}}}})
    def test_a_missing_file_says_so_instead_of_registering_nothing(self, _fetched):
        with self.assertRaises(ValueError) as caught:
            providers.resolve("https://commons.wikimedia.org/wiki/File:Nao_existe.jpg")
        self.assertIn("Commons não tem o arquivo", str(caught.exception))

    def test_a_page_that_is_not_a_file_is_refused_with_the_right_shape(self):
        with self.assertRaises(ValueError) as caught:
            providers.resolve("https://commons.wikimedia.org/wiki/Main_Page")
        self.assertIn("wiki/File:", str(caught.exception))

    @patch.object(providers, "get_json", return_value=COMMONS_FILE)
    def test_a_sound_file_is_refused(self, _fetched):
        payload = json.loads(json.dumps(COMMONS_FILE))
        payload["query"]["pages"]["123"]["imageinfo"][0]["mime"] = "audio/ogg"
        with patch.object(providers, "get_json", return_value=payload):
            with self.assertRaises(ValueError) as caught:
                providers.resolve("https://commons.wikimedia.org/wiki/File:Som.ogg")
        self.assertIn("não é vídeo nem imagem", str(caught.exception))


NASA_SEARCH = {
    "collection": {
        "items": [
            {
                "data": [{"nasa_id": "foto-1", "title": "SLS na plataforma", "media_type": "image"}],
                "links": [{"rel": "preview", "href": "https://images-assets.nasa.gov/foto-1/thumb.jpg"}],
            },
            {
                "data": [{"nasa_id": "video-1", "title": "Decolagem", "media_type": "video"}],
                "links": [{"rel": "preview", "href": "https://images-assets.nasa.gov/video-1/thumb.jpg"}],
            },
        ]
    }
}
NASA_ASSETS = {
    "collection": {
        "items": [
            {"href": "https://images-assets.nasa.gov/foto-1/foto-1~orig.jpg"},
            {"href": "https://images-assets.nasa.gov/video-1/video-1~orig.mp4"},
        ]
    }
}


class SearchMediaFlagTests(unittest.TestCase):
    """`--media` abre a rota das fontes que publicam foto e vídeo no mesmo acervo."""

    def fetch(self, media_types):
        def fake(url, params=None, headers=None, cache_ttl=None):
            if "images-api.nasa.gov/search" in url:
                media_types.append((params or {}).get("media_type"))
                return NASA_SEARCH
            return NASA_ASSETS

        return fake

    def test_nasa_defaults_to_both_kinds(self):
        seen = []
        with patch.object(providers, "get_json", side_effect=self.fetch(seen)):
            items = providers.search("nasa", "SLS", 5)
        self.assertEqual(["image,video"], seen)
        self.assertEqual({"image", "video"}, {i["media"]["kind"] for i in items})

    def test_asking_for_images_only_keeps_the_stills(self):
        seen = []
        with patch.object(providers, "get_json", side_effect=self.fetch(seen)):
            items = providers.search("nasa", "SLS", 5, media="image")
        self.assertEqual(["image"], seen)
        self.assertEqual(["image"], [i["media"]["kind"] for i in items])
        # O arquivo escolhido é a foto, não o mp4 do outro item.
        self.assertTrue(items[0]["media_url"].endswith(".jpg"))

    def test_asking_for_video_only_keeps_the_clips(self):
        seen = []
        with patch.object(providers, "get_json", side_effect=self.fetch(seen)):
            items = providers.search("nasa", "SLS", 5, media="video")
        self.assertEqual(["video"], seen)
        self.assertEqual(["video"], [i["media"]["kind"] for i in items])

    def test_commons_asks_the_api_for_the_right_filetype(self):
        seen = {}

        def fake(url, params=None, headers=None, cache_ttl=None):
            seen["gsrsearch"] = (params or {}).get("gsrsearch")
            return {"query": {"pages": {}}}

        for media, expected in (
            ("image", "filetype:bitmap"),
            ("video", "filetype:video"),
            ("any", "filetype:video|bitmap"),
        ):
            with self.subTest(media=media):
                with patch.object(providers, "get_json", side_effect=fake):
                    providers.search("commons", "apollo", 5, media=media)
                self.assertIn(expected, seen["gsrsearch"])

    def test_an_unknown_media_value_is_refused(self):
        with self.assertRaises(ValueError):
            providers.search("nasa", "SLS", 5, media="gif")

    def test_youtube_ignores_the_flag_instead_of_breaking(self):
        with patch.object(providers, "_youtube", return_value=[]) as fake:
            providers.search("youtube", "SLS", 5, media="image")
        self.assertEqual(("SLS", 5), fake.call_args[0])


if __name__ == "__main__":
    unittest.main()
