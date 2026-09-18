"""Garante que toda âncora `GUIDE.md#...` citada nos docs do plugin resolve
para um heading real de `docs/GUIDE.md` (contrato de docs, não de código)."""

import unittest
from pathlib import Path

import check_anchors as anchors  # noqa: E402


class TestDocAnchors(unittest.TestCase):
    def test_no_broken_guide_anchors(self):
        problems = anchors.check()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_slugify_matches_github_style(self):
        self.assertEqual(anchors.slugify("Instalação"), "instalação")
        self.assertEqual(anchors.slugify("Fontes e transportes"), "fontes-e-transportes")
        self.assertEqual(
            anchors.slugify("Instagram — navegador/Playwright, dois streams e MP4"),
            "instagram--navegadorplaywright-dois-streams-e-mp4",
        )
        self.assertEqual(
            anchors.slugify("Bancos — busca, prévia e coleta"),
            "bancos--busca-prévia-e-coleta",
        )

    def test_extract_headings_ignores_fenced_code(self):
        markdown = "\n".join(
            [
                "## Real heading",
                "```text",
                "## Not a heading",
                "```",
                "### Another real heading",
            ]
        )
        self.assertEqual(
            anchors.extract_headings(markdown),
            {"real-heading", "another-real-heading"},
        )

    def test_check_reports_broken_anchor_against_a_fake_guide(self):
        """check() must actually flag a bad anchor end-to-end, not just parse it."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            guide = tmp_path / "GUIDE.md"
            guide.write_text("## Seção real\n", encoding="utf-8")
            target = tmp_path / "SKILL.md"
            target.write_text(
                "veja [aqui](docs/GUIDE.md#secao-que-nao-existe) e [ali](docs/GUIDE.md#seção-real)\n",
                encoding="utf-8",
            )

            original = (anchors.ROOT, anchors.GUIDE_PATH, anchors.TARGET_FILES)
            anchors.ROOT = tmp_path
            anchors.GUIDE_PATH = guide
            anchors.TARGET_FILES = [target]
            try:
                problems = anchors.check()
            finally:
                anchors.ROOT, anchors.GUIDE_PATH, anchors.TARGET_FILES = original

            self.assertEqual(len(problems), 1, problems)
            self.assertIn("secao-que-nao-existe", problems[0])


if __name__ == "__main__":
    unittest.main()
