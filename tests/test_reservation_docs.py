"""A doc diz o que esta versão só nomeia: ids, campos, diretivas e contribuições reservadas."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls.roteiro import RESERVED_DIRECTIVES
from getbrolls.sdk.contracts import RESERVED_IDS

GUIDE = (ROOT / "docs" / "GUIDE.md").read_text(encoding="utf-8")
SDK = (ROOT / "docs" / "SDK.md").read_text(encoding="utf-8")
ROTEIRO = (ROOT / "references" / "roteiro.md").read_text(encoding="utf-8")


class ReservationDocsTests(unittest.TestCase):
    def test_guide_names_brolls_as_the_state_folder(self):
        self.assertIn("`brolls/` é a pasta de estado do get-brolls no projeto", GUIDE)

    def test_roteiro_reference_covers_frontmatter_and_reserved_directives(self):
        for fragment in ("`cliente`", "`direcao`", "slug", "`tags`", "ignorada", "reservad"):
            self.assertIn(fragment, ROTEIRO)
        for name in RESERVED_DIRECTIVES:
            self.assertIn(f"[{name}:", ROTEIRO)
        self.assertIn("getbrolls-plan.json", ROTEIRO)

    def test_sdk_lists_reserved_contributions_ids_and_plan_fields(self):
        for kind in ("capturers", "engines", "catalogs", "roteiro_templates"):
            self.assertIn(f"`{kind}`", SDK)
        for ident in RESERVED_IDS:
            self.assertIn(f"`{ident}`", SDK)
        for field in ("projeto_id", "cliente", "direcao", "fps", "canvas", "ref", "direction", '"client"'):
            self.assertIn(field, SDK)
        self.assertIn("getbrolls-plan.json", SDK)


if __name__ == "__main__":
    unittest.main()
