"""Referências de catálogo (`cat:motor/tipo/id@versão`): gramática, codificação e igualdade.

No fim, o comando `references` (memória de referências do projeto): consulta que só lê.
"""

import tempfile
import types
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

from getbrolls import refs
from getbrolls.commands import execute


class CanonicalExampleTests(unittest.TestCase):
    def test_spec_examples_are_already_canonical(self):
        for text in ("cat:getbrolls/process/reels-roteiro@1", "cat:hyperframes/recipe/exemplo-cortes-virais@7"):
            with self.subTest(ref=text):
                self.assertEqual(text, refs.canonical(text))

    def test_parse_splits_engine_type_id_and_version(self):
        ref = refs.parse("cat:hyperframes/recipe/exemplo-cortes-virais@7")
        self.assertEqual(refs.Ref("cat", ("hyperframes", "recipe", "exemplo-cortes-virais"), "7"), ref)
        self.assertEqual("cat:hyperframes/recipe/exemplo-cortes-virais@7", refs.format_ref(ref))

    def test_catalog_ref_builds_the_canonical_text(self):
        self.assertEqual(
            "cat:getbrolls/process/reels-roteiro@1", refs.catalog_ref("getbrolls", "process", "reels-roteiro", "1")
        )
        self.assertEqual("cat:getbrolls/process/a%2Fb@v%401", refs.catalog_ref("getbrolls", "process", "a/b", "v@1"))


class EncodingTests(unittest.TestCase):
    def test_each_reserved_character_is_percent_encoded(self):
        expected = {"%": "%25", ":": "%3A", "/": "%2F", "\\": "%5C", "@": "%40", "#": "%23", "?": "%3F"}
        for char, code in expected.items():
            with self.subTest(char=char):
                self.assertEqual(f"a{code}b", refs.encode(f"a{char}b"))
                self.assertEqual(f"a{char}b", refs.decode(f"a{code}b"))

    def test_space_controls_and_del_are_encoded(self):
        self.assertEqual("a%20b", refs.encode("a b"))
        self.assertEqual("a%09b%0A", refs.encode("a\tb\n"))
        self.assertEqual("%00%1F%7F", refs.encode("\x00\x1f\x7f"))
        self.assertEqual("%C2%85%C2%9F", refs.encode("\x85\x9f"))
        self.assertEqual("%E2%80%83", refs.encode(" "))

    def test_accents_and_non_latin_stay_literal(self):
        self.assertEqual("Título-日本", refs.encode("Título-日本"))

    def test_lower_case_input_is_accepted_and_output_is_upper_case(self):
        self.assertEqual("cat:getbrolls/process/a%3Ab@1", refs.canonical("cat:getbrolls/process/a%3ab@1"))
        # `%c3%a9` é `é`, que não é reservado: a forma canônica é literal.
        self.assertEqual("cat:getbrolls/process/café@1", refs.canonical("cat:getbrolls/process/caf%c3%a9@1"))

    def test_invalid_percent_sequences(self):
        for text in ("a%", "a%4", "a%zz", "a%FF", "%C3"):
            with self.subTest(value=text), self.assertRaisesRegex(ValueError, "%XX inválido"):
                refs.decode(text)

    def test_literal_reserved_in_value_is_an_error(self):
        for text in ("cat:getbrolls/process/a b@1", "cat:getbrolls/process/a#b@1", "cat:getbrolls/process/a?b@1"):
            with self.subTest(ref=text), self.assertRaisesRegex(ValueError, "precisa ser escrito como %XX"):
                refs.parse(text)

    def test_nfc_normalisation(self):
        decomposed = "cat:getbrolls/process/café@1"
        self.assertEqual("cat:getbrolls/process/café@1", refs.canonical(decomposed))
        self.assertEqual(("getbrolls", "process", "café"), refs.parse(decomposed).parts)
        self.assertTrue(refs.same(decomposed, "cat:getbrolls/process/caf%C3%A9@1"))

    def test_round_trip(self):
        for value in ("simples", "a/b:c@d#e?f%g\\h", "Título Grande", "\t\n\x7f", "日本語", "x" * 200):
            with self.subTest(value=value):
                self.assertEqual(value, refs.decode(refs.encode(value)))
                text = refs.catalog_ref("motor", "tipo", value, value)
                self.assertEqual(refs.Ref("cat", ("motor", "tipo", value), value), refs.parse(text))
                self.assertEqual(text, refs.canonical(text))


class LimitTests(unittest.TestCase):
    def test_whole_ref_is_capped_at_512_characters(self):
        head = "cat:getbrolls/process/"
        fits = head + "a" * 200 + "@" + "b" * (512 - len(head) - 201)
        self.assertEqual(512, len(fits))
        with self.assertRaisesRegex(ValueError, "200"):
            refs.parse(fits)  # a versão passou de 200
        fits = head + "a" * 200 + "@" + "%25" * 96
        self.assertLessEqual(len(fits), 512)
        refs.parse(fits)
        with self.assertRaisesRegex(ValueError, "passou de 512 caracteres"):
            refs.parse(head + "a" * 200 + "@" + "%25" * 100)

    def test_id_and_version_are_one_to_two_hundred_characters(self):
        refs.catalog_ref("m", "t", "a" * 200, "1")
        with self.assertRaisesRegex(ValueError, "200"):
            refs.catalog_ref("m", "t", "a" * 201, "1")
        with self.assertRaisesRegex(ValueError, "valor vazio"):
            refs.parse("cat:m/t/@1")
        with self.assertRaisesRegex(ValueError, "valor vazio"):
            refs.parse("cat:m/t/a@")

    def test_formatted_ref_over_512_characters_is_refused(self):
        with self.assertRaisesRegex(ValueError, "passou de 512 caracteres"):
            refs.catalog_ref("m", "t", "/" * 200, "1")


class GrammarErrorTests(unittest.TestCase):
    def test_error_message_names_the_ref(self):
        with self.assertRaises(ValueError) as caught:
            refs.parse("nada")
        self.assertEqual(
            'Referência inválida "nada": use tipo:valor ou cat:motor/tipo/id@versão.', str(caught.exception)
        )

    def test_bad_shapes(self):
        for text in ("cat:", "cat:m", "cat:m/t", "cat:m/t/id", "cat:m/t/a@b@c", "cat:m/t/a/b@1", ":x", ""):
            with self.subTest(ref=text), self.assertRaisesRegex(ValueError, "^Referência inválida"):
                refs.parse(text)

    def test_engine_and_type_rule(self):
        for text in (
            "cat:Getbrolls/t/a@1",
            "cat:1engine/t/a@1",
            "cat:m/Tipo/a@1",
            "cat:m/t%41/a@1",
            f"cat:{'a' * 33}/t/a@1",
        ):
            with self.subTest(ref=text), self.assertRaisesRegex(ValueError, "^Referência inválida"):
                refs.parse(text)
        refs.parse(f"cat:{'a' * 32}/t_1-x/a@1")

    def test_unknown_kind(self):
        with self.assertRaisesRegex(ValueError, 'tipo "foo" não existe'):
            refs.parse("foo:bar")

    def test_other_local_kinds_are_still_refused(self):
        for kind in ("scene", "beat", "candidate", "clip", "aroll", "export", "doc", "task"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "ainda não é aceita"):
                refs.parse(f"{kind}:x")
        with self.assertRaisesRegex(ValueError, "ainda não é aceita"):
            refs.format_ref(refs.Ref("scene", ("c01",)))

    def test_not_text(self):
        with self.assertRaisesRegex(ValueError, "^Referência inválida"):
            refs.parse(None)  # pyright: ignore[reportArgumentType]

    def test_same_compares_canonical_forms(self):
        self.assertTrue(refs.same("cat:m/t/a%3ab@1", "cat:m/t/a%3Ab@1"))
        self.assertFalse(refs.same("cat:m/t/a@1", "cat:m/t/a@2"))


class ReferencesCommandTests(unittest.TestCase):
    """`references` lê `references.json` e nunca cria `brolls/`."""

    def test_references_in_a_new_folder_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "novo"
            project.mkdir()
            args = types.SimpleNamespace(
                command="references", project=str(project), env_file=None, confirm_format_change=False
            )
            self.assertEqual({"items": []}, execute(args))
            self.assertEqual([], list(project.iterdir()))


class RecipeRefTests(unittest.TestCase):
    def test_recipe_ref_is_owner_dot_slug(self):
        self.assertEqual(
            "cat:getbrolls/recipe/cliente-exemplo.reels-split@4", refs.recipe_ref("cliente-exemplo", "reels-split", 4)
        )
        self.assertEqual("cat:getbrolls/recipe/pessoal.vsl@1", refs.recipe_ref(refs.PERSONAL_OWNER, "vsl", 1))

    def test_recipe_ref_refuses_bad_parts(self):
        for owner, slug, number in (
            ("Cliente", "x", 1), ("a.b", "x", 1), ("cliente", "x.y", 1), ("cliente", "../x", 1),
            ("cliente", "a" * 65, 1), ("cliente", "x", 0), ("cliente", "x", True), ("cliente", "x", "1"),
        ):  # fmt: skip
            with self.subTest(owner=owner, slug=slug, number=number), self.assertRaises(ValueError):
                refs.recipe_ref(owner, slug, number)  # pyright: ignore[reportArgumentType]

    def test_recipe_ref_names_the_missing_part(self):
        for args, missing in (((None, "x", 1), "dono"), (("a", "x", None), "versão")):
            with self.subTest(args=args), self.assertRaises(ValueError) as caught:
                refs.recipe_ref(*args)  # pyright: ignore[reportArgumentType]
            self.assertIn("falta o dono" if missing == "dono" else "falta a versão", str(caught.exception))
            self.assertNotIn("None", str(caught.exception))

    def test_recipe_ref_bounds_the_version_like_recipe_parts(self):
        self.assertEqual("cat:getbrolls/recipe/a.x@999999999", refs.recipe_ref("a", "x", 999999999))
        with self.assertRaises(ValueError):
            refs.recipe_ref("a", "x", 10**12)
        with self.assertRaises(ValueError):
            refs.recipe_parts("a.x@1000000000")

    def test_recipe_parts_decodes_before_the_slug_rule(self):
        # A ref canônica é conferida já decodificada (SPEC2 §2): `%2E` é o ponto entre dono e slug.
        self.assertEqual(("a", "x", 1), refs.recipe_parts("cat:getbrolls/recipe/a%2Ex@1"))
        self.assertEqual("cat:getbrolls/recipe/a.x@1", refs.canonical("cat:getbrolls/recipe/a%2Ex@1"))

    def test_recipe_parts_reads_the_three_forms(self):
        self.assertEqual((None, "reels-split", None), refs.recipe_parts("reels-split"))
        self.assertEqual((None, "reels-split", 4), refs.recipe_parts("reels-split@4"))
        self.assertEqual(("cliente-exemplo", "reels-split", None), refs.recipe_parts("cliente-exemplo.reels-split"))
        self.assertEqual(("cliente-exemplo", "reels-split", 4), refs.recipe_parts("cliente-exemplo.reels-split@4"))
        self.assertEqual(
            ("cliente-exemplo", "reels-split", 4),
            refs.recipe_parts("cat:getbrolls/recipe/cliente-exemplo.reels-split@4"),
        )

    def test_recipe_parts_refuses_paths_and_extra_dots(self):
        for bad in (
            "", "a.b.c", ".x", "x.", "Reels", "x@0", "x@01", "x@latest", "x@", "a/b", "..", "x%2Ey",
            "cat:getbrolls/recipe/x@1", "cat:getbrolls/recipe/a.b@01", "cat:getbrolls/recipe/a%2Eb.c@1",
            "cat:getbrolls/template/a.b@1", "cat:hyperframes/recipe/a.b@1", "scene:c01", "asset:sfx:x",
        ):  # fmt: skip
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "^Referência inválida"):
                refs.recipe_parts(bad)


class LocalRefTests(unittest.TestCase):
    def test_format_ref_refuses_an_invalid_local_ref(self):
        with self.assertRaisesRegex(ValueError, "^Referência inválida"):
            refs.format_ref(refs.Ref("asset", ("sfx", "a/b")))

    def test_format_ref_writes_seg_and_media(self):
        for kind, ident in (("seg", "seg_3f9a1c2b7d"), ("media", "0123456789abcdef")):
            with self.subTest(kind=kind):
                self.assertEqual(f"{kind}:{ident}", refs.format_ref(refs.Ref(kind, (ident,))))

    def test_asset_ref_of_any_kind(self):
        ref = refs.parse("asset:lettering:T%C3%ADtulo%20Grande")
        self.assertEqual(refs.Ref("asset", ("lettering", "Título Grande")), ref)
        self.assertEqual("asset:lettering:Título%20Grande", refs.format_ref(ref))
        self.assertEqual(refs.Ref("asset", ("transicao", "whip")), refs.parse("asset:transicao:whip"))

    def test_asset_equality_folds_the_name(self):
        self.assertTrue(refs.same("asset:marca:Logo", "asset:marca:logo"))
        self.assertFalse(refs.same("asset:marca:logo", "asset:sfx:logo"))

    def test_handoff_seg_and_media(self):
        self.assertEqual(refs.Ref("handoff", ("hyperframes", "003")), refs.parse("handoff:hyperframes/003"))
        self.assertEqual("handoff:davinci/1200", refs.canonical("handoff:davinci/1200"))
        self.assertEqual(refs.Ref("seg", ("seg_3f9a1c2b7d",)), refs.parse("seg:seg_3f9a1c2b7d"))
        self.assertEqual(refs.Ref("media", ("0123456789abcdef",)), refs.parse("media:0123456789abcdef"))

    def test_bad_local_values(self):
        for bad in (
            "asset:Sfx:x", "asset:sfx", "asset:sfx:", "asset:sfx:..%2Fx", "asset:sfx:a/b", "asset:sfx:x:y",
            "asset:sfx:%20x", "handoff:hyperframes/1", "handoff:hyperframes/latest", "handoff:Hyper/001",
            "handoff:hyperframes", "seg:seg_3F9A1C2B7D", "seg:3f9a1c2b7d", "media:0123", "media:0123456789ABCDEF",
        ):  # fmt: skip
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "^Referência inválida"):
                refs.parse(bad)


if __name__ == "__main__":
    unittest.main()
