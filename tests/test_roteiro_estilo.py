"""Cabeçalho `estilo:` (era `direcao:`): o nome antigo recusa com o novo; o plano sobe para a forma 3."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import roteiro_frontmatter, roteiro_plan
from getbrolls.sdk import schemas

HEAD = ["---", "type: roteiro", "genero: reels", "tema: x"]


class EstiloTests(unittest.TestCase):
    def test_direcao_points_to_estilo(self):
        meta, _, errors = roteiro_frontmatter.parse_frontmatter([*HEAD, "direcao: rampa-e-whip", "---"])
        self.assertNotIn("direcao", meta)
        self.assertIn((5, '"direcao" agora se chama "estilo": troque `direcao:` por `estilo:`'), errors)
        _, _, errors = roteiro_frontmatter.parse_frontmatter([*HEAD, "Direção: rampa", "---"])
        self.assertIn((5, '"Direção" agora se chama "estilo": troque `Direção:` por `estilo:`'), errors)

    def test_estilo_is_a_slug_key(self):
        meta, _, errors = roteiro_frontmatter.parse_frontmatter([*HEAD, "estilo: ritmo-seco", "---"])
        self.assertEqual(([], "ritmo-seco"), (errors, meta["estilo"]))
        _, _, errors = roteiro_frontmatter.parse_frontmatter([*HEAD, "estilo: Ritmo Seco", "---"])
        self.assertTrue(any('"estilo" tem que ser um slug' in message for _, message in errors), errors)

    def test_the_plan_and_export_versions(self):
        self.assertEqual(
            (3, 1, 2), (roteiro_plan.plan_version(), roteiro_plan.HASH_VERSION, schemas.version("export_plan"))
        )
        self.assertEqual("estilo", roteiro_plan.META_KEYS[-1])
        self.assertNotIn("direcao", roteiro_plan.META_KEYS)
