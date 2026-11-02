import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import media
from getbrolls.media import cut, preview, probe
from getbrolls.models import approve, candidate, require_fetch, set_segment


class CoreTests(unittest.TestCase):
    def test_invalid_segment(self):
        for a, b in [(2, 1), (-1, 2), (0, float("nan"))]:
            with self.assertRaises(ValueError):
                set_segment(candidate("local", "x", "X"), a, b)

    def test_approval_invalidated(self):
        c = candidate("local", "x", "X")
        set_segment(c, 0, 1)
        approve(c, "human")
        set_segment(c, 0, 2)
        self.assertEqual(c["approval"]["status"], "pending")

    def test_fetch_requires_rights_and_approval(self):
        c = candidate("local", "x", "X")
        set_segment(c, 0, 1)
        with self.assertRaises(ValueError):
            require_fetch(c)
        approve(c, "human")
        with self.assertRaises(ValueError):
            require_fetch(c)

    def test_source_change_invalidates(self):
        c = candidate("local", "x", "X")
        set_segment(c, 0, 1)
        approve(c, "human")
        c["source_url"] = "changed"
        with self.assertRaises(ValueError):
            require_fetch(c)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_real_media(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src = p / "source.mp4"
            out = p / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=640x360:rate=10:duration=3",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            cut(src, out, 0.5, 1.5)
            self.assertAlmostEqual(probe(out)["duration_s"], 1, delta=0.15)
            preview(src, p / "preview.jpg", 0, 2)
            self.assertTrue((p / "preview.jpg").stat().st_size > 0)
            bad = p / "bad.mp4"
            bad.write_text("bad", encoding="utf-8")
            with self.assertRaises(ValueError):
                probe(bad)


class CutSeekTests(unittest.TestCase):
    """`cut` busca na entrada (`-ss` antes do `-i`): o ffmpeg não decodifica a fonte desde o início."""

    def test_cut_seeks_before_reading_the_input(self):
        seen = {}

        def fake_run(args, *, op=None):
            if op == "cut":
                seen["argv"] = list(args)
                Path(args[-1]).write_bytes(b"x")
                return ""
            if op == "probe":
                streams = [{"codec_type": "video", "width": 64, "height": 64, "avg_frame_rate": "10/1"}]
                return json.dumps({"streams": streams, "format": {"duration": "1.0"}})
            return ""

        with tempfile.TemporaryDirectory() as d, patch.object(media, "run", side_effect=fake_run):
            cut(Path(d) / "source.mp4", Path(d) / "clip.mp4", 3600.0, 3601.0)
        argv = seen["argv"]
        self.assertLess(argv.index("-ss"), argv.index("-i"))
        self.assertEqual("3600.0", argv[argv.index("-ss") + 1])
        self.assertEqual("1.0", argv[argv.index("-t") + 1])

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_input_seek_still_lands_on_the_requested_frame(self):
        """Um keyframe só, no quadro 0: a busca na entrada continua exata com re-encode."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src, out = p / "ramp.mp4", p / "clip.mp4"
            # Brilho cresce com o tempo (T*40): o 1º quadro do corte diz onde a busca caiu.
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=64x64:r=10:d=6,format=yuv420p,geq=lum='T*40':cb=128:cr=128",
                    "-c:v",
                    "libx264",
                    "-g",
                    "100",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            cut(src, out, 4.0, 5.0)
            self.assertAlmostEqual(probe(out)["duration_s"], 1, delta=0.15)
            first = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                check=True,
                capture_output=True,
            ).stdout
        # t=4 s → luma 160; um corte que caísse no início da fonte ficaria perto de 0.
        self.assertGreater(sum(first) / len(first), 120)


if __name__ == "__main__":
    unittest.main()
