"""Ritmo de lote: instagram_pairs não martela a CDN, yt-dlp dorme entre pedidos, 429 respeita Retry-After."""

import contextlib
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import http, queue, social
from getbrolls import instagram_pairs as ig

PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def write_pairs(configs, stems):
    configs.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        (configs / f"{stem}_video.conf").write_text('url = "https://example.org/v"\n', encoding="utf-8")
        (configs / f"{stem}_audio.conf").write_text('url = "https://example.org/a"\n', encoding="utf-8")


def batch_args(root, *extra, project=True):
    args = [
        "--config-dir",
        str(root / "configs"),
        "--output-dir",
        str(root / "out"),
        "--parts-dir",
        str(root / "parts"),
        "--config-output-root",
        str(root),
        "--layout",
        "flat",
        "--summary-json",
        str(root / "work/summary.json"),
    ]
    if project:
        args += ["--project", str(root)]
    return args + list(extra)


def fake_process(**kwargs):
    return {"output": str(kwargs["output"]), "stem": kwargs["stem"], "audio_hash_sha256": kwargs["stem"]}


class PairsBatchTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(ig, "ensure_tool"))
        self.sleep = self.enterContext(patch.object(ig.time, "sleep"))
        # O script fala no terminal; a suíte não precisa desse ruído.
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def test_pace_parsing(self):
        self.assertEqual((20, 60), ig.parse_pace("20-60"))
        self.assertEqual((0, 0), ig.parse_pace("0"))
        self.assertEqual((5, 5), ig.parse_pace("5"))
        for value in ("a-b", "60-20", "-5", "1-2-3", ""):
            with self.subTest(value=value), self.assertRaises(ig.CollectError):
                ig.parse_pace(value)
        parser = ig.build_parser()
        args = parser.parse_args(["--config-dir", "x", "--output-dir", "y"])
        self.assertEqual("20-60", args.pace)
        self.assertEqual(25, args.max_per_run)
        self.assertFalse(args.continue_on_error)
        self.assertIsNone(args.project)

    def test_pace_sleeps_between_stems_but_not_before_the_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A", "02_B", "03_C"])
            with (
                patch.object(ig, "process_one", side_effect=fake_process),
                patch.object(ig.random, "uniform", return_value=7.5),
            ):
                self.assertEqual(0, ig.main(batch_args(root, "--pace", "5-10")))
            self.assertEqual([7.5, 7.5], [call.args[0] for call in self.sleep.call_args_list])
            summary = json.loads((root / "work/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(1, summary["schema_version"])
            self.assertEqual(
                {"done": 3, "failed": 0, "skipped": 0}, {k: summary[k] for k in ("done", "failed", "skipped")}
            )
            self.assertEqual(["done"] * 3, [item["status"] for item in summary["results"]])
            self.assertIsNone(summary["stopped_by"])
            self.sleep.reset_mock()
            with patch.object(ig, "process_one", side_effect=fake_process):
                ig.main(batch_args(root, "--pace", "0", "--output-dir", str(root / "out2")))
            self.sleep.assert_not_called()

    def test_max_per_run_skips_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A", "02_B", "03_C"])
            with patch.object(ig, "process_one", side_effect=fake_process) as processed:
                self.assertEqual(0, ig.main(batch_args(root, "--pace", "0", "--max-per-run", "2")))
            self.assertEqual(2, processed.call_count)
            summary = json.loads((root / "work/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(["done", "done", "skipped"], [item["status"] for item in summary["results"]])
            self.assertEqual("max-per-run", summary["results"][2]["reason"])
            self.assertEqual("max-per-run", summary["stopped_by"])

    def test_max_per_run_zero_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A"])
            with patch.object(ig, "process_one", side_effect=fake_process):
                self.assertEqual(1, ig.main(batch_args(root, "--pace", "0", "--max-per-run", "0")))

    def test_continue_on_error_records_redacted_failure_and_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A", "02_B", "03_C"])
            writes = []

            def flaky(**kwargs):
                writes.append((root / "work/summary.json").is_file())
                if kwargs["stem"] == "02_B":
                    ig.die("curl failed for https://cdn.example/secret?oh=1 config")
                return fake_process(**kwargs)

            with patch.object(ig, "process_one", side_effect=flaky):
                self.assertEqual(1, ig.main(batch_args(root, "--pace", "0", "--continue-on-error")))
            self.assertEqual([True, True, True], writes, "resumo já validado/gravado antes do primeiro stem")
            summary = json.loads((root / "work/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(["done", "failed", "done"], [item["status"] for item in summary["results"]])
            self.assertNotIn("cdn.example", json.dumps(summary))
            self.assertIn("URL omitida", summary["results"][1]["reason"])
            # Sem a flag, a falha interrompe o lote (exit 1) e os demais ficam registrados como pulados,
            # mas o resumo já persistido não desaparece: main() converte CollectError em código de saída.
            with patch.object(ig, "process_one", side_effect=flaky):
                self.assertEqual(1, ig.main(batch_args(root, "--pace", "0", "--output-dir", str(root / "out2"))))
            summary = json.loads((root / "work/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(["done", "failed", "skipped"], [item["status"] for item in summary["results"]])
            self.assertIsNone(summary["stopped_by"])

    def test_curl_flags_and_http_403_429_stop_the_batch_with_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A", "02_B", "03_C"])
            queue_path = queue.queue_path(root)
            state = queue.empty_state()
            queue.add(state, "instagram", ["https://www.instagram.com/reel/ABC123xyz/"])
            queue.save(queue_path, state)
            commands = []

            def forbidden(cmd, **kwargs):
                commands.append(cmd)
                raise subprocess.CalledProcessError(22, cmd, stderr=b"curl: (22) The requested URL returned error: 403")

            with (
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=forbidden),
            ):
                self.assertEqual(1, ig.main(batch_args(root, "--pace", "0", "--continue-on-error")))
            self.assertEqual(1, len(commands), "após 403/429 o lote para de imediato")
            self.assertIn("--retry", commands[0])
            self.assertEqual("2", commands[0][commands[0].index("--retry") + 1])
            self.assertEqual("5", commands[0][commands[0].index("--retry-delay") + 1])
            self.assertNotIn("--retry-all-errors", commands[0])
            summary = json.loads((root / "work/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(["failed", "skipped", "skipped"], [item["status"] for item in summary["results"]])
            self.assertEqual(["cooldown", "cooldown"], [item["reason"] for item in summary["results"][1:]])
            self.assertIn("403", summary["results"][0]["reason"])
            self.assertEqual("cooldown", summary["stopped_by"])
            saved = queue.load(queue_path)
            self.assertTrue(saved["providers"]["instagram"]["cooldown_until"])
            self.assertEqual(1, saved["providers"]["instagram"]["cooldown_strikes"])

    def test_403_without_continue_on_error_exits_1_and_persists_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A"])
            queue.save(queue.queue_path(root), queue.empty_state())

            def forbidden(cmd, **kwargs):
                raise subprocess.CalledProcessError(22, cmd, stderr=b"curl: (22) The requested URL returned error: 403")

            with (
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=forbidden),
            ):
                self.assertEqual(1, ig.main(batch_args(root, "--pace", "0")))
            summary = json.loads((root / "work/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(["failed"], [item["status"] for item in summary["results"]])
            self.assertEqual("cooldown", summary["stopped_by"])

    def test_cooldown_recorded_under_project_not_config_output_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A"])
            project = root / "the-project"
            project.mkdir()
            queue.save(queue.queue_path(project), queue.empty_state())
            # queue.json não existe sob config-output-root (root); só sob --project.

            def forbidden(cmd, **kwargs):
                raise subprocess.CalledProcessError(22, cmd, stderr=b"curl: (22) The requested URL returned error: 403")

            args = [
                "--config-dir",
                str(root / "configs"),
                "--output-dir",
                str(root / "out"),
                "--parts-dir",
                str(root / "parts"),
                "--config-output-root",
                str(root),
                "--project",
                str(project),
                "--layout",
                "flat",
                "--summary-json",
                str(root / "work/summary.json"),
                "--pace",
                "0",
            ]
            with (
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=forbidden),
            ):
                self.assertEqual(1, ig.main(args))
            saved = queue.load(queue.queue_path(project))
            self.assertTrue(saved["providers"]["instagram"]["cooldown_until"])
            self.assertFalse((root / "work" / "queue.json").is_file())

    def test_warning_printed_when_no_project_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A"])

            def forbidden(cmd, **kwargs):
                raise subprocess.CalledProcessError(22, cmd, stderr=b"curl: (22) The requested URL returned error: 403")

            stderr = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=forbidden),
            ):
                self.assertEqual(1, ig.main(batch_args(root, "--pace", "0", project=False)))
            self.assertIn("WARNING: cooldown não registrado", stderr.getvalue())

    def test_warning_printed_when_project_has_no_queue_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A"])

            def forbidden(cmd, **kwargs):
                raise subprocess.CalledProcessError(22, cmd, stderr=b"curl: (22) The requested URL returned error: 403")

            stderr = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=forbidden),
            ):
                self.assertEqual(1, ig.main(batch_args(root, "--pace", "0")))
            self.assertIn("WARNING: cooldown não registrado", stderr.getvalue())

    def test_other_curl_failures_do_not_trigger_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "video.conf"
            conf.write_text('url = "https://example.org/v"\n', encoding="utf-8")

            def timeout(cmd, **kwargs):
                raise subprocess.CalledProcessError(28, cmd, stderr=b"curl: (28) timeout")

            with (
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=timeout),
                self.assertRaises(ig.CollectError) as raised,
            ):
                ig.download_or_reuse(
                    cfg_path=conf,
                    part_path=root / "p.mp4",
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                )
            self.assertFalse(getattr(raised.exception, "cooldown", False))

    def test_unsafe_stem_is_rejected_before_any_path_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ig.CollectError):
                ig.process_one(
                    stem="../escape",
                    video_config=root / "v.conf",
                    audio_config=root / "a.conf",
                    output=root / "out.mp4",
                    parts_dir=root / "parts",
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                    copy_streams=False,
                )
            self.assertEqual([], list(root.iterdir()))

    def test_video_config_single_mode_exits_0_with_count_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "one_video.conf"
            audio = root / "one_audio.conf"
            video.write_text('url = "https://example.org/v"\n', encoding="utf-8")
            audio.write_text('url = "https://example.org/a"\n', encoding="utf-8")
            output = root / "out.mp4"
            with patch.object(ig, "process_one", side_effect=fake_process):
                code = ig.main(
                    [
                        "--video-config",
                        str(video),
                        "--audio-config",
                        str(audio),
                        "--output",
                        str(output),
                        "--parts-dir",
                        str(root / "parts"),
                        "--config-output-root",
                        str(root),
                        "--summary-json",
                        str(root / "work/summary.json"),
                        "--pace",
                        "0",
                    ]
                )
            self.assertEqual(0, code)
            summary = json.loads((root / "work/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(1, summary["count"])
            self.assertEqual(1, summary["done"])

    def test_fail_on_duplicate_audio_end_to_end_with_continue_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_pairs(root / "configs", ["01_A", "02_B"])

            def duplicated(**kwargs):
                return {"output": str(kwargs["output"]), "stem": kwargs["stem"], "audio_hash_sha256": "same-hash"}

            with patch.object(ig, "process_one", side_effect=duplicated):
                code = ig.main(batch_args(root, "--pace", "0", "--continue-on-error", "--fail-on-duplicate-audio"))
            self.assertEqual(1, code)
            # O resumo já foi persistido pelo run_batch antes da checagem de duplicidade.
            summary = json.loads((root / "work/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(["done", "done"], [item["status"] for item in summary["results"]])


class SocialSleepTests(unittest.TestCase):
    def test_ytdlp_command_sleeps_between_requests(self):
        env = {k: v for k, v in os.environ.items() if k != "GB_YTDLP_SLEEP"}
        with (
            patch.object(social, "local_ytdlp", return_value=None),
            patch.object(
                social.shutil, "which", side_effect=lambda name: "/usr/bin/yt-dlp" if name == "yt-dlp" else None
            ),
        ):
            with patch.dict(os.environ, env, clear=True):
                command = social.command()
                for flag, value in (
                    ("--sleep-requests", "1"),
                    ("--sleep-interval", "3"),
                    ("--max-sleep-interval", "8"),
                ):
                    self.assertEqual(value, command[command.index(flag) + 1])
            with patch.dict(os.environ, {**env, "GB_YTDLP_SLEEP": "2,5,9"}, clear=True):
                command = social.command()
                for flag, value in (
                    ("--sleep-requests", "2"),
                    ("--sleep-interval", "5"),
                    ("--max-sleep-interval", "9"),
                ):
                    self.assertEqual(value, command[command.index(flag) + 1])
            for bad in ("1,2", "a,b,c", "1,9,5", "-1,2,3"):
                with (
                    patch.dict(os.environ, {**env, "GB_YTDLP_SLEEP": bad}, clear=True),
                    self.subTest(bad=bad),
                    self.assertRaises(ValueError),
                ):
                    social.command()


class RetryAfterTests(unittest.TestCase):
    def response(self, code, headers):
        error = urllib.error.HTTPError("https://example.org/", code, "limit", headers, None)
        self.addCleanup(error.close)
        return error

    @patch.object(http.time, "sleep")
    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_429_with_short_retry_after_sleeps_and_retries_once(self, builder, safe, sleep):
        builder.return_value.open.side_effect = [
            self.response(429, {"Retry-After": "5"}),
            self.response(429, {"Retry-After": "5"}),
        ]
        with self.assertRaisesRegex(http.ProviderError, "429"):
            http.get_json("https://example.org/")
        self.assertEqual(2, builder.return_value.open.call_count)
        sleep.assert_called_once_with(5)

    @patch.object(http.time, "sleep")
    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_429_with_http_date_is_capped_and_long_waits_are_reported(self, builder, safe, sleep):
        with patch.object(http, "_retry_after_seconds", wraps=http._retry_after_seconds) as wrapped:
            builder.return_value.open.side_effect = [self.response(429, {"Retry-After": "600"})]
            with self.assertRaisesRegex(http.ProviderError, "600") as error:
                http.get_json("https://example.org/")
            self.assertIn("429", str(error.exception))
            sleep.assert_not_called()
            self.assertEqual(1, builder.return_value.open.call_count)
            wrapped.assert_called_once_with("600", cap=None)
        self.assertEqual(60, http._retry_after_seconds("Wed, 17 Sep 2036 12:00:00 GMT", cap=60))
        self.assertIsNone(http._retry_after_seconds("garbage"))
        self.assertIsNone(http._retry_after_seconds(None))
        self.assertEqual(0, http._retry_after_seconds("Wed, 17 Sep 2006 12:00:00 GMT"))

    @patch.object(http.time, "sleep")
    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_429_without_retry_after_keeps_the_existing_error(self, builder, safe, sleep):
        builder.return_value.open.side_effect = [self.response(429, {})]
        with self.assertRaisesRegex(http.ProviderError, "Quota atingida"):
            http.get_json("https://example.org/")
        sleep.assert_not_called()
        self.assertEqual(1, builder.return_value.open.call_count)


if __name__ == "__main__":
    unittest.main()
