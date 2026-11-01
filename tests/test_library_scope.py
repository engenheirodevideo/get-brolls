"""A biblioteca pessoal só responde dentro do contexto de quem pergunta.

Um projeto de cliente enxerga o que foi guardado sob o próprio cliente e o que foi guardado sem
cliente nenhum; o que pertence a outro cliente nunca aparece, nem em `library --search` nem nas
pistas (`library_hints`) anexadas ao `search`.
"""

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  # pylint: disable=unused-import
from _cli import run_cli
from test_library import LibraryBase, approved_candidate

from getbrolls import layout, library, search_commands
from getbrolls.cli import build_parser
from getbrolls.commands import execute
from getbrolls.memory import remember

ACME_QUERY = "foguete acme decolando"
BETA_QUERY = "foguete beta decolando"
OWN_QUERY = "foguete próprio decolando"
ACME_ASSET = "https://www.youtube.com/watch?v=acme"
BETA_ASSET = "https://www.youtube.com/watch?v=beta"
OWN_ASSET = "https://www.youtube.com/watch?v=own"


def queries_of(answer):
    return sorted(q["query"] for q in answer["queries"])


def assets_of(answer):
    return sorted(a["source_url"] for a in answer["assets"])


class ScopeBase(LibraryBase):
    """Dois clientes, um projeto sem cliente e um projeto com `client: null`, cada um com a sua pasta."""

    def make_project(self, name, client=None, *, with_file=True):
        folder = Path(self.tmp.name) / name
        folder.mkdir(parents=True)
        if with_file:
            layout.write_project(folder, layout.new_project_doc(client=client))
        return folder

    def setUp(self):
        super().setUp()
        self.acme = self.make_project("acme-video", "acme")
        self.beta = self.make_project("beta-video", "beta")
        self.own = self.make_project("own-video", with_file=False)
        self.null_client = self.make_project("null-client-video")

    def learn_everywhere(self):
        library.learn_query(ACME_QUERY, "youtube", "hit", project=self.acme)
        library.learn_query(BETA_QUERY, "youtube", "hit", project=self.beta)
        library.learn_query(OWN_QUERY, "youtube", "hit", project=self.own)
        library.learn_preference("Foguete acme: sempre plano aberto.", by="Ana", project=self.acme)
        library.learn_preference("Foguete beta: sempre plano fechado.", by="Beto", project=self.beta)
        library.learn_preference("Foguete próprio: plano médio.", project=self.own)
        for folder, url in ((self.acme, ACME_ASSET), (self.beta, BETA_ASSET), (self.own, OWN_ASSET)):
            ledger, c = approved_candidate(folder, source_url=url)
            remember(ledger, c, "approved", "Motivo escrito neste projeto.", "Bruno")
            library.learn_from_candidate(folder, c["id"])


class WhoseEntriesAreVisibleTests(ScopeBase):
    def test_a_client_project_sees_its_own_client_and_the_unscoped_entries_only(self):
        self.learn_everywhere()
        found = library.search("foguete", project=self.acme)
        self.assertEqual("client:acme", found["scope"])
        self.assertEqual(sorted([ACME_QUERY, OWN_QUERY]), queries_of(found))
        self.assertEqual(sorted([ACME_ASSET, OWN_ASSET]), assets_of(found))
        self.assertEqual(
            ["Foguete acme: sempre plano aberto.", "Foguete próprio: plano médio."],
            sorted(p["text"] for p in found["preferences"]),
        )
        self.assertNotIn("beta", json.dumps(found))

    def test_the_other_client_gets_the_mirror_image(self):
        self.learn_everywhere()
        found = library.search("foguete", project=self.beta)
        self.assertEqual("client:beta", found["scope"])
        self.assertEqual(sorted([BETA_QUERY, OWN_QUERY]), queries_of(found))
        self.assertEqual(sorted([BETA_ASSET, OWN_ASSET]), assets_of(found))
        self.assertNotIn("acme", json.dumps(found))

    def test_a_search_with_no_client_sees_only_what_was_recorded_without_one(self):
        self.learn_everywhere()
        for project in (None, self.own, self.null_client):
            with self.subTest(project=project):
                found = library.search("foguete", project=project)
                self.assertEqual([OWN_QUERY], queries_of(found))
                self.assertEqual([OWN_ASSET], assets_of(found))
                self.assertEqual(["Foguete próprio: plano médio."], [p["text"] for p in found["preferences"]])
                text = json.dumps(found)
                self.assertNotIn("acme", text)
                self.assertNotIn("beta", text)

    def test_the_answer_names_the_scope_only_when_a_client_is_active(self):
        self.learn_everywhere()
        self.assertEqual("client:acme", library.search("foguete", project=self.acme)["scope"])
        self.assertNotIn("scope", library.search("foguete"))
        self.assertNotIn("scope", library.search("foguete", project=self.null_client))

    def test_the_scope_is_named_when_the_library_is_off(self):
        os.environ["GB_LIBRARY"] = "off"
        self.assertEqual("client:acme", library.search("foguete", project=self.acme)["scope"])
        self.assertNotIn("scope", library.search("foguete"))

    def test_an_entry_recorded_before_clients_existed_stays_visible_everywhere(self):
        legacy = {
            "query": "foguete antigo",
            "provider": "youtube",
            "outcome": "hit",
            "count": 1,
            "at": "2026-01-01T00:00:00Z",
            "note": None,
        }
        library.library_dir().mkdir(parents=True)
        library.index_path().write_text(
            json.dumps({"schema_version": 1, "assets": [], "queries": [legacy], "providers": {}, "preferences": []}),
            encoding="utf-8",
        )
        for project in (None, self.own, self.acme, self.beta):
            with self.subTest(project=project):
                self.assertEqual(["foguete antigo"], queries_of(library.search("foguete", project=project)))

    def test_providers_never_count_a_query_the_caller_cannot_see(self):
        library.learn_query(ACME_QUERY, "youtube", "hit", project=self.acme)
        library.learn_query(BETA_QUERY, "youtube", "miss", project=self.beta)
        library.learn_query(BETA_QUERY + " dois", "youtube", "miss", project=self.beta)
        library.learn_query(OWN_QUERY, "youtube", "hit", project=self.own)
        found = library.search("foguete", project=self.acme)
        self.assertEqual({"hit": 2, "miss": 0}, found["providers"]["youtube"]["outcomes"])
        self.assertEqual({"hit": 1, "miss": 0}, library.search("foguete")["providers"]["youtube"]["outcomes"])

    def test_the_stored_provider_counts_are_used_when_nothing_is_hidden(self):
        library.learn_query(OWN_QUERY, "youtube", "hit", project=self.own)
        library.learn_query(OWN_QUERY, "youtube", "miss", project=self.own)
        found = library.search("foguete", project=self.acme)
        self.assertEqual({"hit": 1, "miss": 1}, found["providers"]["youtube"]["outcomes"])


class ProviderCounterTests(ScopeBase):
    def counts_of(self, project):
        return library.search("foguete", project=project)["providers"].get("pexels", {}).get("outcomes")

    def test_another_clients_misses_never_reach_a_counter_even_after_its_queries_are_evicted(self):
        library.learn_query(OWN_QUERY, "pexels", "hit", project=self.own)
        library.learn_query(ACME_QUERY, "pexels", "hit", project=self.acme)
        for suffix in ("um", "dois"):
            library.learn_query(f"foguete beta {suffix}", "pexels", "miss", auto=True, project=self.beta)
        self.assertEqual({"hit": 1, "miss": 0}, self.counts_of(self.own))
        self.assertEqual({"hit": 2, "miss": 0}, self.counts_of(self.acme))
        with patch.object(library, "MAX_QUERIES", 4):
            for n in range(5):
                library.learn_query(f"foguete próprio {n}", "pexels", "hit", project=self.own)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual([], [q for q in data["queries"] if q.get("client") == "beta"])
        self.assertEqual(0, data["providers"]["pexels"]["outcomes"]["miss"])
        self.assertEqual(6, self.counts_of(self.own)["hit"])
        self.assertEqual(0, self.counts_of(self.own)["miss"])
        self.assertEqual(0, self.counts_of(self.acme)["miss"])
        self.assertEqual(0, self.counts_of(self.beta)["miss"])

    def test_a_client_counter_is_its_own_visible_queries_on_top_of_the_unscoped_ones(self):
        library.learn_query(OWN_QUERY, "pexels", "miss", project=self.own)
        library.learn_query(ACME_QUERY, "pexels", "hit", project=self.acme)
        library.learn_query(ACME_QUERY, "pexels", "hit", project=self.acme)
        self.assertEqual({"hit": 2, "miss": 1}, self.counts_of(self.acme))
        self.assertEqual({"hit": 0, "miss": 1}, self.counts_of(self.own))
        self.assertEqual({"hit": 0, "miss": 1}, self.counts_of(self.beta))


class WhoIsTheClientTests(ScopeBase):
    def test_a_layout_0_project_takes_its_client_from_the_roteiro(self):
        legacy = self.make_project("legacy-video", with_file=False)
        (legacy / "ROTEIRO.md").write_text(
            "---\ntype: roteiro\ngenero: reels\ntema: Teste\ncliente: acme\n---\n\n## Cena 1\n", encoding="utf-8"
        )
        library.learn_query(ACME_QUERY, "youtube", "hit", project=legacy)
        library.learn_preference("Foguete acme: plano aberto.", project=legacy)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(["acme"], [q["client"] for q in data["queries"]])
        self.assertEqual(["acme"], [p["client"] for p in data["preferences"]])
        found = library.search("foguete", project=legacy)
        self.assertEqual("client:acme", found["scope"])
        self.assertEqual([ACME_QUERY], queries_of(found))
        self.assertEqual([], queries_of(library.search("foguete", project=self.beta)))
        self.assertEqual([], queries_of(library.search("foguete", project=self.own)))

    def test_a_roteiro_that_is_not_the_getbrolls_one_names_no_client(self):
        legacy = self.make_project("legacy-video", with_file=False)
        (legacy / "ROTEIRO.md").write_text("---\ncliente: acme\n---\n", encoding="utf-8")
        self.assertEqual((None, None), library.project_client(legacy))

    def test_a_roteiro_client_that_is_not_a_slug_is_refused_on_write_and_personal_on_read(self):
        self.learn_everywhere()
        for index, value in enumerate(("Acme Corp", "acme # x")):
            with self.subTest(value=value):
                legacy = self.make_project(f"legacy-{index}", with_file=False)
                (legacy / "ROTEIRO.md").write_text(f"---\ntype: roteiro\ncliente: {value}\n---\n", encoding="utf-8")
                client, problem = library.project_client(legacy)
                self.assertIsNone(client)
                self.assertIn("ROTEIRO.md", problem or "")
                with self.assertRaisesRegex(ValueError, "ROTEIRO.md"):
                    library.learn_query(ACME_QUERY, "youtube", "hit", project=legacy)
                with self.assertRaisesRegex(ValueError, "ROTEIRO.md"):
                    library.learn_preference("Foguete: plano aberto.", project=legacy)
                found = library.search("foguete", project=legacy)
                self.assertNotIn("scope", found)
                self.assertEqual([OWN_QUERY], queries_of(found))

    def test_a_blank_roteiro_client_stays_personal(self):
        legacy = self.make_project("legacy-video", with_file=False)
        (legacy / "ROTEIRO.md").write_text("---\ntype: roteiro\ncliente:\n---\n", encoding="utf-8")
        self.assertEqual((None, None), library.project_client(legacy))
        library.learn_query(OWN_QUERY, "youtube", "hit", project=legacy)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertNotIn("client", data["queries"][0])

    def test_a_roteiro_saved_with_a_byte_order_mark_still_names_its_client(self):
        legacy = self.make_project("legacy-video", with_file=False)
        (legacy / "ROTEIRO.md").write_text(
            "---\ntype: roteiro\ngenero: reels\ntema: Teste\ncliente: acme\n---\n\n## Cena 1\n",
            encoding="utf-8-sig",
        )
        self.assertEqual(("acme", None), library.project_client(legacy))
        library.learn_query(ACME_QUERY, "youtube", "hit", project=legacy)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(["acme"], [q["client"] for q in data["queries"]])

    def test_the_project_json_wins_over_the_roteiro(self):
        (self.acme / "ROTEIRO.md").write_text("---\ntype: roteiro\ncliente: beta\n---\n", encoding="utf-8")
        self.assertEqual(("acme", None), library.project_client(self.acme))

    def test_a_client_with_a_trailing_newline_is_refused_like_everywhere_else(self):
        sneaky = self.make_project("sneaky-video", with_file=False)
        doc = layout.new_project_doc(client="acme")
        doc["client"] = "acme\n"
        (sneaky / layout.PROJECT_FILE).write_text(json.dumps(doc), encoding="utf-8")
        client, problem = library.project_client(sneaky)
        self.assertIsNone(client)
        self.assertTrue(problem)
        with self.assertRaisesRegex(ValueError, "project.json"):
            library.learn_query(ACME_QUERY, "youtube", "hit", project=sneaky)
        self.assertNotIn("scope", library.search("foguete", project=sneaky))


class WhatIsRecordedTests(ScopeBase):
    def test_entries_learned_in_a_client_project_carry_the_client(self):
        self.learn_everywhere()
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        for kind in ("queries", "preferences", "assets"):
            with self.subTest(kind=kind):
                clients = sorted(item.get("client") or "-" for item in data[kind])
                self.assertEqual(["-", "acme", "beta"], clients)

    def test_entries_learned_without_a_client_stay_byte_identical_to_2_5(self):
        library.learn_query(OWN_QUERY, "youtube", "hit", project=self.own)
        library.learn_query(BETA_QUERY, "youtube", "hit", project=self.null_client)
        library.learn_preference("Foguete próprio: plano médio.", project=self.null_client)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        for item in data["queries"] + data["preferences"]:
            self.assertNotIn("client", item)

    def test_the_same_search_in_two_clients_stays_two_entries(self):
        library.learn_query(ACME_QUERY, "youtube", "hit", project=self.acme)
        library.learn_query(ACME_QUERY, "youtube", "hit", project=self.beta)
        library.learn_query(ACME_QUERY, "youtube", "hit", project=self.acme)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual([("acme", 2), ("beta", 1)], sorted((q["client"], q["count"]) for q in data["queries"]))

    def test_the_same_clip_in_two_clients_stays_two_entries(self):
        learned = {}
        for folder in (self.acme, self.beta):
            ledger, c = approved_candidate(folder, source_url=ACME_ASSET)
            remember(ledger, c, "approved", "Motivo.", "Bruno")
            library.learn_from_candidate(folder, c["id"])
            learned[folder] = c["id"]
        library.learn_from_candidate(self.acme, learned[self.acme], shot="beat-02")
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual([("acme", 2), ("beta", 1)], sorted((a["client"], len(a["used_by"])) for a in data["assets"]))

    def test_an_unscoped_clip_learned_again_in_a_client_project_shows_once_in_that_client(self):
        for folder in (self.own, self.acme):
            ledger, c = approved_candidate(folder, source_url=OWN_ASSET)
            remember(ledger, c, "approved", f"Motivo escrito em {folder.name}.", "Bruno")
            library.learn_from_candidate(folder, c["id"])
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(["-", "acme"], sorted(a.get("client") or "-" for a in data["assets"]))
        in_acme = library.search("foguete", project=self.acme)["assets"]
        self.assertEqual(["acme"], [a["client"] for a in in_acme])
        in_own = library.search("foguete", project=self.own)["assets"]
        self.assertEqual([None], [a.get("client") for a in in_own])
        self.assertNotIn("acme", json.dumps(in_own))
        self.assertEqual(1, len(library.search("foguete", project=self.beta)["assets"]))

    def test_a_project_json_that_cannot_be_read_never_records_an_entry_for_everyone(self):
        broken = self.make_project("broken-video", with_file=False)
        (broken / layout.PROJECT_FILE).write_text("{ não é json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "project.json"):
            library.learn_query(ACME_QUERY, "youtube", "hit", project=broken)
        with self.assertRaisesRegex(ValueError, "project.json"):
            library.learn_preference("Foguete: plano aberto.", project=broken)
        self.assertFalse(library.index_path().exists())

    def test_a_project_json_that_cannot_be_read_sees_only_the_unscoped_entries(self):
        self.learn_everywhere()
        broken = self.make_project("broken-video", with_file=False)
        (broken / layout.PROJECT_FILE).write_text("{ não é json", encoding="utf-8")
        found = library.search("foguete", project=broken)
        self.assertEqual([OWN_QUERY], queries_of(found))
        self.assertNotIn("scope", found)

    def test_a_provider_failure_found_in_a_client_project_is_recorded_under_that_client(self):
        context = SimpleNamespace(dry_run=False, args=SimpleNamespace(project=str(self.acme)))
        search_commands._search_record_provider_failure(  # pylint: disable=protected-access
            context, "youtube", ACME_QUERY, ValueError("fora do ar")
        )
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(["acme"], [q["client"] for q in data["queries"]])
        self.assertEqual([], queries_of(library.search("foguete", project=self.beta)))


class HintsTests(ScopeBase):
    def test_hints_come_only_from_the_callers_scope(self):
        self.learn_everywhere()
        hints = library.hints("foguete", limit=20, project=self.acme)
        urls = sorted(h["source_url"] for h in hints if h["kind"] == "asset")
        seen = sorted(h["query"] for h in hints if h["kind"] == "query")
        self.assertEqual(sorted([ACME_ASSET, OWN_ASSET]), urls)
        self.assertEqual(sorted([ACME_QUERY, OWN_QUERY]), seen)
        self.assertNotIn("beta", json.dumps(hints))

    def test_hints_with_no_project_see_only_the_unscoped_entries(self):
        self.learn_everywhere()
        found = library.hints("foguete", limit=20)
        self.assertEqual([OWN_QUERY], [h["query"] for h in found if h["kind"] == "query"])
        text = json.dumps(found)
        self.assertNotIn("acme", text)
        self.assertNotIn("beta", text)


class CommandLineTests(ScopeBase):
    """Pela CLI de verdade: `--project` decide o escopo e o JSON o diz."""

    def run_json(self, *args, project):
        env = {"GB_HOME": str(self.home)}
        return run_cli(*args, project=project, env=env)

    def test_library_search_answers_for_the_project_it_was_asked_from(self):
        self.run_json("learn", "--query", ACME_QUERY, "--provider", "youtube", "--outcome", "hit", project=self.acme)
        self.run_json("learn", "--query", BETA_QUERY, "--provider", "youtube", "--outcome", "hit", project=self.beta)
        self.run_json("learn", "--preference", "Foguete: nunca em preto.", project=self.own)
        for project, scope, expected in (
            (self.acme, "client:acme", [ACME_QUERY]),
            (self.beta, "client:beta", [BETA_QUERY]),
            (self.own, None, []),
            (self.null_client, None, []),
        ):
            with self.subTest(project=project.name):
                found = self.run_json("library", "--search", "foguete", project=project)
                self.assertEqual(scope, found.get("scope"))
                self.assertEqual(expected, queries_of(found))
                self.assertEqual(["Foguete: nunca em preto."], [p["text"] for p in found["preferences"]])
        raw = library.index_path().read_text(encoding="utf-8")
        self.assertNotIn(str(self.acme), raw)

    def test_the_search_hints_never_carry_another_clients_entries(self):
        self.run_json("learn", "--query", BETA_QUERY, "--provider", "youtube", "--outcome", "hit", project=self.beta)
        self.assertEqual([], library.hints(BETA_QUERY, project=self.acme))
        self.assertEqual(1, len(library.hints(BETA_QUERY, project=self.beta)))

    def test_search_attaches_hints_of_its_own_scope_and_records_failures_under_its_client(self):
        library.learn_query(BETA_QUERY, "youtube", "hit", project=self.beta)
        library.learn_query(OWN_QUERY, "youtube", "hit", project=self.own)
        args = build_parser().parse_args(
            ["search", "--project", str(self.acme), "--query", "foguete", "--provider", "pexels"]
        )
        with patch("getbrolls.providers.search", return_value=[]):
            result: Any = execute(args)
        self.assertEqual([OWN_QUERY], [h["query"] for h in result["library_hints"]])
        with (
            patch("getbrolls.providers.search", side_effect=ValueError("chave ausente")),
            self.assertRaises(ValueError),
        ):
            execute(args)
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        failed = [q for q in data["queries"] if q.get("auto")]
        self.assertEqual([("pexels", "acme")], [(q["provider"], q["client"]) for q in failed])


if __name__ == "__main__":
    unittest.main()
