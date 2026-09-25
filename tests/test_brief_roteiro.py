"""BRIEF.md num projeto com ROTEIRO.md: beats vazios permitidos e beats aposentados fora de brief/deliver."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT

from getbrolls import brief, delivery
from getbrolls.commands import brief_report, brief_state
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment

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


CLI = ROOT / "scripts" / "gb.py"
RETIRED_REASON = "beat aposentado pelo roteiro"


def run_cli(test, *args):
    done = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, encoding="utf-8", check=False
    )
    test.assertEqual(0, done.returncode, done.stderr + done.stdout)
    return json.loads(done.stdout)


def fetched(source_id, title, shot):
    """Clipe aprovado, permitido, coletado e conferido, ligado ao beat `shot`."""
    c = candidate("local", source_id, title, source_url="https://example.org/" + source_id)
    set_segment(c, 0, 2)
    c["creator"]["name"] = "Autora Exemplo"
    c["preview"]["contact_sheet_path"] = f"previews/{source_id}.jpg"
    c["approval"] = {"status": "approved", "by": "Humano", "at": now(), "revision": 1, "channel": "chat",
                     "statement": "aprovo"}  # fmt: skip
    c["rights"]["status"] = "permitted"
    c["rights"]["evidence"] = ["Condições conferidas na página da fonte"]
    c["output"] = {"path": f"clips/{source_id}.mp4", "sha256": "a" * 64, "verified": True}
    c["state"] = "verified"
    c["shot"] = shot
    c["id"] += ":shot:" + shot
    return c


class RetiredDeliveryTests(unittest.TestCase):
    """Q18: clipe de beat aposentado fica em brolls/, mas não vira pasta viva em entrega/."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", self.project)

    def write_brief(self, data):
        body = "# Brief\n\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n"
        (self.project / "BRIEF.md").write_text(body, encoding="utf-8")

    def store(self, items):
        ledger = Ledger(self.project)
        stored = [ledger.add(c) for c in items]
        ledger.save_many("fixture", stored)
        for c in stored:
            for rel in (c["output"]["path"], c["preview"]["contact_sheet_path"]):
                path = ledger.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"conteudo de " + rel.encode())
        return stored

    def folders(self):
        root = self.project / "entrega"
        return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []

    def three_clips(self):
        return self.store([fetched("a", "Titulo c01", "c01"), fetched("b", "Titulo c02", "c02"),
                           fetched("c", "Titulo manual", "manual-1")])  # fmt: skip

    def test_deliver_skips_clips_of_retired_beats(self):
        self.write_brief(BASE)
        _, retired, _ = self.three_clips()
        expected = [delivery.beat_dir_name(1, "c01", "mesa"), delivery.beat_dir_name(2, "manual-1", "algo")]
        entry = {"id": retired["id"], "shot": "c02", "reason": RETIRED_REASON}
        planned = delivery.build_delivery(str(self.project), dry_run=True)
        self.assertEqual(planned["retired"], [entry])
        self.assertEqual(self.folders(), [])
        report = delivery.build_delivery(str(self.project))
        self.assertEqual(self.folders(), expected)
        self.assertEqual(report["retired"], [entry])
        self.assertNotIn("c02", {item["beat"] for item in report["items"]})
        self.assertTrue((self.project / "brolls" / "clips" / "b.mp4").is_file())
        cli = run_cli(self, "deliver", "--dry-run", "--project", self.project)
        self.assertEqual(cli["retired"], [entry])

    def test_shot_outside_the_brief_keeps_todays_behaviour(self):
        self.write_brief(BASE)
        self.store([fetched("a", "Titulo c01", "c01"), fetched("d", "Titulo fora", "fora")])
        report = delivery.build_delivery(str(self.project))
        self.assertEqual(
            self.folders(), [delivery.beat_dir_name(1, "c01", "mesa"), delivery.beat_dir_name(3, "fora", "Titulo fora")]
        )
        self.assertNotIn("retired", report)

    def test_folder_of_a_beat_retired_later_is_swept(self):
        alive = copy.deepcopy(BASE)
        del alive["beats"][1]["retired"]
        self.write_brief(alive)
        self.three_clips()
        delivery.build_delivery(str(self.project))
        old = delivery.beat_dir_name(2, "c02", "mapa")
        self.assertIn(old, self.folders())
        self.write_brief(BASE)
        report = delivery.build_delivery(str(self.project))
        self.assertNotIn(old, self.folders())
        self.assertTrue(any(rel.startswith(old) for rel in report["removed"]))

    def test_status_does_not_ask_to_deliver_a_retired_clip(self):
        self.write_brief(BASE)
        self.three_clips()
        run_cli(self, "deliver", "--project", self.project)
        status = run_cli(self, "status", "--project", self.project)
        self.assertNotEqual(status["summary"]["do"]["step"], "deliver")
        self.assertNotIn("entrega/ com deliver", status["summary"]["next"])
        self.assertIn("Fluxo completo", status["summary"]["next"])


class ZeroBeatsWithRoteiroTests(unittest.TestCase):
    """Com ROTEIRO.md e nenhum beat ativo, todo comando aponta para o sync do roteiro."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", self.project)
        body = "# Brief\n\n```json\n" + json.dumps(with_beats([]), ensure_ascii=False, indent=2) + "\n```\n"
        (self.project / "BRIEF.md").write_text(body, encoding="utf-8")
        (self.project / "ROTEIRO.md").write_text("---\n", encoding="utf-8")

    def test_status_points_to_sync(self):
        status = run_cli(self, "status", "--project", self.project)
        self.assertEqual(status["summary"]["do"]["step"], "roteiro-sync")
        self.assertIn("roteiro --action sync", status["summary"]["do"]["command"])
        self.assertIn("roteiro --action sync", status["summary"]["next"])

    def test_plain_brief_points_to_sync(self):
        result = run_cli(self, "brief", "--project", self.project)
        self.assertIn("sync", result["summary"]["next"])
        self.assertNotIn("buscar as fontes", result["summary"]["next"])

    def test_validate_is_consistent(self):
        result = run_cli(self, "brief", "--validate", "--project", self.project)
        self.assertTrue(result["valid"])
        self.assertNotIn("para resolver", result["summary"]["line"])
        self.assertIn("ROTEIRO.md", result["summary"]["line"])
        self.assertIn("roteiro --action sync", result["summary"]["next"])
        self.assertNotIn("repita", result["summary"]["next"])


if __name__ == "__main__":
    unittest.main()
