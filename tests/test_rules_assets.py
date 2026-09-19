import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
import _paths  # noqa: F401  (efeito de import: insere scripts/ em sys.path; ROOT é de getbrolls.rules, não deste helper)
from _media import synth_image

from getbrolls.browser import plan
from getbrolls.http import _scrub
from getbrolls.ledger import Ledger
from getbrolls.memory import remember
from getbrolls.models import approve, candidate, set_segment
from getbrolls.rules import ROOT, allowed, format_report, load_rules

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
            p.write_text("```json\n" + json.dumps(r) + "\n```", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "responsabilidade"):
                load_rules(d)

    def test_browser_plan_and_memory(self):
        with tempfile.TemporaryDirectory() as d:
            led = Ledger(d)
            r = load_rules(d)
            result = plan(led, "https://www.nasa.gov/news/", r)
            self.assertEqual(result["viewport"]["width"], 390)
            self.assertFalse(result["viewport"]["emulates_device"])
            self.assertEqual(result["commands"][1][-3:], ["resize", "390", "844"])
            r["blocked_domains"] = ["nasa.gov"]
            with self.assertRaises(ValueError):
                plan(led, "https://www.nasa.gov/news/", r)
            c = candidate("local", "x", "x")
            set_segment(c, 0, 1)
            led.add(c)
            with self.assertRaises(ValueError):
                remember(led, c, "approved", "Good", "Human")
            approve(c, "Human")
            remember(led, c, "approved", "Good", "Human")
            remember(led, c, "rejected", "Bad fit elsewhere", "Human")
            self.assertEqual(
                len(json.loads((led.root / "references.json").read_text(encoding="utf-8"))["items"]),
                2,
            )

    def test_rule_changes_invalidate_and_block_import(self):
        from getbrolls.models import signature
        from getbrolls.review import import_review, project_id, review_epoch
        from getbrolls.rules import sync_formats

        with tempfile.TemporaryDirectory() as d:
            led = Ledger(d)
            r = load_rules(d)
            c = candidate("local", "x", "x", "https://example.org/a")
            set_segment(c, 0, 1)
            led.add(c)
            approve(c, "Human")
            led.save("fixture")
            data = {
                "type": "getbrolls-review",
                "templateVersion": 2,
                "project": project_id(led),
                "items": [
                    {"id": c["id"], "signature": signature(c), "reviewEpoch": review_epoch(c), "state": "approved"}
                ],
            }
            path = Path(d) / "review.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            r["blocked_domains"] = ["example.org"]
            with self.assertRaisesRegex(ValueError, "bloqueado"):
                import_review(led, path, "Human", r)
            r["video_format"] = "reels"
            # A mudança derruba uma aprovação humana: só passa com o sim explícito.
            sync_formats(led, r, confirm=True)
            self.assertEqual(c["approval"]["status"], "pending")
            self.assertEqual(c["format"]["target"], "reels")
            r["asset_types"] = ["web_screenshot"]
            r["blocked_domains"] = []
            self.assertEqual(plan(led, "https://www.nasa.gov/", r)["asset_type"], "web_screenshot")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_image_lifecycle_user_declaration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            image = root / "news.png"
            synth_image(image, color="blue", size="390x844")

            def call(*args, ok=True):
                run = subprocess.run(
                    [sys.executable, str(CLI), *map(str, args), "--project", d],
                    text=True,
                    capture_output=True,
                    encoding="utf-8",
                    check=False,
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
                "approve",
                *base,
                "--by",
                "Human",
                "--statement",
                "Aprovo esta imagem para o vídeo.",
            )
            call("permit", *base, "--declaration", ok=False)
            r = load_rules(d)
            r["copyright"] = {
                "mode": "user_declaration",
                "responsible_person": "Fixture User",
                "declaration": "Synthetic test image authored locally.",
            }
            (root / "RULES.md").write_text("```json\n" + json.dumps(r) + "\n```", encoding="utf-8")
            out = call("permit", *base, "--declaration")
            self.assertEqual(out["rights"]["basis"], "user_declaration")
            out = call("fetch", *base)
            self.assertTrue(out["output"]["path"].endswith(".png"))
            self.assertEqual(call("verify")["count"], 1)


if __name__ == "__main__":
    unittest.main()


class FormatChangeGateTests(unittest.TestCase):
    """#44: mudar o formato-alvo derruba aprovações — só depois de um sim explícito."""

    def _project_with_approved_item(self, folder):

        ledger = Ledger(folder)
        rules = load_rules(folder)
        c = candidate("local", "fixture", "Synthetic")
        c["media"].update(width=1920, height=1080)
        set_segment(c, 0, 1)
        c["format"] = format_report(c, rules)
        approve(c, "Human", "chat", "Aprovo este trecho para o vídeo.")
        ledger.add(c)
        ledger.save("approve", c)
        return ledger, rules

    def test_change_that_invalidates_approvals_aborts_and_lists_the_items(self):
        from getbrolls.rules import sync_formats

        with tempfile.TemporaryDirectory() as folder:
            ledger, rules = self._project_with_approved_item(folder)
            rules["video_format"] = "reels"
            before = ledger.path.read_bytes()
            with self.assertRaises(ValueError) as ctx:
                sync_formats(ledger, rules)
            self.assertIn("fixture", str(ctx.exception))
            self.assertIn("--confirm-format-change", str(ctx.exception))
            self.assertEqual(before, ledger.path.read_bytes())
            self.assertEqual("approved", ledger.data["items"][0]["approval"]["status"])

    def test_the_same_change_goes_through_once_confirmed(self):
        from getbrolls.rules import sync_formats

        with tempfile.TemporaryDirectory() as folder:
            ledger, rules = self._project_with_approved_item(folder)
            rules["video_format"] = "reels"
            sync_formats(ledger, rules, confirm=True)
            self.assertEqual("pending", ledger.data["items"][0]["approval"]["status"])
            self.assertEqual("reels", ledger.data["items"][0]["format"]["target"])

    def test_a_change_without_approvals_never_needs_the_flag(self):
        from getbrolls.rules import sync_formats

        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            rules = load_rules(folder)
            c = candidate("local", "fixture", "Synthetic")
            c["media"].update(width=1920, height=1080)
            set_segment(c, 0, 1)
            c["format"] = format_report(c, rules)
            ledger.add(c)
            rules["video_format"] = "reels"
            sync_formats(ledger, rules)
            self.assertEqual("reels", ledger.data["items"][0]["format"]["target"])

    def test_status_reads_a_project_with_a_pending_format_change_without_the_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            _ledger, rules = self._project_with_approved_item(folder)
            path = Path(folder) / "RULES.md"
            rules["video_format"] = "reels"
            path.write_text("```json\n" + json.dumps(rules) + "\n```", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(CLI), "status", "--project", folder],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(1, report["format_pending"])
            self.assertIn("serve", report)
            self.assertFalse(report["serve"]["running"])

    def test_read_only_consults_are_not_blocked_by_the_format_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            _ledger, rules = self._project_with_approved_item(folder)
            path = Path(folder) / "RULES.md"
            rules["video_format"] = "reels"
            path.write_text("```json\n" + json.dumps(rules) + "\n```", encoding="utf-8")
            for command in (["references"], ["inspect", "--url", "https://example.org/a"]):
                with self.subTest(command=command[0]):
                    proc = subprocess.run(  # noqa: PLW1510 - only stdout/stderr matter here, exit code is not asserted
                        [sys.executable, str(CLI), *command, "--project", folder],
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotIn("--confirm-format-change", proc.stdout + proc.stderr)
            # A aprovação segue de pé: a consulta não sincroniza formato nenhum.
            self.assertEqual("approved", Ledger(folder).data["items"][0]["approval"]["status"])
            # Um comando de escrita continua barrado até o sim explícito.
            blocked = subprocess.run(
                [sys.executable, str(CLI), "review", "--project", folder],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertNotEqual(0, blocked.returncode)
            self.assertIn("--confirm-format-change", blocked.stdout + blocked.stderr)
