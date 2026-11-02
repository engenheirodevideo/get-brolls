"""`setup` de verdade, com rede: só roda com `GB_REAL_SETUP_TESTS=1`.

Instala a venv do yt-dlp e o Playwright CLI (quando há npm) num `GB_HOME` temporário,
confere que os dois respondem, que a segunda rodada não refaz nada, que `--where` aponta
para o runtime e que `--upgrade ytdlp` registra a versão no marcador.
"""

import json
import os
import shutil
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
    def check_tools(self, payload):
        """O Playwright CLI instalado responde; sem npm (ou Node < 22) a parte fica de fora."""
        tools = next(e for e in payload["installed"] if e["part"] == "tools")
        if shutil.which("npm"):
            self.assertIn(tools["status"], ("installed", "skipped"), tools)
        if tools["status"] == "installed":
            bin_dir = Path(tools["path"]) / "node_modules" / ".bin"
            cli_path = next(p for p in (bin_dir / "playwright-cli.cmd", bin_dir / "playwright-cli") if p.exists())
            probe = subprocess.run([str(cli_path), "--version"], capture_output=True, timeout=120, check=False)
            self.assertEqual(0, probe.returncode)
        return tools

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
            tools = self.check_tools(json.loads(done.stdout))

            started = time.monotonic()
            again = _setup(home)
            self.assertLess(time.monotonic() - started, 15)
            venv = next(e for e in json.loads(again.stdout)["installed"] if e["part"] == "venv")
            self.assertEqual("already", venv["status"], venv)
            if tools["status"] == "installed":
                again_tools = next(e for e in json.loads(again.stdout)["installed"] if e["part"] == "tools")
                self.assertEqual("already", again_tools["status"], again_tools)

            where = _setup(home, "--where", "venv")
            self.assertEqual(0, where.returncode, where.stderr)
            self.assertTrue(Path(json.loads(where.stdout)["path"]).is_relative_to(home / "runtime"))

            self.check_upgrade(home, folder)

    def check_upgrade(self, home, folder):
        """`--upgrade ytdlp` de verdade: `upgraded`, ou `unchanged` quando a fixada já é a mais nova."""
        upgraded = _setup(home, "--upgrade", "ytdlp")
        self.assertIn(upgraded.returncode, (0, 4), upgraded.stderr)
        upgrade = json.loads(upgraded.stdout)["upgrade"]
        # Sem versão mais nova que a fixada no índice, o honesto é `unchanged` (com aviso).
        self.assertIn(upgrade["status"], ("upgraded", "unchanged"), upgrade)
        marker = json.loads((folder.parent / ".getbrolls-runtime-venv.json").read_text(encoding="utf-8"))
        self.assertEqual("ready", marker["status"])
        if upgrade["status"] == "upgraded":
            self.assertTrue(marker["upgraded"]["yt-dlp"])
        else:
            self.assertTrue(upgrade["warnings"])
            self.assertEqual(upgrade["previous"], upgrade["version"])


if __name__ == "__main__":
    unittest.main()
