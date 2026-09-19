"""Audited diagnostics, the CLI JSON error envelope and command-level error surfacing."""

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import cli, commands, providers, runtime
from getbrolls.acquisition import prepare_source
from getbrolls.ledger import Ledger
from getbrolls.runtime import OperationError, audited


class InternalErrorClassificationTests(unittest.TestCase):
    def _args(self, project):
        return argparse.Namespace(command="status", project=str(project))

    def test_key_error_becomes_internal_error_with_diagnostics_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)

            def boom(args):
                raise KeyError("missing_field")

            with self.assertRaises(OperationError) as ctx:
                audited(self._args(project), boom)
            self.assertEqual(ctx.exception.payload["error_code"], "INTERNAL_ERROR")
            self.assertIn("diagnostics.jsonl", ctx.exception.payload["message"])
            log = project / "brolls" / "diagnostics.jsonl"
            self.assertTrue(log.is_file())
            last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last["type"], "KeyError")
            self.assertIn("traceback", last)
            self.assertIn("repr", last)

    def test_attribute_error_is_internal_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)

            def boom(args):
                raise AttributeError("no such attr")

            with self.assertRaises(OperationError) as ctx:
                audited(self._args(project), boom)
            self.assertEqual(ctx.exception.payload["error_code"], "INTERNAL_ERROR")

    def test_value_error_keeps_invalid_data_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)

            def boom(args):
                raise ValueError("bad input")

            with self.assertRaises(OperationError) as ctx:
                audited(self._args(project), boom)
            self.assertEqual(ctx.exception.payload["error_code"], "INVALID_DATA")
            self.assertIn("bad input", ctx.exception.payload["message"])


class CliEntrypointErrorEnvelopeTests(unittest.TestCase):
    def test_uncaught_exception_becomes_json_envelope_exit_3(self):
        with patch.object(cli, "main", side_effect=RuntimeError("kaboom")), patch("sys.stdout"):
            code = cli.entrypoint()
        self.assertEqual(code, 3)

    def test_envelope_is_valid_json_with_internal_error_code(self):
        buf = io.StringIO()
        with patch.object(cli, "main", side_effect=RuntimeError("kaboom")), patch("sys.stderr", buf):
            cli.entrypoint()
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["error_code"], "INTERNAL_ERROR")
        self.assertEqual(payload["type"], "RuntimeError")

    def test_uses_named_exit_code_constants(self):
        self.assertEqual(cli.EXIT_OPERATION_ERROR, 2)
        self.assertEqual(cli.EXIT_INTERNAL_ERROR, 3)
        with (
            patch.object(cli, "main", side_effect=RuntimeError("kaboom")),
            patch("sys.stdout"),
            patch("sys.stderr"),
        ):
            code = cli.entrypoint()
        self.assertEqual(code, cli.EXIT_INTERNAL_ERROR)

    def test_broken_pipe_does_not_become_internal_error(self):
        # #35: a downstream `| head` closing the pipe must not be reported as an
        # unexpected bug (INTERNAL_ERROR); it's an ordinary, expected shutdown.
        buf = io.StringIO()
        with (
            patch.object(cli, "main", side_effect=BrokenPipeError()),
            patch("sys.stdout"),
            patch("sys.stderr", buf),
        ):
            code = cli.entrypoint()
        self.assertNotEqual(code, cli.EXIT_INTERNAL_ERROR)
        self.assertNotIn("INTERNAL_ERROR", buf.getvalue())

    def test_generic_fallback_includes_redacted_traceback(self):
        buf = io.StringIO()
        with patch.object(cli, "main", side_effect=RuntimeError("kaboom")), patch("sys.stderr", buf):
            cli.entrypoint()
        payload = json.loads(buf.getvalue())
        self.assertIn("traceback", payload)
        self.assertIn("RuntimeError", payload["traceback"])

    def test_generic_fallback_writes_diagnostics_log_and_mentions_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)
            buf = io.StringIO()
            with (
                patch.object(cli, "main", side_effect=RuntimeError("kaboom")),
                patch("sys.stderr", buf),
                patch("sys.argv", ["gb", "status", "--project", str(project)]),
            ):
                cli.entrypoint()
            payload = json.loads(buf.getvalue())
            self.assertIn("diagnostics.jsonl", payload["error"])
            log = project / "brolls" / "diagnostics.jsonl"
            self.assertTrue(log.is_file())
            last = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(last["type"], "RuntimeError")
            self.assertIn("traceback", last)


def _search_args(project, query="cats", provider="pexels", limit=5, intent="illustrative"):
    return argparse.Namespace(
        command="search",
        project=str(project),
        provider=provider,
        query=query,
        limit=limit,
        intent=intent,
        env_file=None,
    )


class SearchAllProvidersFailTests(unittest.TestCase):
    def test_all_providers_failing_raises_human_readable_message_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            with (
                patch.object(providers, "search", side_effect=ValueError("chave ausente")),
                self.assertRaises(ValueError) as ctx,
            ):
                commands.execute(_search_args(project, provider="pexels"))
        message = str(ctx.exception)
        self.assertIn("pexels", message)
        self.assertIn("chave ausente", message)
        # Must not be a JSON blob (issue explicitly calls that out as the bug).
        self.assertFalse(message.strip().startswith("["))
        self.assertFalse(message.strip().startswith("{"))

    def test_provider_failure_records_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            event = {"warnings": [], "state_committed": False}
            token = runtime.ACTIVE.set(event)
            try:
                with (
                    patch.object(providers, "search", side_effect=ValueError("boom")),
                    self.assertRaises(ValueError),
                ):
                    commands.execute(_search_args(project, provider="pexels"))
            finally:
                runtime.ACTIVE.reset(token)
            self.assertTrue(any(w["code"] == "PROVIDER_FAILED" for w in event["warnings"]))


class UnknownAcquisitionMethodTests(unittest.TestCase):
    def test_unknown_method_message_names_the_method(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            ledger = Ledger(project)
            candidate = {
                "id": "abc",
                "provider": "commons",
                "acquisition": {"method": "some-exotic-method"},
                "source_url": "https://commons.wikimedia.org/x",
            }
            with self.assertRaises(ValueError) as ctx:
                prepare_source(ledger, candidate, 0, 1)
        self.assertIn("some-exotic-method", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
