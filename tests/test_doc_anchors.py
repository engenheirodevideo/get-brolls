"""Garante que toda âncora `GUIDE.md#...` citada nos docs do plugin resolve
para um heading real de `docs/GUIDE.md` (contrato de docs, não de código)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

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

    def test_detects_broken_anchor(self):
        guide_slugs = anchors.extract_headings(anchors.GUIDE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("secao-que-nao-existe", guide_slugs)
        refs = anchors.find_anchor_refs("veja [aqui](docs/GUIDE.md#secao-que-nao-existe)")
        self.assertEqual(refs, ["secao-que-nao-existe"])


if __name__ == "__main__":
    unittest.main()
