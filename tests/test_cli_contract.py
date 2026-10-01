"""Contrato da CLI: `prog`, `--version`, erro de uso e códigos de saída.

Erro de uso sai com código 2. O erro vai para stderr: com stderr fora de um
terminal (agente, script, `2>` para arquivo), sai em JSON; num terminal, sai em
texto como o do argparse, mais "Você quis dizer: …?" quando há um nome parecido.

Códigos: 0 ok, 1 operação/dados, 2 uso, 3 interno, 4 pré-requisito, 130
interrompido. Nenhum erro mostra traceback nem repr para a pessoa: os detalhes
ficam em `brolls/diagnostics.jsonl` (ou `$GB_HOME/diagnostics.jsonl` sem projeto).
"""

import argparse
import contextlib
import io
import json
import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV, WHEEL_MODE, wheel_origin

from getbrolls import __version__, _paths, cli, commands, runtime
from getbrolls.errors import DataRootError, PrerequisiteError, UsageError
from getbrolls.runtime import OperationError


def run(*args):
    return subprocess.run(
        [*CLI_ARGV, *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


class _Stderr(io.StringIO):
    """stderr em memória que diz se é terminal."""

    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


def parse_in_process(argv, *, tty, stdout_tty=None):
    """`build_parser().parse_args(argv)` com stderr (não) terminal; devolve (código, stderr).

    `stdout_tty` (padrão: o contrário de `tty`) prova que só o stderr decide o formato.
    """
    stderr = _Stderr(tty)
    with (
        mock.patch.object(cli.sys.stdout, "isatty", return_value=(not tty) if stdout_tty is None else stdout_tty),
        contextlib.redirect_stderr(stderr),
    ):
        try:
            cli.build_parser().parse_args(argv)
        except SystemExit as exc:
            return exc.code, stderr.getvalue()
    raise AssertionError(f"parse_args({argv!r}) não saiu")


class ProgTests(unittest.TestCase):
    def test_prog_is_getbrolls(self):
        self.assertEqual("getbrolls", cli.build_parser().prog)
        self.assertEqual("getbrolls search", cli._SUBPARSERS["search"].prog)


class VersionTests(unittest.TestCase):
    def test_version_names_python_and_the_data_origin(self):
        done = run("--version")
        self.assertEqual(0, done.returncode, done.stderr)
        line = done.stdout.strip()
        self.assertTrue(line.startswith(f"getbrolls {__version__} (Python "), line)
        expected = "wheel" if WHEEL_MODE else "checkout"
        self.assertIn(f"dados: {expected})", line)
        self.assertEqual(expected, wheel_origin(line))

    def test_version_line_uses_this_install(self):
        with mock.patch.object(_paths, "origin", return_value="wheel"):
            self.assertEqual(
                f"getbrolls {__version__} (Python {platform.python_version()}; dados: wheel)", cli.version_line()
            )

    def test_version_line_without_data_says_they_are_missing(self):
        with mock.patch.object(_paths, "origin", return_value="unknown"):
            line = cli.version_line()
        self.assertTrue(line.endswith("; dados: ausentes)"), line)
        self.assertIsNone(wheel_origin(line))


class UsageErrorJsonTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-usage-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def usage_error(self, *args):
        done = run(*args)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual("", done.stdout)
        self.assertEqual([], list(self.project.iterdir()))
        return json.loads(done.stderr)

    def test_unknown_command_is_json_usage_error_with_suggestion(self):
        payload = self.usage_error("serch", "--project", self.project)
        self.assertEqual(["error", "error_en", "error_code", "usage", "suggestion", "prog"], list(payload))
        self.assertEqual("USAGE_ERROR", payload["error_code"])
        self.assertEqual("search", payload["suggestion"])
        self.assertEqual("getbrolls", payload["prog"])
        self.assertTrue(payload["usage"].startswith("usage: getbrolls"), payload["usage"])
        self.assertIn("invalid choice: 'serch'", payload["error_en"])
        self.assertIn("opção inválida: 'serch' (escolha entre ", payload["error"])

    def test_unknown_flag_suggests_the_close_option(self):
        payload = self.usage_error("search", "--project", self.project, "--query", "x", "--limt", "3")
        self.assertEqual("unrecognized arguments: --limt 3", payload["error_en"])
        self.assertEqual("argumentos não reconhecidos: --limt 3", payload["error"])
        self.assertEqual("--limit", payload["suggestion"])

    def test_subcommand_choice_error_names_the_subcommand(self):
        payload = self.usage_error("plugins", "--action", "lst")
        self.assertEqual("getbrolls plugins", payload["prog"])
        self.assertTrue(payload["usage"].startswith("usage: getbrolls plugins"), payload["usage"])
        self.assertEqual("list", payload["suggestion"])

    def test_nothing_close_means_no_suggestion(self):
        payload = self.usage_error("zzzzzz", "--project", self.project)
        self.assertIsNone(payload["suggestion"])

    def test_misspelled_required_flag_suggests_the_flag(self):
        payload = self.usage_error("status", "--projct", self.project)
        self.assertEqual("the following arguments are required: --project", payload["error_en"])
        self.assertEqual("faltam argumentos obrigatórios: --project", payload["error"])
        self.assertEqual("--project", payload["suggestion"])

    def test_flag_of_another_command_suggests_the_missing_required_one(self):
        payload = self.usage_error("library", "--query", "x")
        self.assertIn("--search", payload["error"])
        self.assertEqual("--search", payload["suggestion"])

    def test_common_argparse_messages_are_pt_br(self):
        payload = self.usage_error("search", "--project", self.project, "--query")
        self.assertEqual("argumento --query: falta o valor (esperava um)", payload["error"])
        self.assertEqual("argument --query: expected one argument", payload["error_en"])
        payload = self.usage_error("inspect", "--project", self.project)
        self.assertTrue(payload["error"].startswith("um destes argumentos é obrigatório: "), payload)
        payload = self.usage_error("inspect", "--project", self.project, "--candidate", "a", "--url", "b")
        self.assertIn("não vale junto com", payload["error"])
        self.assertEqual("mensagem nova do argparse", cli.usage_pt_br("mensagem nova do argparse"))

    def test_missing_required_option_is_a_usage_error(self):
        payload = self.usage_error("search", "--project", self.project)
        self.assertIn("--query", payload["error"])
        self.assertIsNone(payload["suggestion"])


class UsageErrorTerminalTests(unittest.TestCase):
    def test_usage_error_on_a_tty_is_text_with_did_you_mean(self):
        code, stderr = parse_in_process(["serch"], tty=True)
        self.assertEqual(2, code)
        self.assertIn("usage: getbrolls", stderr)
        self.assertIn("getbrolls: erro: argumento command: opção inválida: 'serch'", stderr)
        self.assertTrue(stderr.endswith("Você quis dizer: search?\n"), stderr)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(stderr)

    def test_unknown_flag_on_a_tty_suggests_the_option(self):
        code, stderr = parse_in_process(["search", "--project", "p", "--query", "x", "--limt", "3"], tty=True)
        self.assertEqual(2, code)
        self.assertIn("argumentos não reconhecidos: --limt 3", stderr)
        self.assertIn("Você quis dizer: --limit?", stderr)

    def test_misspelled_required_flag_on_a_tty_suggests_the_flag(self):
        code, stderr = parse_in_process(["status", "--projct", "x"], tty=True)
        self.assertEqual(2, code)
        self.assertIn("faltam argumentos obrigatórios: --project", stderr)
        self.assertTrue(stderr.endswith("Você quis dizer: --project?\n"), stderr)

    def test_tty_without_a_close_name_prints_no_suggestion(self):
        code, stderr = parse_in_process(["zzzzzz"], tty=True)
        self.assertEqual(2, code)
        self.assertNotIn("Você quis dizer", stderr)

    def test_off_a_tty_the_same_error_is_one_json_line(self):
        code, stderr = parse_in_process(["serch"], tty=False)
        self.assertEqual(2, code)
        self.assertEqual(1, len(stderr.strip().splitlines()))
        self.assertEqual("search", json.loads(stderr)["suggestion"])

    def test_a_broken_stderr_counts_as_not_a_terminal(self):
        with mock.patch.object(cli.sys.stderr, "isatty", side_effect=ValueError("I/O operation on closed file")):
            self.assertFalse(cli._stderr_is_tty())

    def test_stderr_to_a_file_is_json_even_with_stdout_on_a_terminal(self):
        code, stderr = parse_in_process(["serch"], tty=False, stdout_tty=True)
        self.assertEqual(2, code)
        self.assertEqual("search", json.loads(stderr)["suggestion"])

    def test_stderr_on_a_terminal_is_text_even_with_stdout_piped(self):
        code, stderr = parse_in_process(["serch"], tty=True, stdout_tty=False)
        self.assertEqual(2, code)
        self.assertIn("Você quis dizer: search?", stderr)

    def test_a_stale_choice_error_does_not_leak_into_the_next_error(self):
        parser = cli.build_parser()
        with contextlib.suppress(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["serch"])
        stderr = _Stderr(False)
        with (
            contextlib.redirect_stderr(stderr),
            contextlib.suppress(SystemExit),
        ):
            parser.error("outra coisa")
        self.assertIsNone(json.loads(stderr.getvalue())["suggestion"])


TRACEBACK = "Traceback (most recent call last)"


def entry(argv, patches=()):
    """Roda `cli.entrypoint()` com `sys.argv` = argv e os `(objeto, nome, kwargs)` de
    `patches` aplicados; devolve (código, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(cli.sys, "argv", ["getbrolls", *map(str, argv)]))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        for obj, name, kwargs in patches:
            stack.enter_context(mock.patch.object(obj, name, **kwargs))
        code = cli.entrypoint()
    return code, out.getvalue(), err.getvalue()


def main_raising(exc):
    return [(cli, "main", {"side_effect": exc})]


class ExitCodeTableTests(unittest.TestCase):
    def test_exit_code_constants_and_table(self):
        self.assertEqual({0, 1, 2, 3, 4, 130}, {code for code, *_ in cli.EXIT_CODES})
        self.assertEqual(
            (0, 1, 2, 3, 4, 130),
            (
                cli.EXIT_OK,
                cli.EXIT_OPERATION_ERROR,
                cli.EXIT_USAGE_ERROR,
                cli.EXIT_INTERNAL_ERROR,
                cli.EXIT_PREREQUISITE,
                cli.EXIT_INTERRUPTED,
            ),
        )
        self.assertLessEqual(set(runtime.ERROR_EXIT.values()), {code for code, *_ in cli.EXIT_CODES})

    def test_error_code_maps_to_exit_code_in_one_place(self):
        self.assertEqual(2, runtime.exit_code_for("USAGE_ERROR"))
        self.assertEqual(3, runtime.exit_code_for("INTERNAL_ERROR"))
        self.assertEqual(4, runtime.exit_code_for("PREREQUISITE_MISSING"))
        self.assertEqual(130, runtime.exit_code_for("INTERRUPTED"))
        for other in ("INVALID_DATA", "IO_ERROR", None, "ALGO_NOVO"):
            self.assertEqual(1, runtime.exit_code_for(other))

    def test_exception_classes_map_to_error_codes(self):
        self.assertEqual("USAGE_ERROR", runtime.error_code_for(UsageError("x")))
        self.assertEqual("PREREQUISITE_MISSING", runtime.error_code_for(PrerequisiteError("x")))
        self.assertEqual("PREREQUISITE_MISSING", runtime.error_code_for(DataRootError("x")))
        self.assertEqual("IO_ERROR", runtime.error_code_for(OSError("x")))
        self.assertEqual("INVALID_DATA", runtime.error_code_for(ValueError("x")))


class OperationExitTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-exit-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_operation_error_exits_1_without_traceback(self):
        done = run("fetch", "--candidate", "nope", "--project", self.project)
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertEqual("", done.stdout)
        self.assertNotIn(TRACEBACK, done.stderr)
        self.assertEqual(1, len(done.stderr.strip().splitlines()), done.stderr)
        payload = json.loads(done.stderr)
        self.assertEqual("INVALID_DATA", payload["error_code"])
        self.assertNotIn("traceback", payload)
        self.assertNotIn("repr", payload)
        # Sem gravação pendente nem estado gravado, a dica de recovery/review seria ruído.
        self.assertIs(False, payload["recovery_pending"])
        self.assertNotIn("hint", payload)

    def test_the_traceback_still_reaches_the_project_diagnostics(self):
        done = run("fetch", "--candidate", "nope", "--project", self.project)
        self.assertEqual(1, done.returncode, done.stderr)
        lines = (self.project / "brolls" / "diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[-1])
        self.assertIn(TRACEBACK, event["traceback"])
        self.assertIn("repr", event)

    def test_env_file_missing_is_usage_error_exit_2(self):
        done = run("--env-file", self.project / "nao", "providers")
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual("USAGE_ERROR", json.loads(done.stderr)["error_code"])

    def test_gb_env_file_inside_an_env_file_exits_2(self):
        env_file = self.project / "custom.env"
        env_file.write_text(f"GB_ENV_FILE={env_file}\n", encoding="utf-8")
        done = run("--env-file", env_file, "providers")
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual("USAGE_ERROR", json.loads(done.stderr)["error_code"])
        self.assertNotIn(TRACEBACK, done.stderr)

    def test_foreground_serve_error_keeps_stdout_and_exits_1(self):
        done = run("serve", "--project", self.project)
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        self.assertNotIn(TRACEBACK, done.stdout + done.stderr)
        payload = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual("INVALID_DATA", payload["error_code"])

    def test_broken_pipe_exits_0_without_traceback(self):
        with subprocess.Popen(
            [*CLI_ARGV, "providers"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as proc:
            assert proc.stdout is not None
            proc.stdout.close()  # `| head -0`: o leitor fecha antes da primeira escrita
            _, stderr = proc.communicate(timeout=120)
        self.assertEqual(0, proc.returncode, stderr)
        self.assertNotIn(TRACEBACK, stderr)
        self.assertNotIn("BrokenPipeError", stderr)

    def test_broken_pipe_keeps_the_pending_result_exit(self):
        """`doctor` com `ready: false` num pipe fechado sai 4, não 0: o veredito não some com o pipe."""
        env = {**os.environ, "GB_FFMPEG_PATH": str(self.project / "sem-ffmpeg")}
        with subprocess.Popen(
            [*CLI_ARGV, "doctor"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        ) as proc:
            assert proc.stdout is not None
            proc.stdout.close()
            _, stderr = proc.communicate(timeout=120)
        self.assertEqual(4, proc.returncode, stderr)
        self.assertNotIn(TRACEBACK, stderr)
        self.assertNotIn("BrokenPipeError", stderr)


class ErrorHintTests(unittest.TestCase):
    """A dica do erro só aparece quando há o que retomar ou regenerar."""

    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-hint-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def failure(self, command="resolve", commit=False, pending=False):
        def execute(_args):
            if pending:
                (self.project / "brolls").mkdir(exist_ok=True)
                (self.project / "brolls" / ".pending-transaction.json").write_text("{}", encoding="utf-8")
            if commit:
                runtime.record_commit()
            raise ValueError("falhou")

        with self.assertRaises(OperationError) as caught:
            runtime.audited(argparse.Namespace(command=command, project=str(self.project)), execute)
        return caught.exception.payload

    def test_no_hint_without_pending_recovery_or_committed_state(self):
        self.assertNotIn("hint", self.failure())

    def test_pending_recovery_gets_the_recovery_hint(self):
        payload = self.failure(pending=True)
        self.assertIs(True, payload["recovery_pending"])
        self.assertIn("retoma", payload["hint"])
        self.assertNotIn("Se recovery_pending", payload["hint"])

    def test_committed_state_gets_the_review_hint(self):
        payload = self.failure(commit=True)
        self.assertIn("review", payload["hint"])
        self.assertNotIn("recovery_pending", payload["hint"])

    def test_roteiro_never_gets_the_review_hint(self):
        self.assertNotIn("hint", self.failure(command="roteiro", commit=True))


class InProcessExitTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gb-home-exit-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {"GB_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_prerequisite_error_exits_4(self):
        failure = OperationError({"error_code": "PREREQUISITE_MISSING", "message": "x"})
        code, out, err = entry(["status"], main_raising(failure))
        self.assertEqual(4, code)
        self.assertEqual("", out)
        self.assertEqual("PREREQUISITE_MISSING", json.loads(err)["error_code"])

    def test_prerequisite_error_outside_the_audit_exits_4(self):
        code, _, err = entry(["status"], main_raising(DataRootError("Faltam os dados; reinstale.")))
        self.assertEqual(4, code)
        payload = json.loads(err)
        self.assertEqual("PREREQUISITE_MISSING", payload["error_code"])
        self.assertIn("reinstale", payload["error"])
        self.assertNotIn(TRACEBACK, err)

    def test_usage_error_outside_the_audit_exits_2(self):
        code, _, err = entry(["status"], main_raising(UsageError("flag errada")))
        self.assertEqual(2, code)
        self.assertEqual("USAGE_ERROR", json.loads(err)["error_code"])

    def test_missing_data_file_exits_4(self):
        unknown = _paths.detect(Path(tempfile.gettempdir()) / "nada" / "getbrolls")
        project = self.home / "proj"
        project.mkdir()
        with mock.patch.object(_paths, "install", return_value=unknown):
            code, out, err = entry(["init-brief", "--project", project])
        self.assertEqual(4, code, err)
        self.assertEqual("", out)
        payload = json.loads(err)
        self.assertEqual("PREREQUISITE_MISSING", payload["error_code"])
        self.assertNotIn("traceback", payload)
        self.assertNotIn(TRACEBACK, err)

    def test_keyboard_interrupt_exits_130(self):
        code, _, err = entry(["status"], main_raising(KeyboardInterrupt()))
        self.assertEqual(130, code)
        self.assertEqual("INTERRUPTED", json.loads(err)["error_code"])
        failure = OperationError({"error_code": "INTERRUPTED", "message": "x"})
        code, _, err = entry(["status"], main_raising(failure))
        self.assertEqual(130, code)

    def test_ctrl_c_during_a_command_exits_130(self):
        project = self.home / "proj"
        project.mkdir()
        interrupt = [(commands, "execute", {"side_effect": KeyboardInterrupt()})]
        code, out, err = entry(["status", "--project", project], interrupt)
        self.assertEqual(130, code, err)
        self.assertEqual("", out)
        payload = json.loads(err)
        self.assertEqual("INTERRUPTED", payload["error_code"])
        self.assertNotIn(TRACEBACK, err)

    def test_internal_error_without_project_logs_to_gb_home(self):
        code, _, err = entry(["providers"], main_raising(RuntimeError("kaboom")))
        self.assertEqual(3, code)
        payload = json.loads(err)
        self.assertEqual("INTERNAL_ERROR", payload["error_code"])
        self.assertEqual("RuntimeError", payload["type"])
        self.assertIn("[type: RuntimeError]", payload["error"])
        for key in ("traceback", "repr", "message"):
            self.assertNotIn(key, payload)
        self.assertNotIn("kaboom", err)
        log = self.home / "diagnostics.jsonl"
        self.assertEqual(str(log), payload["log"])
        event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertIn("RuntimeError", event["traceback"])

    def test_audited_error_without_project_logs_to_gb_home(self):
        args = argparse.Namespace(command="providers", project=None)

        def boom(_args):
            raise ValueError("ruim")

        with self.assertRaises(OperationError) as caught:
            runtime.audited(args, boom)
        payload = caught.exception.payload
        self.assertNotIn("traceback", payload)
        self.assertNotIn("repr", payload)
        log = self.home / "diagnostics.jsonl"
        self.assertEqual(str(log), payload["log"])
        event = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        self.assertIn("ValueError", event["traceback"])

    def test_successful_command_without_project_logs_nothing(self):
        args = argparse.Namespace(command="providers", project=None)
        self.assertEqual({}, runtime.audited(args, lambda _args: {}))
        self.assertFalse((self.home / "diagnostics.jsonl").exists())

    def test_internal_error_message_names_the_type_not_the_repr(self):
        args = argparse.Namespace(command="providers", project=None)

        def boom(_args):
            raise KeyError("segredo")

        with self.assertRaises(OperationError) as caught:
            runtime.audited(args, boom)
        payload = caught.exception.payload
        self.assertEqual("INTERNAL_ERROR", payload["error_code"])
        self.assertIn("[type: KeyError]", payload["message"])
        self.assertNotIn("<class", payload["message"])
        self.assertEqual(3, runtime.exit_code_for(payload["error_code"]))

    def test_usage_and_prerequisite_errors_inside_the_audit(self):
        args = argparse.Namespace(command="providers", project=None)
        for exc, code in ((UsageError("x"), "USAGE_ERROR"), (DataRootError("x"), "PREREQUISITE_MISSING")):
            with self.subTest(exc=type(exc).__name__), self.assertRaises(OperationError) as caught:
                runtime.audited(args, mock.Mock(side_effect=exc))
            self.assertEqual(code, caught.exception.payload["error_code"])

    def test_doctor_result_exit_is_command_scoped(self):
        self.assertEqual(4, cli.result_exit("doctor", {"ready": False}))
        self.assertEqual(0, cli.result_exit("doctor", {"ready": True}))
        self.assertEqual(0, cli.result_exit("x", {"ready": False}))
        self.assertEqual(0, cli.result_exit("doctor", None))
        code, out, _ = entry(["x", "p", "c"], [(cli, "main", {"return_value": {"ready": False}})])
        self.assertEqual(0, code)
        self.assertEqual({"ready": False}, json.loads(out))


if __name__ == "__main__":
    unittest.main()
