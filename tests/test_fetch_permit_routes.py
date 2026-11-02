"""A recusa do `fetch` sem direitos registrados cita as três rotas do `permit`.

L-10: a mensagem dizia só "permit --evidence", e quem tinha preset da fonte
(`--preset nasa`) ou uma declaração própria (`--declared-by`) não ficava sabendo.
"""

import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls.models import approve, candidate, require_fetch, set_segment


class FetchNamesEveryPermitRoute(unittest.TestCase):
    def test_the_refusal_lists_evidence_preset_and_declaration(self):
        item = candidate("nasa", "abc", "Aprovado sem direitos")
        set_segment(item, 0, 2)
        approve(item, "Pessoa Humana")
        with self.assertRaises(ValueError) as caught:
            require_fetch(item)
        message = str(caught.exception)
        for route in ("--evidence", "--preset", "--declared-by"):
            self.assertIn(route, message)


if __name__ == "__main__":
    unittest.main()
