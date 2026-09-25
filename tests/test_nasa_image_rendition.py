"""Foto da NASA sai na versão original, não na cópia de 1280 px.

M-5: `search --provider nasa --media image` → `fetch` entregava `~medium.jpg`
(1280x1018) mesmo com `~orig.jpg` publicado no mesmo item. O vídeo continua como
estava: `~medium.mp4` primeiro, que é o arquivo de trabalho leve da fonte.
"""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import providers

IMAGE_BASE = "https://images-assets.nasa.gov/image/S69-1/S69-1"
VIDEO_BASE = "https://images-assets.nasa.gov/video/KSC-1/KSC-1"


def assets(*hrefs):
    return {"collection": {"items": [{"href": href} for href in hrefs]}}


class NasaImageRendition(unittest.TestCase):
    def _urls(self, payload, suffixes):
        with patch.object(providers, "get_json", return_value=payload):
            return providers._nasa_asset_urls("S69-1", suffixes)

    def test_an_image_prefers_the_original_over_medium(self):
        payload = assets(
            IMAGE_BASE + "~thumb.jpg",
            IMAGE_BASE + "~medium.jpg",
            IMAGE_BASE + "~small.jpg",
            IMAGE_BASE + "~orig.jpg",
            IMAGE_BASE + "~large.jpg",
            IMAGE_BASE + "~metadata.json",
        )
        urls = self._urls(payload, providers.NASA_IMAGE_SUFFIXES)
        self.assertEqual(
            [IMAGE_BASE + tag + ".jpg" for tag in ("~orig", "~large", "~medium", "~small", "~thumb")],
            urls,
        )

    def test_without_an_original_the_largest_rendition_wins(self):
        payload = assets(IMAGE_BASE + "~small.jpg", IMAGE_BASE + "~medium.jpg", IMAGE_BASE + "~large.jpg")
        self.assertEqual(IMAGE_BASE + "~large.jpg", self._urls(payload, providers.NASA_IMAGE_SUFFIXES)[0])

    def test_refresh_used_by_fetch_hands_out_the_original_image(self):
        payload = assets(IMAGE_BASE + "~medium.jpg", IMAGE_BASE + "~orig.jpg")
        item = {"provider": "nasa", "source_id": "S69-1", "media": {"kind": "image"}}
        with patch.object(providers, "get_json", return_value=payload):
            self.assertEqual(IMAGE_BASE + "~orig.jpg", providers.refresh(item)["media_url"])

    def test_video_keeps_the_medium_working_file_first(self):
        payload = assets(VIDEO_BASE + "~orig.mp4", VIDEO_BASE + "~medium.mp4", VIDEO_BASE + "~small.mp4")
        self.assertEqual(VIDEO_BASE + "~medium.mp4", self._urls(payload, (".mp4",))[0])

    def test_a_tiff_original_loses_to_every_jpg(self):
        payload = assets(
            IMAGE_BASE + "~orig.tif",
            IMAGE_BASE + "~large.jpg",
            IMAGE_BASE + "~small.jpg",
        )
        self.assertEqual(
            [IMAGE_BASE + "~large.jpg", IMAGE_BASE + "~small.jpg", IMAGE_BASE + "~orig.tif"],
            self._urls(payload, providers.NASA_IMAGE_SUFFIXES),
        )

    def test_refresh_lists_the_smaller_renditions_as_fallbacks(self):
        payload = assets(IMAGE_BASE + "~medium.jpg", IMAGE_BASE + "~orig.jpg", IMAGE_BASE + "~large.jpg")
        item = {"provider": "nasa", "source_id": "S69-1", "media": {"kind": "image"}}
        with patch.object(providers, "get_json", return_value=payload):
            fresh = providers.refresh(item)
        self.assertEqual([IMAGE_BASE + "~large.jpg", IMAGE_BASE + "~medium.jpg"], fresh["media_url_fallbacks"])


class _Sized(io.BytesIO):
    """Resposta HTTPS mínima com o `Content-Length` que o teste escolher."""

    def __init__(self, payload, length):
        super().__init__(payload)
        self.headers = {"Content-Length": str(length)}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class SizeCapFallsBackToASmallerRendition(unittest.TestCase):
    """I-2: a original acima do teto de download não derruba a coleta; a próxima versão menor vale."""

    def test_fetch_uses_the_next_rendition_when_the_original_is_over_the_cap(self):
        from getbrolls import http

        body = b"\xff\xd8\xff\xe0" + b"0" * 64
        asked = []

        class Opener:
            def open(self, request, timeout=None):
                asked.append(request.full_url)
                huge = request.full_url.endswith("~orig.jpg")
                return _Sized(b"" if huge else body, 600 * 1024 * 1024 if huge else len(body))

        fresh = {
            "media_url": IMAGE_BASE + "~orig.jpg",
            "media_url_fallbacks": [IMAGE_BASE + "~large.jpg", IMAGE_BASE + "~medium.jpg"],
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(http, "_opener", Opener):
            target = Path(tmp) / "out.part"
            used = http.download_rendition(fresh, target)
            self.assertEqual(body, target.read_bytes())
        self.assertEqual(IMAGE_BASE + "~large.jpg", used)
        self.assertEqual([IMAGE_BASE + "~orig.jpg", IMAGE_BASE + "~large.jpg"], asked)

    def test_without_fallbacks_the_cap_error_is_kept(self):
        from getbrolls import http

        class Opener:
            def open(self, request, timeout=None):
                return _Sized(b"", 600 * 1024 * 1024)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(http, "_opener", Opener),
            self.assertRaises(http.ProviderError) as caught,
        ):
            http.download_rendition({"media_url": VIDEO_BASE + "~medium.mp4"}, Path(tmp) / "x.part")
        self.assertIn("excede limite", str(caught.exception))

    def test_fetch_and_preview_go_through_the_fallback_download(self):
        import inspect as source_code

        from getbrolls import acquisition, commands

        self.assertIn("download_rendition(", source_code.getsource(acquisition.prepare_source))
        self.assertIn("download_rendition(", source_code.getsource(commands))


if __name__ == "__main__":
    unittest.main()
