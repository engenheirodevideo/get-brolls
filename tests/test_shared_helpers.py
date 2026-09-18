"""Teste próprio dos helpers `_paths`, `_cli` e `_media` (código novo, sem uso ainda)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_audio, synth_image, synth_video
from _paths import CLI, ROOT, SKILLS


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
