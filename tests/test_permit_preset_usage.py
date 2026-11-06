"""`permit --preset <desconhecido>` é erro de uso, como no 2.5.0 — nada do
projeto é aberto, travado ou registrado, e nenhum `brolls/` é criado.

Fora de um terminal (como aqui, com stdout capturado) o erro de uso sai em JSON
em stderr; `error_en` guarda a mensagem do argparse byte a byte e `error`, a mesma em pt-BR."""

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV
from test_sdk_loader import LoaderTestCase

from getbrolls.presets import PERMIT_PRESETS
from getbrolls.sdk import loader


def argparse_choice_error(name, choices):
    """A linha de erro que o parser do 2.5.0 (`--preset` com `choices=`) imprimia."""

    parser = argparse.ArgumentParser(prog="getbrolls")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("permit").add_argument("--preset", choices=choices)
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.suppress(SystemExit):
        parser.parse_args(["permit", "--preset", name])
    return stderr.getvalue().strip().splitlines()[-1].split("error: ", 1)[1]


def permit_stderr(folder, name, env=None):
    return subprocess.run(
        [*CLI_ARGV, "permit", "--project", str(folder), "--candidate", "x", "--preset", name],
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
        payload = json.loads(done.stderr)
        self.assertEqual("USAGE_ERROR", payload["error_code"])
        self.assertIn("usage: getbrolls permit", payload["usage"])
        self.assertIn("invalid choice: 'naoexiste'", payload["error_en"])
        self.assertIn("opção inválida: 'naoexiste'", payload["error"])
        self.assertEqual("", done.stdout)
        self.assertEqual([], sorted(p.name for p in folder.iterdir()))

    def test_message_is_byte_identical_to_the_argparse_choices_error(self):
        folder = Path(tempfile.mkdtemp(prefix="gb-preset-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        payload = json.loads(permit_stderr(folder, "naoexiste").stderr)
        self.assertEqual(argparse_choice_error("naoexiste", sorted(PERMIT_PRESETS)), payload["error_en"])


class UnknownPresetWithPluginTests(LoaderTestCase):
    def test_plugin_presets_join_the_quoted_list(self):
        self.install()
        loader.enable("demo", confirm=True)
        folder = Path(tempfile.mkdtemp(prefix="gb-preset-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        done = permit_stderr(folder, "naoexiste", env={"GB_HOME": str(self.home)})
        self.assertEqual(2, done.returncode)
        expected = argparse_choice_error("naoexiste", sorted({*PERMIT_PRESETS, "demo"}))
        self.assertEqual(expected, json.loads(done.stderr)["error_en"])
        self.assertIn("demo", expected)


if __name__ == "__main__":
    unittest.main()
