"""BRIEF.md num projeto com ROTEIRO.md: beats vazios permitidos e beats aposentados fora de brief/deliver."""

import copy
import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT

from getbrolls import brief, delivery
from getbrolls.commands import brief_report, brief_state

BASE = {
    "version": 1,
    "video": {"title": "T", "objective": "O", "audience": None,
              "delivery": {"format": "native", "duration_s": 45, "platform": None}},
    "rights": {"posture": "per_item_evidence", "stock_allowed": False, "notes": None},
    "defaults": {"allowed_sources": ["youtube"], "intent": "literal", "duration_hint_s": 4, "stock": False},
    "beats": [
        {"id": "c01", "target": "mesa"},
        {"id": "c02", "target": "mapa", "retired": True},
        {"id": "manual-1", "target": "algo"},
    ],
}  # fmt: skip
EMPTY_MESSAGE = (
    'Em BRIEF.md, "beats" tem que ser uma lista com pelo menos um beat; cada beat precisa de "id" e "target".'
)


def with_beats(beats):
    data = copy.deepcopy(BASE)
    data["beats"] = beats
    return data


class ValidateBriefTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_empty_beats_only_with_roteiro(self):
        for project in (None, self.project):
            with self.subTest(project=project), self.assertRaises(ValueError) as ctx:
                brief.validate_brief(with_beats([]), project=project)
            self.assertEqual(str(ctx.exception), EMPTY_MESSAGE)
        (self.project / "ROTEIRO.md").write_text("---\n", encoding="utf-8")
        data, conflicts = brief.validate_brief(with_beats([]), project=self.project)
        self.assertEqual((data["beats"], conflicts), ([], []))
        with self.assertRaises(ValueError):
            brief.validate_brief(with_beats([]))

    def test_retired_beats_are_validated_but_not_returned(self):
        data, _ = brief.validate_brief(copy.deepcopy(BASE))
        self.assertEqual([b["id"] for b in data["beats"]], ["c01", "manual-1"])
        duplicated = with_beats([{"id": "c01", "target": "a"}, {"id": "c01", "target": "b", "retired": True}])
        with self.assertRaises(ValueError) as ctx:
            brief.validate_brief(duplicated)
        self.assertIn("repetido", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            brief.validate_brief(with_beats([{"id": "c01", "target": "a", "retired": "sim"}]))
        self.assertIn("beats[0].retired", str(ctx.exception))

    def test_only_retired_beats_without_roteiro_is_the_old_error(self):
        with self.assertRaises(ValueError) as ctx:
            brief.validate_brief(with_beats([{"id": "c01", "target": "a", "retired": True}]))
        self.assertEqual(str(ctx.exception), EMPTY_MESSAGE)


class ConsumersTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def write_brief(self, data):
        body = "# Brief\n\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n"
        (self.project / "BRIEF.md").write_text(body, encoding="utf-8")

    def report(self, validate=False, beat=None):
        return brief_report(types.SimpleNamespace(project=str(self.project), validate=validate, beat=beat))

    def test_brief_and_status_skip_retired_beats(self):
        self.write_brief(BASE)
        self.assertEqual([b["id"] for b in self.report()["beats"]], ["c01", "manual-1"])
        with self.assertRaises(ValueError) as ctx:
            self.report(beat="c02")
        self.assertIn("c01, manual-1", str(ctx.exception))
        state = brief_state(str(self.project), None, [])
        assert state is not None
        self.assertEqual(state["beats"], 2)

    def test_deliver_order_skips_retired_beats(self):
        self.write_brief(BASE)
        self.assertEqual([b["id"] for b in delivery._brief_beats(str(self.project))], ["c01", "manual-1"])

    def test_validate_with_roteiro_and_no_beats_points_to_sync(self):
        self.write_brief(with_beats([]))
        (self.project / "ROTEIRO.md").write_text("---\n", encoding="utf-8")
        result = self.report(validate=True)
        self.assertEqual(result["beats"], 0)
        self.assertIn("roteiro --action sync", " ".join(result["summary"]["problems"]))
        self.assertEqual(delivery._brief_beats(str(self.project)), [])


class DocsAndSchemaTests(unittest.TestCase):
    def test_schema_documents_retired(self):
        schema = json.loads((ROOT / "schemas" / "brief.schema.json").read_text(encoding="utf-8"))
        beat = schema["properties"]["beats"]["items"]
        self.assertEqual(beat["properties"]["retired"]["type"], "boolean")
        self.assertNotIn("minItems", schema["properties"]["beats"])

    def test_interview_has_the_roteiro_branch(self):
        for path in (ROOT / "commands" / "get-brolls-brief.md", ROOT / "references" / "interview.md"):
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertIn("ROTEIRO.md", body)
                self.assertIn('"beats": []', body)


if __name__ == "__main__":
    unittest.main()
