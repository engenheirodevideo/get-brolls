"""Próximo passo humano: toda sugestão da escada é um comando que a CLI aceita."""

import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls.cli import build_parser
from getbrolls.guidance import STEPS, command_for, next_action

# O comando repassa o `--project` como recebeu, sem resolver links simbólicos: assim
# `do.command` e o `project` do relatório falam do mesmo caminho.
PROJECT = "/tmp/projeto-do-video"
ABSOLUTE = PROJECT


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
    "brief-invalid": base_state(brief={"error": 'O beat "abertura" repetiu o id.'}),
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
    "inspect": base_state(
        counts=full(candidates=3), duration_unknown=2, inspect_candidate="youtube:abc"
    ),
    "preview": base_state(counts=full(candidates=3)),
    "approve": base_state(counts=full(candidates=3, previews=3)),
    "permit": base_state(counts=full(candidates=3, previews=3, approved=3)),
    "fetch": base_state(counts=full(candidates=3, previews=3, approved=3, permitted=3)),
    "verify": base_state(
        counts=full(candidates=3, previews=3, approved=3, permitted=3, delivered=3)
    ),
    "deliver": base_state(
        counts=full(
            candidates=3, previews=3, approved=3, permitted=3, delivered=3, verified=3
        ),
        undelivered=3,
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

    def test_invalid_brief_asks_for_a_fix_instead_of_a_new_brief(self):
        action = next_action(LADDER_STATES["brief-invalid"])
        self.assertEqual("brief-invalid", action["step"])
        self.assertIn("repetiu o id", action["why"])
        self.assertIn("brief --validate", action["command"])
        self.assertFalse(action["blocking_human"])

    def test_format_conflict_without_approvals_only_warns_on_the_search_rung(self):
        state = base_state(brief={"beats": 1, "covered": 1, "missing": [], "conflicts": ["Formato do brief difere do RULES.md."]})
        action = next_action(state)
        self.assertEqual("search", action["step"])
        self.assertIn("Formato do brief", action["for_human"])
        # Com algo já aprovado, o mesmo conflito vira o degrau de formato.
        decided = dict(state, counts=full(candidates=2, previews=2, approved=2))
        self.assertEqual("format", next_action(decided)["step"])

    def test_preview_rung_carries_an_interval_and_points_at_inspect_first(self):
        action = next_action(LADDER_STATES["preview"])
        self.assertIn("--start", action["command"])
        self.assertIn("--end", action["command"])
        self.assertIn("contact sheet", action["for_human"])
        self.assertIn("inspect", action["for_human"])
        self.assertIn("inspect", action["why"])

    def test_a_candidate_without_a_known_duration_gets_inspect_before_preview(self):
        action = next_action(LADDER_STATES["inspect"])
        self.assertEqual("inspect", action["step"])
        self.assertIn("inspect --project", action["command"])
        self.assertIn("youtube:abc", action["command"])
        self.assertIn("--query NARRACAO_OU_ALVO", action["command"])
        self.assertFalse(action["blocking_human"])
        # Com a duração conhecida, a escada volta ao degrau da prévia.
        known = dict(LADDER_STATES["inspect"], duration_unknown=0)
        self.assertEqual("preview", next_action(known)["step"])

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
                    {"step", "why", "command", "url", "for_human", "blocking_human"},
                    set(action),
                )
                self.assertTrue(action["why"])
                self.assertTrue(action["for_human"].endswith((".", "!")))


class SuggestedCommandRuns(unittest.TestCase):
    """O comando do degrau não pode só parsear: ele tem que rodar de verdade."""

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_preview_rung_command_runs_on_a_local_candidate(self):
        cli = str(Path(__file__).resolve().parents[1] / "scripts/gb.py")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "original.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                 "testsrc=size=160x90:duration=6:rate=10", "-c:v", "libx264",
                 "-pix_fmt", "yuv420p", str(src)],
                check=True,
            )
            resolved = subprocess.run(
                [sys.executable, cli, "resolve", "--file", str(src), "--project", tmp],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(0, resolved.returncode, resolved.stderr)
            candidate = json.loads(resolved.stdout)["id"]
            state = base_state(
                project=tmp, counts=full(candidates=1), candidate=candidate
            )
            action = next_action(state)
            self.assertEqual("preview", action["step"])
            argv = shlex.split(action["command"])
            done = subprocess.run(
                [sys.executable, *argv[1:]],
                capture_output=True, text=True, encoding="utf-8",
            )
            # TypeError de --start/--end None sairia como INTERNAL_ERROR (saída 3).
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertNotIn("TypeError", done.stdout + done.stderr)
            self.assertTrue(json.loads(done.stdout)["preview"].get("contact_sheet_path"))


class BoardRoute(unittest.TestCase):
    """#44: com o Storyboard gerado, o degrau da decisão humana abre o board sozinho."""

    def test_board_rung_starts_the_server_in_the_background(self):
        state = dict(LADDER_STATES["approve"], review_page=True)
        action = next_action(state)
        self.assertEqual("approve", action["step"])
        self.assertIn("serve", action["command"])
        self.assertIn("--background", action["command"])
        self.assertEqual("http://127.0.0.1:8767/review.html", action["url"])
        self.assertTrue(action["blocking_human"])
        parsed = build_parser().parse_args(shlex.split(action["command"])[2:])
        self.assertEqual(ABSOLUTE, parsed.project)

    def test_the_board_route_imports_without_pointing_at_a_file(self):
        state = dict(LADDER_STATES["approve"], review_page=True)
        action = next_action(state)
        self.assertIn("import-review", action["for_human"])
        self.assertNotIn("--file", action["for_human"])
        command = command_for("import-review", PROJECT)
        self.assertNotIn("--file", command)
        build_parser().parse_args(shlex.split(command)[2:])

    def test_without_the_board_the_chat_route_stays(self):
        action = next_action(LADDER_STATES["approve"])
        self.assertIn("--channel chat", action["command"])


class DeliveryRung(unittest.TestCase):
    def test_verified_files_outside_entrega_ask_for_deliver(self):
        action = next_action(LADDER_STATES["deliver"])
        self.assertEqual("deliver", action["step"])
        self.assertIn("deliver --project", action["command"])
        self.assertFalse(action["blocking_human"])

    def test_the_last_rung_is_done_once_everything_is_organised(self):
        self.assertEqual("done", next_action(LADDER_STATES["done"])["step"])


if __name__ == "__main__":
    unittest.main()
