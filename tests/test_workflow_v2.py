import unittest, tempfile, os, sys, json, shutil, subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls.config import load_env, settings
from getbrolls.models import candidate, set_segment, signature, require_fetch
from getbrolls.ledger import Ledger
from getbrolls.review import import_review, project_id
from getbrolls.media import review_preview, probe
import package_release


class WorkflowTests(unittest.TestCase):
    def test_env_precedence_and_literal_values(self):
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(os.environ, {"GB_GIF_FPS": "6"}, clear=True),
        ):
            p = Path(d) / ".env"
            p.write_text(
                'GB_GIF_FPS=8\nGB_PREVIEW_MODE=static\nPEXELS_API_KEY="$(echo DO_NOT_RUN)"\n'
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
            p.write_text("UNKNOWN=secret")
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
                    {"id": c["id"], "signature": signature(c), "state": "approved"}
                    for c in ledger.data["items"]
                ],
            }
            path = Path(d) / "review.json"
            payload["items"][1]["signature"] = "stale"
            path.write_text(json.dumps(payload))
            before = ledger.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "desatualizada"):
                import_review(ledger, path, "Human")
            self.assertEqual(before, ledger.path.read_bytes())
            self.assertEqual(ledger.get("local:0")["approval"]["status"], "pending")
            payload["items"][1]["signature"] = signature(ledger.get("local:1"))
            path.write_text(json.dumps(payload))
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

    def test_release_contains_skill_only(self):
        selected = package_release.files()
        files = [str(p.relative_to(package_release.ROOT)) for p in selected]
        self.assertIn(".env.example", files)
        self.assertIn("LICENSE", files)
        self.assertIn("GUIDE.md", files)
        self.assertIn("QUALITY.md", files)
        self.assertIn("assets/review-v2.js", files)
        self.assertFalse(any(path.startswith("references/") for path in files))
        internal_label = "auto" + "edit"
        self.assertFalse(
            any(internal_label in path.lower() for path in files),
            "A entrega pública não deve expor nomes internos em caminhos.",
        )
        for path in selected:
            if path.suffix in {".md", ".py", ".sh", ".yaml", ".yml"}:
                self.assertNotIn(
                    internal_label,
                    path.read_text(errors="ignore").lower(),
                    f"Nome interno encontrado em {path.relative_to(package_release.ROOT)}",
                )
        self.assertFalse(
            any(
                x.endswith((".gif", ".mp4", ".jpg"))
                or (x.endswith(".html") and x != "assets/storyboard-template.html")
                or x == ".env"
                or "storyboard-case" in x
                or "landing" in x
                for x in files
            )
        )

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
