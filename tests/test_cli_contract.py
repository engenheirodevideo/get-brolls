"""Contrato do parser da CLI: `prog`, `--version` com a origem dos dados e erro de uso.

Erro de uso sai com código 2. Com stdout fora de um terminal (agente, script,
pipe), a mensagem vai em JSON para stderr; num terminal, sai em texto como a
do argparse, mais "Você quis dizer: …?" quando há um nome parecido.
"""

import contextlib
import io
import json
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV, WHEEL_MODE, wheel_origin

from getbrolls import __version__, _paths, cli


def run(*args):
    return subprocess.run(
        [*CLI_ARGV, *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def parse_in_process(argv, *, tty):
    """`build_parser().parse_args(argv)` com stdout (não) terminal; devolve (código, stderr)."""
    stderr = io.StringIO()
    with (
        mock.patch.object(cli.sys.stdout, "isatty", return_value=tty),
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
        self.assertEqual(["error", "error_code", "usage", "suggestion", "prog"], list(payload))
        self.assertEqual("USAGE_ERROR", payload["error_code"])
        self.assertEqual("search", payload["suggestion"])
        self.assertEqual("getbrolls", payload["prog"])
        self.assertTrue(payload["usage"].startswith("usage: getbrolls"), payload["usage"])
        self.assertIn("invalid choice: 'serch'", payload["error"])

    def test_unknown_flag_suggests_the_close_option(self):
        payload = self.usage_error("search", "--project", self.project, "--query", "x", "--limt", "3")
        self.assertEqual("unrecognized arguments: --limt 3", payload["error"])
        self.assertEqual("--limit", payload["suggestion"])

    def test_subcommand_choice_error_names_the_subcommand(self):
        payload = self.usage_error("plugins", "--action", "lst")
        self.assertEqual("getbrolls plugins", payload["prog"])
        self.assertTrue(payload["usage"].startswith("usage: getbrolls plugins"), payload["usage"])
        self.assertEqual("list", payload["suggestion"])

    def test_nothing_close_means_no_suggestion(self):
        payload = self.usage_error("zzzzzz", "--project", self.project)
        self.assertIsNone(payload["suggestion"])

    def test_missing_required_option_is_a_usage_error(self):
        payload = self.usage_error("search", "--project", self.project)
        self.assertIn("--query", payload["error"])
        self.assertIsNone(payload["suggestion"])


class UsageErrorTerminalTests(unittest.TestCase):
    def test_usage_error_on_a_tty_is_text_with_did_you_mean(self):
        code, stderr = parse_in_process(["serch"], tty=True)
        self.assertEqual(2, code)
        self.assertIn("usage: getbrolls", stderr)
        self.assertIn("getbrolls: error: ", stderr)
        self.assertTrue(stderr.endswith("Você quis dizer: search?\n"), stderr)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(stderr)

    def test_unknown_flag_on_a_tty_suggests_the_option(self):
        code, stderr = parse_in_process(["search", "--project", "p", "--query", "x", "--limt", "3"], tty=True)
        self.assertEqual(2, code)
        self.assertIn("unrecognized arguments: --limt 3", stderr)
        self.assertIn("Você quis dizer: --limit?", stderr)

    def test_tty_without_a_close_name_prints_no_suggestion(self):
        code, stderr = parse_in_process(["zzzzzz"], tty=True)
        self.assertEqual(2, code)
        self.assertNotIn("Você quis dizer", stderr)

    def test_off_a_tty_the_same_error_is_one_json_line(self):
        code, stderr = parse_in_process(["serch"], tty=False)
        self.assertEqual(2, code)
        self.assertEqual(1, len(stderr.strip().splitlines()))
        self.assertEqual("search", json.loads(stderr)["suggestion"])

    def test_a_broken_stdout_counts_as_not_a_terminal(self):
        with mock.patch.object(cli.sys.stdout, "isatty", side_effect=ValueError("I/O operation on closed file")):
            self.assertFalse(cli._stdout_is_tty())

    def test_a_stale_choice_error_does_not_leak_into_the_next_error(self):
        parser = cli.build_parser()
        with contextlib.suppress(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["serch"])
        stderr = io.StringIO()
        with (
            mock.patch.object(cli.sys.stdout, "isatty", return_value=False),
            contextlib.redirect_stderr(stderr),
            contextlib.suppress(SystemExit),
        ):
            parser.error("outra coisa")
        self.assertIsNone(json.loads(stderr.getvalue())["suggestion"])


if __name__ == "__main__":
    unittest.main()
