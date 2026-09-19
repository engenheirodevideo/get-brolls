"""Regression tests for the failure-visibility polish pass.

Covers: rules.py fail-closed on a corrupted GLOBAL RULES.md, http.py download()
no longer misclassifying programming bugs as provider errors, library.py hints()
surfacing a corrupted index instead of swallowing it, health.py live_checks()
distinguishing bug signatures from provider/network failures, POSIX permissions
on the private sources cache and the diagnostics log, and runtime.redact()'s
extended scrubbing (home dir, sensitive headers, key/token-style query values).
"""

import argparse
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import acquisition, health, http, library, runtime
from getbrolls.http import ProviderError

skip_on_windows = unittest.skipIf(os.name == "nt", "Bits POSIX de permissão não existem no Windows")


# --- Item 2: http.download() must not reclassify programming bugs -----------------


class DownloadDoesNotMisclassifyBugsTests(unittest.TestCase):
    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_key_error_propagates_instead_of_becoming_provider_error(self, builder, safe):
        builder.return_value.open.side_effect = KeyError("boom")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.mp4"
            with self.assertRaises(KeyError):
                http.download("https://example.org/video.mp4", target)
            self.assertFalse(target.exists())

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_type_error_propagates_instead_of_becoming_provider_error(self, builder, safe):
        builder.return_value.open.side_effect = TypeError("bad arg")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.mp4"
            with self.assertRaises(TypeError):
                http.download("https://example.org/video.mp4", target)

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_attribute_error_propagates_instead_of_becoming_provider_error(self, builder, safe):
        builder.return_value.open.side_effect = AttributeError("no such attr")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.mp4"
            with self.assertRaises(AttributeError):
                http.download("https://example.org/video.mp4", target)

    @patch.object(http, "_safe_network")
    @patch.object(http.urllib.request, "build_opener")
    def test_real_os_error_still_becomes_a_provider_error(self, builder, safe):
        # A genuine transport failure must keep working exactly as before.
        builder.return_value.open.side_effect = ConnectionResetError("reset by peer")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.mp4"
            with self.assertRaises(ProviderError) as ctx:
                http.download("https://example.org/video.mp4", target)
        self.assertIn("ConnectionResetError", str(ctx.exception))


# --- Item 3: library.hints() must surface a corrupted index, not swallow it -------


class LibraryHintsCorruptedIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for key in ("GB_HOME", "GB_LIBRARY"):
            old = os.environ.get(key)
            self.addCleanup(
                lambda k=key, v=old: os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
            )
            os.environ.pop(key, None)
        os.environ["GB_HOME"] = self.tmp.name

    def test_corrupted_index_returns_no_hints_but_records_a_warning_naming_the_file(self):
        index_path = library.index_path()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("not valid json {{{", encoding="utf-8")
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        try:
            result = library.hints("cats")
        finally:
            runtime.ACTIVE.reset(token)
        self.assertEqual(result, [])
        self.assertTrue(any(w["code"] == "LIBRARY_INDEX_UNREADABLE" for w in event["warnings"]))
        message = next(w["message"] for w in event["warnings"] if w["code"] == "LIBRARY_INDEX_UNREADABLE")
        self.assertIn(str(index_path), message)

    def test_a_healthy_missing_library_is_silent_as_before(self):
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        try:
            result = library.hints("cats")
        finally:
            runtime.ACTIVE.reset(token)
        self.assertEqual(result, [])
        self.assertEqual([], event["warnings"])


# --- Item 4: health.live_checks() must distinguish bugs from provider failures ----


class LiveChecksClassificationTests(unittest.TestCase):
    def setUp(self):
        for key in ("PEXELS_API_KEY", "PIXABAY_API_KEY"):
            os.environ.pop(key, None)

    def test_network_failure_detail_names_the_exception_class(self):
        def fake_search(provider, query, limit=1, media="any"):
            if provider == "commons":
                raise OSError("network down")
            return []

        with patch.object(health.providers, "search", side_effect=fake_search):
            result = health.live_checks()
        entry = next(c for c in result["checks"] if c["provider"] == "commons")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("OSError", entry["detail"])

    def test_bug_signature_is_reported_distinctly_from_a_provider_failure(self):
        def fake_search(provider, query, limit=1, media="any"):
            if provider == "nasa":
                raise KeyError("missing_field")
            return []

        with patch.object(health.providers, "search", side_effect=fake_search):
            result = health.live_checks()
        entry = next(c for c in result["checks"] if c["provider"] == "nasa")
        self.assertEqual(entry["status"], "failed")
        self.assertIn("KeyError", entry["detail"])
        self.assertIn("bug", entry["detail"].lower())
        # Must not read like the network/provider branch's wording (config/connectivity check).
        self.assertNotIn("Consulte configuração, conectividade", entry["detail"])

    def test_missing_key_is_not_tested_and_never_calls_search(self):
        with patch.object(health.providers, "search", return_value=[]) as search_mock:
            result = health.live_checks()
        pexels_entry = next(c for c in result["checks"] if c["provider"] == "pexels")
        self.assertEqual(pexels_entry["status"], "not_tested_missing_key")
        self.assertEqual(pexels_entry["env_key"], "PEXELS_API_KEY")
        called_providers = {call.args[0] for call in search_mock.call_args_list}
        self.assertNotIn("pexels", called_providers)

    def test_successful_search_reports_ok_for_every_reachable_provider(self):
        with patch.object(health.providers, "search", return_value=[]):
            result = health.live_checks()
        for entry in result["checks"]:
            if entry["status"] != "not_tested_missing_key":
                self.assertIn(entry["status"], ("search_ok", "search_ok_empty"))


# --- Item 5: POSIX permissions on the private sources cache and diagnostics log ---


class PrivateSourcesCachePermissionTests(unittest.TestCase):
    @skip_on_windows
    def test_cache_direct_media_creates_the_cache_dir_0700_and_the_file_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project" / "brolls"
            root.mkdir(parents=True)
            ledger = MagicMock()
            ledger.root = root
            candidate = {"id": "abc123", "media_url": "https://example.org/x.mp4"}
            with (
                patch("getbrolls.http.download") as download_mock,
                patch.object(acquisition, "probe", return_value={"duration_s": 1.0}),
                patch.object(acquisition, "digest", return_value="deadbeef"),
            ):
                download_mock.side_effect = lambda url, target: Path(target).write_bytes(b"x")
                acquisition.cache_direct_media(ledger, candidate, refresh=False)
            cache = root.parent / ".getbrolls-sources"
            self.assertEqual(0o700, stat.S_IMODE(cache.stat().st_mode))
            media_files = [p for p in cache.iterdir() if p.suffix == ".mp4"]
            self.assertEqual(1, len(media_files))
            self.assertEqual(0o600, stat.S_IMODE(media_files[0].stat().st_mode))

    @skip_on_windows
    def test_an_existing_looser_cache_dir_is_tightened_on_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project" / "brolls"
            root.mkdir(parents=True)
            cache = root.parent / ".getbrolls-sources"
            cache.mkdir(mode=0o755)
            cache.chmod(0o755)  # mkdir's mode= is masked by umask; force the loose bit
            self.assertEqual(0o755, stat.S_IMODE(cache.stat().st_mode))
            acquisition._ensure_private_cache_dir(cache)
            self.assertEqual(0o700, stat.S_IMODE(cache.stat().st_mode))


class DiagnosticsLogPermissionTests(unittest.TestCase):
    @skip_on_windows
    def test_diagnostics_log_is_created_0600(self):
        import argparse

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)
            args = argparse.Namespace(command="status", project=str(project))
            runtime.audited(args, lambda a: {"ok": True})
            log = project / "brolls" / "diagnostics.jsonl"
            self.assertTrue(log.is_file())
            self.assertEqual(0o600, stat.S_IMODE(log.stat().st_mode))

    @skip_on_windows
    def test_an_existing_looser_diagnostics_log_is_tightened(self):
        import argparse

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)
            log = project / "brolls" / "diagnostics.jsonl"
            log.write_text("", encoding="utf-8")
            log.chmod(0o644)
            self.assertEqual(0o644, stat.S_IMODE(log.stat().st_mode))
            args = argparse.Namespace(command="status", project=str(project))
            runtime.audited(args, lambda a: {"ok": True})
            self.assertEqual(0o600, stat.S_IMODE(log.stat().st_mode))


# --- Item 6: runtime.redact() extended scrubbing ----------------------------------


class RedactExtendedScrubbingTests(unittest.TestCase):
    def setUp(self):
        self.home = str(Path.home())

    def test_table(self):
        cases = [
            ("Authorization: Bearer sekret-token-xyz", "[REDACTED]", "sekret-token-xyz"),
            ("Cookie: session=sekret-cookie-abc", "[REDACTED]", "sekret-cookie-abc"),
            ("Set-Cookie: session=sekret-cookie-abc; Path=/", "[REDACTED]", "sekret-cookie-abc"),
            ("X-Api-Key: sekret-api-key-123", "[REDACTED]", "sekret-api-key-123"),
            ("falha ao chamar provedor com token=sekret-plain-token no corpo", "[REDACTED]", "sekret-plain-token"),
            ("assinatura sig=sekret-signature-value inválida", "[REDACTED]", "sekret-signature-value"),
            ("key=sekret-bare-key sem URL nenhuma", "[REDACTED]", "sekret-bare-key"),
        ]
        for text, expect_present, expect_absent in cases:
            with self.subTest(text=text):
                result = runtime.redact(text)
                self.assertIn(expect_present, result)
                if expect_absent:
                    self.assertNotIn(expect_absent, result)

    def test_existing_provider_key_and_url_redaction_still_works(self):
        with patch.dict(os.environ, {"PEXELS_API_KEY": "pexels-secret-key"}):
            result = runtime.redact("chave pexels-secret-key na URL https://example.org/x?key=1")
        self.assertNotIn("pexels-secret-key", result)
        self.assertNotIn("example.org", result)

    def test_idempotent(self):
        text = "Authorization: Bearer abc, https://example.org/x?token=def, key=ghi solto"
        once = runtime.redact(text)
        twice = runtime.redact(once)
        self.assertEqual(once, twice)

    def test_never_raises_on_weird_input(self):
        for value in (None, 123, b"bytes", ["a", "list"], {"a": 1}):
            with self.subTest(value=value):
                runtime.redact(value)  # must not raise

    def test_redact_leaves_paths_intact_so_the_message_stays_actionable(self):
        # The person (or the agent driving the CLI) copies paths out of error
        # messages; `~` inside quotes is not expanded by a shell.
        message = f"Cache gravado em {self.home}/.getbrolls-sources/clip.mp4"
        self.assertEqual(message, runtime.redact(message))

    def test_scrub_home_replaces_the_prefix_and_keeps_the_rest_legible(self):
        message = f"Cache gravado em {self.home}/.getbrolls-sources/clip.mp4"
        self.assertEqual("Cache gravado em ~/.getbrolls-sources/clip.mp4", runtime.scrub_home(message))

    def test_scrub_home_also_matches_a_json_escaped_prefix(self):
        home = "C:\\Users\\Fulano"
        with patch.object(runtime.Path, "home", return_value=Path(home)):
            line = json.dumps({"traceback": home + "\\proj\\x.py"})
            self.assertNotIn("Fulano", runtime.scrub_home(line))

    def test_scrub_home_never_raises(self):
        with patch.object(runtime.Path, "home", side_effect=RuntimeError):
            self.assertEqual("texto", runtime.scrub_home("texto"))
        for value in (None, 123, ["a"]):
            runtime.scrub_home(value)  # must not raise

    def test_the_error_envelope_keeps_real_paths_but_the_diagnostics_file_does_not(self):
        with tempfile.TemporaryDirectory(dir=self.home) as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)

            def boom(_args):
                raise ValueError(f"arquivo ausente em {project}/x.mp4")

            args = argparse.Namespace(project=str(project), command="fetch")
            with self.assertRaises(runtime.OperationError) as caught:
                runtime.audited(args, boom)
            self.assertIn(str(project), caught.exception.payload["message"])
            logged = (project / "brolls" / "diagnostics.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(self.home, logged)
            self.assertIn("~", logged)


if __name__ == "__main__":
    unittest.main()
