import unittest, tempfile, os, sys, json, shutil, subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls.config import load_env, settings
from getbrolls.models import candidate, set_segment, signature, require_fetch
from getbrolls.ledger import Ledger
from getbrolls.review import import_review, project_id, review_epoch
from getbrolls.media import review_preview, probe


class WorkflowTests(unittest.TestCase):
    def test_env_precedence_and_literal_values(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(os.environ, {"GB_GIF_FPS": "6"}, clear=True),
        ):
            p = Path(d) / ".env"
            p.write_text(
                'GB_GIF_FPS=8\nGB_PREVIEW_MODE=static\nPEXELS_API_KEY="$(echo DO_NOT_RUN)"\n',
                encoding="utf-8",
            )
            load_env(p)
            self.assertEqual(settings()["fps"], 6)
            self.assertEqual(settings()["mode"], "static")
            self.assertEqual(os.environ["PEXELS_API_KEY"], "$(echo DO_NOT_RUN)")
            os.environ["GB_GIF_FPS"] = "nan"
            with self.assertRaises(ValueError):
                settings()

    def test_env_unknown_and_bounds(self):
        with patch.dict(os.environ, {"GB_GIF_WIDTH": "0"}, clear=True):
            with self.assertRaises(ValueError):
                settings()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("UNKNOWN=secret", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "variável desconhecida"):
                load_env(p)

    def test_review_atomic_validation_stale_and_rights(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(d)
            for i in range(2):
                c = candidate("local", str(i), "Synthetic")
                set_segment(c, 0, 1)
                ledger.add(c)
            ledger.save("fixture")
            pid = project_id(ledger)
            payload = {
                "type": "getbrolls-review",
                "templateVersion": 2,
                "project": pid,
                "items": [
                    {"id": c["id"], "signature": signature(c), "reviewEpoch": review_epoch(c), "state": "approved"}
                    for c in ledger.data["items"]
                ],
            }
            path = Path(d) / "review.json"
            payload["items"][1]["signature"] = "stale"
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = ledger.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "desatualizada"):
                import_review(ledger, path, "Human")
            self.assertEqual(before, ledger.path.read_bytes())
            self.assertEqual(ledger.get("local:0")["approval"]["status"], "pending")
            payload["items"][1]["signature"] = signature(ledger.get("local:1"))
            path.write_text(json.dumps(payload), encoding="utf-8")
            import_review(ledger, path, "Human")
            self.assertEqual(ledger.get("local:0")["approval"]["status"], "approved")
            with self.assertRaisesRegex(ValueError, "autorização"):
                require_fetch(ledger.get("local:0"))
            set_segment(ledger.get("local:0"), 0, 2)
            with self.assertRaises(ValueError):
                import_review(ledger, path, "Human")

    def test_context_and_shot_bind_approval(self):
        c = candidate("local", "abc", "Source")
        set_segment(c, 0, 1)
        original = signature(c)
        c["narration"] = "Different spoken line"
        self.assertNotEqual(signature(c), original)
        original = signature(c)
        c["match"]["reason"] = "New collection reason"
        self.assertNotEqual(signature(c), original)
        original = signature(c)
        c["id"] += ":shot:two"
        self.assertNotEqual(signature(c), original)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required"
    )
    def test_gif_static_duration_aspect_fallback(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True),
        ):
            root = Path(d)
            (root / "previews").mkdir()
            src = root / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=240x426:duration=2:rate=24",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            cfg = settings()
            cfg["frames"] = 5
            result = review_preview(src, root / "previews", "gif", 0, 2, cfg)
            info = probe(root / result["gif_path"])
            self.assertEqual(info["width"], 240)
            self.assertEqual(info["height"], 426)
            self.assertAlmostEqual(info["duration_s"], 2, delta=0.15)
            cfg["mode"] = "static"
            result = review_preview(src, root / "previews", "static", 0, 2, cfg)
            self.assertIsNone(result["gif_path"])
            self.assertTrue((root / result["contact_sheet_path"]).exists())
            cfg["mode"] = "gif"
            cfg["max_mb"] = 0.000001
            result = review_preview(src, root / "previews", "large", 0, 2, cfg)
            self.assertIsNone(result["gif_path"])
            self.assertIn("excedeu", result["warning"])
            cfg["max_seconds"] = 1
            with self.assertRaises(ValueError):
                review_preview(src, root / "previews", "long", 0, 2, cfg)


if __name__ == "__main__":
    unittest.main()


class ContactSheetTests(unittest.TestCase):
    """The CLI contact sheet mirrors gb_contact.sh: padded grid, cell times, labels when possible."""

    def make_source(self, root, seconds=4):
        src = root / "source.mp4"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                f"testsrc2=size=240x426:duration={seconds}:rate=24",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
            ],
            check=True,
        )
        return src

    def test_frame_times_cover_the_whole_interval(self):
        from getbrolls.media import frame_times

        self.assertEqual(frame_times(7, 12, 5), [7.0, 8.0, 9.0, 10.0, 11.0])
        self.assertEqual(frame_times(0, 2, 4), [0.0, 0.5, 1.0, 1.5])

    def test_sheet_without_drawtext_is_padded_and_reports_times(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "getbrolls.media.drawtext_available", return_value=False
        ):
            root = Path(d)
            (root / "previews").mkdir()
            src = self.make_source(root)
            cfg = settings()
            cfg["mode"] = "static"
            cfg["frames"] = 6
            result = review_preview(
                src, root / "previews", "plain", 1, 3, cfg, label={"title": "T", "id": "x"}
            )
            self.assertFalse(result["sheet_labels"])
            self.assertEqual(result["sheet_grid"], [4, 2])
            self.assertEqual(len(result["frame_times_s"]), 6)
            self.assertAlmostEqual(result["frame_times_s"][0], 1.0)
            self.assertAlmostEqual(result["frame_times_s"][-1], 1.0 + 5 * (2 / 6), places=3)
            info = probe(root / result["contact_sheet_path"])
            # 4 columns of 480 px, 10 px padding between cells, 10 px margin each side.
            self.assertEqual(info["width"], 4 * 480 + 3 * 10 + 2 * 10)

    def test_sheet_with_drawtext_adds_index_and_banner(self):
        from getbrolls import media

        calls = []

        def fake_run(args):
            calls.append(args)
            out = Path(args[-1])
            out.write_bytes(b"")
            return ""

        with tempfile.TemporaryDirectory() as d, patch.object(
            media, "drawtext_available", return_value=True
        ), patch.object(media, "find_font", return_value="/fonts/Arial.ttf"), patch.object(
            media, "run", side_effect=fake_run
        ):
            root = Path(d)
            (root / "previews").mkdir()
            cfg = settings()
            cfg["mode"] = "static"
            cfg["frames"] = 3
            result = media.review_preview(
                root / "in.mp4", root / "previews", "lab", 2, 5, cfg,
                label={"title": "Foguete", "id": "youtube:abc"},
            )
            self.assertTrue(result["sheet_labels"])
            sheet_filter = [a for a in calls if "tile=3x1" in " ".join(a)][0]
            vf = sheet_filter[sheet_filter.index("-vf") + 1]
            self.assertIn("drawtext=fontfile='/fonts/Arial.ttf'", vf)
            self.assertIn("eif", vf)
            self.assertIn("padding=10:margin=10", vf)
            self.assertIn("pad=iw:ih+72", vf)
            self.assertIn("textfile=", vf)

    def test_labels_and_times_use_source_time_when_working_file_is_offset(self):
        from getbrolls import media

        calls = []

        def fake_run(args):
            calls.append(args)
            vf = args[args.index("-vf") + 1] if "-vf" in args else ""
            if "textfile=" in vf:
                # Capture the banner before the staging directory disappears.
                path = vf.split("textfile='")[1].split("'")[0].replace("\\:", ":")
                self.banner_text = Path(path).read_text(encoding="utf-8")
            Path(args[-1]).write_bytes(b"")
            return ""

        with tempfile.TemporaryDirectory() as d, patch.object(
            media, "drawtext_available", return_value=True
        ), patch.object(media, "find_font", return_value="/fonts/Arial.ttf"), patch.object(
            media, "run", side_effect=fake_run
        ):
            root = Path(d)
            (root / "previews").mkdir()
            cfg = settings()
            cfg["mode"] = "static"
            cfg["frames"] = 4
            # yt-dlp downloaded 59–65 s into a file that starts at 0.
            result = media.review_preview(
                root / "in.mp4", root / "previews", "off", 0, 6, cfg,
                label={"title": "Liftoff", "id": "youtube:x", "offset": 59, "duration": 122.4},
            )
            self.assertEqual(result["frame_times_s"], [59.0, 60.5, 62.0, 63.5])
            banner = calls[-1][calls[-1].index("-vf") + 1]
            self.assertIn("textfile=", banner)
            self.assertIn("corte 0:59.0–1:05.0 de 2:02.4", self.banner_text)
            # ffmpeg still cuts the working file at 0–6.
            self.assertEqual(calls[-1][calls[-1].index("-ss") + 1], "0")

    def test_font_pin_must_exist(self):
        from getbrolls.media import find_font

        with patch.dict(os.environ, {"GB_FONT_FILE": "/nao/existe.ttf"}):
            with self.assertRaises(ValueError):
                find_font()
