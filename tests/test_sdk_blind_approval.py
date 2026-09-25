"""C M-7: candidato de plugin sem nada que a pessoa possa ter visto (sem prévia local,
sem poster_url, sem embed_url) não é aprovado; `preview --reference-only` diz que
ficou sem imagem. Fonte embutida não muda."""

import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import cli
from getbrolls.ledger import Ledger
from getbrolls.runtime import OperationError

BLIND_PLUGIN = """
from getbrolls.sdk.contracts import ProviderCapabilities

EMBED = None


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True)

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        item = self.api.candidate("demo", "1", "Demo", "https://demo.example/v/1")
        item["media"]["kind"] = "video"
        item["media"]["duration_s"] = 30
        if EMBED:
            item["preview"]["embed_url"] = EMBED
        return [item]

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


def register(api):
    api.provider(Fonte(api))
"""


class BlindApprovalTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.project = Path(tempfile.mkdtemp(prefix="gb-m7-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def gb(self, *args):
        return cli.main([args[0], "--project", str(self.project), *args[1:]])

    def found(self, code=BLIND_PLUGIN):
        self.install({**MANIFEST, "contributes": {"providers": ["demo"]}}, code)
        pin_plugins("demo")
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        return ident, self.gb("preview", "--candidate", ident, "--reference-only")

    def test_nothing_to_show_is_said_and_approval_is_refused(self):
        ident, preview = self.found()
        self.assertIn("Sem imagem de referência", preview["summary"]["line"])
        with self.assertRaises(OperationError) as caught:
            self.gb("approve", "--candidate", ident, "--start", "0", "--end", "2", "--by", "Bruno", "--statement", "ok")
        self.assertIn("não pode ser aprovada", str(caught.exception))
        self.assertEqual("pending", Ledger(self.project).get(ident)["approval"]["status"])

    def test_embed_url_is_something_seen(self):
        ident, _preview = self.found(BLIND_PLUGIN.replace("EMBED = None", 'EMBED = "https://demo.example/embed/1"'))
        done = self.gb(
            "approve", "--candidate", ident, "--start", "0", "--end", "2", "--by", "Bruno", "--statement", "ok"
        )
        self.assertEqual("approved", done["approval"]["status"])


if __name__ == "__main__":
    unittest.main()
