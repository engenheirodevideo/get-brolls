"""Referências de catálogo (`cat:motor/tipo/id@versão`): gramática, codificação e igualdade."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

from getbrolls import refs


class CanonicalExampleTests(unittest.TestCase):
    def test_spec_examples_are_already_canonical(self):
        for text in ("cat:getbrolls/process/reels-roteiro@1", "cat:hyperframes/recipe/yan-cortes-virais@7"):
            with self.subTest(ref=text):
                self.assertEqual(text, refs.canonical(text))

    def test_parse_splits_engine_type_id_and_version(self):
        ref = refs.parse("cat:hyperframes/recipe/yan-cortes-virais@7")
        self.assertEqual(refs.Ref("cat", ("hyperframes", "recipe", "yan-cortes-virais"), "7"), ref)
        self.assertEqual("cat:hyperframes/recipe/yan-cortes-virais@7", refs.format_ref(ref))

    def test_catalog_ref_builds_the_canonical_text(self):
        self.assertEqual(
            "cat:getbrolls/process/reels-roteiro@1", refs.catalog_ref("getbrolls", "process", "reels-roteiro", "1")
        )
        self.assertEqual("cat:getbrolls/process/a%2Fb@v%401", refs.catalog_ref("getbrolls", "process", "a/b", "v@1"))

    def test_template_ref(self):
        self.assertEqual("cat:getbrolls/template/reels-acme@3", refs.template_ref("reels-acme", 3))

    def test_template_ref_refuses_a_bad_slug_or_version(self):
        for slug, number in (("Reels", 1), ("../x", 1), ("a" * 65, 1), ("ok", 0), ("ok", True), ("ok", "1")):
            with self.subTest(slug=slug, number=number), self.assertRaises(ValueError):
                refs.template_ref(slug, number)  # pyright: ignore[reportArgumentType]


class TemplatePartsTests(unittest.TestCase):
    def test_parts_of_a_template_ref(self):
        self.assertEqual(("reels-acme", 3), refs.template_parts("cat:getbrolls/template/reels-acme@3"))

    def test_decoded_slug_must_match_the_slug_rule(self):
        for text in (
            "cat:getbrolls/template/..%2F..%2Fetc@1",
            "cat:getbrolls/template/%2E%2E@1",
            "cat:getbrolls/template/a%5Cb@1",
            "cat:getbrolls/template/Reels@1",
            "cat:getbrolls/template/reels@01",
            "cat:getbrolls/template/reels@0",
            "cat:getbrolls/template/reels@latest",
            "cat:hyperframes/template/reels@1",
            "cat:getbrolls/process/reels@1",
        ):
            with self.subTest(ref=text), self.assertRaises(ValueError):
                refs.template_parts(text)


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

    def test_local_kinds_arrive_later(self):
        for kind in refs.LOCAL_KINDS:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "disponível a partir da 2.7"):
                refs.parse(f"{kind}:x")
        with self.assertRaisesRegex(ValueError, "disponível a partir da 2.7"):
            refs.format_ref(refs.Ref("scene", ("c01",)))

    def test_not_text(self):
        with self.assertRaisesRegex(ValueError, "^Referência inválida"):
            refs.parse(None)  # pyright: ignore[reportArgumentType]

    def test_same_compares_canonical_forms(self):
        self.assertTrue(refs.same("cat:m/t/a%3ab@1", "cat:m/t/a%3Ab@1"))
        self.assertFalse(refs.same("cat:m/t/a@1", "cat:m/t/a@2"))


if __name__ == "__main__":
    unittest.main()
