"""Pino de caracterização de `providers._pick_largest`, o helper extraído de
`_pexels_rows` e `_pixabay_rows`. O caso do Pexels (preferir 1080p a um arquivo 4K) já
tinha cobertura em `test_remote_providers.py`; este arquivo cobre o mesmo comportamento
do lado do Pixabay, que não tinha teste sobre o teto de 1920px.
"""

import os
import unittest
from unittest.mock import patch

from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import providers


class PixabayPicksLargestFittingVariantTests(unittest.TestCase):
    @patch.dict(os.environ, {"PIXABAY_API_KEY": "fixture-key"})
    @patch.object(providers, "get_json")
    def test_pixabay_prefers_full_hd_over_4k(self, get):
        get.return_value = {
            "hits": [
                {
                    "id": 1,
                    "tags": "fixture",
                    "pageURL": "https://pixabay.com/videos/fixture-1/",
                    "user": "Fixture",
                    "duration": 9,
                    "videos": {
                        "large": {
                            "url": "https://cdn.pixabay.com/4k.mp4",
                            "width": 3840,
                            "height": 2160,
                            "thumbnail": "https://cdn.pixabay.com/4k-thumb.jpg",
                        },
                        "medium": {
                            "url": "https://cdn.pixabay.com/hd.mp4",
                            "width": 1920,
                            "height": 1080,
                            "thumbnail": "https://cdn.pixabay.com/hd-thumb.jpg",
                        },
                    },
                }
            ]
        }
        item = providers.search("pixabay", "laboratory", 1)[0]
        self.assertEqual(item["media_url"], "https://cdn.pixabay.com/hd.mp4")
        self.assertEqual(item["media"]["height"], 1080)

    @patch.dict(os.environ, {"PIXABAY_API_KEY": "fixture-key"})
    @patch.object(providers, "get_json")
    def test_pixabay_falls_back_to_the_largest_when_nothing_fits(self, get):
        get.return_value = {
            "hits": [
                {
                    "id": 2,
                    "tags": "fixture",
                    "pageURL": "https://pixabay.com/videos/fixture-2/",
                    "user": "Fixture",
                    "duration": 9,
                    "videos": {
                        "large": {
                            "url": "https://cdn.pixabay.com/8k.mp4",
                            "width": 7680,
                            "height": 4320,
                            "thumbnail": "https://cdn.pixabay.com/8k-thumb.jpg",
                        },
                        "medium": {
                            "url": "https://cdn.pixabay.com/4k.mp4",
                            "width": 3840,
                            "height": 2160,
                            "thumbnail": "https://cdn.pixabay.com/4k-thumb.jpg",
                        },
                    },
                }
            ]
        }
        item = providers.search("pixabay", "laboratory", 1)[0]
        self.assertEqual(item["media_url"], "https://cdn.pixabay.com/8k.mp4")
        self.assertEqual(item["media"]["height"], 4320)


class PickLargestUnitTests(unittest.TestCase):
    def test_returns_empty_dict_for_no_variants(self):
        self.assertEqual({}, providers._pick_largest([]))

    def test_default_cap_is_1920(self):
        variants = [
            {"width": 1920, "height": 1080},
            {"width": 3840, "height": 2160},
        ]
        self.assertEqual({"width": 1920, "height": 1080}, providers._pick_largest(variants))

    def test_custom_cap_is_honoured(self):
        variants = [
            {"width": 1280, "height": 720},
            {"width": 1920, "height": 1080},
        ]
        self.assertEqual({"width": 1280, "height": 720}, providers._pick_largest(variants, cap=1280))


if __name__ == "__main__":
    unittest.main()
