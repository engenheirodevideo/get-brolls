"""Brief/intake: BRIEF.md legível, validação em PT-BR e beats prontos para virar comando."""

import copy
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/gb.py"
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls import brief as brief_module
from getbrolls.cli import SUMMARIES, build_parser

TEMPLATE = ROOT / "docs" / "BRIEF.md"

VALID = {
    "version": 1,
    "video": {
        "title": "Reels sobre o eclipse",
        "objective": "Explicar por que o eclipse de abril virou notícia",
        "audience": "Quem não acompanha astronomia",
        "delivery": {"format": "native", "duration_s": 45, "platform": "instagram"},
    },
    "rights": {
        "posture": "per_item_evidence",
        "stock_allowed": False,
        "notes": None,
    },
    "defaults": {
        "allowed_sources": ["youtube", "commons", "nasa"],
        "intent": "literal",
        "duration_hint_s": 4,
        "stock": False,
    },
    "beats": [
        {
            "id": "abertura",
            "narration": "Em abril o céu escureceu no meio da tarde.",
            "target": "Registro real do eclipse total",
            "queries": ["eclipse total 2024 registro"],
        },
        {
            "id": "reacao-publico",
            "narration": "Muita gente parou na rua pra olhar.",
            "target": "Pessoas assistindo ao eclipse",
        },
    ],
}


def write_brief(project, data):
    path = Path(project) / "BRIEF.md"
    path.write_text(
        "---\ntype: brief\n---\n\n# Brief\n\n```json\n"
        + json.dumps(data, ensure_ascii=False, indent=2)
        + "\n```\n",
        encoding="utf-8",
    )
    return path


def run_cli(test, *args, ok=True):
    done = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    test.assertEqual(0 if ok else 2, done.returncode, done.stderr or done.stdout)
    return json.loads(done.stdout if ok else done.stderr)


def loaded(data, rules=None):
    return brief_module.validate_brief(copy.deepcopy(data), rules)


class BriefTemplateTests(unittest.TestCase):
    def test_template_has_exactly_one_json_block_and_validates(self):
        raw = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("type: brief", raw)
        self.assertEqual(1, raw.count("```json"))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "BRIEF.md").write_text(raw, encoding="utf-8")
            data = brief_module.load_brief(tmp)
        self.assertEqual(1, data["version"])
        self.assertTrue(data["beats"])

    def test_schema_file_documents_version_one(self):
        schema = json.loads(
            (ROOT / "schemas" / "brief.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, schema["properties"]["version"]["const"])
        for key in ("video", "rights", "defaults", "beats"):
            self.assertIn(key, schema["properties"])


class LoadBriefTests(unittest.TestCase):
    def test_missing_brief_says_how_to_create_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as raised:
                brief_module.load_brief(tmp)
        self.assertIn("init-brief", str(raised.exception))

    def test_env_override_points_at_another_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_brief(tmp, VALID)
            other = Path(tmp) / "outro"
            other.mkdir()
            os.environ["GB_BRIEF_FILE"] = str(path)
            try:
                data = brief_module.load_brief(other)
                self.assertEqual("Reels sobre o eclipse", data["video"]["title"])
                os.environ["GB_BRIEF_FILE"] = str(other / "nao-existe.md")
                with self.assertRaises(ValueError) as raised:
                    brief_module.load_brief(other)
                self.assertIn("GB_BRIEF_FILE", str(raised.exception))
            finally:
                os.environ.pop("GB_BRIEF_FILE", None)

    def test_two_json_blocks_are_refused_in_portuguese(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "BRIEF.md").write_text(
                "```json\n{}\n```\n\n```json\n{}\n```\n", encoding="utf-8"
            )
            with self.assertRaises(ValueError) as raised:
                brief_module.load_brief(tmp)
        self.assertIn("exatamente um bloco", str(raised.exception))


class ValidateBriefTests(unittest.TestCase):
    def test_valid_brief_has_no_conflicts(self):
        data, conflicts = loaded(VALID)
        self.assertEqual([], conflicts)
        self.assertEqual(2, len(data["beats"]))

    def test_beat_id_must_match_the_shot_alphabet_and_be_unique(self):
        bad = copy.deepcopy(VALID)
        bad["beats"][0]["id"] = "Abertura Geral"
        with self.assertRaises(ValueError) as raised:
            loaded(bad)
        self.assertIn("id", str(raised.exception))
        repeated = copy.deepcopy(VALID)
        repeated["beats"][1]["id"] = "abertura"
        with self.assertRaises(ValueError) as raised:
            loaded(repeated)
        self.assertIn("repetid", str(raised.exception).lower())

    def test_beat_ids_are_valid_shot_values(self):
        data, _ = loaded(VALID)
        parser = build_parser()
        for beat in data["beats"]:
            args = parser.parse_args(
                ["resolve", "--project", ".", "--url", "https://x/y", "--shot", beat["id"]]
            )
            self.assertEqual(beat["id"], args.shot)

    def test_target_is_required_on_every_beat(self):
        bad = copy.deepcopy(VALID)
        bad["beats"][1].pop("target")
        with self.assertRaises(ValueError) as raised:
            loaded(bad)
        self.assertIn("target", str(raised.exception))

    def test_unknown_source_is_refused_and_unknown_keys_are_ignored(self):
        bad = copy.deepcopy(VALID)
        bad["beats"][0]["allowed_sources"] = ["vimeo"]
        with self.assertRaises(ValueError) as raised:
            loaded(bad)
        self.assertIn("allowed_sources", str(raised.exception))
        extra = copy.deepcopy(VALID)
        extra["beats"][0]["gosto_pessoal"] = "azul"
        extra["campo_novo"] = 7
        data, conflicts = loaded(extra)
        self.assertEqual([], conflicts)
        self.assertNotIn("gosto_pessoal", data["beats"][0]["resolved"])

    def test_duration_hint_stays_between_half_a_second_and_two_minutes(self):
        for value in (0.1, 240):
            bad = copy.deepcopy(VALID)
            bad["beats"][0]["duration_hint_s"] = value
            with self.assertRaises(ValueError):
                loaded(bad)

    def test_stock_true_without_a_stock_bank_is_an_error(self):
        bad = copy.deepcopy(VALID)
        bad["beats"][0]["stock"] = True
        bad["beats"][0]["allowed_sources"] = ["youtube"]
        with self.assertRaises(ValueError) as raised:
            loaded(bad)
        self.assertIn("stock", str(raised.exception))

    def test_stock_false_with_a_stock_bank_is_an_error(self):
        bad = copy.deepcopy(VALID)
        bad["beats"][0]["allowed_sources"] = ["pexels"]
        with self.assertRaises(ValueError) as raised:
            loaded(bad)
        self.assertIn("pexels", str(raised.exception))

    def test_user_declaration_requires_the_rules_declaration(self):
        data = copy.deepcopy(VALID)
        data["rights"]["posture"] = "user_declaration"
        empty = {
            "video_format": "native",
            "copyright": {
                "mode": "per_item_evidence",
                "responsible_person": None,
                "declaration": None,
            },
        }
        with self.assertRaises(ValueError) as raised:
            loaded(data, empty)
        self.assertIn("RULES.md", str(raised.exception))
        filled = copy.deepcopy(empty)
        filled["copyright"] = {
            "mode": "user_declaration",
            "responsible_person": "Bruno Moreira",
            "declaration": "Assumo a responsabilidade pelo uso destes materiais.",
        }
        _, conflicts = loaded(data, filled)
        self.assertEqual([], conflicts)

    def test_user_declaration_is_refused_when_the_rules_cannot_be_read(self):
        # RULES.md ausente/ilegível vira rules=None em brief_report: uma postura que
        # transfere responsabilidade não pode passar por falta de arquivo para conferir.
        data = copy.deepcopy(VALID)
        data["rights"]["posture"] = "user_declaration"
        with self.assertRaises(ValueError) as raised:
            loaded(data, None)
        self.assertIn("RULES.md", str(raised.exception))

    def test_format_divergence_is_a_conflict_not_a_failure(self):
        data = copy.deepcopy(VALID)
        data["video"]["delivery"]["format"] = "reels"
        parsed, conflicts = loaded(data, {"video_format": "horizontal", "copyright": {}})
        self.assertEqual(1, len(conflicts))
        self.assertIn("reels", conflicts[0])
        self.assertIn("horizontal", conflicts[0])
        self.assertEqual("reels", parsed["video"]["delivery"]["format"])


class ResolveBeatTests(unittest.TestCase):
    def test_beat_inherits_the_defaults_it_does_not_declare(self):
        data, _ = loaded(VALID)
        resolved = data["beats"][1]["resolved"]
        self.assertEqual(["youtube", "commons", "nasa"], resolved["allowed_sources"])
        self.assertEqual("literal", resolved["intent"])
        self.assertEqual(4, resolved["duration_hint_s"])
        self.assertFalse(resolved["stock"])
        self.assertEqual([], resolved["queries"])

    def test_declared_values_win_over_the_defaults(self):
        data = copy.deepcopy(VALID)
        data["defaults"]["stock"] = True
        data["defaults"]["allowed_sources"] = ["pexels", "pixabay"]
        data["rights"]["stock_allowed"] = True
        data["beats"][0].update(
            {"intent": "illustrative", "duration_hint_s": 8, "allowed_sources": ["pexels"]}
        )
        parsed, _ = loaded(data)
        resolved = parsed["beats"][0]["resolved"]
        self.assertEqual("illustrative", resolved["intent"])
        self.assertEqual(8, resolved["duration_hint_s"])
        self.assertEqual(["pexels"], resolved["allowed_sources"])
        self.assertTrue(resolved["stock"])


class BeatCommandTests(unittest.TestCase):
    def parsed_commands(self, project, beat):
        commands = brief_module.beat_commands(project, beat)
        parser = build_parser()
        out = {}
        for name, line in commands.items():
            if name == "note":  # prosa para o agente, não comando
                continue
            tokens = shlex.split(line)
            self.assertTrue(tokens[1].endswith("gb.py"), line)
            out[name] = parser.parse_args(tokens[2:])
        return commands, out

    def test_every_command_parses_and_carries_the_beat_identity(self):
        data, _ = loaded(VALID)
        with tempfile.TemporaryDirectory() as tmp:
            for beat in data["beats"]:
                commands, parsed = self.parsed_commands(tmp, beat["resolved"])
                self.assertEqual({"search", "resolve", "preview"}, set(commands))
                self.assertEqual("search", parsed["search"].command)
                self.assertEqual(beat["resolved"]["intent"], parsed["search"].intent)
                self.assertEqual(beat["id"], parsed["resolve"].shot)
                self.assertEqual("preview", parsed["preview"].command)
                # Intervalo não se inventa no brief: quem vê a fonte é que o define.
                self.assertIsNone(parsed["preview"].start)
                self.assertIsNone(parsed["preview"].end)
                self.assertEqual(
                    Path(tmp).resolve(), Path(parsed["search"].project).resolve()
                )

    def test_narration_travels_verbatim_into_preview(self):
        data, _ = loaded(VALID)
        with tempfile.TemporaryDirectory() as tmp:
            _, parsed = self.parsed_commands(tmp, data["beats"][0]["resolved"])
            self.assertEqual(
                "Em abril o céu escureceu no meio da tarde.", parsed["preview"].narration
            )

    def test_a_beat_without_a_searchable_source_gets_a_note_instead_of_search(self):
        data = copy.deepcopy(VALID)
        data["beats"][0]["allowed_sources"] = ["instagram", "local"]
        parsed, _ = loaded(data)
        with tempfile.TemporaryDirectory() as tmp:
            commands, _ = self.parsed_commands(tmp, parsed["beats"][0]["resolved"])
            self.assertNotIn("search", commands)
            self.assertIn("instagram", commands["note"])
            self.assertIn("resolve", commands["note"])

    def test_a_beat_without_narration_omits_the_flag(self):
        data = copy.deepcopy(VALID)
        data["beats"][0]["narration"] = None
        parsed, _ = loaded(data)
        with tempfile.TemporaryDirectory() as tmp:
            commands = brief_module.beat_commands(tmp, parsed["beats"][0]["resolved"])
            self.assertNotIn("--narration", commands["preview"])


class BeatProgressTests(unittest.TestCase):
    def test_candidates_link_to_the_beat_by_shot(self):
        data, _ = loaded(VALID)
        items = [
            {"id": "youtube:aaa:shot:abertura", "shot": "abertura"},
            {"id": "youtube:bbb"},
        ]
        progress = brief_module.beat_progress(data["beats"], items)
        self.assertEqual(["youtube:aaa:shot:abertura"], progress["abertura"])
        self.assertEqual([], progress["reacao-publico"])


class BriefCommandTests(unittest.TestCase):
    def test_both_subcommands_are_summarised_and_take_project(self):
        for name in ("init-brief", "brief"):
            self.assertIn(name, SUMMARIES)
            parser = build_parser()
            args = parser.parse_args([name, "--project", "."])
            self.assertEqual(name, args.command)

    def test_init_brief_copies_the_template_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli(self, "init-brief", "--project", tmp)
            self.assertTrue(Path(result["brief"]).is_file())
            self.assertEqual(
                TEMPLATE.read_text(encoding="utf-8"),
                (Path(tmp) / "BRIEF.md").read_text(encoding="utf-8"),
            )
            again = run_cli(self, "init-brief", "--project", tmp, ok=False)
            self.assertIn("já existe", again["error"])

    def test_brief_puts_the_summary_first_and_lists_ready_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_brief(tmp, VALID)
            result = run_cli(self, "brief", "--project", tmp)
            self.assertEqual("summary", next(iter(result)))
            self.assertEqual(
                ["line", "problems", "next"], list(result["summary"])
            )
            self.assertIn("approve --all", result["summary"]["next"])
            self.assertEqual(2, len(result["beats"]))
            first = result["beats"][0]
            self.assertEqual("abertura", first["id"])
            self.assertIn("resolved", first)
            self.assertIn("--shot abertura", first["commands"]["resolve"])
            self.assertEqual([], first["candidates"])
            self.assertEqual([], result["conflicts"])

    def test_validate_only_reports_health_without_the_command_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_brief(tmp, VALID)
            result = run_cli(self, "brief", "--project", tmp, "--validate")
            self.assertTrue(result["valid"])
            self.assertEqual(2, result["beats"])
            broken = copy.deepcopy(VALID)
            broken["beats"][0]["target"] = ""
            write_brief(tmp, broken)
            failure = run_cli(self, "brief", "--project", tmp, "--validate", ok=False)
            self.assertIn("target", failure["error"])

    def test_validate_does_not_call_a_brief_with_problems_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = copy.deepcopy(VALID)
            data["rights"]["stock_allowed"] = False
            data["defaults"]["stock"] = True
            data["defaults"]["allowed_sources"] = ["pexels"]
            write_brief(tmp, data)
            result = run_cli(self, "brief", "--project", tmp, "--validate")
            self.assertTrue(result["summary"]["problems"])
            self.assertNotIn("válido", result["summary"]["line"])
            self.assertIn("brief --validate", result["summary"]["next"])

    def test_init_brief_writes_the_file_that_brief_will_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "briefs" / "video-01.md"
            environment = dict(os.environ, GB_BRIEF_FILE=str(target))
            done = subprocess.run(
                [sys.executable, str(CLI), "init-brief", "--project", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertTrue(target.is_file())
            self.assertFalse((Path(tmp) / "BRIEF.md").exists())
            self.assertEqual(
                target.resolve(), Path(json.loads(done.stdout)["brief"]).resolve()
            )

    def test_beat_filter_selects_one_beat_and_names_the_valid_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_brief(tmp, VALID)
            result = run_cli(self, "brief", "--project", tmp, "--beat", "abertura")
            self.assertEqual(1, len(result["beats"]))
            self.assertEqual("abertura", result["beats"][0]["id"])
            missing = run_cli(
                self, "brief", "--project", tmp, "--beat", "inexistente", ok=False
            )
            self.assertIn("reacao-publico", missing["error"])

    def test_brief_never_creates_the_project_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_brief(tmp, VALID)
            run_cli(self, "brief", "--project", tmp)
            self.assertFalse((Path(tmp) / "brolls").exists())


class BriefDocumentationTests(unittest.TestCase):
    def test_interview_reference_and_slash_command_ship_with_the_plugin(self):
        interview = ROOT / "references" / "interview.md"
        command = ROOT / "commands" / "get-brolls-brief.md"
        self.assertTrue(interview.is_file())
        self.assertTrue(command.is_file())
        body = interview.read_text(encoding="utf-8")
        self.assertIn("type: reference", body)
        self.assertIn("templates-de-resposta.md", body)
        self.assertIn("approve --all", body)
        self.assertIn("brief --validate", body)
        head = command.read_text(encoding="utf-8")
        self.assertIn("name: get-brolls-brief", head)
        self.assertIn("references/interview.md", head)

    def test_both_skills_mention_the_interview_before_searching(self):
        for path in (ROOT / "SKILL.md", ROOT / "skills/get-brolls/SKILL.md"):
            body = path.read_text(encoding="utf-8")
            self.assertIn("BRIEF.md", body)
            self.assertIn("/get-brolls-brief", body)

    def test_the_lazy_interview_case_is_in_the_eval_corpus(self):
        case = ROOT / "eval/corpus/brief-entrevista-preguicosa.md"
        self.assertTrue(case.is_file())
        body = case.read_text(encoding="utf-8")
        self.assertIn("## Roteiro", body)
        self.assertIn("## Gabarito", body)
        self.assertIn("stock", body)


if __name__ == "__main__":
    unittest.main()
