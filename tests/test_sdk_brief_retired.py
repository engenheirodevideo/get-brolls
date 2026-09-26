"""`ctx.brief()` de plugin enxerga os beats que a escada do `status` enxerga: sem os aposentados."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.brief import load_brief
from getbrolls.sdk import CommandContext

BRIEF = {
    "version": 1,
    "video": {"title": "T", "objective": "O", "audience": None,
              "delivery": {"format": "native", "duration_s": 45, "platform": None}},
    "rights": {"posture": "per_item_evidence", "stock_allowed": False, "notes": None},
    "defaults": {"allowed_sources": ["youtube"], "intent": "literal", "duration_hint_s": 4, "stock": False},
    "beats": [
        {"id": "c01", "target": "mesa"},
        {"id": "c02", "target": "mapa", "retired": True},
        {"id": "manual-1", "target": "algo", "retired": False},
        {"id": "c03", "target": "rua", "retired": True},
    ],
}  # fmt: skip

READER_MANIFEST = {**MANIFEST, "contributes": {"commands": ["ler"]}}
READER_CODE = """
def ler(args, ctx):
    return {"brief": ctx.brief(), "retired": ctx.retired_beat_ids()}


def register(api):
    api.command("ler", ler, "Lê o brief do projeto")
"""


def write_brief(project, data):
    """BRIEF.md mais um ROTEIRO.md do get-brolls: só com ele `retired` vale."""
    body = "# Brief\n\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n"
    (Path(project) / "BRIEF.md").write_text(body, encoding="utf-8")
    (Path(project) / "ROTEIRO.md").write_text('---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n', encoding="utf-8")


def without_retired(data):
    active = copy.deepcopy(data)
    active["beats"] = [b for b in active["beats"] if b.get("retired") is not True]
    return active


class CommandContextBriefTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-sdk-brief-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_retired_beats_leave_brief_and_are_listed_apart(self):
        write_brief(self.project, BRIEF)
        ctx = CommandContext("demo", self.project)
        data = ctx.brief()
        assert data is not None
        self.assertEqual(without_retired(BRIEF), data)
        self.assertEqual(["c01", "manual-1"], [b["id"] for b in data["beats"]])
        self.assertEqual(["c02", "c03"], ctx.retired_beat_ids())

    def test_brief_without_retired_beats_is_the_raw_block(self):
        plain = copy.deepcopy(BRIEF)
        plain["beats"] = [{"id": "c01", "target": "mesa"}, {"id": "c02", "target": "mapa"}]
        write_brief(self.project, plain)
        ctx = CommandContext("demo", self.project)
        self.assertEqual(load_brief(self.project), ctx.brief())
        self.assertEqual([], ctx.retired_beat_ids())

    def test_copy_does_not_touch_the_file(self):
        write_brief(self.project, BRIEF)
        before = (self.project / "BRIEF.md").read_text(encoding="utf-8")
        ctx = CommandContext("demo", self.project)
        first = ctx.brief()
        assert first is not None
        first["beats"].clear()
        self.assertEqual(before, (self.project / "BRIEF.md").read_text(encoding="utf-8"))
        again = ctx.brief()
        assert again is not None
        self.assertEqual(2, len(again["beats"]))

    def test_one_predicate_and_brief_order(self):
        data = copy.deepcopy(BRIEF)
        data["beats"] = [
            {"id": "c03", "target": "rua", "retired": True},
            {"id": "Cena 9", "target": "x", "retired": True},
            {"id": "c01", "target": "mesa"},
            {"id": "c02", "target": "mapa", "retired": True},
        ]
        write_brief(self.project, data)
        ctx = CommandContext("demo", self.project)
        brief = ctx.brief()
        assert brief is not None
        self.assertEqual(["Cena 9", "c01"], [b["id"] for b in brief["beats"]])
        self.assertEqual(["c03", "c02"], ctx.retired_beat_ids())

    def test_without_a_getbrolls_roteiro_nothing_is_retired(self):
        write_brief(self.project, BRIEF)
        (self.project / "ROTEIRO.md").write_text("# Meu roteiro\n", encoding="utf-8")
        ctx = CommandContext("demo", self.project)
        self.assertEqual(load_brief(self.project), ctx.brief())
        self.assertEqual([], ctx.retired_beat_ids())

    def test_no_project_or_no_brief(self):
        self.assertIsNone(CommandContext("demo", None).brief())
        self.assertEqual([], CommandContext("demo", None).retired_beat_ids())
        ctx = CommandContext("demo", self.project)
        self.assertIsNone(ctx.brief())
        self.assertEqual([], ctx.retired_beat_ids())


class PluginCommandSeesActiveBeatsTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.install(READER_MANIFEST, code=READER_CODE)
        self.project = Path(tempfile.mkdtemp(prefix="gb-project-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def env(self):
        pin_plugins("demo")
        return {"GB_HOME": str(self.home), "GB_PLUGINS": "demo"}

    def test_plugin_command_reads_only_active_beats(self):
        write_brief(self.project, BRIEF)
        out = run_cli("x", "demo", "ler", project=self.project, env=self.env())
        self.assertEqual(without_retired(BRIEF), out["result"]["brief"])
        self.assertEqual(["c02", "c03"], out["result"]["retired"])


if __name__ == "__main__":
    unittest.main()
