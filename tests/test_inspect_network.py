"""Prova de rede, opt-in: o `inspect` volta com falas de verdade de uma fonte real.

A suíte comum usa um yt-dlp dublê, e um dublê generoso demais foi justamente o que
escondeu o bug de `--dump-single-json` implicar `--simulate` — nenhum `.vtt` chegava
ao disco e o `inspect` devolvia zero cues em toda blind round. Este arquivo só roda
com `GB_EVAL_NETWORK=1`, porque pede o vídeo de verdade ao YouTube.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)

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


if __name__ == "__main__":
    unittest.main()
