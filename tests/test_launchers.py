"""Launchers (`playwright.sh`/`.ps1` e os helpers de YouTube) acham o runtime pelo `setup --where`."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import profile

BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")
POSIX_BASH = os.name != "nt" and BASH is not None
RUNTIME_SH = ROOT / "scripts" / "getbrolls" / "tools" / "youtube" / "_runtime.sh"
# O que o ambiente de quem roda não pode decidir por estes testes.
_DROPPED = {*profile.FIELDS.values(), "GB_PROFILE", "GB_PROFILE_SHA256", "GB_ENV_FILE"}


def executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


class LauncherCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.runtime = self.tmp / "runtime"
        self.runtime.mkdir()

    def env(self, **extra):
        environ = {key: value for key, value in os.environ.items() if key not in _DROPPED}
        environ["GB_HOME"] = str(self.tmp / "gbhome")
        environ["HOME"] = environ["USERPROFILE"] = str(self.tmp / "user")
        environ["GB_RUNTIME_DIR"] = str(self.runtime)
        for key, value in extra.items():
            if value is None:
                environ.pop(key, None)
            else:
                environ[key] = value
        return environ

    def run_bash(self, script, *args, env=None):
        return subprocess.run(
            [str(BASH), "-c", script, "launcher", *args],
            cwd=self.tmp,
            env=env if env is not None else self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )


@unittest.skipUnless(POSIX_BASH, "Os launchers .sh são do macOS/Linux; o Windows usa os .ps1.")
class PlaywrightShTests(LauncherCase):
    def test_playwright_sh_uses_the_shared_tools(self):
        executable(self.runtime / ".tools/node_modules/.bin/playwright-cli", '#!/bin/sh\necho "fake-pw $*"\n')
        done = self.run_bash('exec bash "$1" --version', str(ROOT / "scripts/playwright.sh"))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("fake-pw --version", done.stdout.strip())

    def test_playwright_sh_explains_a_missing_cli(self):
        done = self.run_bash('exec bash "$1" --version', str(ROOT / "scripts/playwright.sh"))
        self.assertEqual(1, done.returncode, done.stdout)
        self.assertIn("Playwright CLI ausente", done.stderr)
        self.assertIn("setup", done.stderr)


@unittest.skipUnless(POSIX_BASH, "Os helpers .sh são do macOS/Linux; o Windows usa a CLI principal.")
class YtdlpHelperTests(LauncherCase):
    """`gb_ytdlp` lê `in_use.executable` do `setup --where venv`."""

    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.log = self.tmp / "getbrolls-args.txt"
        # yt-dlp do PATH: só vale quando o runtime não responde.
        executable(self.bin / "yt-dlp", '#!/bin/sh\necho "path-ytdlp $*"\n')

    def path_env(self, **extra):
        environ = self.env(**extra)
        environ["PATH"] = f"{self.bin}{os.pathsep}{environ.get('PATH', '')}"
        return environ

    def packaged_helper(self, getbrolls_body):
        """Cópia do `_runtime.sh` fora do checkout (sem gb.py): cai no `getbrolls` do PATH."""
        helper = self.tmp / "pkg" / "getbrolls" / "tools" / "youtube" / "extra" / "_runtime.sh"
        helper.parent.mkdir(parents=True)
        shutil.copyfile(RUNTIME_SH, helper)
        executable(self.bin / "getbrolls", getbrolls_body)
        return helper

    def fake_getbrolls(self, stdout):
        return f'#!/bin/sh\nprintf "%s\\n" "$*" > "{self.log}"\nprintf "%s" {json.dumps(stdout)}\n'

    def test_checkout_helper_runs_the_venv_in_use(self):
        executable(self.runtime / ".venv/bin/yt-dlp", '#!/bin/sh\necho "venv-ytdlp $*"\n')
        done = self.run_bash('source "$1"; gb_ytdlp --version', str(RUNTIME_SH), env=self.path_env())
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(done.stdout.startswith("venv-ytdlp "), done.stdout)
        self.assertTrue(done.stdout.strip().endswith("--version"), done.stdout)

    def test_without_a_venv_the_path_ytdlp_answers(self):
        done = self.run_bash('source "$1"; gb_ytdlp --version', str(RUNTIME_SH), env=self.path_env())
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(done.stdout.startswith("path-ytdlp "), done.stdout)

    def test_packaged_helper_asks_getbrolls_with_the_profile_off_by_default(self):
        fake = executable(self.tmp / "fake-venv" / "bin" / "yt-dlp", '#!/bin/sh\necho "managed-ytdlp $*"\n')
        helper = self.packaged_helper(self.fake_getbrolls(json.dumps({"in_use": {"executable": str(fake)}})))
        done = self.run_bash('source "$1"; gb_ytdlp --version', str(helper), env=self.path_env())
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(done.stdout.startswith("managed-ytdlp "), done.stdout)
        self.assertEqual("--profile off setup --where venv", self.log.read_text(encoding="utf-8").strip())

    def test_packaged_helper_keeps_an_inherited_profile(self):
        fake = executable(self.tmp / "fake-venv" / "bin" / "yt-dlp", '#!/bin/sh\necho "managed-ytdlp $*"\n')
        helper = self.packaged_helper(self.fake_getbrolls(json.dumps({"in_use": {"executable": str(fake)}})))
        env = self.path_env(GB_PROFILE=str(self.tmp / "getbrolls.toml"))
        done = self.run_bash('source "$1"; gb_ytdlp --version', str(helper), env=env)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("setup --where venv", self.log.read_text(encoding="utf-8").strip())

    def test_empty_answer_is_explained_and_falls_back_to_path(self):
        helper = self.packaged_helper(self.fake_getbrolls(""))
        done = self.run_bash('source "$1"; gb_ytdlp --version', str(helper), env=self.path_env())
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("não devolveu JSON", done.stderr)
        self.assertTrue(done.stdout.startswith("path-ytdlp "), done.stdout)

    def test_non_json_answer_is_explained_without_a_traceback(self):
        helper = self.packaged_helper(self.fake_getbrolls("isto não é json"))
        done = self.run_bash('source "$1"; gb_ytdlp --version', str(helper), env=self.path_env())
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("não é JSON", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        self.assertTrue(done.stdout.startswith("path-ytdlp "), done.stdout)

    def test_failing_getbrolls_falls_back_to_path(self):
        helper = self.packaged_helper("#!/bin/sh\nexit 2\n")
        done = self.run_bash('source "$1"; gb_ytdlp --version', str(helper), env=self.path_env())
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(done.stdout.startswith("path-ytdlp "), done.stdout)


@unittest.skipUnless(PWSH and shutil.which("python"), "Sem pwsh (ou python) no PATH.")
class PlaywrightPs1Tests(LauncherCase):
    def test_playwright_ps1_uses_the_shared_tools(self):
        bin_dir = self.runtime / ".tools" / "node_modules" / ".bin"
        if os.name == "nt":
            executable(bin_dir / "playwright-cli.cmd", "@echo fake-pw %*\r\n")
        else:
            executable(bin_dir / "playwright-cli", '#!/bin/sh\necho "fake-pw $*"\n')
        done = subprocess.run(
            [str(PWSH), "-NoProfile", "-File", str(ROOT / "scripts" / "playwright.ps1"), "--version"],
            cwd=self.tmp,
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("fake-pw --version", done.stdout.strip())


if __name__ == "__main__":
    unittest.main()
