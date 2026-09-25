"""ORIGEM.md, credits.md e o README da entrega nunca interpretam texto de plugin como
Markdown/HTML: imagem remota, link e ênfase saem como texto. Fonte embutida não muda."""

import re
import tempfile
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from test_delivery import fetched, project

from getbrolls import delivery
from getbrolls.rendering import render

HOSTILE_TITLE = 'Clip <img src="https://tracker.example/p.gif"> [Licença CC0 verificada](https://evil.example/)'
OBSIDIAN_EXTRAS = " %%oculto%% ~~risco~~ ==realce== #tag $x^2$ www.evil.example https://evil.example/x"
HOSTILE_LICENSE = "**CC0 — conferida pelo core**"
HOSTILE_AUTHOR = "Autor ![x](https://tracker.example/a.png) `code` _it_ |col|"


def plugin_candidate():
    c = fetched("a", HOSTILE_TITLE + OBSIDIAN_EXTRAS)
    c["provider"] = "demo"
    c["id"] = "demo:a"
    c["creator"]["name"] = HOSTILE_AUTHOR
    c["rights"]["license_name"] = HOSTILE_LICENSE
    c["rights"]["license_url"] = "https://demo.example/l_(x)"
    c["rights"]["evidence"] = [
        "Vi a página da fonte",
        "Licença registrada pelo plugin demo: [pago](https://evil.example/) <b>ok</b>",
    ]
    return c


def assert_inert(case, text):
    # Tira cada par "barra + caractere" (escapado): o que sobra não pode ter sintaxe viva.
    live = re.sub(r"\\.", "", text)
    for raw in ("<img", "](https", "**CC0", "![x]", "`code`", "<b>", "%%", "~~", "==", "#tag", "$x", "https:/", "www."):
        case.assertNotIn(raw, live, raw)
    case.assertIn("\\<img", text)
    case.assertIn("\\[Licença CC0 verificada\\]\\(https\\:\\/\\/evil\\.example\\/\\)", text)
    case.assertIn("\\%\\%oculto\\%\\% \\~\\~risco\\~\\~ \\=\\=realce\\=\\= \\#tag \\$x\\^2\\$", text)
    case.assertIn("www\\.evil\\.example https\\:\\/\\/evil\\.example\\/x", text)
    case.assertIn("\\*\\*CC0 — conferida pelo core\\*\\*", text)
    case.assertIn("Licença registrada pelo plugin demo: \\[pago\\]", text)
    case.assertIn("Vi a página da fonte", text)


class InertPluginMarkdownTests(unittest.TestCase):
    def test_origin_renders_plugin_text_inert(self):
        assert_inert(self, delivery.render_origin(plugin_candidate(), "a.mp4"))

    def test_credits_render_plugin_text_inert(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project(tmp, [plugin_candidate()])
            render(ledger)
            assert_inert(self, (ledger.root / "credits.md").read_text(encoding="utf-8"))

    def test_builtin_text_is_unchanged(self):
        c = fetched("a", HOSTILE_TITLE + OBSIDIAN_EXTRAS)
        c["rights"]["license_name"] = HOSTILE_LICENSE
        lines = delivery.render_origin(c, "a.mp4").splitlines()
        self.assertIn("- Título na fonte: " + HOSTILE_TITLE + OBSIDIAN_EXTRAS, lines)
        self.assertIn("- Licença: " + HOSTILE_LICENSE, lines)
        self.assertIn("- Fonte: https://example.org/a", lines)
        self.assertIn("- Evidência: Condições conferidas na página da fonte", lines)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project(tmp, [c])
            render(ledger)
            credit_lines = (ledger.root / "credits.md").read_text(encoding="utf-8").splitlines()
        self.assertIn("- Licença: " + HOSTILE_LICENSE, credit_lines)


if __name__ == "__main__":
    unittest.main()
