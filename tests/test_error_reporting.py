"""Legible failures across the subprocess and network boundaries (issue #31, parts B/C).

Covers yt-dlp error classification in social.py, http.download/get_json error separation,
provider refresh(), media.run and the Instagram pair collector.
"""

import contextlib
import email.message
import email.utils
import errno
import hashlib
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path

# Any só aparece citado em cast("Any", ...); o pyright resolve a string, o pylint não.
from typing import Any, cast  # pylint: disable=unused-import
from unittest.mock import MagicMock, patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _portable import unlink_frozen

from getbrolls import cli, http, instagram_media, media, providers, queue, runtime, social
from getbrolls import instagram_pairs as ig
from getbrolls.http import ProviderError
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment
from getbrolls.rules import format_report, load_rules

PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def exit_message(exc):
    """instagram_pairs.die attaches a redacted `message`; SystemExit has none statically."""
    return getattr(exc, "message", "") or ""


def _cpe(stderr, returncode=1):
    return subprocess.CalledProcessError(returncode, ["yt-dlp"], output="", stderr=stderr)


class SocialErrorClassificationTests(unittest.TestCase):
    def _run_with_stderr(self, stderr, returncode=1, side_effect=None):
        with (
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch("subprocess.run", side_effect=side_effect or _cpe(stderr, returncode)),
            self.assertRaises(ProviderError) as ctx,
        ):
            social.run(["--dummy"])
        return str(ctx.exception)

    def test_429_rate_limit_is_detectable_by_queue_cooldown_wording(self):
        msg = self._run_with_stderr("ERROR: HTTP Error 429: Too Many Requests")
        self.assertIn("429", msg)
        self.assertIn("limite de requisições da fonte (429); aguarde e tente de novo", msg)

        self.assertTrue(queue.is_cooldown_reason(msg))

    def test_private_or_removed_video(self):
        msg = self._run_with_stderr("ERROR: Private video. Sign in if you've been granted access")
        self.assertIn("privado", msg.lower())

    def test_unavailable_video(self):
        msg = self._run_with_stderr("ERROR: Video unavailable")
        self.assertIn("indisponível", msg.lower())

    def test_geo_block(self):
        msg = self._run_with_stderr("ERROR: This video is not available in your country")
        self.assertIn("geo", msg.lower())

    def test_unsupported_format(self):
        msg = self._run_with_stderr("ERROR: Requested format is not available")
        self.assertIn("formato", msg.lower())

    def test_unsupported_url(self):
        msg = self._run_with_stderr("ERROR: Unsupported URL: foo://bar")
        self.assertIn("não suportada", msg.lower())

    def test_login_phrase_matches_only_specific_wording(self):
        msg = self._run_with_stderr("ERROR: Sign in to confirm your age")
        self.assertIn("sessão de acesso", msg.lower())
        msg2 = self._run_with_stderr("ERROR: [debug] login flow trace for plugin xyz")
        self.assertNotIn("sessão de acesso", msg2.lower())

    def test_stderr_tail_is_appended_and_redacted(self):
        stderr = "\n".join([f"line {i}" for i in range(10)] + ["https://secret.example/token=abc"])
        msg = self._run_with_stderr(stderr)
        self.assertIn("line 5", msg)
        self.assertNotIn("line 0", msg)  # only last ~6 lines
        self.assertNotIn("secret.example", msg)

    def test_timeout_expired_has_specific_message(self):
        with (
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=42)),
            self.assertRaises(ProviderError) as ctx,
        ):
            social.run(["--dummy"], timeout=42)
        self.assertIn("42", str(ctx.exception))
        self.assertIn("excedeu", str(ctx.exception).lower())

    def test_file_not_found_names_executable(self):
        with (
            patch.object(social, "command", return_value=["yt-dlp-missing"]),
            patch("subprocess.run", side_effect=FileNotFoundError(2, "No such file", "yt-dlp-missing")),
            self.assertRaises(ProviderError) as ctx,
        ):
            social.run(["--dummy"])
        self.assertIn("yt-dlp-missing", str(ctx.exception))

    def test_permission_error_names_executable(self):
        with (
            patch.object(social, "command", return_value=["yt-dlp-locked"]),
            patch("subprocess.run", side_effect=PermissionError(13, "Permission denied", "yt-dlp-locked")),
            self.assertRaises(ProviderError) as ctx,
        ):
            social.run(["--dummy"])
        self.assertIn("yt-dlp-locked", str(ctx.exception))

    def test_success_with_warning_lines_is_surfaced_to_search(self):
        completed = subprocess.CompletedProcess(
            ["yt-dlp"], 0, stdout='{"entries": []}', stderr="WARNING: falling back to generic extractor"
        )
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        try:
            with (
                patch.object(social, "command", return_value=["yt-dlp"]),
                patch("subprocess.run", return_value=completed),
            ):
                social.search("foo", 3)
        finally:
            runtime.ACTIVE.reset(token)
        self.assertTrue(any(w["code"] == "YTDLP_WARNING" for w in event["warnings"]))

    def test_no_warnings_flag_removed_from_command(self):
        with (
            patch("getbrolls.social.local_ytdlp", return_value=None),
            patch("shutil.which", return_value="/usr/bin/yt-dlp"),
        ):
            args = social.command()
        self.assertNotIn("--no-warnings", args)


class RetryAfterCapTests(unittest.TestCase):
    def test_cap_none_is_accepted_without_type_error(self):
        # Pyright regression: cap defaulted to int, so cap=None must still work at runtime.
        self.assertEqual(http._retry_after_seconds("5", cap=None), 5)
        self.assertEqual(http._retry_after_seconds("500", cap=None), 500)


class GetJsonCacheWriteTests(unittest.TestCase):
    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_cache_write_failure_does_not_look_like_a_provider_outage(self, builder, safe):
        response = MagicMock()
        response.headers = {}
        response.read.return_value = b'{"ok": true}'
        builder.return_value.open.return_value.__enter__.return_value = response
        for name in ("GB_CACHE_DIR", "GETBROLLS_CACHE_DIR"):
            with self.subTest(env=name):
                other = "GETBROLLS_CACHE_DIR" if name == "GB_CACHE_DIR" else "GB_CACHE_DIR"
                with tempfile.TemporaryDirectory() as tmp:
                    cache_dir = Path(tmp) / "ro-cache"
                    cache_dir.mkdir()
                    with patch.dict(os.environ, {name: str(cache_dir)}):
                        os.environ.pop(other, None)
                        with patch("pathlib.Path.mkdir", side_effect=OSError(errno.ENOSPC, "no space")):
                            result = http.get_json("https://example.org/api", cache_ttl=60)
                self.assertEqual(result, {"ok": True})


class DownloadErrorSeparationTests(unittest.TestCase):
    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_http_error_message_includes_code_and_body(self, builder, safe):
        error_body = io.BytesIO(b"quota exceeded, details here")
        http_error = urllib.error.HTTPError(
            "https://example.org/video.mp4", 403, "Forbidden", email.message.Message(), error_body
        )
        builder.return_value.open.side_effect = http_error
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.mp4"
            with self.assertRaises(ProviderError) as ctx:
                http.download("https://example.org/video.mp4", target)
        self.assertIn("403", str(ctx.exception))
        self.assertIn("quota exceeded", str(ctx.exception))

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_url_error_message_names_reason(self, builder, safe):
        builder.return_value.open.side_effect = urllib.error.URLError("connection refused")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.mp4"
            with self.assertRaises(ProviderError) as ctx:
                http.download("https://example.org/video.mp4", target)
        self.assertIn("URLError", str(ctx.exception))

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_write_failure_reports_errno_and_path_distinct_from_network(self, builder, safe):
        response = MagicMock()
        response.headers = {}
        response.read.side_effect = [b"chunk", b""]
        builder.return_value.open.return_value.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.mp4"
            with (
                patch.object(Path, "open", side_effect=OSError(errno.ENOSPC, "No space left on device")),
                self.assertRaises(ProviderError) as ctx,
            ):
                http.download("https://example.org/video.mp4", target)
        self.assertIn(str(errno.ENOSPC), str(ctx.exception))
        self.assertNotIn("URLError", str(ctx.exception))


class RefreshRaisesInsteadOfNoneTests(unittest.TestCase):
    def test_nasa_refresh_raises_when_no_video_asset_left(self):
        item = {"provider": "nasa", "source_id": "abc123"}
        with (
            patch.object(providers, "get_json", return_value={"collection": {"items": []}}),
            self.assertRaises(ProviderError),
        ):
            providers.refresh(item)

    def test_commons_refresh_raises_when_file_is_not_video(self):
        item = {"provider": "commons", "source_id": "42"}
        with (
            patch.object(
                providers,
                "get_json",
                return_value={
                    "query": {
                        "pages": {
                            "42": {"imageinfo": [{"mime": "image/jpeg", "url": "https://commons.wikimedia.org/x.jpg"}]}
                        }
                    }
                },
            ),
            self.assertRaises(ProviderError),
        ):
            providers.refresh(item)


class MediaErrorSeparationTests(unittest.TestCase):
    def test_called_process_error_includes_exit_code_and_stderr_tail(self):
        with (
            patch.object(
                media.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(5, ["ffmpeg"], output="", stderr="some detail"),
            ),
            self.assertRaises(ValueError) as ctx,
        ):
            media.run(["ffmpeg", "-x"])
        self.assertIn("exit 5", str(ctx.exception))
        self.assertIn("some detail", str(ctx.exception))
        self.assertIn("doctor", str(ctx.exception))

    def test_timeout_expired_has_its_own_message(self):
        with (
            patch.object(media.subprocess, "run", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 180)),
            self.assertRaises(ValueError) as ctx,
        ):
            media.run(["ffmpeg", "-x"])
        self.assertIn("180", str(ctx.exception))
        self.assertNotIn("exit", str(ctx.exception))


class DrawtextCacheTests(unittest.TestCase):
    def setUp(self):
        media._DRAWTEXT.clear()
        # Ciclo de vida cobre o teste inteiro; a limpeza já é feita via addCleanup.
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        self.env_patch = patch.dict("os.environ", {"GB_CACHE_DIR": self.tmp.name})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        # A real file on disk gives drawtext_available() a real, stable mtime to key its
        # cache on, instead of patching Path.stat globally for every Path in the process.
        self.ffmpeg_file = Path(self.tmp.name) / "ffmpeg-bin"
        self.ffmpeg_file.write_bytes(b"x")

    def test_probe_result_is_persisted_to_disk_and_reused(self):
        for name in ("GB_CACHE_DIR", "GETBROLLS_CACHE_DIR"):
            with self.subTest(env=name):
                other = "GETBROLLS_CACHE_DIR" if name == "GB_CACHE_DIR" else "GB_CACHE_DIR"
                media._DRAWTEXT.clear()
                # Both names point at the same tmp dir; clear the leftover disk cache
                # from the previous iteration so each name is probed from scratch.
                media._drawtext_cache_path(str(self.ffmpeg_file)).unlink(missing_ok=True)
                with patch.dict(os.environ, {name: self.tmp.name}):
                    os.environ.pop(other, None)
                    with (
                        patch.object(media, "tool_path", return_value=str(self.ffmpeg_file)),
                        patch.object(media, "run", return_value=" ... drawtext ... ") as run_mock,
                    ):
                        self.assertTrue(media.drawtext_available())
                        self.assertEqual(run_mock.call_count, 1)
                        media._DRAWTEXT.clear()
                        # Second call (new "process") must not re-invoke ffmpeg -filters; reads the disk cache.
                        self.assertTrue(media.drawtext_available())
                        self.assertEqual(run_mock.call_count, 1)

    def test_probe_failure_records_warning_distinct_from_missing_filter(self):
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        try:
            with (
                patch.object(media, "tool_path", return_value=str(self.ffmpeg_file)),
                patch.object(media, "run", side_effect=ValueError("ffmpeg não encontrado")),
            ):
                self.assertFalse(media.drawtext_available())
        finally:
            runtime.ACTIVE.reset(token)
        self.assertTrue(any(w["code"] == "FFMPEG_PROBE_FAILED" for w in event["warnings"]))

    def test_missing_filter_does_not_record_a_warning(self):
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        try:
            with (
                patch.object(media, "tool_path", return_value=str(self.ffmpeg_file)),
                patch.object(media, "run", return_value="no such filter here"),
            ):
                self.assertFalse(media.drawtext_available())
        finally:
            runtime.ACTIVE.reset(token)
        self.assertEqual([], event["warnings"])


# PUBLIC_DNS is defined once, near the top of this module (finding #25: it was duplicated here).


class RunErrorTests(unittest.TestCase):
    def test_run_dies_with_exit_code_and_stderr_tail(self):
        with (
            contextlib.redirect_stderr(io.StringIO()),
            patch.object(
                subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(7, ["ffmpeg", "x"], output="", stderr="boom detail"),
            ),
            self.assertRaises(ig.CollectError) as ctx,
        ):
            ig.run(["ffmpeg", "x"], quiet=True)
        self.assertIn("ffmpeg falhou (exit 7)", exit_message(ctx.exception))
        self.assertIn("boom detail", exit_message(ctx.exception))


class ReuseValidityTests(unittest.TestCase):
    def test_reused_part_runs_ffprobe_and_fails_when_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "video.conf"
            conf.write_text('url = "https://example.org/v"\n', encoding="utf-8")
            part = root / "p.mp4"
            part.write_bytes(b"not-really-media")
            with (
                patch.object(socket, "getaddrinfo", return_value=PUBLIC_DNS),
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["ffprobe"])),
                self.assertRaises(ig.CollectError) as ctx,
            ):
                ig.download_or_reuse(
                    cfg_path=conf,
                    part_path=part,
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                )
            self.assertIn("corrompida", exit_message(ctx.exception))
            self.assertIn("--force-download", exit_message(ctx.exception))

    def test_reused_part_accepted_when_ffprobe_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "video.conf"
            conf.write_text('url = "https://example.org/v"\n', encoding="utf-8")
            part = root / "p.mp4"
            part.write_bytes(b"looks-fine")
            with (
                patch.object(socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(["ffprobe"], 0)),
            ):
                action = ig.download_or_reuse(
                    cfg_path=conf,
                    part_path=part,
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                )
            self.assertEqual(action, "reused-part")


class TimeoutTests(unittest.TestCase):
    def test_curl_timeout_has_specific_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conf = root / "video.conf"
            conf.write_text('url = "https://example.org/v"\n', encoding="utf-8")

            def timeout(cmd, **kwargs):
                raise subprocess.TimeoutExpired(cmd, 600)

            with (
                patch.object(socket, "getaddrinfo", return_value=PUBLIC_DNS),
                patch.object(subprocess, "run", side_effect=timeout),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(ig.CollectError) as ctx,
            ):
                ig.download_or_reuse(
                    cfg_path=conf,
                    part_path=root / "p.mp4",
                    config_output_root=root,
                    force_download=False,
                    prefer_config_output=False,
                )
            self.assertIn("timed out", exit_message(ctx.exception))


class AudioHashOptionalTests(unittest.TestCase):
    def test_verify_output_hash_is_null_when_not_requested(self):
        with (
            patch.object(
                instagram_media,
                "ffprobe_json",
                return_value={
                    "format": {"duration": "1.0", "size": "10"},
                    "streams": [
                        {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": 10, "height": 10},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                },
            ),
            patch.object(instagram_media, "audio_hash") as hash_fn,
        ):
            report = ig.verify_output(Path("/dev/null"), compute_audio_hash=False)
        hash_fn.assert_not_called()
        self.assertIn("audio_hash_sha256", report)
        self.assertIsNone(report["audio_hash_sha256"])


class Finding9ProviderErrorAuditedTests(unittest.TestCase):
    """#9: ProviderError -> INVALID_DATA with RULES.md hint + PROVIDER_ERROR warning."""

    def test_provider_error_becomes_invalid_data_with_rules_hint(self):
        args = __import__("argparse").Namespace(command="status", project=None)

        def boom(_args):
            raise ProviderError("fonte recusou")

        with self.assertRaises(runtime.OperationError) as ctx:
            runtime.audited(args, boom)
        self.assertEqual(ctx.exception.payload["error_code"], "INVALID_DATA")
        self.assertIn("RULES.md", ctx.exception.payload["message"])

    def test_provider_error_emits_provider_error_warning(self):
        args = __import__("argparse").Namespace(command="status", project=None)

        def boom(_args):
            raise ProviderError("fonte recusou")

        with self.assertRaises(runtime.OperationError) as ctx:
            runtime.audited(args, boom)
        self.assertTrue(any(w["code"] == "PROVIDER_ERROR" for w in ctx.exception.payload["warnings"]))


class Finding13RateAndUnavailableRegexTests(unittest.TestCase):
    """#13: _RATE_RE / _UNAVAILABLE_RE false positives on ffmpeg frame counters and 'removed temporary file'."""

    def _run_with_stderr(self, stderr):
        with (
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch("subprocess.run", side_effect=_cpe(stderr)),
            self.assertRaises(ProviderError) as ctx,
        ):
            social.run(["--dummy"])
        return str(ctx.exception)

    def test_ffmpeg_frame_counter_is_not_a_rate_limit(self):
        msg = self._run_with_stderr("frame=  120 fps= 429 fps q=-1.0 size= 100kB time=00:00:04")
        self.assertNotIn("limite de requisições", msg)

    def test_removed_temporary_file_is_not_unavailable(self):
        msg = self._run_with_stderr("Deleting original file (pass -k to keep) removed temporary file")
        self.assertNotIn("indisponível", msg.lower())

    def test_real_429_is_still_detected(self):
        msg = self._run_with_stderr("ERROR: HTTP Error 429: Too Many Requests")
        self.assertIn("limite de requisições", msg)

    def test_real_rate_limit_phrase_is_still_detected(self):
        msg = self._run_with_stderr("ERROR: rate limit exceeded, try again later")
        self.assertIn("limite de requisições", msg)


class Finding31LoginBeforeUnavailableTests(unittest.TestCase):
    def test_unavailable_plus_sign_in_classifies_as_login(self):
        with (
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch("subprocess.run", side_effect=_cpe("ERROR: Video unavailable. Sign in to confirm your age.")),
            self.assertRaises(ProviderError) as ctx,
        ):
            social.run(["--dummy"])
        self.assertIn("sessão de acesso", str(ctx.exception))


class Finding41MultilineWarningsTests(unittest.TestCase):
    def test_extract_warnings_keeps_wrapped_continuation_lines(self):
        # A single yt-dlp WARNING can wrap onto indented continuation lines with no
        # "WARNING" prefix of their own; those must not be dropped.
        stderr = (
            "WARNING: nsig extraction failed: You may experience\n"
            "         throttling for some formats\n"
            "WARNING: second issue"
        )
        warnings = social._extract_warnings(stderr)
        self.assertEqual(len(warnings), 2)
        self.assertIn("nsig extraction failed", warnings[0])
        self.assertIn("throttling for some formats", warnings[0])
        self.assertIn("second issue", warnings[1])

    def test_extract_warnings_keeps_all_lines(self):
        stderr = "WARNING: first issue\nsome noise\nWARNING: second issue\nWARNING: third issue"
        warnings = social._extract_warnings(stderr)
        self.assertEqual(len(warnings), 3)
        self.assertIn("first issue", warnings[0])
        self.assertIn("second issue", warnings[1])
        self.assertIn("third issue", warnings[2])


class Finding27DrawtextProbeFailureNotCachedTests(unittest.TestCase):
    def setUp(self):
        media._DRAWTEXT.clear()
        # Ciclo de vida cobre o teste inteiro; a limpeza já é feita via addCleanup.
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        self.env_patch = patch.dict("os.environ", {"GB_CACHE_DIR": self.tmp.name})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.ffmpeg_file = Path(self.tmp.name) / "ffmpeg-bin"
        self.ffmpeg_file.write_bytes(b"x")

    def test_probe_failure_does_not_persist_false_to_disk(self):
        for name in ("GB_CACHE_DIR", "GETBROLLS_CACHE_DIR"):
            with self.subTest(env=name):
                other = "GETBROLLS_CACHE_DIR" if name == "GB_CACHE_DIR" else "GB_CACHE_DIR"
                media._DRAWTEXT.clear()
                # Both names point at the same tmp dir; clear the leftover disk cache
                # from the previous iteration so each name is probed from scratch.
                media._drawtext_cache_path(str(self.ffmpeg_file)).unlink(missing_ok=True)
                with patch.dict(os.environ, {name: self.tmp.name}):
                    os.environ.pop(other, None)
                    with (
                        patch.object(media, "tool_path", return_value=str(self.ffmpeg_file)),
                        patch.object(media, "run", side_effect=ValueError("ffmpeg não encontrado")),
                    ):
                        self.assertFalse(media.drawtext_available())
                    cache_path = media._drawtext_cache_path(str(self.ffmpeg_file))
                    self.assertFalse(cache_path.exists())
                    media._DRAWTEXT.clear()
                    # A later working probe must not be short-circuited by a stale disk cache from the failure.
                    with (
                        patch.object(media, "tool_path", return_value=str(self.ffmpeg_file)),
                        patch.object(media, "run", return_value=" ... drawtext ... "),
                    ):
                        self.assertTrue(media.drawtext_available())


class Finding28DownloadCatchAllTests(unittest.TestCase):
    def test_generic_exception_message_includes_type_and_chains_original(self):
        # `download()`'s except clause is narrowed to real transport/IO failures
        # (http.client.HTTPException, OSError, ValueError) so programming bugs
        # propagate uncaught instead of being misreported as a provider error. This
        # "weird" failure is itself an OSError subclass (a plausible odd transport
        # failure), so it still gets wrapped with its type name and chained cause.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.mp4"

            class WeirdError(OSError):
                pass

            def boom_open(*a, **kw):
                raise WeirdError("sekret-token-xyz")

            with (
                patch.object(http, "public_url", return_value="https://example.org/v.mp4"),
                patch.object(http, "_opener") as opener_mock,
            ):
                opener_mock.return_value.open.side_effect = boom_open
                with self.assertRaises(ProviderError) as ctx:
                    http.download("https://example.org/v.mp4", target)
        self.assertIn("Weird", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, WeirdError)


class Finding23And50RetryAfterCapTests(unittest.TestCase):
    def test_past_http_date_sleeps_zero_and_retries(self):
        past = email.utils.format_datetime(
            __import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        )
        self.assertEqual(http._retry_after_seconds(past), 0)

    def test_far_future_http_date_is_capped_not_slept_in_full(self):
        future = email.utils.format_datetime(
            __import__("datetime").datetime(2036, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        )
        capped = http._retry_after_seconds(future)
        assert capped is not None
        self.assertLessEqual(capped, http.RETRY_AFTER_CAP_S)

    def test_get_json_429_with_far_future_retry_after_does_not_sleep_for_years(self):
        class FakeHeaders(dict):
            pass

        future = email.utils.format_datetime(
            __import__("datetime").datetime(2036, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        )
        error = urllib.error.HTTPError(
            "https://example.org/api",
            429,
            "Too Many Requests",
            cast("Any", FakeHeaders({"Retry-After": future})),
            io.BytesIO(b""),
        )

        class FakeOpener:
            def open(self, *a, **kw):
                raise error

        sleep_calls = []
        with (
            patch.object(http, "_opener", return_value=FakeOpener()),
            patch.object(http.time, "sleep", side_effect=sleep_calls.append),
            self.assertRaises(ProviderError),
        ):
            http.get_json("https://example.org/api")
        self.assertTrue(all(s <= http.RETRY_AFTER_CAP_S for s in sleep_calls))


class Finding55Get429LastAttemptRaisesTests(unittest.TestCase):
    def test_429_with_retry_after_on_last_attempt_raises_instead_of_returning_none(self):
        class FakeHeaders(dict):
            pass

        error = urllib.error.HTTPError(
            "https://example.org/api",
            429,
            "Too Many Requests",
            cast("Any", FakeHeaders({"Retry-After": "1"})),
            io.BytesIO(b""),
        )

        class FakeOpener:
            def open(self, *a, **kw):
                raise error

        with (
            patch.object(http, "_opener", return_value=FakeOpener()),
            patch.object(http.time, "sleep"),
            self.assertRaises(ProviderError),
        ):
            http.get_json("https://example.org/api")

    def test_get_json_never_silently_returns_none(self):
        class FakeOpener:
            def open(self, *a, **kw):
                raise urllib.error.URLError("boom")

        with (
            patch.object(http, "_opener", return_value=FakeOpener()),
            patch.object(http.time, "sleep"),
            self.assertRaises(ProviderError),
        ):
            http.get_json("https://example.org/api")


class Finding36CacheAndBodyReadNarrowingTests(unittest.TestCase):
    def test_corrupted_cache_read_records_warning_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = {"warnings": [], "state_committed": False}
            token = runtime.ACTIVE.set(event)
            try:
                with patch.dict("os.environ", {"GETBROLLS_CACHE_DIR": tmp}):
                    os.environ.pop("GB_CACHE_DIR", None)  # o nome novo venceria o antigo
                    cache_path = Path(tmp) / (hashlib.sha256(b"https://example.org/api").hexdigest() + ".json")
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(b"not valid json")

                    with patch.object(http, "_opener") as opener_mock:
                        opener_mock.return_value.open.side_effect = urllib.error.URLError("no network needed")
                        with contextlib.suppress(ProviderError):
                            http.get_json("https://example.org/api", cache_ttl=3600)
            finally:
                runtime.ACTIVE.reset(token)
            self.assertTrue(any(w["code"] == "CACHE_UNAVAILABLE" for w in event["warnings"]))


class Finding10CliExitCodeConstantsTests(unittest.TestCase):
    def test_cli_defines_named_exit_code_constants(self):
        self.assertEqual(cli.EXIT_OPERATION_ERROR, 1)
        self.assertEqual(cli.EXIT_INTERNAL_ERROR, 3)


class Finding63StderrTailTotalLengthCapTests(unittest.TestCase):
    def test_total_length_is_capped_at_600_chars(self):
        stderr = "\n".join(f"WARNING line {i} " + ("x" * 200) for i in range(10))
        tail = runtime.stderr_tail(stderr)
        self.assertLessEqual(len(tail), 600)


class RecoveredWriteReportingTests(unittest.TestCase):
    """A gravação interrompida que um comando retoma não é a gravação desse comando."""

    def test_recovery_is_not_reported_as_this_commands_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            event = {
                "transaction": "interrompida:0",
                "at": "2026-10-07T12:00:00+00:00",
                "operation": "fixture",
                "id": None,
                "revision": None,
                "state": None,
            }
            ledger.pending.write_text(json.dumps({"data": ledger.data, "events": [event]}), encoding="utf-8")
            error = run_cli(
                "approve", "--candidate", "nope", "--by", "Ana Teste", "--statement", "aprovo", project=tmp, expect=1
            )
            self.assertFalse(ledger.pending.exists())
        self.assertIs(False, error["state_committed"])
        self.assertIs(True, error["recovered"])
        self.assertIn("RECOVERED_WRITE", [w["code"] for w in error["warnings"]])
        self.assertNotIn("review para regenerar", error.get("hint", ""))
        self.assertIn("recovered=true", error["hint"])

    def test_the_commit_hint_wins_when_the_command_also_recovered(self):
        # `roteiro` abre o Ledger (que pode concluir a gravação de outro) e grava: com os dois,
        # a dica não pode dizer que este comando não gravou nada.
        both = {"state_committed": True, "recovered": True}
        hint = runtime._error_hint  # pylint: disable=protected-access
        self.assertIsNone(hint("roteiro", both))
        self.assertIn("review", hint("approve", both) or "")
        self.assertIn("deliver", hint("deliver", {**both, "delivery_conflicts": True}) or "")
        # Sem conflito de entrega, o `deliver` que gravou o estado (a troca de formato) leva a de `review`.
        self.assertIn("review", hint("deliver", both) or "")

    def test_the_recovered_hint_does_not_promise_that_nothing_was_written(self):
        # Prévias e GIFs vão para o disco antes do journal: `state_committed=false` só diz que
        # o estado do projeto (o manifesto) não foi gravado.
        text = runtime._error_hint("preview", {"recovered": True}) or ""  # pylint: disable=protected-access
        self.assertIn("recovered=true", text)
        self.assertIn("state_committed=false", text)
        self.assertNotIn("não gravou nada", text)

    def test_a_plain_failure_is_not_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            Ledger(tmp)
            error = run_cli(
                "approve", "--candidate", "nope", "--by", "Ana Teste", "--statement", "aprovo", project=tmp, expect=1
            )
        self.assertIs(False, error["recovered"])
        self.assertNotIn("hint", error)


def _delivered_candidate(source_id, title):
    """Candidato já coletado e conferido, com clipe e contact sheet próprios."""
    c = candidate("local", source_id, title, source_url="https://example.org/" + source_id)
    set_segment(c, 0, 2)
    c["creator"]["name"] = "Autora Exemplo"
    c["preview"]["contact_sheet_path"] = f"previews/{source_id}.jpg"
    c["approval"] = {"status": "approved", "by": "Humano", "at": now(), "revision": 1}
    c["approval"].update({"channel": "chat", "statement": "aprovo"})
    c["rights"]["status"] = "permitted"
    c["rights"]["evidence"] = ["Condições conferidas na página da fonte"]
    c["output"] = {"path": f"clips/{source_id}.mp4", "sha256": "a" * 64, "verified": True}
    c["state"] = "verified"
    return c


def _store(ledger, c):
    stored = ledger.add(c)
    ledger.save_many("fixture", [stored])
    for rel in (stored["output"]["path"], stored["preview"]["contact_sheet_path"]):
        (ledger.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (ledger.root / rel).write_bytes(b"conteudo de " + rel.encode())


class DeliverConflictHintTests(unittest.TestCase):
    def test_deliver_conflict_hint_talks_about_the_conflict(self):
        # Os itens sem conflito já foram entregues (state_committed=true), mas regenerar o
        # Storyboard não resolve nada: o que falta é resolver o arquivo em conflito.
        with tempfile.TemporaryDirectory() as tmp:
            _store(Ledger(tmp), _delivered_candidate("a", "Palco"))
            first = run_cli("deliver", project=tmp)
            media = Path(tmp) / first["items"][0]["path"]
            unlink_frozen(media)
            media.write_bytes(b"edicao da pessoa")
            _store(Ledger(tmp), _delivered_candidate("b", "Outro"))
            error = run_cli("deliver", project=tmp, expect=1)
        self.assertIs(True, error["state_committed"])
        self.assertNotIn("review", error["hint"])
        self.assertIn("deliver", error["hint"])
        self.assertIn("conflito", error["hint"])
        # A marca que escolhe a dica é interna: não entra no JSON de erro.
        self.assertNotIn("delivery_conflicts", error)

    def test_a_deliver_refused_after_a_format_commit_gets_the_review_hint(self):
        # A troca de formato grava `format_changed` antes de o `deliver` recusar o BRIEF.md
        # inválido: o estado foi gravado, mas nada foi entregue e não há conflito nenhum.
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            c = candidate("local", "seen", "Synthetic", "https://example.org/a")
            c["media"].update(width=1920, height=1080)
            set_segment(c, 0, 1)
            c["format"] = format_report(c, load_rules(tmp))
            ledger.add(c)
            ledger.save("fixture", c)
            run_cli("init-rules", "--format", "reels", project=tmp)
            Path(tmp, "BRIEF.md").write_text('# Brief\n\n```json\n{"version": 1,,}\n```\n', encoding="utf-8")
            error = run_cli("deliver", project=tmp, expect=1)
            self.assertFalse((Path(tmp) / "entrega").exists())
        self.assertIs(True, error["state_committed"])
        self.assertIn("BRIEF.md", error["error"])
        self.assertNotIn("conflito", error["hint"])
        self.assertIn("review", error["hint"])


if __name__ == "__main__":
    unittest.main()
