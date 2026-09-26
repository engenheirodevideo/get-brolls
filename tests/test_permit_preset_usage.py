"""`permit --preset <desconhecido>` é erro de uso, como no 2.5.0 — nada do
projeto é aberto, travado ou registrado, e nenhum `brolls/` é criado."""

import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI
from test_sdk_loader import LoaderTestCase

from getbrolls.presets import PERMIT_PRESETS
from getbrolls.sdk import loader


def argparse_choice_error(name, choices):
    """A linha de erro que o parser do 2.5.0 (`--preset` com `choices=`) imprimia."""

    parser = argparse.ArgumentParser(prog="gb.py")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("permit").add_argument("--preset", choices=choices)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.suppress(SystemExit):
        parser.parse_args(["permit", "--preset", name])
    return stderr.getvalue().strip().splitlines()[-1].split("error: ", 1)[1]


def permit_stderr(folder, name, env=None):
    return subprocess.run(
        [sys.executable, str(CLI), "permit", "--project", str(folder), "--candidate", "x", "--preset", name],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **(env or {})},
        check=False,
        timeout=120,
    )


class UnknownPresetTests(unittest.TestCase):
    def test_unknown_preset_is_a_usage_error_that_touches_nothing(self):
        folder = Path(tempfile.mkdtemp(prefix="gb-preset-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        done = permit_stderr(folder, "naoexiste")
        self.assertEqual(2, done.returncode)
        self.assertIn("usage:", done.stderr)
        self.assertIn("invalid choice: 'naoexiste'", done.stderr)
        self.assertEqual("", done.stdout)
        self.assertEqual([], sorted(p.name for p in folder.iterdir()))

    def test_message_is_byte_identical_to_the_argparse_choices_error(self):
        folder = Path(tempfile.mkdtemp(prefix="gb-preset-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        last = permit_stderr(folder, "naoexiste").stderr.strip().splitlines()[-1]
        self.assertEqual(argparse_choice_error("naoexiste", sorted(PERMIT_PRESETS)), last.split("error: ", 1)[1])


class UnknownPresetWithPluginTests(LoaderTestCase):
    def test_plugin_presets_join_the_quoted_list(self):
        self.install()
        loader.enable("demo", confirm=True)
        folder = Path(tempfile.mkdtemp(prefix="gb-preset-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        done = permit_stderr(folder, "naoexiste", env={"GB_HOME": str(self.home)})
        self.assertEqual(2, done.returncode)
        expected = argparse_choice_error("naoexiste", sorted({*PERMIT_PRESETS, "demo"}))
        self.assertEqual(expected, done.stderr.strip().splitlines()[-1].split("error: ", 1)[1])
        self.assertIn("'demo'", expected)


if __name__ == "__main__":
    unittest.main()
