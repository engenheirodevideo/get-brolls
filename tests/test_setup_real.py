"""`setup` de verdade, com rede: só roda com `GB_REAL_SETUP_TESTS=1`.

Instala a venv do yt-dlp num `GB_HOME` temporário, confere que o yt-dlp responde, que a
segunda rodada não refaz nada e que `--upgrade ytdlp` registra a versão no marcador.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import cli

ENABLED = os.environ.get("GB_REAL_SETUP_TESTS") == "1"


def _setup(home, *args):
    env = {key: value for key, value in os.environ.items() if key not in ("GB_RUNTIME_DIR", "GB_YTDLP_PATH")}
    env.pop("GB_VENV_PATH", None)
    env["GB_HOME"] = str(home)
    return subprocess.run(
        cli("setup", *args), capture_output=True, text=True, encoding="utf-8", env=env, timeout=1800, check=False
    )


@unittest.skipUnless(ENABLED, "rede: defina GB_REAL_SETUP_TESTS=1 para rodar")
class RealSetupTests(unittest.TestCase):
    def test_real_setup_installs_runs_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            done = _setup(home)
            self.assertIn(done.returncode, (0, 4), done.stderr)
            venv = next(e for e in json.loads(done.stdout)["installed"] if e["part"] == "venv")
            self.assertEqual("installed", venv["status"], venv)
            folder = Path(venv["path"])
            self.assertTrue(folder.is_relative_to(home / "runtime"))
            ytdlp = next(p for p in (folder / "bin" / "yt-dlp", folder / "Scripts" / "yt-dlp.exe") if p.exists())
            probe = subprocess.run([str(ytdlp), "--version"], capture_output=True, timeout=120, check=False)
            self.assertEqual(0, probe.returncode)

            started = time.monotonic()
            again = _setup(home)
            self.assertLess(time.monotonic() - started, 15)
            venv = next(e for e in json.loads(again.stdout)["installed"] if e["part"] == "venv")
            self.assertEqual("already", venv["status"], venv)

            upgraded = _setup(home, "--upgrade", "ytdlp")
            self.assertIn(upgraded.returncode, (0, 4), upgraded.stderr)
            self.assertEqual("upgraded", json.loads(upgraded.stdout)["upgrade"]["status"])
            marker = json.loads((folder.parent / ".getbrolls-runtime-venv.json").read_text(encoding="utf-8"))
            self.assertEqual("ready", marker["status"])
            self.assertTrue(marker["upgraded"]["yt-dlp"])


if __name__ == "__main__":
    unittest.main()
