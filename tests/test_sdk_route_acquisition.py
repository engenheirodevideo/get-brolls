"""Rota de plugin no fluxo: pasta de trabalho, verificação do core, estágio e licença."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _media import skip_unless_ffmpeg, synth_image, synth_video
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import acquisition, cli, providers
from getbrolls.http import ProviderError
from getbrolls.ledger import Ledger, digest
from getbrolls.runtime import OperationError
from getbrolls.sdk.registry import reset_registry

ROUTE_MANIFEST = {**MANIFEST, "contributes": {"providers": ["demo"], "routes": ["demo"]}}


def _hardlinks_work():
    """`os.link` funciona aqui? No NTFS funciona; só um sistema de arquivos sem
    hardlink (ou um `os` sem `link`) faz o teste de hardlink ser pulado."""
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "a"
        source.write_bytes(b"x")
        try:
            os.link(source, Path(tmp) / "b")
        except (AttributeError, NotImplementedError, OSError):
            return False
    return True


HARDLINKS_WORK = _hardlinks_work()

ROUTE_PLUGIN = """
import os
import shutil
from pathlib import Path

from getbrolls.sdk import ProviderCapabilities, RouteResult

STAGE = "preview"
LICENSE = None


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True, route="demo")

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")
        item["preview"]["embed_url"] = "https://demo.example/embed/1"  # algo para a pessoa ver
        item["media"]["duration_s"] = 6
        item["media"]["kind"] = "video"
        return [item]

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


class Copia:
    name = "demo"
    stage = STAGE

    def prepare(self, item, workdir):
        with Path(os.environ["DEMO_CALLS"]).open("a", encoding="utf-8") as calls:
            calls.write(item["id"] + "\\n")
        item["approval"] = "mexido pela rota"
        target = workdir / "v.mp4"
        shutil.copyfile(os.environ["DEMO_SOURCE"], target)
        return RouteResult(target, LICENSE)


def register(api):
    api.provider(Fonte(api))
    api.route(Copia())
"""

RETURN_LINE = "        return RouteResult(target, LICENSE)"
FETCH_PLUGIN = ROUTE_PLUGIN.replace('STAGE = "preview"', 'STAGE = "fetch"').replace(
    "LICENSE = None", 'LICENSE = "Licença padrão Demo, pedido L-1"'
)
OUTSIDE = ROUTE_PLUGIN.replace(RETURN_LINE, '        return RouteResult(Path(os.environ["DEMO_SOURCE"]))')
SYMLINK = ROUTE_PLUGIN.replace(
    RETURN_LINE,
    '        link = workdir / "link.mp4"\n'
    '        link.symlink_to(os.environ["DEMO_SOURCE"])\n'
    "        return RouteResult(link)",
)
EMPTY = ROUTE_PLUGIN.replace(
    RETURN_LINE, '        (workdir / "vazio.mp4").write_bytes(b"")\n        return RouteResult(workdir / "vazio.mp4")'
)
NOT_MEDIA = ROUTE_PLUGIN.replace(
    RETURN_LINE,
    '        (workdir / "nota.mp4").write_text("não sou vídeo")\n        return RouteResult(workdir / "nota.mp4")',
)
HARDLINK = ROUTE_PLUGIN.replace(
    RETURN_LINE,
    '        link = workdir / "link.mp4"\n'
    '        os.link(os.environ["DEMO_SOURCE"], link)\n'
    "        return RouteResult(link)",
)
IMAGE_FETCH_PLUGIN = (
    FETCH_PLUGIN.replace(
        'item["media"]["duration_s"] = 6\n        item["media"]["kind"] = "video"', 'item["media"]["kind"] = "image"'
    )
    .replace('target = workdir / "v.mp4"', 'target = workdir / "v.png"')
    .replace('os.environ["DEMO_SOURCE"]', 'os.environ["DEMO_IMAGE_SOURCE"]')
)
for variant in (FETCH_PLUGIN, OUTSIDE, SYMLINK, EMPTY, NOT_MEDIA, HARDLINK, IMAGE_FETCH_PLUGIN):
    assert variant != ROUTE_PLUGIN  # replace() sem alvo encontrado devolveria o original
assert IMAGE_FETCH_PLUGIN != FETCH_PLUGIN


@skip_unless_ffmpeg
class RouteAcquisitionTests(LoaderTestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.TemporaryDirectory()
        cls.source = Path(cls._media.name) / "fonte.mp4"
        synth_video(cls.source, duration=6)
        cls.image_source = Path(cls._media.name) / "foto.png"
        synth_image(cls.image_source)

    @classmethod
    def tearDownClass(cls):
        cls._media.cleanup()

    def setUp(self):
        super().setUp()
        self.project = Path(tempfile.mkdtemp(prefix="gb-project-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.calls = self.project.parent / (self.project.name + "-calls.txt")
        self.addCleanup(self.calls.unlink, missing_ok=True)

    def enable(self, code=ROUTE_PLUGIN):
        self.install(ROUTE_MANIFEST, code=code)
        pin_plugins("demo")
        env = {
            "GB_PLUGINS": "demo",
            "DEMO_SOURCE": str(self.source),
            "DEMO_IMAGE_SOURCE": str(self.image_source),
            "DEMO_CALLS": str(self.calls),
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def candidate(self):
        return providers.search("demo", "mar", 1)[0]

    def calls_made(self):
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def leftovers(self):
        return sorted((self.project / ".getbrolls-sources").glob("plugin-demo-*"))

    def test_preview_route_fills_the_working_source_through_the_cache(self):
        self.enable()
        ledger = Ledger(self.project)
        c = self.candidate()
        with self.assertLogs("getbrolls.acquisition", level="INFO") as cm:
            acquisition.prepare_source(ledger, c, 0, 2)
        local = Path(c["local_path"])
        self.assertEqual(self.project.resolve() / ".getbrolls-sources", local.parent)
        self.assertEqual(digest(self.source), c["local_sha256"])
        self.assertEqual("pending", c["approval"]["status"])
        self.assertEqual([], self.leftovers())
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_route", joined)
        self.assertIn("stage=preview", joined)
        self.assertNotIn(str(self.project), joined)
        acquisition.prepare_source(ledger, c, 1, 3)
        self.assertEqual(["demo:1"], self.calls_made())

    def test_fetch_stage_route_never_runs_outside_fetch(self):
        self.enable(FETCH_PLUGIN)
        ledger = Ledger(self.project)
        c = self.candidate()
        for run in (
            lambda: acquisition.prepare_source(ledger, c, 0, 2),
            lambda: acquisition.prepare_source(ledger, c, 0, 6, tolerant=True, stage="scan"),
            lambda: acquisition.cache_direct_media(ledger, c),
        ):
            with self.subTest(run=run), self.assertRaises(ValueError) as caught:
                run()
            self.assertIn("--reference-only", str(caught.exception))
        self.assertEqual([], self.calls_made())
        self.assertIsNone(c.get("local_path"))

    def test_fetch_stage_route_runs_in_fetch_and_hands_over_the_license(self):
        self.enable(FETCH_PLUGIN)
        ledger = Ledger(self.project)
        with acquisition.plugin_source(ledger, self.candidate(), "fetch") as routed:
            self.assertTrue(routed.path.is_file())
            self.assertEqual("Licença padrão Demo, pedido L-1", routed.license)
            self.assertEqual("demo", routed.plugin)
        self.assertEqual([], self.leftovers())

    def test_core_refuses_what_the_route_returns_wrong(self):
        cases = {"fora": OUTSIDE, "vazio": EMPTY, "não é mídia": NOT_MEDIA}
        if os.name != "nt":
            cases["link"] = SYMLINK
        if HARDLINKS_WORK:
            cases["hardlink"] = HARDLINK
        self.enable()
        for label, code in cases.items():
            with self.subTest(label):
                self.install(ROUTE_MANIFEST, code=code)
                pin_plugins("demo")
                reset_registry()
                ledger = Ledger(self.project)
                with (
                    self.assertLogs("getbrolls.acquisition", level="WARNING") as cm,
                    self.assertRaises(ProviderError) as caught,
                ):
                    acquisition.prepare_source(ledger, self.candidate(), 0, 2)
                self.assertTrue(str(caught.exception).startswith("Plugin demo:"))
                self.assertIn("event=plugin_call_failed", "\n".join(cm.output))
                self.assertEqual([], self.leftovers())
        self.assertTrue(self.source.is_file())

    def test_hardlink_is_refused_and_leaves_the_original_untouched(self):
        if not HARDLINKS_WORK:
            self.skipTest("os.link não funciona neste sistema de arquivos")
        self.enable(HARDLINK)
        before_mode = self.source.stat().st_mode
        ledger = Ledger(self.project)
        with self.assertRaises(ProviderError) as caught:
            acquisition.prepare_source(ledger, self.candidate(), 0, 2)
        self.assertIn("hardlink", str(caught.exception))
        self.assertEqual(before_mode, self.source.stat().st_mode)
        self.assertEqual([], self.leftovers())

    def test_size_cap_applies_to_routes(self):
        self.enable()
        with patch.object(acquisition, "ROUTE_MAX_BYTES", 10), self.assertRaises(ProviderError) as caught:
            acquisition.prepare_source(Ledger(self.project), self.candidate(), 0, 2)
        self.assertIn("teto", str(caught.exception))

    def test_route_of_an_unloaded_plugin_names_the_problem(self):
        self.enable()
        c = self.candidate()
        with patch.dict(os.environ, {"GB_PLUGINS": "off"}):
            reset_registry()
            with self.assertRaises(ValueError) as caught:
                acquisition.prepare_source(Ledger(self.project), c, 0, 2)
        self.assertIn("não está carregada", str(caught.exception))

    def test_full_flow_fetches_only_after_approval_and_permit(self):
        self.enable(FETCH_PLUGIN)
        project = str(self.project)
        found = cli.main(["search", "--project", project, "--provider", "demo", "--query", "mar", "--limit", "1"])
        ident = found["items"][0]["id"]
        with self.assertRaises(OperationError) as caught:
            cli.main(["preview", "--project", project, "--candidate", ident, "--start", "0", "--end", "2"])
        self.assertIn("--reference-only", str(caught.exception))
        cli.main(
            ["preview", "--project", project, "--candidate", ident, "--start", "0", "--end", "2", "--reference-only"]
        )
        cli.main(
            [
                "approve",
                "--project",
                project,
                "--candidate",
                ident,
                "--by",
                "Pessoa Teste",
                "--statement",
                "pode usar esse",
            ]
        )
        cli.main(["permit", "--project", project, "--candidate", ident, "--evidence", "Plano anual da conta Demo"])
        self.assertEqual([], self.calls_made())
        done = cli.main(["fetch", "--project", project, "--candidate", ident])
        self.assertTrue(done["output"]["verified"])
        self.assertTrue((self.project / "brolls" / done["output"]["path"]).is_file())
        self.assertEqual(
            ["Plano anual da conta Demo", "Licença registrada pelo plugin demo: Licença padrão Demo, pedido L-1"],
            done["rights"]["evidence"],
        )
        self.assertEqual(["demo:1"], self.calls_made())
        self.assertEqual([], self.leftovers())
        self.assertEqual([], sorted((self.project / "brolls" / "previews").glob("download-*")))

    def test_full_flow_fetches_an_image_once_and_leaves_nothing_in_previews(self):
        self.enable(IMAGE_FETCH_PLUGIN)
        project = str(self.project)
        found = cli.main(["search", "--project", project, "--provider", "demo", "--query", "mar", "--limit", "1"])
        ident = found["items"][0]["id"]
        cli.main(["preview", "--project", project, "--candidate", ident, "--reference-only"])
        cli.main(
            [
                "approve",
                "--project",
                project,
                "--candidate",
                ident,
                "--by",
                "Pessoa Teste",
                "--statement",
                "pode usar essa",
            ]
        )
        cli.main(["permit", "--project", project, "--candidate", ident, "--evidence", "Plano anual da conta Demo"])
        self.assertEqual([], self.calls_made())
        done = cli.main(["fetch", "--project", project, "--candidate", ident])
        self.assertTrue(done["output"]["verified"])
        self.assertTrue((self.project / "brolls" / done["output"]["path"]).is_file())
        self.assertEqual(["demo:1"], self.calls_made())
        self.assertEqual([], self.leftovers())
        self.assertEqual([], sorted((self.project / "brolls" / "previews").glob("download-*")))
        with self.assertRaises(OperationError) as caught:
            cli.main(["fetch", "--project", project, "--candidate", ident])
        self.assertIn("já está coletada", str(caught.exception))
        # A recusa acontece antes de rodar a rota de novo: nenhuma chamada extra ao plugin.
        self.assertEqual(["demo:1"], self.calls_made())


class BuiltinUntouchedTests(unittest.TestCase):
    def test_core_candidates_never_build_the_registry(self):
        core = {"media_url": "https://videos.example.org/v.mp4", "acquisition": {"method": "https"}}
        with patch("getbrolls.sdk.registry.get_registry", side_effect=AssertionError("não devia montar")):
            self.assertIsNone(acquisition.route_name(core))
            self.assertTrue(acquisition.direct_media(core))
            self.assertFalse(acquisition.direct_media({"acquisition": {"method": "yt-dlp"}}))
        self.assertEqual("demo", acquisition.route_name({"acquisition": {"method": "plugin:demo"}}))
        self.assertIsNone(acquisition.route_name({"acquisition": {"method": "plugin:"}}))


if __name__ == "__main__":
    unittest.main()
