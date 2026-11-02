"""Audited diagnostics, the CLI JSON error envelope and command-level error surfacing."""

import argparse
import errno
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

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

    def test_index_error_is_an_audited_internal_error(self):
        # Uma exceção fora da lista que o `audited` conhecia (IndexError) deixava o evento preso
        # em "running" e o fallback do entrypoint gravava um segundo evento, sem `operation`.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)
            err = io.StringIO()
            with (
                patch.object(commands, "execute", side_effect=IndexError("list index out of range")),
                patch("sys.stderr", err),
                patch("sys.stdout", io.StringIO()),
                patch("sys.argv", ["getbrolls", "status", "--project", str(project)]),
            ):
                code = cli.entrypoint()
            self.assertEqual(cli.EXIT_INTERNAL_ERROR, code)
            payload = json.loads(err.getvalue().strip().splitlines()[-1])
            self.assertEqual("status", payload["operation"])
            self.assertEqual("error", payload["status"])
            self.assertEqual("INTERNAL_ERROR", payload["error_code"])
            self.assertEqual("IndexError", payload["type"])
            lines = (project / "brolls" / "diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(lines), lines)
            event = json.loads(lines[0])
            self.assertEqual("error", event["status"])
            self.assertEqual("status", event["operation"])
            self.assertEqual("INTERNAL_ERROR", event["error_code"])

    def _logged_path(self, payload):
        """O diagnostics.jsonl que a mensagem do erro interno cita, pela marca (`$GB_HOME/…`, `<projeto>/…`),
        de volta ao caminho do arquivo: o `log` do envelope é que leva o caminho absoluto."""
        found = re.search(r"\(([^()]*diagnostics\.jsonl)\)\.$", payload["message"])
        self.assertIsNotNone(found, payload["message"])
        assert found is not None
        self.assertEqual("$GB_HOME/diagnostics.jsonl", found[1])
        return str(Path(os.environ["GB_HOME"]) / "diagnostics.jsonl")

    def test_error_message_names_the_log_that_was_written(self):
        # `init` numa pasta nova: a pasta nunca chega a existir, e o evento vai para o
        # diagnostics.jsonl de $GB_HOME. A mensagem tem que citar esse arquivo, não um
        # `<projeto>/brolls/diagnostics.jsonl` que ninguém gravou.
        def boom(args):
            raise KeyError("x")

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_HOME": str(Path(tmp) / "home")}):
            project = Path(tmp) / "novo"
            with self.assertRaises(OperationError) as ctx:
                audited(argparse.Namespace(command="init", project=str(project)), boom)
            payload = ctx.exception.payload
            self.assertEqual(payload["log"], self._logged_path(payload))
            self.assertTrue(Path(payload["log"]).is_file())
            self.assertFalse(project.exists())

    def test_read_only_error_outside_a_project_tree_names_the_log_that_was_written(self):
        # `status` numa pasta sem `brolls/` nunca cria a árvore só para logar: o erro fica
        # no diagnostics.jsonl de $GB_HOME, e é esse que a mensagem cita.
        def boom(args):
            raise KeyError("x")

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_HOME": str(Path(tmp) / "home")}):
            project = Path(tmp) / "pasta"
            project.mkdir()
            with self.assertRaises(OperationError) as ctx:
                audited(self._args(project), boom)
            payload = ctx.exception.payload
            self.assertEqual(payload["log"], self._logged_path(payload))
            self.assertTrue(Path(payload["log"]).is_file())
            self.assertFalse((project / "brolls").exists())

    def test_traceback_keeps_the_frame_that_raised(self):
        # O fim do traceback (o frame que levantou e a linha `KeyError: …`) é o que importa
        # para achar o bug; um corte pelo começo do texto jogava justamente essa parte fora.
        def raise_from_the_deepest_frame():
            raise KeyError("campo_que_faltou")

        def descend(depth):
            if depth == 0:
                raise_from_the_deepest_frame()
            descend(depth - 1)

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            (project / "brolls").mkdir(parents=True)
            with self.assertRaises(OperationError):
                audited(self._args(project), lambda _args: descend(30))
            event = json.loads((project / "brolls" / "diagnostics.jsonl").read_text(encoding="utf-8"))
            self.assertIn("raise_from_the_deepest_frame", event["traceback"])
            self.assertTrue(event["traceback"].rstrip().endswith("KeyError: 'campo_que_faltou'"), event["traceback"])

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
        self.assertEqual(cli.EXIT_OPERATION_ERROR, 1)
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

    def _closed_pipe_run(self, error):
        """`entrypoint()` com stdout cujo write levanta `error`, como um pipe já fechado."""

        class ClosedPipe(io.StringIO):
            def write(self, s):
                raise error

        def ran(argv=None):
            cli._RESULT_EXIT.set(cli.EXIT_PREREQUISITE)  # pylint: disable=protected-access
            return {"ready": False}

        buf = io.StringIO()
        with patch.object(cli, "main", side_effect=ran), patch("sys.stdout", ClosedPipe()), patch("sys.stderr", buf):
            code = cli.entrypoint()
        return code, buf.getvalue()

    def test_windows_closed_pipe_einval_keeps_the_pending_exit(self):
        # No Windows o pipe fechado chega como `OSError: [Errno 22] Invalid argument`
        # (ERROR_NO_DATA), não como BrokenPipeError: é o mesmo `| head`, não um bug.
        code, stderr = self._closed_pipe_run(OSError(errno.EINVAL, "Invalid argument"))
        self.assertEqual(cli.EXIT_PREREQUISITE, code)
        self.assertNotIn("INTERNAL_ERROR", stderr)

    def test_epipe_oserror_on_write_keeps_the_pending_exit(self):
        code, stderr = self._closed_pipe_run(OSError(errno.EPIPE, "Broken pipe"))
        self.assertEqual(cli.EXIT_PREREQUISITE, code)
        self.assertNotIn("INTERNAL_ERROR", stderr)

    def test_other_oserror_on_write_is_still_internal_error(self):
        code, stderr = self._closed_pipe_run(OSError(errno.ENOSPC, "No space left on device"))
        self.assertEqual(cli.EXIT_INTERNAL_ERROR, code)
        self.assertIn("INTERNAL_ERROR", stderr)

    def test_einval_from_the_command_itself_is_still_internal_error(self):
        # Só a escrita do resultado em stdout é tratada como pipe fechado.
        buf = io.StringIO()
        with (
            patch.object(cli, "main", side_effect=OSError(errno.EINVAL, "Invalid argument")),
            patch("sys.stdout", io.StringIO()),
            patch("sys.stderr", buf),
        ):
            code = cli.entrypoint()
        self.assertEqual(cli.EXIT_INTERNAL_ERROR, code)
        self.assertIn("INTERNAL_ERROR", buf.getvalue())

    def test_generic_fallback_keeps_the_traceback_out_of_stderr(self):
        with tempfile.TemporaryDirectory() as home:
            buf = io.StringIO()
            with (
                patch.dict(os.environ, {"GB_HOME": home}),
                patch.object(cli, "main", side_effect=RuntimeError("kaboom")),
                patch("sys.stderr", buf),
                patch("sys.argv", ["gb", "providers"]),
            ):
                cli.entrypoint()
            payload = json.loads(buf.getvalue())
            self.assertNotIn("traceback", payload)
            self.assertNotIn("repr", payload)
            event = json.loads((Path(home) / "diagnostics.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            self.assertIn("RuntimeError", event["traceback"])

    def test_generic_fallback_never_creates_a_project_tree_to_keep_the_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "pasta"
            folder.mkdir()
            home = Path(tmp) / "home"
            buf = io.StringIO()
            with (
                patch.dict(os.environ, {"GB_HOME": str(home)}),
                patch.object(cli, "main", side_effect=RuntimeError("kaboom")),
                patch("sys.stderr", buf),
                patch("sys.argv", ["gb", "status", "--project", str(folder)]),
            ):
                cli.entrypoint()
            payload = json.loads(buf.getvalue())
            self.assertFalse((folder / "brolls").exists())
            self.assertEqual(str(home / "diagnostics.jsonl"), payload["log"])
            self.assertTrue((home / "diagnostics.jsonl").is_file())

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
