"""O próximo passo acompanha o fluxo real, mesmo sem BRIEF.md, e nunca anda em círculo."""

import json
import shlex
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video

from getbrolls.cli import build_parser
from getbrolls.commands import status_next
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


def with_brief_state(**counts):
    state = no_brief_state(**counts)
    state["brief"] = {"beats": 1, "covered": 1, "missing": [], "conflicts": []}
    return state


class ApprovedItemsComeBeforeLeftoverPreviews(unittest.TestCase):
    """M-2: depois do `approve`, o próximo passo pedia prévia dos candidatos que sobraram."""

    DOWNSTREAM: tuple[tuple[str, dict[str, int], int], ...] = (
        ("permit", {"approved": 1}, 0),
        ("fetch", {"approved": 1, "permitted": 1}, 0),
        ("verify", {"approved": 1, "permitted": 1, "delivered": 1}, 0),
        ("deliver", {"approved": 1, "permitted": 1, "delivered": 1, "verified": 1}, 1),
    )

    def test_do_sends_approved_items_down_the_flow_before_previewing_leftovers(self):
        for step, counts, undelivered in self.DOWNSTREAM:
            for factory in (with_brief_state, no_brief_state):
                with self.subTest(step=step, brief=factory.__name__):
                    state = factory(candidates=4, previews=1, **counts)
                    state.update(pending_preview=3, undelivered=undelivered)
                    state.update(duration_unknown=3, inspect_candidate="youtube:sobra")
                    self.assertEqual(step, next_action(state)["step"])

    def test_next_names_the_same_downstream_step(self):
        for step, counts, undelivered in self.DOWNSTREAM:
            with self.subTest(step=step):
                full = dict(no_brief_state(candidates=4, previews=1, **counts)["counts"])
                phrase = status_next(full, 0, pending_preview=3, undelivered=undelivered)
                self.assertIn(step, phrase)
                self.assertNotIn("Gere prévias", phrase)

    def test_without_any_approval_leftovers_still_get_a_preview(self):
        state = with_brief_state(candidates=4, previews=0)
        state["pending_preview"] = 4
        self.assertEqual("preview", next_action(state)["step"])
        counts = dict(state["counts"])
        self.assertIn("Gere prévias", status_next(counts, 0, pending_preview=4, undelivered=0))


LOCAL_BRIEF = {
    "version": 1,
    "video": {
        "title": "Vídeo de teste",
        "objective": "Conferir que o próximo passo avança",
        "audience": "Testes",
        "delivery": {"format": "native", "duration_s": 30, "platform": "instagram"},
    },
    "rights": {"posture": "per_item_evidence", "stock_allowed": False, "notes": None},
    "defaults": {"allowed_sources": ["local"], "intent": "literal", "duration_hint_s": 3, "stock": False},
    "beats": [
        {
            "id": "abertura",
            "narration": "O padrão de teste aparece na tela.",
            "target": "Padrão de teste colorido",
        }
    ],
}

# O que só o humano diria: o teste faz o papel dele e preenche os lugares em MAIÚSCULAS.
HUMAN_FILLS = {
    '"FRASE EXATA DITA POR ELE"': "Aprovo este trecho para o vídeo.",
    "NOME": "Fixture Humano",
    "EVIDENCIA_REAL": "Vídeo sintético gerado localmente pelo teste",
}
LADDER_ORDER = ("preview", "approve", "permit", "fetch", "verify", "deliver", "done")


@skip_unless_ffmpeg
class FollowingDoFinishesTheFlow(unittest.TestCase):
    """Seguir `summary.do` ao pé da letra leva do candidato à entrega, sem voltar atrás."""

    def test_following_summary_do_reaches_done_and_never_repeats_a_step(self):
        parser = build_parser()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BRIEF.md").write_text(
                "---\ntype: brief\n---\n\n```json\n" + json.dumps(LOCAL_BRIEF, ensure_ascii=False) + "\n```\n",
                encoding="utf-8",
            )
            for name in ("a.mp4", "b.mp4"):
                synth_video(
                    root / name,
                    size="160x90",
                    duration=6,
                    rate=10,
                    pattern="testsrc" if name == "a.mp4" else "testsrc2",
                )
                run_cli("resolve", "--file", root / name, "--shot", "abertura", project=root)
            seen = []
            for _ in range(12):
                action = run_cli("status", project=root)["summary"]["do"]
                seen.append(action["step"])
                if action["step"] == "done":
                    break
                self.assertIn(action["step"], LADDER_ORDER, seen)
                command = action["command"]
                for placeholder, value in HUMAN_FILLS.items():
                    command = command.replace(placeholder, shlex.quote(value))
                argv = shlex.split(command)[2:]
                parser.parse_args(argv)
                run_cli(*argv)
            self.assertEqual("done", seen[-1], seen)
            positions = [LADDER_ORDER.index(step) for step in seen]
            # Cada degrau aparece uma vez e só para frente: nada de laço nem de volta.
            self.assertEqual(sorted(set(positions)), positions, seen)
            self.assertIn("permit", seen)
            self.assertEqual("permit", seen[seen.index("approve") + 1], seen)


if __name__ == "__main__":
    unittest.main()
