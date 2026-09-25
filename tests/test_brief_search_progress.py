"""Busca do beat que volta vazia não vira o mesmo comando para sempre.

O degrau `brief-search` sempre sugeria a primeira fonte pesquisável do beat.
Quando ela voltava sem nada, `status` repetia o comando idêntico em laço e nunca
tentava a próxima fonte permitida. Agora a busca vazia fica registrada no projeto,
o degrau passa para a fonte seguinte e, esgotadas todas, diz isso à pessoa.
"""

import copy
import shlex
import tempfile
import unittest
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)
from test_brief import VALID, write_brief

from getbrolls import cli, providers
from getbrolls.cli import build_parser
from getbrolls.ledger import Ledger


def one_beat_brief(project, sources, **beat):
    data = copy.deepcopy(VALID)
    data["beats"] = [dict(data["beats"][0], allowed_sources=sources, **beat)]
    if "pexels" in sources:
        data["rights"]["stock_allowed"] = True
    write_brief(project, data)
    # `status` lê um projeto existente; a árvore `brolls/` nasce no primeiro comando.
    Ledger(project)


def status_do(project):
    return cli.main(["status", "--project", project])["summary"]["do"]


def run(command):
    return cli.main(shlex.split(command)[2:])


class EmptySearchesMoveOn(unittest.TestCase):
    def test_following_do_through_two_empty_searches_reaches_the_person(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            commands, providers_asked = [], []
            with patch.object(
                providers,
                "search",
                side_effect=lambda name, *a, **k: providers_asked.append(name) or [],
            ):
                for _ in range(2):
                    action = status_do(tmp)
                    self.assertEqual("brief-search", action["step"])
                    commands.append(action["command"])
                    run(action["command"])
                final = status_do(tmp)
            # Duas buscas, duas fontes diferentes: nenhum comando repetido.
            self.assertEqual(["commons", "nasa"], providers_asked)
            self.assertEqual(2, len(set(commands)), commands)
            self.assertIn("--provider commons", commands[0])
            self.assertIn("--provider nasa", commands[1])
            # Todas as fontes vazias: a pessoa decide, e nada de comando que falha de novo.
            self.assertEqual("brief-exhausted", final["step"])
            self.assertIsNone(final["command"])
            self.assertTrue(final["blocking_human"])
            self.assertIn("abertura", final["for_human"])
            self.assertIn("commons", final["for_human"])
            self.assertIn("nasa", final["for_human"])
            self.assertIn("allowed_sources", final["for_human"])

    def test_a_search_that_found_something_is_not_recorded_as_empty(self):
        from getbrolls.models import candidate

        found = [candidate("commons", "1", "Achado", "https://commons.wikimedia.org/wiki/File:A.jpg")]
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            command = status_do(tmp)["command"]
            with patch.object(providers, "search", return_value=found):
                run(command)
            self.assertNotEqual("brief-search", status_do(tmp)["step"])

    def test_dry_run_does_not_count_as_a_tried_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            command = status_do(tmp)["command"]
            with patch.object(providers, "search", return_value=[]):
                run(command + " --dry-run")
            self.assertEqual(command, status_do(tmp)["command"])

    def test_the_brief_command_also_moves_to_the_next_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            with patch.object(providers, "search", return_value=[]):
                run(status_do(tmp)["command"])
            beat = cli.main(["brief", "--project", tmp])["beats"][0]
            self.assertIn("--provider nasa", beat["commands"]["search"])


def exhaust(project):
    """Segue `do` até o beat esgotar, com toda fonte respondendo vazio."""
    with patch.object(providers, "search", return_value=[]):
        for _ in range(6):
            action = status_do(project)
            if action["step"] != "brief-search":
                return action
            run(action["command"])
    raise AssertionError("o beat não esgotou")


def add_approved_item(project):
    from getbrolls.models import approve, candidate, set_segment

    ledger = Ledger(project)
    item = candidate("youtube", "aprovado123", "Aprovado", "https://www.youtube.com/watch?v=aprovado123")
    set_segment(item, 0, 2)
    item["preview"]["contact_sheet_path"] = "previews/aprovado.jpg"
    approve(item, "Pessoa Humana")
    ledger.save_many("fixture", [ledger.add(item)])


class ExhaustedBeatWaitsForApprovedWork(unittest.TestCase):
    """O beat esgotado não pode travar item já aprovado a caminho da entrega."""

    def test_an_approved_item_goes_to_permit_before_the_exhausted_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            self.assertEqual("brief-exhausted", exhaust(tmp)["step"])
            add_approved_item(tmp)
            self.assertEqual("permit", status_do(tmp)["step"])

    def test_the_question_names_every_way_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            text = exhaust(tmp)["for_human"]
        for option in ('"queries"', "allowed_sources", "próprio material", "remova o beat do BRIEF.md"):
            self.assertIn(option, text)


class NewQueriesAreTried(unittest.TestCase):
    """Busca nova em `queries` vale no começo ou no fim da lista."""

    def _after_exhausting(self, queries):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"], queries=["eclipse registro"])
            exhaust(tmp)
            one_beat_brief(tmp, ["commons", "nasa"], queries=queries)
            return status_do(tmp)

    def test_a_query_appended_at_the_end_is_searched(self):
        action = self._after_exhausting(["eclipse registro", "sombra da lua"])
        self.assertEqual("brief-search", action["step"])
        self.assertIn("--query 'sombra da lua'", action["command"])
        self.assertIn("--provider commons", action["command"])

    def test_a_query_prepended_at_the_start_is_searched(self):
        action = self._after_exhausting(["sombra da lua", "eclipse registro"])
        self.assertEqual("brief-search", action["step"])
        self.assertIn("--query 'sombra da lua'", action["command"])


class SourcesWithoutKeyAreSkipped(unittest.TestCase):
    """M-a: fonte sem chave de API não é sugerida; só sobrando ela, a pessoa ouve qual chave falta."""

    def test_the_keyless_source_is_skipped_and_then_named(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PEXELS_API_KEY": ""}):
            one_beat_brief(tmp, ["pexels", "commons"], stock=True)
            first = status_do(tmp)
            self.assertIn("--provider commons", first["command"])
            with patch.object(providers, "search", return_value=[]):
                run(first["command"])
            after = status_do(tmp)
        self.assertNotIn("--provider pexels", after["command"] or "")
        self.assertIn("PEXELS_API_KEY", after["for_human"])

    def test_only_keyless_sources_left_is_a_question_without_command(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PEXELS_API_KEY": ""}):
            one_beat_brief(tmp, ["pexels", "commons"], stock=True)
            with patch.object(providers, "search", return_value=[]):
                run(status_do(tmp)["command"])
            action = status_do(tmp)
        self.assertEqual("brief-unavailable", action["step"])
        self.assertIsNone(action["command"])
        self.assertTrue(action["blocking_human"])
        self.assertIn("PEXELS_API_KEY", action["for_human"])

    def test_all_sources_keyless_from_the_start_is_the_same_question(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PEXELS_API_KEY": ""}):
            one_beat_brief(tmp, ["pexels"], stock=True)
            action = status_do(tmp)
        self.assertEqual("brief-unavailable", action["step"])
        self.assertIsNone(action["command"])
        self.assertIn("PEXELS_API_KEY", action["for_human"])

    def test_an_approved_item_goes_to_permit_before_the_key_question(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PEXELS_API_KEY": ""}):
            one_beat_brief(tmp, ["pexels"], stock=True)
            add_approved_item(tmp)
            self.assertEqual("permit", status_do(tmp)["step"])


class BeatsWithoutApiSearch(unittest.TestCase):
    """Beat só de Instagram/TikTok/material próprio: o passo é trazer o link ou o arquivo."""

    def _do(self, sources, **extra):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PEXELS_API_KEY": ""}):
            one_beat_brief(tmp, sources, **extra)
            return status_do(tmp)

    def test_an_instagram_only_beat_asks_for_the_url_with_resolve(self):
        action = self._do(["instagram"])
        self.assertEqual("brief-resolve", action["step"])
        self.assertTrue(action["blocking_human"])
        self.assertIn(" resolve ", action["command"])
        self.assertIn("--url URL_PUBLICA", action["command"])
        self.assertIn("--shot abertura", action["command"])
        self.assertNotIn("TERMOS_DA_BUSCA", action["command"])
        self.assertNotIn(" search ", action["command"])
        build_parser().parse_args(shlex.split(action["command"])[2:])

    def test_a_local_only_beat_asks_for_the_file_with_resolve(self):
        action = self._do(["local"])
        self.assertEqual("brief-resolve", action["step"])
        self.assertIn("--file ARQUIVO", action["command"])
        self.assertIn("--shot abertura", action["command"])
        self.assertIn("arquivo", action["for_human"])

    def test_a_mixed_keyless_beat_does_not_claim_earlier_searches(self):
        """Pexels sem chave + Instagram: nada foi buscado, e o link continua sendo uma saída."""
        action = self._do(["pexels", "instagram"], stock=True)
        self.assertEqual("brief-unavailable", action["step"])
        self.assertIsNone(action["command"])
        self.assertIn("PEXELS_API_KEY", action["for_human"])
        self.assertNotIn("já voltaram vazias", action["for_human"])
        self.assertIn("resolve --url", action["for_human"])


class MixedBeatsSayWhatWasReallySearched(unittest.TestCase):
    """Beat com fonte pesquisável e fonte só por link/arquivo: a frase não inventa busca."""

    def _exhausted(self, sources, env=None, **extra):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", env or {}):
            one_beat_brief(tmp, sources, **extra)
            return exhaust(tmp)

    def test_youtube_plus_instagram_exhausted_offers_the_link_route(self):
        action = self._exhausted(["youtube", "instagram"])
        self.assertEqual("brief-exhausted", action["step"])
        self.assertNotIn("todas as fontes que o BRIEF.md permite", action["for_human"])
        self.assertIn("todas as fontes que consigo pesquisar", action["for_human"])
        self.assertIn("resolve --url URL_PUBLICA --shot abertura", action["for_human"])

    def test_pexels_with_key_plus_instagram_exhausted_offers_the_link_route(self):
        action = self._exhausted(["pexels", "instagram"], env={"PEXELS_API_KEY": "chave-de-teste"}, stock=True)
        self.assertEqual("brief-exhausted", action["step"])
        self.assertNotIn("todas as fontes que o BRIEF.md permite", action["for_human"])
        self.assertIn("resolve --url URL_PUBLICA --shot abertura", action["for_human"])

    def test_a_record_from_a_source_removed_from_the_brief_is_not_an_earlier_search(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PEXELS_API_KEY": ""}):
            one_beat_brief(tmp, ["youtube", "instagram"])
            with patch.object(providers, "search", return_value=[]):
                run(status_do(tmp)["command"])
            one_beat_brief(tmp, ["pexels", "instagram"], stock=True)
            action = status_do(tmp)
        self.assertEqual("brief-unavailable", action["step"])
        self.assertNotIn("já voltaram vazias", action["for_human"])

    def test_own_files_are_offered_with_resolve_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PEXELS_API_KEY": ""}):
            one_beat_brief(tmp, ["pexels", "local"], stock=True)
            action = status_do(tmp)
        self.assertEqual("brief-unavailable", action["step"])
        self.assertIn("resolve --file ARQUIVO --shot abertura", action["for_human"])
        self.assertNotIn("--url", action["for_human"])
        self.assertNotIn("material de local", action["for_human"])

    def test_link_and_file_sources_get_both_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["instagram", "local"])
            action = status_do(tmp)
        self.assertEqual("brief-resolve", action["step"])
        self.assertIn("resolve --url URL_PUBLICA --shot abertura", action["for_human"])
        self.assertIn("resolve --file ARQUIVO --shot abertura", action["for_human"])

    def test_steps_that_wait_for_the_person_offer_a_way_out(self):
        for sources, extra, step in (
            (["instagram"], {}, "brief-resolve"),
            (["pexels"], {"stock": True}, "brief-unavailable"),
        ):
            with (
                self.subTest(step=step),
                tempfile.TemporaryDirectory() as tmp,
                patch.dict("os.environ", {"PEXELS_API_KEY": ""}),
            ):
                one_beat_brief(tmp, sources, **extra)
                action = status_do(tmp)
                self.assertEqual(step, action["step"])
                self.assertIn("remova o beat do BRIEF.md ou siga sem ele", action["for_human"])


class EmptySearchRecord(unittest.TestCase):
    def test_a_malformed_record_is_tolerated_and_rebuilt(self):
        """M-b: registro editado à mão nunca vira TypeError."""
        for broken in (None, "x", 7, [1, None, "a", {"shot": "abertura"}]):
            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as tmp:
                one_beat_brief(tmp, ["commons", "nasa"])
                ledger = Ledger(tmp)
                ledger.data["empty_searches"] = broken
                ledger.save("fixture")
                self.assertEqual("brief-search", status_do(tmp)["step"])
                with patch.object(providers, "search", return_value=[]):
                    run(status_do(tmp)["command"])
                self.assertIn("--provider nasa", status_do(tmp)["command"])

    def test_the_record_is_its_own_journal_operation(self):
        """M-c: a gravação do registro não aparece como um `search` sem candidato."""
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            with patch.object(providers, "search", return_value=[]):
                run(status_do(tmp)["command"])
            journal = cli.main(["status", "--project", tmp])["journal"]
        self.assertEqual("search-empty", journal["last"]["operation"])

    def test_only_an_empty_final_result_is_recorded(self):
        """M-d: se o encurtamento achou algo, a busca não foi vazia."""
        from getbrolls.models import candidate

        long_query = "registro real do eclipse total visto da cidade ao meio dia"
        found = [candidate("commons", "9", "Achado", "https://commons.wikimedia.org/wiki/File:B.jpg")]

        def answer(name, query, *args, **kwargs):
            return [] if query == long_query else found

        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            with patch.object(providers, "search", side_effect=answer):
                result = cli.main(
                    ["search", "--project", tmp, "--provider", "commons", "--query", long_query, "--shot", "abertura"]
                )
            self.assertEqual(1, len(result["items"]))
            self.assertEqual([], Ledger(tmp).data.get("empty_searches", []))


if __name__ == "__main__":
    unittest.main()
