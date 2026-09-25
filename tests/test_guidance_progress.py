"""O próximo passo acompanha o fluxo real, mesmo sem BRIEF.md, e nunca anda em círculo."""

import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video

from getbrolls.guidance import next_action

PROJECT = "/tmp/projeto-sem-brief"
NO_BRIEF_OPENING = "Antes de buscar qualquer coisa"


def no_brief_state(**counts):
    base: dict[str, int] = dict.fromkeys(
        ("candidates", "previews", "pending", "rejected", "approved", "permitted", "delivered", "verified"), 0
    )
    base.update(counts)
    return {
        "project": PROJECT,
        "counts": base,
        "format_pending": 0,
        "brief": None,
        "review_page": False,
        "rights_mode": "per_item_evidence",
        "undelivered": 0,
    }


class NoBriefFollowsTheRealStage(unittest.TestCase):
    """M-1: sem BRIEF.md, `do` repetia "Antes de buscar..." até depois da entrega."""

    def test_an_empty_project_still_starts_with_the_brief(self):
        action = next_action(no_brief_state())
        self.assertEqual("init-brief", action["step"])
        self.assertIn(NO_BRIEF_OPENING, action["for_human"])

    def test_after_the_human_approved_the_ladder_follows_the_real_stage(self):
        cases = {
            "permit": {"candidates": 1, "previews": 1, "approved": 1},
            "fetch": {"candidates": 1, "previews": 1, "approved": 1, "permitted": 1},
            "verify": {"candidates": 1, "previews": 1, "approved": 1, "permitted": 1, "delivered": 1},
            "done": {"candidates": 1, "previews": 1, "approved": 1, "permitted": 1, "delivered": 1, "verified": 1},
        }
        for step, counts in cases.items():
            with self.subTest(step=step):
                action = next_action(no_brief_state(**counts))
                self.assertEqual(step, action["step"])
                self.assertNotIn(NO_BRIEF_OPENING, action["for_human"])

    def test_verified_but_not_in_entrega_asks_for_deliver(self):
        state = no_brief_state(candidates=1, previews=1, approved=1, permitted=1, delivered=1, verified=1)
        state["undelivered"] = 1
        self.assertEqual("deliver", next_action(state)["step"])

    def test_with_candidates_the_brief_suggestion_does_not_claim_nothing_was_searched(self):
        # Antes da primeira aprovação o plano ainda ajuda a escolher; a frase só não
        # pode dizer "antes de buscar" para quem já buscou.
        action = next_action(no_brief_state(candidates=2))
        self.assertEqual("init-brief", action["step"])
        self.assertIn("/get-brolls-brief", action["for_human"])
        self.assertNotIn(NO_BRIEF_OPENING, action["for_human"])


@skip_unless_ffmpeg
class NoBriefDeliveryReadme(unittest.TestCase):
    def test_deliver_and_the_readme_close_the_flow_without_asking_for_a_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "original.mp4"
            synth_video(src, size="160x90", duration=3, rate=10)
            cid = run_cli("resolve", "--file", src, project=root)["id"]
            run_cli("preview", "--candidate", cid, "--start", 0.5, "--end", 1.5, project=root)
            run_cli(
                "approve",
                "--candidate",
                cid,
                "--by",
                "Fixture Humano",
                "--channel",
                "chat",
                "--statement",
                "Aprovo este trecho.",
                project=root,
            )
            self.assertEqual("permit", run_cli("status", project=root)["summary"]["do"]["step"])
            run_cli("permit", "--candidate", cid, "--evidence", "Vídeo sintético local", project=root)
            run_cli("fetch", "--candidate", cid, project=root)
            run_cli("verify", project=root)
            delivered = run_cli("deliver", project=root)
            self.assertNotIn(NO_BRIEF_OPENING, delivered["summary"]["next"])
            self.assertIn("Fluxo completo", delivered["summary"]["next"])
            readme = (root / "entrega" / "README.md").read_text(encoding="utf-8")
            self.assertNotIn(NO_BRIEF_OPENING, readme)
            self.assertEqual("done", run_cli("status", project=root)["summary"]["do"]["step"])


if __name__ == "__main__":
    unittest.main()
