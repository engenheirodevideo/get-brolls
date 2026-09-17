"""Analisar antes de coletar: o que a fonte tem, antes de pedir mídia."""

import json
import os
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls import inspecting, social
from getbrolls.cli import build_parser, main

URL = "https://www.youtube.com/watch?v=abcdefghijk"

VTT = """WEBVTT

00:00:00.000 --> 00:00:04.000
A tempestade de areia cobriu a cidade

00:00:04.000 --> 00:00:08.500
<c>e o céu ficou laranja no meio da tarde</c>

00:00:20.000 --> 00:00:24.000
Depois disso, os moradores voltaram às ruas
"""

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

STUB = """#!{python}
import json, os, sys
data = json.loads(os.environ["GB_TEST_YTDLP_JSON"])
subtitle = os.environ.get("GB_TEST_YTDLP_VTT")
argv = sys.argv[1:]
if "-o" in argv:
    template = argv[argv.index("-o") + 1]
    if subtitle:
        for lang in ("pt",):
            path = template.replace("%(ext)s", lang + ".vtt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(subtitle)
print(json.dumps(data))
"""


def stub_ytdlp(directory, payload, vtt=None):
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

    def test_nothing_to_offer_is_an_empty_list_not_an_error(self):
        probe = self.probe(chapters=[], subtitles={}, subtitle_langs=[], description="")
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
        self.assertEqual(["en", "pt"], probe["subtitle_langs"])
        self.assertIn("poeira", probe["description"])

    def test_the_vtt_lands_in_the_private_sources_folder_with_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / ".getbrolls-sources"
            env = stub_ytdlp(tmp, WITH_EVERYTHING, VTT)
            with patch.dict(os.environ, env):
                probe = social.probe_remote(URL, cache=cache)
            saved = Path(probe["subtitles"]["pt"]["path"])
            self.assertEqual(cache, saved.parent)
            self.assertEqual(0o600, stat.S_IMODE(saved.stat().st_mode))
            self.assertIn("tempestade", saved.read_text(encoding="utf-8").lower())
            self.assertTrue(probe["subtitles"]["pt"]["cues"])

    def test_a_source_without_chapters_or_subtitles_still_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = stub_ytdlp(tmp, BARE)
            with patch.dict(os.environ, env):
                probe = social.probe_remote(URL, cache=Path(tmp) / ".getbrolls-sources")
        self.assertEqual(90.0, probe["duration_s"])
        self.assertEqual([], probe["chapters"])
        self.assertEqual([], probe["subtitle_langs"])
        self.assertEqual({}, probe["subtitles"])

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
        self.assertIn("--dump-single-json", captured["args"])
        self.assertIn("--write-auto-subs", captured["args"])
        self.assertIn("pt,en", captured["args"])
        self.assertIn("--sleep-requests", captured["command"])
        self.assertEqual("2", captured["command"][captured["command"].index("--sleep-requests") + 1])


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
            self.assertEqual(["en", "pt"], payload["subtitle_langs"])
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


if __name__ == "__main__":
    unittest.main()
