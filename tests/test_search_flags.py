"""`search --shot` e `search --dry-run`: ligar ao beat, e diagnosticar sem sujar o projeto.

Quatro dos cinco executores da rodada cega bateram em `search --shot` recusado embora
SKILL.md, `references/` e `brief --beat` prometessem a flag; três poluíram as contagens
de `status` com buscas que eram só diagnóstico.
"""

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import library, providers
from getbrolls.commands import execute
from getbrolls.models import candidate
from getbrolls.runtime import OperationError, audited


def found(n=2):
    return [
        candidate("youtube", f"id{i}", f"Vídeo {i}", f"https://www.youtube.com/watch?v=aaaaaaaaaa{i}") for i in range(n)
    ]


def args(project, **extra):
    base = {
        "command": "search",
        "project": str(project),
        "env_file": None,
        "confirm_format_change": False,
        "provider": "youtube",
        "query": "foguete SLS decolando",
        "limit": 5,
        "intent": "literal",
        "shot": None,
        "dry_run": False,
    }
    base.update(extra)
    return types.SimpleNamespace(**base)


def manifest(project):
    """Manifesto como está no disco; `{"items": []}` quando o projeto nem foi criado."""
    path = Path(project) / "brolls" / "manifest.json"
    if not path.is_file():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


class ShotFlag(unittest.TestCase):
    def test_shot_suffixes_the_id_and_records_the_beat_like_resolve_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found()):
                result = audited(args(tmp, shot="abertura"), execute)
            for item in result["items"]:
                self.assertTrue(item["id"].endswith(":shot:abertura"), item["id"])
                self.assertEqual("abertura", item["shot"])
            saved = manifest(tmp)["items"]
            self.assertEqual({"abertura"}, {c["shot"] for c in saved})

    def test_without_shot_nothing_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp), execute)
            self.assertNotIn("shot", result["items"][0])
            self.assertNotIn(":shot:", result["items"][0]["id"])

    def test_a_shot_with_a_path_separator_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("../fuga", "com espaço", "a/b", ""):
                with self.subTest(bad=bad):
                    with patch.object(providers, "search", return_value=found(1)):
                        if bad == "":
                            # Vazio é "não informado", não erro: o comportamento antigo.
                            audited(args(tmp + "/vazio", shot=bad), execute)
                            continue
                        with self.assertRaises(Exception) as caught:
                            audited(args(tmp, shot=bad), execute)
                    self.assertIn("--shot", str(caught.exception))


class DryRun(unittest.TestCase):
    def test_dry_run_lists_results_without_touching_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(3)):
                first = audited(args(tmp), execute)
            before = manifest(tmp)
            self.assertEqual(3, len(before["items"]))
            with patch.object(providers, "search", return_value=found(3)):
                result = audited(args(tmp, dry_run=True), execute)
            self.assertTrue(result["dry_run"])
            self.assertEqual(3, len(result["items"]))
            self.assertIn("nada foi registrado", result["note"])
            # Byte por byte: a busca de diagnóstico não pode mexer no projeto.
            self.assertEqual(before, manifest(tmp))
            self.assertEqual(len(first["items"]), len(before["items"]))

    def test_dry_run_on_an_empty_project_registers_nothing_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(2)):
                result = audited(args(tmp, dry_run=True), execute)
            self.assertEqual(2, len(result["items"]))
            self.assertEqual([], manifest(tmp)["items"])

    def test_dry_run_still_carries_the_shot_in_the_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp, dry_run=True, shot="abertura"), execute)
            self.assertEqual("abertura", result["items"][0]["shot"])
            self.assertEqual([], manifest(tmp)["items"])

    def test_dry_run_does_not_learn_the_automatic_miss(self):
        """Diagnóstico não vira memória editorial: a fonte que falhou no teste não fica marcada."""
        boom = providers.ProviderError("chave ausente")
        query = "consulta exclusiva do teste de dry-run"

        def learned():
            return [q for q in library.load_index()["queries"] if q.get("query") == query]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", side_effect=boom), self.assertRaises(OperationError):
                audited(args(tmp, dry_run=True, query=query), execute)
            self.assertEqual([], learned())
            # Sem `--dry-run`, a mesma falha continua sendo aprendida.
            with patch.object(providers, "search", side_effect=boom), self.assertRaises(OperationError):
                audited(args(tmp, query=query), execute)
            rows = learned()
            self.assertEqual(1, len(rows))
            self.assertTrue(rows[0]["auto"])
            self.assertEqual("miss", rows[0]["outcome"])

    def test_a_normal_search_reports_dry_run_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp), execute)
            self.assertFalse(result["dry_run"])
            self.assertNotIn("nada foi registrado", result.get("note") or "")


YTDLP_FLAT = {
    "entries": [
        {
            "id": "AV8Rv74TPGE",
            "title": "Decolagem do SLS",
            "channel": "NASA",
            "duration": 95,
            "thumbnails": [{"url": "https://i.ytimg.com/vi/AV8Rv74TPGE/hq.jpg"}],
        },
        {
            "id": "BV8Rv74TPGF",
            "title": "Separação dos boosters",
            "uploader": "Canal do Espaço",
            "duration": 42,
            "thumbnails": [],
        },
        {"id": "CV8Rv74TPGH", "title": "Vista da órbita", "duration": 12, "thumbnails": []},
        {"id": "DV8Rv74TPGI", "title": "Retorno da cápsula", "duration": 8, "thumbnails": []},
    ]
}


class ProviderFactsInTheListing(unittest.TestCase):
    """O que o YouTube já responde na busca: canal e duração, sem outra chamada."""

    def stub(self, tmp, **extra):
        from getbrolls import social

        with patch.object(social, "run", return_value=(json.dumps(YTDLP_FLAT), [])):
            return audited(args(tmp, limit=5, **extra), execute)

    def test_channel_uploader_and_duration_reach_the_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.stub(tmp)
            rows = {r["id"]: r for r in result["items"]}
            first = rows["youtube:AV8Rv74TPGE"]
            self.assertEqual("NASA", first["channel"])
            self.assertEqual("NASA", first["uploader"])
            self.assertEqual(95, first["duration_s"])
            # `uploader` vale quando a fonte não manda `channel`.
            self.assertEqual("Canal do Espaço", rows["youtube:BV8Rv74TPGF"]["channel"])
            # Sem canal nenhum, a chave não é inventada.
            self.assertNotIn("channel", rows["youtube:CV8Rv74TPGH"])

    def test_the_atalhos_do_not_leak_into_the_manifest(self):
        expected = {"youtube:" + row["id"]: row["duration"] for row in YTDLP_FLAT["entries"]}
        with tempfile.TemporaryDirectory() as tmp:
            self.stub(tmp)
            items = manifest(tmp)["items"]
            self.assertEqual(sorted(expected), sorted(c["id"] for c in items))
            for item in items:
                self.assertNotIn("channel", item)
                self.assertNotIn("uploader", item)
                self.assertNotIn("duration_s", item)
                # A duração continua no lugar de sempre, com o valor literal do stub.
                self.assertEqual(expected[item["id"]], item["media"]["duration_s"])

    def test_summary_line_counts_and_names_the_first_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            line = self.stub(tmp)["summary"]["line"]
            self.assertIn("4 candidatos", line)
            self.assertIn("Decolagem do SLS", line)
            self.assertIn("Separação dos boosters", line)
            self.assertIn("Vista da órbita", line)
            # O quarto vira contagem, não título.
            self.assertNotIn("Retorno da cápsula", line)
            self.assertIn("+1", line)

    def test_summary_line_says_when_nothing_came_and_flags_the_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=[]):
                empty = audited(args(tmp), execute)
            self.assertIn("Nenhum candidato", empty["summary"]["line"])
            with patch.object(providers, "search", return_value=found(1)):
                dry = audited(args(tmp, dry_run=True), execute)
            self.assertIn("1 candidato", dry["summary"]["line"])
            self.assertIn("Diagnóstico", dry["summary"]["line"])


class LongQueryRetry(unittest.TestCase):
    """Frase inteira volta vazia; a busca encurta uma vez e conta que encurtou."""

    LONG = "print da página de preços do concorrente com o valor em destaque"

    def test_zero_items_with_a_long_query_retries_once_with_six_tokens(self):
        seen = []

        def fake(name, query, limit, media="any"):
            seen.append(query)
            return found(2) if len(query.split()) <= 6 else []

        with tempfile.TemporaryDirectory() as tmp, patch.object(providers, "search", side_effect=fake):
            result = audited(args(tmp, query=self.LONG), execute)
        self.assertEqual([self.LONG, "print página preços concorrente valor destaque"], seen)
        self.assertEqual(2, len(result["items"]))
        self.assertEqual(self.LONG, result["query"])
        self.assertEqual("print página preços concorrente valor destaque", result["query_used"])
        self.assertEqual(self.LONG, result["retry"]["from"])
        self.assertIn("repeti uma vez", result["summary"]["line"])

    def test_a_short_query_is_never_retried(self):
        seen = []

        def fake(name, query, limit, media="any"):
            seen.append(query)
            return []

        with tempfile.TemporaryDirectory() as tmp, patch.object(providers, "search", side_effect=fake):
            result = audited(args(tmp, query="foguete SLS decolando"), execute)
        self.assertEqual(["foguete SLS decolando"], seen)
        self.assertNotIn("retry", result)

    def test_the_zero_is_never_silent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(providers, "search", return_value=[]):
            result = audited(args(tmp, query="foguete SLS decolando"), execute)
        line = result["summary"]["line"]
        self.assertEqual([], result["items"])
        self.assertIn("Nenhum candidato", line)
        self.assertIn("foguete SLS decolando", line)
        self.assertIn("resolve --url", line)

    def test_a_retry_that_also_comes_back_empty_still_explains_itself(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(providers, "search", return_value=[]):
            result = audited(args(tmp, query=self.LONG), execute)
        self.assertEqual([], result["items"])
        self.assertIn("repeti uma vez", result["summary"]["line"])
        self.assertEqual("print página preços concorrente valor destaque", result["query_used"])


class BeatQueryIsEntityAndAction(unittest.TestCase):
    """`brief --beat` manda entidade + ação, não a frase de leitura humana."""

    def beat(self, target, queries=None):
        return {
            "id": "abertura",
            "target": target,
            "queries": queries or [],
            "intent": "literal",
            "allowed_sources": ["youtube"],
            "stock": False,
            "narration": None,
        }

    def test_a_long_target_is_trimmed_to_six_meaningful_tokens(self):
        from getbrolls.brief import beat_commands, search_query

        beat = self.beat("print da página de preços do concorrente com o valor em destaque")
        self.assertEqual("print página preços concorrente valor destaque", search_query(beat))
        self.assertLessEqual(len(search_query(beat).split()), 6)
        command = beat_commands("/tmp/projeto", beat)["search"]
        self.assertIn("print página preços concorrente valor destaque", command)
        self.assertNotIn("da página", command)

    def test_an_explicit_query_from_the_person_goes_through_untouched(self):
        from getbrolls.brief import search_query

        beat = self.beat("qualquer coisa", queries=["exatamente o que eu quero buscar aqui agora"])
        self.assertEqual("exatamente o que eu quero buscar aqui agora", search_query(beat))


if __name__ == "__main__":
    unittest.main()
