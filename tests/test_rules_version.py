"""`version` do RULES.md: ausente vale 1, `schema_version` é sinônimo, versão mais nova recusa."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import _paths, rules

OLD_MESSAGE = 'Em RULES.md, "version" tem que ser o número 1. Ajuste essa linha.'


def write_block(path, data):
    path.write_text(
        "# Regras\n\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n", encoding="utf-8"
    )


def _template_block():
    return rules.read_rules_block(_paths.data_path("docs", "RULES.md"))


class RulesVersionTests(unittest.TestCase):
    def setUp(self):
        # Ciclo de vida cobre o teste inteiro; a limpeza é feita via addCleanup.
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir(parents=True)
        for key in ("GB_HOME", "GB_RULES_FILE"):
            old = os.environ.get(key)
            self.addCleanup(
                lambda k=key, v=old: os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
            )
            os.environ.pop(key, None)
        os.environ["GB_HOME"] = str(Path(self.tmp.name) / "home")

    def load(self, **changes):
        block = {k: v for k, v in _template_block().items() if k != "version"}
        block.update({k: v for k, v in changes.items() if v is not None})
        write_block(self.project / "RULES.md", block)
        return rules.load_rules(self.project)

    def test_absent_version_means_one(self):
        self.assertEqual(1, self.load()["version"])

    def test_schema_version_is_a_synonym(self):
        self.assertEqual(1, self.load(schema_version=1)["version"])

    def test_both_present_and_different_is_refused(self):
        with self.assertRaisesRegex(ValueError, '"version" e "schema_version" dizem coisas diferentes'):
            self.load(version=1, schema_version=2)

    def test_both_present_and_equal_passes(self):
        self.assertEqual(1, self.load(version=1, schema_version=1)["version"])

    def test_newer_version_uses_the_standard_phrase(self):
        with self.assertRaisesRegex(ValueError, "RULES.md foi gravado por uma versão mais nova do get-brolls"):
            self.load(version=2)
        with self.assertRaisesRegex(ValueError, "RULES.md foi gravado por uma versão mais nova do get-brolls"):
            self.load(schema_version=3)

    def test_wrong_type_keeps_the_old_message_byte_for_byte(self):
        for bad in ("1", True, 1.0, 0):
            with self.subTest(value=bad), self.assertRaises(ValueError) as ctx:
                self.load(version=bad)
            self.assertEqual(OLD_MESSAGE, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
