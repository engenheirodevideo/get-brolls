"""Prova de rede, opt-in: o `inspect` volta com falas de verdade de uma fonte real.

A suíte comum usa um yt-dlp dublê, e um dublê generoso demais foi justamente o que
escondeu o bug de `--dump-single-json` implicar `--simulate` — nenhum `.vtt` chegava
ao disco e o `inspect` devolvia zero cues em toda blind round. Este arquivo só roda
com `GB_EVAL_NETWORK=1`, porque pede o vídeo de verdade ao YouTube.
"""

import os
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import inspecting, social

# 95 s, legenda automática em pt, sobre conta de luz.
URL = "https://www.youtube.com/watch?v=AV8Rv74TPGE"
QUERY = "conta de luz"


@unittest.skipUnless(os.environ.get("GB_EVAL_NETWORK") == "1", "rede: defina GB_EVAL_NETWORK=1")
class InspectOverTheNetworkTests(unittest.TestCase):
    def test_a_real_youtube_source_comes_back_with_cues_and_a_window_with_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = social.probe_remote(URL, cache=Path(tmp) / ".getbrolls-sources")
        cues = (probe["subtitles"].get("pt") or {}).get("cues") or []
        self.assertGreaterEqual(len(cues), 1, f"nenhuma fala lida; idiomas: {probe['subtitle_langs']}")
        self.assertTrue(probe["duration_s"])
        windows = inspecting.candidate_windows(probe, QUERY, 3)
        with_text = [w for w in windows if (w.get("text") or "").strip()]
        self.assertGreaterEqual(len(with_text), 1, f"janelas sem texto: {windows}")


@unittest.skipUnless(os.environ.get("GB_EVAL_NETWORK") == "1", "rede: defina GB_EVAL_NETWORK=1")
class ScanOverTheNetworkTests(unittest.TestCase):
    """`preview --scan` numa fonte real: a grade cobre o vídeo inteiro, do 0 ao fim.

    O dublê de yt-dlp não baixa mídia, então a varredura só é provada aqui: o que
    pode dar errado é justamente a mídia de trabalho começar fora do zero ou vir
    mais curta do que a fonte anunciou, e aí os rótulos apontam para tempos que não
    existem no vídeo.
    """

    DURATION_S = 95

    def test_the_grid_covers_the_whole_source_with_labels_inside_the_span(self):
        from getbrolls import providers
        from getbrolls.commands import scan_candidate
        from getbrolls.config import settings
        from getbrolls.ledger import Ledger

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            # Pelo `resolve` real: é ele que grava a rota de aquisição da fonte.
            c = ledger.add(providers.resolve(URL))
            ledger.save("fixture", c)
            scan_candidate(ledger, c, settings())
            scan = Ledger(tmp).get(c["id"])["scan"]
            self.assertTrue((Path(tmp) / "brolls" / scan["scan_path"]).is_file())

        self.assertEqual(0, scan["start_s"])
        self.assertAlmostEqual(self.DURATION_S, scan["end_s"], delta=2)
        self.assertAlmostEqual(self.DURATION_S, scan["duration_s"], delta=2)
        # Vídeo curto: nada foi cortado pelo teto de varredura.
        self.assertFalse(scan["capped"])
        # A grade tem uma célula por rótulo, e nenhum rótulo cai fora do trecho.
        self.assertEqual(scan["frames"], len(scan["frame_times_s"]))
        self.assertEqual(12, scan["frames"])
        for time_s in scan["frame_times_s"]:
            self.assertGreaterEqual(time_s, scan["start_s"])
            self.assertLess(time_s, scan["end_s"])
        self.assertEqual(sorted(scan["frame_times_s"]), scan["frame_times_s"])


if __name__ == "__main__":
    unittest.main()
