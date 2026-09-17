"""Próximo passo humano: toda sugestão da escada é um comando que a CLI aceita."""

import shlex
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls.cli import build_parser
from getbrolls.guidance import STEPS, command_for, next_action

PROJECT = "/tmp/projeto-do-video"
# O comando sempre traz o caminho resolvido (no macOS /tmp é link para /private/tmp).
ABSOLUTE = str(Path(PROJECT).expanduser().resolve())


def base_state(**extra):
    state = {
        "project": PROJECT,
        "counts": dict.fromkeys(
            ("candidates", "previews", "approved", "permitted", "delivered", "verified"), 0
        ),
        "format_pending": 0,
        "brief": {"beats": 1, "covered": 1, "missing": [], "conflicts": []},
        "review_page": False,
        "rights_mode": "per_item_evidence",
    }
    state.update(extra)
    return state


def full(**counts):
    return {**base_state()["counts"], **counts}


# Um estado por degrau da escada, do topo para a base.
LADDER_STATES = {
    "init-brief": base_state(brief=None),
    "format": base_state(format_pending=2, counts=full(candidates=2, previews=2, approved=2)),
    "brief-search": base_state(
        brief={
            "beats": 2,
            "covered": 1,
            "missing": [{"id": "abertura", "search": None}],
            "conflicts": [],
        },
        counts=full(candidates=1, previews=1),
    ),
    "search": base_state(),
    "preview": base_state(counts=full(candidates=3)),
    "approve": base_state(counts=full(candidates=3, previews=3)),
    "permit": base_state(counts=full(candidates=3, previews=3, approved=3)),
    "fetch": base_state(counts=full(candidates=3, previews=3, approved=3, permitted=3)),
    "verify": base_state(
        counts=full(candidates=3, previews=3, approved=3, permitted=3, delivered=3)
    ),
    "done": base_state(
        counts=full(
            candidates=3, previews=3, approved=3, permitted=3, delivered=3, verified=3
        )
    ),
}


class Guidance(unittest.TestCase):
    def test_every_rung_suggests_a_command_the_cli_accepts(self):
        parser = build_parser()
        for step, state in LADDER_STATES.items():
            with self.subTest(step=step):
                action = next_action(state)
                self.assertEqual(step, action["step"])
                if action["command"] is None:
                    self.assertEqual("done", step)
                    continue
                argv = shlex.split(action["command"])
                self.assertTrue(argv[0].endswith("python3") or "python" in argv[0], argv[0])
                self.assertTrue(argv[1].endswith("gb.py"), argv[1])
                parsed = parser.parse_args(argv[2:])
                self.assertEqual(ABSOLUTE, parsed.project)

    def test_command_for_every_step_parses(self):
        parser = build_parser()
        for step in STEPS:
            with self.subTest(step=step):
                command = command_for(step, PROJECT, "local:a")
                parsed = parser.parse_args(shlex.split(command)[2:])
                self.assertEqual(ABSOLUTE, parsed.project)

    def test_placeholders_stay_uppercase_when_only_the_human_knows(self):
        action = next_action(LADDER_STATES["approve"])
        self.assertIn("--by NOME", action["command"])
        self.assertIn("--channel chat", action["command"])
        self.assertIn("FRASE", action["command"])

    def test_board_url_only_when_the_page_exists(self):
        self.assertIsNone(next_action(LADDER_STATES["approve"])["url"])
        state = dict(LADDER_STATES["approve"], review_page=True)
        self.assertEqual("http://127.0.0.1:8767/review.html", next_action(state)["url"])

    def test_blocking_human_only_on_review_permit_and_format(self):
        blocking = {
            step: next_action(state)["blocking_human"]
            for step, state in LADDER_STATES.items()
        }
        self.assertEqual(
            {"format", "approve", "permit"},
            {step for step, value in blocking.items() if value},
        )

    def test_user_declaration_permit_does_not_block_the_human(self):
        state = dict(LADDER_STATES["permit"], rights_mode="user_declaration")
        self.assertFalse(next_action(state)["blocking_human"])

    def test_missing_brief_names_the_slash_command_and_init_brief(self):
        action = next_action(LADDER_STATES["init-brief"])
        self.assertIn("/get-brolls-brief", action["for_human"])
        self.assertIn("init-brief", action["command"])
        self.assertFalse(action["blocking_human"])

    def test_missing_beat_reuses_the_search_ready_from_the_brief(self):
        ready = f'python3 "/x/gb.py" search --project {ABSOLUTE} --query "abertura"'
        state = dict(
            LADDER_STATES["brief-search"],
            brief={
                "beats": 2,
                "covered": 1,
                "missing": [{"id": "abertura", "search": ready}],
                "conflicts": [],
            },
        )
        action = next_action(state)
        self.assertEqual(ready, action["command"])
        self.assertIn("abertura", action["for_human"])

    def test_for_human_is_a_sentence_and_why_explains_the_rung(self):
        for step, state in LADDER_STATES.items():
            with self.subTest(step=step):
                action = next_action(state)
                self.assertEqual(
                    ["step", "why", "command", "url", "for_human", "blocking_human"],
                    list(action),
                )
                self.assertTrue(action["why"])
                self.assertTrue(action["for_human"].endswith((".", "!")))


if __name__ == "__main__":
    unittest.main()
