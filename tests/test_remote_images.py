"""Imagem estática remota (NASA e Commons) do começo ao fim, sem rede.

A busca por `--media image` nas duas fontes devolvia candidatos que o resto do
fluxo não sabia tratar: `preview` sem intervalo morria em `TypeError`, a imagem do
Commons não passava do `refresh` (só aceitava `video/*`), a coleta saía com
extensão `.part` em `clips/` e em `entrega/`, e o `status` mandava rodar `inspect`
num item que não tem duração nenhuma para descobrir. Aqui a API e o download são
dublados; o arquivo servido é uma imagem de verdade gerada pelo FFmpeg.
"""

import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _media import skip_unless_ffmpeg, synth_image
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import cli, http, providers

NASA_ID = "as11-40-5903"
NASA_MEDIA = f"https://images-assets.nasa.gov/image/{NASA_ID}/{NASA_ID}~medium.jpg"
NASA_THUMB = f"https://images-assets.nasa.gov/image/{NASA_ID}/{NASA_ID}~thumb.jpg"
COMMONS_MEDIA = "https://upload.wikimedia.org/wikipedia/commons/a/ab/FullMoon2010.jpg"
COMMONS_THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/FullMoon2010.jpg/320px-FullMoon2010.jpg"


def _nasa_search():
    return {
        "collection": {
            "items": [
                {
                    "data": [{"nasa_id": NASA_ID, "title": "Aldrin", "media_type": "image", "center": "JSC"}],
                    "links": [{"rel": "preview", "href": NASA_THUMB}],
                }
            ]
        }
    }


def _nasa_asset():
    return {"collection": {"items": [{"href": NASA_MEDIA}, {"href": NASA_MEDIA.replace("~medium", "~thumb")}]}}


def _commons_info(url=COMMONS_MEDIA, mime="image/jpeg"):
    return {
        "url": url,
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:FullMoon2010.jpg",
        "thumburl": COMMONS_THUMB,
        "mime": mime,
        "width": 64,
        "height": 48,
        "extmetadata": {
            "Artist": {"value": "Gregory H. Revera"},
            "LicenseShortName": {"value": "CC BY-SA 3.0"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/3.0"},
        },
    }


def _commons(url=COMMONS_MEDIA, mime="image/jpeg"):
    return {
        "query": {
            "pages": {
                "11901243": {
                    "pageid": 11901243,
                    "title": "File:FullMoon2010.jpg",
                    "imageinfo": [_commons_info(url, mime)],
                }
            }
        }
    }


def fake_api(url, params=None, headers=None, cache_ttl=0):
    if url.startswith("https://images-api.nasa.gov/search"):
        return _nasa_search()
    if url.startswith("https://images-api.nasa.gov/asset/"):
        return _nasa_asset()
    if url.startswith("https://commons.wikimedia.org/w/api.php"):
        return _commons()
    raise AssertionError(f"pedido inesperado à API: {url}")


class _Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def opener_for(files):
    """`http._opener` dublado: cada URL devolve os bytes do arquivo que ela nomeia."""

    class _Opener:
        def open(self, request, timeout=None):
            url = request.full_url
            if url not in files:
                raise AssertionError(f"download inesperado: {url}")
            return _Response(files[url])

    return _Opener


@skip_unless_ffmpeg
class RemoteImageFlowBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        synth_image(root / "photo.jpg", color="blue", size="64x48")
        synth_image(root / "photo.png", color="green", size="64x48")
        cls.jpeg = (root / "photo.jpg").read_bytes()
        cls.png = (root / "photo.png").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-remote-image-"))
        self.addCleanup(shutil.rmtree, self.project, True)
        self.files = {
            NASA_MEDIA: self.jpeg,
            NASA_THUMB: self.jpeg,
            COMMONS_MEDIA: self.jpeg,
            COMMONS_THUMB: self.jpeg,
        }
        api = patch.object(providers, "get_json", side_effect=fake_api)
        api.start()
        self.addCleanup(api.stop)
        net = patch.object(http, "_opener", opener_for(self.files))
        net.start()
        self.addCleanup(net.stop)

    def gb(self, *args):
        return cli.main([args[0], "--project", str(self.project), *args[1:]])

    def found(self, provider):
        items = self.gb("search", "--provider", provider, "--query", "moon", "--media", "image", "--limit", "1")[
            "items"
        ]
        self.assertEqual(1, len(items), items)
        self.assertEqual("image", items[0]["media"]["kind"])
        return items[0]

    def approve_and_permit(self, cid, preset):
        self.gb("approve", "--candidate", cid, "--by", "Pessoa Revisora", "--channel", "chat", "--statement", "aprovo")
        self.gb("permit", "--candidate", cid, "--preset", preset)


class CommonsImageRefreshTests(unittest.TestCase):
    """BUG-02: o `refresh` do Commons só aceitava `video/*`, e a foto nunca era coletada."""

    def _item(self, kind):
        item = providers.candidate("commons", "11901243", "File:FullMoon2010.jpg", "https://commons.wikimedia.org/x")
        item["media"]["kind"] = kind
        return item

    def test_an_image_candidate_refreshes_to_its_image_file(self):
        with patch.object(providers, "get_json", return_value=_commons()):
            fresh = providers.refresh(self._item("image"))
        self.assertEqual(COMMONS_MEDIA, fresh["media_url"])

    def test_a_video_candidate_still_refuses_an_image_file(self):
        with (
            patch.object(providers, "get_json", return_value=_commons()),
            self.assertRaises(providers.ProviderError),
        ):
            providers.refresh(self._item("video"))

    def test_an_image_candidate_refuses_a_file_that_became_a_video(self):
        with (
            patch.object(providers, "get_json", return_value=_commons(mime="video/webm")),
            self.assertRaises(providers.ProviderError),
        ):
            providers.refresh(self._item("image"))


class CommonsImageFetchTests(RemoteImageFlowBase):
    def test_a_commons_image_is_collected_after_approval_and_permit(self):
        item = self.found("commons")
        self.gb("preview", "--candidate", item["id"], "--reference-only")
        self.approve_and_permit(item["id"], "commons")
        fetched = self.gb("fetch", "--candidate", item["id"])
        self.assertTrue(fetched["output"]["verified"])
        self.assertEqual(self.jpeg, (self.project / "brolls" / fetched["output"]["path"]).read_bytes())


class RemoteImagePreviewTests(RemoteImageFlowBase):
    """BUG-01: `preview` de imagem remota sem intervalo morria em `TypeError`."""

    def assert_static_preview(self, provider, preset):
        item = self.found(provider)
        shown = self.gb("preview", "--candidate", item["id"])
        self.assertEqual("awaiting_approval", shown["state"])
        poster = shown["files"]["poster"]
        self.assertTrue(poster and Path(poster).is_file(), shown["files"])
        # A mídia de trabalho é a própria foto, no cache privado, com a extensão dela.
        self.assertTrue(shown["local_path"].endswith(".jpg"), shown["local_path"])
        self.assertEqual(64, shown["media"]["width"])
        # Aprovar o que foi visto e coletar entrega exatamente esses bytes.
        self.approve_and_permit(item["id"], preset)
        fetched = self.gb("fetch", "--candidate", item["id"])
        self.assertTrue(fetched["output"]["path"].endswith(".jpg"), fetched["output"])
        self.assertEqual(self.jpeg, (self.project / "brolls" / fetched["output"]["path"]).read_bytes())

    def test_a_nasa_image_gets_a_static_preview_without_a_range(self):
        self.assert_static_preview("nasa", "nasa")

    def test_a_commons_image_gets_a_static_preview_without_a_range(self):
        self.assert_static_preview("commons", "commons")

    def test_a_range_on_a_remote_image_is_still_refused(self):
        item = self.found("nasa")
        with self.assertRaises(Exception) as caught:
            self.gb("preview", "--candidate", item["id"], "--start", "0", "--end", "1")
        self.assertIn("Imagem estática não precisa de intervalo", str(caught.exception))


class RemoteImageExtensionTests(RemoteImageFlowBase):
    """BUG-03: a foto coletada sem prévia local saía como `.part` em `clips/` e `entrega/`."""

    def collect_reference_only(self, provider="nasa"):
        item = self.found(provider)
        self.gb("preview", "--candidate", item["id"], "--reference-only")
        self.approve_and_permit(item["id"], provider)
        return self.gb("fetch", "--candidate", item["id"])

    def test_the_collected_photo_and_its_delivery_keep_the_real_extension(self):
        fetched = self.collect_reference_only()
        rel = fetched["output"]["path"]
        self.assertTrue(rel.endswith(".jpg"), rel)
        self.assertEqual(self.jpeg, (self.project / "brolls" / rel).read_bytes())
        # O arquivo temporário do download não fica para trás em `previews/`.
        self.assertEqual([], list((self.project / "brolls" / "previews").glob("*.part")))
        self.gb("verify")
        self.gb("deliver")
        delivered = [p.name for p in (self.project / "entrega").rglob("*") if p.is_file() and p.suffix != ".md"]
        self.assertTrue(any(name.endswith(".jpg") for name in delivered), delivered)
        self.assertFalse(any(name.endswith(".part") for name in delivered), delivered)

    def test_the_extension_follows_the_content_not_the_url(self):
        # A URL diz `.jpg`, mas o servidor entregou PNG: o arquivo é o que ele é.
        self.files[NASA_MEDIA] = self.png
        fetched = self.collect_reference_only()
        self.assertTrue(fetched["output"]["path"].endswith(".png"), fetched["output"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
