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
            "genero: vsl": "genero",
            'aspecto: "4:5"': "aspecto",
            "duracao_alvo_s: 3": "duracao_alvo_s",
            "duracao_alvo_s: ²": "duracao_alvo_s",
            "legenda: talvez": "legenda",
            "cliente: Acme Corp": "slug",
            "direcao: rampa_e_whip": "slug",
            "Tema: outro": "chave `Tema`: use `tema`",
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

    def test_obsidian_properties_are_ignored_with_their_lists(self):
        head = [
            *VALID_HEAD[:-1],
            "created: 2026-09-26",
            "updated: 2026-09-26",
            "tags:",
            "  - get-brolls",
            "  - reels",
            "aliases:",
            "- Roteiro do reels",
            "",
            "  - outro nome",
            "cssclasses: [wide]",
            "Data de publicação: 2026-10-01",
            "publish: false",
            "---",
        ]
        meta, body, errors = roteiro.parse_frontmatter([*head, "## Gancho"])
        self.assertEqual([], errors)
        self.assertEqual(len(head), body)
        self.assertEqual({"genero", "aspecto", "duracao_alvo_s", "tema", "legenda", "status"}, set(meta))

    def test_typos_of_get_brolls_keys_are_errors_with_a_suggestion(self):
        cases = {
            "Tema: outro": "chave `Tema`: use `tema`",
            "LEGENDA: false": "chave `LEGENDA`: use `legenda`",
            "duração_alvo_s: 30": "chave `duração_alvo_s`: use `duracao_alvo_s`",
            "Duracao Alvo S: 30": "chave `Duracao Alvo S`: use `duracao_alvo_s`",
            "direção: rampa-e-whip": "chave `direção`: use `direcao`",
            "legendas: false": "chave desconhecida `legendas`: quis dizer `legenda`?",
            'aspeto: "16:9"': "chave desconhecida `aspeto`: quis dizer `aspecto`?",
            "client: acme": "chave desconhecida `client`: quis dizer `cliente`?",
        }
        for line, message in cases.items():
            with self.subTest(line=line):
                head = [*VALID_HEAD[:-1], line, "---"]
                _, _, errors = roteiro.parse_frontmatter(head)
                self.assertEqual([(len(head) - 1, message)], errors)

    def test_space_before_the_colon_is_the_same_key(self):
        head = [x for x in VALID_HEAD if not x.startswith(("legenda", "aspecto"))]
        head = [*head[:-1], "legenda : false", 'aspecto  : "16:9"', "---"]
        meta, _, errors = roteiro.parse_frontmatter(head)
        self.assertEqual([], errors)
        self.assertEqual((False, "16:9"), (meta["legenda"], meta["aspecto"]))

    def test_blank_or_null_client_and_direction_are_absent(self):
        for value in ("", " null", " ~", ' ""'):
            with self.subTest(value=value):
                meta, _, errors = roteiro.parse_frontmatter([*VALID_HEAD[:-1], f"cliente:{value}", "---"])
                self.assertEqual([], errors)
                self.assertNotIn("cliente", meta)

    def test_a_list_under_a_get_brolls_key_is_still_an_error(self):
        head = [*VALID_HEAD[:-1], "tags: x", "status: draft", "  - item", "---"]
        _, _, errors = roteiro.parse_frontmatter(head)
        self.assertEqual([(len(head) - 1, errors[0][1])], errors)
        self.assertIn("lista", errors[0][1])

    def test_client_and_direction_are_slugs_in_meta(self):
        head = [*VALID_HEAD[:-1], "cliente: acme-corp", 'direcao: "rampa-e-whip"', "---"]
        meta, _, errors = roteiro.parse_frontmatter(head)
        self.assertEqual([], errors)
        self.assertEqual(("acme-corp", "rampa-e-whip"), (meta["cliente"], meta["direcao"]))
        meta, _, _ = roteiro.parse_frontmatter(VALID_HEAD)
        self.assertNotIn("cliente", meta)
        self.assertNotIn("direcao", meta)

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
        doc = roteiro.parse(_doc(), plugins=frozenset({"hf"}))
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

    def test_direction_directives_are_reserved(self):
        for line in (
            "[DIRECAO: montagem-no-ritmo]",
            "[direção: respiro]",
            "[Transição: whip]",
            "[TRANSICAO]",
            "[ritmo: rápido]",
            "[VELOCIDADE: 2x]",
            "[direcao:whip]",
        ):
            with self.subTest(line=line), self.assertRaises(roteiro.RoteiroError) as caught:
                roteiro.parse(_doc(f"## A\n[A-ROLL]\n{line}\nOi.\n"), plugins=frozenset())
            self.assertIn("reservado para a próxima versão", str(caught.exception))

    def test_reserved_names_without_a_colon_are_refused_too(self):
        for line in ("[TRANSIÇÃO whip]", "[velocidade 2x]", "[RITMO — rápido]", "[direção respiro]"):
            with self.subTest(line=line), self.assertRaises(roteiro.RoteiroError) as caught:
                roteiro.parse(_doc(f"## A\n[A-ROLL]\n{line}\nOi.\n"), plugins=frozenset())
            self.assertIn("reservado para a próxima versão", str(caught.exception))

    def test_a_note_that_only_starts_like_a_reserved_name_is_still_a_note(self):
        body = "## A\n[A-ROLL]\n[ritmo acelerado aqui]\n[velocidade da luz]\nOi.\n"
        doc = roteiro.parse(_doc(body), plugins=frozenset())
        self.assertEqual(("ritmo acelerado aqui", "velocidade da luz"), doc.scenes[0].notes)

    def test_directive_errors(self):
        cases = {
            "## Sem layout\nSó fala.\n": "layout",
            "## Dois\n[A-ROLL]\n[BROLL: x]\n": "mais de um layout",
            "## X\n[SPLTI: a | b]\n": "SPLIT",
            "## X\n[SPLIT: só um lado]\n": "dois lados",
            "## X\n[BROLL]\n": "alvo",
            "## X\n[A-ROLL]\n[LETTERING: sem aspas]\n": "aspas",
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


class ObsidianCommentTests(unittest.TestCase):
    """`%%comentário%%` some no modo leitura do Obsidian: a revisão não veria o texto que viraria fala."""

    MESSAGE = "comentário do Obsidian (%%) esconde texto da revisão"

    def test_percent_comment_anywhere_in_the_body_is_an_error(self):
        cases = {
            "\n## A\n[A-ROLL]\nOi %%escondido%% tchau.\n": 12,
            "\n## A\n[A-ROLL]\n%%\nescondido\n%%\n": 12,
            "\n## A %%x%%\n[A-ROLL]\nOi.\n": 10,
            "\n# Título %%x%%\n## A\n[A-ROLL]\n": 10,
        }
        for body, line in cases.items():
            with self.subTest(body=body):
                with self.assertRaises(roteiro.RoteiroError) as ctx:
                    roteiro.parse(_doc(body), plugins=frozenset())
                self.assertIn(f"linha {line}: {self.MESSAGE}", str(ctx.exception))

    def test_percent_in_the_frontmatter_is_not_a_comment(self):
        text = "\n".join(VALID_HEAD).replace("editando reels #1", "100%% certo") + "\n\n## A\n[A-ROLL]\nOi.\n"
        self.assertEqual(roteiro.parse(text, plugins=frozenset()).meta["tema"], "IA: 100%% certo")


class TakePrefixTests(unittest.TestCase):
    """Take que começa com `a-`/`b-` colidiria com o nome do lado do SPLIT (`c03-a-t2`)."""

    def test_take_starting_with_a_side_prefix_is_rejected(self):
        for take in ("a-1", "B-final"):
            with self.subTest(take=take):
                with self.assertRaises(roteiro.RoteiroError) as ctx:
                    roteiro.parse(_doc(f"\n## A\n[A-ROLL: {take}]\n"), plugins=frozenset())
                self.assertIn(f'take "{take.lower()}" começa com "a-" ou "b-"', str(ctx.exception))
        self.assertIsNotNone(roteiro.take_problem("a-1"))
        self.assertIsNone(roteiro.take_problem("ab-1"))
        self.assertIsNone(roteiro.take_problem("t-a"))


class ExtensionQuotedTests(unittest.TestCase):
    def test_extension_arguments_keep_their_quoted_flags(self):
        text = _doc('\n## A\n[A-ROLL]\n[hf:zoom-in: "1.2" | forte]\nOi.\n')
        ext = roteiro.parse(text, plugins=frozenset({"hf"})).scenes[0].extensions[0]
        self.assertEqual((ext.args, ext.quoted), (("1.2", "forte"), (True, False)))
