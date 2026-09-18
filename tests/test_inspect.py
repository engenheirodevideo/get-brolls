"""Analisar antes de coletar: o que a fonte tem, antes de pedir mídia."""

import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)

from getbrolls import inspecting, social
from getbrolls.cli import build_parser

URL = "https://www.youtube.com/watch?v=abcdefghijk"

VTT = """WEBVTT

00:00:00.000 --> 00:00:04.000
A tempestade de areia cobriu a cidade

00:00:04.000 --> 00:00:08.500
<c>e o céu ficou laranja no meio da tarde</c>

00:00:20.000 --> 00:00:24.000
Depois disso, os moradores voltaram às ruas
"""

# Legenda automática do YouTube, copiada de um arquivo real baixado com yt-dlp
# (`https://www.youtube.com/watch?v=AV8Rv74TPGE`, faixa `pt`). Três coisas que o VTT
# de estúdio não tem e quebram um leitor ingênuo: tags `<00:00:00.560><c>…</c>` de
# tempo por palavra, linhas só com um espaço dentro do bloco, e o par de cues em
# rolagem — um cue de 10 ms repetindo a linha anterior e o seguinte reabrindo com ela.
# `<SP>` marca a linha de um espaço só, que sumiria em qualquer editor.
YOUTUBE_AUTO_VTT = """\
WEBVTT
Kind: captions
Language: pt

00:00:00.320 --> 00:00:02.389 align:start position:0%
<SP>
Me<00:00:00.560><c> permita</c><00:00:01.040><c> te</c><00:00:01.199><c> fazer</c><00:00:01.439><c> uma</c><00:00:01.680><c> pergunta.</c><00:00:02.200><c> Quanto</c>

00:00:02.389 --> 00:00:02.399 align:start position:0%
Me permita te fazer uma pergunta. Quanto
<SP>

00:00:02.399 --> 00:00:04.190 align:start position:0%
Me permita te fazer uma pergunta. Quanto
é<00:00:02.520><c> que</c><00:00:02.679><c> você</c><00:00:03.040><c> está</c><00:00:03.280><c> pagando</c><00:00:03.639><c> de</c><00:00:03.840><c> energia</c>

00:00:04.190 --> 00:00:04.200 align:start position:0%
é que você está pagando de energia
<SP>

00:00:04.200 --> 00:00:06.869 align:start position:0%
é que você está pagando de energia
elétrica<00:00:04.680><c> aí</c><00:00:04.880><c> na</c><00:00:05.000><c> sua</c><00:00:05.279><c> casa?</c><00:00:05.879><c> Eh,</c><00:00:06.359><c> essas</c>

00:00:06.869 --> 00:00:06.879 align:start position:0%
elétrica aí na sua casa? Eh, essas
<SP>

00:00:06.879 --> 00:00:09.030 align:start position:0%
elétrica aí na sua casa? Eh, essas
contas<00:00:07.359><c> todas</c><00:00:08.000><c> estão</c><00:00:08.320><c> pesando</c><00:00:08.679><c> no</c><00:00:08.880><c> seu</c>

00:00:09.030 --> 00:00:09.040 align:start position:0%
contas todas estão pesando no seu
""".replace("<SP>", " ")

WITH_EVERYTHING = {
    "id": "abcdefghijk",
    "title": "Tempestade de areia",
    "duration": 120,
    "description": "Trechos:\n00:10 chegada da poeira\n01:05 céu laranja",
    "chapters": [
        {"start_time": 0, "end_time": 30, "title": "Abertura"},
        {"start_time": 30, "end_time": 120, "title": "Céu laranja sobre a cidade"},
    ],
    "automatic_captions": {"pt": [{"ext": "vtt"}], "en": [{"ext": "vtt"}]},
    "subtitles": {},
}

BARE = {
    "id": "abcdefghijk",
    "title": "Sem capítulos",
    "duration": 90,
    "description": "Marcos do vídeo\n00:12 poeira chegando\n1:05 céu laranja",
    "chapters": None,
    "automatic_captions": {},
    "subtitles": {},
}

# O contrato real do yt-dlp, e a razão do bug que este arquivo passou a cobrir:
# `--dump-single-json` implica `--simulate`, e em modo simulado nada chega ao disco —
# nem `.vtt`, nem `.info.json`. Com `--no-simulate` é o contrário: os arquivos aparecem
# e o stdout deixa de trazer o JSON. O dublê tem que obedecer aos dois lados, senão
# a suíte fica verde enquanto o `inspect` real volta sem uma única legenda.
STUB = """#!{python}
import json, os, sys
data = json.loads(os.environ["GB_TEST_YTDLP_JSON"])
subtitle = os.environ.get("GB_TEST_YTDLP_VTT")
argv = sys.argv[1:]
writing = "--no-simulate" in argv
template = argv[argv.index("-o") + 1] if "-o" in argv else None
if writing and template:
    if "--write-info-json" in argv and not os.environ.get("GB_TEST_YTDLP_NO_INFO"):
        with open(template.replace("%(ext)s", "info.json"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data))
    if subtitle and "--write-auto-subs" in argv:
        for lang in ("pt",):
            path = template.replace("%(ext)s", lang + ".vtt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(subtitle)
if not writing:
    print(json.dumps(data))
"""


def stub_ytdlp(directory, payload, vtt=None, no_info=False):
    """yt-dlp falso no disco: nenhum teste toca a rede."""
    path = Path(directory) / "yt-dlp-stub.py"
    path.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    path.chmod(0o755)
    executable = path
    if os.name == "nt":
        # CreateProcess ignora shebang: no Windows o executável fixado é um .cmd
        # que chama o Python, como o playwright-cli.cmd do instalador.
        executable = Path(directory) / "yt-dlp-stub.cmd"
        executable.write_text(f'@echo off\r\n"{sys.executable}" "{path}" %*\r\n', encoding="utf-8")
    env = {"GB_YTDLP_PATH": str(executable), "GB_TEST_YTDLP_JSON": json.dumps(payload)}
    if no_info:
        # Fonte que engasgou no meio: legenda no disco, mas nenhum `.info.json`.
        env["GB_TEST_YTDLP_NO_INFO"] = "1"
    if vtt is not None:
        env["GB_TEST_YTDLP_VTT"] = vtt
    return env


class VttTests(unittest.TestCase):
    def test_minimal_vtt_becomes_cues_with_seconds_and_clean_text(self):
        cues = inspecting.parse_vtt(VTT)
        self.assertEqual(3, len(cues))
        self.assertEqual((0.0, 4.0), (cues[0]["start_s"], cues[0]["end_s"]))
        self.assertEqual("A tempestade de areia cobriu a cidade", cues[0]["text"])
        # Tags de estilo do VTT não entram no texto pontuado.
        self.assertNotIn("<", cues[1]["text"])
        self.assertEqual(24.0, cues[2]["end_s"])

    def test_youtube_auto_captions_survive_word_tags_blank_lines_and_rolling(self):
        """O formato que o `inspect` encontra de verdade, não o VTT limpo de estúdio."""
        cues = inspecting.parse_vtt(YOUTUBE_AUTO_VTT)
        spoken = [cue["text"] for cue in cues]
        # Nada de tag de tempo/estilo sobrando no texto pontuado.
        self.assertFalse([t for t in spoken if "<" in t or ">" in t])
        # Nenhum cue vazio: a linha de um espaço só não vira fala nem corta o bloco.
        self.assertFalse([t for t in spoken if not t.strip()])
        # A primeira fala não se perde atrás da linha em branco que abre o bloco.
        self.assertEqual("Me permita te fazer uma pergunta. Quanto", spoken[0])
        self.assertAlmostEqual(0.32, cues[0]["start_s"])
        # Rolagem não conta duas vezes: cada frase aparece uma vez só.
        self.assertEqual(len(spoken), len(set(spoken)))
        joined = " ".join(spoken)
        self.assertEqual(1, joined.count("Me permita te fazer uma pergunta."))
        self.assertIn("é que você está pagando de energia", joined)
        self.assertIn("elétrica aí na sua casa?", joined)

    def test_rolling_captions_do_not_inflate_the_score(self):
        """Sem deduplicar, a janela repetiria as mesmas palavras e a nota subiria sozinha."""
        cues = inspecting.parse_vtt(YOUTUBE_AUTO_VTT)
        text = " ".join(cue["text"] for cue in cues)
        self.assertEqual(1, inspecting.tokens(text).count("permita"))
        self.assertEqual(1, inspecting.tokens(text).count("energia"))

    def test_short_timestamps_without_hours_are_accepted(self):
        cues = inspecting.parse_vtt("WEBVTT\n\n01:02.500 --> 01:06.000\noi\n")
        self.assertEqual([62.5, 66.0], [cues[0]["start_s"], cues[0]["end_s"]])

    def test_garbage_is_ignored_instead_of_raising(self):
        self.assertEqual([], inspecting.parse_vtt("não é um vtt"))


class ScoreTests(unittest.TestCase):
    def test_score_is_token_overlap_and_ignores_accents_and_case(self):
        self.assertEqual(1.0, inspecting.score("Céu laranja", "o ceu LARANJA da tarde"))
        self.assertEqual(0.0, inspecting.score("céu laranja", "praia deserta"))
        self.assertAlmostEqual(0.5, inspecting.score("céu laranja", "o ceu azul"))

    def test_without_a_query_every_window_scores_zero(self):
        self.assertEqual(0.0, inspecting.score("", "qualquer texto"))


class WindowTests(unittest.TestCase):
    def probe(self, **extra):
        data = {
            "duration_s": 120.0,
            "chapters": [{"start_s": 30.0, "end_s": 120.0, "title": "Céu laranja sobre a cidade"}],
            "subtitle_langs": ["pt"],
            "description": "00:10 chegada da poeira",
            "subtitles": {"pt": {"path": None, "cues": inspecting.parse_vtt(VTT)}},
        }
        data.update(extra)
        return data

    def test_the_query_ranks_the_window_that_actually_says_it(self):
        windows = inspecting.candidate_windows(self.probe(), "céu laranja", 3)
        self.assertTrue(windows)
        self.assertGreater(windows[0]["score"], 0)
        self.assertIn(windows[0]["source"], ("subtitle", "chapter"))
        self.assertIn("laranja", windows[0]["text"].lower())
        self.assertGreaterEqual(len(windows), 2)

    def test_max_windows_is_respected_and_windows_stay_inside_the_duration(self):
        windows = inspecting.candidate_windows(self.probe(), "céu laranja", 2)
        self.assertEqual(2, len(windows))
        for window in windows:
            self.assertLessEqual(window["end_s"], 120.0)
            self.assertLess(window["start_s"], window["end_s"])
            self.assertIn(window["source"], ("subtitle", "chapter", "description_timestamp"))

    def test_without_subtitles_or_chapters_the_description_timestamps_answer(self):
        probe = self.probe(chapters=[], subtitles={}, subtitle_langs=[])
        windows = inspecting.candidate_windows(probe, "poeira", 3)
        self.assertEqual("description_timestamp", windows[0]["source"])
        self.assertEqual(10.0, windows[0]["start_s"])

    def test_two_languages_with_the_same_timings_do_not_take_two_slots(self):
        cues = inspecting.parse_vtt(VTT)
        english = [dict(cue, text="orange sky over the city") for cue in cues]
        probe = self.probe(
            subtitle_langs=["en", "pt"],
            subtitles={
                "pt": {"path": None, "cues": cues},
                "en": {"path": None, "cues": english},
            },
        )
        windows = inspecting.candidate_windows(probe, "céu laranja", 5)
        subtitles = [w for w in windows if w["source"] == "subtitle"]
        starts = [w["start_s"] for w in subtitles]
        self.assertEqual(len(starts), len(set(starts)))
        self.assertEqual(2, len(starts))
        # O primeiro idioma do dicionário manda: o texto é o dele, não o do segundo.
        self.assertTrue(all("orange sky" not in w["text"] for w in subtitles), subtitles)
        self.assertIn("laranja", subtitles[0]["text"])

    def test_probe_remote_orders_the_subtitles_by_the_requested_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            with patch.dict(os.environ, env):
                probe = social.probe_remote(URL, langs=("pt", "en"), cache=Path(tmp) / ".getbrolls-sources")
        self.assertEqual(["pt"], list(probe["subtitles"]))

    def test_with_nothing_named_the_clock_still_gives_somewhere_to_look(self):
        """Devolver `[]` empurrava o agente para o palpite; o mapa grosseiro é melhor."""
        probe = self.probe(chapters=[], subtitles={}, subtitle_langs=[], description="")
        windows = inspecting.candidate_windows(probe, "poeira", 3)
        self.assertEqual(3, len(windows))
        for window in windows:
            self.assertEqual("even_spacing", window["source"])
            self.assertEqual(0.0, window["score"])
            self.assertLess(window["start_s"], window["end_s"])
            self.assertLessEqual(window["end_s"], 120.0)

    def test_an_untitled_chapter_is_still_a_window_when_nothing_else_matched(self):
        probe = self.probe(
            chapters=[{"start_s": 10.0, "end_s": 40.0, "title": ""}],
            subtitles={},
            subtitle_langs=[],
            description="",
        )
        windows = inspecting.candidate_windows(probe, "poeira", 3)
        self.assertEqual(["chapter"], [w["source"] for w in windows])
        self.assertEqual("Capítulo sem título", windows[0]["text"])

    def test_cues_answer_before_the_clock_when_no_token_overlaps(self):
        probe = self.probe(chapters=[], subtitle_langs=[], description="")
        windows = inspecting.fallback_windows(probe, 3)
        self.assertTrue(windows)
        self.assertEqual({"subtitle"}, {w["source"] for w in windows})
        self.assertTrue(all(w["text"] for w in windows))

    def test_nothing_at_all_and_no_duration_is_still_an_empty_list(self):
        probe = self.probe(chapters=[], subtitles={}, subtitle_langs=[], description="", duration_s=None)
        self.assertEqual([], inspecting.candidate_windows(probe, "poeira", 3))


class ProbeRemoteTests(unittest.TestCase):
    def test_probe_reads_duration_chapters_and_subtitle_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / ".getbrolls-sources"
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            with patch.dict(os.environ, env):
                probe = social.probe_remote(URL, cache=cache)
        self.assertEqual(120.0, probe["duration_s"])
        self.assertEqual(["Abertura", "Céu laranja sobre a cidade"], [ch["title"] for ch in probe["chapters"]])
        # Só os idiomas pedidos (na ordem pedida); não as centenas traduzidas.
        self.assertEqual(["pt", "en"], probe["subtitle_langs"])
        self.assertEqual(2, probe["subtitle_langs_total"])
        self.assertIn("poeira", probe["description"])

    def test_the_vtt_lands_in_the_private_sources_folder_with_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / ".getbrolls-sources"
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            with patch.dict(os.environ, env):
                probe = social.probe_remote(URL, cache=cache)
            saved = Path(probe["subtitles"]["pt"]["path"])
            self.assertEqual(cache, saved.parent)
            if os.name != "nt":  # Windows não tem bits POSIX de permissão
                self.assertEqual(0o600, stat.S_IMODE(saved.stat().st_mode))
            self.assertIn("tempestade", saved.read_text(encoding="utf-8").lower())
            self.assertTrue(probe["subtitles"]["pt"]["cues"])

    def test_the_vtt_refuses_to_write_through_a_planted_file(self):
        """`O_CREAT|O_EXCL` com 0600: nunca escrever através de algo plantado com o nome."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / ".getbrolls-sources"
            cache.mkdir()
            victim = Path(tmp) / "alvo.txt"
            victim.write_text("não me sobrescreva", encoding="utf-8")
            stem = hashlib.sha256(URL.encode()).hexdigest()[:16]
            planted = cache / f"{stem}-pt.vtt"
            planted.symlink_to(victim)
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            with patch.dict(os.environ, env):
                probe = social.probe_remote(URL, cache=cache)
            saved = Path(probe["subtitles"]["pt"]["path"])
            self.assertFalse(saved.is_symlink())
            if os.name != "nt":  # Windows não tem bits POSIX de permissão
                self.assertEqual(0o600, stat.S_IMODE(saved.lstat().st_mode))
            self.assertEqual("não me sobrescreva", victim.read_text(encoding="utf-8"))

    def test_hundreds_of_auto_translations_do_not_flood_the_answer(self):
        many = {f"x{n}": [{"ext": "vtt"}] for n in range(300)}
        many["pt-orig"] = [{"ext": "vtt", "name": "Portuguese (Original)"}]
        many["pt"] = [{"ext": "vtt"}]
        listed, total = social.relevant_langs(
            {"automatic_captions": many, "subtitles": {}},
            ("pt", "en"),
        )
        self.assertEqual(302, total)
        self.assertLessEqual(len(listed), social.MAX_SUBTITLE_LANGS)
        self.assertEqual(["pt", "pt-orig"], listed)

    def test_the_original_track_is_listed_even_when_nobody_asked_for_it(self):
        data = {
            "automatic_captions": {"ja": [{"ext": "vtt", "name": "Japanese (Original)"}]},
            "subtitles": {},
        }
        listed, total = social.relevant_langs(data, ("pt", "en"))
        self.assertEqual((["ja"], 1), (listed, total))

    def test_a_source_without_chapters_or_subtitles_still_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, BARE)
            with patch.dict(os.environ, env):
                probe = social.probe_remote(URL, cache=Path(tmp) / ".getbrolls-sources")
        self.assertEqual(90.0, probe["duration_s"])
        self.assertEqual([], probe["chapters"])
        self.assertEqual([], probe["subtitle_langs"])
        self.assertEqual({}, probe["subtitles"])

    def test_without_an_info_json_the_metadata_comes_from_a_second_simulated_call(self):
        """Sem `.info.json`, o probe ainda responde — e o erro de verdade não some."""
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT, no_info=True)
            with patch.dict(os.environ, env):
                probe = social.probe_remote(URL, cache=Path(tmp) / ".getbrolls-sources")
        self.assertEqual(120.0, probe["duration_s"])
        self.assertEqual("Tempestade de areia", probe["title"])
        # A legenda que chegou antes do tropeço continua valendo.
        self.assertTrue(probe["subtitles"]["pt"]["cues"])

    def test_the_probe_keeps_the_ytdlp_pacing_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, BARE)
            with patch.dict(os.environ, {**env, "GB_YTDLP_SLEEP": "2,4,9"}):
                captured = {}
                original = social.run

                def spy(arguments, timeout=180):
                    captured["args"] = arguments
                    captured["command"] = social.command()
                    return original(arguments, timeout=timeout)

                with patch.object(social, "run", spy):
                    social.probe_remote(URL, cache=Path(tmp) / ".getbrolls-sources")
        self.assertIn("--skip-download", captured["args"])
        # `--no-simulate` é o que faz o yt-dlp escrever de fato; `--dump-single-json`
        # implicaria `--simulate` e não sobraria legenda nenhuma no disco.
        self.assertIn("--no-simulate", captured["args"])
        self.assertIn("--ignore-errors", captured["args"])
        self.assertNotIn("--dump-single-json", captured["args"])
        self.assertIn("--write-info-json", captured["args"])
        self.assertIn("--write-auto-subs", captured["args"])
        self.assertIn("pt,en", captured["args"])
        self.assertIn("--sleep-requests", captured["command"])
        self.assertEqual("2", captured["command"][captured["command"].index("--sleep-requests") + 1])


class SummaryTests(unittest.TestCase):
    """O veredito em PT-BR não pode soar igual quando houve legenda e quando não houve."""

    def setUp(self):
        from getbrolls import commands

        self.summary = commands.inspect_summary

    def test_without_any_cue_the_summary_says_no_subtitles_were_obtained(self):
        probe = {"duration_s": 90.0, "subtitles": {}}
        windows = [{"start_s": 22.5, "end_s": 34.5, "text": "", "source": "even_spacing", "score": 0.0}]
        line = self.summary(windows, probe)["line"]
        self.assertIn("Sem legendas obtidas", line)
        self.assertNotIn("ponto de partida", line)

    def test_with_cues_but_no_match_the_summary_says_nothing_matched(self):
        probe = {
            "duration_s": 90.0,
            "subtitles": {"pt": {"cues": [{"start_s": 0.0, "end_s": 4.0, "text": "chuva na serra"}]}},
        }
        windows = [{"start_s": 0.0, "end_s": 4.0, "text": "chuva na serra", "source": "subtitle", "score": 0.0}]
        line = self.summary(windows, probe)["line"]
        self.assertIn("Nenhuma casou com a frase", line)
        self.assertNotIn("Sem legendas obtidas", line)

    def test_a_match_is_announced_as_a_match(self):
        probe = {
            "duration_s": 90.0,
            "subtitles": {"pt": {"cues": [{"start_s": 0.0, "end_s": 4.0, "text": "conta de luz"}]}},
        }
        windows = [{"start_s": 0.0, "end_s": 4.0, "text": "conta de luz", "source": "subtitle", "score": 1.0}]
        line = self.summary(windows, probe)["line"]
        self.assertIn("A mais parecida", line)


def run_cli(args, env=None):
    environment = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/gb.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def project_with_candidate(tmp):
    run_cli(["init-rules", "--project", tmp])
    done = run_cli(["resolve", "--url", URL, "--project", tmp])
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)["id"]


class InspectCommandTests(unittest.TestCase):
    def test_inspect_is_registered_with_help_and_parses(self):
        from getbrolls.cli import SUMMARIES

        self.assertIn("inspect", SUMMARIES)
        parsed = build_parser().parse_args(
            shlex.split("inspect --project /tmp/p --url " + URL + ' --query "céu laranja"')
        )
        self.assertEqual("inspect", parsed.command)
        self.assertEqual(3, parsed.max_windows)

    def test_inspect_by_url_returns_the_contract_without_touching_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            run_cli(["init-rules", "--project", tmp])
            done = run_cli(
                ["inspect", "--project", tmp, "--url", URL, "--query", "céu laranja"],
                env=env,
            )
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            payload = json.loads(done.stdout)
            self.assertEqual(120.0, payload["duration_s"])
            self.assertEqual(["pt", "en"], payload["subtitle_langs"])
            self.assertEqual(2, payload["subtitle_langs_total"])
            self.assertIn("janela", payload["summary"]["line"])
            self.assertTrue(payload["summary"]["next"])
            self.assertTrue(payload["candidate_windows"])
            for window in payload["candidate_windows"]:
                self.assertEqual({"start_s", "end_s", "text", "source", "score"}, set(window))
            self.assertFalse((Path(tmp) / "brolls/manifest.json").exists())

    def test_inspect_on_a_candidate_only_writes_the_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            candidate = project_with_candidate(tmp)
            manifest = Path(tmp) / "brolls/manifest.json"
            before = json.loads(manifest.read_text(encoding="utf-8"))
            item_before = next(c for c in before["items"] if c["id"] == candidate)
            done = run_cli(
                ["inspect", "--project", tmp, "--candidate", candidate, "--query", "céu laranja"],
                env=env,
            )
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            after = json.loads(manifest.read_text(encoding="utf-8"))
            item = next(c for c in after["items"] if c["id"] == candidate)
            self.assertEqual(120.0, item["media"]["duration_s"])
            # Nada de decisão ou intervalo: inspecionar não aprova nem seleciona.
            self.assertEqual(item_before["approval"], item["approval"])
            self.assertEqual(item_before["segment"], item["segment"])
            self.assertEqual(item_before["rights"], item["rights"])
            self.assertEqual(item_before["state"], item["state"])
            for key, value in item_before["media"].items():
                if key != "duration_s":
                    self.assertEqual(value, item["media"][key], key)
            self.assertFalse(list((Path(tmp) / "brolls/clips").glob("*")))
            self.assertFalse(list((Path(tmp) / "brolls/previews").glob("*")))

    def test_after_inspect_an_interval_longer_than_the_video_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            candidate = project_with_candidate(tmp)
            run_cli(["inspect", "--project", tmp, "--candidate", candidate], env=env)
            done = run_cli(
                ["preview", "--project", tmp, "--candidate", candidate, "--start", "100", "--end", "300"],
                env=env,
            )
            self.assertNotEqual(0, done.returncode)
            self.assertIn("duração", done.stdout + done.stderr)

    def test_url_and_candidate_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["inspect", "--project", "/tmp/p", "--url", URL, "--candidate", "x"])
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["inspect", "--project", "/tmp/p"])


class SuggestedWindowFitsThePreviewTests(unittest.TestCase):
    """Fricção 3 da rodada 2: o `inspect` sugeria 11 s e o `preview` recusava aos 10 s."""

    def test_a_long_window_is_clamped_to_the_preview_ceiling(self):
        from getbrolls.commands import clamp_windows

        windows = [{"start_s": 122.0, "end_s": 133.0, "text": "x", "source": "chapter", "score": 0.0}]
        clamped = clamp_windows(windows, 10.0)
        self.assertEqual(132.0, clamped[0]["end_s"])
        self.assertEqual(122.0, clamped[0]["start_s"])
        # O contrato de chaves da janela não muda com o corte.
        self.assertEqual({"start_s", "end_s", "text", "source", "score"}, set(clamped[0]))

    def test_a_window_exactly_at_the_ceiling_is_left_alone(self):
        """`16.1 - 6.1` dá 10.000000000000002: o corte não pode morder o que já cabe."""
        from getbrolls.commands import clamp_windows

        windows = [{"start_s": 6.1, "end_s": 16.1, "text": "x", "source": "chapter", "score": 0.0}]
        self.assertEqual(16.1, clamp_windows(windows, 10.0)[0]["end_s"])

    def test_preview_accepts_an_interval_equal_to_the_ceiling(self):
        """O teto é inclusivo: o intervalo que o `inspect` sugere tem que passar."""
        from getbrolls.config import settings
        from getbrolls.media import review_preview

        config = settings()
        cap = float(config["max_seconds"])
        with tempfile.TemporaryDirectory() as tmp:
            over = self.assertRaises(ValueError)
            with over:
                review_preview("/nao/existe.mp4", tmp, "x", 6.1, 6.1 + cap + 1, config)
            self.assertIn("GB_PREVIEW_MAX_SECONDS", str(over.exception))
            for start in (0.0, 6.1, 8.6):
                # Passa do guarda de teto e só falha adiante, por falta de mídia.
                with self.assertRaises(Exception) as caught:
                    review_preview("/nao/existe.mp4", tmp, "x", start, start + cap, config)
                self.assertNotIn("GB_PREVIEW_MAX_SECONDS", str(caught.exception))

    def test_the_command_suggests_an_interval_the_preview_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            run_cli(["init-rules", "--project", tmp])
            done = run_cli(
                ["inspect", "--project", tmp, "--url", URL, "--query", "céu laranja"],
                env={**env, "GB_PREVIEW_MAX_SECONDS": "10"},
            )
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            payload = json.loads(done.stdout)
            for window in payload["candidate_windows"]:
                self.assertLessEqual(window["end_s"] - window["start_s"], 10.0 + 1e-6)
            self.assertIn("GB_PREVIEW_MAX_SECONDS", payload["summary"]["line"])
            self.assertIn("10 s", payload["summary"]["line"])


class UnusualSourceWarningsTests(unittest.TestCase):
    """Fricção 6 da rodada 2: nem duração de risco nem 360° apareciam antes da prévia."""

    def test_a_long_source_and_a_360_video_are_announced(self):
        from getbrolls.commands import inspect_warnings

        self.assertEqual([], inspect_warnings({"duration_s": 120.0, "title": "Curto"}))
        self.assertEqual(["fonte longa: 207 min"], inspect_warnings({"duration_s": 12420.0, "title": "Transmissão"}))
        self.assertEqual(["vídeo 360°"], inspect_warnings({"duration_s": 60.0, "title": "NASA KSC 360 tour"}))
        self.assertEqual(["vídeo 360°"], inspect_warnings({"duration_s": 60.0, "title": "Tour", "tags": ["vr", "360"]}))
        # `360p` e `1360` são resolução e número, não formato esférico.
        self.assertEqual([], inspect_warnings({"duration_s": 60.0, "title": "Arquivo em 360p", "tags": ["1360"]}))

    def test_the_command_reports_the_warnings_in_the_payload_and_in_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                **WITH_EVERYTHING,
                "title": "Cobertura 360 do lançamento",
                "duration": 12600,
                "tags": ["360"],
            }
            env = stub_ytdlp(tmp, payload, VTT)
            run_cli(["init-rules", "--project", tmp])
            done = run_cli(["inspect", "--project", tmp, "--url", URL], env=env)
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            body = json.loads(done.stdout)
            self.assertEqual(["fonte longa: 210 min", "vídeo 360°"], body["warnings"])
            self.assertIn("fonte longa", body["summary"]["line"])
            self.assertIn("360", body["summary"]["line"])


class DoctorAcceptsProjectTests(unittest.TestCase):
    """Fricção 4 da rodada 2: o SKILL manda `--project` em todo comando e o doctor recusava."""

    def test_doctor_takes_and_ignores_project(self):
        parsed = build_parser().parse_args(["doctor", "--project", "/tmp/p"])
        self.assertEqual("doctor", parsed.command)
        self.assertEqual("/tmp/p", parsed.project)
        with tempfile.TemporaryDirectory() as tmp:
            done = run_cli(["doctor", "--project", tmp])
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            self.assertIn("summary", json.loads(done.stdout))
            # Comando de diagnóstico não cria projeto nenhum.
            self.assertFalse((Path(tmp) / "brolls").exists())


class ScanTests(unittest.TestCase):
    def test_preview_scan_is_a_flag_and_the_cap_is_a_known_env_var(self):
        from getbrolls.config import KEYS, settings

        parsed = build_parser().parse_args(["preview", "--project", "/tmp/p", "--candidate", "x", "--scan"])
        self.assertTrue(parsed.scan)
        self.assertIn("GB_SCAN_MAX_SECONDS", KEYS)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GB_SCAN_MAX_SECONDS", None)
            self.assertEqual(900, settings()["scan_max_seconds"])

    def test_scan_and_an_interval_together_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = run_cli(["preview", "--project", tmp, "--candidate", "x", "--scan", "--start", "0", "--end", "5"])
            self.assertNotEqual(0, done.returncode)
            self.assertIn("--scan", done.stdout + done.stderr)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_scan_maps_the_whole_local_video_without_choosing_an_interval(self):
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
                    "testsrc=size=160x90:duration=20:rate=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            run_cli(["init-rules", "--project", tmp])
            resolved = run_cli(["resolve", "--file", str(src), "--project", tmp])
            candidate = json.loads(resolved.stdout)["id"]
            done = run_cli(["preview", "--project", tmp, "--candidate", candidate, "--scan"])
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            payload = json.loads(done.stdout)
            self.assertTrue(Path(payload["files"]["scan"]).is_file())
            # `--scan` não é uma resposta menor que as outras: `files` traz o mesmo
            # contrato, com a varredura a mais.
            self.assertLessEqual({"contact_sheet", "poster", "gif", "review", "scan"}, set(payload["files"]))
            self.assertEqual(12, payload["scan"]["frames"])
            self.assertAlmostEqual(20 / 12, payload["scan"]["every_s"], places=2)
            # Rótulos em tempo da fonte, como `preview.frame_times_s`: é com eles que
            # a pessoa escreve o --start/--end do preview.
            times = payload["scan"]["frame_times_s"]
            self.assertEqual(12, len(times))
            self.assertAlmostEqual(0.0, times[0], places=2)
            self.assertLess(times[-1], 20.0)
            # Varrer não decide: nem intervalo, nem aprovação, nem prévia do trecho.
            self.assertIsNone(payload["segment"]["start_s"])
            self.assertEqual(0, payload["segment"]["revision"])
            self.assertEqual("pending", payload["approval"]["status"])
            self.assertIsNone(payload["preview"].get("contact_sheet_path"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_the_cap_limits_how_much_of_a_long_video_is_scanned(self):
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
                    "testsrc=size=160x90:duration=60:rate=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            run_cli(["init-rules", "--project", tmp])
            resolved = run_cli(["resolve", "--file", str(src), "--project", tmp])
            candidate = json.loads(resolved.stdout)["id"]
            done = run_cli(
                ["preview", "--project", tmp, "--candidate", candidate, "--scan"],
                env={"GB_SCAN_MAX_SECONDS": "30"},
            )
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            scan = json.loads(done.stdout)["scan"]
            self.assertEqual(30.0, scan["span_s"])
            self.assertTrue(scan["capped"])
            self.assertEqual(30.0, scan["downloaded_seconds"])
            self.assertIn("GB_SCAN_MAX_SECONDS", scan["note"])
            self.assertIn("30", scan["note"])

    def test_the_preview_ceiling_error_names_the_limit_and_the_value_asked(self):
        from getbrolls.commands import execute
        from getbrolls.runtime import audited

        with tempfile.TemporaryDirectory() as tmp:
            run_cli(["init-rules", "--project", tmp])
            candidate = project_with_candidate(tmp)
            args = types.SimpleNamespace(
                command="preview",
                project=tmp,
                env_file=None,
                confirm_format_change=False,
                candidate=candidate,
                start=0.0,
                end=40.0,
                scan=False,
                reference_only=False,
                narration=None,
                reason=None,
            )
            from getbrolls.runtime import OperationError

            with patch.dict(os.environ, {"GB_PREVIEW_MAX_SECONDS": "10"}):
                with self.assertRaises(OperationError) as caught:
                    audited(args, execute)
            message = str(caught.exception)
            self.assertIn("GB_PREVIEW_MAX_SECONDS", message)
            self.assertIn("10", message)
            self.assertIn("40", message)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_the_grid_follows_the_media_that_exists_not_the_span_requested(self):
        """A duração anunciada pode passar do arquivo real; a grade segue o arquivo."""
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
                    "testsrc=size=160x90:duration=8:rate=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            run_cli(["init-rules", "--project", tmp])
            resolved = run_cli(["resolve", "--file", str(src), "--project", tmp])
            candidate = json.loads(resolved.stdout)["id"]
            manifest = Path(tmp) / "brolls/manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            # A fonte mente: diz 126 s onde o arquivo tem 8 s.
            for item in data["items"]:
                item["media"]["duration_s"] = 126.0
            manifest.write_text(json.dumps(data), encoding="utf-8")
            done = run_cli(["preview", "--project", tmp, "--candidate", candidate, "--scan"])
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            scan = json.loads(done.stdout)["scan"]
            self.assertLessEqual(scan["span_s"], 8.5)
            self.assertLessEqual(scan["downloaded_seconds"], 8.5)
            # Rótulos dentro do que existe: nenhum quadro anunciado depois do fim.
            self.assertLess(scan["frame_times_s"][-1], 8.5)
            self.assertTrue(Path(tmp, "brolls", scan["scan_path"]).is_file())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_a_whole_short_video_says_so_in_the_note(self):
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
            run_cli(["init-rules", "--project", tmp])
            resolved = run_cli(["resolve", "--file", str(src), "--project", tmp])
            candidate = json.loads(resolved.stdout)["id"]
            done = run_cli(["preview", "--project", tmp, "--candidate", candidate, "--scan"])
            scan = json.loads(done.stdout)["scan"]
            self.assertFalse(scan["capped"])
            self.assertIn("inteiro", scan["note"])


class DirectMediaSourceTests(unittest.TestCase):
    """Fricção 1 da rodada 2: candidato NASA ia para o yt-dlp e voltava "Unsupported URL"."""

    MEDIA_URL = "https://images-assets.nasa.gov/video/KSC-2022/KSC-2022~medium.mp4"

    def _fixture(self, tmp, seconds=8):
        """Arquivo local no lugar do `media_url`: nada sai para a rede no teste."""
        src = Path(tmp) / "nasa.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size=160x90:duration={seconds}:rate=10",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(src),
            ],
            check=True,
        )
        return src

    def _candidate(self, tmp):
        from getbrolls.ledger import Ledger
        from getbrolls.models import candidate

        ledger = Ledger(tmp)
        item = candidate("nasa", "KSC-2022", "Rollout for launch")
        item["source_url"] = "https://images.nasa.gov/details/KSC-2022"
        item["media_url"] = self.MEDIA_URL
        item["acquisition"].update({"status": "available", "method": "https", "evidence": []})
        stored = ledger.add(item)
        ledger.save("fixture", stored)
        return stored["id"]

    def _args(self, **extra):
        base = dict(env_file=None, confirm_format_change=False, project=None)
        base.update(extra)
        return types.SimpleNamespace(**base)

    def _patches(self, fixture):
        def fake_download(url, target, **kwargs):
            self.assertEqual(self.MEDIA_URL, url)
            shutil.copyfile(fixture, target)
            return target

        def explode(*args, **kwargs):
            raise AssertionError("fonte de arquivo direto não pode ir para o yt-dlp")

        return (
            patch("getbrolls.http.download", side_effect=fake_download),
            patch("getbrolls.providers.refresh", side_effect=lambda item: dict(item)),
            patch("getbrolls.social.probe_remote", side_effect=explode),
            patch("getbrolls.social.download_segment", side_effect=explode),
        )

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_inspect_reads_the_duration_from_the_direct_file_and_reports_no_subtitles(self):
        from getbrolls.commands import execute
        from getbrolls.runtime import audited

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(tmp)
            candidate_id = self._candidate(tmp)
            run_cli(["init-rules", "--project", tmp])
            args = self._args(
                command="inspect",
                project=tmp,
                candidate=candidate_id,
                url=None,
                query="rollout",
                max_windows=3,
            )
            download, refresh, probe_remote, segment = self._patches(fixture)
            with download, refresh, probe_remote, segment:
                payload = audited(args, execute)
            self.assertAlmostEqual(8.0, payload["duration_s"], places=1)
            self.assertEqual([], payload["subtitle_langs"])
            self.assertEqual([], payload["chapters"])
            self.assertTrue(payload["candidate_windows"])
            for window in payload["candidate_windows"]:
                self.assertLessEqual(window["end_s"], 8.05)
            # Único efeito no projeto: a duração, como em qualquer `inspect`.
            from getbrolls.ledger import Ledger

            stored = Ledger(tmp).get(candidate_id)
            self.assertAlmostEqual(8.0, stored["media"]["duration_s"], places=1)
            self.assertIsNone(stored["segment"]["start_s"])
            self.assertEqual("pending", stored["approval"]["status"])

    def test_the_download_this_route_costs_is_announced_with_its_size(self):
        from getbrolls.commands import inspect_warnings

        found = inspect_warnings({"duration_s": 8.0, "title": "Rollout", "downloaded_bytes": 3 * 1024 * 1024})
        self.assertEqual(1, len(found))
        self.assertIn("baixar o arquivo inteiro", found[0])
        self.assertIn("3.0 MB", found[0])
        # Rota normal (página com metadados) não baixa nada e não avisa nada.
        self.assertEqual([], inspect_warnings({"duration_s": 8.0, "title": "Rollout"}))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_inspect_on_a_direct_source_says_it_had_to_download_the_file(self):
        from getbrolls.commands import execute
        from getbrolls.runtime import audited

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(tmp)
            candidate_id = self._candidate(tmp)
            run_cli(["init-rules", "--project", tmp])
            args = self._args(
                command="inspect",
                project=tmp,
                candidate=candidate_id,
                url=None,
                query=None,
                max_windows=3,
            )
            download, refresh, probe_remote, segment = self._patches(fixture)
            with download, refresh, probe_remote, segment:
                payload = audited(args, execute)
            warning = next(w for w in payload["warnings"] if "arquivo inteiro" in w)
            self.assertIn("MB", warning)
            self.assertIn("arquivo inteiro", payload["summary"]["line"])
            self.assertNotIn("downloaded_bytes", payload)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_preview_uses_the_direct_download_instead_of_ytdlp(self):
        from getbrolls.commands import execute
        from getbrolls.runtime import audited

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(tmp)
            candidate_id = self._candidate(tmp)
            run_cli(["init-rules", "--project", tmp])
            args = self._args(
                command="preview",
                project=tmp,
                candidate=candidate_id,
                start=1.0,
                end=4.0,
                scan=False,
                reference_only=False,
                narration=None,
                reason=None,
            )
            download, refresh, probe_remote, segment = self._patches(fixture)
            with download, refresh, probe_remote, segment:
                payload = audited(args, execute)
            self.assertTrue(Path(payload["files"]["contact_sheet"]).is_file())
            self.assertEqual((1.0, 4.0), (payload["segment"]["start_s"], payload["segment"]["end_s"]))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_scan_works_on_a_direct_source_without_a_known_duration(self):
        from getbrolls.commands import execute
        from getbrolls.runtime import audited

        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(tmp)
            candidate_id = self._candidate(tmp)
            run_cli(["init-rules", "--project", tmp])
            args = self._args(
                command="preview",
                project=tmp,
                candidate=candidate_id,
                start=None,
                end=None,
                scan=True,
                reference_only=False,
                narration=None,
                reason=None,
            )
            download, refresh, probe_remote, segment = self._patches(fixture)
            with download, refresh, probe_remote, segment:
                payload = audited(args, execute)
            self.assertTrue(Path(payload["files"]["scan"]).is_file())
            # `--scan` não é uma resposta menor que as outras: `files` traz o mesmo
            # contrato, com a varredura a mais.
            self.assertLessEqual({"contact_sheet", "poster", "gif", "review", "scan"}, set(payload["files"]))
            self.assertEqual(12, payload["scan"]["frames"])
            self.assertLessEqual(payload["scan"]["frame_times_s"][-1], 8.0)


# Uma cor por faixa de 15 s: o quadro diz sozinho em que segundo da fonte ele estava.
SCAN_COLORS = {"red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0)}
SCAN_BAND_S = 15


def color_at(source_second):
    """Cor que a fonte mostra nesse segundo, pela ordem das faixas."""
    return list(SCAN_COLORS)[min(int(source_second // SCAN_BAND_S), len(SCAN_COLORS) - 1)]


def cell_rgb(sheet, index, cols=4, width=240, height=136, padding=6, margin=6):
    """RGB do centro da célula `index` do contact sheet, sem dependência de imagem."""
    col, row = index % cols, index // cols
    x = margin + col * (width + padding) + width // 2
    y = margin + row * (height + padding) + height // 2
    with tempfile.TemporaryDirectory() as work:
        raw = Path(work) / "pixel.raw"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(sheet),
                "-vf",
                # 2x2: o recorte de 1 px é recusado pelo croma do JPEG.
                f"crop=2:2:{x}:{y}",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                str(raw),
            ],
            check=True,
        )
        return tuple(raw.read_bytes()[:3])


def nearest_color(rgb):
    return min(SCAN_COLORS, key=lambda name: sum((a - b) ** 2 for a, b in zip(SCAN_COLORS[name], rgb, strict=False)))


class ScanLabelsMatchTheSourceTests(unittest.TestCase):
    """Fricção 4 da rodada 2: a célula da grade não mostrava o que o rótulo prometia."""

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_a_working_copy_that_starts_at_20s_still_labels_source_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            whole = Path(tmp) / "fonte.mp4"
            inputs = []
            for name in SCAN_COLORS:
                inputs += ["-f", "lavfi", "-i", f"color=c={name}:s=160x90:d={SCAN_BAND_S}:r=10"]
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    *inputs,
                    "-filter_complex",
                    "".join(f"[{i}:v]" for i in range(len(SCAN_COLORS))) + f"concat=n={len(SCAN_COLORS)}:v=1[v]",
                    "-map",
                    "[v]",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(whole),
                ],
                check=True,
            )
            # A mídia de trabalho é um recorte que começa aos 20 s da fonte — é o que
            # sobra de uma prévia anterior, e é com ela que a varredura tem de contar.
            trimmed = Path(tmp) / "trabalho.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "20",
                    "-i",
                    str(whole),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(trimmed),
                ],
                check=True,
            )
            run_cli(["init-rules", "--project", tmp])
            resolved = run_cli(["resolve", "--file", str(trimmed), "--project", tmp])
            candidate_id = json.loads(resolved.stdout)["id"]
            manifest = Path(tmp) / "brolls/manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for item in data["items"]:
                item["local_start_s"] = 20.0
                item["media"]["duration_s"] = float(SCAN_BAND_S * len(SCAN_COLORS))
            manifest.write_text(json.dumps(data), encoding="utf-8")

            done = run_cli(["preview", "--project", tmp, "--candidate", candidate_id, "--scan"])
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            scan = json.loads(done.stdout)["scan"]
            times = scan["frame_times_s"]
            self.assertAlmostEqual(20.0, times[0], places=1)
            self.assertAlmostEqual(20.0, scan["start_s"], places=1)
            self.assertAlmostEqual(60.0, scan["end_s"], delta=0.6)
            sheet = Path(tmp) / "brolls" / scan["scan_path"]
            self.assertTrue(sheet.is_file())
            for index in (0, 4, 8, 11):
                label = times[index]
                with self.subTest(cell=index, label=label):
                    self.assertEqual(color_at(label), nearest_color(cell_rgb(sheet, index)))
            # A nota diz o trecho real da fonte, não "os primeiros N s".
            self.assertIn("0:20", scan["note"])
            self.assertNotIn("primeiros", scan["note"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_the_scan_says_it_ignores_the_stored_segment(self):
        """Fricção 2 da rodada 2: `--scan` parecia não fazer nada em quem já tinha intervalo."""
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
                    "testsrc=size=160x90:duration=12:rate=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            run_cli(["init-rules", "--project", tmp])
            resolved = run_cli(["resolve", "--file", str(src), "--project", tmp])
            candidate_id = json.loads(resolved.stdout)["id"]
            run_cli(["preview", "--project", tmp, "--candidate", candidate_id, "--start", "4", "--end", "6"])
            done = run_cli(["preview", "--project", tmp, "--candidate", candidate_id, "--scan"])
            self.assertEqual(0, done.returncode, done.stdout + done.stderr)
            payload = json.loads(done.stdout)
            scan = payload["scan"]
            self.assertIn("ignora o intervalo já escolhido", scan["note"])
            # A grade é do vídeo inteiro, não do intervalo guardado.
            self.assertAlmostEqual(0.0, scan["frame_times_s"][0], places=1)
            self.assertGreater(scan["frame_times_s"][-1], 6.0)
            # E varrer não mexe no que já estava decidido.
            self.assertEqual((4.0, 6.0), (payload["segment"]["start_s"], payload["segment"]["end_s"]))


class LanguageMismatchTests(unittest.TestCase):
    """Legenda em EN e `--query` em PT pontuam zero: isso é idioma, não conteúdo."""

    def test_the_vtt_language_header_is_read(self):
        self.assertEqual("en", inspecting.parse_vtt_language("WEBVTT\nKind: captions\nLanguage: en\n\n"))
        self.assertEqual("pt-BR", inspecting.parse_vtt_language("WEBVTT\nLanguage: pt_BR\n\n"))
        self.assertIsNone(inspecting.parse_vtt_language("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\noi\n"))

    def test_a_language_code_reduces_to_its_base(self):
        self.assertEqual("pt", inspecting.base_language("pt-BR"))
        for empty in ("und", "", None, "zxx", "123"):
            self.assertIsNone(inspecting.base_language(empty))

    def test_the_query_language_comes_from_function_words(self):
        self.assertEqual("pt", inspecting.guess_language("o momento em que ele diz que não funciona"))
        self.assertEqual("en", inspecting.guess_language("the moment when he says that it does not work"))
        # Sem palavra funcional de nenhum dos dois: melhor não arriscar um palpite.
        self.assertIsNone(inspecting.guess_language("Jensen Huang GTC 2024"))

    def test_only_a_real_difference_is_reported(self):
        probe = {"subtitle_langs": ["pt", "en-orig"], "original_lang": "en-orig"}
        self.assertEqual(("en", "pt"), inspecting.language_mismatch(probe, "o trecho em que ele fala do preço"))
        self.assertIsNone(inspecting.language_mismatch(probe, "the part where he talks about the price"))
        self.assertIsNone(inspecting.language_mismatch({"subtitle_langs": ["pt-BR"]}, "o trecho em que ele fala"))
        # Sem legenda declarada não há com o que comparar.
        self.assertIsNone(inspecting.language_mismatch({"subtitle_langs": []}, "o trecho em que ele fala"))

    def test_the_warning_names_both_sides_and_says_what_to_do(self):
        from getbrolls.commands import inspect_warnings

        warnings = inspect_warnings({"subtitle_langs": ["en"]}, "o trecho em que ele fala do preço")
        self.assertIn(
            "legenda em EN, sua --query está em PT: traduza a fala ao idioma da fonte",
            warnings,
        )

    def test_the_summary_line_says_it_too(self):
        from getbrolls.commands import inspect_summary

        probe = {"duration_s": 90.0, "subtitle_langs": ["en"], "subtitles": {}}
        windows = [{"start_s": 0.0, "end_s": 12.0, "text": "", "source": "even_spacing", "score": 0.0}]
        line = inspect_summary(windows, probe, 0.0, "o trecho em que ele fala do preço")["line"]
        self.assertIn("legenda em EN", line)
        self.assertIn("traduza a fala ao idioma da fonte", line)
        # Sem janela nenhuma o aviso continua aparecendo: é ele que explica o zero.
        empty = inspect_summary([], probe, 0.0, "o trecho em que ele fala do preço")["line"]
        self.assertIn("legenda em EN", empty)

    def test_the_skill_and_its_mirror_ask_for_the_query_in_the_source_language(self):
        for path in (ROOT / "SKILL.md", ROOT / "skills/get-brolls/SKILL.md"):
            self.assertIn("no idioma da fonte", path.read_text(encoding="utf-8"))

    def test_the_skill_states_the_per_preview_cap_and_one_call_each(self):
        for path in (ROOT / "SKILL.md", ROOT / "skills/get-brolls/SKILL.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("GB_PREVIEW_MAX_SECONDS", text)
            self.assertIn("10 s por prévia", text)
            self.assertIn("um `preview` por chamada", text)

    def test_the_skill_locates_the_contact_sheet_in_the_manifest_too(self):
        for path in (ROOT / "SKILL.md", ROOT / "skills/get-brolls/SKILL.md"):
            text = path.read_text(encoding="utf-8")
            self.assertIn("files.contact_sheet", text)
            self.assertIn("preview.contact_sheet_path", text)
            self.assertIn("relativo a `brolls/`", text)


class PreviewFilesOnEveryBranchTests(unittest.TestCase):
    """Toda rota de `preview` devolve `files` com caminho absoluto; nenhuma fica muda."""

    def test_the_scan_branch_carries_the_other_artifacts_too(self):
        from getbrolls.commands import preview_files

        class FakeLedger:
            root = Path("/tmp/projeto/brolls")

        item = {"preview": {"contact_sheet_path": "previews/a.jpg", "poster_path": None, "gif_path": None}}
        files = preview_files(FakeLedger(), item)
        self.assertEqual({"contact_sheet", "poster", "gif", "review"}, set(files))
        self.assertTrue(files["contact_sheet"].endswith("brolls/previews/a.jpg"))
        self.assertTrue(Path(files["contact_sheet"]).is_absolute())
        self.assertIsNone(files["poster"])

    def test_providers_reference_locates_the_contact_sheet_in_both_shapes(self):
        body = (ROOT / "references/providers.md").read_text(encoding="utf-8")
        self.assertIn("`contact_sheet`", body)
        self.assertIn("preview.contact_sheet_path", body)
        self.assertIn("relativo a `brolls/`", body)
        self.assertIn("`--reference-only`, `--scan`", body)

    def test_providers_reference_says_one_preview_per_call(self):
        body = (ROOT / "references/providers.md").read_text(encoding="utf-8")
        self.assertIn("Um `preview` por chamada", body)
        self.assertIn("teto de tempo da ferramenta", body)

    def test_providers_reference_covers_consent_banners_before_capture(self):
        body = (ROOT / "references/providers.md").read_text(encoding="utf-8")
        self.assertIn("Aviso de cookies antes de capturar", body)
        self.assertIn("mais\npreservadora de privacidade", body)
        self.assertIn("Rejeitar tudo", body)
        self.assertIn("não existe código na skill que dispense banner sozinho", body)

    def test_providers_reference_covers_the_desktop_app_screen(self):
        body = (ROOT / "references/providers.md").read_text(encoding="utf-8")
        self.assertIn("Tela de um aplicativo de desktop", body)
        self.assertIn("screenshot do seu computador", body)
        self.assertIn("tutorial ou demonstração no YouTube", body)
        self.assertIn("recriar a interface de memória", body)


if __name__ == "__main__":
    unittest.main()
