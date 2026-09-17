import base64, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls.storyboard import render_page

ROOT = Path(__file__).resolve().parents[1]


class StoryboardTest(unittest.TestCase):
    def test_empty_and_escaped_portable_review(self):
        page = render_page([], title="Teste <script>")
        self.assertIn("Nada aqui ainda", page)
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
        self.assertIn("Os quadros do trecho (4) · grade 4×1 · corte 0:07.0–0:12.0", page)
        self.assertIn("Trecho do vídeo", page)
        self.assertNotIn("Contact sheet", page)
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
        self.assertIn("Imagem da fonte · sem prévia em movimento", page)
        self.assertIn('<span class="preview-badge">só imagem</span>', page)
        self.assertNotIn("Trecho do vídeo", page)
        self.assertNotIn('alt="Prévia', page)
        self.assertIn('alt="Miniatura da fonte — Foguete decolando"', page)


class StoryboardV2Test(unittest.TestCase):
    """The generator ships the approved V2 template (source card, speech bubble, decisions)."""

    def render_two(self):
        import tempfile
        from getbrolls.ledger import Ledger
        from getbrolls.models import candidate, set_segment
        from getbrolls.rendering import render

        with tempfile.TemporaryDirectory() as d:
            ledger = Ledger(d)
            bare = candidate("youtube", "one", "Sem prévia ainda")
            bare["source_url"] = "https://www.youtube.com/watch?v=one"
            bare["preview"]["poster_url"] = "https://i.ytimg.com/vi/one/hq.jpg"
            shown = candidate("youtube", "two", "Foguete <decolando>")
            shown["source_url"] = "https://www.youtube.com/watch?v=two"
            shown["creator"]["name"] = "KHOU 11"
            shown["media"]["duration_s"] = 122.0
            set_segment(shown, 59, 65)
            shown["narration"] = "e o foguete saiu do chão"
            shown["preview"].update(
                poster_path="previews/b-poster.jpg",
                gif_path="previews/b.gif",
                contact_sheet_path="previews/b-sheet.jpg",
                frame_times_s=[59.0, 62.0],
                sheet_grid=[2, 1],
                sheet_labels=True,
            )
            ledger.data["items"] += [bare, shown]
            return Path(render(ledger)).read_text(encoding="utf-8")

    def test_header_gallery_and_panels_follow_v2(self):
        page = self.render_two()
        self.assertIn('<header class="artifact-header">', page)
        self.assertIn('<span class="wordmark">engenheiro<span>de vídeo<b>.</b></span></span>', page)
        self.assertIn("<span>2 quadros</span>", page)
        self.assertIn('<div class="gallery-head"><h2>Storyboard</h2>', page)
        self.assertIn('data-storyboard-mode="hover"', page)
        self.assertIn('id="pending-only"', page)
        # Source card, speech bubble and the three decisions.
        self.assertIn('<a class="source-link-card" href="https://www.youtube.com/watch?v=two"', page)
        self.assertIn('<span class="source-domain">youtube.com</span>', page)
        self.assertIn("<strong>Foguete &lt;decolando&gt;</strong>", page)
        self.assertIn("<p>corte 0:59.0–1:05.0 de 2:02.0</p>", page)
        self.assertIn("Por que eu escolhi este:", page)
        self.assertIn("Pode usar? ainda não conferido", page)
        self.assertIn('<span class="cut-position"', page)
        self.assertIn("Abrir fonte original ↗", page)
        self.assertIn('<span class="script-label">Fala do roteiro</span>', page)
        self.assertIn("“e o foguete saiu do chão”", page)
        # Três botões: "outra fonte" virou caixinha dentro de "Pedir ajuste"; o valor
        # exportado `alternative` segue existindo no JS/no schema.
        for decision in ("approved", "changes", "rejected"):
            self.assertIn(f'data-decision="{decision}"', page)
        self.assertNotIn('data-decision="alternative"', page)
        self.assertIn("<h2>Esse trecho serve?</h2>", page)
        self.assertIn("data-alternative", page)
        self.assertIn('title="Descarta o trecho. Eu não baixo ele."', page)
        self.assertIn('class="comment-toggle"', page)
        # Presenter interval in MM:SS.ff and the first frame with a preview flagged.
        self.assertIn("00:59.00–01:05.00", page)
        self.assertIn('id="shot-0" data-preview="0"', page)
        self.assertIn('id="shot-1" data-preview="1"', page)
        self.assertIn('data-animated-thumb="previews/b.gif"', page)
        # The old toolbar and the "Revisar trecho" panel are gone.
        self.assertNotIn('<section class="review-toolbar">', page)
        self.assertNotIn("Revisar trecho", page)

    def test_brand_logo_is_not_cropped(self):
        page = self.render_two()
        css = page.split("</style>")[0]
        self.assertIn(".brand-logo{display:block;width:48px;height:48px;max-width:none;object-fit:contain", css)
        self.assertNotIn("brand-logo-frame", page)
