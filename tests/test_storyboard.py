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


class ContactSheetRenderingTest(unittest.TestCase):
    def render_item(self, **overrides):
        import tempfile
        from getbrolls.ledger import Ledger
        from getbrolls.models import candidate, set_segment
        from getbrolls.rendering import render

        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(d)
            c = candidate("youtube", "abc", "Foguete decolando")
            c["source_url"] = "https://www.youtube.com/watch?v=abc"
            set_segment(c, 7, 12)
            c["preview"].update(overrides)
            ledger.data["items"].append(c)
            return Path(render(ledger)).read_text(encoding="utf-8")

    def test_contact_sheet_is_inline_with_legend_when_unlabelled(self):
        page = self.render_item(
            poster_path="previews/a-poster.jpg",
            contact_sheet_path="previews/a-sheet.jpg",
            frame_times_s=[7.0, 8.3, 9.5, 10.8],
            sheet_grid=[4, 1],
            sheet_labels=False,
        )
        self.assertIn('<figure class="contact-sheet">', page)
        self.assertIn('<img src="previews/a-sheet.jpg"', page)
        self.assertIn("1 = 7,0 s · 2 = 8,3 s · 3 = 9,5 s · 4 = 10,8 s", page)
        self.assertIn("Contact sheet · 4 quadros · grade 4×1 · corte 0:07.0–0:12.0", page)
        self.assertIn("Prévia do trecho", page)
        self.assertNotIn("Sem prévia", page)
        self.assertNotIn("Ver contact sheet", page)

    def test_labelled_sheet_has_no_legend(self):
        page = self.render_item(
            poster_path="previews/a-poster.jpg",
            contact_sheet_path="previews/a-sheet.jpg",
            frame_times_s=[7.0, 9.5],
            sheet_grid=[2, 1],
            sheet_labels=True,
        )
        self.assertIn('<figure class="contact-sheet">', page)
        self.assertNotIn('<p class="sheet-legend">', page)

    def test_source_thumbnail_is_never_called_a_preview(self):
        page = self.render_item(poster_url="https://i.ytimg.com/vi/abc/hq.jpg")
        self.assertIn("Miniatura da fonte · sem prévia", page)
        self.assertIn('<span class="preview-badge">Sem prévia</span>', page)
        self.assertNotIn("Prévia do trecho", page)
        self.assertNotIn('alt="Prévia', page)
        self.assertIn('alt="Miniatura da fonte — Foguete decolando"', page)
