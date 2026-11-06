"""Voz no export: ffprobe com resposta ruim e falhas de leitura do sidecar e da transcrição de `analysis/`."""

import errno
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_export_voice import PRODUCER, analysis_words, words

from getbrolls import analysis, export_voice


def probe_with(raw):
    """`probe_voice` de um arquivo qualquer com o ffprobe respondendo `raw`."""
    with mock.patch.object(export_voice.media, "run", return_value=raw):
        return export_voice.probe_voice(Path("aroll/c01.mov"))


class ProbeAnswerTests(unittest.TestCase):
    def test_file_without_a_video_stream_is_refused(self):
        raw = json.dumps({"streams": [{"codec_type": "audio"}], "format": {"duration": "3"}})
        with self.assertRaisesRegex(ValueError, "arquivo sem vídeo"):
            probe_with(raw)

    def test_unreadable_answers_are_refused(self):
        for raw in (
            "isto não é json",
            "[1, 2]",
            '{"streams": [1]}',
            '{"streams": [{"codec_type": "video"}], "format": "x"}',
        ):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "resposta do ffprobe ilegível"):
                probe_with(raw)

    def test_missing_or_non_positive_duration_is_refused(self):
        for duration in ("0", "-1", "nan", "inf"):
            raw = json.dumps({"streams": [{"codec_type": "video"}], "format": {"duration": duration}})
            with self.subTest(duration=duration), self.assertRaisesRegex(ValueError, "duração ilegível"):
                probe_with(raw)
        with self.assertRaisesRegex(ValueError, "duração ilegível"):
            probe_with(json.dumps({"streams": [{"codec_type": "video"}]}))

    def test_duration_falls_back_to_the_stream_and_rounds(self):
        raw = json.dumps({"streams": [{"codec_type": "video", "duration": "2.12345", "width": 4, "height": 2}]})
        self.assertEqual(
            {"duration_s": 2.123, "width": 4, "height": 2, "has_audio": False},
            probe_with(raw),
        )

    def test_a_missing_executable_wrapped_by_media_run_is_a_probe_missing_error(self):
        wrapped = ValueError("ffprobe não encontrado")
        wrapped.__cause__ = FileNotFoundError("ffprobe")
        with (
            mock.patch.object(export_voice.media, "run", side_effect=wrapped),
            self.assertRaises(export_voice.ProbeMissingError) as caught,
        ):
            export_voice.probe_voice(Path("aroll/c01.mov"))
        self.assertEqual("ffprobe não encontrado", str(caught.exception))

    def test_other_value_errors_from_the_run_pass_through(self):
        with (
            mock.patch.object(export_voice.media, "run", side_effect=ValueError("arquivo corrompido")),
            self.assertRaises(ValueError) as caught,
        ):
            export_voice.probe_voice(Path("aroll/c01.mov"))
        self.assertNotIsInstance(caught.exception, export_voice.ProbeMissingError)
        self.assertEqual("arquivo corrompido", str(caught.exception))


class SidecarReadFailureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-sidecar-edges-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.voice = self.root / "c03.mov"
        self.voice.write_bytes(b"video")
        os.utime(self.voice, ns=(1_000_000_000_000, 1_000_000_000_000))
        self.sidecar = self.root / "c03.transcript.json"
        self.sidecar.write_text(json.dumps(words(("a", 0.0, 1.0))), encoding="utf-8")
        os.utime(self.sidecar, ns=(2_000_000_000_000, 2_000_000_000_000))

    def run_words(self):
        return export_voice.timed_words(self.voice, 5.0, (9.6, 5.2), "c03")

    def assert_refused(self, reason):
        found, warnings = self.run_words()
        self.assertIsNone(found)
        self.assertEqual([f"c03: aroll/c03.transcript.json inválido ({reason}): legenda estimada"], warnings)

    def test_lstat_failure_is_reported_as_unreadable(self):
        with mock.patch.object(Path, "lstat", side_effect=PermissionError(errno.EACCES, "negado")):
            self.assert_refused("não consegui ler")

    def test_open_failure_is_reported_as_unreadable(self):
        with mock.patch.object(export_voice.os, "open", side_effect=PermissionError(errno.EACCES, "negado")):
            self.assert_refused("não consegui ler")

    def test_open_loop_error_is_reported_as_a_link(self):
        with mock.patch.object(export_voice.os, "open", side_effect=OSError(errno.ELOOP, "laço")):
            self.assert_refused("é um link")

    def test_fstat_failure_is_reported_as_unreadable_and_closes_the_fd(self):
        closed = []
        real_close = os.close
        with (
            mock.patch.object(export_voice.os, "fstat", side_effect=OSError(errno.EIO, "io")),
            mock.patch.object(export_voice.os, "close", side_effect=lambda fd: (closed.append(fd), real_close(fd))),
        ):
            self.assert_refused("não consegui ler")
        self.assertEqual(1, len(closed))

    def test_a_file_that_grows_past_the_limit_after_the_stat_is_refused_while_reading(self):
        self.sidecar.write_bytes(b" " * (export_voice.SIDECAR_MAX_BYTES + 10))
        os.utime(self.sidecar, ns=(2_000_000_000_000, 2_000_000_000_000))
        with mock.patch.object(export_voice, "_info_problem", return_value=None):  # tamanho do `stat` já velho
            self.assert_refused("passa de 1 MiB")

    def test_a_file_swapped_for_a_hardlink_between_the_lstat_and_the_open_is_refused(self):
        real_fstat = os.fstat

        def hardlinked(fd):
            info = real_fstat(fd)
            return os.stat_result((info.st_mode, info.st_ino, info.st_dev, 2, *tuple(info)[4:]))

        with mock.patch.object(export_voice.os, "fstat", hardlinked):
            self.assert_refused("é um link")

    def test_voice_file_gone_after_the_sidecar_read_is_reported_as_unreadable(self):
        self.voice.unlink()
        self.assert_refused("não consegui ler")


class AnalysisFailureTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-voice-analysis-edges-")).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        (self.project / "aroll").mkdir()
        self.voice = self.project / "aroll" / "c03.mov"
        self.voice.write_bytes(b"voz da cena tres")
        self.media_id = analysis.ensure_media(self.project, "aroll/c03.mov", probe=False)["media_id"]

    def transcript(self, word_list, status="done"):
        doc = {"status": status, "reason": None, "language": "pt", "text": "x", "word_count": len(word_list)}
        analysis.write_component(
            self.project, self.media_id, "transcript", {**doc, "words": word_list}, producer=PRODUCER
        )

    def run_words(self, window=(9.6, 5.2), voice=None):
        return export_voice.timed_words(voice or self.voice, 5.0, window, "c03", project=self.project)

    def test_voice_outside_the_project_is_estimated_silently(self):
        outside = Path(tempfile.mkdtemp(prefix="gb-voice-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        voice = outside / "c03.mov"
        voice.write_bytes(b"voz")
        self.transcript(analysis_words(("analise", 0.0, 1.0)))
        self.assertEqual((None, []), self.run_words(voice=voice))

    def test_corrupt_transcript_file_is_a_warning_with_its_code(self):
        self.transcript(analysis_words(("analise", 0.0, 1.0)))
        path = self.project / "analysis" / "media" / self.media_id / "transcript.json"
        path.write_text("{ isto não é json", encoding="utf-8")
        found, warnings = self.run_words()
        self.assertIsNone(found)
        self.assertEqual(1, len(warnings))
        self.assertRegex(warnings[0], r"^c03: transcrição de analysis/ inválida \(\w+\): legenda estimada$")
        self.assertNotIn("ilegível", warnings[0])

    def test_value_error_from_the_reader_is_a_warning_without_a_code(self):
        self.transcript(analysis_words(("analise", 0.0, 1.0)))
        with mock.patch.object(analysis, "read_component", side_effect=ValueError("id ruim")):
            found, warnings = self.run_words()
        self.assertIsNone(found)
        self.assertEqual(["c03: transcrição de analysis/ inválida (ilegível): legenda estimada"], warnings)

    def test_transcript_with_too_many_words_is_refused(self):
        self.transcript(analysis_words(("a", 0.0, 0.5), ("b", 0.5, 1.0), ("c", 1.0, 1.5)))
        with mock.patch.object(export_voice, "MAX_ANALYSIS_WORDS", 2):
            found, warnings = self.run_words()
        self.assertIsNone(found)
        self.assertEqual(["c03: transcrição de analysis/ com mais de 2 palavras"], warnings)

    def test_transcript_with_only_zero_length_words_is_a_silent_estimate(self):
        self.transcript(analysis_words(("a", 1.0, 1.0), ("b", 2.0, 2.0)))
        self.assertEqual((None, []), self.run_words())

    def test_words_past_the_scene_window_are_dropped_with_a_warning(self):
        self.transcript(analysis_words(("fica", 0.0, 0.5), ("sai", 2.0, 3.0)))
        found, warnings = self.run_words(window=(10.0, 1.0))
        self.assertEqual(words(("fica", 10.0, 10.5)), found)
        self.assertEqual(["c03: 1 palavra(s) da transcrição de analysis/ passam do fim da cena"], warnings)


if __name__ == "__main__":
    unittest.main()
