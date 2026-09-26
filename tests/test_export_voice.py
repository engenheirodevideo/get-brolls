"""Voz no export: ffprobe (duração, trilha de áudio) e sidecar de transcrição validado e deslocado."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import export_voice


def synth_voice(path, duration=2):
    """Vídeo com trilha de áudio (seno), para o ffprobe ver `has_audio`."""
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x90:duration={duration}:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
    )  # fmt: skip


def words(*triples):
    return [{"text": t, "start": s, "end": e} for t, s, e in triples]


class ProbeVoiceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-voice-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    @skip_unless_ffmpeg
    def test_video_with_and_without_audio(self):
        synth_voice(self.root / "c01.mov", duration=2)
        synth_video(self.root / "c02.mp4", duration=3)
        with_audio = export_voice.probe_voice(self.root / "c01.mov")
        self.assertTrue(with_audio["has_audio"])
        self.assertAlmostEqual(2.0, with_audio["duration_s"], delta=0.15)
        self.assertEqual((160, 90), (with_audio["width"], with_audio["height"]))
        silent = export_voice.probe_voice(self.root / "c02.mp4")
        self.assertFalse(silent["has_audio"])
        self.assertAlmostEqual(3.0, silent["duration_s"], delta=0.15)

    @skip_unless_ffmpeg
    def test_unreadable_file_raises_value_error(self):
        broken = self.root / "c03.mov"
        broken.write_bytes(b"isto nao e video")
        with self.assertRaises(ValueError):
            export_voice.probe_voice(broken)

    def test_missing_ffprobe_is_its_own_error(self):
        video = self.root / "c01.mov"
        video.write_bytes(b"voz")
        with (
            mock.patch.object(export_voice.media.subprocess, "run", side_effect=FileNotFoundError("ffprobe")),
            self.assertRaises(export_voice.ProbeMissingError) as caught,
        ):
            export_voice.probe_voice(video)
        self.assertIsInstance(caught.exception, ValueError)
        with (
            mock.patch.object(export_voice.media, "run", side_effect=FileNotFoundError("ffprobe")),
            self.assertRaises(export_voice.ProbeMissingError),
        ):
            export_voice.probe_voice(video)


class TimedWordsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-sidecar-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.voice = self.root / "c03.mov"
        self.voice.write_bytes(b"video")
        os.utime(self.voice, ns=(1_000_000_000_000, 1_000_000_000_000))

    def sidecar(self, data, raw=None):
        path = self.root / "c03.transcript.json"
        path.write_bytes(raw if raw is not None else json.dumps(data).encode("utf-8"))
        os.utime(path, ns=(2_000_000_000_000, 2_000_000_000_000))
        return path

    def run_words(self, duration=5.0, window=(9.6, 5.2)):
        return export_voice.timed_words(self.voice, duration, window, "c03")

    def test_sidecar_name_follows_the_voice_stem(self):
        self.assertEqual("c01-t2.transcript.json", export_voice.sidecar_path(Path("aroll/c01-t2.mov")).name)
        self.assertEqual("c03-a.transcript.json", export_voice.sidecar_path(Path("aroll/c03-a.MOV")).name)

    def test_missing_sidecar_is_a_silent_estimate(self):
        self.assertEqual((None, []), self.run_words())

    def test_valid_sidecar_is_shifted_to_global_time_and_ids_dropped(self):
        data = words(("Eu", 0.02, 0.2), ("digo,", 0.2, 0.451))
        data[0]["id"] = "w0"
        self.sidecar(data)
        found, warnings = self.run_words()
        self.assertEqual(words(("Eu", 9.62, 9.8), ("digo,", 9.8, 10.051)), found)
        self.assertEqual([], warnings)

    def test_words_are_clipped_to_the_scene_window(self):
        self.sidecar(words(("a", 0.0, 1.0), ("b", 4.9, 5.04), ("c", 5.0, 5.04)))
        found, warnings = self.run_words(duration=5.0, window=(10.0, 5.0))
        self.assertEqual(words(("a", 10.0, 11.0), ("b", 14.9, 15.0)), found)
        self.assertEqual(["c03: 1 palavra(s) de aroll/c03.transcript.json passam do fim da cena"], warnings)

    def test_older_sidecar_is_ignored_with_a_warning(self):
        path = self.sidecar(words(("a", 0.0, 1.0)))
        os.utime(path, ns=(500_000_000_000, 500_000_000_000))
        found, warnings = self.run_words()
        self.assertIsNone(found)
        self.assertIn("mais velha que o A-ROLL", warnings[0])

    def test_bad_shapes_fall_back_to_the_estimate(self):
        cases = [
            ("não é JSON UTF-8", None, b"\xff\xfe nada"),
            ("não é JSON UTF-8", None, b'[{"text": "a", "start": 0, "end": ' + b"1" * 5000 + b"}]"),
            ("não é JSON UTF-8", None, b"[" * 200_000 + b"]" * 200_000),
            ("não é uma lista de palavras", {"words": []}, None),
            ("item 0 não é objeto", ["oi"], None),
            ("item 0 sem texto", words(("  ", 0.0, 1.0)), None),
            (
                "item 1 com texto longo demais ou em mais de uma linha",
                words(("a", 0.0, 1.0), ("b\nc", 1.0, 2.0)),
                None,
            ),
            ("item 0 com tempo inválido", words(("a", 1.0, 1.0)), None),
            ("item 0 com tempo inválido", None, b'[{"text": "a", "start": 0, "end": 1' + b"0" * 400 + b"}]"),
            ("item 1 começa antes do anterior", words(("a", 1.0, 2.0), ("b", 0.5, 2.5)), None),
            ("passa da duração do vídeo", words(("a", 0.0, 5.2)), None),
            ("passa da duração do vídeo", words(("a", 0.0, 100.0), ("b", 1.0, 2.0)), None),
        ]
        for index, (reason, data, raw) in enumerate(cases):
            with self.subTest(index=index, reason=reason):
                self.sidecar(data, raw)
                found, warnings = self.run_words(duration=5.0)
                self.assertIsNone(found)
                self.assertEqual([f"c03: aroll/c03.transcript.json inválido ({reason}): legenda estimada"], warnings)

    def test_bool_and_nan_times_are_refused(self):
        for bad in (True, float("nan"), float("inf"), "1"):
            with self.subTest(bad=bad):
                raw = json.dumps([{"text": "a", "start": 0, "end": bad}]).encode("utf-8")
                self.sidecar(None, raw)
                found, warnings = self.run_words()
                self.assertIsNone(found)
                self.assertEqual(
                    ["c03: aroll/c03.transcript.json inválido (item 0 com tempo inválido): legenda estimada"], warnings
                )

    def test_empty_sidecar_is_an_estimate_with_a_warning(self):
        self.sidecar([])
        self.assertEqual(
            (None, ["c03: transcrição vazia: legenda estimada (aroll/c03.transcript.json)"]), self.run_words()
        )

    def test_same_mtime_sidecar_is_accepted(self):
        path = self.sidecar(words(("a", 0.0, 1.0)))
        os.utime(path, ns=(1_000_000_000_000, 1_000_000_000_000))
        self.assertEqual((words(("a", 9.6, 10.6)), []), self.run_words())

    def test_too_many_words_and_too_big_file(self):
        self.sidecar(words(*((f"w{i}", i * 0.001, i * 0.001 + 0.0005) for i in range(5001))))
        self.assertIn("mais de 5000 palavras", self.run_words(duration=10.0)[1][0])
        self.sidecar(None, b"[" + b" " * (1024 * 1024) + b"]")
        self.assertIn("passa de 1 MiB", self.run_words()[1][0])

    def test_link_sidecar_is_refused(self):
        real = self.root / "outro.json"
        real.write_text(json.dumps(words(("a", 0.0, 1.0))), encoding="utf-8")
        link = self.root / "c03.transcript.json"
        try:
            link.symlink_to(real)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        found, warnings = self.run_words()
        self.assertIsNone(found)
        self.assertIn("(é um link)", warnings[0])

    def refused_reason(self):
        found, warnings = self.run_words()
        self.assertIsNone(found)
        self.assertEqual(1, len(warnings))
        return warnings[0]

    def test_dangling_link_sidecar_is_refused(self):
        try:
            (self.root / "c03.transcript.json").symlink_to(self.root / "sumiu.json")
        except OSError:
            self.skipTest("este sistema não cria symlink")
        self.assertIn("(é um link)", self.refused_reason())

    def test_hardlink_sidecar_is_refused(self):
        real = self.root / "outro.json"
        real.write_text(json.dumps(words(("a", 0.0, 1.0))), encoding="utf-8")
        os.utime(real, ns=(2_000_000_000_000, 2_000_000_000_000))
        try:
            os.link(real, self.root / "c03.transcript.json")
        except OSError:
            self.skipTest("este sistema não cria hardlink")
        self.assertIn("(é um link)", self.refused_reason())

    def test_directory_sidecar_is_refused(self):
        (self.root / "c03.transcript.json").mkdir()
        self.assertIn("(não é um arquivo)", self.refused_reason())

    @unittest.skipIf(os.name == "nt", "FIFO não existe no Windows")
    def test_fifo_sidecar_is_refused_without_blocking(self):
        os.mkfifo(self.root / "c03.transcript.json")
        self.assertIn("(não é um arquivo)", self.refused_reason())


if __name__ == "__main__":
    unittest.main()
