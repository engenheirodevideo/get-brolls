"""`inspect` de vídeo do Commons lê a duração da API, sem baixar o arquivo inteiro.

M-6: no QA, `inspect --candidate commons:<vídeo>` baixou 72 MB para dentro do
projeto antes de qualquer confirmação — e só depois disse "Confirme antes de
baixar". O Commons publica a duração no mesmo `imageinfo` que a busca já pede;
com ela, o `inspect` cumpre o que o guia promete: metadados primeiro, nada de mídia.
"""

import tempfile
import types
import unittest
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import providers
from getbrolls.commands import execute
from getbrolls.runtime import audited

PAGE = {
    "pageid": 149451140,
    "title": "File:Riding on a Sounding Rocket.webm",
    "imageinfo": [
        {
            "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Riding_on_a_Sounding_Rocket.webm",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Riding_on_a_Sounding_Rocket.webm",
            "mime": "video/webm",
            "width": 1280,
            "height": 720,
            "size": 75_600_000,
            "duration": 270.3,
            "extmetadata": {"LicenseShortName": {"value": "Public domain"}, "Artist": {"value": "NASA"}},
        }
    ],
}


def api(url, params=None, **kwargs):
    if url.startswith("https://commons.wikimedia.org/w/api.php"):
        return {"query": {"pages": {str(PAGE["pageid"]): PAGE}}}
    raise AssertionError(f"pedido inesperado: {url}")


def refuse_download(*args, **kwargs):
    raise AssertionError("inspect não pode baixar o vídeo do Commons: a duração vem da API")


def ns(**values):
    return types.SimpleNamespace(env_file=None, confirm_format_change=False, **values)


class CommonsInspectReadsMetadata(unittest.TestCase):
    def test_search_records_the_duration_the_api_already_publishes(self):
        with patch.object(providers, "get_json", side_effect=api):
            item = providers.search("commons", "sounding rocket", 1, media="video")[0]
        self.assertEqual(270.3, item["media"]["duration_s"])

    def test_inspect_candidate_answers_from_metadata_without_downloading(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(providers, "get_json", side_effect=api),
            patch("getbrolls.http.download", side_effect=refuse_download),
        ):
            search = ns(
                command="search",
                project=tmp,
                provider="commons",
                query="sounding rocket",
                limit=1,
                intent="literal",
                shot=None,
                dry_run=False,
                media="video",
            )
            candidate_id = audited(search, execute)["items"][0]["id"]
            inspect = ns(command="inspect", project=tmp, candidate=candidate_id, url=None, query=None, max_windows=3)
            payload = audited(inspect, execute)
        self.assertEqual(270.3, payload["duration_s"])
        self.assertTrue(payload["candidate_windows"])
        self.assertFalse([w for w in payload["warnings"] if "arquivo inteiro" in w], payload["warnings"])
        self.assertNotIn("arquivo inteiro", payload["summary"]["line"])

    def test_inspect_url_of_a_commons_page_answers_from_metadata_too(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(providers, "get_json", side_effect=api),
            patch("getbrolls.http.download", side_effect=refuse_download),
        ):
            inspect = ns(
                command="inspect",
                project=tmp,
                candidate=None,
                url=PAGE["imageinfo"][0]["descriptionurl"],
                query=None,
                max_windows=3,
            )
            payload = audited(inspect, execute)
        self.assertEqual(270.3, payload["duration_s"])


if __name__ == "__main__":
    unittest.main()
