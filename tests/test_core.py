import unittest, sys, tempfile, subprocess, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls.models import candidate, set_segment, approve, require_fetch
from getbrolls.media import probe, cut, preview


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


if __name__ == "__main__":
    unittest.main()
