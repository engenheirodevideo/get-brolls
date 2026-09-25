"""`brief --validate` não chama de válido o modelo que ninguém preencheu.

L-6: `init-brief` seguido de `brief --validate`, sem editar nada, respondia
"válido: 1 beat" com o título "Troque pelo nome real do vídeo" — e o agente seguia
buscando pelo texto de exemplo do modelo.
"""

import copy
import tempfile
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from test_brief import VALID, write_brief


class TemplateLeftovers(unittest.TestCase):
    def test_the_untouched_template_is_not_called_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-brief", "--project", tmp)
            payload = run_cli("brief", "--validate", "--project", tmp)
        summary = payload["summary"]
        self.assertNotIn("válido", summary["line"])
        self.assertTrue(any("video.title" in problem for problem in summary["problems"]), summary["problems"])
        self.assertTrue(any('"abertura"' in problem for problem in summary["problems"]), summary["problems"])
        self.assertIn("Resolva os pontos", summary["next"])

    def test_one_placeholder_left_behind_is_named(self):
        data = copy.deepcopy(VALID)
        data["beats"][0]["target"] = "O que precisa aparecer na tela neste trecho"
        with tempfile.TemporaryDirectory() as tmp:
            write_brief(tmp, data)
            problems = run_cli("brief", "--validate", "--project", tmp)["summary"]["problems"]
        self.assertEqual(1, len(problems), problems)
        self.assertIn("target", problems[0])

    def test_a_filled_brief_stays_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_brief(tmp, VALID)
            payload = run_cli("brief", "--validate", "--project", tmp)
        self.assertIn("válido", payload["summary"]["line"])
        self.assertEqual([], payload["summary"]["problems"])


if __name__ == "__main__":
    unittest.main()
