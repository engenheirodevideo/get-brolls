"""media.py: recusas e falhas de ffmpeg/ffprobe simuladas, sem depender do ffmpeg instalado."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
import _logs
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import media


class TempCase(unittest.TestCase):
    def setUp(self):
        # O ciclo de vida cobre o teste inteiro; a limpeza é feita via addCleanup.
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)


class RunFailureTests(unittest.TestCase):
    def test_an_oserror_from_the_tool_becomes_a_media_failure_and_is_logged(self):
        with (
            patch.object(media.effects, "run", side_effect=PermissionError("sem permissão")),
            self.assertLogs("getbrolls.media", level="WARNING") as cm,
            self.assertRaisesRegex(ValueError, "Falha de mídia") as raised,
        ):
            media.run(["ffprobe", "-version"], op="probe")
        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        event = _logs.events(cm.output, "subprocess")[0]
        self.assertEqual(
            ("ffprobe", "probe", "error", None), (event["tool"], event["op"], event["status"], event["exit"])
        )


class DrawtextCacheEdgeTests(TempCase):
    def setUp(self):
        super().setUp()
        media._DRAWTEXT.clear()
        self.addCleanup(media._DRAWTEXT.clear)
        env = patch.dict(os.environ, {"GB_CACHE_DIR": str(self.root / "cache")})
        env.start()
        self.addCleanup(env.stop)
        self.ffmpeg = self.root / "ffmpeg-bin"
        self.ffmpeg.write_bytes(b"x")

    def test_a_corrupt_disk_cache_is_ignored_and_replaced_by_a_fresh_probe(self):
        cache = media._drawtext_cache_path(str(self.ffmpeg))
        cache.parent.mkdir(parents=True)
        cache.write_text("{ nao e json", encoding="utf-8")
        with (
            patch.object(media, "tool_path", return_value=str(self.ffmpeg)),
            patch.object(media, "run", return_value=" ... drawtext ... ") as run_mock,
        ):
            self.assertTrue(media.drawtext_available())
        run_mock.assert_called_once()
        self.assertTrue(json.loads(cache.read_text(encoding="utf-8"))["available"])

    def test_a_cache_that_cannot_be_written_still_returns_the_probe_answer(self):
        with (
            patch.object(media, "tool_path", return_value=str(self.ffmpeg)),
            patch.object(media, "run", return_value=" ... scale ... "),
            patch.object(media.effects, "mkdir", side_effect=PermissionError("somente leitura")),
        ):
            self.assertFalse(media.drawtext_available())
        self.assertFalse(media._drawtext_cache_path(str(self.ffmpeg)).exists())
        self.assertFalse(media._DRAWTEXT[str(self.ffmpeg)])

    def test_an_ffmpeg_that_cannot_be_inspected_still_gets_a_cache_key(self):
        missing = self.root / "nao-existe"
        path = media._drawtext_cache_path(str(missing))
        self.assertTrue(path.name.startswith("drawtext-"))
        self.assertEqual(path, media._drawtext_cache_path(str(missing)))
        self.assertNotEqual(path, media._drawtext_cache_path(str(self.ffmpeg)))


class FindFontTests(TempCase):
    def test_a_pinned_font_file_is_returned_resolved(self):
        font = self.root / "fonte.ttf"
        font.write_bytes(b"ttf")
        with (
            patch.dict(os.environ, {"GB_FONT_FILE": str(font)}),
            self.assertLogs("getbrolls.media", level="DEBUG") as cm,
        ):
            found = media.find_font()
        self.assertEqual(str(font.resolve()), found)
        event = _logs.events(cm.output, "font")[0]
        self.assertEqual(("True", "env"), (event["found"], event["source"]))

    def test_a_pinned_font_that_does_not_exist_is_refused(self):
        with (
            patch.dict(os.environ, {"GB_FONT_FILE": str(self.root / "sumiu.ttf")}),
            self.assertRaisesRegex(ValueError, "GB_FONT_FILE"),
        ):
            media.find_font()

    def test_the_first_existing_system_font_is_used_when_nothing_is_pinned(self):
        font = self.root / "sistema.ttf"
        font.write_bytes(b"ttf")
        with (
            patch.dict(os.environ, {"GB_FONT_FILE": ""}),
            patch.object(media, "DEFAULT_FONTS", (str(self.root / "falta.ttf"), str(font))),
        ):
            self.assertEqual(str(font.resolve()), media.find_font())


class ProbeAndCutRefusalTests(TempCase):
    def test_probe_refuses_a_file_without_a_video_stream(self):
        audio_only = json.dumps({"streams": [{"codec_type": "audio"}], "format": {"duration": "3"}})
        with patch.object(media, "run", return_value=audio_only), self.assertRaisesRegex(ValueError, "sem vídeo"):
            media.probe(self.root / "a.m4a")

    def test_cut_refuses_to_overwrite_an_existing_file(self):
        dst = self.root / "final.mp4"
        dst.write_bytes(b"original")
        with patch.object(media, "run") as run_mock, self.assertRaisesRegex(ValueError, "já existe"):
            media.cut(self.root / "src.mp4", dst, 1, 3)
        run_mock.assert_not_called()
        self.assertEqual(b"original", dst.read_bytes())

    def test_cut_refuses_a_duration_that_does_not_match_the_interval_and_leaves_nothing(self):
        dst = self.root / "final.mp4"
        probed = {"duration_s": 9.0, "width": 160, "height": 90, "fps": 10.0}
        with (
            patch.object(media, "run") as run_mock,
            patch.object(media, "probe", return_value=probed),
            self.assertRaisesRegex(ValueError, "Duração do corte"),
        ):
            media.cut(self.root / "src.mp4", dst, 1, 3)
        self.assertEqual(["cut"], [call.kwargs["op"] for call in run_mock.call_args_list])
        self.assertEqual([], list(self.root.iterdir()))

    def test_preview_fails_when_ffmpeg_wrote_no_image(self):
        dst = self.root / "prev.jpg"
        with patch.object(media, "run"), self.assertRaisesRegex(ValueError, "prévia"):
            media.preview(self.root / "src.mp4", dst, 0, 2)
        self.assertFalse(dst.exists())


class ScanSheetRefusalTests(TempCase):
    def test_scan_refuses_a_span_of_zero(self):
        with patch.object(media, "run") as run_mock, self.assertRaisesRegex(ValueError, "duração conhecida"):
            media.scan_sheet(self.root / "src.mp4", self.root / "previews", "x", 0, 0)
        run_mock.assert_not_called()

    def test_scan_fails_when_ffmpeg_wrote_no_sheet_and_leaves_no_file(self):
        directory = self.root / "previews"
        with patch.object(media, "run"), self.assertRaisesRegex(ValueError, "varrer"):
            media.scan_sheet(self.root / "src.mp4", directory, "x", 0, 30)
        self.assertEqual([], list(directory.iterdir()))


class SniffAndCopyImageTests(TempCase):
    def test_sniff_suffix_gives_the_default_when_the_file_cannot_be_read(self):
        self.assertEqual(".bin", media.sniff_suffix(self.root / "nao-existe", ".bin"))

    def test_sniff_suffix_recognises_webp_by_content(self):
        path = self.root / "arquivo-sem-extensao"
        path.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
        self.assertEqual(".webp", media.sniff_suffix(path, ".bin"))

    def test_copy_image_refuses_to_overwrite_an_existing_file(self):
        src = self.root / "src.png"
        src.write_bytes(b"nova")
        dst = self.root / "dst.png"
        dst.write_bytes(b"antiga")
        with self.assertRaisesRegex(ValueError, "já existe"):
            media.copy_image(src, dst)
        self.assertEqual(b"antiga", dst.read_bytes())

    def test_copy_image_refuses_a_copy_that_differs_from_the_original(self):
        src = self.root / "src.png"
        src.write_bytes(b"original")
        dst = self.root / "dst.png"

        def corrupting_copy(source, target):
            Path(target).write_bytes(Path(source).read_bytes() + b"?")

        with (
            patch.object(media.effects, "copy_file", corrupting_copy),
            patch.object(media, "probe"),
            patch.object(media, "run"),
            self.assertRaisesRegex(ValueError, "não confere"),
        ):
            media.copy_image(src, dst)
        self.assertFalse(dst.exists())
        self.assertEqual(b"original", src.read_bytes())


if __name__ == "__main__":
    unittest.main()
