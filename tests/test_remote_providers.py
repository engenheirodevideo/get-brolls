"""Synthetic offline API fixtures; no secrets and no network calls."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls import providers
from getbrolls import http


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
        self.assertEqual(
            get.call_args.args[0], "https://api.pexels.com/v1/videos/search"
        )
        self.assertNotIn("fixture-key", json.dumps(item))

    @patch.dict(os.environ, {"PIXABAY_API_KEY": "fixture-key"})
    @patch.object(providers, "get_json", return_value={"hits": []})
    def test_pixabay_requests_day_cache(self, get):
        self.assertEqual(providers.search("pixabay", "laboratory", 1), [])
        self.assertEqual(get.call_args.kwargs["cache_ttl"], 86400)

    @patch.object(providers, "get_json")
    def test_commons_filters_images_and_strips_author_html(self, get):
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
        rows = providers.search("commons", "science", 3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["creator"]["name"], "Name")
        self.assertIsNone(rows[0]["rights"]["license_url"])

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
            {
                "collection": {
                    "items": [
                        {"href": "https://images-assets.nasa.gov/fixture~medium.mp4"}
                    ]
                }
            },
        ]
        item = providers.search("nasa", "space", 1)[0]
        self.assertEqual(item["creator"]["name"], "Third Party")
        self.assertEqual(item["rights"]["status"], "unknown")
        self.assertTrue(item["media_url"].endswith(".mp4"))

    @patch.dict(os.environ, {"PEXELS_API_KEY": "fixture-key"})
    @patch.object(providers, "get_json")
    def test_refresh_keeps_approval_and_segment(self, get):
        original = providers.candidate(
            "pexels", "1", "Fixture", "https://www.pexels.com/video/a-1/"
        )
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
        self.assertIsNone(
            http.public_url("https://cdn.example.org/video.mp4?X-Amz-Signature=secret")
        )
        self.assertIsNone(
            http.public_url("https://cdn.example.org/video.mp4?key=secret")
        )

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_cache_uses_redacted_payload_and_makes_no_second_request(
        self, builder, safe
    ):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"items": [], "url": "https://example.org/?key=secret", "key": "secret"}'
        )
        builder.return_value.open.return_value = response
        with (
            tempfile.TemporaryDirectory() as cache,
            patch.dict(os.environ, {"GETBROLLS_CACHE_DIR": cache}),
        ):
            first = http.get_json(
                "https://example.org/api", {"key": "secret"}, cache_ttl=86400
            )
            second = http.get_json(
                "https://example.org/api", {"key": "secret"}, cache_ttl=86400
            )
            self.assertEqual(first, second)
            self.assertEqual(builder.return_value.open.call_count, 1)
            self.assertNotIn("secret", next(Path(cache).iterdir()).read_text())

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_auth_failure_not_retried_and_no_secret_in_error(self, builder, safe):
        error_response = urllib.error.HTTPError(
            "https://example.org/?key=secret", 403, "secret", {}, None
        )
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
        error_response = urllib.error.HTTPError(
            "https://example.org/", 503, "unavailable", {}, None
        )
        self.addCleanup(error_response.close)
        builder.return_value.open.side_effect = error_response
        with self.assertRaises(http.ProviderError):
            http.get_json("https://example.org/")
        self.assertEqual(builder.return_value.open.call_count, 3)


if __name__ == "__main__":
    unittest.main()
