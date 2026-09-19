"""Structured logging call sites added to http.py and media.py.

Offline only: the opener/urlopen is mocked the same way tests/test_remote_providers.py
and tests/test_error_reporting.py already do. Real ffmpeg/ffprobe runs (media.probe/cut)
are exercised through tests/_media.py and skipped when ffmpeg is unavailable.
"""

import email.message
import hashlib
import io
import logging
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import http, media
from getbrolls.http import ProviderError


class _FakeResponse:
    """Minimal context-manager stand-in for the object `opener.open()` returns.

    `read(n)` consumes from an internal cursor like a real stream (empty once
    exhausted); `download()` calls it in a loop and would spin forever against a
    fake that always returns the whole body regardless of `n`.
    """

    def __init__(self, body, status=200, headers=None):
        self._body = body
        self._pos = 0
        self.status = status
        self.headers = headers or {}

    def read(self, n=-1):
        if n is None or n < 0:
            chunk, self._pos = self._body[self._pos :], len(self._body)
            return chunk
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _SuccessOpener:
    def open(self, *args, **kwargs):
        return _FakeResponse(b'{"ok": true}', status=200)


class _FlakyThenSuccessOpener:
    """First call raises a retryable 503, second call succeeds."""

    def __init__(self):
        self.calls = 0

    def open(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise urllib.error.HTTPError(
                "https://example.org/api", 503, "Service Unavailable", email.message.Message(), io.BytesIO(b"")
            )
        return _FakeResponse(b'{"ok": true}', status=200)


def _cache_path_for(url, tmp):
    return Path(tmp) / (hashlib.sha256(url.encode()).hexdigest() + ".json")


class GetJsonRequestLoggingTests(unittest.TestCase):
    def test_success_logs_one_info_request_line(self):
        with patch.object(http, "_opener", return_value=_SuccessOpener()):
            with self.assertLogs("getbrolls.http", level="DEBUG") as cm:
                data = http.get_json("https://api.example.org/v1/search")
        self.assertEqual(data, {"ok": True})
        lines = [r.getMessage() for r in cm.records]
        request_lines = [line for line in lines if "event=request " in line or line.endswith("event=request")]
        self.assertEqual(1, len(request_lines))
        line = request_lines[0]
        self.assertIn("host=api.example.org", line)
        self.assertIn("op=json", line)
        self.assertIn("status=200", line)
        self.assertIn("cache=off", line)
        self.assertIn("attempt=1", line)
        self.assertIn("ms=", line)
        self.assertIn("bytes=", line)

    def test_cache_hit_logs_debug_and_never_opens_the_network(self):
        url = "https://api.example.org/v1/cached"
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = _cache_path_for(url, tmp)
            cache_path.write_text('{"cached": true}', encoding="utf-8")
            with patch.dict(os.environ, {"GB_CACHE_DIR": tmp}):

                class _ExplodingOpener:
                    def open(self, *a, **kw):
                        raise AssertionError("cache hit must not touch the network")

                with patch.object(http, "_opener", return_value=_ExplodingOpener()):
                    with self.assertLogs("getbrolls.http", level="DEBUG") as cm:
                        data = http.get_json(url, cache_ttl=3600)
        self.assertEqual(data, {"cached": True})
        lines = [r.getMessage() for r in cm.records]
        hit_lines = [line for line in lines if "cache=hit" in line]
        self.assertEqual(1, len(hit_lines))
        self.assertIn("host=api.example.org", hit_lines[0])
        self.assertIn("op=json", hit_lines[0])
        self.assertEqual(hit_lines[0].split()[0], "event=request")
        # Cache hit means no network round trip at all: DEBUG level, no attempt/status noise.
        for record in cm.records:
            self.assertLessEqual(record.levelno, logging.DEBUG)

    def test_retry_then_success_logs_retry_warning_and_final_success(self):
        with (
            patch.object(http, "_opener", return_value=_FlakyThenSuccessOpener()),
            patch.object(http.time, "sleep"),
        ):
            with self.assertLogs("getbrolls.http", level="DEBUG") as cm:
                data = http.get_json("https://api.example.org/v1/flaky")
        self.assertEqual(data, {"ok": True})
        lines = [(r.levelno, r.getMessage()) for r in cm.records]
        retry_lines = [msg for level, msg in lines if msg.startswith("event=retry")]
        self.assertEqual(1, len(retry_lines))
        self.assertIn("host=api.example.org", retry_lines[0])
        self.assertIn("attempt=1", retry_lines[0])
        self.assertIn("wait_s=", retry_lines[0])
        self.assertIn("reason=503", retry_lines[0])
        self.assertTrue(any(level == logging.WARNING for level, msg in lines if msg.startswith("event=retry")))
        final_lines = [msg for _, msg in lines if msg.startswith("event=request") and "attempt=2" in msg]
        self.assertEqual(1, len(final_lines))
        self.assertIn("status=200", final_lines[0])

    def test_refused_target_logs_request_refused_before_any_network_io(self):
        with self.assertLogs("getbrolls.http", level="DEBUG") as cm:
            with self.assertRaises(ProviderError):
                http.get_json("http://api.example.org/v1/insecure")
        lines = [r.getMessage() for r in cm.records]
        refused = [line for line in lines if line.startswith("event=request_refused")]
        self.assertEqual(1, len(refused))
        self.assertIn("host=api.example.org", refused[0])
        self.assertIn("reason=invalid_target", refused[0])

    def test_no_record_ever_contains_url_path_query_key_or_auth_header(self):
        secret_key = "FAKE-PROVIDER-KEY-Q7F3X9"
        secret_token = "FAKE-AUTH-TOKEN-Z9K2"
        with patch.dict(os.environ, {"PEXELS_API_KEY": secret_key}):
            with patch.object(http, "_opener", return_value=_SuccessOpener()):
                with self.assertLogs("getbrolls.http", level="DEBUG") as cm:
                    http.get_json(
                        "https://api.example.org/v1/search",
                        params={"key": secret_key, "q": "laboratory"},
                        headers={"Authorization": f"Bearer {secret_token}"},
                    )
        text = "\n".join(r.getMessage() for r in cm.records)
        self.assertNotIn(secret_key, text)
        self.assertNotIn(secret_token, text)
        self.assertNotIn("Authorization", text)
        self.assertNotIn("/v1/search", text)
        self.assertNotIn("?", text)
        self.assertNotIn("q=laboratory", text)


class DownloadRequestLoggingTests(unittest.TestCase):
    def test_success_logs_info_request_line_with_bytes(self):
        class _DownloadOpener:
            def open(self, *a, **kw):
                return _FakeResponse(b"0123456789", status=200, headers={"Content-Length": "10"})

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "clip.mp4"
            with patch.object(http, "_opener", return_value=_DownloadOpener()):
                with self.assertLogs("getbrolls.http", level="DEBUG") as cm:
                    http.download("https://videos.example.org/clip.mp4?v=2", target)
            self.assertEqual(target.read_bytes(), b"0123456789")
        lines = [r.getMessage() for r in cm.records]
        request_lines = [line for line in lines if line.startswith("event=request ")]
        self.assertEqual(1, len(request_lines))
        line = request_lines[0]
        self.assertIn("host=videos.example.org", line)
        self.assertIn("op=download", line)
        self.assertIn("status=200", line)
        self.assertIn("bytes=10", line)
        self.assertIn("cache=off", line)
        self.assertNotIn("clip.mp4", "\n".join(lines))
        self.assertNotIn("?v=2", "\n".join(lines))

    def test_size_cap_abort_logs_warning_with_limit(self):
        class _OversizedOpener:
            def open(self, *a, **kw):
                return _FakeResponse(b"", status=200, headers={"Content-Length": str(10 * 1024 * 1024)})

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "big.mp4"
            with patch.object(http, "_opener", return_value=_OversizedOpener()):
                with self.assertLogs("getbrolls.http", level="DEBUG") as cm:
                    with self.assertRaises(ProviderError):
                        http.download("https://videos.example.org/big.mp4", target, max_bytes=1024 * 1024)
        lines = [r.getMessage() for r in cm.records]
        aborted = [line for line in lines if line.startswith("event=download_aborted")]
        self.assertEqual(1, len(aborted))
        self.assertIn("host=videos.example.org", aborted[0])
        self.assertIn("reason=size_cap", aborted[0])
        self.assertIn("limit_mb=1", aborted[0])
        self.assertFalse(target.exists())

    def test_refused_target_not_public_url_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "clip.mp4"
            with self.assertLogs("getbrolls.http", level="DEBUG") as cm:
                with self.assertRaises(ProviderError):
                    http.download("https://user:pass@videos.example.org/clip.mp4", target)
        lines = [r.getMessage() for r in cm.records]
        refused = [line for line in lines if line.startswith("event=request_refused")]
        self.assertEqual(1, len(refused))
        self.assertIn("reason=not_public_url", refused[0])


class MediaSubprocessLoggingTests(unittest.TestCase):
    @skip_unless_ffmpeg
    def test_probe_emits_a_subprocess_line_with_op_ms_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.mp4"
            synth_video(src, duration=1)
            with self.assertLogs("getbrolls.media", level="DEBUG") as cm:
                info = media.probe(src)
        self.assertIn("width", info)
        lines = [r.getMessage() for r in cm.records]
        subprocess_lines = [line for line in lines if line.startswith("event=subprocess")]
        self.assertEqual(1, len(subprocess_lines))
        line = subprocess_lines[0]
        self.assertIn("tool=ffprobe", line)
        self.assertIn("op=probe", line)
        self.assertIn("status=ok", line)
        self.assertIn("exit=0", line)
        self.assertIn("ms=", line)
        # Never the raw argv: no filter flags, no source path fragments.
        self.assertNotIn("-show_streams", line)
        self.assertNotIn(str(src), line)

    @skip_unless_ffmpeg
    def test_cut_emits_cut_and_decode_check_subprocess_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.mp4"
            dst = Path(tmp) / "out.mp4"
            synth_video(src, duration=3)
            with self.assertLogs("getbrolls.media", level="DEBUG") as cm:
                media.cut(src, dst, 0, 1)
            self.assertTrue(dst.is_file())
        lines = [r.getMessage() for r in cm.records if r.getMessage().startswith("event=subprocess")]
        ops = {line.split("op=")[1].split()[0] for line in lines}
        self.assertIn("cut", ops)
        self.assertIn("decode_check", ops)
        self.assertIn("probe", ops)
        for line in lines:
            self.assertIn("tool=ffmpeg" if "op=cut" in line or "op=decode_check" in line else "tool=ffprobe", line)
            self.assertIn("status=ok", line)

    def test_subprocess_failure_logs_warning_with_error_status(self):
        with patch.object(
            media.subprocess,
            "run",
            side_effect=__import__("subprocess").CalledProcessError(5, ["ffmpeg"], output="", stderr="boom"),
        ):
            with self.assertLogs("getbrolls.media", level="DEBUG") as cm:
                with self.assertRaises(ValueError):
                    media.run(["ffmpeg", "-x"])
        lines = [r.getMessage() for r in cm.records if r.getMessage().startswith("event=subprocess")]
        self.assertEqual(1, len(lines))
        self.assertIn("status=error", lines[0])
        self.assertIn("exit=5", lines[0])
        self.assertIn("tool=ffmpeg", lines[0])
        # The wrapped exception message may carry the stderr tail; the log line must not.
        self.assertNotIn("boom", lines[0])


class DrawtextFontFallbackLoggingTests(unittest.TestCase):
    def test_font_not_found_logs_debug_event(self):
        with patch.dict(os.environ, {"GB_FONT_FILE": ""}):
            with patch.object(media, "DEFAULT_FONTS", ()):
                with self.assertLogs("getbrolls.media", level="DEBUG") as cm:
                    found = media.find_font()
        self.assertIsNone(found)
        lines = [r.getMessage() for r in cm.records]
        font_lines = [line for line in lines if line.startswith("event=font")]
        self.assertEqual(1, len(font_lines))
        self.assertIn("found=False", font_lines[0])
        self.assertIn("source=system", font_lines[0])

    @skip_unless_ffmpeg
    def test_plain_sheet_fallback_reason_no_drawtext(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "previews").mkdir()
            src = root / "in.mp4"
            synth_video(src, duration=3)
            config = {
                "max_seconds": 10,
                "frames": 4,
                "mode": "static",
                "width": 480,
                "fps": 8,
                "colors": 64,
                "max_mb": 8,
            }
            with patch.object(media, "drawtext_available", return_value=False):
                with self.assertLogs("getbrolls.media", level="DEBUG") as cm:
                    result = media.review_preview(src, root / "previews", "plain", 0, 1, config)
        self.assertFalse(result["sheet_labels"])
        lines = [r.getMessage() for r in cm.records]
        fallback_lines = [line for line in lines if line.startswith("event=fallback")]
        self.assertEqual(1, len(fallback_lines))
        self.assertIn("kind=plain_sheet", fallback_lines[0])
        self.assertIn("reason=no_drawtext", fallback_lines[0])


class LoggingDisabledBehaviorUnchangedTests(unittest.TestCase):
    """Turning logging off must not change any return value (default-settings parity)."""

    def test_get_json_return_value_is_identical_with_logging_disabled(self):
        with patch.object(http, "_opener", return_value=_SuccessOpener()):
            enabled = http.get_json("https://api.example.org/v1/search")
        logging.disable(logging.CRITICAL)
        try:
            with patch.object(http, "_opener", return_value=_SuccessOpener()):
                disabled = http.get_json("https://api.example.org/v1/search")
        finally:
            logging.disable(logging.NOTSET)
        self.assertEqual(enabled, disabled)

    @skip_unless_ffmpeg
    def test_probe_return_value_is_identical_with_logging_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.mp4"
            synth_video(src, duration=1)
            enabled = media.probe(src)
            logging.disable(logging.CRITICAL)
            try:
                disabled = media.probe(src)
            finally:
                logging.disable(logging.NOTSET)
        self.assertEqual(enabled, disabled)


if __name__ == "__main__":
    unittest.main()
