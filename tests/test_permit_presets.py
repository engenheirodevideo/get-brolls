"""#44: `permit --preset` escreve as condições genéricas da fonte — nunca uma licença."""

import json
import subprocess
import sys
import tempfile
import unittest
from typing import Any

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import CLI

from getbrolls.commands import PERMIT_PRESETS


def call(*args, ok=True) -> Any:
    proc = subprocess.run(
        [sys.executable, str(CLI), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if ok:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(proc.stdout)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    return proc.stdout + proc.stderr


class PermitPresetTests(unittest.TestCase):
    def _candidate(self, root):
        return call(
            "resolve",
            "--url",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "--project",
            root,
        )

    def test_every_preset_points_back_to_the_source_page(self):
        for name, preset in PERMIT_PRESETS.items():
            with self.subTest(preset=name):
                self.assertIn("verifique a página da fonte: ", preset["text"])
                self.assertTrue(preset["text"].startswith(preset["text"].strip()))
                self.assertTrue(preset["url"].startswith("https://"))
                self.assertTrue(preset["text"].endswith(preset["url"]))

    def test_preset_records_the_generic_terms_as_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._candidate(tmp)
            result = call("permit", "--candidate", c["id"], "--preset", "youtube", "--project", tmp)
            self.assertEqual("permitted", result["rights"]["status"])
            self.assertEqual("per_item_evidence", result["rights"]["basis"])
            self.assertEqual(1, len(result["rights"]["evidence"]))
            self.assertIn("verifique a página da fonte: ", result["rights"]["evidence"][0])

    def test_preset_with_evidence_concatenates_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._candidate(tmp)
            result = call(
                "permit",
                "--candidate",
                c["id"],
                "--preset",
                "commons",
                "--evidence",
                "Licença CC BY-SA 4.0 declarada na página do arquivo.",
                "--project",
                tmp,
            )
            evidence = result["rights"]["evidence"][0]
            self.assertIn("verifique a página da fonte: ", evidence)
            self.assertIn("Licença CC BY-SA 4.0", evidence)

    def test_preset_does_not_unlock_fetch_by_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._candidate(tmp)
            call("permit", "--candidate", c["id"], "--preset", "nasa", "--project", tmp)
            call("fetch", "--candidate", c["id"], "--project", tmp, ok=False)

    def test_unknown_preset_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._candidate(tmp)
            call(
                "permit",
                "--candidate",
                c["id"],
                "--preset",
                "vimeo",
                "--project",
                tmp,
                ok=False,
            )


if __name__ == "__main__":
    unittest.main()
