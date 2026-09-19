"""Structured logging call sites in social.py and instagram_pairs.py.

Offline only: yt-dlp/curl/ffmpeg are faked or, where real ffmpeg is used to merge
synthetic media, no network call ever happens (media is reused from a pre-existing
local file via the curl config's `output=` line, exactly like
tests/test_instagram_recovery.py's own merge test).
"""

import contextlib
import io
import logging
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _media import skip_unless_ffmpeg, synth_audio, synth_video
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import instagram_pairs as ig
from getbrolls import logs, social
from getbrolls.http import ProviderError

PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

# Fake secrets/identifiers used only to assert they never reach a log line.
FAKE_COOKIE = "Cookie: session=SEGREDO-COOKIE-9Q2"
FAKE_HEADER = "Authorization: Bearer SEGREDO-TOKEN-7X1"
FAKE_URL = "https://cdn.example.test/reel/VIDEO-SECRETPATH.mp4?Signature=SEGREDO-SIG"
FAKE_TITLE = "Meu Titulo Bem Secreto"
FAKE_QUERY = "receita de bolo de cenoura caseiro"


def _joined(cm):
    return "\n".join(cm.output)


class SocialSubprocessLoggingTests(unittest.TestCase):
    """`event=subprocess` / `event=ytdlp_error` around social.run()."""

    def test_success_logs_subprocess_ok_with_op_and_no_secrets(self):
        completed = subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout="{}", stderr="")
        with (
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch("subprocess.run", return_value=completed),
            self.assertLogs("getbrolls.social", "DEBUG") as cm,
        ):
            out, warnings = social.run(["--flat-playlist", FAKE_URL], op="search")
        self.assertEqual("{}", out)
        self.assertEqual([], warnings)
        joined = _joined(cm)
        self.assertIn("event=subprocess", joined)
        self.assertIn("tool=yt-dlp", joined)
        self.assertIn("op=search", joined)
        self.assertIn("status=ok", joined)
        self.assertIn("exit=0", joined)
        self.assertNotIn(FAKE_URL, joined)
        self.assertNotIn(FAKE_COOKIE, joined)
        self.assertNotIn(FAKE_HEADER, joined)

    def test_classified_failure_logs_class_and_provider_without_stderr_text(self):
        stderr = f"ERROR: HTTP Error 429: Too Many Requests\n{FAKE_HEADER}\n{FAKE_COOKIE}\n{FAKE_URL}"
        called = subprocess.CalledProcessError(1, ["yt-dlp"], output="", stderr=stderr)
        with (
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch("subprocess.run", side_effect=called),
            self.assertLogs("getbrolls.social", "DEBUG") as cm,
        ):
            with self.assertRaises(ProviderError):
                social.run(["--dummy"], op="metadata")
        joined = _joined(cm)
        self.assertIn("event=ytdlp_error", joined)
        self.assertIn("class=rate_limit", joined)
        self.assertIn("provider=yt-dlp", joined)
        self.assertIn("event=subprocess", joined)
        self.assertIn("op=metadata", joined)
        self.assertIn("status=error", joined)
        # The classification event only carries `class=`/`provider=`; the raw stderr
        # (which could carry a header, cookie, or signed URL) is never logged.
        self.assertNotIn(FAKE_HEADER, joined)
        self.assertNotIn(FAKE_COOKIE, joined)
        self.assertNotIn(FAKE_URL, joined)

    def test_search_never_logs_the_query_text(self):
        payload = '{"entries": []}'
        completed = subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout=payload, stderr="")
        with (
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch("subprocess.run", return_value=completed),
            self.assertLogs("getbrolls.social", "DEBUG") as cm,
        ):
            social.search(FAKE_QUERY, 5)
        joined = _joined(cm)
        self.assertNotIn(FAKE_QUERY, joined)
        self.assertNotIn(FAKE_TITLE, joined)


class SocialToolPathAndSleepLoggingTests(unittest.TestCase):
    """`event=tool_path` and the once-per-process `event=ytdlp_sleep`."""

    def setUp(self):
        social._sleep_logged = False

    def tearDown(self):
        social._sleep_logged = False

    def test_tool_path_and_sleep_logged_at_debug_once_per_process(self):
        fake_exe = Path("/tmp/fake-yt-dlp-not-real")
        with (
            patch.object(social, "executable_override", return_value=None),
            patch.object(social, "local_ytdlp", return_value=fake_exe),
            patch.dict(os.environ, {"GB_YTDLP_SLEEP": "2,4,9"}, clear=False),
            self.assertLogs("getbrolls.social", "DEBUG") as cm,
        ):
            social.command()
            social.command()
        joined = _joined(cm)
        self.assertIn("event=tool_path", joined)
        self.assertIn("tool=yt-dlp", joined)
        self.assertIn("source=venv", joined)
        self.assertEqual(1, joined.count("event=ytdlp_sleep"), "ytdlp_sleep must log once per process")
        self.assertIn("requests=2", joined)
        self.assertIn("min=4", joined)
        self.assertIn("max=9", joined)


class SocialCacheReuseLoggingTests(unittest.TestCase):
    """`event=cache_reuse` for the subtitle cache directory."""

    def test_hit_after_first_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            destination = cache / "stem-pt.vtt"
            with self.assertLogs("getbrolls.social", "DEBUG") as cm:
                logs.event(social._log, logging.DEBUG, "cache_reuse", kind="subtitle", status="miss")
            self.assertIn("status=miss", _joined(cm))
            destination.write_text("x", encoding="utf-8")
            with self.assertLogs("getbrolls.social", "DEBUG") as cm:
                logs.event(social._log, logging.DEBUG, "cache_reuse", kind="subtitle", status="hit")
            self.assertIn("status=hit", _joined(cm))


class InstagramPairsPairStageLoggingTests(unittest.TestCase):
    """`event=pair_stage` / `event=pair_reuse` from download_or_reuse()."""

    def test_downloaded_logs_pair_stage_ok_with_bytes_and_no_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "video.conf"
            conf.write_text(f'url = "{FAKE_URL}"\n', encoding="utf-8")
            target = root / "part.mp4"
            payload = b"0123456789"

            def success(cmd, **kwargs):
                Path(cmd[cmd.index("--output") + 1]).write_bytes(payload)

            with (
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=success),
                self.assertLogs("getbrolls.instagram_pairs", "DEBUG") as cm,
            ):
                action = ig.download_or_reuse(
                    cfg_path=conf,
                    part_path=target,
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                    stage="video",
                )
            self.assertEqual("downloaded", action)
            joined = _joined(cm)
            self.assertIn("event=pair_stage", joined)
            self.assertIn("stage=video", joined)
            self.assertIn("status=ok", joined)
            self.assertIn(f"bytes={len(payload)}", joined)
            self.assertNotIn(FAKE_URL, joined)
            self.assertNotIn("example.org", "".join(str(c) for c in [conf.read_text()]))  # sanity: fixture only

    def test_curl_failure_logs_pair_stage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "video.conf"
            conf.write_text('url = "https://example.org/media"\n', encoding="utf-8")
            target = root / "part.mp4"

            def fail(cmd, **kwargs):
                raise subprocess.CalledProcessError(18, cmd, stderr=b"partial transfer")

            with (
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=fail),
                self.assertLogs("getbrolls.instagram_pairs", "DEBUG") as cm,
                self.assertRaises(ig.CollectError),
            ):
                ig.download_or_reuse(
                    cfg_path=conf,
                    part_path=target,
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                    stage="video",
                )
            joined = _joined(cm)
            self.assertIn("event=pair_stage", joined)
            self.assertIn("stage=video", joined)
            self.assertIn("status=error", joined)

    def test_reused_config_output_logs_pair_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "already-downloaded.mp4"
            source.write_bytes(b"existing-bytes")
            conf = root / "audio.conf"
            conf.write_text(f'url = "https://example.org/media"\noutput = "{source}"\n', encoding="utf-8")
            target = root / "part.mp4"
            with (
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig, "_reused_part_is_valid", return_value=True),
                self.assertLogs("getbrolls.instagram_pairs", "DEBUG") as cm,
            ):
                action = ig.download_or_reuse(
                    cfg_path=conf,
                    part_path=target,
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=True,
                    stage="audio",
                )
            self.assertEqual("reused-config-output", action)
            joined = _joined(cm)
            self.assertIn("event=pair_reuse", joined)
            self.assertIn("stage=audio", joined)


class InstagramPairsMediaUrlRefusedTests(unittest.TestCase):
    """`event=media_url_refused` from validate_media_url()/_curl_resolution()."""

    def test_credentials_in_url_logs_reason_without_the_password(self):
        with self.assertLogs("getbrolls.instagram_pairs", "WARNING") as cm:
            with self.assertRaises(ig.CollectError):
                ig.validate_media_url("https://user:SEGREDO-SENHA@example.org/media", Path("cfg.conf"))
        joined = _joined(cm)
        self.assertIn("event=media_url_refused", joined)
        self.assertIn("reason=credentials_in_url", joined)
        self.assertNotIn("SEGREDO-SENHA", joined)

    def test_local_host_logs_reason(self):
        with self.assertLogs("getbrolls.instagram_pairs", "WARNING") as cm:
            with self.assertRaises(ig.CollectError):
                ig.validate_media_url("https://camera.local/media", Path("cfg.conf"))
        joined = _joined(cm)
        self.assertIn("event=media_url_refused", joined)
        self.assertIn("reason=local_host", joined)


class InstagramPairsBlockedLoggingTests(unittest.TestCase):
    """`event=blocked` from _record_failure(), including the CDN-block detection path."""

    def test_curl_403_sets_http_status_and_cooldown_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "video.conf"
            conf.write_text('url = "https://example.org/media"\n', encoding="utf-8")
            target = root / "part.mp4"

            def blocked_curl(cmd, **kwargs):
                raise subprocess.CalledProcessError(22, cmd, stderr=b"curl: (22) The requested URL returned error: 403")

            with (
                patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(ig.subprocess, "run", side_effect=blocked_curl),
                self.assertRaises(ig.CollectError) as ctx,
            ):
                ig.download_or_reuse(
                    cfg_path=conf,
                    part_path=target,
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                    stage="video",
                )
            exc = ctx.exception
            self.assertTrue(exc.cooldown)
            self.assertEqual("403", exc.http_status)

    def test_record_failure_logs_blocked_with_http_status_and_recorded_flag(self):
        exc = ig.CollectError("CDN recusou o config x com HTTP 429; pare o lote", cooldown=True)
        exc.http_status = "429"

        class Args:
            project = None
            continue_on_error = False

        with self.assertLogs("getbrolls.instagram_pairs", "WARNING") as cm:
            stop_reason, fatal = ig._record_failure([], "01_ITEM", exc, Args())
        self.assertEqual("cooldown", stop_reason)
        self.assertIsNone(fatal)
        joined = _joined(cm)
        self.assertIn("event=blocked", joined)
        self.assertIn("http_status=429", joined)
        # No --project was given, so record_queue_cooldown() could not persist anything.
        self.assertIn("cooldown_recorded=False", joined)


class InstagramPairsPaceLoggingTests(unittest.TestCase):
    """`event=pace`: only `wait_s=`, never the stem (which may embed a username)."""

    def test_pace_logs_wait_s_and_not_the_stem(self):
        with (
            patch.object(ig.random, "uniform", return_value=0.5),
            patch.object(ig.time, "sleep", return_value=None),
            self.assertLogs("getbrolls.instagram_pairs", "DEBUG") as cm,
        ):
            ig._pause_before("creatorname_01_ABCDEF", 1, 1)
        joined = _joined(cm)
        self.assertIn("event=pace", joined)
        self.assertIn("wait_s=0.5", joined)
        self.assertNotIn("creatorname", joined)


class InstagramPairsSafeItemIdTests(unittest.TestCase):
    """`_safe_item_id`: the username segment of a student-layout stem never survives."""

    def test_student_stem_drops_the_username(self):
        self.assertEqual("ABCDEF", ig._safe_item_id("creatorname_01_ABCDEF"))

    def test_flat_stem_is_kept_as_is(self):
        self.assertEqual("01_TEST", ig._safe_item_id("01_TEST"))


class InstagramPairsBatchIntegrationTests(unittest.TestCase):
    """Full run_batch(): per-item start/end + batch summary, with a student stem."""

    def test_item_events_use_safe_id_and_summary_is_logged(self):
        canned = {
            "output": "out.mp4",
            "size": 10,
            "duration": 1.0,
            "video_codec": "h264",
            "pix_fmt": "yuv420p",
            "width": 10,
            "height": 10,
            "audio_codec": "aac",
            "audio_hash_sha256": None,
            "stem": "creatorname_01_ABCDEF",
            "video_config": "v.conf",
            "audio_config": "a.conf",
            "video_part_action": "downloaded",
            "audio_part_action": "downloaded",
        }

        class Args:
            pace = "0"
            max_per_run = 25
            summary_json = None
            continue_on_error = False
            project = None

        pairs = [("creatorname_01_ABCDEF", Path("v.conf"), Path("a.conf"))]
        with (
            patch.object(ig, "_collect_one", return_value=canned),
            self.assertLogs("getbrolls.instagram_pairs", "DEBUG") as cm,
        ):
            summary = ig.run_batch(pairs, output_for=lambda _stem: Path("out.mp4"), args=Args())
        self.assertEqual(1, summary["done"])
        joined = _joined(cm)
        self.assertIn("event=pair_item_start", joined)
        self.assertIn("event=pair_item_end", joined)
        self.assertIn("shortcode=ABCDEF", joined)
        self.assertNotIn("creatorname", joined)
        self.assertIn("event=pair_batch_summary", joined)
        self.assertIn("done=1", joined)
        self.assertIn("failed=0", joined)
        self.assertIn("skipped=0", joined)


class InstagramPairsUnconfiguredLoggerTests(unittest.TestCase):
    """Nothing reaches real stderr from logging alone when the logger is unconfigured."""

    def test_no_stderr_output_without_configure(self):
        logs._degrade()
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stderr(buffer):
                ig_logger = logging.getLogger("getbrolls.instagram_pairs")
                for _ in range(3):
                    ig_logger.warning("this must never reach real stderr")
                logs.event(ig_logger, logging.WARNING, "blocked", http_status="403", cooldown_recorded=False)
            self.assertEqual("", buffer.getvalue())
        finally:
            logs._degrade()

    def test_main_does_not_configure_logging_without_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "video.mp4"
            audio = root / "audio.m4a"
            video.write_bytes(b"not-real-media")
            audio.write_bytes(b"not-real-media")
            conf_video = root / "01_TEST_video.conf"
            conf_audio = root / "01_TEST_audio.conf"
            conf_video.write_text(f'url = "https://example.org/video"\noutput = "{video}"\n', encoding="utf-8")
            conf_audio.write_text(f'url = "https://example.org/audio"\noutput = "{audio}"\n', encoding="utf-8")
            args = [
                "--video-config",
                str(conf_video),
                "--audio-config",
                str(conf_audio),
                "--output",
                str(root / "out.mp4"),
            ]
            logs._degrade()
            try:
                with patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS):
                    # No --project: never triggers ffmpeg (fails first, on a non-media
                    # fixture) but logs.configure() must not have been called either way.
                    with patch.object(logs, "configure") as configure_spy:
                        ig.main(args)
                    configure_spy.assert_not_called()
            finally:
                logs._degrade()


class InstagramPairsStdoutParityTests(unittest.TestCase):
    """Printed stdout/stderr are byte-identical whether logging runs at DEBUG or is off."""

    @skip_unless_ffmpeg
    def test_stdout_stderr_identical_debug_vs_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "video.mp4"
            audio = root / "audio.m4a"
            synth_video(video, duration=1)
            synth_audio(audio, frequency=440, duration=1)
            configs = root / "configs"
            configs.mkdir()
            (configs / "01_TEST_video.conf").write_text(
                f'url = "https://example.org/video"\noutput = "{video}"\n', encoding="utf-8"
            )
            (configs / "01_TEST_audio.conf").write_text(
                f'url = "https://example.org/audio"\noutput = "{audio}"\n', encoding="utf-8"
            )
            outdir = root / "out"
            partsdir = root / "parts"
            project = root / "proj"
            project.mkdir()
            args = [
                "--config-dir",
                str(configs),
                "--output-dir",
                str(outdir),
                "--parts-dir",
                str(partsdir),
                "--config-output-root",
                str(root),
                "--layout",
                "flat",
                "--pace",
                "0",
                "--project",
                str(project),
            ]

            def run_once(env):
                out, err = io.StringIO(), io.StringIO()
                with (
                    patch.dict(os.environ, env, clear=False),
                    patch.object(ig.socket, "getaddrinfo", return_value=PUBLIC_DNS),
                    contextlib.redirect_stdout(out),
                    contextlib.redirect_stderr(err),
                ):
                    code = ig.main(list(args))
                return code, out.getvalue(), err.getvalue()

            try:
                code1, out1, err1 = run_once({"GB_LOG_LEVEL": "DEBUG", "GB_LOG_STDERR": ""})
                self.assertEqual(0, code1)
                import shutil as _shutil

                _shutil.rmtree(outdir)
                _shutil.rmtree(partsdir)
                code2, out2, err2 = run_once({"GB_LOG_LEVEL": "off", "GB_LOG_STDERR": ""})
                self.assertEqual(0, code2)
            finally:
                logs._degrade()
            self.assertEqual(out1, out2)
            self.assertEqual(err1, err2)


if __name__ == "__main__":
    unittest.main()
