"""Sync do roteiro para o BRIEF.md: revisão, ids, merge, portão de alvo, ordem de gravação e plano sem escrita."""

import json
import os
import re
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import ledger as ledger_module
from getbrolls import models, roteiro, roteiro_commands, roteiro_ids, roteiro_review, roteiro_sync
from getbrolls.commands import brief_report, status_report
from getbrolls.ledger import Ledger
from getbrolls.rules import load_rules

BRIEF = {
    "version": 1,
    "video": {"title": "T", "objective": "O", "audience": None,
              "delivery": {"format": "reels", "duration_s": 45, "platform": None}},
    "rights": {"posture": "per_item_evidence", "stock_allowed": False, "notes": None},
    "defaults": {"allowed_sources": ["youtube"], "intent": "literal", "duration_hint_s": 4, "stock": False},
    "beats": [{"id": "manual-1", "target": "algo manual", "queries": ["q"]}],
}  # fmt: skip
ROTEIRO = (
    '---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n\n'
    "## Gancho\n[A-ROLL]\nOi.\n\n"
    "## Problema\n[BROLL: timeline cheia]\nTodo mundo trava.\n\n"
    "## Prova\n[SPLIT: tela | mapa]\nProva.\n"
)
PROSE = "# Brief\n\nTexto da pessoa.\n\n```json\n{}\n```\n\nFim da prosa.\n"


def write_rules(project, video_format="reels", declaration=False):
    raw = (ROOT / "docs" / "RULES.md").read_text(encoding="utf-8")
    block = re.search(r"```json\s*\n(.*?)\n```", raw, re.DOTALL)
    assert block is not None
    data = json.loads(block.group(1))
    data["video_format"] = video_format
    if declaration:
        data["copyright"] = {
            "mode": "user_declaration",
            "responsible_person": "Bruno Moreira",
            "declaration": "Assumo.",
        }
    text = raw[: block.start(1)] + json.dumps(data, ensure_ascii=False, indent=2) + raw[block.end(1) :]
    (Path(project) / "RULES.md").write_text(text, encoding="utf-8")


def tree(root):
    """Retrato completo da pasta: caminho → bytes (None para pasta)."""
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


class SyncCase(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-sync-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        write_rules(self.project)
        self.write_brief(BRIEF)
        self.write(ROTEIRO)

    def write_brief(self, data):
        text = PROSE.replace("{}", json.dumps(data, ensure_ascii=False, indent=2))
        (self.project / "BRIEF.md").write_text(text, encoding="utf-8")

    def write(self, text):
        (self.project / "ROTEIRO.md").write_text(text, encoding="utf-8")

    def text(self, name):
        return (self.project / name).read_text(encoding="utf-8")

    def doc(self):
        return roteiro.parse(roteiro.load_text(self.project), plugins=frozenset())

    def review(self):
        roteiro_review.record_review(self.project, self.doc(), "Bruno Moreira", "chat", "pode seguir")

    def edit(self, old, new):
        text = self.text("ROTEIRO.md")
        self.assertIn(old, text)
        self.write(text.replace(old, new))

    def beats(self):
        return json.loads(self.text("BRIEF.md").split("```json\n")[1].split("\n```")[0])["beats"]

    def sync(self, **kwargs):
        return roteiro_sync.run(self.project, write=True, plugins=frozenset(), **kwargs)

    def plan(self):
        return roteiro_sync.run(self.project, write=False, plugins=frozenset())

    def approved(self, shot, source_id="abc"):
        ledger = Ledger(self.project)
        c = models.candidate("youtube", source_id, "t", f"https://www.youtube.com/watch?v={source_id}")
        c["shot"] = shot
        models.set_segment(c, 0, 4)
        models.approve(c, "Bruno Moreira", "chat", "aprovo")
        ledger.add(c)
        ledger.save("test", c)
        return c["id"]

    def approval(self, candidate_id):
        return Ledger(self.project, recover=False).get(candidate_id)["approval"]["status"]


class GuardTests(SyncCase):
    def test_sync_refuses_without_review_and_writes_nothing(self):
        before = tree(self.project)
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn("revisão humana", str(ctx.exception))
        self.assertEqual(before, tree(self.project))

    def test_sync_refuses_without_brief(self):
        (self.project / "BRIEF.md").unlink()
        self.review()
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn("/get-brolls-brief", str(ctx.exception))

    def test_brief_outside_the_project_is_refused(self):
        other = Path(tempfile.mkdtemp(prefix="gb-other-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        shutil.copyfile(self.project / "BRIEF.md", other / "BRIEF.md")
        self.review()
        with (
            mock.patch.dict(os.environ, {"GB_BRIEF_FILE": str(other / "BRIEF.md")}),
            self.assertRaises(ValueError) as ctx,
        ):
            self.sync()
        self.assertIn("GB_BRIEF_FILE aponta para fora do projeto", str(ctx.exception))

    def test_broken_old_brief_is_a_clean_error(self):
        self.review()
        (self.project / "BRIEF.md").write_text("# Brief\n\n```json\n{ quebrado\n```\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn("erro de digitação", str(ctx.exception))
        broken = {k: v for k, v in BRIEF.items() if k != "rights"}
        self.write_brief(broken)
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn('"rights"', str(ctx.exception))

    def test_user_declaration_brief_syncs_with_the_project_rules(self):
        write_rules(self.project, declaration=True)
        self.write_brief({**BRIEF, "rights": {**BRIEF["rights"], "posture": "user_declaration"}})
        self.review()
        self.assertEqual(self.sync()["new"], ["c02", "c03-a", "c03-b"])

    def test_aspect_mismatch_blocks_sync_but_plan_reports_it(self):
        write_rules(self.project, video_format="native")
        self.review()
        self.assertIn("init-rules --format reels", " ".join(self.plan()["problems"]))
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn("init-rules --format reels", str(ctx.exception))

    def test_invalid_component_blocks_sync_but_not_plan(self):
        self.edit("[A-ROLL]\nOi.", "[A-ROLL]\n[SFX: ../x]\nOi.")
        self.review()
        self.assertIn("linha 9", " ".join(self.plan()["problems"]))
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn("não grava nada", str(ctx.exception))


class WriteTests(SyncCase):
    def test_sync_assigns_ids_writes_beats_and_keeps_prose(self):
        self.review()
        report = self.sync()
        roteiro_text = self.text("ROTEIRO.md")
        self.assertIn("## Gancho <!-- c01 -->", roteiro_text)
        self.assertIn("## Prova <!-- c03 -->", roteiro_text)
        self.assertEqual([b["id"] for b in self.beats()], ["c02", "c03-a", "c03-b", "manual-1"])
        self.assertEqual(self.beats()[-1]["queries"], ["q"])
        self.assertEqual(report["new"], ["c02", "c03-a", "c03-b"])
        self.assertTrue(report["order_changed"])
        self.assertIn("renumera", " ".join(report["warnings"]))
        brief_text = self.text("BRIEF.md")
        self.assertTrue(brief_text.startswith("# Brief\n\nTexto da pessoa.\n\n```json\n"))
        self.assertTrue(brief_text.endswith("\n```\n\nFim da prosa.\n"))
        self.assertTrue((self.project / "BRIEF.md.sync.bak").is_file())
        self.assertEqual((self.project / "ROTEIRO.md.sync.bak").read_text(encoding="utf-8"), ROTEIRO)
        self.assertFalse((self.project / "BRIEF.md.bak").exists())
        self.assertFalse((self.project / "ROTEIRO.md.bak").exists())
        self.assertTrue(roteiro_review.review_state(self.project, self.doc())["reviewed"])
        self.assertEqual(roteiro_ids.read_state(self.project)["next_id"], 4)
        self.assertEqual(set(roteiro_ids.read_state(self.project)["scenes"]), {"c01", "c02", "c03"})

    def test_empty_beats_from_the_interview_are_filled(self):
        self.write_brief({**BRIEF, "beats": []})
        self.review()
        self.sync()
        self.assertEqual([b["id"] for b in self.beats()], ["c02", "c03-a", "c03-b"])

    def test_second_sync_is_a_no_op_and_keeps_fields_the_sync_does_not_own(self):
        self.review()
        self.sync()
        data = json.loads(self.text("BRIEF.md").split("```json\n")[1].split("\n```")[0])
        data["beats"][0]["queries"] = ["minha busca"]
        data["beats"][0]["intent"] = "illustrative"
        self.write_brief(data)
        self.sync()
        first = (self.text("BRIEF.md"), self.text("ROTEIRO.md"))
        again = self.sync()
        self.assertEqual(again["written"], [])
        self.assertEqual(first, (self.text("BRIEF.md"), self.text("ROTEIRO.md")))
        self.assertEqual((self.beats()[0]["queries"], self.beats()[0]["intent"]), (["minha busca"], "illustrative"))

    def test_plan_is_strictly_read_only(self):
        self.review()
        self.approved("manual-1")
        before = tree(self.project)
        report = self.plan()
        self.assertEqual(before, tree(self.project))
        self.assertEqual(report["written"], [])
        self.assertEqual(report["ids_to_assign"], {"7": "c01", "11": "c02", "15": "c03"})
        self.assertEqual(report["new"], ["c02", "c03-a", "c03-b"])

    def test_plan_without_brolls_creates_nothing(self):
        before = tree(self.project)
        self.plan()
        self.assertEqual(before, tree(self.project))
        self.assertFalse((self.project / "brolls").exists())

    def test_plan_refuses_a_pending_transaction(self):
        (self.project / "brolls").mkdir()
        (self.project / "brolls" / roteiro_sync.PENDING).write_text("{}", encoding="utf-8")
        before = tree(self.project)
        with self.assertRaises(ValueError) as ctx:
            self.plan()
        self.assertIn("gravação interrompida", str(ctx.exception))
        self.assertEqual(before, tree(self.project))

    def test_removed_scene_is_retired_and_its_id_is_not_reused(self):
        self.review()
        self.sync()
        text = self.text("ROTEIRO.md")
        self.write(text.split("## Prova")[0] + "## Nova\n[BROLL: outra]\nNova fala.\n")
        self.review()
        report = self.sync()
        self.assertEqual(report["retired"], ["c03-a", "c03-b"])
        self.assertIn("## Nova <!-- c04 -->", self.text("ROTEIRO.md"))
        beats = {b["id"]: b for b in self.beats()}
        self.assertTrue(beats["c03-a"]["retired"])
        self.assertNotIn("retired", beats["c04"])
        self.assertEqual(report["beats"], ["c02", "c04", "manual-1"])

    def test_scene_coming_back_reactivates_its_beat(self):
        self.review()
        self.sync()
        full = self.text("ROTEIRO.md")
        self.write(full.split("## Prova")[0])
        self.review()
        self.sync()
        self.write(full)
        self.review()
        report = self.sync()
        self.assertEqual(report["reactivated"], ["c03-a", "c03-b"])
        self.assertNotIn("retired", {k for b in self.beats() for k in b})

    def test_new_scene_after_a_retirement_gets_a_fresh_id(self):
        self.review()
        self.sync()
        self.approved("c02")
        text = self.text("ROTEIRO.md")
        start, end = text.index("## Problema"), text.index("## Prova")
        self.write(text[:start] + text[end:])
        self.review()
        self.assertEqual(self.sync()["retired"], ["c02"])
        self.write(self.text("ROTEIRO.md") + "\n## Nova\n[BROLL: praia]\nFala nova.\n")
        self.review()
        planned = self.plan()
        self.assertIsNone(planned["refusal"])
        self.assertEqual(planned["new"], ["c04"])
        report = self.sync()
        self.assertIn("## Nova <!-- c04 -->", self.text("ROTEIRO.md"))
        self.assertNotIn("<!-- c02 -->", self.text("ROTEIRO.md"))
        self.assertEqual(report["beats"], ["c03-a", "c03-b", "c04", "manual-1"])


class ApprovalGateTests(SyncCase):
    def setUp(self):
        super().setUp()
        self.review()
        self.sync()
        self.candidate = self.approved("c02")

    def test_speech_change_only_warns_and_keeps_the_approval(self):
        self.edit("Todo mundo trava.", "Todo mundo trava sempre.")
        self.review()
        report = self.sync()
        self.assertEqual((report["speech_changed"], report["target_changed"]), (["c02"], []))
        self.assertIn("fala mudou em c02", " ".join(report["warnings"]))
        self.assertEqual(report["invalidated"], [])
        self.assertEqual(self.approval(self.candidate), "approved")
        self.assertEqual(self.beats()[0]["narration"], "Todo mundo trava sempre.")

    def test_target_change_is_listed_by_plan_and_gated_by_sync(self):
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.review()
        planned = self.plan()
        self.assertEqual(planned["affected_approvals"], [{"candidate": self.candidate, "beat": "c02"}])
        before = tree(self.project)
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn(self.candidate, str(ctx.exception))
        self.assertIn("--confirm-target-change", str(ctx.exception))
        self.assertEqual(before, tree(self.project))
        report = self.sync(confirm=True)
        self.assertEqual(report["invalidated"], [self.candidate])
        self.assertEqual(self.approval(self.candidate), "pending")
        events = (self.project / "brolls" / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"operation": "roteiro_target_changed"', events)
        self.assertEqual(self.beats()[0]["target"], "mesa de edição")

    def test_manifest_is_journaled_before_the_brief_is_written(self):
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.review()
        brief_before = self.text("BRIEF.md")
        real = roteiro_sync.atomic_write

        def fail_on_brief(path, text):
            if Path(path).name == "BRIEF.md":
                raise OSError("disco cheio")
            real(path, text)

        with mock.patch.object(roteiro_sync, "atomic_write", side_effect=fail_on_brief), self.assertRaises(OSError):
            self.sync(confirm=True)
        self.assertEqual(self.approval(self.candidate), "pending")
        self.assertEqual(self.text("BRIEF.md"), brief_before)

    def test_new_id_with_old_material_goes_through_the_gate(self):
        old = self.approved("c05", source_id="old")
        self.edit(
            "## Prova <!-- c03 -->", "## Volta <!-- c05 -->\n[BROLL: outra coisa]\nVolta.\n\n## Prova <!-- c03 -->"
        )
        self.review()
        self.assertEqual(self.plan()["affected_approvals"], [{"candidate": old, "beat": "c05"}])
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn(old, str(ctx.exception))

    def test_lost_id_comment_is_readopted_or_refused(self):
        self.approved("c03-a", source_id="prova")
        self.edit("## Prova <!-- c03 -->", "## Prova")
        self.review()
        report = self.sync()
        self.assertEqual(report["readopted"], {"15": "c03"})
        self.assertIn("## Prova <!-- c03 -->", self.text("ROTEIRO.md"))
        self.edit("## Prova <!-- c03 -->\n[SPLIT: tela | mapa]", "## Prova\n[SPLIT: tela | mapa novo]")
        self.review()
        planned = self.plan()
        self.assertIn("c03", planned["refusal"])
        self.assertEqual(planned["new"], [])
        with self.assertRaises(ValueError):
            self.sync()


class CarriedRequirementTests(SyncCase):
    """Requisitos das revisões das tarefas 6, 7 e 8 que o plano não cobria."""

    def pending_candidate(self, shot, source_id):
        ledger = Ledger(self.project)
        c = models.candidate("youtube", source_id, "t", f"https://www.youtube.com/watch?v={source_id}")
        c["shot"] = shot
        ledger.add(c)
        ledger.save("test", c)
        return c["id"]

    def test_brief_outside_the_project_names_the_fix(self):
        other = Path(tempfile.mkdtemp(prefix="gb-other-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        shutil.copyfile(self.project / "BRIEF.md", other / "BRIEF.md")
        self.review()
        before = tree(self.project)
        with mock.patch.dict(os.environ, {"GB_BRIEF_FILE": str(other / "BRIEF.md")}):
            with self.assertRaises(ValueError) as ctx:
                self.sync()
            with self.assertRaises(ValueError):
                self.plan()
        self.assertIn("apague a variável GB_BRIEF_FILE", str(ctx.exception))
        self.assertIn("mova o BRIEF.md para dentro do projeto", str(ctx.exception))
        self.assertEqual(before, tree(self.project))
        self.assertEqual((other / "BRIEF.md").read_bytes(), (self.project / "BRIEF.md").read_bytes())

    def test_brief_override_pointing_into_the_project_is_accepted(self):
        self.review()
        with mock.patch.dict(os.environ, {"GB_BRIEF_FILE": str(self.project / "BRIEF.md")}):
            self.assertEqual(self.sync()["new"], ["c02", "c03-a", "c03-b"])

    def test_brief_symlinked_from_outside_is_refused(self):
        other = Path(tempfile.mkdtemp(prefix="gb-other-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        shutil.move(self.project / "BRIEF.md", other / "BRIEF.md")
        (self.project / "BRIEF.md").symlink_to(other / "BRIEF.md")
        self.review()
        before = (other / "BRIEF.md").read_bytes()
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn("link para fora do projeto", str(ctx.exception))
        self.assertEqual(before, (other / "BRIEF.md").read_bytes())

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_brief_symlinked_inside_the_project_is_refused_as_a_link(self):
        (self.project / "sub").mkdir()
        shutil.move(self.project / "BRIEF.md", self.project / "sub" / "brief.md")
        (self.project / "BRIEF.md").symlink_to(self.project / "sub" / "brief.md")
        self.review()
        before = tree(self.project)
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        message = str(ctx.exception)
        self.assertIn("BRIEF.md do projeto é um link", message)
        self.assertIn("arquivo de verdade", message)
        self.assertNotIn("fora do projeto", message)
        self.assertEqual(before, tree(self.project))
        self.assertTrue((self.project / "BRIEF.md").is_symlink())

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_roteiro_symlink_is_written_through_and_stays_a_link(self):
        vault = Path(tempfile.mkdtemp(prefix="gb-vault-"))
        self.addCleanup(shutil.rmtree, vault, ignore_errors=True)
        shutil.move(self.project / "ROTEIRO.md", vault / "nota.md")
        (self.project / "ROTEIRO.md").symlink_to(vault / "nota.md")
        self.review()
        report = self.sync()
        self.assertTrue((self.project / "ROTEIRO.md").is_symlink())
        self.assertEqual((self.project / "ROTEIRO.md").resolve(), (vault / "nota.md").resolve())
        self.assertIn("## Gancho <!-- c01 -->", (vault / "nota.md").read_text(encoding="utf-8"))
        self.assertEqual((self.project / "ROTEIRO.md.sync.bak").read_text(encoding="utf-8"), ROTEIRO)
        self.assertFalse((vault / "nota.md.tmp").exists())
        self.assertIn("ROTEIRO.md", report["written"])

    def test_sync_never_touches_the_backups_of_new_force(self):
        (self.project / "ROTEIRO.md.bak").write_bytes(b"roteiro antigo\r\n")
        (self.project / "BRIEF.md.bak").write_bytes(b"brief antigo\n")
        self.review()
        self.sync()
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.write(self.text("ROTEIRO.md") + "\n## Nova\n[BROLL: x]\nNova.\n")
        self.review()
        self.sync()
        self.assertEqual((self.project / "ROTEIRO.md.bak").read_bytes(), b"roteiro antigo\r\n")
        self.assertEqual((self.project / "BRIEF.md.bak").read_bytes(), b"brief antigo\n")

    def test_manifest_is_saved_before_the_roteiro_is_written(self):

        self.review()
        self.sync()
        self.approved("c02")
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.write(self.text("ROTEIRO.md") + "\n## Nova\n[BROLL: x]\nNova.\n")
        self.review()
        order = []
        real_ledger, real_sync = ledger_module.atomic_write, roteiro_sync.atomic_write

        def spy(real):
            def write(path, text):
                order.append(Path(path).name)
                real(path, text)

            return write

        with (
            mock.patch.object(ledger_module, "atomic_write", side_effect=spy(real_ledger)),
            mock.patch.object(roteiro_sync, "atomic_write", side_effect=spy(real_sync)),
        ):
            self.sync(confirm=True)
        self.assertLess(order.index(roteiro_sync.PENDING), order.index("manifest.json"))
        self.assertLess(order.index("manifest.json"), order.index("ROTEIRO.md"))
        self.assertLess(order.index("ROTEIRO.md"), order.index("BRIEF.md"))

    def test_crash_after_the_manifest_converges_without_a_second_confirmation(self):
        self.review()
        self.sync()
        candidate = self.approved("c02")
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.write(self.text("ROTEIRO.md") + "\n## Nova\n[BROLL: x]\nNova.\n")
        self.review()
        real = roteiro_sync.atomic_write

        def crash_on_roteiro(path, text):
            if Path(path).name == "ROTEIRO.md":
                raise OSError("queda")
            real(path, text)

        with mock.patch.object(roteiro_sync, "atomic_write", side_effect=crash_on_roteiro), self.assertRaises(OSError):
            self.sync(confirm=True)
        self.assertEqual(self.approval(candidate), "pending")
        self.assertNotIn("<!-- c04 -->", self.text("ROTEIRO.md"))
        report = self.sync()
        self.assertEqual((report["invalidated"], report["affected_approvals"]), ([], []))
        self.assertIn("## Nova <!-- c04 -->", self.text("ROTEIRO.md"))
        self.assertEqual(self.beats()[0]["target"], "mesa de edição")
        self.assertEqual(self.approval(candidate), "pending")
        before = tree(self.project)
        self.assertEqual(self.sync()["written"], [])
        self.assertEqual(before, tree(self.project))

    def test_refused_plan_does_not_report_zero_beats(self):
        self.review()
        self.sync()
        self.approved("c03-a", source_id="prova")
        self.edit("## Prova <!-- c03 -->\n[SPLIT: tela | mapa]", "## Prova\n[SPLIT: tela | mapa novo]")
        self.review()
        planned = self.plan()
        self.assertIsNotNone(planned["refusal"])
        self.assertIsNone(planned["beats"])
        self.assertIsNone(planned["total_s"])

    def test_refused_ids_write_nothing_and_plan_does_not_raise(self):
        self.review()
        self.sync()
        self.approved("c03-a", source_id="prova")
        self.edit("## Prova <!-- c03 -->\n[SPLIT: tela | mapa]", "## Prova\n[SPLIT: tela | mapa novo]")
        self.review()
        before = tree(self.project)
        planned = self.plan()
        self.assertIn("c03", planned["refusal"])
        self.assertEqual(planned["written"], [])
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertEqual(str(ctx.exception), planned["refusal"])
        self.assertEqual(before, tree(self.project))

    def test_no_op_sync_writes_nothing_at_all(self):
        self.review()
        self.sync()
        before = tree(self.project)
        report = self.sync()
        self.assertEqual(report["written"], [])
        self.assertEqual(before, tree(self.project))

    def test_state_is_written_last(self):
        self.review()
        order = []
        real_write, real_state = roteiro_sync.atomic_write, roteiro_ids.write_state

        def spy_write(path, text):
            order.append(Path(path).name)
            real_write(path, text)

        def spy_state(project, state):
            order.append(roteiro_ids.STATE_FILE)
            real_state(project, state)

        with (
            mock.patch.object(roteiro_sync, "atomic_write", side_effect=spy_write),
            mock.patch.object(roteiro_ids, "write_state", side_effect=spy_state),
        ):
            report = self.sync()
        self.assertEqual(order, ["ROTEIRO.md", "BRIEF.md", roteiro_ids.STATE_FILE])
        self.assertEqual(report["written"], ["ROTEIRO.md", "BRIEF.md", f"brolls/{roteiro_ids.STATE_FILE}"])

    def test_ids_reserved_by_retired_beats_and_any_manifest_item_are_not_reused(self):
        self.review()
        self.sync()
        text = self.text("ROTEIRO.md")
        self.write(text.split("## Prova")[0])
        self.review()
        self.sync()  # c03-a / c03-b aposentados
        (self.project / "brolls" / roteiro_ids.STATE_FILE).unlink()
        self.write(self.text("ROTEIRO.md") + "## Nova\n[BROLL: outra]\nNova.\n")
        self.review()
        self.assertEqual(list(self.plan()["ids_to_assign"].values()), ["c04"])
        self.pending_candidate("c07", "rejeitado")
        self.assertEqual(list(self.plan()["ids_to_assign"].values()), ["c08"])

    def test_unreadable_state_is_a_clean_error_and_writes_nothing(self):
        self.review()
        (self.project / "brolls" / roteiro_ids.STATE_FILE).write_text("{ quebrado", encoding="utf-8")
        before = tree(self.project)
        for call in (self.plan, self.sync):
            with self.assertRaises(ValueError) as ctx:
                call()
            self.assertIn("ilegível", str(ctx.exception))
        self.assertEqual(before, tree(self.project))

    def test_id_ceiling_is_a_clean_error_and_writes_nothing(self):
        self.review()
        roteiro_ids.write_state(self.project, {"next_id": 1000, "scenes": {}})
        before = tree(self.project)
        for call in (self.plan, self.sync):
            with self.assertRaises(ValueError) as ctx:
                call()
            self.assertIn("c999", str(ctx.exception))
        self.assertEqual(before, tree(self.project))

    def test_target_change_without_approval_is_a_visible_warning(self):
        self.review()
        self.sync()
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.review()
        report = self.sync()
        self.assertEqual((report["target_changed"], report["affected_approvals"]), (["c02"], []))
        self.assertIn("alvo mudou em c02", " ".join(report["warnings"]))

    def test_swapped_id_comments_keep_the_review_but_not_silently(self):
        self.edit("## Gancho\n[A-ROLL]", "## Gancho\n[BROLL: café]")
        self.review()
        self.sync()
        candidate = self.approved("c02")
        text = self.text("ROTEIRO.md")
        swapped = text.replace("<!-- c01 -->", "<!-- X -->").replace("<!-- c02 -->", "<!-- c01 -->")
        self.write(swapped.replace("<!-- X -->", "<!-- c02 -->"))
        self.assertTrue(roteiro_review.review_state(self.project, self.doc())["reviewed"])
        planned = self.plan()
        self.assertEqual(planned["target_changed"], ["c02", "c01"])
        self.assertEqual(planned["affected_approvals"], [{"candidate": candidate, "beat": "c02"}])
        self.assertIn("alvo mudou em c01", " ".join(planned["warnings"]))
        with self.assertRaises(ValueError):
            self.sync()

    def test_readopted_id_goes_through_the_gate(self):
        self.review()
        self.sync()
        candidate = self.approved("c03-a", source_id="prova")
        data = json.loads(self.text("BRIEF.md").split("```json\n")[1].split("\n```")[0])
        data["beats"][1]["target"] = "tela antiga"
        self.write_brief(data)
        self.edit("## Prova <!-- c03 -->", "## Prova")
        self.review()
        planned = self.plan()
        self.assertEqual(planned["readopted"], {"15": "c03"})
        self.assertEqual(planned["affected_approvals"], [{"candidate": candidate, "beat": "c03-a"}])
        before = tree(self.project)
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn(candidate, str(ctx.exception))
        self.assertEqual(before, tree(self.project))

    def test_new_id_with_unapproved_material_is_a_visible_warning(self):
        self.review()
        self.sync()
        self.pending_candidate("c05", "velho")
        self.edit(
            "## Prova <!-- c03 -->", "## Volta <!-- c05 -->\n[BROLL: outra coisa]\nVolta.\n\n## Prova <!-- c03 -->"
        )
        self.review()
        report = self.sync()
        self.assertEqual((report["new"], report["affected_approvals"]), (["c05"], []))
        self.assertIn("c05 já tem candidatos no manifesto", " ".join(report["warnings"]))

    def test_sync_finishes_an_interrupted_write_like_every_writing_command(self):
        self.review()
        self.sync()
        ledger = Ledger(self.project)
        c = models.candidate("youtube", "journal", "t", "https://www.youtube.com/watch?v=journal")
        c["shot"] = "c02"
        ledger.add(c)
        with mock.patch.object(Ledger, "_finish_transaction", side_effect=OSError("queda")), self.assertRaises(OSError):
            ledger.save("test", c)
        pending = self.project / "brolls" / roteiro_sync.PENDING
        self.assertTrue(pending.exists())
        self.sync()
        self.assertFalse(pending.exists())
        self.assertEqual(Ledger(self.project, recover=False).get(c["id"])["shot"], "c02")

    def test_brief_block_is_replaced_even_with_a_bom_and_crlf(self):
        text = "﻿" + PROSE.replace("{}", json.dumps(BRIEF, ensure_ascii=False, indent=2)).replace("\n", "\r\n")
        (self.project / "BRIEF.md").write_bytes(text.encode("utf-8"))
        self.review()
        self.sync()
        self.assertEqual([b["id"] for b in self.beats()], ["c02", "c03-a", "c03-b", "manual-1"])
        self.assertTrue(self.text("BRIEF.md").startswith("﻿# Brief"))
        self.assertTrue(self.text("BRIEF.md").endswith("Fim da prosa.\n"))


class StatusDriftTests(SyncCase):
    """`status` só manda para o sync quando o roteiro está fora de sincronia com o brief."""

    AROLL_ONLY = (
        '---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n\n'
        "## Gancho\n[A-ROLL]\nOi.\n\n## CTA\n[FULL: logo]\nTchau.\n"
    )

    def status(self):

        return status_report(Ledger(self.project, recover=False), load_rules(self.project))["summary"]

    def brief_line(self):

        return brief_report(types.SimpleNamespace(project=str(self.project), validate=True, beat=None))["summary"]

    def test_roteiro_without_broll_leaves_the_sync_step_after_the_sync(self):
        self.write_brief({**BRIEF, "beats": []})
        self.write(self.AROLL_ONLY)
        # Sem revisão o sync recusaria: o degrau é o check (que mostra o hash do review).
        self.assertEqual(self.status()["do"]["step"], "roteiro-check")
        self.review()
        self.assertEqual(self.status()["do"]["step"], "roteiro-sync")
        self.sync()
        summary = self.status()
        self.assertNotEqual(summary["do"]["step"], "roteiro-sync")
        self.assertIn("o roteiro não pede b-roll", self.brief_line()["line"])
        self.write(self.text("ROTEIRO.md") + "\n## Prova\n[BROLL: mapa]\nProva.\n")
        self.assertEqual(self.status()["do"]["step"], "roteiro-check")
        self.review()
        self.assertEqual(self.status()["do"]["step"], "roteiro-sync")

    def test_status_is_read_only_while_checking_drift(self):
        self.write_brief({**BRIEF, "beats": []})
        self.write(self.AROLL_ONLY)
        self.review()
        self.sync()
        before = tree(self.project)
        self.status()
        self.brief_line()
        self.assertEqual(before, tree(self.project))

    def test_new_scene_after_sync_is_drift_even_with_active_beats(self):
        self.review()
        self.sync()
        self.assertNotEqual(self.status()["do"]["step"], "roteiro-sync")
        self.write(self.text("ROTEIRO.md") + "\n## Nova\n[BROLL: praia]\nFala.\n")
        self.assertEqual(self.status()["do"]["step"], "roteiro-check")
        self.review()
        self.assertEqual(self.status()["do"]["step"], "roteiro-sync")
        self.assertIn("mudou desde o último sync", " ".join(self.brief_line()["problems"]))

    def test_roteiro_without_scenes_converges_after_one_sync(self):
        self.write_brief({**BRIEF, "beats": []})
        self.write('---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n')
        self.review()
        self.sync()
        self.assertTrue(roteiro_ids.state_path(self.project).is_file())
        self.assertNotEqual(self.status()["do"]["step"], "roteiro-sync")


class PlanSafetyTests(SyncCase):
    """O que muda sem trocar o texto também anula a revisão e aparece no plano."""

    def test_brand_file_retiring_a_reviewed_beat_voids_the_review_and_warns(self):
        self.write(ROTEIRO + "\n## Fim\n[FULL: praia]\nTchau.\n")
        self.review()
        self.assertIn("c04", self.sync()["beats"])
        candidate = self.approved("c04")
        (self.project / "assets" / "marca").mkdir(parents=True)
        (self.project / "assets" / "marca" / "praia.png").write_bytes(b"x")
        self.assertFalse(self.plan()["reviewed"])
        with self.assertRaises(ValueError) as ctx:
            self.sync()
        self.assertIn("revisão humana", str(ctx.exception))
        self.review()
        planned = self.plan()
        report = self.sync()
        self.assertEqual(report["retired"], ["c04"])
        for warnings in (planned["warnings"], report["warnings"]):
            joined = " ".join(warnings)
            self.assertIn('FULL "praia" virou componente de marca porque assets/marca/praia existe', joined)
            self.assertNotIn("c04 saiu do roteiro", joined)
        self.assertEqual(self.approval(candidate), "approved")

    def test_target_change_names_the_stale_queries(self):
        self.review()
        self.sync()
        data = json.loads(self.text("BRIEF.md").split("```json\n")[1].split("\n```")[0])
        data["beats"][0]["queries"] = ["timeline cheia de cortes"]
        data["beats"][0]["notes"] = "cortes rápidos"
        self.write_brief(data)
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.review()
        stale = (
            "alvo mudou em c02: as buscas (queries) e as notas (notes) ainda são do alvo antigo — "
            "revise antes de buscar"
        )
        self.assertIn(stale, self.plan()["warnings"])
        self.assertIn(stale, self.sync()["warnings"])
        self.assertEqual(self.beats()[0]["queries"], ["timeline cheia de cortes"])

    def test_target_change_without_queries_keeps_the_short_warning(self):
        self.review()
        self.sync()
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.review()
        self.assertNotIn("queries", " ".join(self.plan()["warnings"]))

    def test_plan_summary_says_the_sync_will_refuse_without_review(self):

        args = types.SimpleNamespace(command="roteiro", action="plan", project=str(self.project))
        self.assertIn("o sync vai recusar: falta a revisão da pessoa", roteiro_commands.run(args)["summary"]["line"])
        self.review()
        self.assertNotIn("vai recusar", roteiro_commands.run(args)["summary"]["line"])


class LineEndingTests(SyncCase):
    """O que a revisão e o sync gravam sai com `\\n` puro: no Windows, o modo texto trocaria por `\\r\\n`."""

    @staticmethod
    def written(action):
        """`{nome: newline}` de cada arquivo que `action` abriu para gravar pelo `Path.open`."""
        real_open = Path.open
        seen = {}

        def spy(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if set(mode) & set("wax"):
                seen[path.name] = kwargs.get("newline")
            return real_open(path, *args, **kwargs)

        with mock.patch.object(Path, "open", autospec=True, side_effect=spy):
            action()
        return seen

    def test_review_and_sync_write_lf_on_every_system(self):
        self.assertEqual("\n", self.written(self.review)["roteiro-reviews.jsonl"])
        seen = self.written(self.sync)
        self.assertEqual(["\n", "\n"], [seen["ROTEIRO.md.tmp"], seen["BRIEF.md.tmp"]])
        for name in ("ROTEIRO.md", "BRIEF.md", "brolls/roteiro-reviews.jsonl"):
            self.assertNotIn(b"\r", (self.project / name).read_bytes(), name)

    def test_atomic_write_keeps_the_text_byte_for_byte(self):
        target = self.project / "nota.md"
        seen = self.written(lambda: ledger_module.atomic_write(target, "a\nb\r\nc\n"))
        self.assertEqual({"nota.md.tmp": "\n"}, seen)
        self.assertEqual(b"a\nb\r\nc\n", target.read_bytes())


if __name__ == "__main__":
    unittest.main()
