"""Offline coverage for the optional GB_*_PATH tool pins."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from getbrolls import config, media, social

CLI = ROOT / "scripts/gb.py"
PATH_KEYS = ("GB_YTDLP_PATH", "GB_VENV_PATH", "GB_FFMPEG_PATH", "GB_FFPROBE_PATH")


def clean_env(**values):
    """Environment with every GB_*_PATH removed, plus the given overrides."""
    environment = {k: v for k, v in os.environ.items() if k not in PATH_KEYS}
    environment.update(values)
    return patch.dict(os.environ, environment, clear=True)


def make_executable(directory, name):
    path = Path(directory) / name
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
        with clean_env(), tempfile.TemporaryDirectory() as root:
            self.assertIsNone(social.local_ytdlp(root))
            binary = make_executable(Path(root) / ".venv/bin", "yt-dlp")
            self.assertEqual(social.local_ytdlp(root), binary)


class ExecutableOverrideTests(unittest.TestCase):
    def test_ffmpeg_and_ffprobe_pins_are_used(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = make_executable(d, "ffmpeg-pinned")
            ffprobe = make_executable(d, "ffprobe-pinned")
            with clean_env(
                GB_FFMPEG_PATH=str(ffmpeg), GB_FFPROBE_PATH=str(ffprobe)
            ), patch("getbrolls.media.subprocess.run") as runner:
                runner.return_value = subprocess.CompletedProcess([], 0, stdout="{}")
                media.run(["ffmpeg", "-v", "error"])
                self.assertEqual(runner.call_args[0][0][0], str(ffmpeg))
                media.run(["ffprobe", "-v", "error"])
                self.assertEqual(runner.call_args[0][0][0], str(ffprobe))

    def test_ytdlp_pin_wins_over_venv(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "skill"
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


class VenvOverrideTests(unittest.TestCase):
    def test_posix_layout(self):
        with tempfile.TemporaryDirectory() as d:
            venv = Path(d) / "shared-venv"
            binary = make_executable(venv / "bin", "yt-dlp")
            with clean_env(GB_VENV_PATH=str(venv)):
                self.assertEqual(social.local_ytdlp(Path(d) / "ignored"), binary)

    def test_windows_layout(self):
        # The Windows layout is probed by path, so it resolves on any host.
        for name in ("yt-dlp.exe", "yt-dlp"):
            with tempfile.TemporaryDirectory() as d:
                venv = Path(d) / "shared-venv"
                binary = make_executable(venv / "Scripts", name)
                with clean_env(GB_VENV_PATH=str(venv)):
                    self.assertEqual(social.local_ytdlp(Path(d) / "ignored"), binary)

    def test_pinned_venv_without_ytdlp_does_not_use_default_venv(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "skill"
            make_executable(root / ".venv/bin", "yt-dlp")
            empty = Path(d) / "vazia"
            empty.mkdir()
            with clean_env(GB_VENV_PATH=str(empty)):
                self.assertIsNone(social.local_ytdlp(root))

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
            env_file.write_text(
                "GB_FFMPEG_PATH=" + str(ffmpeg) + "\nGB_VENV_PATH=\n", encoding="utf-8"
            )
            with clean_env():
                config.load_env(env_file)
                self.assertEqual(os.environ["GB_FFMPEG_PATH"], str(ffmpeg))
                self.assertEqual(config.tool_path("ffmpeg"), str(ffmpeg))

    def test_process_environment_wins_over_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            from_file = make_executable(d, "ffmpeg-file")
            from_process = make_executable(d, "ffmpeg-process")
            env_file = Path(d) / ".env"
            env_file.write_text(
                "GB_FFMPEG_PATH=" + str(from_file) + "\n", encoding="utf-8"
            )
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
            environment = {
                k: v for k, v in os.environ.items() if k != "GB_FONT_FILE"
            }
            with patch.dict(os.environ, environment, clear=True):
                config.load_env(env_file)
                self.assertEqual(str(font), os.environ["GB_FONT_FILE"])
        self.assertIn("GB_FONT_FILE", config.KEYS)


class DoctorReportTests(unittest.TestCase):
    def test_doctor_reports_active_override(self):
        with tempfile.TemporaryDirectory() as d:
            ffmpeg = make_executable(d, "ffmpeg-pinned")
            environment = {
                k: v for k, v in os.environ.items() if k not in PATH_KEYS
            }
            environment["GB_FFMPEG_PATH"] = str(ffmpeg)
            done = subprocess.run(
                [sys.executable, str(CLI), "doctor"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                timeout=120,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            payload = json.loads(done.stdout)
            self.assertEqual(
                payload["tool_paths"], {"GB_FFMPEG_PATH": str(ffmpeg)}
            )
            self.assertTrue(payload["executables"]["ffmpeg"])

    def test_doctor_without_override_reports_nothing(self):
        environment = {k: v for k, v in os.environ.items() if k not in PATH_KEYS}
        done = subprocess.run(
            [sys.executable, str(CLI), "doctor"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["tool_paths"], {})


if __name__ == "__main__":
    unittest.main()
