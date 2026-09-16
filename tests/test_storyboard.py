import base64, sys, unittest
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

    def test_rendered_storyboard_uses_current_logo(self):
        logo = ROOT / "assets" / "brand-logo.png"
        self.assertTrue(logo.is_file())
        encoded = base64.b64encode(logo.read_bytes()).decode("ascii")
        page = render_page([])
        self.assertIn('class="brand-logo"', page)
        self.assertIn(f'src="data:image/png;base64,{encoded}"', page)
        self.assertNotIn('<div class="brand"><svg', page)


if __name__ == "__main__":
    unittest.main()
