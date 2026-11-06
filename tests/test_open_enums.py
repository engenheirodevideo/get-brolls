"""Enums abertos: valor desconhecido avisa `ENUM_UNKNOWN` uma vez por processo, campo e valor, e nunca é erro."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import open_enums, runtime


class KnownTests(unittest.TestCase):
    def setUp(self):
        open_enums.reset()
        self.addCleanup(open_enums.reset)
        self.event = {"warnings": []}
        token = runtime.ACTIVE.set(self.event)
        self.addCleanup(runtime.ACTIVE.reset, token)

    def codes(self):
        return [row["code"] for row in self.event["warnings"]]

    def test_a_known_value_passes_without_a_warning(self):
        self.assertTrue(open_enums.known("tier", "project", ("project", "client")))
        self.assertEqual([], self.codes())

    def test_an_unknown_value_warns_once_per_field_and_value(self):
        for _ in range(2):
            self.assertFalse(open_enums.known("tier", "nivel_futuro", ("project", "client")))
        self.assertFalse(open_enums.known("component_kind", "tipo_futuro", ("sfx",)))
        self.assertEqual(["ENUM_UNKNOWN", "ENUM_UNKNOWN"], self.codes())
        self.assertIn('nível "nivel_futuro" é desconhecido', self.event["warnings"][0]["message"])

    def test_a_field_without_a_label_keeps_its_name_and_reset_warns_again(self):
        open_enums.known("transicao", "giro", ())
        open_enums.reset()
        open_enums.known("transicao", "giro", ())
        self.assertEqual(2, self.codes().count("ENUM_UNKNOWN"))
        self.assertIn('transicao "giro"', self.event["warnings"][0]["message"])


if __name__ == "__main__":
    unittest.main()
