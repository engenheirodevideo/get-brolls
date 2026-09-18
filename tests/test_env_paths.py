"""Offline coverage for the optional GB_*_PATH tool pins."""

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)

from getbrolls import config, media, social

CLI = ROOT / "scripts/gb.py"
PATH_KEYS = config.PATH_KEYS


def clean_environ(**values):
    """Environment dict with every GB_*_PATH removed, plus the given overrides."""
    return {
        **{k: v for k, v in os.environ.items() if k not in PATH_KEYS},
        **values,
    }


def clean_env(**values):
    """Environment with every GB_*_PATH removed, plus the given overrides."""
    return patch.dict(os.environ, clean_environ(**values), clear=True)


def make_executable(directory, name):
    # Caminho real: os pins resolvem symlinks, e /var é symlink no macOS.
    path = Path(directory).resolve() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class DefaultsUnchangedTests(unittest.TestCase):
    def test_no_override_keeps_plain_tool_names(self):
        with clean_env():
            self.assertIsNone(config.executable_override("GB_FFMPEG_PATH"))
            self.assertIsNone(config.venv_override())
            self.assertEqual(config.tool_path("ffmpeg"), "ffmpeg")
            self.assertEqual(config.tool_path("ffprobe"), "ffprobe")
            self.assertEqual(config.tool_path("yt-dlp"), "yt-dlp")
            self.assertEqual(config.active_overrides(), {})

    def test_media_run_keeps_default_command(self):
        with clean_env(), patch("getbrolls.media.subprocess.run") as runner:
            runner.return_value = subprocess.CompletedProcess([], 0, stdout="{}")
            media.run(["ffprobe", "-v", "error"])
            self.assertEqual(runner.call_args[0][0], ["ffprobe", "-v", "error"])

    def test_local_ytdlp_keeps_venv_discovery(self):
        with clean_env(), tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertIsNone(social.local_ytdlp(root))
            binary = make_executable(root / ".venv/bin", "yt-dlp")
            self.assertEqual(social.local_ytdlp(root), binary)


class ExecutableOverrideTests(unittest.TestCase):
    def test_ffmpeg_and_ffprobe_pins_are_used(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = make_executable(d, "ffmpeg-pinned")
            ffprobe = make_executable(d, "ffprobe-pinned")
            with (
                clean_env(GB_FFMPEG_PATH=str(ffmpeg), GB_FFPROBE_PATH=str(ffprobe)),
                patch("getbrolls.media.subprocess.run") as runner,
            ):
                runner.return_value = subprocess.CompletedProcess([], 0, stdout="{}")
                media.run(["ffmpeg", "-v", "error"])
                self.assertEqual(runner.call_args[0][0][0], str(ffmpeg))
                media.run(["ffprobe", "-v", "error"])
                self.assertEqual(runner.call_args[0][0][0], str(ffprobe))

    def test_ytdlp_pin_wins_over_venv(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve() / "skill"
            make_executable(root / ".venv/bin", "yt-dlp")
            pinned = make_executable(Path(d) / "pinned", "yt-dlp")
            with clean_env(GB_YTDLP_PATH=str(pinned)):
                self.assertEqual(social.local_ytdlp(root), pinned)

    def test_missing_path_fails_naming_variable_and_path(self):
        missing = str(Path(tempfile.gettempdir()) / "gb-inexistente-ffmpeg")
        with clean_env(GB_FFMPEG_PATH=missing):
            with self.assertRaises(ValueError) as caught:
                config.tool_path("ffmpeg")
            self.assertIn("GB_FFMPEG_PATH", str(caught.exception))
            self.assertIn(missing, str(caught.exception))

    @unittest.skipIf(os.name == "nt", "No Windows a executabilidade vem da extensão.")
    def test_non_executable_path_fails(self):
        with tempfile.TemporaryDirectory() as d:
            plain = Path(d) / "ffprobe.txt"
            plain.write_text("nada", encoding="utf-8")
            plain.chmod(0o644)
            with clean_env(GB_FFPROBE_PATH=str(plain)):
                with self.assertRaises(ValueError) as caught:
                    config.tool_path("ffprobe")
                self.assertIn("GB_FFPROBE_PATH", str(caught.exception))
                self.assertIn(str(plain), str(caught.exception))

    def test_empty_value_behaves_as_unset(self):
        with clean_env(GB_FFMPEG_PATH="", GB_VENV_PATH="   "):
            self.assertEqual(config.tool_path("ffmpeg"), "ffmpeg")
            self.assertIsNone(config.venv_override())

    def test_relative_pin_resolves_to_an_absolute_path(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d).resolve()
            ffmpeg = make_executable(directory, "ffmpeg-pinned")
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with clean_env(GB_FFMPEG_PATH="ffmpeg-pinned"):
                    resolved = config.tool_path("ffmpeg")
            finally:
                os.chdir(cwd)
            self.assertEqual(str(ffmpeg), resolved)
            self.assertTrue(Path(resolved).is_absolute())

    def test_directory_pin_says_it_is_not_an_executable_file(self):
        with tempfile.TemporaryDirectory() as d:
            with clean_env(GB_FFMPEG_PATH=d):
                with self.assertRaises(ValueError) as caught:
                    config.tool_path("ffmpeg")
            message = str(caught.exception)
            self.assertIn("não é um arquivo executável", message)
            self.assertNotIn("não existe", message)


class VenvOverrideTests(unittest.TestCase):
    def test_posix_layout(self):
        with tempfile.TemporaryDirectory() as d:
            venv = Path(d).resolve() / "shared-venv"
            binary = make_executable(venv / "bin", "yt-dlp")
            with clean_env(GB_VENV_PATH=str(venv)):
                self.assertEqual(social.local_ytdlp(Path(d) / "ignored"), binary)

    def test_windows_layout(self):
        # The Windows layout is probed by path, so it resolves on any host.
        for name in ("yt-dlp.exe", "yt-dlp"):
            with tempfile.TemporaryDirectory() as d:
                venv = Path(d).resolve() / "shared-venv"
                binary = make_executable(venv / "Scripts", name)
                with clean_env(GB_VENV_PATH=str(venv)):
                    self.assertEqual(social.local_ytdlp(Path(d) / "ignored"), binary)

    def test_pinned_venv_without_ytdlp_fails_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve() / "skill"
            make_executable(root / ".venv/bin", "yt-dlp")
            empty = Path(d).resolve() / "vazia"
            empty.mkdir()
            with clean_env(GB_VENV_PATH=str(empty)):
                with self.assertRaises(ValueError) as caught:
                    social.local_ytdlp(root)
            message = str(caught.exception)
            self.assertIn("GB_VENV_PATH", message)
            self.assertIn(str(empty), message)
            self.assertIn("yt-dlp", message)

    def test_missing_directory_fails(self):
        missing = str(Path(tempfile.gettempdir()) / "gb-inexistente-venv")
        with clean_env(GB_VENV_PATH=missing):
            with self.assertRaises(ValueError) as caught:
                social.local_ytdlp()
            self.assertIn("GB_VENV_PATH", str(caught.exception))
            self.assertIn(missing, str(caught.exception))


class EnvFileTests(unittest.TestCase):
    def test_env_file_supplies_tool_paths(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = make_executable(d, "ffmpeg-pinned")
            env_file = Path(d) / ".env"
            env_file.write_text("GB_FFMPEG_PATH=" + str(ffmpeg) + "\nGB_VENV_PATH=\n", encoding="utf-8")
            with clean_env():
                config.load_env(env_file)
                self.assertEqual(os.environ["GB_FFMPEG_PATH"], str(ffmpeg))
                self.assertEqual(config.tool_path("ffmpeg"), str(ffmpeg))

    def test_process_environment_wins_over_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            from_file = make_executable(d, "ffmpeg-file")
            from_process = make_executable(d, "ffmpeg-process")
            env_file = Path(d) / ".env"
            env_file.write_text("GB_FFMPEG_PATH=" + str(from_file) + "\n", encoding="utf-8")
            with clean_env(GB_FFMPEG_PATH=str(from_process)):
                config.load_env(env_file)
                self.assertEqual(config.tool_path("ffmpeg"), str(from_process))


class EnvFileVocabularyTests(unittest.TestCase):
    def test_unknown_variable_names_itself_and_the_accepted_set(self):
        with tempfile.TemporaryDirectory() as d:
            env_file = Path(d) / ".env"
            env_file.write_text("GB_INVENTADA=1\n", encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                config.load_env(env_file)
        message = str(raised.exception)
        self.assertIn("GB_INVENTADA", message)
        self.assertIn("GB_FONT_FILE", message)
        self.assertIn("PEXELS_API_KEY", message)

    def test_font_file_is_accepted_by_the_env_loader(self):
        with tempfile.TemporaryDirectory() as d:
            font = Path(d) / "fonte.ttf"
            font.write_text("fixture", encoding="utf-8")
            env_file = Path(d) / ".env"
            env_file.write_text(f"GB_FONT_FILE={font}\n", encoding="utf-8")
            environment = {k: v for k, v in os.environ.items() if k != "GB_FONT_FILE"}
            with patch.dict(os.environ, environment, clear=True):
                config.load_env(env_file)
                self.assertEqual(str(font), os.environ["GB_FONT_FILE"])
        self.assertIn("GB_FONT_FILE", config.KEYS)


class DoctorReportTests(unittest.TestCase):
    def doctor(self, directory, **values):
        """doctor com `.env` neutro: o `.env` do desenvolvedor não entra no teste."""
        empty = Path(directory) / "vazio.env"
        empty.write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(CLI), "--env-file", str(empty), "doctor"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=clean_environ(**values),
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_doctor_reports_active_override(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = make_executable(d, "ffmpeg-pinned")
            payload = self.doctor(d, GB_FFMPEG_PATH=str(ffmpeg))
            self.assertEqual(payload["tool_paths"], {"GB_FFMPEG_PATH": str(ffmpeg.resolve())})
            self.assertTrue(payload["executables"]["ffmpeg"])

    def test_doctor_without_override_reports_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.doctor(d)["tool_paths"], {})

    def test_doctor_turns_an_invalid_pin_into_a_missing_entry(self):
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "gb-inexistente-ffmpeg")
            payload = self.doctor(d, GB_FFMPEG_PATH=missing)
            entry = next(e for e in payload["summary"]["missing"] if e["item"] == "GB_FFMPEG_PATH")
            self.assertIn(missing, entry["note"])
            self.assertEqual({}, payload["tool_paths"])

    def test_doctor_fix_names_the_installer_by_absolute_path(self):
        # Só a entrada do pin inválido: ferramentas de sistema ausentes na máquina
        # (ex.: ffmpeg num runner limpo) apontam para o gerenciador, não o instalador.
        with tempfile.TemporaryDirectory() as d:
            payload = self.doctor(d, GB_VENV_PATH=str(Path(d) / "sem-venv"))
            entries = [e for e in payload["summary"]["missing"] if e["item"] == "GB_VENV_PATH"]
            self.assertTrue(entries, payload["summary"]["missing"])
            for entry in entries:
                self.assertIn("install.sh", entry["fix"])
                self.assertIn(str(ROOT / "scripts" / "install.sh"), entry["fix"])

    def test_doctor_resolves_each_tool_to_an_absolute_executable(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = make_executable(d, "ffmpeg-pinned")
            payload = self.doctor(d, GB_FFMPEG_PATH=str(ffmpeg))
            resolved = payload["resolved"]
            for name in ("ffmpeg", "ffprobe", "yt-dlp"):
                self.assertIn(name, resolved)
            self.assertEqual(str(ffmpeg.resolve()), resolved["ffmpeg"])
            for name, path in resolved.items():
                if path is not None:
                    self.assertTrue(Path(path).is_absolute(), name)


class DeclaredKeys(unittest.TestCase):
    """Toda `GB_*` lida pelo código precisa estar em `config.KEYS` e no `.env.example`.

    Uma variável fora de `KEYS` faz `load_env` recusar o `.env` inteiro: a pessoa põe
    no arquivo a variável que a documentação promete e todo comando para de rodar.
    """

    READER = re.compile(r'(?:os\.environ\.get|os\.getenv|os\.environ\[)\(?"(GB_[A-Z0-9_]+)"')

    def _read_keys(self):
        found = {}
        for path in sorted((ROOT / "scripts" / "getbrolls").glob("*.py")):
            for key in self.READER.findall(path.read_text(encoding="utf-8")):
                found.setdefault(key, path.name)
        return found

    def test_every_gb_variable_the_code_reads_is_declared_in_keys(self):
        found = self._read_keys()
        self.assertTrue(found)
        undeclared = {key: where for key, where in found.items() if key not in config.KEYS}
        self.assertEqual({}, undeclared)

    def test_every_declared_key_appears_in_the_env_example(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        missing = [key for key in sorted(config.KEYS) if key not in example]
        self.assertEqual([], missing)

    def test_gb_brief_file_in_a_dot_env_does_not_break_every_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("GB_BRIEF_FILE=\nGB_SCAN_MAX_SECONDS=120\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                config.load_env(env_file)


if __name__ == "__main__":
    unittest.main()
