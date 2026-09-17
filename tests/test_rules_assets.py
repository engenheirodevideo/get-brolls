import unittest, tempfile, json, sys, os, subprocess, shutil
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls.rules import load_rules, allowed, format_report, ROOT
from getbrolls.models import candidate, set_segment, approve
from getbrolls.ledger import Ledger
from getbrolls.memory import remember
from getbrolls.browser import plan
from getbrolls.http import _scrub

CLI = ROOT / "scripts/gb.py"


class RulesTests(unittest.TestCase):
    def test_nasa_upgrade_restricted(self):
        self.assertEqual(
            _scrub("http://images-assets.nasa.gov/a.mp4"),
            "https://images-assets.nasa.gov/a.mp4",
        )
        for url in (
            "http://evil.test/a",
            "http://images-assets.nasa.gov.evil.test/a",
            "http://x@images-assets.nasa.gov/a",
            "http://images-assets.nasa.gov/a?token=secret",
        ):
            self.assertIsNone(_scrub(url))

    def test_rules_domains_formats_declaration(self):
        with tempfile.TemporaryDirectory() as d:
            r = load_rules(d)
            r["blocked_domains"] = ["example.org"]
            c = candidate("local", "x", "x", "https://sub.example.org/a")
            self.assertFalse(allowed(c, r))
            c["source_url"] = "https://other.org"
            self.assertTrue(allowed(c, r))
            r["asset_types"] = ["image"]
            self.assertFalse(allowed(c, r))
            r["video_format"] = "reels"
            c["media"].update(width=1920, height=1080)
            self.assertEqual(format_report(c, r)["fit"], "needs_layout_review")
            r["copyright"]["mode"] = "user_declaration"
            p = Path(d) / "RULES.md"
            p.write_text(
                "```json\n" + json.dumps(r) + "\n```", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "responsabilidade"):
                load_rules(d)

    def test_browser_plan_and_memory(self):
        with tempfile.TemporaryDirectory() as d:
            l = Ledger(d)
            r = load_rules(d)
            result = plan(l, "https://www.nasa.gov/news/", r)
            self.assertEqual(result["viewport"]["width"], 390)
            self.assertFalse(result["viewport"]["emulates_device"])
            self.assertEqual(result["commands"][1][-3:], ["resize", "390", "844"])
            r["blocked_domains"] = ["nasa.gov"]
            with self.assertRaises(ValueError):
                plan(l, "https://www.nasa.gov/news/", r)
            c = candidate("local", "x", "x")
            set_segment(c, 0, 1)
            l.add(c)
            with self.assertRaises(ValueError):
                remember(l, c, "approved", "Good", "Human")
            approve(c, "Human")
            remember(l, c, "approved", "Good", "Human")
            remember(l, c, "rejected", "Bad fit elsewhere", "Human")
            self.assertEqual(
                len(
                    json.loads(
                        (l.root / "references.json").read_text(encoding="utf-8")
                    )["items"]
                ),
                2,
            )

    def test_rule_changes_invalidate_and_block_import(self):
        from getbrolls.rules import sync_formats
        from getbrolls.review import import_review, project_id, review_epoch
        from getbrolls.models import signature

        with tempfile.TemporaryDirectory() as d:
            l = Ledger(d)
            r = load_rules(d)
            c = candidate("local", "x", "x", "https://example.org/a")
            set_segment(c, 0, 1)
            l.add(c)
            approve(c, "Human")
            l.save("fixture")
            data = {
                "type": "getbrolls-review",
                "templateVersion": 2,
                "project": project_id(l),
                "items": [
                    {"id": c["id"], "signature": signature(c), "reviewEpoch": review_epoch(c), "state": "approved"}
                ],
            }
            path = Path(d) / "review.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            r["blocked_domains"] = ["example.org"]
            with self.assertRaisesRegex(ValueError, "bloqueado"):
                import_review(l, path, "Human", r)
            r["video_format"] = "reels"
            sync_formats(l, r)
            self.assertEqual(c["approval"]["status"], "pending")
            self.assertEqual(c["format"]["target"], "reels")
            r["asset_types"] = ["web_screenshot"]
            r["blocked_domains"] = []
            self.assertEqual(
                plan(l, "https://www.nasa.gov/", r)["asset_type"], "web_screenshot"
            )

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_image_lifecycle_user_declaration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            image = root / "news.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=390x844",
                    "-frames:v",
                    "1",
                    str(image),
                ],
                check=True,
            )

            def call(*args, ok=True):
                run = subprocess.run(
                    [sys.executable, str(CLI), *map(str, args), "--project", d],
                    text=True,
                    capture_output=True,
                    encoding="utf-8",
                )
                self.assertEqual(run.returncode, 0 if ok else 2, run.stderr)
                return json.loads(run.stdout if ok else run.stderr)

            call("init-rules")
            c = call(
                "resolve",
                "--file",
                image,
                "--asset-type",
                "news_screenshot",
                "--source-url",
                "https://www.nasa.gov/news/",
                "--title",
                "Synthetic news",
                "--shot",
                "news-one",
            )
            base = ["--candidate", c["id"]]
            self.assertEqual(c["media"]["kind"], "image")
            call("preview", *base)
            call(
                "approve", *base, "--by", "Human",
                "--statement", "Aprovo esta imagem para o vídeo.",
            )
            call("permit", *base, "--declaration", ok=False)
            r = load_rules(d)
            r["copyright"] = {
                "mode": "user_declaration",
                "responsible_person": "Fixture User",
                "declaration": "Synthetic test image authored locally.",
            }
            (root / "RULES.md").write_text(
                "```json\n" + json.dumps(r) + "\n```", encoding="utf-8"
            )
            out = call("permit", *base, "--declaration")
            self.assertEqual(out["rights"]["basis"], "user_declaration")
            out = call("fetch", *base)
            self.assertTrue(out["output"]["path"].endswith(".png"))
            self.assertEqual(call("verify")["count"], 1)


if __name__ == "__main__":
    unittest.main()
