import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "scripts/gb.py"


def _review_payload(page):
    match = re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
    assert match, "review.html sem o payload embutido"
    return match.group(1)


class CliTest(unittest.TestCase):
    def call(self, *args, ok=True):
        p = subprocess.run(
            [sys.executable, str(CLI), *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(p.returncode, 0 if ok else 2, p.stderr)
        return json.loads(p.stdout if ok else p.stderr)

    def test_cli_forces_utf8_when_parent_requests_cp1252(self):
        environment = {**os.environ, "PYTHONIOENCODING": "cp1252"}
        result = subprocess.run(
            [sys.executable, str(CLI), "doctor"],
            capture_output=True,
            env=environment,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        output = result.stdout.decode("utf-8")
        self.assertIn("→", output)
        self.assertNotIn("�", output)
        self.assertEqual("broll", json.loads(output)["preview"]["scope"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_complete_local_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "original.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=640x360:duration=3:rate=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            c = self.call("resolve", "--file", src, "--project", root)
            id = c["id"]
            base = ["--candidate", id, "--project", root]
            # Cada comando do fluxo também diz, em uma linha, o que acabou de fazer.
            self.assertIn("Registrei o candidato", c["summary"])
            self.call("fetch", *base, ok=False)
            preview = self.call("preview", *base, "--start", 0.5, "--end", 1.5)
            self.assertIn("Gerei a prévia", preview["summary"])
            approved = self.call(
                "approve",
                *base,
                "--start",
                0.5,
                "--end",
                1.5,
                "--by",
                "Fixture humano",
                "--statement",
                "Aprovo este trecho para o vídeo.",
            )
            self.assertIn("Registrei a aprovação humana", approved["summary"])
            self.call("fetch", *base, ok=False)
            permitted = self.call(
                "permit",
                *base,
                "--evidence",
                "Vídeo sintético de teste gerado localmente",
            )
            self.assertIn("condições de uso", permitted["summary"])
            out = self.call("fetch", *base)
            self.assertTrue(out["output"]["verified"])
            self.assertIn("Coletei o corte final", out["summary"])
            verified = self.call("verify", "--project", root)
            self.assertEqual(verified["count"], 1)
            self.assertIn("1 arquivo coletado: íntegro e decodificável", verified["summary"])
            reviewed = self.call("review", "--project", root)
            self.assertIn("Gerei o Storyboard", reviewed["summary"])
            state = self.call("status", "--project", root)
            self.assertEqual(1, state["counts"]["verified"])
            self.assertIn("completo", state["summary"]["next"])
            self.call("fetch", *base, ok=False)
            self.assertTrue((root / "brolls/review.html").exists())
            self.assertIn(
                "Vídeo sintético",
                (root / "brolls/credits.md").read_text(encoding="utf-8"),
            )
            self.call("reject", *base)
            rejected = self.call("preview", *base, "--start", 0.5, "--end", 1.5)
            self.assertEqual(rejected["approval"]["status"], "rejected")
            self.assertEqual(rejected["state"], "rejected")
            self.call("fetch", *base, ok=False)
            self.call("preview", *base, "--start", 0, "--end", 1)
            self.call("fetch", *base, ok=False)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_shots_context_and_import_cli_sync(self):

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "original.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=160x90:duration=2:rate=10",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                check=True,
            )
            c = self.call(
                "resolve",
                "--file",
                src,
                "--shot",
                "one",
                "--source-url",
                "https://example.org/original",
                "--creator",
                "Test",
                "--project",
                root,
            )
            other = self.call("resolve", "--file", src, "--shot", "two", "--project", root)
            self.assertNotEqual(c["id"], other["id"])
            base = ["--candidate", c["id"], "--project", root]
            self.call("preview", *base, "--start", 0, "--end", 1, "--narration", "Line one")
            self.call(
                "approve",
                *base,
                "--start",
                0,
                "--end",
                1,
                "--by",
                "Human",
                "--statement",
                "Aprovo este trecho para o vídeo.",
            )

            def payload():
                page = (root / "brolls/review.html").read_text(encoding="utf-8")
                return json.loads(_review_payload(page))

            data = payload()
            self.assertEqual(data["items"][0]["review"]["state"], "approved")
            data["items"] = [{**data["items"][0], "state": "approved"}]
            review = root / "decision.json"
            review.write_text(json.dumps(data), encoding="utf-8")
            self.call("import-review", "--file", review, "--by", "Human", "--project", root)
            self.call("reject", *base)
            self.assertEqual(payload()["items"][0]["review"]["state"], "pending")
            self.call(
                "approve",
                *base,
                "--start",
                0,
                "--end",
                1,
                "--by",
                "Human",
                "--statement",
                "Aprovo este trecho para o vídeo.",
            )
            self.call("preview", *base, "--start", 0, "--end", 1, "--narration", "Line two")
            self.call(
                "import-review",
                "--file",
                review,
                "--by",
                "Human",
                "--project",
                root,
                ok=False,
            )
            self.assertEqual(payload()["items"][0]["review"]["state"], "pending")

    def test_social_resolve_keeps_acquisition_and_url_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self.call(
                "resolve",
                "--url",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "--project",
                tmp,
            )
            self.assertEqual(c["acquisition"]["method"], "yt-dlp")
            self.call(
                "resolve",
                "--url",
                "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
                "--project",
                tmp,
                ok=False,
            )

    def test_resolve_rejects_an_empty_file_or_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            for flag in ("--file", "--url"):
                failure = self.call("resolve", flag, "", "--project", tmp, ok=False)
                self.assertIn(flag, failure["message"])
                self.assertIn("não pode ser vazio", failure["message"])

    def test_youtube_review_inline_segment_safe(self):
        from html.parser import HTMLParser

        class DOM(HTMLParser):
            def __init__(self):
                super().__init__()
                self.nodes = []

            def handle_starttag(self, tag, attrs):
                self.nodes.append((tag, dict(attrs)))

        with tempfile.TemporaryDirectory() as tmp:
            c = self.call(
                "resolve",
                "--url",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "--project",
                tmp,
            )
            self.call(
                "preview",
                "--reference-only",
                "--candidate",
                c["id"],
                "--start",
                2.4,
                "--end",
                8.6,
                "--project",
                tmp,
            )
            manifest = Path(tmp) / "brolls/manifest.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["items"][0]["title"] = "<img src=x onerror=alert(1)>"
            data["items"][0]["preview"]["poster_url"] = "https://example.org/poster.jpg?access_token=SECRET_TEST"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            self.call("review", "--project", tmp)
            page = (Path(tmp) / "brolls/review.html").read_text(encoding="utf-8")
            dom = DOM()
            dom.feed(page)
            self.assertFalse(any(t == "iframe" for t, a in dom.nodes), "Player must be lazy")
            self.assertFalse(any(t in ("iframe", "video") or "data-youtube" in a for t, a in dom.nodes))
            self.assertNotIn("SECRET_TEST", page)
            self.assertFalse(any("onerror" in a for t, a in dom.nodes))
            self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
            self.assertIn("Abrir fonte original", page)
            self.assertIn("Salvar decisões", page)
            self.assertNotIn("Exportar revisão", page)


if __name__ == "__main__":
    unittest.main()
