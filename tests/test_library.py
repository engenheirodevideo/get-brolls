"""Biblioteca global: ponteiro editorial entre projetos, nunca licença nem aprovação."""

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/gb.py"
sys.path.insert(0, str(ROOT / "scripts"))

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)

from getbrolls import library
from getbrolls.cli import SUMMARIES, build_parser
from getbrolls.ledger import Ledger
from getbrolls.memory import remember
from getbrolls.models import approve, candidate, require_fetch, set_segment
from getbrolls.review import project_id


def run_cli(test, *args, ok=True, env=None):
    done = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **(env or {})},
    )
    test.assertEqual(0 if ok else 2, done.returncode, done.stderr)
    return json.loads(done.stdout if ok else done.stderr)


def approved_candidate(project, source_url="https://www.youtube.com/watch?v=abc"):
    ledger = Ledger(project)
    c = ledger.add(candidate("youtube", "abc", "Foguete da NASA subindo", source_url))
    c["creator"] = {"name": "NASA", "url": "https://www.nasa.gov"}
    c["media"]["duration_s"] = 120.0
    set_segment(c, 10.0, 14.0)
    c["preview"]["contact_sheet_path"] = "previews/abc.jpg"
    approve(c, "Bruno", channel="chat", statement="Aprovo esse, pode usar.")
    ledger.save("approve", c)
    return ledger, c


class LibraryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir(parents=True)
        for key in ("GB_HOME", "GB_LIBRARY", "GB_RULES_FILE"):
            old = os.environ.get(key)
            self.addCleanup(
                lambda k=key, v=old: os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
            )
            os.environ.pop(key, None)
        os.environ["GB_HOME"] = str(self.home)


class LibraryTests(LibraryBase):
    def test_every_answer_says_rights_do_not_travel(self):
        for answer in (
            library.learn_query("foguete decolando", "youtube", "hit"),
            library.learn_preference("Prefiro plano aberto de foguete."),
            library.search("foguete"),
        ):
            self.assertTrue(answer["rights_not_transferable"])

    def test_the_index_is_written_atomically_and_private(self):
        library.learn_query("foguete decolando", "youtube", "hit")
        path = library.index_path()
        if os.name != "nt":  # Windows não tem bits POSIX de permissão
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertFalse(list(path.parent.glob("*.tmp")))
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(1, data["schema_version"])
        self.assertEqual("hit", data["queries"][0]["outcome"])
        self.assertEqual({"hit": 1, "miss": 0}, data["providers"]["youtube"]["outcomes"])

    def test_a_note_becomes_a_file_under_notes(self):
        answer = library.learn_query("foguete decolando", "youtube", "miss", note="Só resultados de simulação.")
        note = library.library_dir() / answer["entry"]["note"]
        self.assertIn("simulação", note.read_text(encoding="utf-8"))
        if os.name != "nt":  # Windows não tem bits POSIX de permissão
            self.assertEqual(0o600, stat.S_IMODE(note.stat().st_mode))

    def test_off_disables_reading_and_writing(self):
        os.environ["GB_LIBRARY"] = "off"
        answer = library.learn_query("foguete", "youtube", "hit")
        self.assertFalse(answer["enabled"])
        self.assertTrue(answer["rights_not_transferable"])
        self.assertFalse(library.index_path().exists())
        self.assertEqual([], library.search("foguete")["assets"])

    def test_from_candidate_copies_the_pointer_and_never_the_rights(self):
        ledger, c = approved_candidate(self.project)
        remember(ledger, c, "approved", "Plano aberto exato da fala.", "Bruno")
        answer = library.learn_from_candidate(self.project, c["id"])
        entry = answer["entry"]
        self.assertTrue(answer["rights_not_transferable"])
        self.assertEqual(c["source_url"], entry["source_url"])
        self.assertEqual({"start_s": 10.0, "end_s": 14.0}, {k: entry["clip"][k] for k in ("start_s", "end_s")})
        self.assertIn("signature", entry["clip"])
        for forbidden in ("evidence", "approval", "rights"):
            self.assertNotIn(forbidden, entry)
        self.assertEqual([], [k for k in entry if "evidence" in k])

    def test_the_ledger_of_a_project_never_names_another_project_path(self):
        ledger, c = approved_candidate(self.project)
        remember(ledger, c, "approved", "Plano aberto exato da fala.", "Bruno")
        identity = project_id(ledger)
        library.learn_from_candidate(self.project, c["id"], shot="beat-01")
        raw = library.index_path().read_text(encoding="utf-8")
        self.assertNotIn(str(self.project), raw)
        self.assertNotIn("project_path", raw)
        used = json.loads(raw)["assets"][0]["used_by"][0]
        self.assertEqual({"project_id", "shot", "at"}, set(used), used)
        self.assertEqual(identity, used["project_id"])
        self.assertEqual("beat-01", used["shot"])

    def test_reusing_the_same_asset_only_adds_a_use(self):
        ledger, c = approved_candidate(self.project)
        remember(ledger, c, "approved", "Plano aberto exato da fala.", "Bruno")
        first = library.learn_from_candidate(self.project, c["id"], shot="beat-01")
        second = library.learn_from_candidate(self.project, c["id"], shot="beat-02")
        self.assertEqual(first["entry"]["asset_id"], second["entry"]["asset_id"])
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["assets"]))
        self.assertEqual(2, len(data["assets"][0]["used_by"]))

    def test_a_candidate_without_a_reference_is_refused(self):
        _, c = approved_candidate(self.project)
        with self.assertRaisesRegex(ValueError, "remember"):
            library.learn_from_candidate(self.project, c["id"])

    def test_a_library_entry_never_makes_fetch_pass_in_another_project(self):
        ledger, c = approved_candidate(self.project)
        remember(ledger, c, "approved", "Plano aberto exato da fala.", "Bruno")
        library.learn_from_candidate(self.project, c["id"])
        other = Path(self.tmp.name) / "outro"
        other.mkdir()
        fresh = Ledger(other).add(candidate("youtube", "abc", "Foguete da NASA subindo", c["source_url"]))
        hits = library.search("foguete")["assets"]
        self.assertTrue(hits)
        with self.assertRaises(ValueError):
            require_fetch(fresh)
        self.assertEqual([], fresh["rights"]["evidence"])
        self.assertEqual("pending", fresh["approval"]["status"])

    def test_two_writers_at_once_keep_both_entries(self):
        import threading

        errors = []

        def write(n):
            try:
                library.learn_query(f"busca {n}", "youtube", "hit")
            except Exception as e:  # noqa: BLE001 — o teste quer a falha real
                errors.append(e)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([], errors)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(8, len(data["queries"]))
        self.assertFalse(list(library.library_dir().glob("*.tmp")))
        self.assertFalse((library.library_dir() / "index.lock").exists())

    def test_the_same_query_counts_up_instead_of_piling_lines(self):
        for _ in range(3):
            library.learn_query("foguete decolando", "pexels", "miss", auto=True)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["queries"]))
        self.assertEqual(3, data["queries"][0]["count"])
        self.assertEqual(3, data["providers"]["pexels"]["outcomes"]["miss"])

    def test_the_query_list_has_a_ceiling_and_drops_automatic_ones_first(self):
        library.learn_query("busca escrita por gente", "youtube", "hit")
        for n in range(library.MAX_QUERIES + 10):
            library.learn_query(f"automatica {n}", "pexels", "miss", auto=True)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(library.MAX_QUERIES, len(data["queries"]))
        self.assertIn("busca escrita por gente", [q["query"] for q in data["queries"]])

    def test_hints_never_repeat_free_text_from_another_project(self):
        ledger, c = approved_candidate(self.project)
        remember(ledger, c, "approved", "Cliente X pediu esse plano.", "Bruno")
        library.learn_from_candidate(self.project, c["id"])
        for hint in library.hints("foguete"):
            self.assertNotIn("reason", hint)
            self.assertNotIn("by", hint)
        explicit = library.search("foguete")["assets"][0]
        self.assertEqual("Cliente X pediu esse plano.", explicit["reason"])

    def test_an_unreadable_index_never_breaks_search(self):
        library.learn_query("foguete decolando", "youtube", "hit")
        library.index_path().write_text("{ isso não é json", encoding="utf-8")
        self.assertEqual([], library.hints("foguete"))

    def test_the_tests_never_point_at_the_real_home(self):
        import tempfile as tf

        os.environ.pop("GB_HOME", None)

        os.environ["GB_HOME"] = str(_isolation.GB_HOME)
        self.assertTrue(str(library.home_dir()).startswith(tf.gettempdir()), library.home_dir())
        self.assertNotEqual(Path.home() / ".getbrolls", library.home_dir())

    def test_search_finds_assets_queries_and_preferences(self):
        ledger, c = approved_candidate(self.project)
        remember(ledger, c, "approved", "Plano aberto exato da fala.", "Bruno")
        library.learn_from_candidate(self.project, c["id"])
        library.learn_query("foguete decolando", "youtube", "hit")
        library.learn_preference("Foguete sempre em plano aberto.")
        answer = library.search("FOGUETE")
        self.assertTrue(answer["assets"] and answer["queries"] and answer["preferences"])
        self.assertEqual([], library.search("sushi")["assets"])


class LibraryCommandTests(LibraryBase):
    def test_both_subcommands_are_summarised_and_take_project(self):
        for name in ("learn", "library"):
            self.assertIn(name, SUMMARIES)
            args = build_parser().parse_args(
                [name, "--project", "/tmp/x"] + (["--preference", "x"] if name == "learn" else ["--search", "x"])
            )
            self.assertEqual("/tmp/x", args.project)

    def test_learn_and_library_through_the_cli(self):
        env = {"GB_HOME": str(self.home)}
        run_cli(self, "init-rules", "--project", self.project, env=env)
        answer = run_cli(
            self,
            "learn",
            "--project",
            self.project,
            "--query",
            "foguete decolando",
            "--provider",
            "youtube",
            "--outcome",
            "hit",
            env=env,
        )
        self.assertTrue(answer["rights_not_transferable"])
        found = run_cli(self, "library", "--project", self.project, "--search", "foguete", env=env)
        self.assertTrue(found["rights_not_transferable"])
        self.assertEqual(1, len(found["queries"]))

    def test_search_appends_hints_and_records_a_failed_provider(self):
        from unittest.mock import patch

        from getbrolls.commands import execute

        args = build_parser().parse_args(
            [
                "search",
                "--project",
                str(self.project),
                "--query",
                "foguete decolando",
                "--provider",
                "pexels",
            ]
        )
        library.learn_query("foguete decolando", "youtube", "hit")
        with patch("getbrolls.providers.search", side_effect=ValueError("chave ausente")):
            with self.assertRaises(ValueError):
                execute(args)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        automatic = [q for q in data["queries"] if q.get("auto")]
        self.assertEqual(["pexels"], [q["provider"] for q in automatic])
        self.assertEqual("miss", automatic[0]["outcome"])
        with patch("getbrolls.providers.search", return_value=[]):
            result: Any = execute(args)
        hints = result["library_hints"]
        self.assertTrue(all(h["rights_not_transferable"] for h in hints))
        self.assertLessEqual(len(hints), 5)
        self.assertIn("foguete decolando", [h.get("query") for h in hints])

    def test_the_search_rung_mentions_the_library_without_changing_the_command(self):
        import shlex

        from getbrolls.guidance import next_action

        state = {
            "project": str(self.project),
            "counts": {"candidates": 0},
            "brief": {"beats": 1, "covered": 1, "missing": [], "conflicts": []},
        }
        plain = next_action(state)
        self.assertNotIn("biblioteca", plain["for_human"])
        library.learn_query("foguete decolando", "youtube", "hit")
        with_library = next_action(state)
        self.assertIn("biblioteca", with_library["for_human"])
        self.assertEqual(plain["command"], with_library["command"])
        build_parser().parse_args(shlex.split(with_library["command"])[2:])

    def test_the_cli_refuses_learn_without_anything_to_learn(self):
        error = run_cli(self, "learn", "--project", self.project, ok=False)
        self.assertIn("--query", error["error"])


class IsolationIsStructural(unittest.TestCase):
    """`tests/_isolation.py` era convenção: seis módulos importavam, o resto contava com a sorte.

    `discover -s tests` não importa `tests/__init__.py`, e unittest não tem hook de
    sessão, então não dá para pôr isso num lugar só. O que dá é garantir por teste:
    todo módulo que chega na biblioteca, na escada ou na CLI — direta ou
    indiretamente — importa `_isolation` antes, e nenhuma rodada toca `~/.getbrolls`.
    """

    REACHES_HOME = re.compile(r"\blibrary\b|\bguidance\b|next_action|commands\.execute|\bexecute\(|run_cli|gb\.py")

    def test_every_module_that_can_reach_the_personal_folder_imports_isolation(self):
        offenders = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            if self.REACHES_HOME.search(text) and "import _isolation" not in text:
                offenders.append(path.name)
        self.assertEqual([], offenders)

    def test_isolation_points_gb_home_away_from_the_real_one(self):
        self.assertEqual(os.environ["GB_HOME"], str(_isolation.GB_HOME))
        self.assertNotEqual(Path.home() / ".getbrolls", _isolation.GB_HOME)


if __name__ == "__main__":
    unittest.main()
