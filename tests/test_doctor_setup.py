"""`doctor` diz se está pronto (`ready`, saída 4) e `setup --check` confere o runtime sem instalar."""

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import ready_exit
from _paths import CLI_ARGV, WHEEL_MODE

from getbrolls import _paths, bootstrap, cli, commands
from getbrolls.runtime import READ_ONLY_COMMANDS


def _run(*args, env=None):
    environment = {**os.environ, **(env or {})}
    return subprocess.run(
        [*CLI_ARGV, *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=120,
        check=False,
    )


class DoctorReadyTests(unittest.TestCase):
    def test_doctor_exits_4_iff_something_required_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = _run("doctor", env={"GB_FFMPEG_PATH": str(Path(tmp) / "sem-ffmpeg")})
        self.assertEqual(4, done.returncode, done.stderr)
        payload = json.loads(done.stdout)
        self.assertTrue(payload["summary"]["missing"])
        self.assertIs(False, payload["ready"])

        clean = _run("doctor")
        payload = json.loads(clean.stdout)
        self.assertEqual(4 if payload["summary"]["missing"] else 0, clean.returncode, clean.stderr)
        self.assertEqual(not payload["summary"]["missing"], payload["ready"])
        self.assertEqual(ready_exit(payload), clean.returncode)

    def test_doctor_ready_is_the_second_key(self):
        payload = json.loads(_run("doctor").stdout)
        self.assertEqual(["summary", "ready"], list(payload)[:2])

    def test_doctor_accepts_json(self):
        done = _run("doctor", "--json")
        self.assertIn(done.returncode, (0, 4), done.stderr)
        self.assertIn("ready", json.loads(done.stdout))

    def test_doctor_install_block(self):
        install = json.loads(_run("doctor").stdout)["install"]
        self.assertEqual("wheel" if WHEEL_MODE else "checkout", install["origin"])
        for key in ("version", "python", "data_root", "cli"):
            self.assertTrue(install[key], key)
        self.assertEqual([], install["data_missing"])
        self.assertIn(install["runtime"]["venv"]["source"], ("GB_RUNTIME_DIR", "gb_home", "checkout"))
        self.assertIn("source", install["env_file"])
        self.assertEqual(os.environ["GB_HOME"], install["gb_home"])

    def test_doctor_env_file_flag_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "neutro.env"
            env_file.write_text("", encoding="utf-8")
            done = _run("--env-file", env_file, "doctor")
        self.assertIn(done.returncode, (0, 4), done.stderr)
        self.assertEqual("flag", json.loads(done.stdout)["install"]["env_file"]["source"])

    def test_missing_data_becomes_a_missing_entry(self):
        with patch.object(_paths, "verify_data", return_value=["docs/RULES.md"]), patch.dict(os.environ):
            result = cli.main(["doctor"])
        entry = next(e for e in result["summary"]["missing"] if e["item"] == "dados da instalação")
        self.assertIn("docs/RULES.md", entry["note"])
        self.assertTrue(entry["fix"])
        self.assertIs(False, result["ready"])
        self.assertEqual(4, cli.result_exit("doctor", result))

    def test_missing_data_fix_in_a_wheel_is_the_reinstall(self):
        fake = _paths.Install("wheel", Path("pkg"), Path("pkg/_data"), None)
        with patch.object(_paths, "install", return_value=fake):
            summary = commands.doctor_summary({}, (), ["docs/RULES.md"])
        entry = next(e for e in summary["missing"] if e["item"] == "dados da instalação")
        self.assertEqual("uv tool install --reinstall getbrolls", entry["fix"])
        self.assertEqual(_paths.REINSTALL_COMMAND, entry["fix"])
        self.assertEqual("Faltam: docs/RULES.md", entry["note"])


class SetupCheckTests(unittest.TestCase):
    def test_setup_check_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            done = _run("setup", "--check", env={"GB_HOME": str(home)})
            self.assertIn(done.returncode, (0, 4), done.stderr)
            payload = json.loads(done.stdout)
            self.assertEqual(ready_exit(payload), done.returncode)
            self.assertFalse((home / "runtime").exists())
        self.assertEqual(
            ["ytdlp", "playwright", "ffmpeg", "ffprobe", "curl", "node", "npx", "data"],
            [s["id"] for s in payload["steps"]],
        )
        self.assertTrue(payload["summary"]["line"])
        for step in payload["steps"]:
            self.assertEqual(step["ok"], step["found"] is not None, step)

    def test_setup_check_accepts_json(self):
        done = _run("setup", "--check", "--json")
        self.assertIn(done.returncode, (0, 4), done.stderr)

    def test_setup_check_and_upgrade_cannot_be_combined(self):
        done = _run("setup", "--check", "--upgrade", "ytdlp")
        self.assertEqual(2, done.returncode, done.stderr)
        error = json.loads(done.stderr)
        self.assertEqual("USAGE_ERROR", error["error_code"])
        self.assertIn("--check", error["error"])
        self.assertEqual("", done.stdout)

    def test_setup_check_in_a_checkout_points_at_setup(self):
        with patch.object(bootstrap, "_resolved", return_value={}):
            result = bootstrap.check()
        self.assertIs(False, result["ready"])
        for step in (s for s in result["steps"] if s["id"] in bootstrap.RUNTIME_STEPS):
            self.assertEqual([_paths.cli_prefix_text() + " setup"], step["commands"], step)
        ffmpeg = next(s for s in result["steps"] if s["id"] == "ffmpeg")
        self.assertEqual([], ffmpeg["commands"])
        self.assertEqual(commands.SYSTEM_TOOLS, ffmpeg["note"])

    def test_setup_check_in_a_wheel_install_points_at_the_shared_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "site" / "getbrolls"
            data = pkg / "_data"
            data.mkdir(parents=True)
            (data / "MANIFEST").write_text("", encoding="utf-8")
            for entry in _paths.REQUIRED_DATA:
                (data / entry).parent.mkdir(parents=True, exist_ok=True)
                (data / entry).write_text(entry, encoding="utf-8")
            wheel = _paths.detect(pkg)
            env = {k: v for k, v in os.environ.items() if k != "GB_RUNTIME_DIR"}
            with (
                patch.object(_paths, "install", return_value=wheel),
                patch.object(bootstrap, "_resolved", return_value={}),
                patch.dict(os.environ, env, clear=True),
            ):
                shared = _paths.gb_home() / "runtime" / str(_paths.part_sha("venv"))
                result = bootstrap.check()
                prefix = _paths.cli_prefix_text()
            self.assertFalse(shared.exists())
        for step in (s for s in result["steps"] if s["id"] in bootstrap.RUNTIME_STEPS):
            self.assertEqual([prefix + " setup"], step["commands"], step)
            self.assertFalse(any("npm" in command for command in step["commands"]), step)

    def test_setup_check_names_every_required_executable_of_the_doctor(self):
        ids = {s["id"] for s in bootstrap.check()["steps"]}
        expected = {bootstrap.STEP_IDS.get(name, name) for name in commands.REQUIRED_EXECUTABLES}
        self.assertEqual(expected | {"data"}, ids)

    def test_missing_data_is_a_setup_step_with_the_reinstall(self):
        fake = _paths.Install("wheel", Path("pkg"), Path("pkg/_data"), None)
        with (
            patch.object(_paths, "install", return_value=fake),
            patch.object(_paths, "verify_data", return_value=["docs/RULES.md"]),
        ):
            result = bootstrap.check()
        data = next(s for s in result["steps"] if s["id"] == "data")
        self.assertIs(False, data["ok"])
        self.assertIsNone(data["found"])
        self.assertEqual([_paths.REINSTALL_COMMAND], data["commands"])
        self.assertIn("docs/RULES.md", data["note"])
        self.assertIs(False, result["ready"])
        self.assertIn("data", result["summary"]["missing"])

    def test_without_the_dependency_files_the_runtime_fix_is_the_reinstall(self):
        fake = _paths.Install("wheel", Path("pkg"), Path("pkg/_data"), None)
        with (
            patch.object(_paths, "install", return_value=fake),
            patch.object(_paths, "part_sha", return_value=None),
            patch.object(bootstrap, "_resolved", return_value={}),
        ):
            result = bootstrap.check()
        for step in (s for s in result["steps"] if s["id"] in bootstrap.RUNTIME_STEPS):
            self.assertEqual([_paths.REINSTALL_COMMAND], step["commands"], step)
            self.assertFalse(any("pip install -r" in command for command in step["commands"]), step)

    def test_setup_check_ready_agrees_with_doctor_ready(self):
        """Tudo no PATH e um único item quebrado por vez: os dois vereditos sempre batem."""

        def which(missing=None):
            return lambda name, *_args, **_kwargs: None if name == missing else sys.executable

        pins = ("GB_FFMPEG_PATH", "GB_FFPROBE_PATH", "GB_YTDLP_PATH", "GB_VENV_PATH")
        clean = {key: value for key, value in os.environ.items() if key not in pins}
        with tempfile.TemporaryDirectory() as tmp:
            cases = {
                "ready": (),
                "broken-pin": (patch.dict(os.environ, {"GB_FFMPEG_PATH": str(Path(tmp) / "sem-ffmpeg")}),),
                "no-curl": (patch.object(commands.shutil, "which", side_effect=which("curl")),),
                "no-npx": (patch.object(commands.shutil, "which", side_effect=which("npx")),),
                "no-data": (patch.object(_paths, "verify_data", return_value=["docs/RULES.md"]),),
            }
            for label, patches in cases.items():
                with self.subTest(case=label), contextlib.ExitStack() as stack:
                    stack.enter_context(patch.dict(os.environ, clean, clear=True))
                    stack.enter_context(patch.object(commands.shutil, "which", side_effect=which()))
                    for item in patches:
                        stack.enter_context(item)
                    doctor = cli.main(["doctor"])
                    setup = bootstrap.check()
                    self.assertEqual(doctor["ready"], setup["ready"], (doctor["summary"], setup["summary"]))
                    self.assertIs(label == "ready", setup["ready"], setup["summary"])

    def test_setup_is_read_only_and_a_prerequisite_command(self):
        self.assertIn("setup", READ_ONLY_COMMANDS)
        self.assertIn("setup", cli.PREREQUISITE_COMMANDS)
        self.assertIn("setup", cli.SUMMARIES)


if __name__ == "__main__":
    unittest.main()
