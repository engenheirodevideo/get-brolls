"""Teste próprio dos helpers `_paths`, `_cli` e `_media` (código novo, sem uso ainda)."""

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_audio, synth_image, synth_video
from _paths import CLI, CLI_ARGV, ROOT, SKILLS, WHEEL_MODE, cli, wheel_origin


class PathsTests(unittest.TestCase):
    def test_root_is_the_repository_root(self):
        self.assertTrue((ROOT / "scripts" / "getbrolls").is_dir())
        self.assertTrue((ROOT / "tests").is_dir())

    def test_cli_points_at_gb_py(self):
        self.assertEqual(ROOT / "scripts" / "gb.py", CLI)
        self.assertTrue(CLI.is_file())

    def test_skills_is_the_root_and_mirror_pair(self):
        root_skill, mirror_skill = SKILLS
        self.assertEqual(ROOT / "SKILL.md", root_skill)
        self.assertEqual(ROOT / "skills" / "get-brolls" / "SKILL.md", mirror_skill)
        self.assertTrue(root_skill.is_file())
        self.assertTrue(mirror_skill.is_file())


class RunCliTests(unittest.TestCase):
    def test_success_parses_stdout_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_cli("init-rules", "--project", tmp, "--format", "reels")
            self.assertEqual("reels", out["video_format"])

    def test_expect_non_zero_parses_stderr_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", "--project", tmp)
            refused = run_cli("init-rules", "--project", tmp, "--format", "reels", expect=2)
            self.assertIn("--format", str(refused))

    def test_project_kwarg_is_equivalent_to_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_cli("init-rules", project=tmp)
            self.assertTrue(Path(out["rules"]).is_file())

    def test_env_kwarg_is_merged_into_the_inherited_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = (Path(tmp) / "outside" / "BRIEF.md").resolve()
            result = run_cli(
                "init-brief",
                "--project",
                tmp,
                env={"GB_BRIEF_FILE": str(target)},
            )
            self.assertEqual(str(target), result["brief"])
            self.assertTrue(target.is_file())


class SynthMediaTests(unittest.TestCase):
    @skip_unless_ffmpeg
    def test_synth_video_produces_a_decodable_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            synth_video(path, size="160x90", duration=1, rate=10)
            self.assertTrue(path.is_file())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("video", probe.stdout)

    @skip_unless_ffmpeg
    def test_synth_video_accepts_the_testsrc2_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            synth_video(path, size="160x90", duration=1, rate=10, pattern="testsrc2")
            self.assertTrue(path.is_file())

    @skip_unless_ffmpeg
    def test_synth_image_produces_a_decodable_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foto.png"
            synth_image(path)
            self.assertTrue(path.is_file())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("video", probe.stdout)

    @skip_unless_ffmpeg
    def test_synth_audio_produces_a_decodable_audio_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.m4a"
            synth_audio(path, frequency=440, duration=1)
            self.assertTrue(path.is_file())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("audio", probe.stdout)


if __name__ == "__main__":
    unittest.main()


def _import_paths_with(**env):
    """Importa `_paths` num processo limpo com `env` por cima, para ver o modo escolhido."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GB_TEST_")}
    environment.update(env)
    environment["PYTHONPATH"] = str(ROOT / "tests")
    return subprocess.run(
        [sys.executable, "-c", "import _paths; print(_paths.MODE, _paths.CLI_ARGV)"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


_PROBE_ENV = (
    "import _isolation, os, json; print(json.dumps(sorted(k for k in os.environ"
    " if k in {'GB_ENV_FILE','PYTHONPATH','GETBROLLS_X','GETBROLLS_CACHE_DIR'})))"
)


class IsolationTests(unittest.TestCase):
    def _isolated_env(self, **env):
        environment = {**os.environ, "PYTHONPATH": str(ROOT / "tests"), **env}
        done = subprocess.run(
            [
                sys.executable,
                "-c",
                _PROBE_ENV,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=True,
        )
        return done.stdout.strip()

    def test_shell_config_leaks_are_removed(self):
        seen = self._isolated_env(GB_ENV_FILE="/x", GETBROLLS_X="1", GETBROLLS_CACHE_DIR="/c", GB_TEST_CLI="checkout")
        self.assertEqual('["GETBROLLS_CACHE_DIR", "PYTHONPATH"]', seen)

    def test_wheel_mode_drops_pythonpath(self):
        seen = self._isolated_env(GB_TEST_CLI="wheel", GB_TEST_WHEEL_PYTHON="/x/python")
        self.assertNotIn("PYTHONPATH", seen)


class CliArgvTests(unittest.TestCase):
    @unittest.skipIf(WHEEL_MODE, "modo wheel usa o pacote instalado")
    def test_cli_argv_defaults_to_the_checkout_script(self):
        self.assertEqual((sys.executable, str(CLI)), CLI_ARGV)

    def test_cli_helper_prefixes_the_argv_and_stringifies_arguments(self):
        self.assertEqual([*CLI_ARGV, "status", "3"], cli("status", 3))

    def test_unknown_mode_is_refused(self):
        done = _import_paths_with(GB_TEST_CLI="egg")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("GB_TEST_CLI", done.stderr)

    def test_wheel_mode_requires_the_wheel_python(self):
        done = _import_paths_with(GB_TEST_CLI="wheel", GB_TEST_WHEEL_PYTHON="")
        self.assertNotEqual(0, done.returncode)
        self.assertIn("GB_TEST_WHEEL_PYTHON", done.stderr)

    def test_wheel_mode_runs_the_installed_package_isolated_from_the_checkout(self):
        done = _import_paths_with(GB_TEST_CLI="wheel", GB_TEST_WHEEL_PYTHON="/x/bin/python")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("('/x/bin/python', '-P', '-m', 'getbrolls')", done.stdout)

    def test_wheel_origin_reads_the_reported_data_origin(self):
        self.assertEqual("wheel", wheel_origin("getbrolls 2.6.0 (dados: wheel)"))
        self.assertEqual("checkout", wheel_origin("getbrolls 2.6.0 (dados: checkout)"))
        self.assertIsNone(wheel_origin("getbrolls 2.6.0"))

    def test_no_test_builds_the_cli_argv_by_hand(self):
        this_file = Path(__file__).resolve()
        allowed = {this_file, ROOT / "tests" / "_paths.py", ROOT / "tests" / "_cli.py"}
        offenders = [
            f"{path.name}:{node.lineno}"
            for path in sorted((ROOT / "tests").glob("*.py"))
            if path.resolve() not in allowed
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.List) and _starts_with_executable_and_cli(node)
        ]
        self.assertEqual([], offenders)


_ARGV_HEAD = ("executable", "cli")


def _starts_with_executable_and_cli(node):
    if len(node.elts) < len(_ARGV_HEAD):
        return False
    first, second = node.elts[: len(_ARGV_HEAD)]
    is_executable = ast.unparse(first) == "sys.executable"
    return is_executable and any(isinstance(n, ast.Name) and n.id in {"CLI", "cli"} for n in ast.walk(second))


@unittest.skipUnless(WHEEL_MODE, "só no modo wheel")
class WheelModeTests(unittest.TestCase):
    def setUp(self):
        version = subprocess.run(
            [*CLI_ARGV, "--version"], capture_output=True, text=True, encoding="utf-8", check=False
        )
        origin = wheel_origin(version.stdout + version.stderr)
        if origin is None:
            self.skipTest("a CLI instalada ainda não informa a origem dos dados em --version")
        self.assertEqual("wheel", origin)

    def test_the_installed_package_lives_outside_the_repository(self):
        done = subprocess.run(
            [CLI_ARGV[0], "-P", "-c", "import getbrolls,sys;print(getbrolls.__file__)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        self.assertFalse(Path(done.stdout.strip()).resolve().is_relative_to(ROOT))

    def test_pythonpath_does_not_leak_into_the_suite(self):
        self.assertNotIn("PYTHONPATH", os.environ)
        self.assertTrue(_paths.WHEEL_MODE)
