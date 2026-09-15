import re, struct, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls.storyboard import render_page

ROOT = Path(__file__).resolve().parents[1]


class StoryboardTest(unittest.TestCase):
    def test_empty_and_escaped_portable_review(self):
        page = render_page([], title="Teste <script>")
        self.assertIn("Nenhum quadro", page)
        self.assertIn("Teste &lt;script&gt;", page)
        self.assertNotIn("data:font/ttf;base64,", page)
        self.assertIn("system-ui", page)

    def test_gallery_and_detail_share_items(self):
        page = render_page(
            [
                {
                    "title": "Plano <1>",
                    "content": "<p>Fonte verificada</p>",
                    "narration": "Fala & contexto",
                    "time": "2–8 s",
                }
            ]
        )
        self.assertIn("Plano &lt;1&gt;", page)
        self.assertIn('data-index="0"', page)
        self.assertIn('id="shot-0"', page)
        self.assertIn("Fala &amp; contexto", page)

    def test_delivery_includes_storyboard_artifact_with_current_logo(self):
        artifact = ROOT / "assets" / "storyboard-template.html"
        logo = ROOT / "assets" / "brand-logo.png"

        self.assertTrue(artifact.is_file())
        self.assertTrue(logo.is_file())
        data = logo.read_bytes()
        self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
        width, height, _, color_type, _, _, _ = struct.unpack(">IIBBBBB", data[16:29])
        self.assertEqual((334, 333), (width, height))
        self.assertIn(color_type, (4, 6), "A logo precisa manter transparência.")
        page = artifact.read_text()
        self.assertIn('class="brand-logo"', page)
        self.assertIn('src="brand-logo.png"', page)
        self.assertNotIn('<div class="brand"><svg', page)
        self.assertNotIn("<base ", page)

        local_refs = re.findall(r'(?:src|href)="(?!https?:|data:|#)([^"?]+)', page)
        missing = [ref for ref in local_refs if not (artifact.parent / ref).is_file()]
        self.assertEqual([], missing, f"Recursos ausentes no template: {missing}")


if __name__ == "__main__":
    unittest.main()
