"""`scripts/check_anchors.py` contra uma árvore de docs temporária."""

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from typing import ClassVar
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

# isort: split
import check_anchors as anchors


class AnchorTreeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-anchors-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.guide = self.put("docs/GUIDE.md", "# Guia\n\n## Seção real\n\n## Outra\n")
        (self.tmp / "references").mkdir()
        self.skill = self.tmp / "SKILL.md"
        self.targets = [self.skill]

    def put(self, relative: str, text: str) -> Path:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def patched(self):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(anchors, "ROOT", self.tmp))
        stack.enter_context(mock.patch.object(anchors, "GUIDE_PATH", self.guide))
        stack.enter_context(mock.patch.object(anchors, "TARGET_FILES", self.targets))
        return stack

    def run_main(self) -> tuple[int, str]:
        out = io.StringIO()
        with self.patched(), contextlib.redirect_stdout(out):
            code = anchors.main()
        return code, out.getvalue()


class WindowsFile(PureWindowsPath):
    """A Windows path with just enough of `Path` for `check()`: it reads from `files`, keyed by its text."""

    files: ClassVar[dict[str, str]] = {}

    def exists(self):
        return str(self) in self.files

    def read_text(self, encoding="utf-8"):  # pylint: disable=unused-argument
        return self.files[str(self)]

    def resolve(self):
        return self


class WindowsPathTests(unittest.TestCase):
    def test_problems_name_a_windows_file_with_slashes(self):
        root = WindowsFile(r"C:\r")
        guide = root / "docs" / "GUIDE.md"
        skill = root / "SKILL.md"
        reference = root / "references" / "a.md"
        files = {
            str(guide): "# Guia\n",
            str(skill): "[x](docs/GUIDE.md#nope)\n",
            str(reference): "veja GUIDE.md#nope2 e [y](missing.md)\n",
        }
        with (
            mock.patch.object(WindowsFile, "files", files),
            mock.patch.object(anchors, "ROOT", root),
            mock.patch.object(anchors, "GUIDE_PATH", guide),
            mock.patch.object(anchors, "TARGET_FILES", [skill, reference]),
        ):
            problems = anchors.check()
        self.assertEqual(
            [
                "SKILL.md: âncora quebrada GUIDE.md#nope",
                "references/a.md: âncora quebrada GUIDE.md#nope2",
                "references/a.md: link quebrado missing.md",
            ],
            problems,
        )


class CheckTests(AnchorTreeCase):
    def test_main_reports_broken_guide_anchors_and_exits_1(self):
        self.put("SKILL.md", "[a](docs/GUIDE.md#seção-real) [b](docs/GUIDE.md#nao-existe)\n")
        code, out = self.run_main()
        self.assertEqual(1, code)
        self.assertEqual("Âncoras quebradas encontradas:\n- SKILL.md: âncora quebrada GUIDE.md#nao-existe\n", out)

    def test_main_ok_when_everything_resolves(self):
        self.put("SKILL.md", "[a](docs/GUIDE.md#seção-real)\n")
        code, out = self.run_main()
        self.assertEqual((0, "Todas as âncoras e links relativos citados resolvem.\n"), (code, out))

    def test_missing_guide_is_reported_by_path(self):
        self.guide.unlink()
        with self.patched():
            problems = anchors.check()
        self.assertEqual([f"{self.guide}: arquivo não encontrado"], problems)
        code, out = self.run_main()
        self.assertEqual(1, code)
        self.assertIn("arquivo não encontrado", out)

    def test_missing_target_file_is_skipped(self):
        self.targets.append(self.tmp / "README.md")  # não existe
        self.put("SKILL.md", "sem âncoras\n")
        with self.patched():
            self.assertEqual([], anchors.check())

    def test_relative_links_are_checked_only_inside_references(self):
        ref = self.put(
            "references/a.md",
            "[ok](b.md) [ok2](b.md#titulo-b) [morto](c.md) [ancora](b.md#nada) "
            "[img](../x.png) [ext](https://x.dev/y.md)\n",
        )
        self.put("references/b.md", "# Titulo B\n")
        # fora de references/ o mesmo link quebrado é ignorado
        self.put("SKILL.md", "[morto](c.md)\n")
        self.targets.append(ref)
        with self.patched():
            problems = anchors.check()
        self.assertEqual(
            [
                "references/a.md: link quebrado c.md",
                "references/a.md: âncora quebrada b.md#nada",
            ],
            problems,
        )

    def test_dotdot_links_resolve_relative_to_the_file(self):
        ref = self.put("references/a.md", "[guia](../docs/GUIDE.md#outra) [ruim](../docs/GUIDE.md#zzz)\n")
        self.targets.append(ref)
        with self.patched():
            problems = anchors.check()
        # a mesma referência é pega pela regra GUIDE.md# e pela de link relativo
        self.assertEqual(
            [
                "references/a.md: âncora quebrada GUIDE.md#zzz",
                "references/a.md: âncora quebrada ../docs/GUIDE.md#zzz",
            ],
            problems,
        )


class ParsingTests(unittest.TestCase):
    def test_duplicate_headings_get_numeric_suffixes(self):
        slugs = anchors.extract_headings("## Dica\n\ntexto\n\n## Dica\n\n## Dica\n")
        self.assertEqual({"dica", "dica-1", "dica-2"}, slugs)

    def test_tilde_fences_and_non_headings_are_ignored(self):
        slugs = anchors.extract_headings("~~~\n## dentro\n~~~\n#sem-espaco\ntexto\n## fora\n")
        self.assertEqual({"fora"}, slugs)

    def test_find_anchor_refs_and_relative_links(self):
        text = "`${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md#storyboard` e [x](./a.md#h) e [y](b.md)"
        self.assertEqual(["storyboard"], anchors.find_anchor_refs(text))
        self.assertEqual(["./a.md#h", "b.md"], anchors.find_relative_links(text))


class ProgramTests(unittest.TestCase):
    def test_script_runs_as_a_program_on_the_real_docs(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_anchors.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn("resolvem", result.stdout)

    def test_utf8_output_reconfigures_streams(self):
        fake = mock.Mock()
        with mock.patch.object(sys, "stdout", fake), mock.patch.object(sys, "stderr", io.StringIO()):
            anchors._utf8_output()  # pylint: disable=protected-access
        fake.reconfigure.assert_called_once_with(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
