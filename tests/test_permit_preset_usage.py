"""A M1: `permit --preset <desconhecido>` é erro de uso, como no 2.5.0 — nada do
projeto é aberto, travado ou registrado, e nenhum `brolls/` é criado."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import CLI


class UnknownPresetTests(unittest.TestCase):
    def test_unknown_preset_is_a_usage_error_that_touches_nothing(self):
        folder = Path(tempfile.mkdtemp(prefix="gb-preset-"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        done = subprocess.run(
            [sys.executable, str(CLI), "permit", "--project", str(folder), "--candidate", "x", "--preset", "naoexiste"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ),
            check=False,
            timeout=120,
        )
        self.assertEqual(2, done.returncode)
        self.assertIn("usage:", done.stderr)
        self.assertIn("invalid choice: 'naoexiste'", done.stderr)
        self.assertEqual("", done.stdout)
        self.assertEqual([], sorted(p.name for p in folder.iterdir()))


if __name__ == "__main__":
    unittest.main()
