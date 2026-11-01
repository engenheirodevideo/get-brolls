"""`scripts/gen_skill_mirror.py` numa raiz temporária (nunca o espelho real)."""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  # pylint: disable=unused-import

# isort: split
import gen_skill_mirror as gen

SKILL = "---\nname: demo\n---\n<!-- sync -->\nVeja docs/GUIDE.md e BRIEF.md e scripts/run.py.\n"


class MirrorTreeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-mirror-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "docs").mkdir()
        (self.tmp / "docs" / "GUIDE.md").write_text("# g\n", encoding="utf-8")
        (self.tmp / "scripts").mkdir()
        (self.tmp / "scripts" / "run.py").write_text("x = 1\n", encoding="utf-8")
        self.root_skill = self.tmp / "SKILL.md"
        self.mirror = self.tmp / "skills" / "get-brolls" / "SKILL.md"
        self.root_skill.write_text(SKILL, encoding="utf-8")

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(gen, "ROOT", self.tmp),
            mock.patch.object(gen, "ROOT_SKILL", self.root_skill),
            mock.patch.object(gen, "MIRROR_SKILL", self.mirror),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = gen.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class MirrorTests(MirrorTreeCase):
    def test_write_mode_creates_the_mirror_with_prefixes_and_fixed_comment(self):
        code, out, _ = self.run_main()
        self.assertEqual(0, code)
        self.assertEqual("skills/get-brolls/SKILL.md gerado a partir de SKILL.md.\n", out)
        self.assertEqual(
            "---\nname: demo\n---\n" + gen.MIRROR_SYNC_COMMENT + "\n"
            "Veja ${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md e BRIEF.md e ${CLAUDE_PLUGIN_ROOT}/scripts/run.py.\n",
            self.mirror.read_text(encoding="utf-8"),
        )
        self.assertEqual(SKILL, self.root_skill.read_text(encoding="utf-8"))  # raiz intacta

    def test_check_passes_after_write_and_does_not_touch_the_mirror(self):
        self.run_main()
        before = self.mirror.read_text(encoding="utf-8")
        code, out, _ = self.run_main("--check")
        self.assertEqual((0, "skills/get-brolls/SKILL.md está em sincronia com SKILL.md.\n"), (code, out))
        self.assertEqual(before, self.mirror.read_text(encoding="utf-8"))

    def test_check_out_of_sync_prints_a_diff_and_exits_1_without_writing(self):
        self.run_main()
        edited = self.mirror.read_text(encoding="utf-8").replace("Veja", "Olhe")
        self.mirror.write_text(edited, encoding="utf-8")
        code, out, _ = self.run_main("--check")
        self.assertEqual(1, code)
        self.assertIn("--- skills/get-brolls/SKILL.md\n", out)
        self.assertIn("+++ skills/get-brolls/SKILL.md (gerado)\n", out)
        self.assertIn("-Olhe docs", out.replace("${CLAUDE_PLUGIN_ROOT}/", ""))
        self.assertIn("+Veja", out)
        self.assertEqual(edited, self.mirror.read_text(encoding="utf-8"))

    def test_check_with_missing_mirror_is_out_of_sync_and_creates_nothing(self):
        code, out, _ = self.run_main("--check")
        self.assertEqual(1, code)
        self.assertIn("+Veja", out)
        self.assertFalse(self.mirror.exists())

    def test_broken_repo_path_aborts_with_exit_1_and_writes_nothing(self):
        self.root_skill.write_text(SKILL.replace("scripts/run.py", "scripts/sumiu.py"), encoding="utf-8")
        for argv in ((), ("--check",)):
            code, out, err = self.run_main(*argv)
            self.assertEqual((1, ""), (code, out))
            self.assertIn('SKILL.md: caminho "scripts/sumiu.py" parece apontar para o repositório', err)
        self.assertFalse(self.mirror.exists())

    def test_existing_directory_and_prefixed_path_are_left_alone(self):
        (self.tmp / "assets" / "pack.v2").mkdir(parents=True)
        body = "ver assets/pack.v2 e ${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md e outro.txt\n"
        with mock.patch.object(gen, "ROOT", self.tmp):
            self.assertEqual(body, gen.add_plugin_prefix(body))

    def test_display_falls_back_to_absolute_path_outside_the_root(self):
        with mock.patch.object(gen, "ROOT", self.tmp):
            self.assertEqual("docs/GUIDE.md", gen.display(self.tmp / "docs" / "GUIDE.md"))
            outside = Path(tempfile.gettempdir()).resolve() / "fora.md"
            self.assertEqual(outside.as_posix(), gen.display(outside))

    def test_display_writes_a_windows_path_with_slashes(self):
        with mock.patch.object(gen, "ROOT", PureWindowsPath(r"C:\r")):
            self.assertEqual("docs/GUIDE.md", gen.display(PureWindowsPath(r"C:\r\docs\GUIDE.md")))
            self.assertEqual("D:/fora/x.md", gen.display(PureWindowsPath(r"D:\fora\x.md")))


class RenderErrorTests(unittest.TestCase):
    def test_text_without_frontmatter_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "sem frontmatter reconhecível"):
            gen.render_mirror("# sem frontmatter\n<!-- c -->\n")

    def test_zero_or_two_sync_comments_are_rejected(self):
        for body, count in (("nada\n", 0), ("<!-- a -->\n<!-- b -->\n", 2)):
            with self.assertRaisesRegex(ValueError, f"achou {count}"):
                gen.replace_sync_comment(body)

    def test_split_frontmatter_separates_header_and_body(self):
        self.assertEqual(("---\na: 1\n---\n", "corpo\n"), gen.split_frontmatter("---\na: 1\n---\ncorpo\n"))


class ProgramTests(unittest.TestCase):
    def test_utf8_output_reconfigures_streams(self):
        fake = mock.Mock()
        with mock.patch.object(sys, "stdout", fake), mock.patch.object(sys, "stderr", io.StringIO()):
            gen._utf8_output()  # pylint: disable=protected-access
        fake.reconfigure.assert_called_once_with(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
