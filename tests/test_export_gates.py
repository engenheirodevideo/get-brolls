"""Portões do export: roteiro do get-brolls, UTF-8, BRIEF.md da pasta, problemas, revisão e sync em dia."""

import json
import os
from unittest import mock

from _isolation import GB_HOME  # efeito de import: define GB_HOME (e o teste o compara antes e depois)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)
from test_roteiro_sync import BRIEF, SyncCase, tree, write_rules

from getbrolls import export_gates, roteiro_sync


class GateCase(SyncCase):
    """Projeto de `test_roteiro_sync`: RULES.md em reels, BRIEF.md com um beat manual, roteiro de três cenas."""

    def ready(self):
        self.review()
        self.sync()

    def snapshot(self):
        """Projeto inteiro e GB_HOME inteiro: os portões só leem, passando ou recusando."""
        return tree(self.project), tree(GB_HOME)

    def check(self):
        before = self.snapshot()
        try:
            return export_gates.check(self.project, plugins=frozenset())
        finally:
            self.assertEqual(before, self.snapshot(), "os portões do export gravaram algo")

    def refused(self, fragment):
        with self.assertRaises(ValueError) as caught:
            self.check()
        self.assertIn(fragment, str(caught.exception))


class HappyPathTests(GateCase):
    def test_reviewed_and_synced_project_passes_with_plan_and_items(self):
        self.ready()
        found = self.check()
        self.assertEqual(["c01", "c02", "c03"], [s["id"] for s in found["plan"]["scenes"]])
        self.assertEqual([], found["items"])
        self.assertEqual(3, len(found["doc"].scenes))

    def test_items_come_from_the_manifest(self):
        self.ready()
        candidate_id = self.approved("c02")
        self.assertEqual([candidate_id], [c["id"] for c in self.check()["items"]])

    def test_gates_only_read(self):
        self.ready()
        before = {
            p.relative_to(self.project).as_posix(): p.read_bytes() for p in self.project.rglob("*") if p.is_file()
        }
        self.check()
        after = {p.relative_to(self.project).as_posix(): p.read_bytes() for p in self.project.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


class RoteiroGateTests(GateCase):
    def test_missing_roteiro_uses_the_load_text_message(self):
        (self.project / "ROTEIRO.md").unlink()
        self.refused("Este projeto não tem ROTEIRO.md")

    def test_non_utf8_gets_the_utf8_message_before_is_roteiro(self):
        (self.project / "ROTEIRO.md").write_bytes(b"---\ntype: roteiro\ntema: \xe9\n---\n")
        self.refused("não está em UTF-8")

    def test_someone_elses_roteiro_is_refused(self):
        self.write("# Meu roteiro\n\nTexto solto.\n")
        self.refused(export_gates.NOT_ROTEIRO)

    def test_pending_journal_is_refused_without_recovering(self):
        self.ready()
        pending = self.project / "brolls" / roteiro_sync.PENDING
        pending.write_text("{}", encoding="utf-8")
        self.refused("gravação interrompida")
        self.assertTrue(pending.exists())

    def test_roteiro_problems_are_listed(self):
        self.ready()
        write_rules(self.project, video_format="horizontal")
        self.refused("ROTEIRO.md com problema; o export não começa")


class BriefGateTests(GateCase):
    def test_gb_brief_file_is_refused_with_export_text(self):
        self.ready()
        outside = self.project.parent / f"{self.project.name}-brief.md"
        outside.write_text((self.project / "BRIEF.md").read_text(encoding="utf-8"), encoding="utf-8")
        self.addCleanup(outside.unlink)
        with mock.patch.dict(os.environ, {"GB_BRIEF_FILE": str(outside)}):
            self.refused(export_gates.BRIEF_OUTSIDE)

    def test_brief_link_is_refused_with_export_text(self):
        self.ready()
        real = self.project / "notas" / "brief-real.md"
        real.parent.mkdir()
        (self.project / "BRIEF.md").rename(real)
        try:
            (self.project / "BRIEF.md").symlink_to(real)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        self.refused(export_gates.BRIEF_OUTSIDE)

    def test_missing_brief_asks_for_the_sync(self):
        self.ready()
        (self.project / "BRIEF.md").unlink()
        self.refused(export_gates.OUT_OF_SYNC)


class ReviewAndSyncGateTests(GateCase):
    def test_unreviewed_roteiro_is_refused(self):
        self.refused("não tem revisão humana registrada")

    def test_never_synced_is_refused(self):
        self.review()
        self.refused(export_gates.OUT_OF_SYNC)

    def test_target_change_after_sync_is_refused(self):
        self.ready()
        self.edit("[BROLL: timeline cheia]", "[BROLL: timeline vazia]")
        self.review()
        self.refused(export_gates.OUT_OF_SYNC)

    def test_speech_change_after_sync_is_refused(self):
        self.ready()
        self.edit("Todo mundo trava.", "Todo mundo trava muito.")
        self.review()
        self.refused(export_gates.OUT_OF_SYNC)

    def test_new_scene_without_id_is_refused(self):
        self.ready()
        text = self.text("ROTEIRO.md") + "\n## Nova\n[BROLL: praia]\nMais.\n"
        self.write(text)
        self.review()
        self.refused(export_gates.OUT_OF_SYNC)

    def test_manual_beats_do_not_block(self):
        self.ready()
        beats = self.beats()
        self.assertIn("manual-1", [b["id"] for b in beats])
        self.assertEqual(BRIEF["beats"][0]["target"], next(b for b in beats if b["id"] == "manual-1")["target"])
        self.check()
        self.assertTrue(json.loads(json.dumps(self.check()["plan"])))
