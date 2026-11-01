"""instagram_pairs roda como script (por caminho e com -m) e grava a fila nos dois modos (sem rede).

O script tem dois modos de importar o pacote: `python3 scripts/getbrolls/instagram_pairs.py` (sem pacote)
e `python -m getbrolls.instagram_pairs`. Os três pontos que dependem de `__package__` são o gravador da
recusa, o do cooldown e a leitura da fila antes do primeiro pedido; aqui cada um roda de verdade em cada modo,
com um `curl` de mentira no PATH. A URL é um IP público literal: nenhum DNS é consultado.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import queue

SCRIPT = ROOT / "scripts" / "getbrolls" / "instagram_pairs.py"
URL = "https://93.184.216.34/v/video.mp4"

FAKE_CURL = """#!/bin/sh
cat > /dev/null
echo called >> "$GB_FAKE_CALLS"
echo "curl: (22) The requested URL returned error: $GB_FAKE_STATUS" >&2
exit 22
"""
FAKE_OK = "#!/bin/sh\nexit 0\n"

MODES = ("path", "module")


@unittest.skipIf(os.name == "nt", "os curl/ffmpeg de mentira são scripts sh")
class ScriptModeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-igscript-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.calls = self.root / "calls.txt"
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in (("curl", FAKE_CURL), ("ffmpeg", FAKE_OK), ("ffprobe", FAKE_OK)):
            tool = bin_dir / name
            tool.write_text(body, encoding="utf-8")
            tool.chmod(0o755)
        configs = self.root / "configs"
        configs.mkdir()
        for kind in ("video", "audio"):
            (configs / f"01_A_{kind}.conf").write_text(f'url = "{URL}"\n', encoding="utf-8")
        self.bin_dir = bin_dir

    def run_script(self, mode, status, *extra):
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        env.update(
            PATH=f"{self.bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            GB_FAKE_STATUS=str(status),
            GB_FAKE_CALLS=str(self.calls),
            PYTHONIOENCODING="utf-8",
        )
        argv = [
            "--config-dir",
            str(self.root / "configs"),
            "--output-dir",
            str(self.root / "out"),
            "--parts-dir",
            str(self.root / "parts"),
            "--config-output-root",
            str(self.root),
            "--layout",
            "flat",
            "--summary-json",
            str(self.root / "work" / "summary.json"),
            "--project",
            str(self.root),
            "--pace",
            "0",
            *extra,
        ]
        if mode == "path":
            command = [sys.executable, str(SCRIPT), *argv]
        else:
            env["PYTHONPATH"] = str(ROOT / "scripts")
            command = [sys.executable, "-m", "getbrolls.instagram_pairs", *argv]
        return subprocess.run(
            command, cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False
        )

    def seed_queue(self):
        queue.save(queue.queue_path(self.root), queue.empty_state())

    def saved(self):
        return queue.load(queue.queue_path(self.root))["providers"]["instagram"]

    def test_a_refusal_is_recorded_in_the_queue_in_both_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.seed_queue()
                done = self.run_script(mode, 403)
                self.assertEqual(1, done.returncode, done.stderr)
                self.assertIn("stopped", self.saved())
                self.assertIn("parada registrada em queue.json", done.stderr)
                self.assertNotIn(URL, done.stderr + done.stdout)

    def test_a_rate_limit_is_recorded_as_a_cooldown_in_both_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.seed_queue()
                done = self.run_script(mode, 429)
                self.assertEqual(1, done.returncode, done.stderr)
                until = self.saved()["cooldown_until"]
                self.assertTrue(until)
                self.assertIn(f"cooldown recorded in queue.json until {until}", done.stderr)

    def test_a_recorded_stop_keeps_the_next_run_from_asking_the_cdn_in_both_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.seed_queue()
                queue.record_refusal(self.root, "instagram", "HTTP 403", reason_class="http_403")
                self.calls.unlink(missing_ok=True)
                done = self.run_script(mode, 403)
                self.assertNotEqual(0, done.returncode)
                self.assertIn("fila do instagram parada", done.stderr)
                self.assertFalse(self.calls.exists(), "nenhum pedido sai com a fila parada")
                summary = json.loads((self.root / "work" / "summary.json").read_text(encoding="utf-8"))
                self.assertEqual(["skipped"], [item["status"] for item in summary["results"]])

    def test_a_script_run_loads_the_package_modules_only_under_the_package_name(self):
        """A pasta do módulo sai do `sys.path` no modo script: `queue` e `http` são os da stdlib."""
        probe = (
            "import json, runpy, sys\n"
            "try:\n"
            "    runpy.run_path(sys.argv[1], run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "names = [n for n in sys.modules if 'instagram' in n]\n"
            "files = {n: getattr(sys.modules.get(n), '__file__', None) for n in ('queue', 'http')}\n"
            "print(json.dumps({'instagram': sorted(names), 'files': files}))\n"
        )
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        done = subprocess.run(
            [sys.executable, "-c", probe, str(SCRIPT), "--help"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        loaded = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual([], [name for name in loaded["instagram"] if not name.startswith("getbrolls.")])
        siblings = {"getbrolls.instagram_" + part for part in ("common", "urls", "media", "queue")}
        self.assertTrue(siblings <= set(loaded["instagram"]), loaded["instagram"])
        for name, where in loaded["files"].items():
            if where:
                self.assertNotEqual(str(SCRIPT.parent), str(Path(where).parent), name)


if __name__ == "__main__":
    unittest.main()
