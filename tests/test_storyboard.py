import base64
import sys
import unittest
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


def render_one(**overrides):
    """Um candidato de YouTube renderizado pelo pipeline real, do ledger ao HTML."""
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


class ContactSheetRenderingTest(unittest.TestCase):
    def render_item(self, **overrides):
        return render_one(**overrides)

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


class PanelStringsSnapshotTest(unittest.TestCase):
    """As frases do painel de decisão: mudá-las é decisão de produto, não refactor.

    Elas estavam sem teste nenhum — `REVIEW_PANEL` e as mensagens do `review.js`
    passaram três rodadas de UX copy sem rede. Este teste é a rede: quem trocar um
    texto vê o teste vermelho e decide de propósito.
    """

    ASSETS = ROOT / "assets"

    def page(self):
        return render_one(
            poster_path="previews/a-poster.jpg",
            contact_sheet_path="previews/a-sheet.jpg",
        )

    def test_panel_placeholders_and_labels_are_exactly_these(self):
        page = self.page()
        self.assertIn('placeholder="Me conta em uma linha o que você queria…"', page)
        self.assertIn('<input data-suggestion type="url" placeholder="https://…">', page)
        self.assertIn("<h2>Esse trecho serve?</h2>", page)
        self.assertIn("<label>O que mudar<textarea data-comment", page)
        self.assertIn("Achou outro vídeo? cole o link (opcional)", page)
        self.assertIn(" Não é esse vídeo: procure outro</label>", page)
        self.assertIn(
            '<button class="confirm-review" type="button" hidden>Confirmar pedido de ajuste</button>',
            page,
        )
        self.assertIn('<button class="comment-toggle" type="button" aria-expanded="false">Comentar</button>', page)

    def test_the_three_decision_buttons_say_what_each_one_causes(self):
        page = self.page()
        self.assertIn('title="Marca o trecho como aprovado. Depois eu baixo ele pra sua pasta.">Aprovar</button>', page)
        self.assertIn(
            'title="Você escreve o que mudar (outro pedaço do vídeo, ou outro vídeo) e eu refaço.">Pedir ajuste</button>',
            page,
        )
        self.assertIn('title="Descarta o trecho. Eu não baixo ele.">Reprovar</button>', page)

    def test_review_js_error_and_confirm_strings_are_exactly_these(self):
        js = (self.ASSETS / "review.js").read_text(encoding="utf-8")
        self.assertIn('status.textContent = "Me conta em uma linha o que você queria.";', js)
        self.assertIn('wanted() === "changes" ? "Confirmar pedido de ajuste" : "Confirmar: procure outro vídeo"', js)
        self.assertIn('note("Não consegui salvar no projeto; baixei o arquivo em vez disso.");', js)
        self.assertIn('"Decisões salvas em getbrolls-review.json (na sua pasta de Downloads). "', js)
        self.assertIn('"Agora volte à conversa e diga onde salvou."', js)
        self.assertIn('copy.textContent = "Copiar caminho";', js)
        for state, label in (
            ("pending", "Você ainda não disse"),
            ("approved", "Aprovado"),
            ("changes", "Pedi ajuste"),
            ("rejected", "Não serve"),
            ("alternative", "Pedi outro vídeo"),
        ):
            self.assertIn(f'{state}: "{label}"', js)

    def test_the_live_region_is_mounted_empty_at_load_without_a_timer(self):
        """Determinismo: a região viva existe desde o load e o export só troca o texto."""
        js = (self.ASSETS / "review.js").read_text(encoding="utf-8")
        self.assertIn('<p class="export-done" role="status" aria-live="polite"></p>', js)
        self.assertIn('const done = document.querySelector(".export-done");', js)
        # O `setTimeout(…, 100)` que atrasava o anúncio saiu de cena.
        self.assertNotIn("bar.after(done)", js)
        self.assertNotIn("}, 100);", js)
        # O único `setTimeout` que sobra é o `revokeObjectURL`, que não é anúncio.
        self.assertEqual(1, js.count("setTimeout("))
        self.assertIn("setTimeout(() => URL.revokeObjectURL(url), 1000);", js)
        # Vazia ela não pinta faixa nenhuma na página.
        self.assertIn(".export-done:empty{display:none}", (self.ASSETS / "review.css").read_text(encoding="utf-8"))

    def test_the_panel_column_has_no_fixed_width_that_would_overflow_375px(self):
        """375 px sem overflow: nada no painel pode ter largura fixa maior que isso."""
        import re

        css = (self.ASSETS / "review.css").read_text(encoding="utf-8")
        for value in re.findall(r"(?:^|[;{])\s*(?:min-)?width:\s*(\d+)px", css):
            self.assertLessEqual(int(value), 375, f"largura fixa de {value}px estoura a tela de 375 px")


class GalleryThumbnailFallbackTest(unittest.TestCase):
    """Sem imagem, a miniatura diz só a tarja; a frase longa fica no detalhe."""

    def test_the_gallery_fallback_is_the_badge_text_and_the_panel_keeps_the_long_one(self):
        page = render_page([{"title": "Sem imagem", "content": "<p>x</p>", "no_preview": True}])
        self.assertIn('<div class="thumbs"><span class="placeholder">só imagem</span>', page)
        self.assertIn('<span class="preview-badge">só imagem</span>', page)
        # O cartão da galeria não repete a explicação comprida.
        gallery = page.split('<div class="gallery">')[1].split("<template")[0]
        self.assertNotIn("Não consegui gerar o movimento", gallery)

    def test_the_detail_panel_still_carries_the_full_explanation(self):
        page = render_one()
        panel = page[page.index("<template") :]
        self.assertIn("Não consegui gerar o movimento — veja o original no link", panel)
        self.assertNotIn("sem imagem da fonte", page)


if __name__ == "__main__":
    unittest.main()
