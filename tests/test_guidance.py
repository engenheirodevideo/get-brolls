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

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)

from getbrolls.cli import build_parser
from getbrolls.guidance import STEPS, command_for, next_action

# O comando repassa o `--project` como recebeu, sem resolver links simbólicos: assim
# `do.command` e o `project` do relatório falam do mesmo caminho.
PROJECT = "/tmp/projeto-do-video"
ABSOLUTE = PROJECT


def base_state(**extra):
    state = {
        "project": PROJECT,
        "counts": dict.fromkeys(("candidates", "previews", "approved", "permitted", "delivered", "verified"), 0),
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
    "inspect": base_state(counts=full(candidates=3), duration_unknown=2, inspect_candidate="youtube:abc"),
    "preview": base_state(counts=full(candidates=3)),
    "approve": base_state(counts=full(candidates=3, previews=3)),
    "permit": base_state(counts=full(candidates=3, previews=3, approved=3)),
    "fetch": base_state(counts=full(candidates=3, previews=3, approved=3, permitted=3)),
    "verify": base_state(counts=full(candidates=3, previews=3, approved=3, permitted=3, delivered=3)),
    "deliver": base_state(
        counts=full(candidates=3, previews=3, approved=3, permitted=3, delivered=3, verified=3),
        undelivered=3,
    ),
    "done": base_state(counts=full(candidates=3, previews=3, approved=3, permitted=3, delivered=3, verified=3)),
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

    def test_board_url_only_when_the_server_is_up(self):
        self.assertIsNone(next_action(LADDER_STATES["approve"])["url"])
        # Página gerada, servidor parado: não há porta para prometer.
        page = dict(LADDER_STATES["approve"], review_page=True)
        action = next_action(page)
        self.assertIsNone(action["url"])
        self.assertIn("serve --background", action["for_human"])
        self.assertNotIn("127.0.0.1", action["for_human"])
        # Servidor no ar: vale a porta que ele gravou, não a padrão.
        live = dict(page, board_url="http://127.0.0.1:57114/review.html")
        action = next_action(live)
        self.assertEqual("http://127.0.0.1:57114/review.html", action["url"])
        self.assertIn("http://127.0.0.1:57114/review.html", action["for_human"])
        self.assertNotIn("8767", action["for_human"])

    def test_blocking_human_only_on_review_permit_and_format(self):
        blocking = {step: next_action(state)["blocking_human"] for step, state in LADDER_STATES.items()}
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
        state = base_state(
            brief={"beats": 1, "covered": 1, "missing": [], "conflicts": ["Formato do brief difere do RULES.md."]}
        )
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
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=160x90:duration=6:rate=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            resolved = subprocess.run(
                [sys.executable, cli, "resolve", "--file", str(src), "--project", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(0, resolved.returncode, resolved.stderr)
            candidate = json.loads(resolved.stdout)["id"]
            state = base_state(project=tmp, counts=full(candidates=1), candidate=candidate)
            action = next_action(state)
            self.assertEqual("preview", action["step"])
            argv = shlex.split(action["command"])
            done = subprocess.run(
                [sys.executable, *argv[1:]],
                capture_output=True,
                text=True,
                encoding="utf-8",
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
        # Sem servidor no ar, o endereço quem imprime é o próprio `serve`.
        self.assertIsNone(action["url"])
        self.assertTrue(action["blocking_human"])
        parsed = build_parser().parse_args(shlex.split(action["command"])[2:])
        self.assertEqual(ABSOLUTE, parsed.project)

    def test_the_board_route_imports_without_pointing_at_a_file(self):
        state = dict(LADDER_STATES["approve"], review_page=True)
        action = next_action(state)
        self.assertIn("import-review", action["for_human"])
        self.assertNotIn("--file", action["for_human"])
        command = command_for("import-review", PROJECT)
        assert command is not None
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


# `summary.next` (escada de `status`) e `summary.do.step` (escada de `guidance`) são
# duas leituras do mesmo estado: se divergirem, a pessoa ouve dois próximos passos.
LADDER_PHRASES = {
    "search": "registre fontes",
    "inspect": "sem quadro",
    "preview": "sem quadro",
    "approve": "decisão humana",
    "permit": "permit",
    "fetch": "fetch",
    "verify": "verify",
    "deliver": "deliver",
    "done": "completo",
}


class SameLadder(unittest.TestCase):
    def test_next_and_do_name_the_same_stage_for_every_fixture(self):
        from getbrolls.commands import status_next

        for step, state in LADDER_STATES.items():
            if step not in LADDER_PHRASES:
                # Degraus do brief/formato não têm par na escada de contagens.
                continue
            with self.subTest(step=step):
                counts = dict(state["counts"])
                counts.setdefault("pending", 0)
                counts.setdefault("rejected", 0)
                phrase = status_next(
                    counts,
                    state.get("format_pending", 0),
                    pending_preview=state.get("pending_preview", counts["candidates"] - counts["previews"]),
                    undelivered=state.get("undelivered", 0),
                )
                self.assertIn(LADDER_PHRASES[step], phrase.lower(), (step, phrase))
                self.assertEqual(step, next_action(state)["step"])

    def test_a_fully_delivered_project_reports_the_flow_as_complete(self):
        from getbrolls.commands import status_next

        counts = {**full(candidates=3, previews=3, approved=3, permitted=3, delivered=3, verified=3)}
        counts.update(pending=0, rejected=0)
        self.assertIn("completo", status_next(counts, 0, pending_preview=0, undelivered=0))

    def test_verified_files_still_outside_entrega_are_named_by_next_too(self):
        from getbrolls.commands import status_next

        counts = {**full(candidates=3, previews=3, approved=3, permitted=3, delivered=3, verified=3)}
        counts.update(pending=0, rejected=0)
        self.assertIn("deliver", status_next(counts, 0, pending_preview=0, undelivered=3))

    def test_a_rejected_item_without_a_preview_does_not_pin_the_ladder_to_preview(self):
        from getbrolls.commands import status_next

        counts = {**full(candidates=4, previews=3, approved=3, permitted=3, delivered=3, verified=3)}
        counts.update(pending=0, rejected=1)
        self.assertIn("completo", status_next(counts, 0, pending_preview=0, undelivered=0))


class HumanStateWinsOverAgentDraft(unittest.TestCase):
    """Fricção 1 da rodada 2: com a entrega pronta, o degrau apontava um descarte."""

    def _delivered(self, **extra):
        counts = full(candidates=16, previews=3, approved=2, permitted=2, delivered=2, verified=2)
        counts["pending"] = 0
        counts["rejected"] = 1
        return base_state(counts=counts, undelivered=0, **extra)

    def test_a_finished_flow_says_so_even_with_candidates_left_without_a_preview(self):
        # 13 candidatos sem prévia continuam no manifesto; nenhum deles é o próximo passo.
        action = next_action(self._delivered(pending_preview=13, duration_unknown=5, inspect_candidate="youtube:x"))
        self.assertEqual("done", action["step"])
        self.assertIsNone(action["command"])
        self.assertIn("Fluxo completo", action["for_human"])
        self.assertIn("13 candidatos sem decisão", action["for_human"])
        self.assertIn("reject", action["for_human"])
        self.assertNotIn("prévia dos candidatos", action["for_human"])
        self.assertFalse(action["blocking_human"])

    def test_the_same_finished_flow_reads_the_same_in_status_next(self):
        from getbrolls.commands import status_next

        counts = self._delivered()["counts"]
        phrase = status_next(counts, 0, pending_preview=13, undelivered=0)
        self.assertIn("Fluxo completo", phrase)
        self.assertIn("13 candidatos sem decisão", phrase)

    def test_a_decision_still_pending_is_never_called_a_finished_flow(self):
        """A parada obrigatória da revisão não pode virar aparte de "sobraram"."""
        counts = full(candidates=5, previews=5, approved=2, permitted=2, delivered=2, verified=2)
        counts["pending"] = 3
        state = base_state(counts=counts, undelivered=0)
        action = next_action(state)
        self.assertEqual("approve", action["step"])
        self.assertTrue(action["blocking_human"])
        self.assertNotIn("Fluxo completo", action["for_human"])
        self.assertNotIn("sem decisão", action["for_human"])
        # E com as três decididas, aí sim o fluxo fecha.
        decided = full(candidates=5, previews=5, approved=5, permitted=5, delivered=5, verified=5)
        decided["pending"] = 0
        self.assertEqual("done", next_action(base_state(counts=decided, undelivered=0))["step"])

    def test_status_next_also_refuses_to_close_with_a_decision_pending(self):
        from getbrolls.commands import status_next

        counts = full(candidates=5, previews=5, approved=2, permitted=2, delivered=2, verified=2)
        counts.update(pending=3, rejected=0)
        phrase = status_next(counts, 0, pending_preview=0, undelivered=0)
        self.assertIn("decisão humana", phrase)
        self.assertNotIn("Fluxo completo", phrase)

    def test_an_item_with_a_preview_and_no_decision_is_not_a_leftover(self):
        """ "Sobraram N" conta só candidato sem prévia: com prévia, é decisão pendente."""
        from getbrolls.guidance import leftovers

        counts = full(candidates=10, previews=5, approved=2, permitted=2, delivered=2, verified=2)
        counts.update(pending=3, rejected=0)
        self.assertEqual(5, leftovers(base_state(counts=counts), counts))

    def test_a_pending_decision_wins_over_inspect_and_preview(self):
        counts = full(candidates=8, previews=3)
        counts["pending"] = 3
        state = base_state(
            counts=counts,
            pending_preview=5,
            duration_unknown=5,
            inspect_candidate="youtube:descartado",
        )
        action = next_action(state)
        self.assertEqual("approve", action["step"])
        self.assertTrue(action["blocking_human"])
        self.assertIn("3 item", action["why"])

    def test_status_next_names_the_pending_decision_too(self):
        from getbrolls.commands import status_next

        counts = full(candidates=8, previews=3)
        counts.update(pending=3, rejected=0)
        phrase = status_next(counts, 0, pending_preview=5, undelivered=0)
        self.assertIn("decisão humana", phrase)
        self.assertNotIn("Gere prévias", phrase)


class MissingBeatPhrasing(unittest.TestCase):
    def test_a_literal_beat_without_material_is_simply_searched(self):
        state = base_state(
            brief={
                "beats": 1,
                "covered": 0,
                "missing": [{"id": "abertura", "search": None, "intent": "literal", "target": "foguete SLS"}],
                "conflicts": [],
            }
        )
        self.assertIn("vou buscar por ele", next_action(state)["for_human"])

    def test_a_beat_with_no_literal_target_asks_the_person_instead_of_promising_footage(self):
        """A guarda literal proíbe preencher com material aproximado; a frase precisa combinar."""
        state = base_state(
            brief={
                "beats": 1,
                "covered": 0,
                "missing": [
                    {
                        "id": "sem-palco",
                        "search": None,
                        "intent": "illustrative",
                        "target": "anúncio que não aconteceu",
                    }
                ],
                "conflicts": [],
            }
        )
        phrase = next_action(state)["for_human"]
        self.assertIn("sem-palco", phrase)
        self.assertNotIn("vou buscar por ele", phrase)
        self.assertIn("seu próprio material", phrase)
        self.assertIn("sem fonte", phrase)


if __name__ == "__main__":
    unittest.main()
