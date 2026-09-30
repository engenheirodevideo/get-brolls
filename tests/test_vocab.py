"""O vocabulário compartilhado (`getbrolls.vocab`) é a única fonte dos enums.

Os schemas publicados, o brief, o RULES, a CLI e o SDK repetem os mesmos valores;
aqui cada repetição é conferida contra a tupla do módulo folha, e o módulo folha
continua sem importar nada do get-brolls.
"""

import json
import subprocess
import sys
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import assets, brief, models, vocab
from getbrolls.sdk import contracts


def _schema(name):
    return json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


class CandidateSchemaMatchesVocab(unittest.TestCase):
    def setUp(self):
        self.props = _schema("candidate")["properties"]

    def test_state(self):
        self.assertEqual(list(vocab.CANDIDATE_STATES), self.props["state"]["enum"])

    def test_approval_status(self):
        self.assertEqual(list(vocab.APPROVAL_STATUSES), self.props["approval"]["properties"]["status"]["enum"])

    def test_rights_status(self):
        self.assertEqual(list(vocab.RIGHTS_STATUSES), self.props["rights"]["properties"]["status"]["enum"])

    def test_delivery_method_without_null(self):
        methods = self.props["delivery"]["properties"]["method"]["enum"]
        self.assertIn(None, methods)
        self.assertEqual(list(vocab.DELIVERY_METHODS), [m for m in methods if m is not None])

    def test_match_kind(self):
        self.assertEqual(list(vocab.MATCH_KINDS), self.props["match"]["properties"]["kind"]["enum"])

    def test_media_kind(self):
        self.assertEqual(list(vocab.MEDIA_KINDS), self.props["media"]["properties"]["kind"]["enum"])


class BriefSchemaMatchesVocab(unittest.TestCase):
    def setUp(self):
        self.props = _schema("brief")["properties"]

    def test_delivery_format(self):
        fmt = self.props["video"]["properties"]["delivery"]["properties"]["format"]["enum"]
        self.assertEqual(list(vocab.FORMATS), fmt)

    def test_both_intents(self):
        default_intent = self.props["defaults"]["properties"]["intent"]["enum"]
        beat_intent = self.props["beats"]["items"]["properties"]["intent"]["enum"]
        self.assertEqual(list(vocab.MATCH_KINDS), default_intent)
        self.assertEqual(list(vocab.MATCH_KINDS), beat_intent)


class CoreUsesVocab(unittest.TestCase):
    def test_brief_and_sdk_share_the_same_objects(self):
        self.assertIs(brief.INTENTS, contracts.MATCH_KINDS)
        self.assertIs(brief.FORMATS, vocab.FORMATS)
        self.assertIs(contracts.MATCH_KINDS, vocab.MATCH_KINDS)
        self.assertIs(contracts.MEDIA_KINDS, vocab.MEDIA_KINDS)

    def test_models_reexports_candidate_states(self):
        self.assertIs(models.CANDIDATE_STATES, vocab.CANDIDATE_STATES)
        self.assertIn(models.candidate("youtube", "x", "t")["state"], vocab.CANDIDATE_STATES)
        self.assertIn(models.pending_approval()["status"], vocab.APPROVAL_STATUSES)

    def test_resolver_kinds_are_licensed_asset_kinds(self):
        licensed = {k for k, spec in assets.ASSET_KINDS.items() if spec.licensed}
        self.assertLessEqual(set(contracts.RESOLVER_KINDS), licensed)

    def test_cli_format_choices_follow_vocab(self):
        from getbrolls import cli  # pylint: disable=import-outside-toplevel  # só este teste precisa do parser

        parser = cli.build_parser()
        sub = next(a for a in parser._actions if isinstance(a.choices, dict) and "init-rules" in a.choices)  # pylint: disable=protected-access
        assert isinstance(sub.choices, dict)
        init_rules = sub.choices["init-rules"]
        fmt = next(a for a in init_rules._actions if a.dest == "video_format")  # pylint: disable=protected-access
        self.assertEqual(list(vocab.FORMATS), list(fmt.choices))

    def test_vocab_is_a_leaf(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import getbrolls.vocab; "
            "print(sorted(m for m in sys.modules if m.startswith('getbrolls')))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code, str(ROOT / "scripts")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual("['getbrolls', 'getbrolls.vocab']", out.strip())


if __name__ == "__main__":
    unittest.main()
