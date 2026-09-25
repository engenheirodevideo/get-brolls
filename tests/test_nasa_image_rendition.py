"""Foto da NASA sai na versão original, não na cópia de 1280 px.

M-5: `search --provider nasa --media image` → `fetch` entregava `~medium.jpg`
(1280x1018) mesmo com `~orig.jpg` publicado no mesmo item. O vídeo continua como
estava: `~medium.mp4` primeiro, que é o arquivo de trabalho leve da fonte.
"""

import unittest
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


if __name__ == "__main__":
    unittest.main()
