"""getbrolls.log: the new logging foundation, on top of the untouched diagnostics.jsonl."""

import contextlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import CLI, ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import logs


def _raw_run(*args, project=None, env=None):
    """Like _cli.run_cli, but returns the whole CompletedProcess (no parsing/assert)."""
    command_args = list(args)
    if project is not None:
        command_args += ["--project", str(project)]
    environment = dict(os.environ)
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, str(CLI), *map(str, command_args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def _log_path(project):
    return Path(project).resolve() / "brolls" / "getbrolls.log"


class DefaultSettingsTests(unittest.TestCase):
    """(a) Default behavior: stdout/stderr shape unchanged, getbrolls.log gets written."""

    def test_success_and_failure_keep_the_stdout_stderr_contract_and_write_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok = _raw_run("init-rules", project=tmp)
            self.assertEqual(0, ok.returncode, ok.stderr)
            json.loads(ok.stdout)  # exactly one JSON document, nothing else on stdout
            self.assertEqual("", ok.stderr)

            # Same file exists already: --format without --force is refused (exit 2).
            failed = _raw_run("init-rules", "--format", "reels", project=tmp)
            self.assertEqual(2, failed.returncode)
            self.assertEqual("", failed.stdout)
            envelope = json.loads(failed.stderr)  # exactly one JSON document on stderr
            self.assertIn("error_code", envelope)

            log_file = _log_path(tmp)
            self.assertTrue(log_file.is_file())
            text = log_file.read_text(encoding="utf-8")
            self.assertIn("event=command_start", text)
            self.assertIn("event=command_end", text)
            self.assertIn("command=init-rules", text)


class VerboseStderrTests(unittest.TestCase):
    """(b) GB_LOG_LEVEL=DEBUG GB_LOG_STDERR=1: stdout purity holds, envelope stays last."""

    def test_stdout_still_exactly_one_json_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"GB_LOG_LEVEL": "DEBUG", "GB_LOG_STDERR": "1"}
            done = _raw_run("init-rules", project=tmp, env=env)
            self.assertEqual(0, done.returncode, done.stderr)
            json.loads(done.stdout)

    def test_error_envelope_is_the_last_stderr_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"GB_LOG_LEVEL": "DEBUG", "GB_LOG_STDERR": "1"}
            _raw_run("init-rules", project=tmp, env=env)
            failed = _raw_run("init-rules", "--format", "reels", project=tmp, env=env)
            self.assertEqual(2, failed.returncode)
            lines = [line for line in failed.stderr.splitlines() if line.strip()]
            self.assertGreater(len(lines), 1, "GB_LOG_STDERR should have added lines before the envelope")
            envelope = json.loads(lines[-1])
            self.assertIn("error_code", envelope)
            # Every earlier line is a log line, not JSON.
            for line in lines[:-1]:
                with self.assertRaises(json.JSONDecodeError):
                    json.loads(line)


class LogLevelOffTests(unittest.TestCase):
    """(c) GB_LOG_LEVEL=off disables the file entirely."""

    def test_no_log_file_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = _raw_run("init-rules", project=tmp, env={"GB_LOG_LEVEL": "off"})
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertFalse(_log_path(tmp).exists())


class InvalidLogLevelTests(unittest.TestCase):
    """(d) An invalid GB_LOG_LEVEL fails like any other invalid setting."""

    def test_invalid_level_produces_the_standard_invalid_data_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", project=tmp)
            payload = run_cli("status", project=tmp, env={"GB_LOG_LEVEL": "NONSENSE"}, expect=2)
            self.assertEqual("INVALID_DATA", payload.get("error_code"))
            self.assertIn("GB_LOG_LEVEL", payload.get("error", ""))


class ReadOnlyCommandTests(unittest.TestCase):
    """(e) A read-only command never creates anything, log included."""

    def test_status_on_a_folder_without_brolls_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = _raw_run("status", project=tmp)
            self.assertEqual(2, done.returncode)
            self.assertIn("Projeto não encontrado", done.stderr)
            self.assertEqual([], list(Path(tmp).iterdir()), "status criou arquivos")


class FileModeAndRotationTests(unittest.TestCase):
    """(f) 0600 on POSIX; rotation keeps up to 3 backups."""

    @unittest.skipIf(os.name == "nt", "permissões POSIX")
    def test_log_file_is_created_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = _raw_run("init-rules", project=tmp)
            self.assertEqual(0, done.returncode, done.stderr)
            mode = _log_path(tmp).stat().st_mode & 0o777
            self.assertEqual(0o600, mode)

    def test_rotation_keeps_three_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)
            try:
                with (
                    patch.object(logs, "MAX_BYTES", 400),
                    patch.dict(os.environ, {"GB_LOG_LEVEL": "DEBUG", "GB_LOG_STDERR": ""}, clear=False),
                ):
                    logs.configure(str(project), read_only=False)
                    logger = logs.get("rotation-test")
                    for i in range(500):
                        logs.event(logger, logging.INFO, "filler", i=i, pad="x" * 40)
                brolls = project / "brolls"
                self.assertTrue((brolls / "getbrolls.log").is_file())
                backups = [brolls / f"getbrolls.log.{n}" for n in (1, 2, 3)]
                self.assertTrue(all(p.is_file() for p in backups), backups)
                self.assertFalse((brolls / "getbrolls.log.4").exists())
            finally:
                logs._degrade()


class RedactionTests(unittest.TestCase):
    """(g, part 1) Table-driven: secrets never survive the filter, at DEBUG level."""

    def _filtered(self, message, env=None):
        logger = logging.getLogger("test-redaction-filter")
        record = logger.makeRecord("test-redaction-filter", logging.DEBUG, __file__, 0, message, (), None)
        with patch.dict(os.environ, env or {}, clear=False):
            logs.RedactingFilter().filter(record)
        return record.getMessage()

    def test_provider_key_is_redacted(self):
        out = self._filtered("using key=FAKE-PEXELS-KEY-123", env={"PEXELS_API_KEY": "FAKE-PEXELS-KEY-123"})
        self.assertNotIn("FAKE-PEXELS-KEY-123", out)

    def test_authorization_header_is_redacted(self):
        out = self._filtered("Authorization: Bearer faketoken-abc123")
        self.assertNotIn("faketoken-abc123", out)

    def test_cookie_header_is_redacted(self):
        out = self._filtered("Cookie: session=secret-cookie-987")
        self.assertNotIn("secret-cookie-987", out)

    def test_token_query_value_is_redacted(self):
        out = self._filtered("params token=super-secret-token&x=1")
        self.assertNotIn("super-secret-token", out)

    def test_sig_query_value_is_redacted(self):
        out = self._filtered("params sig=super-secret-sig")
        self.assertNotIn("super-secret-sig", out)

    def test_signed_cdn_url_is_redacted(self):
        out = self._filtered("fetched https://cdn.example.com/x.mp4?Expires=1&Signature=SUPERSECRETSIG")
        self.assertNotIn("SUPERSECRETSIG", out)
        self.assertNotIn("cdn.example.com", out)

    def test_home_directory_is_scrubbed(self):
        home = str(Path.home())
        out = self._filtered(f"path {home}/projeto/arquivo.mp4")
        self.assertNotIn(home, out)


class OptionValuesNeverLoggedTests(unittest.TestCase):
    """(g, part 2) Option NAMES may be logged; option VALUES never are."""

    def test_evidence_value_never_reaches_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", project=tmp)
            secret = "SEGREDO-EVIDENCIA-Q7F3"
            run_cli(
                "permit",
                "--candidate",
                "ghost-candidate",
                "--evidence",
                secret,
                project=tmp,
                env={"GB_LOG_LEVEL": "DEBUG"},
                expect=2,
            )
            text = _log_path(tmp).read_text(encoding="utf-8")
            self.assertNotIn(secret, text)

    def test_url_value_never_reaches_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", project=tmp)
            # A non-HTTPS URL is refused before any network I/O (`public_url()`).
            run_cli(
                "browser-plan",
                "--url",
                "http://example.com/SEGREDO-URL-Z9K2",
                project=tmp,
                env={"GB_LOG_LEVEL": "DEBUG"},
                expect=2,
            )
            text = _log_path(tmp).read_text(encoding="utf-8")
            self.assertNotIn("SEGREDO-URL-Z9K2", text)


class ConfigureRobustnessTests(unittest.TestCase):
    """(h) configure() is idempotent and never raises, even with a broken filesystem."""

    def test_configure_twice_does_not_duplicate_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)
            try:
                with patch.dict(os.environ, {"GB_LOG_LEVEL": "INFO", "GB_LOG_STDERR": ""}, clear=False):
                    logs.configure(str(project), read_only=False)
                    logs.configure(str(project), read_only=False)
                    logger = logs.get("idempotency-test")
                    logs.event(logger, logging.INFO, "probe", n=1)
                text = (project / "brolls" / "getbrolls.log").read_text(encoding="utf-8")
                self.assertEqual(1, text.count("event=probe"))
            finally:
                logs._degrade()

    @unittest.skipIf(os.name == "nt", "permissões POSIX")
    def test_unwritable_directory_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked"
            locked.mkdir()
            locked.chmod(0o500)
            try:
                logs.configure(str(locked), read_only=False)  # must not raise
                logger = logs.get("locked-test")
                logs.event(logger, logging.INFO, "probe")  # must not raise either
            finally:
                locked.chmod(0o700)
                logs._degrade()

    @unittest.skipIf(os.name == "nt", "permissões POSIX")
    def test_status_still_succeeds_when_the_project_is_unwritable(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            run_cli("init-rules", project=str(project))
            project.chmod(0o500)
            try:
                payload = run_cli("status", project=str(project), env={"GB_LOG_LEVEL": "DEBUG"})
                self.assertIn("summary", payload)
            finally:
                project.chmod(0o700)


class EventRenderingTests(unittest.TestCase):
    """(i) logs.event / render_fields rendering rules."""

    def test_none_becomes_dash(self):
        self.assertEqual("event=e a=-", logs.render_fields("e", {"a": None}))

    def test_plain_value_is_unquoted(self):
        self.assertEqual("event=e a=123", logs.render_fields("e", {"a": 123}))

    def test_value_with_space_is_quoted(self):
        self.assertEqual('event=e a="two words"', logs.render_fields("e", {"a": "two words"}))

    def test_value_with_equals_is_quoted(self):
        self.assertIn('a="x=y"', logs.render_fields("e", {"a": "x=y"}))

    def test_value_with_quote_is_escaped(self):
        rendered = logs.render_fields("e", {"a": 'say "hi"'})
        self.assertIn('\\"hi\\"', rendered)

    def test_long_value_is_truncated_to_500_chars(self):
        rendered = logs.render_fields("e", {"a": "x" * 600})
        value = rendered.split("a=", 1)[1]
        self.assertLessEqual(len(value), 500)

    def test_odd_type_never_raises(self):
        class Explode:
            def __str__(self):
                raise RuntimeError("boom")

        rendered = logs.render_fields("e", {"a": Explode()})
        self.assertIn("a=", rendered)

    def test_insertion_order_is_preserved(self):
        rendered = logs.render_fields("e", {"z": 1, "a": 2})
        self.assertLess(rendered.index("z="), rendered.index("a="))

    def test_event_never_raises_on_a_broken_logger(self):
        class BrokenLogger:
            def isEnabledFor(self, level):  # noqa: N802 - nome exigido pela API de logging.Logger
                raise RuntimeError("boom")

        logs.event(BrokenLogger(), logging.INFO, "e", a=1)  # must not raise


class TimedContextManagerTests(unittest.TestCase):
    def test_success_logs_info_with_duration(self):
        logger = logging.getLogger("test-timed-success")
        lines = []
        logger.addHandler(logging.Handler())
        with patch.object(logging.Handler, "emit", lambda self, record: lines.append(record.getMessage())):
            logger.setLevel(logging.DEBUG)
            with logs.timed(logger, "probe", extra="x"):
                pass
        self.assertEqual(1, len(lines))
        self.assertIn("event=probe", lines[0])
        self.assertIn("ms=", lines[0])
        self.assertIn("extra=x", lines[0])

    def test_failure_logs_warning_with_error_class_and_reraises(self):
        logger = logging.getLogger("test-timed-failure")
        lines = []
        logger.addHandler(logging.Handler())
        with patch.object(logging.Handler, "emit", lambda self, record: lines.append(record)):
            logger.setLevel(logging.DEBUG)
            with self.assertRaises(ValueError):
                with logs.timed(logger, "probe"):
                    raise ValueError("boom")
        self.assertEqual(1, len(lines))
        self.assertEqual(logging.WARNING, lines[0].levelno)
        self.assertIn("error=ValueError", lines[0].getMessage())
        self.assertIn("ms=", lines[0].getMessage())


class StatusExposesLogPathTests(unittest.TestCase):
    """(7) status gains an additive `log` key pointing at getbrolls.log."""

    def test_log_key_is_null_before_any_write_command_ran(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)
            payload = run_cli("status", project=str(project))
            self.assertIsNone(payload["log"])

    def test_log_key_points_at_the_file_once_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", project=tmp)
            payload = run_cli("status", project=tmp)
            self.assertEqual(str(_log_path(tmp)), payload["log"])


class ErrorEnvelopeAppLogTests(unittest.TestCase):
    """(7) the error envelope gains an additive `app_log`, `log` (diagnostics) untouched."""

    def test_app_log_present_alongside_diagnostics_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", project=tmp)
            payload = run_cli("init-rules", "--format", "reels", project=tmp, expect=2)
            self.assertEqual(str(Path(tmp).resolve() / "brolls" / "diagnostics.jsonl"), payload["log"])
            self.assertEqual(str(_log_path(tmp)), payload["app_log"])


class RecordWarningMirrorTests(unittest.TestCase):
    """(6) runtime.record_warning mirrors each warning as a WARNING log line."""

    def test_a_recorded_warning_appears_in_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", project=tmp)
            brolls = Path(tmp) / "brolls"
            for name in ("candidates", "previews", "clips"):
                (brolls / name).mkdir(parents=True, exist_ok=True)
            (brolls / ".pending-transaction.json").write_text(
                json.dumps({"data": {"schema_version": 1, "items": []}, "events": []}),
                encoding="utf-8",
            )
            # Any write command constructs Ledger(project) first, which finishes the
            # pending transaction and calls record_warning("RECOVERED_WRITE", ...) —
            # deterministic, no fixture/candidate setup needed. The command itself
            # still fails right after (unknown candidate), which is irrelevant here.
            run_cli(
                "permit",
                "--candidate",
                "ghost-candidate",
                "--evidence",
                "evidência sintética",
                project=tmp,
                env={"GB_LOG_LEVEL": "DEBUG"},
                expect=2,
            )
            text = _log_path(tmp).read_text(encoding="utf-8")
            self.assertIn("event=warning", text)
            self.assertIn("code=RECOVERED_WRITE", text)


class OptionNamesTests(unittest.TestCase):
    """Only real option names reach the log, whatever the values look like."""

    def test_a_value_that_looks_like_a_flag_is_not_logged_as_a_name(self):
        from getbrolls.cli import _given_option_names, parse_args

        # argparse only accepts a dash-leading value in the `--opt=value` form.
        argv = ["reject", "--candidate", "x", "--reason=--minha-senha-secreta", "--project", "p"]
        names = _given_option_names(argv, parse_args(argv))
        self.assertEqual("candidate,reason,project", names)
        self.assertNotIn("senha", names or "")

    def test_the_equals_form_keeps_the_name_and_drops_the_value(self):
        from getbrolls.cli import _given_option_names, parse_args

        argv = ["reject", "--candidate=x", "--reason=segredo", "--project=p"]
        names = _given_option_names(argv, parse_args(argv))
        self.assertEqual("candidate,reason,project", names)
        self.assertNotIn("segredo", names or "")


class ChildLoggerRedactionTests(unittest.TestCase):
    """Records from `getbrolls.<module>` children are redacted too, not only the root's."""

    def test_a_secret_logged_by_a_child_logger_never_reaches_the_file(self):
        import logging as stdlib_logging

        from getbrolls import logs

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_LOG_LEVEL": "DEBUG"}):
            try:
                logs.configure(tmp, read_only=False)
                child = logs.get("http")
                child.info("Authorization: Bearer child-secret-token https://example.org/x?sig=child-sig-value")
                logs.event(child, stdlib_logging.INFO, "probe", note="token=child-field-secret")
                for handler in stdlib_logging.getLogger("getbrolls").handlers:
                    handler.flush()
                written = (Path(tmp) / "brolls" / logs.LOG_FILENAME).read_text(encoding="utf-8")
            finally:
                logs.configure(None, read_only=True)
        self.assertIn("logger=getbrolls.http", written)
        for secret in ("child-secret-token", "child-sig-value", "child-field-secret", "example.org"):
            self.assertNotIn(secret, written)


class HandlersAreReleasedTests(unittest.TestCase):
    """The log file is not left open after a command: Windows cannot delete an open file."""

    def _file_handlers(self):
        return [h for h in logging.getLogger("getbrolls").handlers if isinstance(h, logging.FileHandler)]

    def test_an_in_process_command_leaves_no_open_file_handler_and_the_folder_can_be_removed(self):
        from getbrolls.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init-rules", "--project", tmp])
            self.assertTrue((Path(tmp) / "brolls" / logs.LOG_FILENAME).is_file())
            self.assertEqual([], self._file_handlers())
            shutil.rmtree(Path(tmp) / "brolls")

    def test_a_failing_command_releases_the_file_too(self):
        from getbrolls.cli import main
        from getbrolls.runtime import OperationError

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OperationError):
                main(["fetch", "--candidate", "nao-existe", "--project", tmp])
            self.assertEqual([], self._file_handlers())

    def test_the_next_command_logs_again_after_a_shutdown(self):
        from getbrolls.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init-rules", "--project", tmp])
                main(["init-rules", "--format", "reels", "--force", "--project", tmp])
            text = (Path(tmp) / "brolls" / logs.LOG_FILENAME).read_text(encoding="utf-8")
            self.assertEqual(2, text.count("event=command_start"))

    def test_shutdown_never_raises_and_is_idempotent(self):
        logs.shutdown()
        logs.shutdown()


if __name__ == "__main__":
    unittest.main()
