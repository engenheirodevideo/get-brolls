"""`search --shot`: o beat do BRIEF.md decide de que fonte o material dele pode vir.

`search --provider youtube --shot abertura` num beat que só permite NASA era
aceito calado, e o candidato ficava ligado ao beat como se a fonte fosse permitida.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)
from test_brief import VALID, write_brief
from test_search_flags import args, found, manifest

from getbrolls import providers
from getbrolls.commands import execute
from getbrolls.runtime import audited


def brief_with(project, **sources_by_beat):
    data = copy.deepcopy(VALID)
    for beat in data["beats"]:
        if beat["id"] in sources_by_beat:
            beat["allowed_sources"] = sources_by_beat[beat["id"]]
    write_brief(project, data)


class BeatSourcesAreEnforced(unittest.TestCase):
    def test_an_explicit_provider_outside_the_beat_is_refused_with_the_allowed_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief_with(tmp, abertura=["nasa", "commons"])
            with (
                patch.object(providers, "search", return_value=found()) as search,
                self.assertRaises(Exception) as caught,
            ):
                audited(args(tmp, provider="youtube", shot="abertura"), execute)
            message = str(caught.exception)
            self.assertIn("abertura", message)
            self.assertIn("youtube", message)
            self.assertIn("nasa, commons", message)
            search.assert_not_called()
            self.assertEqual([], manifest(tmp)["items"])

    def test_a_beat_with_only_local_material_points_to_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief_with(tmp, abertura=["local"])
            with patch.object(providers, "search", return_value=found()), self.assertRaises(Exception) as caught:
                audited(args(tmp, provider="youtube", shot="abertura"), execute)
            self.assertIn("resolve", str(caught.exception))

    def test_an_allowed_provider_still_searches_and_links_the_beat(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief_with(tmp, abertura=["youtube"])
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp, provider="youtube", shot="abertura"), execute)
            self.assertEqual("abertura", result["items"][0]["shot"])

    def test_auto_only_asks_the_sources_the_beat_allows(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief_with(tmp, abertura=["nasa"])
            with patch.object(providers, "search", return_value=[]) as search:
                audited(args(tmp, provider="auto", shot="abertura"), execute)
            self.assertEqual({"nasa"}, {call.args[0] for call in search.call_args_list})

    def test_a_shot_outside_the_brief_and_a_project_without_brief_keep_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief_with(tmp, abertura=["nasa"])
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp, provider="youtube", shot="cena-livre"), execute)
            self.assertEqual("cena-livre", result["items"][0]["shot"])
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp, provider="youtube", shot="abertura"), execute)
            self.assertEqual("abertura", result["items"][0]["shot"])

    def test_a_broken_brief_does_not_block_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "BRIEF.md").write_text("```json\n" + json.dumps({"version": 2}) + "\n```\n", encoding="utf-8")
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp, provider="youtube", shot="abertura"), execute)
            self.assertEqual(1, len(result["items"]))


if __name__ == "__main__":
    unittest.main()
