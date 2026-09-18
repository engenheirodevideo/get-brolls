"""Cabeçalho do contact sheet: um título hostil não pode derrubar o banner.

Executor r3 da rodada cega ficou com 1 de 5 folhas sem cabeçalho. O título dele
tinha `%`: o `drawtext` lia o `textfile` com expansão ligada, tentava expandir um
`%{...}` que não existe, e desistia do filtro — a folha saía, o cabeçalho não.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)

from getbrolls import media

# Dois-pontos separam opções do filtro, aspas simples delimitam valores e `%` abre
# uma expansão: os três caracteres que já quebraram o banner.
HOSTILE = "NASA: “Artemis” 100% real — o 'foguete' às 10:30 \\ teste"


class DrawtextEscapingTests(unittest.TestCase):
    def sheet_call(self, title):
        """Roda `review_preview` com ffmpeg falso e devolve (filtro, texto do banner)."""
        seen: dict[str, Any] = {}

        def fake_run(args):
            if "-vf" in args:
                flt = args[args.index("-vf") + 1]
                if "tile=" in flt:
                    seen["filter"] = flt
                    for part in flt.split("textfile='")[1:]:
                        path = part.split("'")[0].replace("\\:", ":")
                        seen["banner"] = Path(path).read_text(encoding="utf-8")
            Path(args[-1]).write_bytes(b"x")
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            previews = Path(tmp) / "brolls" / "previews"
            previews.mkdir(parents=True)
            config: dict[str, Any] = dict(media_settings())
            config["frames"] = 4
            config["mode"] = "static"
            with (
                patch.object(media, "run", fake_run),
                patch.object(media, "drawtext_available", return_value=True),
                patch.object(media, "find_font", return_value="/tmp/Fonte Teste.ttf"),
            ):
                media.review_preview(
                    "/tmp/origem.mp4",
                    previews,
                    "x",
                    0,
                    2,
                    config,
                    label={"title": title, "id": "youtube:abc", "duration": 95},
                )
        return seen.get("filter", ""), seen.get("banner", "")

    def test_the_banner_is_drawn_without_expansion(self):
        flt, _ = self.sheet_call(HOSTILE)
        self.assertIn("expansion=none", flt)
        # O contador por célula continua expandindo: é ele que numera os quadros.
        self.assertIn("%{eif", flt)

    def test_the_hostile_title_never_lands_inside_the_filter_string(self):
        flt, banner = self.sheet_call(HOSTILE)
        self.assertNotIn(HOSTILE, flt)
        self.assertNotIn("100% real", flt)
        self.assertNotIn("'foguete'", flt)
        # Ele chega inteiro pelo arquivo, que é o caminho seguro.
        self.assertIn(HOSTILE, banner)
        self.assertIn("[youtube:abc]", banner)
        self.assertIn("corte 0:00.0–0:02.0 de 1:35.0", banner)

    def test_control_characters_never_break_the_banner_lines(self):
        _, banner = self.sheet_call("Linha um\nlinha dois\x00\ttab")
        self.assertNotIn("\x00", banner)
        first = banner.splitlines()[0]
        self.assertEqual("Linha um linha dois  tab", first)
        # O id e o corte continuam numa linha só, abaixo do título.
        self.assertEqual(2, len(banner.splitlines()))

    def test_a_plain_title_is_untouched(self):
        _, banner = self.sheet_call("Foguete decolando")
        self.assertTrue(banner.startswith("Foguete decolando\n["))


def media_settings():
    from getbrolls.config import settings

    return settings()


if __name__ == "__main__":
    unittest.main()
