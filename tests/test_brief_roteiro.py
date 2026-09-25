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
from typing import ClassVar, cast
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT

from getbrolls import brief, delivery
from getbrolls.commands import brief_report, brief_state, record_empty_searches, status_report
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment
from getbrolls.rules import load_rules

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


ROTEIRO_HEAD = '---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n'
SCENES = {"c01": "## Mesa <!-- c01 -->\n[BROLL: mesa]\nFala.\n", "c02": "## Mapa <!-- c02 -->\n[BROLL: mapa]\nFala.\n"}


def with_beats(beats):
    data = copy.deepcopy(BASE)
    data["beats"] = beats
    return data


def write_roteiro_at(project, ids=("c01",)):
    """ROTEIRO.md do get-brolls já sincronizado com os beats de cena ativos `ids`: nada pede sync."""
    project = Path(project)
    (project / "ROTEIRO.md").write_text(ROTEIRO_HEAD + "\n" + "\n".join(SCENES[i] for i in ids), encoding="utf-8")
    state = project / "brolls" / "roteiro-state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text('{"next_id": 3, "scenes": {}}\n', encoding="utf-8")


class ValidateBriefTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_empty_beats_only_with_roteiro(self):
        for project in (None, self.project):
            with self.subTest(project=project), self.assertRaises(ValueError) as ctx:
                brief.validate_brief(with_beats([]), project=project)
            self.assertEqual(str(ctx.exception), EMPTY_MESSAGE)
        (self.project / "ROTEIRO.md").write_text(ROTEIRO_HEAD, encoding="utf-8")
        data, conflicts = brief.validate_brief(with_beats([]), project=self.project)
        self.assertEqual((data["beats"], conflicts), ([], []))
        with self.assertRaises(ValueError):
            brief.validate_brief(with_beats([]))

    def test_retired_beats_are_validated_but_not_returned(self):
        write_roteiro_at(self.project)
        data, _ = brief.validate_brief(copy.deepcopy(BASE), project=self.project)
        self.assertEqual([b["id"] for b in data["beats"]], ["c01", "manual-1"])
        duplicated = with_beats([{"id": "c01", "target": "a"}, {"id": "c01", "target": "b", "retired": True}])
        with self.assertRaises(ValueError) as ctx:
            brief.validate_brief(duplicated, project=self.project)
        self.assertIn("repetido", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            brief.validate_brief(with_beats([{"id": "c01", "target": "a", "retired": "sim"}]), project=self.project)
        self.assertIn("beats[0].retired", str(ctx.exception))

    def test_retired_flag_without_roteiro_is_ignored_like_before(self):
        only_retired = with_beats([{"id": "c01", "target": "a", "retired": True}])
        data, _ = brief.validate_brief(copy.deepcopy(only_retired))
        self.assertEqual([b["id"] for b in data["beats"]], ["c01"])
        write_roteiro_at(self.project)
        data, _ = brief.validate_brief(only_retired, project=self.project)
        self.assertEqual(data["beats"], [])


class ConsumersTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        write_roteiro_at(self.project)

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
        (self.project / "ROTEIRO.md").write_text(ROTEIRO_HEAD, encoding="utf-8")
        (self.project / "brolls" / "roteiro-state.json").unlink()
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


def write_brief_at(project, data):
    body = "# Brief\n\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n"
    (project / "BRIEF.md").write_text(body, encoding="utf-8")


def store_at(project, items):
    """Grava os clipes no manifesto e cria os arquivos de saída e de prévia deles."""
    ledger = Ledger(project)
    stored = [ledger.add(c) for c in items]
    ledger.save_many("fixture", stored)
    for c in stored:
        for rel in (c["output"]["path"], c["preview"]["contact_sheet_path"]):
            path = ledger.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"conteudo de " + rel.encode())
    return stored


class RetiredDeliveryTests(unittest.TestCase):
    """Clipe de beat aposentado fica em brolls/, mas não vira pasta viva em entrega/."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", self.project)
        write_roteiro_at(self.project)

    def write_brief(self, data):
        write_brief_at(self.project, data)

    def store(self, items):
        return store_at(self.project, items)

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
        write_roteiro_at(self.project, ("c01", "c02"))
        self.three_clips()
        delivery.build_delivery(str(self.project))
        old = delivery.beat_dir_name(2, "c02", "mapa")
        self.assertIn(old, self.folders())
        self.write_brief(BASE)
        write_roteiro_at(self.project)
        report = delivery.build_delivery(str(self.project))
        self.assertNotIn(old, self.folders())
        self.assertTrue(any(rel.startswith(old) for rel in report["removed"]))

    def test_reactivated_beat_asks_to_deliver_again(self):
        alive = copy.deepcopy(BASE)
        del alive["beats"][1]["retired"]
        self.write_brief(alive)
        write_roteiro_at(self.project, ("c01", "c02"))
        _, clip, _ = self.three_clips()
        run_cli(self, "deliver", "--project", self.project)
        self.write_brief(BASE)
        write_roteiro_at(self.project)
        run_cli(self, "deliver", "--project", self.project)
        self.assertNotIn("delivery", Ledger(self.project, recover=False).get(clip["id"]))
        self.assertNotEqual(run_cli(self, "status", "--project", self.project)["summary"]["do"]["step"], "deliver")
        self.write_brief(alive)
        write_roteiro_at(self.project, ("c01", "c02"))
        self.assertEqual(run_cli(self, "status", "--project", self.project)["summary"]["do"]["step"], "deliver")
        run_cli(self, "deliver", "--project", self.project)
        self.assertIn(delivery.beat_dir_name(2, "c02", "mapa"), self.folders())

    def test_status_does_not_ask_to_deliver_a_retired_clip(self):
        self.write_brief(BASE)
        self.three_clips()
        run_cli(self, "deliver", "--project", self.project)
        status = run_cli(self, "status", "--project", self.project)
        self.assertNotEqual(status["summary"]["do"]["step"], "deliver")
        self.assertNotIn("entrega/ com deliver", status["summary"]["next"])
        self.assertIn("Fluxo completo", status["summary"]["next"])


BRIEF_STEPS = {"brief-search", "brief-exhausted", "brief-unavailable", "brief-resolve"}


class RetiredBeatLadderTests(unittest.TestCase):
    """Beat aposentado sem candidato nunca vira degrau brief-* nem entra no progresso do `brief`."""

    # Fontes do beat c02 → degrau que ele daria se estivesse ativo.
    VARIANTS: ClassVar[dict[str, tuple[list[str], str]]] = {
        "search": (["youtube"], "brief-search"),
        "resolve": (["instagram"], "brief-resolve"),
        "unavailable": (["pexels"], "brief-unavailable"),
        "exhausted": (["youtube"], "brief-exhausted"),
    }

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", self.project)
        # Chave de banco ausente sem depender do ambiente de quem roda o teste.
        no_keys = patch.object(brief, "env_is_set", return_value=False)
        no_keys.start()
        self.addCleanup(no_keys.stop)
        stored = store_at(self.project, [fetched("a", "Titulo c01", "c01"), fetched("c", "Titulo manual", "manual-1")])
        self.assertEqual(len(stored), 2)
        delivery.build_delivery(str(self.project))

    def write(self, sources, retired, variant):
        data = copy.deepcopy(BASE)
        data["rights"]["stock_allowed"] = True
        beat = data["beats"][1]
        beat["allowed_sources"] = sources
        beat["stock"] = sources == ["pexels"]
        if not retired:
            del beat["retired"]
        write_brief_at(self.project, data)
        write_roteiro_at(self.project, ("c01",) if retired else ("c01", "c02"))
        if variant == "exhausted":
            ledger = Ledger(self.project)
            resolved = brief.resolve_beat(data["defaults"], beat, 1)
            record_empty_searches(ledger, "c02", [(s, q) for s in sources for q in brief.search_queries(resolved)])

    def step(self):
        report = status_report(Ledger(self.project, recover=False), load_rules(str(self.project)))
        return report["summary"]["do"]

    def test_active_control_reaches_each_brief_step(self):
        for variant, (sources, expected) in self.VARIANTS.items():
            with self.subTest(variant=variant):
                self.write(sources, retired=False, variant=variant)
                do = self.step()
                self.assertEqual(do["step"], expected)
                data = Ledger(self.project, recover=False).data
                state = brief_state(str(self.project), None, data["items"], data)
                assert state is not None
                self.assertEqual([entry["id"] for entry in cast("list", state["missing"])], ["c02"])

    def test_retired_beat_never_becomes_a_brief_step(self):
        for variant, (sources, _expected) in self.VARIANTS.items():
            with self.subTest(variant=variant):
                self.write(sources, retired=True, variant=variant)
                do = self.step()
                self.assertNotIn(do["step"], BRIEF_STEPS)
                self.assertNotIn("c02", json.dumps(do, ensure_ascii=False))
                data = Ledger(self.project, recover=False).data
                state = brief_state(str(self.project), None, data["items"], data)
                assert state is not None
                self.assertEqual((state["missing"], state["beats"], state["covered"]), ([], 2, 2))

    def test_brief_progress_does_not_list_a_retired_beat(self):
        self.write(["youtube"], retired=True, variant="search")
        report = brief_report(types.SimpleNamespace(project=str(self.project), validate=False, beat=None))
        self.assertEqual([b["id"] for b in report["beats"]], ["c01", "manual-1"])
        self.assertIn("2 beats, 2 com candidato e 0 sem", report["summary"]["line"])
        self.assertNotIn("c02", " ".join(report["summary"]["problems"]))

    def test_beat_progress_skips_a_raw_retired_beat(self):
        beats = [{"id": "c01"}, {"id": "c02", "retired": True}]
        items = [{"id": "x", "shot": "c02"}]
        self.assertEqual(brief.beat_progress(beats, items), {"c01": []})


class SearchRetiredShotTests(unittest.TestCase):
    """`search --shot` num beat aposentado recusa antes de consultar qualquer fonte."""

    def test_search_on_a_retired_beat_is_refused_in_portuguese(self):
        project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", project)
        write_brief_at(project, BASE)
        write_roteiro_at(project)
        done = subprocess.run(
            [sys.executable, str(CLI), "search", "--query", "mapa", "--shot", "c02", "--provider", "youtube",
             "--project", str(project)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )  # fmt: skip
        self.assertNotEqual(done.returncode, 0)
        error = json.loads(done.stderr or done.stdout)
        self.assertIn('O beat "c02" foi aposentado pelo roteiro', error["error"])
        self.assertIn("roteiro --action sync", error["error"])
        self.assertFalse(error["state_committed"])
        manifest = project / "brolls" / "manifest.json"
        items = json.loads(manifest.read_text(encoding="utf-8"))["items"] if manifest.is_file() else []
        self.assertEqual(items, [])
        # Beat ativo do mesmo brief segue com a regra de fontes de antes.
        from getbrolls.commands import _beat_search_names

        self.assertEqual(_beat_search_names(str(project), "c01", None, "youtube", ["youtube"]), ["youtube"])


class RetiredWithoutFullValidationTests(unittest.TestCase):
    """O aposentado vale mesmo quando o brief não passa na validação completa (postura, RULES.md)."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", self.project)
        write_roteiro_at(self.project)

    def declaration_brief(self):
        data = copy.deepcopy(BASE)
        # RULES.md padrão não tem nome nem declaração: a validação completa recusa.
        data["rights"]["posture"] = "user_declaration"
        write_brief_at(self.project, data)
        with self.assertRaises(ValueError):
            brief.validate_brief(brief.load_brief(self.project), load_rules(str(self.project)), project=self.project)

    def test_user_declaration_posture_still_keeps_retired_clip_out_of_entrega(self):
        self.declaration_brief()
        _, retired, _ = store_at(self.project, [fetched("a", "Titulo c01", "c01"), fetched("b", "Titulo c02", "c02"),
                                               fetched("c", "Titulo manual", "manual-1")])  # fmt: skip
        self.assertEqual(brief.retired_beat_ids(self.project), frozenset({"c02"}))
        report = delivery.build_delivery(str(self.project))
        self.assertEqual(report["retired"], [{"id": retired["id"], "shot": "c02", "reason": RETIRED_REASON}])
        entrega = self.project / "entrega"
        folders = sorted(p.name for p in entrega.iterdir() if p.is_dir())
        self.assertEqual(len(folders), 2)
        self.assertFalse(any("c02" in name for name in folders))
        status = run_cli(self, "status", "--project", self.project)
        self.assertNotEqual(status["summary"]["do"]["step"], "deliver")
        self.assertNotIn("entrega/ com deliver", status["summary"]["next"])

    def test_raw_reading_ignores_bad_ids_and_unreadable_json(self):
        data = copy.deepcopy(BASE)
        data["beats"].append({"id": "Cena 9", "target": "x", "retired": True})
        data["beats"].append({"id": "c09", "target": "x", "retired": "sim"})
        write_brief_at(self.project, data)
        self.assertEqual(brief.retired_beat_ids(self.project), frozenset({"c02"}))
        (self.project / "BRIEF.md").write_text("# Brief\n\n```json\n{ quebrado\n```\n", encoding="utf-8")
        self.assertEqual(brief.retired_beat_ids(self.project), frozenset())
        (self.project / "BRIEF.md").unlink()
        self.assertEqual(brief.retired_beat_ids(self.project), frozenset())


class RetiredShotEverywhereTests(unittest.TestCase):
    """`resolve --shot` e `brief --beat` num beat aposentado dizem o motivo, sem comando de busca."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", self.project)
        write_brief_at(self.project, BASE)
        write_roteiro_at(self.project)

    def failed(self, *args):
        done = subprocess.run(
            [sys.executable, str(CLI), *map(str, args), "--project", str(self.project)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )  # fmt: skip
        self.assertNotEqual(done.returncode, 0)
        return json.loads(done.stderr or done.stdout)

    def test_resolve_on_a_retired_beat_is_refused_like_search(self):
        clip = self.project / "clipe.mp4"
        clip.write_bytes(b"nao e video")
        resolved = self.failed("resolve", "--file", clip, "--shot", "c02")
        searched = self.failed("search", "--query", "mapa", "--shot", "c02", "--provider", "youtube")
        self.assertIn('O beat "c02" foi aposentado pelo roteiro', resolved["error"])
        self.assertEqual(resolved["error"], searched["error"])
        self.assertFalse(resolved["state_committed"])
        manifest = self.project / "brolls" / "manifest.json"
        items = json.loads(manifest.read_text(encoding="utf-8"))["items"] if manifest.is_file() else []
        self.assertEqual(items, [])

    def test_brief_beat_on_a_retired_beat_says_it_was_retired(self):
        error = self.failed("brief", "--beat", "c02")["error"]
        self.assertIn('O beat "c02" foi aposentado pelo roteiro', error)
        self.assertNotIn("search --", error)
        with self.assertRaises(ValueError) as ctx:
            brief_report(types.SimpleNamespace(project=str(self.project), validate=False, beat="nenhum"))
        self.assertIn("não tem o beat", str(ctx.exception))


class ZeroBeatsWithRoteiroTests(unittest.TestCase):
    """Com ROTEIRO.md e nenhum beat ativo, todo comando aponta para o sync do roteiro."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", self.project)
        body = "# Brief\n\n```json\n" + json.dumps(with_beats([]), ensure_ascii=False, indent=2) + "\n```\n"
        (self.project / "BRIEF.md").write_text(body, encoding="utf-8")
        (self.project / "ROTEIRO.md").write_text(ROTEIRO_HEAD, encoding="utf-8")

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


class RoteiroOwnershipTests(unittest.TestCase):
    """Só um ROTEIRO.md do get-brolls (`type: roteiro` no frontmatter) muda o brief; o roteiro da pessoa não."""

    FOREIGN = (
        "# Meu roteiro\n\nCena 1: fala.\n",
        "---\ntype: nota\n---\n\n## Cena\nFala.\n",
        "---\ntitle: x\n---\n",
    )

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-brief-rot-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        run_cli(self, "init-rules", "--project", self.project)

    def test_foreign_roteiro_keeps_the_old_rules(self):
        for text in self.FOREIGN:
            with self.subTest(text=text):
                (self.project / "ROTEIRO.md").write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError) as ctx:
                    brief.validate_brief(with_beats([]), project=self.project)
                self.assertEqual(str(ctx.exception), EMPTY_MESSAGE)
                write_brief_at(self.project, BASE)
                self.assertEqual(brief.retired_beat_ids(self.project), frozenset())
                data, _ = brief.validate_brief(copy.deepcopy(BASE), project=self.project)
                self.assertEqual([b["id"] for b in data["beats"]], ["c01", "c02", "manual-1"])
                status = run_cli(self, "status", "--project", self.project)
                self.assertNotEqual(status["summary"]["do"]["step"], "roteiro-sync")

    def test_unreadable_or_odd_roteiro_never_raises(self):
        from getbrolls import roteiro

        path = self.project / "ROTEIRO.md"
        path.write_bytes(b"---\ntype: roteiro\xff\n---\n")
        self.assertFalse(roteiro.is_roteiro(self.project))
        path.unlink()
        path.mkdir()
        self.assertFalse(roteiro.is_roteiro(self.project))
        path.rmdir()
        self.assertFalse(roteiro.is_roteiro(self.project))

    def test_getbrolls_roteiro_is_recognised(self):
        from getbrolls import roteiro

        path = self.project / "ROTEIRO.md"
        for text in ("---\ntype: roteiro\n---\n", '\ufeff---\r\ntype: "roteiro"\r\ngenero: x\r\n---\r\n'):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                self.assertTrue(roteiro.is_roteiro(self.project))
        path.write_text("---\ngenero: reels\n---\ntype: roteiro\n", encoding="utf-8")
        self.assertFalse(roteiro.is_roteiro(self.project))


if __name__ == "__main__":
    unittest.main()
