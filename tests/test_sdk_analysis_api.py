"""`ctx.analysis`: plugin lê e, com `permissions.project_write: ["analysis"]` aprovado, grava em analysis/."""

import json
import shutil
import tempfile
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import analysis, runtime
from getbrolls.sdk import loader, testing
from getbrolls.sdk.contracts import AnalysisAccess, CommandContext
from getbrolls.sdk.errors import ApiError

COMMANDS = ["transcreve", "absoluto", "nucleo", "marca", "le"]
WRITER = {
    **MANIFEST,
    "id": "legenda",
    "name": "Legenda",
    "contributes": {"commands": COMMANDS},
    "permissions": {"project_write": ["analysis"]},
}
READER = {**WRITER, "id": "leitor", "name": "Leitor", "permissions": {}}

CODE = """
WORDS = [{"text": "Olá", "start": 0.1, "end": 0.4, "probability": None, "speaker": None}]


def _doc(**extra):
    return {"status": "done", "reason": None, "language": "pt", "text": "Olá", "word_count": 1, "words": WORDS,
            "producer": {"tool": "getbrolls", "model": None, "version": None}, **extra}


def transcreve(args, ctx):
    media_id = ctx.analysis.media_id(args["path"])
    ctx.analysis.write(media_id, "transcript", _doc(), model="tiny", version="1.0")
    return {"media_id": media_id, "producer": ctx.analysis.read(media_id, "transcript")["producer"]}


def absoluto(args, ctx):
    media_id = ctx.analysis.media_id(args["path"])
    ctx.analysis.write(media_id, "transcript", _doc(note="/etc/passwd"))
    return {}


def nucleo(args, ctx):
    ctx.analysis.write(ctx.analysis.media_id(args["path"]), "media", {})
    return {}


def marca(args, ctx):
    media_id = ctx.analysis.media_id(args["path"])
    ctx.analysis.write_markers([{"id": "m_0123456789", "media_id": media_id, "scene": None, "start": 1.0,
                                 "end": None, "name": "gancho", "color": "RED", "comment": None}])
    return {"ok": True}


def le(args, ctx):
    media_id = ctx.analysis.media_id(args["path"])
    return {"media_id": media_id, "transcript": ctx.analysis.read(media_id, "transcript")}


def register(api):
    for name in ("transcreve", "absoluto", "nucleo", "marca", "le"):
        api.command(name, globals()[name], "Comando de teste de analysis/")
"""  # fmt: skip


class AnalysisApiTestCase(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.project = Path(tempfile.mkdtemp(prefix="gb-sdk-analysis-")).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        (self.project / "aroll").mkdir()
        (self.project / "aroll" / "c01.mp4").write_bytes(b"voz" * 50)

    def enable(self, manifest):
        self.install(manifest, code=CODE)
        pin_plugins(manifest["id"])
        return {"GB_HOME": str(self.home), "GB_PLUGINS": manifest["id"]}

    def x(self, plugin, command, env, expect=0):
        return run_cli(
            "x", plugin, command, "--arg", "path=aroll/c01.mp4", project=self.project, env=env, expect=expect
        )


class WriteTests(AnalysisApiTestCase):
    def test_plugin_writes_a_transcript_as_itself(self):
        env = self.enable(WRITER)
        out = self.x("legenda", "transcreve", env)["result"]
        self.assertEqual({"tool": "legenda", "model": "tiny", "version": "1.0"}, out["producer"])
        stored = analysis.read_component(self.project, out["media_id"], "transcript")
        assert stored is not None
        self.assertEqual("legenda", stored["producer"]["tool"])
        self.assertTrue(analysis.check_all(self.project)["ok"])

    def test_absolute_path_and_the_core_media_file_are_refused(self):
        env = self.enable(WRITER)
        refused = self.x("legenda", "absoluto", env, expect=1)
        self.assertIn("Plugin legenda:", refused["error"])
        self.assertIn("ABSOLUTE_PATH", refused["error"])
        refused = self.x("legenda", "nucleo", env, expect=1)
        self.assertIn("media.json", refused["error"])

    def test_markers_carry_the_plugin_as_producer(self):
        env = self.enable(WRITER)
        self.assertTrue(self.x("legenda", "marca", env)["result"]["ok"])
        doc = json.loads((self.project / "analysis" / "markers.json").read_text(encoding="utf-8"))
        self.assertEqual(["legenda"], [m["producer"]["tool"] for m in doc["markers"]])

    def test_x_still_takes_no_project_lock(self):
        env = self.enable(WRITER)
        (self.project / "brolls").mkdir()
        with runtime.exclusive_lock(self.project / "brolls" / ".command.lock", "ocupado"):
            self.x("legenda", "transcreve", env)
        self.assertFalse((self.project / "brolls" / "manifest.json").exists())


class PermissionTests(AnalysisApiTestCase):
    def test_without_the_permission_the_plugin_only_reads(self):
        analysis.ensure_media(self.project, "aroll/c01.mp4", probe=False)
        env = self.enable(READER)
        refused = self.x("leitor", "transcreve", env, expect=1)
        self.assertIn("Plugin leitor:", refused["error"])
        self.assertIn("project_write", refused["error"])
        read = self.x("leitor", "le", env)["result"]
        self.assertIsNone(read["transcript"])

    def test_reader_cannot_register_new_media(self):
        env = self.enable(READER)
        refused = self.x("leitor", "le", env, expect=1)
        self.assertIn("analysis --action register", refused["error"])
        self.assertFalse((self.project / "analysis").exists())

    def test_permission_comes_from_the_pin_not_the_manifest(self):
        analysis.ensure_media(self.project, "aroll/c01.mp4", probe=False)
        env = self.enable(WRITER)
        state = json.loads(loader.state_path().read_text(encoding="utf-8"))
        state["enabled"]["legenda"]["permissions"]["project_write"] = []
        loader.state_path().write_text(json.dumps(state), encoding="utf-8")
        refused = self.x("legenda", "transcreve", env, expect=1)
        self.assertIn("project_write", refused["error"])

    def test_without_project_every_call_is_refused(self):
        ctx = CommandContext("legenda", None, permissions={"project_write": ["analysis"]})
        with self.assertRaisesRegex(ApiError, "--project"):
            ctx.analysis.read("a" * 16, "transcript")

    def test_unknown_area_fails_the_check_and_the_preview_lists_it(self):
        folder = self.install({**WRITER, "permissions": {"project_write": ["tasks"]}}, code=CODE)
        refused = run_cli("plugins", "--action", "check", "--path", folder, expect=1)
        self.assertIn("project_write", refused["error"])
        self.install(WRITER, code=CODE)
        preview = loader.enable("legenda", confirm=False)
        self.assertEqual(["analysis"], preview["plugin"]["permissions"]["project_write"])


class HarnessTests(AnalysisApiTestCase):
    def test_testing_builds_the_same_context(self):
        ctx = testing.command_context("legenda", self.project, project_write=["analysis"])
        self.assertIsInstance(ctx.analysis, AnalysisAccess)
        media_id = ctx.analysis.media_id("aroll/c01.mp4")
        words = [{"text": "a", "start": 0.0, "end": 0.2, "probability": None, "speaker": None}]
        doc = {"status": "done", "reason": None, "language": "pt", "text": "a", "word_count": 1, "words": words}
        ctx.analysis.write(media_id, "transcript", doc)
        stored = ctx.analysis.read(media_id, "transcript")
        assert stored is not None
        self.assertEqual("legenda", stored["producer"]["tool"])
        reader = testing.command_context("leitor", self.project)
        with self.assertRaisesRegex(ApiError, "project_write"):
            reader.analysis.write(media_id, "transcript", doc)
        with self.assertRaisesRegex(ApiError, "media"):
            ctx.analysis.write(media_id, "media", doc)
        with self.assertRaisesRegex(ApiError, "register"):
            ctx.analysis.write("b" * 16, "transcript", doc)
