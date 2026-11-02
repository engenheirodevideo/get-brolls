"""Invariante do estado do candidato: `rejection` existe se e somente se a aprovação foi rejeitada."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls.commands import mark_rejected
from getbrolls.models import candidate, invalidate_approval, set_segment


def rejected():
    c = candidate("youtube", "abc", "titulo")
    set_segment(c, 1, 3)
    mark_rejected(c, "feio")
    return c


class RejectionInvariantTests(unittest.TestCase):
    def test_new_interval_on_a_rejected_candidate_drops_the_rejection(self):
        c = rejected()
        self.assertIn("rejection", c)
        set_segment(c, 2, 5)
        self.assertEqual(c["state"], "awaiting_approval")
        self.assertEqual(c["approval"]["status"], "pending")
        self.assertNotIn("rejection", c)

    def test_invalidate_approval_drops_the_rejection_with_or_without_a_revision_bump(self):
        for bump in (False, True):
            with self.subTest(bump=bump):
                c = rejected()
                invalidate_approval(c, bump_revision=bump)
                self.assertNotIn("rejection", c)

    def test_same_interval_keeps_a_rejected_candidate_rejected(self):
        c = rejected()
        set_segment(c, 1, 3)
        self.assertEqual(c["approval"]["status"], "rejected")
        self.assertIn("rejection", c)


if __name__ == "__main__":
    unittest.main()
