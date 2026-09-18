"""Caracterização de invalidação de aprovação.

Fixa o dicionário inteiro do candidato, campo a campo, para os dois caminhos
que hoje invalidam uma aprovação já dada: `set_segment` (models.py) e o laço
de `sync_formats` (rules.py). Os dois caminhos NÃO são idênticos hoje — este
arquivo prova exatamente onde divergem, para que uma extração de helper
compartilhado não os unifique por acidente.
"""

import copy
import tempfile
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.ledger import Ledger
from getbrolls.models import approve, candidate, set_segment
from getbrolls.rules import format_report, load_rules, sync_formats


class SetSegmentInvalidationTests(unittest.TestCase):
    """`set_segment` ao mudar um intervalo já aprovado (models.py:73-90)."""

    def _approved_candidate(self):
        c = candidate("local", "x", "X", "https://example.org/a")
        set_segment(c, 0, 1)
        approve(c, "Human", "chat", "Aprovo este trecho.")
        # Popula campos extras para provar exatamente o que sobrevive à
        # reconstrução do `preview` e o que é descartado.
        c["preview"]["poster_path"] = "should-be-dropped.jpg"
        c["preview"]["contact_sheet_path"] = "should-be-dropped-sheet.jpg"
        c["preview"]["poster_url"] = "kept-poster-url.jpg"
        c["preview"]["embed_url"] = "kept-embed"
        c["preview"]["seek_mode"] = "kept-mode"
        c["review"] = {"state": "approved", "signature": "whatever", "at": "2026-01-01T00:00:00+00:00"}
        return c

    def test_changing_the_interval_pins_the_exact_resulting_dict(self):
        c = self._approved_candidate()
        before = copy.deepcopy(c)
        self.assertEqual(before["approval"]["status"], "approved")
        self.assertIn("review", before)
        self.assertEqual(before["segment"]["revision"], 1)

        set_segment(c, 0, 2)

        expected = copy.deepcopy(before)
        # `preview` é reconstruído: só as chaves poster_url/embed_url/seek_mode
        # sobrevivem, na ordem em que apareciam no dict original — poster_path
        # e contact_sheet_path são descartados.
        expected["preview"] = {
            "poster_url": "kept-poster-url.jpg",
            "embed_url": "kept-embed",
            "seek_mode": "kept-mode",
        }
        # `review` é removido (pop), não apenas esvaziado.
        del expected["review"]
        expected["segment"] = {"start_s": 0, "end_s": 2, "revision": 2}
        expected["approval"] = {"status": "pending", "by": None, "at": None, "revision": None}
        expected["state"] = "awaiting_approval"
        expected["output"] = {"path": None, "sha256": None, "verified": False}

        self.assertEqual(c, expected)
        # Confere em particular que nada além do listado acima mudou.
        for key in before:
            if key not in ("preview", "review", "segment", "approval", "state", "output"):
                self.assertEqual(c.get(key), before[key], key)

    def test_unchanged_interval_touches_nothing(self):
        c = self._approved_candidate()
        before = copy.deepcopy(c)

        set_segment(c, 0, 1)

        self.assertEqual(c, before)


class SyncFormatsInvalidationTests(unittest.TestCase):
    """O laço de `sync_formats` (rules.py:276-294) ao trocar `video_format`."""

    def _approved_project(self, folder):
        ledger = Ledger(folder)
        rules = load_rules(folder)
        c = candidate("local", "fixture", "Synthetic", "https://example.org/a")
        c["media"].update(width=1920, height=1080)
        set_segment(c, 0, 1)
        c["format"] = format_report(c, rules)
        approve(c, "Human", "chat", "Aprovo este trecho para o vídeo.")
        # Mesmos campos extras de preview/review que a caracterização de
        # `set_segment` usa, para comparar os dois caminhos lado a lado.
        c["preview"]["poster_path"] = "previews/should-be-dropped-if-set_segment.jpg"
        c["preview"]["contact_sheet_path"] = "previews/should-be-dropped-if-set_segment.jpg"
        c["preview"]["poster_url"] = "kept-poster-url.jpg"
        c["preview"]["embed_url"] = "kept-embed"
        c["preview"]["seek_mode"] = "kept-mode"
        c["review"] = {"state": "approved", "signature": "whatever", "at": "2026-01-01T00:00:00+00:00"}
        ledger.add(c)
        ledger.save("approve", c)
        return ledger, rules

    def test_format_change_pins_the_exact_resulting_dict(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger, rules = self._approved_project(folder)
            c = ledger.data["items"][0]
            before = copy.deepcopy(c)
            self.assertEqual(before["approval"]["status"], "approved")
            self.assertIn("review", before)
            self.assertEqual(before["segment"]["revision"], 1)

            rules["video_format"] = "reels"
            sync_formats(ledger, rules, confirm=True)

            c = ledger.data["items"][0]
            expected = copy.deepcopy(before)
            # Ao contrário de `set_segment`, `preview` NÃO é tocado — sobrevive
            # inteiro, inclusive as chaves que `set_segment` descartaria.
            expected["preview"] = before["preview"]
            # `review` é removido (pop), igual a `set_segment`.
            del expected["review"]
            # `segment` NÃO é reescrito: start_s/end_s ficam como estavam, só a
            # revisão é incrementada in place.
            expected["segment"] = {
                "start_s": before["segment"]["start_s"],
                "end_s": before["segment"]["end_s"],
                "revision": before["segment"]["revision"] + 1,
            }
            expected["approval"] = {"status": "pending", "by": None, "at": None, "revision": None}
            expected["state"] = "awaiting_approval"
            expected["output"] = {"path": None, "sha256": None, "verified": False}
            expected["format"] = format_report(before, rules)

            self.assertEqual(c, expected)
            for key in before:
                if key not in ("review", "segment", "approval", "state", "output", "format"):
                    self.assertEqual(c.get(key), before[key], key)

    def test_no_format_change_leaves_approval_and_review_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger, rules = self._approved_project(folder)
            before = copy.deepcopy(ledger.data["items"][0])

            sync_formats(ledger, rules, confirm=True)

            c = ledger.data["items"][0]
            self.assertEqual(c["approval"], before["approval"])
            self.assertEqual(c.get("review"), before.get("review"))
            self.assertEqual(c["segment"], before["segment"])
            self.assertEqual(c["preview"], before["preview"])


class BothPathsDifferenceTests(unittest.TestCase):
    """Documenta lado a lado a diferença real entre os dois caminhos."""

    def test_set_segment_rebuilds_preview_sync_formats_does_not(self):
        # set_segment: preview é filtrado a poster_url/embed_url/seek_mode.
        c1 = candidate("local", "x", "X")
        set_segment(c1, 0, 1)
        c1["preview"]["poster_path"] = "survives-nothing.jpg"
        c1["preview"]["contact_sheet_path"] = "survives-nothing.jpg"
        set_segment(c1, 0, 2)
        self.assertNotIn("poster_path", c1["preview"])
        self.assertNotIn("contact_sheet_path", c1["preview"])

        # sync_formats: preview não é tocado, sobrevive inteiro.
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            rules = load_rules(folder)
            c2 = candidate("local", "fixture", "Synthetic", "https://example.org/a")
            c2["media"].update(width=1920, height=1080)
            set_segment(c2, 0, 1)
            c2["format"] = format_report(c2, rules)
            approve(c2, "Human", "chat", "Aprovo este trecho para o vídeo.")
            c2["preview"]["poster_path"] = "previews/survives.jpg"
            c2["preview"]["contact_sheet_path"] = "previews/survives.jpg"
            ledger.add(c2)
            ledger.save("approve", c2)

            rules["video_format"] = "reels"
            sync_formats(ledger, rules, confirm=True)
            c2 = ledger.data["items"][0]
            self.assertEqual(c2["preview"]["poster_path"], "previews/survives.jpg")
            self.assertEqual(c2["preview"]["contact_sheet_path"], "previews/survives.jpg")

    def test_set_segment_rewrites_segment_sync_formats_only_bumps_revision(self):
        # set_segment: start_s/end_s mudam junto com a revisão.
        c1 = candidate("local", "x", "X")
        set_segment(c1, 0, 1)
        set_segment(c1, 5, 9)
        self.assertEqual(c1["segment"], {"start_s": 5, "end_s": 9, "revision": 2})

        # sync_formats: start_s/end_s ficam como estavam, só a revisão sobe.
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            rules = load_rules(folder)
            c2 = candidate("local", "fixture", "Synthetic", "https://example.org/a")
            c2["media"].update(width=1920, height=1080)
            set_segment(c2, 3, 7)
            c2["format"] = format_report(c2, rules)
            approve(c2, "Human", "chat", "Aprovo este trecho para o vídeo.")
            ledger.add(c2)
            ledger.save("approve", c2)

            rules["video_format"] = "reels"
            sync_formats(ledger, rules, confirm=True)
            c2 = ledger.data["items"][0]
            self.assertEqual(c2["segment"], {"start_s": 3, "end_s": 7, "revision": 2})


if __name__ == "__main__":
    unittest.main()
