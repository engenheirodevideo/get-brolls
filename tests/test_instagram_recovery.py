from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import socket
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from getbrolls import instagram_pairs as ig


class InstagramRecoveryTests(unittest.TestCase):
    def test_rejects_unsafe_urls_and_config_output_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, url in enumerate(
                (
                    "file:///etc/passwd",
                    "http://127.0.0.1/media",
                    "https://user:secret@example.org/media",
                    "https://10.0.0.2/media",
                    "https://[::1]/media",
                    "https://localhost/media",
                    "https://camera.local/media",
                )
            ):
                with self.subTest(url=url):
                    conf = root / f"unsafe-{index}.conf"
                    conf.write_text(f'url = "{url}"\n', encoding="utf-8")
                    with self.assertRaises(ig.CollectError):
                        ig.parse_curl_config(conf)
            public_name = root / "public-name.conf"
            public_name.write_text('url = "https://media.example.test/video"\n', encoding="utf-8")
            private_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
            with patch.object(ig.socket, "getaddrinfo", return_value=private_dns), self.assertRaises(ig.CollectError):
                ig.parse_curl_config(public_name)
            mixed_dns = [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.2", 443)),
            ]
            with patch.object(ig.socket, "getaddrinfo", return_value=mixed_dns), self.assertRaises(ig.CollectError):
                ig.parse_curl_config(public_name)
            trailing_dot = root / "trailing-dot.conf"
            trailing_dot.write_text('url = "https://media.example.test./video"\n', encoding="utf-8")
            public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            with patch.object(ig.socket, "getaddrinfo", return_value=public_dns), self.assertRaises(ig.CollectError):
                ig.parse_curl_config(trailing_dot)
            for output in ("../../outside.mp4", "/etc/passwd"):
                with self.subTest(output=output), self.assertRaises(ig.CollectError):
                    ig.resolve_config_output(output, root)
            for stem in (".._01_clip", ".hidden", "bad\\name", "bad:name"):
                with self.subTest(stem=stem), self.assertRaises(ig.CollectError):
                    ig.infer_output_for_stem(stem, root / "out", "auto")

    def test_accepts_public_https_and_output_inside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inside = root / "work/source.mp4"
            conf = root / "safe.conf"
            conf.write_text(f'url = "https://example.org/media"\noutput = "{inside}"\n', encoding="utf-8")
            public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            with patch.object(ig.socket, "getaddrinfo", return_value=public_dns):
                parsed = ig.parse_curl_config(conf)
            self.assertEqual("https://example.org/media", parsed["url"])
            self.assertEqual("example.org:443:93.184.216.34", parsed["curl_resolve"])
            self.assertEqual(inside.resolve(), ig.resolve_config_output(str(inside), root))

    def test_failed_transfer_does_not_poison_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "video.conf"
            conf.write_text('url = "https://example.org/private-media"\n', encoding="utf-8")
            target = root / "part.mp4"

            def fail(cmd, **kwargs):
                Path(cmd[cmd.index("--output") + 1]).write_bytes(b"partial")
                raise subprocess.CalledProcessError(18, cmd)

            kwargs = dict(
                cfg_path=conf,
                part_path=target,
                config_output_root=root,
                force_download=False,
                prefer_config_output=False,
            )
            public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            with (
                patch.object(ig.socket, "getaddrinfo", return_value=public_dns),
                patch.object(ig.subprocess, "run", side_effect=fail) as called,
                self.assertRaises(ig.CollectError),
            ):
                ig.download_or_reuse(**kwargs)
            self.assertFalse(target.exists())
            command = called.call_args.args[0]
            self.assertIn("--resolve", command)
            self.assertNotIn("--location", command)

            def success(cmd, **kwargs):
                Path(cmd[cmd.index("--output") + 1]).write_bytes(b"complete")

            with (
                patch.object(ig.socket, "getaddrinfo", return_value=public_dns),
                patch.object(ig.subprocess, "run", side_effect=success),
            ):
                self.assertEqual(ig.download_or_reuse(**kwargs), "downloaded")
            self.assertEqual(target.read_bytes(), b"complete")

    def test_merge_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "existing.mp4"
            output.write_bytes(b"keep")
            with self.assertRaises(ig.CollectError):
                ig.process_one(
                    stem="01_SAFE",
                    video_config=root / "video.conf",
                    audio_config=root / "audio.conf",
                    output=output,
                    parts_dir=root / "parts",
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                    copy_streams=False,
                )
            self.assertEqual(b"keep", output.read_bytes())

    def test_exclusive_publication_refuses_racing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pending = root / "pending.mp4"
            output = root / "output.mp4"
            pending.write_bytes(b"new")
            output.write_bytes(b"keep")
            with self.assertRaises(ig.CollectError):
                ig.publish_exclusive(pending, output)
            self.assertEqual(b"keep", output.read_bytes())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
    def test_separate_video_audio_merge_and_duplicate_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configs = root / "configs"
            configs.mkdir()
            video = root / "video.mp4"
            audio = root / "audio.m4a"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=160x90:rate=10:duration=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(video),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1",
                    "-c:a",
                    "aac",
                    str(audio),
                ],
                check=True,
            )
            for stem in ("01_TEST", "02_TEST"):
                (configs / (stem + "_video.conf")).write_text(
                    f'url = "https://example.org/video"\noutput = "{video}"\n', encoding="utf-8"
                )
                (configs / (stem + "_audio.conf")).write_text(
                    f'url = "https://example.org/audio"\noutput = "{audio}"\n', encoding="utf-8"
                )
            args = [
                "--config-dir",
                str(configs),
                "--output-dir",
                str(root / "out"),
                "--parts-dir",
                str(root / "parts"),
                "--config-output-root",
                str(root),
                "--layout",
                "flat",
                "--fail-on-duplicate-audio",
                "--pace",
                "0",
            ]
            public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
            with patch.object(ig.socket, "getaddrinfo", return_value=public_dns):
                self.assertEqual(1, ig.main(args))
            report = ig.verify_output(root / "out/01_TEST.mp4")
            self.assertTrue(report["audio_hash_sha256"])
            self.assertEqual(ig.audio_hash(root / "out/01_TEST.mp4"), ig.audio_hash(root / "out/02_TEST.mp4"))
