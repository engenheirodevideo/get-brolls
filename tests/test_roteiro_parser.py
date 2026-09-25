"""ROTEIRO.md: frontmatter restrito, cenas, diretivas e estimativa."""

import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import roteiro

VALID_HEAD = [
    "---",
    "type: roteiro",
    "genero: reels",
    'aspecto: "9:16"',
    "duracao_alvo_s: 45",
    'tema: "IA: editando reels #1"',
    "legenda: true",
    "---",
]


class FrontmatterTests(unittest.TestCase):
    def test_valid_frontmatter(self):
        meta, body, errors = roteiro.parse_frontmatter([*VALID_HEAD, "## Gancho"])
        self.assertEqual(errors, [])
        self.assertEqual(body, 8)
        self.assertEqual(meta["genero"], "reels")
        self.assertEqual(meta["aspecto"], "9:16")
        self.assertEqual(meta["duracao_alvo_s"], 45)
        self.assertEqual(meta["tema"], "IA: editando reels #1")
        self.assertIs(meta["legenda"], True)
        self.assertEqual(meta["status"], "draft")

    def test_defaults_aspect_from_genre(self):
        head = [line for line in VALID_HEAD if not line.startswith("aspecto")]
        meta, _, errors = roteiro.parse_frontmatter(head)
        self.assertEqual(errors, [])
        self.assertEqual(meta["aspecto"], "9:16")

    def test_comment_lines_are_ignored(self):
        head = [*VALID_HEAD[:2], "# comentário", *VALID_HEAD[2:]]
        _, _, errors = roteiro.parse_frontmatter(head)
        self.assertEqual(errors, [])

    def test_errors_carry_line_numbers(self):
        cases = {
            "foo: 1": "desconhecida",
            "genero: vsl": "genero",
            'aspecto: "4:5"': "aspecto",
            "duracao_alvo_s: 3": "duracao_alvo_s",
            "legenda: talvez": "legenda",
            "tags: x": "desconhecida",
            "  - item": "lista",
            "status: gravado": "status",
        }
        for line, fragment in cases.items():
            with self.subTest(line=line):
                # Tira a linha válida da mesma chave: o erro tem que vir da regra do campo,
                # não de "chave repetida".
                key = line.split(":")[0].strip()
                head = [x for x in VALID_HEAD[:-1] if not x.startswith(key + ":")] + [line, "---"]
                _, _, errors = roteiro.parse_frontmatter(head)
                self.assertTrue(errors, line)
                self.assertEqual(errors[0][0], len(head) - 1)
                self.assertIn(fragment, errors[0][1])
                self.assertNotIn("repetida", errors[0][1])

    def test_empty_value_is_error(self):
        head = [line for line in VALID_HEAD if not line.startswith("tema")]
        head = [*head[:-1], "tema:", "---"]
        _, _, errors = roteiro.parse_frontmatter(head)
        self.assertIn("valor", errors[0][1])

    def test_repeated_key_is_error(self):
        head = [*VALID_HEAD[:-1], "genero: reels", "---"]
        _, _, errors = roteiro.parse_frontmatter(head)
        self.assertIn("repetida", errors[0][1])

    def test_missing_required_and_missing_fence(self):
        _, _, errors = roteiro.parse_frontmatter(["---", "type: roteiro", "---"])
        messages = " ".join(m for _, m in errors)
        self.assertIn("genero", messages)
        self.assertIn("tema", messages)
        _, _, errors = roteiro.parse_frontmatter(["## Gancho"])
        self.assertIn("---", errors[0][1])

    def test_fold(self):
        self.assertEqual(roteiro.fold("  Música ÉPICA "), "musica epica")
