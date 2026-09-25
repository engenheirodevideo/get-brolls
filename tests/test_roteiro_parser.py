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
            "duracao_alvo_s: ²": "duracao_alvo_s",
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


BODY = """
## Gancho <!-- c01 -->
[A-ROLL]
[LETTERING: "A IA editou | em 3 min" | neon]
Você não precisa passar 4 horas editando.

## Problema <!-- c02 -->
[b-roll: timeline cheia de cortes]
Todo mundo trava [risos] na mesma parte.
[texto](https://exemplo.com) e [[Nota]] ficam na fala.

## Prova
[FRAME SPLIT: tela do get-brolls | Apresentador]
[sfx: whoosh]
[hf:zoom-in: 1.2]
Eu digo o tema e ele entrega.
"""


def _doc(body=BODY):
    return "\n".join(VALID_HEAD) + "\n" + body


class SceneTests(unittest.TestCase):
    def test_parses_scenes_directives_and_speech(self):
        doc = roteiro.parse(_doc())
        self.assertEqual([s.title for s in doc.scenes], ["Gancho", "Problema", "Prova"])
        self.assertEqual([s.scene_id for s in doc.scenes], ["c01", "c02", None])
        gancho, problema, prova = doc.scenes
        self.assertEqual(gancho.layout.kind, "A-ROLL")
        self.assertEqual(gancho.layers[0].kind, "LETTERING")
        self.assertEqual(gancho.layers[0].args, ("A IA editou | em 3 min", "neon"))
        self.assertEqual(problema.layout.kind, "BROLL")
        self.assertEqual(problema.layout.args, ("timeline cheia de cortes",))
        self.assertIn("[risos]", problema.speech)
        self.assertIn("[[Nota]]", problema.speech)
        self.assertEqual(prova.layout.kind, "SPLIT")
        self.assertEqual(prova.layout.args, ("tela do get-brolls", "Apresentador"))
        self.assertEqual(prova.layers[0].kind, "SFX")
        self.assertEqual(prova.extensions[0].plugin, "hf")
        self.assertEqual(prova.extensions[0].name, "zoom-in")
        self.assertEqual(prova.extensions[0].args, ("1.2",))
        self.assertEqual(prova.line, 20)

    def test_directive_errors(self):
        cases = {
            "## Sem layout\nSó fala.\n": "layout",
            "## Dois\n[A-ROLL]\n[BROLL: x]\n": "mais de um layout",
            "## X\n[SPLTI: a | b]\n": "SPLIT",
            "## X\n[SPLIT: só um lado]\n": "dois lados",
            "## X\n[BROLL]\n": "alvo",
            "## X\n[A-ROLL]\n[LETTERING: sem aspas]\n": "aspas",
            "## X\n[A-ROLL]\n[risos]\n": "diretiva",
            "## X <!-- c01 -->\n[A-ROLL]\n## Y <!-- c01 -->\n[A-ROLL]\n": "c01",
            "texto solto antes\n## X\n[A-ROLL]\n": "fora de cena",
        }
        for body, fragment in cases.items():
            with self.subTest(body=body):
                with self.assertRaises(roteiro.RoteiroError) as ctx:
                    roteiro.parse(_doc("\n" + body))
                self.assertIn(fragment, str(ctx.exception))
                self.assertTrue(all(n > 0 for n, _ in ctx.exception.errors))

    def test_duplicate_id_names_both_lines(self):
        body = "\n## X <!-- c01 -->\n[A-ROLL]\n## Y <!-- c01 -->\n[A-ROLL]\n"
        with self.assertRaises(roteiro.RoteiroError) as ctx:
            roteiro.parse(_doc(body))
        self.assertIn("linhas 10 e 12", str(ctx.exception))

    def test_three_digit_ids_and_h1(self):
        doc = roteiro.parse(_doc("\n# Título do vídeo\n## X <!-- c100 -->\n[A-ROLL]\n"))
        self.assertEqual(doc.scenes[0].scene_id, "c100")

    def test_markdown_lines_are_never_directives(self):
        body = "\n## X\n[A-ROLL]\n- [ ] tarefa\n[link](https://a.b)\n[[Wiki]]\n"
        doc = roteiro.parse(_doc(body))
        self.assertIn("- [ ] tarefa", doc.scenes[0].speech)


class EstimateTests(unittest.TestCase):
    def test_formula_minimum_and_notes(self):
        self.assertEqual(roteiro.estimate(""), (2.0, False))
        self.assertEqual(roteiro.estimate("uma palavra"), (1.5, False))
        self.assertEqual(roteiro.estimate(" ".join(["palavra"] * 25)), (10.0, False))
        self.assertEqual(roteiro.estimate("duas palavras [risos] [pausa longa aqui]"), (1.5, False))

    def test_cap_and_target_warning(self):
        duration, over = roteiro.estimate(" ".join(["palavra"] * 400))
        self.assertEqual((duration, over), (120.0, True))
        long_body = "\n## X\n[A-ROLL]\n" + " ".join(["palavra"] * 400) + "\n"
        doc = roteiro.parse(_doc(long_body))
        text = " ".join(doc.warnings)
        self.assertIn("divida a cena", text)
        self.assertIn("duracao_alvo_s", text)
