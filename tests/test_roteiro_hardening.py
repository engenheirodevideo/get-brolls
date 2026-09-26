"""ROTEIRO.md com entrada hostil: texto escondido, notas, âncoras, takes e desempenho."""

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import roteiro

HEAD = '---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n'  # corpo começa na linha 6


def parse(body, plugins=frozenset()):
    return roteiro.parse(HEAD + body, plugins=plugins)


def errors_of(body, plugins=frozenset()):
    try:
        parse(body, plugins)
    except roteiro.RoteiroError as exc:
        return exc.errors
    raise AssertionError("o roteiro devia ter sido recusado")


class HiddenTextTests(unittest.TestCase):
    def test_heading_comment_must_be_the_trailing_id(self):
        cases = {
            "## A <!-- c01 --> resto\n[A-ROLL]\n": "só vale no fim",
            "## A <!-- nota --> <!-- c01 -->\n[A-ROLL]\n": "só vale no fim",
            "## A <!-- x -->\n[A-ROLL]\n": 'id de cena "x" inválido',
            "## A -->\n[A-ROLL]\n": "só vale no fim",
        }
        for body, fragment in cases.items():
            with self.subTest(body=body):
                found = errors_of(body)
                self.assertEqual([n for n, _ in found], [6])
                self.assertIn(fragment, found[0][1])

    def test_html_comment_in_body_is_an_error(self):
        found = errors_of("## A <!-- c01 -->\n[A-ROLL]\nFala <!-- escondida --> visível.\n")
        self.assertEqual(found[0][0], 8)
        self.assertIn("esconde texto", found[0][1])

    def test_multiline_comment_is_caught_on_its_first_line(self):
        found = errors_of("## A\n[A-ROLL]\n<!--\n[BROLL: escondido]\n-->\n")
        self.assertEqual(found[0][0], 8)

    def test_comment_opened_before_the_first_scene_is_an_error(self):
        body = "# Titulo <!--\n## Escondida\n[BROLL: segredo]\nfala escondida\n-->\n## Visivel\n[A-ROLL]\nOi."
        found = errors_of(body)
        self.assertEqual([n for n, _ in found], [6, 10])
        self.assertIn("esconde texto", found[0][1])


class EncodingTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-enc-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_bom_crlf_and_trailing_spaces_on_fence(self):
        raw = (
            "\N{ZERO WIDTH NO-BREAK SPACE}---   \r\n"
            "type: roteiro\r\n"
            "genero: reels\r\n"
            "tema: t\r\n"
            "---  \r\n"
            "## A\r\n"
            "[A-ROLL]\r\n"
            "Oi.\r\n"
        )
        (self.project / "ROTEIRO.md").write_bytes(raw.encode("utf-8"))
        text = roteiro.load_text(self.project)
        self.assertFalse(text.startswith("\N{ZERO WIDTH NO-BREAK SPACE}"))
        self.assertNotIn("\r", text)
        doc = roteiro.parse(text, plugins=frozenset())
        self.assertEqual(doc.scenes[0].speech, "Oi.")
        self.assertEqual(roteiro.parse(raw, plugins=frozenset()).scenes[0].speech, "Oi.")

    def test_load_text_without_file_says_how_to_create(self):
        with self.assertRaises(ValueError) as ctx:
            roteiro.load_text(self.project)
        self.assertIn("roteiro --action new", str(ctx.exception))

    def test_file_not_in_utf8_is_a_clear_error(self):
        (self.project / "ROTEIRO.md").write_bytes("---\ntema: ação\n".encode("latin-1"))
        with self.assertRaises(ValueError) as ctx:
            roteiro.load_text(self.project)
        self.assertEqual(str(ctx.exception), "ROTEIRO.md não está em UTF-8: salve o arquivo como UTF-8 e rode de novo.")


class DirectiveArgsTests(unittest.TestCase):
    def test_all_args_unquoted_uniformly_with_flags(self):
        doc = parse("## A\n[SPLIT: \"Texto na tela\" | 'mapa antigo']\n[SFX: 'whoosh']\n")
        scene = doc.scenes[0]
        self.assertEqual(scene.layout.args, ("Texto na tela", "mapa antigo"))
        self.assertEqual(scene.layout.quoted, (True, True))
        self.assertEqual(scene.layers[0].args, ("whoosh",))
        full = parse('## A\n[FULL: "Comenta BROLL"]\n').scenes[0].layout
        self.assertEqual((full.args, full.quoted), (("Comenta BROLL",), (True,)))

    def test_lettering_accepts_single_quotes(self):
        doc = parse("## A\n[A-ROLL]\n[LETTERING: 'oi' | neon]\n")
        self.assertEqual(doc.scenes[0].layers[0].args, ("oi", "neon"))

    def test_arity_error_does_not_add_missing_layout(self):
        for body in ("## A\n[BROLL]\n", "## A\n[SPLIT: um lado]\n", "## A\n[BROLL: a | b]\n", "## A\n[SPLTI: a | b]\n"):
            with self.subTest(body=body):
                found = errors_of(body)
                self.assertEqual(len(found), 1, found)
                self.assertNotIn("precisa de um layout", found[0][1])

    def test_aroll_take_is_optional_and_validated(self):
        plain = parse("## A\n[A-ROLL]\n").scenes[0].layout
        self.assertEqual(plain.args, ())
        take = parse("## A\n[A-ROLL: T2]\n").scenes[0].layout
        self.assertEqual(take.args, ("t2",))
        self.assertIn("take", errors_of("## A\n[A-ROLL: take 2!]\n")[0][1])
        self.assertIn("no máximo um take", errors_of("## A\n[A-ROLL: t1 | t2]\n")[0][1])

    def test_pipe_inside_single_quotes_is_text(self):
        self.assertIn("dois lados", errors_of("## A\n[SPLIT: 'x | y']\n")[0][1])
        lettering = parse("## A\n[A-ROLL]\n[LETTERING: 'a | b' | neon]\n").scenes[0].layers[0]
        self.assertEqual((lettering.args, lettering.quoted), (("a | b", "neon"), (True, False)))
        self.assertIn("BROLL aceita no máximo 1", errors_of("## A\n[BROLL: copo d'água | x]\n")[0][1])
        apostrophe = parse('## A\n[A-ROLL]\n[LETTERING: "d\'água" | neon]\n').scenes[0].layers[0]
        self.assertEqual(apostrophe.args, ("d'água", "neon"))
        self.assertEqual(parse("## A\n[BROLL: Sampa's skyline]\n").scenes[0].layout.args, ("Sampa's skyline",))


class PluginAndNotesTests(unittest.TestCase):
    def test_extension_needs_an_enabled_plugin(self):
        doc = parse('## A\n[A-ROLL]\n[hf:zoom-in: "1.2" | lento]\n', plugins=frozenset({"hf"}))
        ext = doc.scenes[0].extensions[0]
        self.assertEqual((ext.plugin, ext.name, ext.args), ("hf", "zoom-in", ("1.2", "lento")))
        found = errors_of("## A\n[A-ROLL]\n[hff:zoom-in]\n", plugins=frozenset({"hf"}))
        self.assertIn("quis dizer hf:zoom-in", found[0][1])
        found = errors_of("## A\n[A-ROLL]\n[hf:zoom-in]\n")
        self.assertIn("não está habilitado", found[0][1])

    def test_enabled_plugins_reads_the_preload_inventory(self):
        rows = [{"id": "hf", "status": "enabled"}, {"id": "velho", "status": "disabled"}]
        with mock.patch("getbrolls.sdk.loader.inventory", return_value=rows):
            self.assertEqual(roteiro.enabled_plugins(), frozenset({"hf"}))
        with mock.patch("getbrolls.sdk.loader.inventory", side_effect=ValueError("plugins.json quebrado")):
            self.assertEqual(roteiro.enabled_plugins(), frozenset())

    def test_whole_line_stage_note_is_a_warning_outside_speech(self):
        doc = parse("## A\n[A-ROLL]\n[pausa]\nOi [risos] gente.\n[olha pra câmera]\nTchau.\n")
        scene = doc.scenes[0]
        self.assertEqual(scene.speech, "Oi [risos] gente.\nTchau.")
        self.assertEqual(scene.speech_clean, "Oi gente.\nTchau.")
        self.assertEqual(scene.notes, ("pausa", "risos", "olha pra câmera"))
        text = " ".join(doc.warnings)
        self.assertIn('linha 8: "[pausa]" não é diretiva', text)
        self.assertIn('linha 10: "[olha pra câmera]"', text)

    def test_links_wikilinks_and_checkboxes_are_not_notes(self):
        scene = parse("## A\n[A-ROLL]\n- [ ] tarefa [[Nota]] e [site](https://a.b)\n").scenes[0]
        self.assertEqual(scene.notes, ())
        self.assertEqual(scene.speech_clean, "- [ ] tarefa [[Nota]] e [site](https://a.b)")

    def test_typo_of_a_directive_is_still_an_error(self):
        self.assertIn("quis dizer LETTERING", errors_of('## A\n[A-ROLL]\n[LETERING: "x"]\n')[0][1])


class HeadingTests(unittest.TestCase):
    def test_malformed_heading_is_one_error_without_cascade(self):
        for body in ("##Titulo\n[A-ROLL]\nFala.\n", "## \n[A-ROLL]\nFala.\n", "##\n[BROLL: x]\n"):
            with self.subTest(body=body):
                found = errors_of(body)
                self.assertEqual(len(found), 1, found)
                self.assertEqual(found[0][0], 6)

    def test_heading_parse_is_linear(self):
        lines = ["## " + " " * 5000 + "x", "## x" + " " * 5000 + "<!-- c01", "##" + " " * 5000]
        for line in lines:
            started = time.perf_counter()
            roteiro._heading(line)
            self.assertLess(time.perf_counter() - started, 0.25)
        started = time.perf_counter()
        with self.assertRaises(roteiro.RoteiroError):
            parse("## " + " " * 5000 + "\n[A-ROLL]\n")
        self.assertLess(time.perf_counter() - started, 0.25)

    def test_scene_ids_are_ascii_and_never_zero(self):
        for bad in ("c00", "c000", "c٠١"):
            with self.subTest(bad=bad):
                self.assertIsNone(roteiro.SCENE_ID_RE.fullmatch(bad))
                self.assertIn("inválido", errors_of(f"## A <!-- {bad} -->\n[A-ROLL]\n")[0][1])
        self.assertIsNone(roteiro.SCENE_BEAT_RE.fullmatch("c00-a"))
        self.assertIsNotNone(roteiro.SCENE_BEAT_RE.fullmatch("c07-b"))


class FrontmatterCommentTests(unittest.TestCase):
    def test_inline_comment_after_value_is_a_clear_error(self):
        _, _, found = roteiro.parse_frontmatter(
            ["---", "type: roteiro", "genero: reels # meu gênero", "tema: t", "---"]
        )
        self.assertEqual(found[0][0], 3)
        self.assertIn("comentário no fim da linha", found[0][1])
        meta, _, found = roteiro.parse_frontmatter(["---", "type: roteiro", "genero: reels", 'tema: "IA #1"', "---"])
        self.assertEqual((found, meta["tema"]), ([], "IA #1"))

    def test_single_quotes_work_in_frontmatter(self):
        meta, _, found = roteiro.parse_frontmatter(
            ["---", "type: roteiro", "genero: reels", "aspecto: '16:9'", "tema: t", "---"]
        )
        self.assertEqual((found, meta["aspecto"]), ([], "16:9"))


class AnchorTests(unittest.TestCase):
    def test_layers_record_the_speech_line_they_precede(self):
        body = (
            '## A\n[A-ROLL]\n[SFX: whoosh]\nUm dois três.\n\n[LETTERING: "x"]\n[pausa]\n'
            "Quatro [risos] cinco.\n[MUSICA: fim]\n"
        )
        scene = parse(body).scenes[0]
        whoosh, lettering, music = scene.layers
        self.assertEqual((whoosh.anchor, whoosh.word_offset), (0, 0))
        self.assertEqual((lettering.anchor, lettering.word_offset), (1, 3))
        self.assertEqual((music.anchor, music.word_offset), (roteiro.END_ANCHOR, 5))

    def test_anchor_indexes_speech_clean_lines(self):
        scene = parse("## A\n[A-ROLL]\nOi.\n[risos] [pausa]\n[SFX: x]\nTchau.\n").scenes[0]
        self.assertEqual(scene.speech_clean.split("\n"), ["Oi.", "Tchau."])
        self.assertEqual((scene.layers[0].anchor, scene.layers[0].word_offset), (1, 1))

    def test_placeholder_left_from_skeleton_warns(self):
        doc = parse("## A\n[BROLL: <o que aparece>]\n<a dor>\n")
        text = " ".join(doc.warnings)
        self.assertIn("linha 7: <o que aparece> parece texto do esqueleto", text)
        self.assertIn("linha 8: <a dor>", text)


if __name__ == "__main__":
    unittest.main()
