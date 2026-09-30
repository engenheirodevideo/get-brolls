"""Imagem estática remota (NASA e Commons) do começo ao fim, sem rede.

A busca por `--media image` nas duas fontes devolvia candidatos que o resto do
fluxo não sabia tratar: `preview` sem intervalo morria em `TypeError`, a imagem do
Commons não passava do `refresh` (só aceitava `video/*`), a coleta saía com
extensão `.part` em `clips/` e em `entrega/`, e o `status` mandava rodar `inspect`
num item que não tem duração nenhuma para descobrir. Aqui a API e o download são
dublados; o arquivo servido é uma imagem de verdade gerada pelo FFmpeg.
"""

import hashlib
import io
import json
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _media import skip_unless_ffmpeg, synth_image
from _paths import (  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
    ROOT,
    suggested_argv,
)

from getbrolls import cli, http, providers
from getbrolls.commands import _flow_state
from getbrolls.guidance import next_action
from getbrolls.ledger import Ledger
from getbrolls.models import id_stem

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
        # Ciclo de vida cobre o teste (ou a classe) inteiro; a limpeza já é feita via addCleanup/tearDownClass.
        cls._tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
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
    """O `refresh` do Commons só aceitava `video/*`, e a foto nunca era coletada."""

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
    """`preview` de imagem remota sem intervalo morria em `TypeError`."""

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
    """A foto coletada sem prévia local saía como `.part` em `clips/` e `entrega/`."""

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


class UnknownImageFormatTests(RemoteImageFlowBase):
    """Extensão de foto só sai de assinatura conhecida; conteúdo estranho não herda a da URL."""

    UNKNOWN = "Formato de imagem não reconhecido"

    def serve_ppm(self):
        ppm = self.project.parent / (self.project.name + ".ppm")
        self.addCleanup(ppm.unlink, True)
        synth_image(ppm, color="red", size="64x48")
        self.files[NASA_MEDIA] = ppm.read_bytes()

    def test_a_static_preview_of_unrecognized_content_is_refused(self):
        # PPM decodifica no FFmpeg, mas não tem assinatura conhecida: antes virava `.jpg`.
        self.serve_ppm()
        item = self.found("nasa")
        with self.assertRaises(Exception) as caught:
            self.gb("preview", "--candidate", item["id"])
        self.assertIn(self.UNKNOWN, str(caught.exception))
        cache = self.project / ".getbrolls-sources"
        self.assertEqual([], [p.name for p in cache.glob("*") if p.suffix in (".jpg", ".bin", ".mp4")])

    def test_a_fetch_of_an_image_that_sniffs_as_a_video_container_is_refused(self):
        # AVIF/HEIC também começam com `ftyp`: nunca podem sair como `.mp4`.
        self.files[NASA_MEDIA] = b"\x00\x00\x00\x1cftypavif" + b"\x00" * 64
        item = self.found("nasa")
        self.gb("preview", "--candidate", item["id"], "--reference-only")
        self.approve_and_permit(item["id"], "nasa")
        with self.assertRaises(Exception) as caught:
            self.gb("fetch", "--candidate", item["id"])
        self.assertIn(self.UNKNOWN, str(caught.exception))
        brolls = self.project / "brolls"
        self.assertFalse(list((brolls / "clips").glob("*")) if (brolls / "clips").exists() else [])
        self.assertEqual([], list((brolls / "previews").glob("*.part")))


class ApprovedPhotoBytesTests(RemoteImageFlowBase):
    """O que o CHANGELOG promete: `fetch` copia os bytes aprovados, e só eles."""

    def previewed_and_permitted(self):
        item = self.found("nasa")
        shown = self.gb("preview", "--candidate", item["id"])
        self.approve_and_permit(item["id"], "nasa")
        return item["id"], Path(shown["local_path"])

    def test_fetch_copies_the_approved_bytes_without_network_even_if_the_remote_changed(self):
        cid, _cached = self.previewed_and_permitted()
        self.files[NASA_MEDIA] = self.png
        with patch.object(http, "_opener", side_effect=AssertionError("fetch não pode ir à rede")):
            fetched = self.gb("fetch", "--candidate", cid)
        self.assertTrue(fetched["output"]["path"].endswith(".jpg"), fetched["output"])
        self.assertEqual(self.jpeg, (self.project / "brolls" / fetched["output"]["path"]).read_bytes())

    def test_a_tampered_working_copy_is_refused(self):
        cid, cached = self.previewed_and_permitted()
        cached.write_bytes(self.png)
        with self.assertRaises(Exception) as caught:
            self.gb("fetch", "--candidate", cid)
        self.assertIn("Original local mudou", str(caught.exception))

    def test_a_photo_cached_as_mp4_by_2_5_0_is_collected_as_jpg(self):
        item = self.found("nasa")
        cache = self.project / ".getbrolls-sources"
        cache.mkdir(mode=0o700)
        sha = hashlib.sha256(self.jpeg).hexdigest()
        legacy = cache / f"{id_stem(item['id'])}-{sha}.mp4"
        legacy.write_bytes(self.jpeg)
        entry = {"path": str(legacy.resolve()), "sha": sha, "start": 0, "duration": 0.0}
        (cache / "index.json").write_text(json.dumps({item["id"]: [entry]}), encoding="utf-8")
        with patch.object(http, "_opener", side_effect=AssertionError("cópia do cache basta")):
            shown = self.gb("preview", "--candidate", item["id"])
        self.assertEqual(str(legacy.resolve()), shown["local_path"])
        self.approve_and_permit(item["id"], "nasa")
        fetched = self.gb("fetch", "--candidate", item["id"])
        self.assertTrue(fetched["output"]["path"].endswith(".jpg"), fetched["output"])
        self.assertEqual(self.jpeg, (self.project / "brolls" / fetched["output"]["path"]).read_bytes())


class RemoteImageGuidanceTests(RemoteImageFlowBase):
    """`status` mandava inspecionar a foto para sempre; `inspect` e `--scan` não saíam do lugar."""

    COVERED: ClassVar[dict] = {"beats": 1, "covered": 1, "missing": [], "blocked": [], "conflicts": []}

    def next_step(self):
        ledger = Ledger(self.project)
        return next_action(_flow_state(ledger, None, brief=dict(self.COVERED)))

    def test_status_sends_a_photo_straight_to_a_rangeless_preview(self):
        item = self.found("nasa")
        step = self.next_step()
        self.assertEqual("preview", step["step"], step)
        self.assertIn(shlex.quote(item["id"]), step["command"])
        self.assertNotIn("--start", step["command"])
        self.assertNotIn("--end", step["command"])
        self.assertNotIn("inspect", step["for_human"])
        # O comando sugerido funciona de verdade, e o passo seguinte já é a decisão humana.
        cli.main(suggested_argv(step["command"]))
        self.assertEqual("approve", self.next_step()["step"])

    def test_inspect_on_a_photo_points_to_the_static_preview(self):
        item = self.found("commons")
        with self.assertRaises(Exception) as caught:
            self.gb("inspect", "--candidate", item["id"], "--query", "lua")
        message = str(caught.exception)
        self.assertIn("preview --candidate", message)
        self.assertIn("sem `--start/--end`", message)

    def test_scan_on_a_photo_points_to_the_static_preview(self):
        item = self.found("nasa")
        with self.assertRaises(Exception) as caught:
            self.gb("preview", "--candidate", item["id"], "--scan")
        message = str(caught.exception)
        self.assertIn("preview --candidate", message)
        self.assertIn("sem `--start/--end`", message)


def _vp8_available():
    if not shutil.which("ffmpeg"):
        return False
    listed = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False)
    return "libvpx" in listed.stdout


@unittest.skipUnless(_vp8_available(), "FFmpeg com libvpx necessário")
class CommonsWebmCacheSuffixTests(RemoteImageFlowBase):
    """Cosmético: o webm do Commons ficava no cache privado como `.mp4`."""

    WEBM = "https://upload.wikimedia.org/wikipedia/commons/f/f0/Moon.webm"

    def test_the_cached_working_copy_keeps_the_webm_extension(self):
        source = self.project.parent / (self.project.name + "-moon.webm")
        self.addCleanup(source.unlink, True)
        lavfi = "testsrc=size=64x48:duration=2:rate=5"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", lavfi, "-c:v", "libvpx", str(source)], check=True)
        self.files[self.WEBM] = source.read_bytes()
        payload = _commons(url=self.WEBM, mime="video/webm")
        with patch.object(providers, "get_json", return_value=payload):
            item = self.gb("search", "--provider", "commons", "--query", "moon", "--media", "video", "--limit", "1")[
                "items"
            ][0]
            shown = self.gb("preview", "--candidate", item["id"], "--start", "0", "--end", "1")
        self.assertTrue(shown["local_path"].endswith(".webm"), shown["local_path"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
