"""O acervo da NASA publica ids com espaço, e o fluxo inteiro quebrava por causa disso.

Contra a API real, `search --provider nasa --query "Artemis I launch SLS"` devolve
itens cujo arquivo mora em `.../Artemis I Launch 2022 CU tracking from Press Site_
compressed~medium.mp4`. O `inspect --candidate` e o `preview` seguintes morriam em
`http.download` com `InvalidURL: URL can't contain control characters` — e o relatório
da busca ainda mostrava `media.kind: None`. Este teste refaz a sequência exata com a
API e a rede dubladas: o que ele prova é que a URL sai percent-encoded e que os três
comandos chegam ao fim.
"""

import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _media import synth_video
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import cli, http, providers

NASA_ID = "Artemis I Launch 2022 CU tracking from Press Site_compressed"
BASE = "https://images-assets.nasa.gov/video/" + NASA_ID + "/" + NASA_ID
MEDIA_URL = BASE + "~medium.mp4"
POSTER_URL = BASE + "~thumb.jpg"


def _search_payload():
    return {
        "collection": {
            "items": [
                {
                    "data": [
                        {
                            "nasa_id": NASA_ID,
                            "title": "Artemis I Launch",
                            "media_type": "video",
                            "center": "KSC",
                        }
                    ],
                    "links": [{"rel": "preview", "href": POSTER_URL}],
                }
            ]
        }
    }


def _asset_payload():
    return {
        "collection": {
            "items": [
                {"href": BASE + "~orig.mp4"},
                {"href": MEDIA_URL},
                {"href": BASE + "~metadata.json"},
            ]
        }
    }


class _Response(io.BytesIO):
    """O mínimo que `http.download` usa de uma resposta HTTPS."""

    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
class NasaSpacedUrlFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        source = Path(cls._tmp.name) / "fixture.mp4"
        synth_video(source, size="320x180", duration=6, rate=10)
        cls.media_bytes = source.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _get_json(self, url, params=None, headers=None, cache_ttl=0):
        if url.startswith("https://images-api.nasa.gov/search"):
            return _search_payload()
        if url.startswith("https://images-api.nasa.gov/asset/"):
            return _asset_payload()
        raise AssertionError(f"pedido inesperado à API: {url}")

    def test_search_inspect_and_preview_survive_an_id_with_spaces(self):
        requested = []
        media = self.media_bytes

        class _Opener:
            def open(self, request, timeout=None):
                requested.append(request.full_url)
                return _Response(media)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(providers, "get_json", side_effect=self._get_json),
            patch.object(http, "_opener", lambda: _Opener()),
        ):
            found = cli.main(
                ["search", "--project", tmp, "--provider", "nasa", "--query", "Artemis I launch SLS", "--limit", "1"]
            )
            item = found["items"][0]
            # A busca já entrega o arquivo utilizável e diz o que ele é.
            self.assertNotIn(" ", item["media_url"])
            self.assertIn("%20", item["media_url"])
            self.assertNotIn(" ", item["preview"]["poster_url"])
            self.assertEqual("video", item["media"]["kind"])

            looked = cli.main(["inspect", "--project", tmp, "--candidate", item["id"], "--query", "Artemis"])
            self.assertAlmostEqual(6.0, looked["duration_s"], delta=0.5)

            preview = cli.main(["preview", "--project", tmp, "--candidate", item["id"], "--start", "0", "--end", "5"])
            sheet = preview["preview"]["contact_sheet_path"]
            self.assertTrue((Path(tmp) / "brolls" / sheet).is_file(), preview)

        # Nenhum pedido saiu com espaço cru, e o arquivo com id espaçado foi mesmo pedido.
        self.assertTrue(requested)
        for url in requested:
            self.assertNotIn(" ", url, requested)
        self.assertIn(MEDIA_URL.replace(" ", "%20"), requested)

    def test_an_already_encoded_path_is_left_alone(self):
        encoded = "https://images-assets.nasa.gov/video/a%20b/a%20b~medium.mp4"
        self.assertEqual(encoded, http.encoded_url(encoded))
        self.assertEqual(encoded, http.encoded_url(http.encoded_url(encoded.replace("%20", " "))))
        # Consulta e host ficam como estão; só o caminho é escapado.
        self.assertEqual(
            "https://host.example/a%20b?q=a b",
            http.encoded_url("https://host.example/a b?q=a b"),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
