"""Rota de `fetch`: licença consumida uma vez só, estágio visível para status/guidance e CLI de inspect.

RT-07/Minor 10 (arquivo da rota vai para o cache privado, licença e marcador gravados
antes do corte, retry reaproveita), Minor 8 (status nunca manda inspecionar nem gerar
prévia com intervalo de uma fonte que só entrega no fetch), Minor 9 (extensão da
imagem roteada), Minor 11 (um teto só) e Minor 15 (inspect --candidate/--url na CLI).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_image, synth_video
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import acquisition, cli, http
from getbrolls.runtime import OperationError

ROUTE_MANIFEST = {**MANIFEST, "contributes": {"providers": ["demo"], "routes": ["demo"]}}

FETCH_PLUGIN = """
import os
import shutil
from pathlib import Path

from getbrolls.sdk import ProviderCapabilities, RouteResult

KIND = "video"
TARGET = "v.mp4"


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True, url_hosts=("demo.example",), route="demo")

    def __init__(self, api):
        self.api = api

    def item(self):
        item = self.api.candidate("demo", "1", "Demo", "https://demo.example/v/1")
        item["media"]["kind"] = KIND
        if KIND == "video":
            item["media"]["duration_s"] = float(os.environ.get("DEMO_DURATION", "30"))
        return item

    def search(self, query, limit, media):
        return [self.item()]

    def resolve(self, url):
        return self.item()

    def refresh(self, item):
        return item


class Licenciada:
    name = "demo"
    stage = "fetch"

    def prepare(self, item, workdir):
        with Path(os.environ["DEMO_CALLS"]).open("a", encoding="utf-8") as calls:
            calls.write(item["id"] + "\\n")
        target = workdir / TARGET
        shutil.copyfile(os.environ["DEMO_SOURCE"], target)
        return RouteResult(target, "Standard License #42")


def register(api):
    api.provider(Fonte(api))
    api.route(Licenciada())
"""

# B2: a rota deixa uma pasta comum (não um objeto git) sem nenhuma permissão dentro
# do workdir. force_rmtree precisa limpar isso no `finally` do plugin_source sem
# levantar — senão o fetch vira INTERNAL_ERROR mesmo com o arquivo já verificado.
LOCKED_DIR_FETCH_PLUGIN = FETCH_PLUGIN.replace(
    '        return RouteResult(target, "Standard License #42")',
    '        (workdir / "locked").mkdir(mode=0)\n        return RouteResult(target, "Standard License #42")',
)
assert LOCKED_DIR_FETCH_PLUGIN != FETCH_PLUGIN


@skip_unless_ffmpeg
class FetchRouteCase(LoaderTestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.TemporaryDirectory()
        cls.video = Path(cls._media.name) / "fonte.mp4"
        synth_video(cls.video, duration=3)
        cls.image = Path(cls._media.name) / "foto.png"
        synth_image(cls.image)

    @classmethod
    def tearDownClass(cls):
        cls._media.cleanup()

    def setUp(self):
        super().setUp()
        self.project = Path(tempfile.mkdtemp(prefix="gb-project-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.calls = self.project.parent / (self.project.name + "-calls.txt")
        self.addCleanup(self.calls.unlink, missing_ok=True)

    def enable(self, code=FETCH_PLUGIN, source=None, duration="30"):
        self.install(ROUTE_MANIFEST, code=code)
        self.env = {
            "GB_HOME": str(self.home),
            "GB_PLUGINS": "demo",
            "DEMO_SOURCE": str(source or self.video),
            "DEMO_CALLS": str(self.calls),
            "DEMO_DURATION": duration,
        }
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def gb(self, *args):
        return cli.main([args[0], "--project", str(self.project), *args[1:]])

    def calls_made(self):
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def manifest_item(self, ident):
        data = json.loads((self.project / "brolls" / "manifest.json").read_text(encoding="utf-8"))
        return next(item for item in data["items"] if item["id"] == ident)

    def approve_range(self, ident, start, end):
        self.gb("preview", "--candidate", ident, "--start", str(start), "--end", str(end), "--reference-only")
        self.gb("approve", "--candidate", ident, "--by", "Bruno", "--statement", "pode usar esse")
        self.gb("permit", "--candidate", ident, "--evidence", "Plano anual da conta Demo")


@skip_unless_ffmpeg
class LicenseConsumedOnceTests(FetchRouteCase):
    def test_failed_cut_keeps_the_license_and_retry_never_calls_the_route_again(self):
        self.enable()
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 10, 15)
        for _attempt in range(2):
            with self.assertRaises(OperationError) as caught:
                self.gb("fetch", "--candidate", ident)
            self.assertIn("duração", str(caught.exception))
            self.assertEqual(["demo:1"], self.calls_made())
            stored = self.manifest_item(ident)
            self.assertEqual(
                1, stored["rights"]["evidence"].count("Licença registrada pelo plugin demo: Standard License #42")
            )
            self.assertIsInstance(stored["acquisition"].get("route_consumed_at"), str)
        self.approve_range(ident, 0, 2)
        done = self.gb("fetch", "--candidate", ident)
        self.assertTrue(done["output"]["verified"])
        self.assertEqual(["demo:1"], self.calls_made())
        self.assertEqual(
            1, done["rights"]["evidence"].count("Licença registrada pelo plugin demo: Standard License #42")
        )
        self.assertEqual([], sorted((self.project / ".getbrolls-sources").glob("plugin-demo-*")))
        self.assertEqual([], sorted((self.project / "brolls" / "previews").glob("download-*")))

    def test_fetch_cache_is_never_used_as_a_preview_source(self):
        self.enable()
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 2)
        self.gb("fetch", "--candidate", ident)
        with self.assertRaises(OperationError) as caught:
            self.gb("preview", "--candidate", ident, "--start", "0", "--end", "1")
        self.assertIn("--reference-only", str(caught.exception))


@skip_unless_ffmpeg
class RoutedImageExtensionTests(FetchRouteCase):
    def image_plugin(self, target):
        code = FETCH_PLUGIN.replace('KIND = "video"', 'KIND = "image"').replace(
            'TARGET = "v.mp4"', f"TARGET = {target!r}"
        )
        self.assertNotEqual(FETCH_PLUGIN, code)
        return code

    def test_image_with_an_unlisted_extension_is_refused(self):
        self.enable(self.image_plugin("foto.bin"), source=self.image)
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.gb("preview", "--candidate", ident, "--reference-only")
        self.gb("approve", "--candidate", ident, "--by", "Bruno", "--statement", "pode usar essa")
        self.gb("permit", "--candidate", ident, "--evidence", "Plano anual da conta Demo")
        with self.assertRaises(OperationError) as caught:
            self.gb("fetch", "--candidate", ident)
        self.assertIn(".jpg", str(caught.exception))
        self.assertEqual([], sorted((self.project / "brolls" / "clips").glob("*")))

    def test_bad_extension_does_not_call_the_route_again_on_retry(self):
        """Minor 9: a rota já rodou e devolveu um arquivo verificado; a recusa por
        extensão não pode custar a licença de novo a cada retry — o cache e a
        licença já foram gravados antes dessa checagem."""
        self.enable(self.image_plugin("foto.bin"), source=self.image)
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.gb("preview", "--candidate", ident, "--reference-only")
        self.gb("approve", "--candidate", ident, "--by", "Bruno", "--statement", "pode usar essa")
        self.gb("permit", "--candidate", ident, "--evidence", "Plano anual da conta Demo")
        for _attempt in range(2):
            with self.assertRaises(OperationError) as caught:
                self.gb("fetch", "--candidate", ident)
            self.assertIn(".jpg", str(caught.exception))
        self.assertEqual(["demo:1"], self.calls_made())  # a rota rodou uma vez só
        stored = self.manifest_item(ident)
        self.assertEqual(
            1, stored["rights"]["evidence"].count("Licença registrada pelo plugin demo: Standard License #42")
        )
        self.assertIsInstance(stored["acquisition"].get("route_consumed_at"), str)
        self.assertEqual([], sorted((self.project / "brolls" / "clips").glob("*")))

    def test_listed_image_extension_is_kept(self):
        self.enable(self.image_plugin("foto.PNG"), source=self.image)
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.gb("preview", "--candidate", ident, "--reference-only")
        self.gb("approve", "--candidate", ident, "--by", "Bruno", "--statement", "pode usar essa")
        self.gb("permit", "--candidate", ident, "--evidence", "Plano anual da conta Demo")
        done = self.gb("fetch", "--candidate", ident)
        self.assertTrue(done["output"]["path"].endswith(".png"))


@skip_unless_ffmpeg
@unittest.skipIf(os.name == "nt", "walk por fd (func=os.open) do force_rmtree é POSIX-only")
@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignora permissão de escrita")
class LockedWorkdirCleanupTests(FetchRouteCase):
    """B2: a rota deixa `workdir/locked` (mode 0) para trás. force_rmtree
    (chamado no `finally` de `plugin_source`) não pode levantar TypeError — o
    arquivo já tinha sido movido para o cache antes da limpeza, então um
    INTERNAL_ERROR aqui perderia o `_save_index` e faria a rota rodar nunca (o
    cache já existe) ou o marcador da licença nunca ser gravado."""

    def test_a_permission_locked_leftover_in_the_workdir_does_not_cause_internal_error(self):
        self.enable(LOCKED_DIR_FETCH_PLUGIN)
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 2)
        done = self.gb("fetch", "--candidate", ident)
        self.assertTrue(done["output"]["verified"])
        self.assertEqual(["demo:1"], self.calls_made())  # a rota rodou uma vez só
        self.assertEqual(
            1, done["rights"]["evidence"].count("Licença registrada pelo plugin demo: Standard License #42")
        )
        self.assertIsInstance(self.manifest_item(ident)["acquisition"].get("route_consumed_at"), str)
        # workdir (e a pasta `locked` de dentro) limpos: nada sobra em .getbrolls-sources/
        self.assertEqual([], sorted((self.project / ".getbrolls-sources").glob("plugin-demo-*")))

        # `verify` reconfere o mesmo arquivo do cache sem chamar a rota de novo.
        again = self.gb("verify")
        self.assertEqual(1, again["count"])
        self.assertEqual(["demo:1"], self.calls_made())
        self.assertTrue(self.manifest_item(ident)["output"]["verified"])


class FetchStageGuidanceTests(FetchRouteCase):
    """Minor 8: status/guidance mandam `preview --reference-only`, nunca inspect ou prévia com intervalo."""

    def test_candidate_carries_the_fetch_stage_and_guidance_recommends_reference_only(self):
        import shlex

        from getbrolls import commands
        from getbrolls.cli import build_parser
        from getbrolls.guidance import next_action
        from getbrolls.ledger import Ledger

        self.enable(duration="0")
        item = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]
        stored = self.manifest_item(item["id"])
        self.assertEqual("fetch", stored["preview"].get("route_stage"))
        items = Ledger(self.project).data["items"]
        self.assertEqual([], commands._uninspected(items))
        self.assertEqual([item["id"]], commands._reference_only(items))
        state = {
            "project": str(self.project),
            "counts": {"candidates": 1, "previews": 0, "approved": 0, "permitted": 0, "delivered": 0, "verified": 0},
            "format_pending": 0,
            "brief": {"beats": 1, "covered": 1, "missing": [], "conflicts": []},
            "review_page": False,
            "rights_mode": "per_item_evidence",
            "candidates": commands._step_candidates(items),
            "duration_unknown": len(commands._uninspected(items)),
            "inspect_candidate": None,
            "reference_only": commands._reference_only(items),
        }
        action = next_action(state)
        self.assertEqual("preview", action["step"])
        self.assertIn("--reference-only", action["command"])
        parsed = build_parser().parse_args(shlex.split(action["command"])[2:])
        self.assertTrue(parsed.reference_only)
        self.assertEqual(item["id"], parsed.candidate)

    def test_route_stage_survives_a_new_segment(self):
        self.enable()
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.gb("preview", "--candidate", ident, "--start", "0", "--end", "2", "--reference-only")
        self.gb("preview", "--candidate", ident, "--start", "1", "--end", "3", "--reference-only")
        self.assertEqual("fetch", self.manifest_item(ident)["preview"].get("route_stage"))

    def test_status_summary_do_never_suggests_inspect(self):
        self.enable(duration="0")
        self.gb("search", "--provider", "demo", "--query", "mar")
        status = run_cli("status", project=self.project, env=self.env)
        self.assertNotIn(" inspect ", status["summary"]["do"]["command"] or "")


class InspectCliTests(FetchRouteCase):
    """Minor 15: `inspect --candidate` e `inspect --url` recusam a rota de fetch antes de rodar o plugin."""

    def test_inspect_candidate_and_url_are_refused_without_calling_the_plugin(self):
        self.enable()
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        for args in (("--candidate", ident), ("--url", "https://demo.example/v/1")):
            with self.subTest(args=args):
                err = run_cli("inspect", *args, project=self.project, expect=2, env=self.env)
                self.assertIn("--reference-only", err["error"])
        self.assertEqual([], self.calls_made())


class SingleCapTests(LoaderTestCase):
    def test_route_cap_is_the_download_cap(self):
        self.assertEqual(http.DOWNLOAD_MAX_BYTES, acquisition.ROUTE_MAX_BYTES)
        self.assertNotIn("512 * 1024 * 1024", Path(acquisition.__file__).read_text(encoding="utf-8"))
