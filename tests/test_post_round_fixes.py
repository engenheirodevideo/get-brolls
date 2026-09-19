"""Fricções da rodada cega pós-2.4: metadado no resolve, motivo do reject, copy de erro.

Cada caso aqui nasceu de um relatório de executor, não de uma hipótese: o candidato
de TikTok que entrava sem canal nem duração, o `reject` que anunciava uma revisão
inexistente, o erro de tamanho que mandava ler as regras editoriais.
"""

import json
import tempfile
import types
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT, SKILLS

from getbrolls import social
from getbrolls.commands import execute, mark_rejected
from getbrolls.ledger import Ledger
from getbrolls.runtime import audited

TIKTOK = "https://www.tiktok.com/@engenheirodevideo/video/7312345678901234567"
YOUTUBE = "https://www.youtube.com/watch?v=abcdefghijk"

# O que o `--dump-single-json` do yt-dlp devolve para um post público de TikTok.
TIKTOK_JSON = {
    "id": "7312345678901234567",
    "title": "Como eu corto b-roll em 3 minutos",
    "uploader": "Engenheiro de Vídeo",
    "uploader_id": "@engenheirodevideo",
    "uploader_url": "https://www.tiktok.com/@engenheirodevideo",
    "duration": 47.0,
}


def resolve_args(project, url, **extra):
    base = {
        "command": "resolve",
        "project": str(project),
        "env_file": None,
        "confirm_format_change": False,
        "url": url,
        "file": None,
        "context_image": None,
        "full_preview_file": None,
        "asset_type": None,
        "title": None,
        "captured_at": None,
        "source_url": None,
        "creator": None,
        "shot": None,
        "intent": "literal",
    }
    base.update(extra)
    return types.SimpleNamespace(**base)


class ResolveFillsRemoteMetadata(unittest.TestCase):
    """C2 pede título, canal e duração; o candidato precisa trazê-los do resolve."""

    def stub(self, payload=TIKTOK_JSON):
        return patch.object(social, "run", return_value=(json.dumps(payload), []))

    def test_a_tiktok_url_comes_back_with_title_creator_and_duration(self):
        with tempfile.TemporaryDirectory() as tmp, self.stub():
            item = audited(resolve_args(tmp, TIKTOK), execute)
        self.assertEqual("Como eu corto b-roll em 3 minutos", item["title"])
        self.assertEqual("Engenheiro de Vídeo", item["creator"]["name"])
        self.assertEqual("@engenheirodevideo", item["creator"]["handle"])
        self.assertEqual(47.0, item["media"]["duration_s"])
        # O título genérico de antes não pode sobreviver ao preenchimento.
        self.assertNotIn("TikTok · ", item["title"])

    def test_a_youtube_url_is_filled_the_same_way(self):
        payload = {
            "id": "abcdefghijk",
            "title": "Keynote completa",
            "channel": "NVIDIA",
            "channel_id": "UC123",
            "duration": 5400,
        }
        with tempfile.TemporaryDirectory() as tmp, self.stub(payload):
            item = audited(resolve_args(tmp, YOUTUBE), execute)
        self.assertEqual("Keynote completa", item["title"])
        self.assertEqual("NVIDIA", item["creator"]["name"])
        self.assertEqual(5400.0, item["media"]["duration_s"])

    def test_a_page_that_refuses_metadata_still_registers_the_url(self):
        """Metadado é bônus; perder o candidato por causa dele seria pior que o vazio."""
        from getbrolls.http import ProviderError

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(social, "run", side_effect=ProviderError("vídeo privado")),
        ):
            item = audited(resolve_args(tmp, TIKTOK), execute)
        self.assertTrue(item["id"].startswith("tiktok:"))
        self.assertIsNone(item["media"]["duration_s"])

    def test_only_one_probe_is_made_and_it_never_downloads(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(social, "run", return_value=(json.dumps(TIKTOK_JSON), [])) as spy,
        ):
            audited(resolve_args(tmp, TIKTOK), execute)
        self.assertEqual(1, spy.call_count)
        argv = spy.call_args[0][0]
        self.assertIn("--skip-download", argv)
        self.assertIn("--dump-single-json", argv)

    def test_intent_is_stored_like_search_does(self):
        with tempfile.TemporaryDirectory() as tmp, self.stub():
            literal = audited(resolve_args(tmp, TIKTOK), execute)
        self.assertEqual("literal", literal["match"]["kind"])
        with tempfile.TemporaryDirectory() as tmp, self.stub():
            other = audited(resolve_args(tmp, TIKTOK, intent="illustrative"), execute)
        self.assertEqual("illustrative", other["match"]["kind"])


class RejectReason(unittest.TestCase):
    """O porquê do descarte fica gravado; a revisão inexistente não é anunciada."""

    def candidate(self, **extra):
        item = {"approval": {"status": "pending"}, "state": "awaiting_approval", "id": "local:a"}
        item.update(extra)
        return item

    def test_the_reason_is_stored_on_the_candidate(self):
        item = mark_rejected(self.candidate(), "enquadramento não mostra o painel")
        self.assertEqual("enquadramento não mostra o painel", item["rejection"]["reason"])
        self.assertEqual("rejected", item["approval"]["status"])
        self.assertTrue(item["rejection"]["at"])

    def test_without_a_reason_nothing_is_invented(self):
        item = mark_rejected(self.candidate())
        self.assertIsNone(item["rejection"]["reason"])
        for blank in ("", "   "):
            self.assertIsNone(mark_rejected(self.candidate(), blank)["rejection"]["reason"])

    def test_an_item_that_never_had_a_review_does_not_lose_one(self):
        from getbrolls.commands import FLOW_SUMMARIES

        item = mark_rejected(self.candidate())
        self.assertFalse(item["rejection"]["invalidated_review"])
        line = FLOW_SUMMARIES["reject"]({**item, "state": "rejected"})
        self.assertNotIn("revisão invalidada", line)

    def test_an_item_that_had_a_review_still_says_it_was_invalidated(self):
        from getbrolls.commands import FLOW_SUMMARIES

        item = mark_rejected(self.candidate(review={"decision": "approved"}), "mudou o enquadramento")
        self.assertTrue(item["rejection"]["invalidated_review"])
        self.assertNotIn("review", item)
        line = FLOW_SUMMARIES["reject"]({**item, "state": "rejected"})
        self.assertIn("revisão invalidada", line)
        self.assertIn("mudou o enquadramento", line)

    def test_the_cli_accepts_the_flag_and_status_shows_the_text(self):
        from getbrolls.cli import build_parser
        from getbrolls.commands import status_report
        from getbrolls.models import candidate, set_segment

        parsed = build_parser().parse_args(
            ["reject", "--project", "/tmp/p", "--candidate", "local:a", "--reason", "fora do tema"]
        )
        self.assertEqual("fora do tema", parsed.reason)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            item = candidate("local", "a", "Descartado")
            set_segment(item, 0, 1)
            mark_rejected(item, "fora do tema")
            ledger.save_many("reject", [ledger.add(item)])
            report = status_report(ledger)
        self.assertEqual(["fora do tema"], [i["rejection_reason"] for i in report["items"]])

    def test_many_candidates_share_the_same_reason(self):
        from getbrolls.commands import reject_all
        from getbrolls.models import candidate, set_segment

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            for name in ("a", "b"):
                item = candidate("local", name, name)
                set_segment(item, 0, 1)
                ledger.add(item)
            ledger.save_many("fixture", ledger.data["items"])
            result = reject_all(ledger, ["local:a", "local:b"], "não é o produto certo")
        self.assertEqual("não é o produto certo", result["reason"])
        self.assertEqual(
            ["não é o produto certo", "não é o produto certo"],
            [c["rejection"]["reason"] for c in ledger.data["items"]],
        )


class DownloadLimitCopy(unittest.TestCase):
    """O teto de download é transporte, não regra editorial: a mensagem tem que dizer isso."""

    def message(self):
        from getbrolls.http import ProviderError, download

        class FakeResponse:
            headers: ClassVar = {"Content-Length": str(700 * 1024 * 1024)}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _n):
                return b""

        class FakeOpener:
            def open(self, *_args, **_kw):
                return FakeResponse()

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("getbrolls.http._opener", return_value=FakeOpener()),
            self.assertRaises(ProviderError) as caught,
        ):
            download("https://exemplo.test/video.mp4", Path(tmp) / "out.mp4", max_bytes=512 * 1024 * 1024)
        return str(caught.exception)

    def test_the_size_and_the_cap_are_both_in_megabytes(self):
        text = self.message()
        self.assertIn("700.0 MB", text)
        self.assertIn("512 MB", text)

    def test_it_never_sends_the_person_to_the_editorial_rules(self):
        text = self.message()
        self.assertNotIn("RULES.md", text)
        self.assertNotIn("docs/RULES", text)

    def test_it_says_what_to_do_instead(self):
        self.assertIn("preview --start/--end", self.message())


class FormatConflictIsActionable(unittest.TestCase):
    """O conflito de formato é um default desalinhado, não um brief inválido."""

    def conflict(self):
        from getbrolls.brief import validate_brief
        from tests.test_brief import VALID

        data = json.loads(json.dumps(VALID))
        data["video"]["delivery"]["format"] = "reels"
        _, conflicts = validate_brief(data, {"video_format": "native", "copyright": {}})
        return conflicts[0]

    def test_the_message_carries_the_exact_command_that_resolves_it(self):
        text = self.conflict()
        self.assertIn("init-rules --format reels --force --project", text)
        self.assertIn("video.delivery.format", text)

    def test_the_message_says_it_is_not_an_invalid_brief(self):
        self.assertIn("Não é erro do brief", self.conflict())

    def test_validate_still_calls_the_brief_valid(self):
        from getbrolls.brief import load_brief, validate_brief
        from tests.test_brief import VALID, write_brief

        data = json.loads(json.dumps(VALID))
        data["video"]["delivery"]["format"] = "reels"
        with tempfile.TemporaryDirectory() as tmp:
            write_brief(tmp, data)
            loaded, conflicts = validate_brief(load_brief(tmp), {"video_format": "native", "copyright": {}})
        self.assertEqual(2, len(loaded["beats"]))
        self.assertEqual(1, len(conflicts))

    def test_the_interview_reference_tells_the_agent_to_align_first(self):
        body = (ROOT / "references/interview.md").read_text(encoding="utf-8")
        self.assertIn("init-rules --format reels --force", body)
        self.assertIn("antes** de você validar", body)

    def test_the_skill_and_mirror_say_it_too(self):
        for path in SKILLS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("alinhe o `video_format` do RULES.md antes de validar", text)


class StillBeatsAskForImages(unittest.TestCase):
    """Beat de foto não vai para o YouTube: ele pede imagem a um acervo de imagem."""

    def beat(self, target, sources=("youtube", "commons", "nasa")):
        return {
            "id": "abertura",
            "target": target,
            "queries": [],
            "intent": "literal",
            "allowed_sources": list(sources),
            "stock": False,
            "narration": None,
        }

    def test_a_photo_target_routes_to_commons_with_media_image(self):
        from getbrolls.brief import beat_commands

        command = beat_commands("/tmp/p", self.beat("foto do foguete SLS na plataforma"))["search"]
        self.assertIn("--provider commons", command)
        self.assertIn("--media image", command)
        self.assertNotIn("--provider youtube", command)

    def test_every_still_word_is_recognised(self):
        from getbrolls.brief import wants_a_still

        for word in ("foto", "imagem", "print", "still", "screenshot", "fotografia"):
            with self.subTest(word=word):
                self.assertTrue(wants_a_still(self.beat(f"um {word} da tela de preços")))
        self.assertFalse(wants_a_still(self.beat("foguete SLS decolando da plataforma")))

    def test_nasa_answers_when_commons_is_not_allowed(self):
        from getbrolls.brief import beat_commands

        command = beat_commands("/tmp/p", self.beat("foto do SLS", ("youtube", "nasa")))["search"]
        self.assertIn("--provider nasa", command)
        self.assertIn("--media image", command)

    def test_a_moving_target_keeps_the_video_route(self):
        from getbrolls.brief import beat_commands

        command = beat_commands("/tmp/p", self.beat("foguete SLS decolando"))["search"]
        self.assertIn("--provider youtube", command)
        self.assertNotIn("--media", command)

    def test_a_still_beat_without_any_image_source_falls_back_instead_of_failing(self):
        from getbrolls.brief import beat_commands

        command = beat_commands("/tmp/p", self.beat("foto do SLS", ("youtube",)))["search"]
        self.assertIn("--provider youtube", command)
        self.assertNotIn("--media", command)


class SearchCarriesDuration(unittest.TestCase):
    """C2 lista duração; ela tem que sobreviver à busca, inclusive com `--shot`."""

    def rows(self):
        from getbrolls.models import candidate

        item = candidate("youtube", "aaaaaaaaaaa", "Keynote", YOUTUBE)
        item["creator"]["name"] = "NVIDIA"
        item["media"]["duration_s"] = 5400.0
        return [item]

    def args(self, project, **extra):
        base = {
            "command": "search",
            "project": str(project),
            "env_file": None,
            "confirm_format_change": False,
            "provider": "youtube",
            "query": "keynote nvidia gtc",
            "limit": 5,
            "intent": "literal",
            "media": "any",
            "shot": None,
            "dry_run": False,
        }
        base.update(extra)
        return types.SimpleNamespace(**base)

    def test_duration_and_channel_reach_the_search_report(self):
        from getbrolls import providers

        with tempfile.TemporaryDirectory() as tmp, patch.object(providers, "search", return_value=self.rows()):
            result = audited(self.args(tmp), execute)
        row = result["items"][0]
        self.assertEqual(5400.0, row["duration_s"])
        self.assertEqual("NVIDIA", row["channel"])

    def test_the_shot_path_keeps_them_too(self):
        from getbrolls import providers

        with tempfile.TemporaryDirectory() as tmp, patch.object(providers, "search", return_value=self.rows()):
            result = audited(self.args(tmp, shot="abertura"), execute)
        row = result["items"][0]
        self.assertTrue(row["id"].endswith(":shot:abertura"))
        self.assertEqual(5400.0, row["duration_s"])
        self.assertEqual("NVIDIA", row["channel"])


class ReleaseNotesCoverTheWave(unittest.TestCase):
    """Cada item desta onda aparece no CHANGELOG e no QUALITY; a rodada fica registrada."""

    def changelog(self):
        return (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    def test_the_changelog_names_every_fix_of_the_wave(self):
        text = self.changelog()
        for marker in (
            "blocked_reason",
            "PEXELS_API_KEY",
            "query_used",
            "traduza a fala ao idioma da fonte",
            "preview.contact_sheet_path",
            "commons.wikimedia.org/wiki/File:",
            "--intent literal|illustrative",
            "rejection.reason",
            "search --media image|video|any",
            "tiktok.com/embed/@handle",
            "um `preview` por chamada",
            "init-rules --format reels --force",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_the_wave_sits_under_the_240_heading(self):
        text = self.changelog()
        start = text.index("## 2.4.0")
        self.assertIn("Onda pós-rodada", text[start : start + 4000])

    def test_quality_records_the_same_wave(self):
        text = (ROOT / "docs/QUALITY.md").read_text(encoding="utf-8")
        self.assertIn("16 casos", text)
        self.assertIn("Rodada cega completa e onda pós-rodada", text)
        # As três guardas continuam declaradas como intocadas.
        for guard in ("require_fetch", "signature", "validate_manifest"):
            self.assertIn(guard, text)

    def test_the_eval_readme_records_the_full_round_without_the_numbers(self):
        text = (ROOT / "eval/README.md").read_text(encoding="utf-8")
        self.assertIn("16 casos", text)
        self.assertIn("Estado do processo", text)
        # O juiz é quem escreve as métricas; esta nota é só de processo.
        self.assertIn("As notas por beat, as métricas e o veredito de cada caso são do juiz", text)

    def test_the_corpus_really_has_sixteen_cases(self):
        cases = sorted((ROOT / "eval/corpus").glob("*.md"))
        self.assertEqual(16, len(cases))


class BriefAndStatusNameTheSameNextStep(unittest.TestCase):
    """Dois comandos, um projeto, um próximo passo: `brief` não pode discordar do `status`."""

    def project(self, tmp, brief_data):
        from getbrolls.models import candidate, set_segment
        from tests.test_brief import write_brief

        ledger = Ledger(tmp)
        # Um item com prévia e sem decisão: é ele que trava o fluxo nos dois comandos.
        item = candidate("local", "a", "Esperando decisão")
        set_segment(item, 0, 1)
        item["preview"]["contact_sheet_path"] = "previews/a.jpg"
        ledger.save_many("fixture", [ledger.add(item)])
        write_brief(tmp, brief_data)
        return ledger

    def both(self, tmp):
        """`brief.summary.next` e `status.summary.do` do mesmo projeto."""
        from getbrolls.commands import _flow_state, brief_report
        from getbrolls.guidance import next_action

        ledger = Ledger(tmp, recover=False)
        report = brief_report(types.SimpleNamespace(project=tmp, validate=False, beat=None))
        do = next_action(_flow_state(ledger, None))
        return report["summary"]["next"], do

    def pending_with_a_missing_beat(self):
        from tests.test_brief import VALID

        return json.loads(json.dumps(VALID))

    def test_a_pending_preview_wins_in_brief_exactly_as_it_does_in_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.project(tmp, self.pending_with_a_missing_beat())
            brief_next, do = self.both(tmp)
        self.assertEqual("approve", do["step"])
        self.assertEqual(do["for_human"], brief_next)
        # A regressão em uma frase: o `brief` mandava buscar o beat vazio.
        self.assertNotIn("vou buscar por ele", brief_next)
        self.assertNotIn("abertura", brief_next)

    def test_without_the_pending_preview_both_go_back_to_the_missing_beat(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self.project(tmp, self.pending_with_a_missing_beat())
            item = ledger.data["items"][0]
            item["approval"]["status"] = "rejected"
            item["state"] = "rejected"
            ledger.save_many("decide", [item])
            brief_next, do = self.both(tmp)
        self.assertEqual("brief-search", do["step"])
        self.assertEqual(do["for_human"], brief_next)
        self.assertIn("abertura", brief_next)


class BlockedBriefNeverSaysGoAhead(unittest.TestCase):
    """Brief com todos os beats travados não é um brief pronto para buscar."""

    def all_blocked(self, count=3):
        from tests.test_brief import VALID

        data = json.loads(json.dumps(VALID))
        base = data["beats"][0]
        data["beats"] = []
        for index in range(count):
            beat = json.loads(json.dumps(base))
            beat["id"] = f"beat-{index}"
            beat["blocked_reason"] = f"Falta o fato {index} que só você tem."
            data["beats"].append(beat)
        return data

    def validate(self, tmp, data):
        from getbrolls.commands import brief_report
        from tests.test_brief import write_brief

        write_brief(tmp, data)
        return brief_report(types.SimpleNamespace(project=tmp, validate=True, beat=None))

    def test_validate_asks_the_question_instead_of_clearing_the_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.validate(tmp, self.all_blocked(6))
        text = payload["summary"]["next"]
        self.assertNotIn("Pode buscar", text)
        self.assertIn("parados esperando você", text)
        self.assertEqual(6, sum(1 for p in payload["summary"]["problems"] if "travado esperando você" in p))

    def test_the_same_question_comes_out_of_status(self):
        from getbrolls.commands import _flow_state
        from getbrolls.guidance import next_action

        with tempfile.TemporaryDirectory() as tmp:
            self.validate(tmp, self.all_blocked(3))
            Ledger(tmp)
            do = next_action(_flow_state(Ledger(tmp, recover=False), None))
        self.assertEqual("brief-blocked", do["step"])
        self.assertTrue(do["blocking_human"])
        with tempfile.TemporaryDirectory() as tmp:
            payload = self.validate(tmp, self.all_blocked(3))
        self.assertEqual(do["for_human"], payload["summary"]["next"])

    def test_a_brief_with_nothing_blocked_still_clears_the_search(self):
        from tests.test_brief import VALID

        with tempfile.TemporaryDirectory() as tmp:
            payload = self.validate(tmp, json.loads(json.dumps(VALID)))
        self.assertIn("Pode buscar", payload["summary"]["next"])


class EveryBlockedBeatIsAskedAtOnce(unittest.TestCase):
    """A pessoa responde uma vez: a mensagem lista todos os travados, não só o primeiro."""

    def beats(self, count):
        return [{"id": f"beat-{i}", "reason": f"Falta o fato {i}.", "target": "alvo"} for i in range(count)]

    def rung(self, count):
        from getbrolls.guidance import next_action

        return next_action(
            {
                "project": "/tmp/projeto",
                "counts": dict.fromkeys(("candidates", "previews", "pending", "approved"), 0),
                "brief": {
                    "beats": count,
                    "covered": 0,
                    "missing": [],
                    "blocked": self.beats(count),
                    "conflicts": [],
                },
            }
        )

    def test_three_blocked_beats_are_all_named_with_their_reasons(self):
        action = self.rung(3)
        for index in range(3):
            self.assertIn(f"beat-{index}", action["for_human"])
            self.assertIn(f"Falta o fato {index}.", action["for_human"])
            self.assertIn(f"beat-{index}", action["why"])
        self.assertIn("3 beats", action["why"])
        self.assertIn("3 beats do brief estão parados", action["for_human"])

    def test_the_message_stays_within_eight_lines(self):
        for count in (1, 3, 5, 6, 12):
            with self.subTest(count=count):
                lines = self.rung(count)["for_human"].splitlines()
                self.assertLessEqual(len(lines), 8, f"{count} beats geraram {len(lines)} linhas")

    def test_a_long_list_says_how_many_were_left_out(self):
        action = self.rung(12)
        self.assertIn("(+7 outro(s) beat(s) travado(s)", action["for_human"])
        self.assertIn("e mais 7.", action["why"])

    def test_a_single_blocked_beat_reads_as_a_sentence_not_a_list(self):
        action = self.rung(1)
        self.assertNotIn("\n", action["for_human"])
        self.assertIn('O beat "beat-0" está parado esperando você:', action["for_human"])
        self.assertIn("esse fato", action["for_human"])

    def test_it_never_promises_a_search(self):
        for count in (1, 3, 12):
            with self.subTest(count=count):
                self.assertNotIn("vou buscar", self.rung(count)["for_human"])


class TranslatedTrackNeverDecidesTheSourceLanguage(unittest.TestCase):
    """A faixa `pt` traduzida não é a fala da fonte; o aviso saía invertido por isso."""

    ORIGINAL_EN: ClassVar = {"subtitle_langs": ["pt", "en-orig"], "original_lang": "en-orig"}

    def test_the_source_language_comes_from_the_original_track(self):
        from getbrolls.inspecting import source_language

        self.assertEqual("en", source_language(self.ORIGINAL_EN))
        # Sem o campo explícito, o sufixo `-orig` resolve sozinho.
        self.assertEqual("en", source_language({"subtitle_langs": ["pt", "en-orig"]}))

    def test_an_english_query_on_an_english_source_is_not_warned(self):
        from getbrolls.commands import inspect_warnings

        warnings = inspect_warnings(self.ORIGINAL_EN, "the part where he talks about the launch")
        self.assertEqual([], [w for w in warnings if "legenda em" in w])

    def test_a_portuguese_query_on_an_english_source_is_warned(self):
        from getbrolls.commands import inspect_warnings

        warnings = inspect_warnings(self.ORIGINAL_EN, "o trecho em que ele fala do lançamento")
        self.assertIn(
            "legenda em EN, sua --query está em PT: traduza a fala ao idioma da fonte",
            warnings,
        )

    def test_the_old_inverted_warning_is_gone(self):
        """A regressão literal do relatório: "legenda em PT" para uma query em EN."""
        from getbrolls.commands import inspect_warnings

        for query in (
            "the part where he talks about the launch",
            "the moment that the rocket lifts off from the pad",
        ):
            with self.subTest(query=query):
                joined = " ".join(inspect_warnings(self.ORIGINAL_EN, query))
                self.assertNotIn("legenda em PT", joined)

    def test_two_tracks_with_nothing_marking_the_original_stay_silent(self):
        from getbrolls.commands import inspect_warnings
        from getbrolls.inspecting import source_language

        ambiguous = {"subtitle_langs": ["pt", "en"]}
        self.assertIsNone(source_language(ambiguous))
        self.assertEqual([], inspect_warnings(ambiguous, "o trecho em que ele fala do lançamento"))

    def test_the_probe_reports_the_original_track(self):
        """`probe_remote` precisa entregar o campo, senão o detector não tem o que ler."""
        from getbrolls import social

        payload = {
            "id": "abcdefghijk",
            "title": "Launch",
            "duration": 120,
            "automatic_captions": {"pt": [{"ext": "vtt"}], "en-orig": [{"ext": "vtt"}]},
            "subtitles": {},
        }
        self.assertEqual("en-orig", social._original_language(payload))


class ResolveExposesTheSameFlatFieldsAsSearch(unittest.TestCase):
    """C2 lê os dois comandos do mesmo jeito: título, canal, duração."""

    def resolved(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(social, "run", return_value=(json.dumps(TIKTOK_JSON), [])),
        ):
            return audited(resolve_args(tmp, TIKTOK), execute)

    def test_the_flat_shortcuts_are_filled(self):
        item = self.resolved()
        self.assertEqual("Como eu corto b-roll em 3 minutos", item["title"])
        self.assertEqual("Engenheiro de Vídeo", item["channel"])
        self.assertEqual("Engenheiro de Vídeo", item["uploader"])
        self.assertEqual(47.0, item["duration_s"])

    def test_they_match_the_nested_fields_they_shortcut(self):
        item = self.resolved()
        self.assertEqual(item["creator"]["name"], item["channel"])
        self.assertEqual(item["media"]["duration_s"], item["duration_s"])

    def test_the_shortcuts_stay_out_of_the_manifest(self):
        """Atalho é da resposta; o manifesto guarda `creator.name` e `media.duration_s`."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(social, "run", return_value=(json.dumps(TIKTOK_JSON), [])):
                audited(resolve_args(tmp, TIKTOK), execute)
            saved = json.loads((Path(tmp) / "brolls/manifest.json").read_text(encoding="utf-8"))["items"][0]
        for shortcut in ("channel", "uploader", "duration_s"):
            self.assertNotIn(shortcut, saved)
        self.assertEqual(47.0, saved["media"]["duration_s"])

    def test_a_source_without_metadata_leaves_the_shortcuts_absent_not_wrong(self):
        from getbrolls.http import ProviderError

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(social, "run", side_effect=ProviderError("vídeo privado")),
        ):
            item = audited(resolve_args(tmp, TIKTOK), execute)
        self.assertNotIn("duration_s", item)
        self.assertNotIn("channel", item)


if __name__ == "__main__":
    unittest.main()
