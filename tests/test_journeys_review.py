"""Jornada da revisão pelo Storyboard: prévia → `review` → decisão exportada → `import-review` → permit → fetch.

É o outro caminho de aprovação (o do `approve` está em `test_journeys_collect`). A decisão da página só vale
para o trecho que a pessoa viu: mudou o intervalo, o `import-review` pula aquele item, e gerar a página de novo
conserta. Pela CLI real, com o plugin `demo` local e FFmpeg de verdade.
"""

import json
import re
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _journeys import JourneyCase, write_brief
from _media import skip_unless_ffmpeg

FIRST = "demo:1:shot:abertura"
SECOND = "demo:2:shot:abertura"


@skip_unless_ffmpeg
class StoryboardJourney(JourneyCase):
    def setUp(self):
        super().setUp()
        self.enable_demo()
        write_brief(self.project)
        self.search("mar calmo", "abertura")
        self.gb("preview", "--candidate", FIRST, "--start", 0, "--end", 2)

    def exported(self, states):
        """A página gerada pelo `review` e as decisões que a pessoa marcaria nela (`states`: id → estado)."""
        page = Path(self.gb("review")["review"]).read_text(encoding="utf-8")
        found = re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
        assert found is not None, "review.html sem o payload embutido"
        payload = json.loads(found.group(1))
        for row in payload["items"]:
            row["state"] = states.get(row["id"], row["state"])
        return payload

    def save(self, payload, name="20261029-120000.json"):
        folder = self.project / "brolls" / "reviews"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_a_decision_from_the_page_approves_the_previewed_range_and_the_clip_is_collected(self):
        payload = self.exported({FIRST: "approved"})
        self.assertEqual("getbrolls-review", payload["type"])
        self.assertEqual({FIRST: "pending", SECOND: "pending"}, {row["id"]: "pending" for row in payload["items"]})
        self.save(payload)
        imported = self.gb("import-review", "--by", "Ana")
        self.assertEqual([], imported["skipped"])
        approval = self.item(FIRST)["approval"]
        self.assertEqual(("approved", "Ana", "storyboard"), (approval["status"], approval["by"], approval["channel"]))
        self.assertEqual("pending", self.item(SECOND)["approval"]["status"])
        self.permit(FIRST)
        fetched = self.gb("fetch", "--candidate", FIRST)
        self.assertTrue(fetched["output"]["verified"])
        self.assertTrue((self.project / "brolls" / fetched["output"]["path"]).is_file())

    def test_a_rejection_from_the_page_blocks_the_fetch_until_the_person_decides_again(self):
        self.save(self.exported({FIRST: "rejected"}))
        self.gb("import-review", "--by", "Ana")
        self.assertEqual("rejected", self.item(FIRST)["approval"]["status"])
        self.permit(FIRST)
        refused = self.gb("fetch", "--candidate", FIRST, expect=1)
        self.assertIn("Aprovação humana ausente", refused["error"])
        self.approve(FIRST)
        self.assertTrue(self.gb("fetch", "--candidate", FIRST)["output"]["verified"])

    def test_a_decision_made_before_the_range_changed_is_skipped_and_a_new_page_fixes_it(self):
        stale = self.exported({FIRST: "approved"})
        self.gb("preview", "--candidate", FIRST, "--start", 1, "--end", 3)
        self.save(stale)
        skipped = self.gb("import-review", "--by", "Ana")["skipped"]
        self.assertEqual([(FIRST, "signature_mismatch")], [(row["id"], row["reason"]) for row in skipped])
        self.assertEqual("pending", self.item(FIRST)["approval"]["status"])
        self.permit(FIRST)
        self.assertIn("Aprovação humana ausente", self.gb("fetch", "--candidate", FIRST, expect=1)["error"])
        # Recuperação: página nova, decisão nova, trecho novo.
        self.save(self.exported({FIRST: "approved"}), "20261029-130000.json")
        again = self.gb(
            "import-review", "--by", "Ana", "--file", self.project / "brolls" / "reviews" / "20261029-130000.json"
        )
        self.assertEqual([], again["skipped"])
        fetched = self.gb("fetch", "--candidate", FIRST)
        self.assertEqual(1.0, fetched["segment"]["start_s"])
        self.assertTrue(fetched["output"]["verified"])

    def test_approving_a_candidate_nobody_previewed_fails_the_whole_file_and_changes_nothing(self):
        self.save(self.exported({FIRST: "approved", SECOND: "approved"}))
        snapshot = self.ledger_bytes()
        error = self.gb("import-review", "--by", "Ana", expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("intervalo", error["error"])
        self.assertEqual(snapshot, self.ledger_bytes())
        # Recuperação: o mesmo arquivo sem o item sem prévia entra.
        self.save(self.exported({FIRST: "approved"}))
        self.assertEqual([], self.gb("import-review", "--by", "Ana")["skipped"])
        self.assertEqual("approved", self.item(FIRST)["approval"]["status"])

    def test_importing_without_any_saved_decision_or_without_a_name_is_refused(self):
        self.gb("review")
        error = self.gb("import-review", "--by", "Ana", expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertEqual("pending", self.item(FIRST)["approval"]["status"])
        usage = self.raw("import-review")
        self.assertEqual(2, usage.returncode)
        self.assertIn("--by", usage.stderr)


if __name__ == "__main__":
    unittest.main()
