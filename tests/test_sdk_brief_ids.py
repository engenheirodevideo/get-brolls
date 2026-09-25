"""`ctx.brief()` e `ctx.retired_beat_ids()` com beat cujo id não é texto: nada cai, nada some."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.sdk import CommandContext

BRIEF = {
    "version": 1,
    "video": {"title": "T", "objective": "O", "audience": None,
              "delivery": {"format": "native", "duration_s": 45, "platform": None}},
    "rights": {"posture": "per_item_evidence", "stock_allowed": False, "notes": None},
    "defaults": {"allowed_sources": ["youtube"], "intent": "literal", "duration_hint_s": 4, "stock": False},
    "beats": [
        {"id": "c01", "target": "mesa"},
        {"id": ["c02"], "target": "lista no lugar do id"},
        {"id": {"x": 1}, "target": "objeto no lugar do id", "retired": True},
        {"id": 7, "target": "número no lugar do id"},
        {"id": "c03", "target": "rua", "retired": True},
        "beat que nem é objeto",
    ],
}  # fmt: skip


def write_project(project, data):
    body = "# Brief\n\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n"
    (project / "BRIEF.md").write_text(body, encoding="utf-8")
    (project / "ROTEIRO.md").write_text('---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n', encoding="utf-8")


class UnhashableBeatIdTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-sdk-ids-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        write_project(self.project, BRIEF)

    def test_brief_keeps_every_beat_without_a_text_id(self):
        data = CommandContext("demo", self.project).brief()
        assert data is not None
        expected = copy.deepcopy(BRIEF["beats"])
        del expected[4]  # só o c03 (texto, aposentado) sai
        self.assertEqual(expected, data["beats"])

    def test_retired_ids_list_only_text_ids(self):
        self.assertEqual(["c03"], CommandContext("demo", self.project).retired_beat_ids())

    def test_file_is_untouched_and_copies_are_independent(self):
        before = (self.project / "BRIEF.md").read_bytes()
        ctx = CommandContext("demo", self.project)
        first = ctx.brief()
        assert first is not None
        first["beats"][1]["id"].append("mexido")
        again = ctx.brief()
        assert again is not None
        self.assertEqual(["c02"], again["beats"][1]["id"])
        self.assertEqual(before, (self.project / "BRIEF.md").read_bytes())


if __name__ == "__main__":
    unittest.main()
