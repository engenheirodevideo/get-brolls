"""Plano de cena: componentes, beats com id fixo por lado, âncoras e impressão digital."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import roteiro, roteiro_plan

HEAD = '---\ntype: roteiro\ngenero: reels\ntema: "t"\nduracao_alvo_s: 45\n---\n'  # corpo começa na linha 7
META = {"aspecto": "9:16"}


class ScenePlanTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-plan-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def put(self, relative):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    def doc(self, body):
        return roteiro.parse(HEAD + body, plugins=frozenset({"hf"}))

    def plan(self, body):
        return roteiro_plan.scene_plan(self.project, self.doc(body))

    def test_broll_beat_uses_clean_speech(self):
        plan = self.plan("## P <!-- c02 -->\n[BROLL: timeline cheia]\nFala [risos] aqui.\n[pausa]\n")
        self.assertEqual(
            plan["beats"],
            [
                {
                    "id": "c02",
                    "target": "timeline cheia",
                    "narration": "Fala aqui.",
                    "duration_hint_s": 1.5,
                    "scene": "c02",
                }
            ],
        )
        self.assertEqual(plan["scenes"][0]["notes"], ["risos", "pausa"])
        self.assertEqual(plan["problems"], [])

    def test_split_ids_are_fixed_by_position(self):
        cases = {
            "[SPLIT: tela do app | apresentador]": [("c03-a", "tela do app")],
            "[SPLIT: A-ROLL | mapa]": [("c03-b", "mapa")],
            '[SPLIT: "Texto" | mapa]': [("c03-b", "mapa")],
            "[SPLIT: A-ROLL | UGC: moça no café]": [],
        }
        for directive, expected in cases.items():
            with self.subTest(directive=directive):
                plan = self.plan(f"## S <!-- c03 -->\n{directive}\nOi.\n")
                self.assertEqual([(b["id"], b["target"]) for b in plan["beats"]], expected)
        both = self.plan("## S <!-- c03 -->\n[SPLIT: tela | mapa]\n" + " ".join(["x"] * 25) + "\n")
        self.assertEqual([(b["id"], b["duration_hint_s"]) for b in both["beats"]], [("c03-a", 10.0), ("c03-b", 10.0)])

    def test_full_brand_text_card_and_broll(self):
        self.put("assets/marca/selo.png")
        for target in ("logo", "Marca", "CTA", '"Comenta BROLL"', "selo"):
            with self.subTest(target=target):
                self.assertEqual(self.plan(f"## F <!-- c04 -->\n[FULL: {target}]\n")["beats"], [])
        plan = self.plan("## F <!-- c04 -->\n[FULL: cidade à noite, 1998]\n")
        self.assertEqual(plan["beats"][0]["target"], "cidade à noite, 1998")
        self.assertEqual(plan["problems"], [])

    def test_ambiguous_brand_is_a_problem_not_a_crash(self):
        self.put("assets/marca/selo.png")
        self.put("assets/marca/selo.svg")
        plan = self.plan("## F <!-- c04 -->\n[FULL: selo]\n")
        self.assertEqual(plan["beats"], [])
        self.assertEqual(plan["scenes"][0]["components"][0]["status"], "invalid")
        self.assertIn("linha 8", plan["problems"][0])
        self.assertIn("ambíguo", plan["problems"][0])

    def test_invalid_component_name_never_raises(self):
        plan = self.plan("## C <!-- c01 -->\n[A-ROLL]\n[SFX: ../segredo]\n")
        row = plan["scenes"][0]["components"][1]
        self.assertEqual((row["status"], row["line"]), ("invalid", 9))
        self.assertIn("inválido", plan["problems"][0])

    def test_aroll_convention_and_ugc_prompt(self):
        self.put("aroll/c01-t2.mp4")
        take = self.plan("## A <!-- c01 -->\n[A-ROLL: t2]\n")["scenes"][0]["components"][0]
        self.assertEqual((take["name"], take["status"]), ("c01-t2", "found"))
        plain = self.plan("## A <!-- c01 -->\n[A-ROLL]\n")["scenes"][0]["components"][0]
        self.assertEqual((plain["name"], plain["status"]), ("c01", "pending"))
        ugc = self.plan("## U <!-- c05 -->\n[UGC: moça abrindo a caixa]\n")["scenes"][0]["components"][0]
        self.assertEqual((ugc["kind"], ugc["name"], ugc["prompt"]), ("aroll", "c05", "moça abrindo a caixa"))
        split = self.plan("## S <!-- c06 -->\n[SPLIT: tela | A-ROLL: t3]\n")["scenes"][0]["components"]
        self.assertEqual([(r["directive"], r["name"]) for r in split], [("SPLIT", "c06-t3")])
        no_id = self.plan("## A\n[A-ROLL]\n")["scenes"][0]["components"][0]
        self.assertEqual((no_id["name"], no_id["status"]), (None, "pending"))

    def test_components_resolved_or_pending_with_warnings(self):
        self.put("assets/sfx/whoosh.wav")
        body = (
            '## C <!-- c01 -->\n[A-ROLL]\n[SFX: whoosh]\n[MUSICA: epica]\n[LETTERING: "oi" | neon]\n'
            '[LETTERING: "sem estilo"]\n[COMP: abertura]\n'
        )
        plan = self.plan(body)
        by = {(c["directive"], c["name"]): c["status"] for c in plan["scenes"][0]["components"]}
        self.assertEqual(
            by,
            {
                ("A-ROLL", "c01"): "pending",
                ("SFX", "whoosh"): "found",
                ("MUSICA", "epica"): "pending",
                ("LETTERING", "neon"): "pending",
                ("COMP", "abertura"): "pending",
            },
        )
        warnings = " ".join(plan["warnings"])
        self.assertIn('c01: MUSICA "epica" pendente', warnings)
        self.assertIn("licença não registrada", warnings)
        self.assertNotIn('A-ROLL "c01" pendente', warnings)

    def test_anchors_and_extensions_are_exposed(self):
        body = "## A <!-- c01 -->\n[A-ROLL]\n[hf:zoom-in: 1.2]\nUm dois.\n[SFX: whoosh]\nTrês.\n"
        scene = self.plan(body)["scenes"][0]
        self.assertEqual(
            scene["anchors"],
            [
                {"directive": "hf:zoom-in", "line": 9, "anchor": 0, "word_offset": 0},
                {"directive": "SFX", "line": 11, "anchor": 1, "word_offset": 2},
            ],
        )
        self.assertEqual(scene["extensions"][0]["args"], ["1.2"])

    def test_content_hash_ignores_formatting_and_ids_but_not_content(self):
        base = "## P\n[BROLL: x]\nFala  aqui.\n"
        same = "##   P   <!-- c09 -->\n[BROLL: x]\n\nFala\naqui.\n"
        other = "## P\n[BROLL: x]\nFala ali.\n"
        moved = "## P\n[BROLL: x]\nFala\n[SFX: a]\naqui.\n"
        still = "## P\n[BROLL: x]\n[SFX: a]\nFala\naqui.\n"

        def content_hash(body):
            return self.plan(body)["scenes"][0]["content_hash"]

        self.assertEqual(content_hash(base), content_hash(same))
        self.assertNotEqual(content_hash(base), content_hash(other))
        self.assertNotEqual(content_hash(moved), content_hash(still))

    def test_scene_without_id_uses_none_and_line_label(self):
        plan = self.plan("## P\n[BROLL: x]\n")
        self.assertIsNone(plan["beats"][0]["id"])
        self.assertEqual(plan["beats"][0]["scene"], "linha 7")


class AspectTests(unittest.TestCase):
    def test_rules_and_brief_must_match_the_aspect(self):
        self.assertEqual(roteiro_plan.aspect_problems(META, {"video_format": "reels"}), [])
        native = roteiro_plan.aspect_problems(META, {"video_format": "native"})
        self.assertIn("init-rules --format reels", native[0])
        brief = {"video": {"delivery": {"format": "horizontal"}}}
        problems = roteiro_plan.aspect_problems(META, {"video_format": "reels"}, brief)
        self.assertEqual(len(problems), 1)
        self.assertIn('"video.delivery.format" para "reels"', problems[0])


class SplitPresenterTests(unittest.TestCase):
    """Lados de SPLIT com apresentador, takes por lado e takes reservados."""

    # Mesmos auxiliares, sem herdar (herdar rodaria os testes de ScenePlanTests duas vezes).
    setUp = ScenePlanTests.setUp
    put = ScenePlanTests.put
    doc = ScenePlanTests.doc
    plan = ScenePlanTests.plan

    def test_two_presenter_sides_get_side_and_distinct_files(self):
        self.put("aroll/c03.mp4")
        plan = self.plan("## S <!-- c03 -->\n[SPLIT: A-ROLL | UGC: moça no café]\nOi.\n")
        rows = plan["scenes"][0]["components"]
        self.assertEqual(
            [(r["side"], r["name"], r["status"], r["prompt"]) for r in rows],
            [("a", "c03-a", "pending", None), ("b", "c03-b", "pending", "moça no café")],
        )
        self.assertEqual(plan["beats"], [])

    def test_both_sides_with_takes(self):
        rows = self.plan("## S <!-- c03 -->\n[SPLIT: A-ROLL: t2 | A-ROLL: t3]\n")["scenes"][0]["components"]
        self.assertEqual([(r["side"], r["name"]) for r in rows], [("a", "c03-a-t2"), ("b", "c03-b-t3")])

    def test_single_presenter_side_keeps_scene_name(self):
        self.put("aroll/c03.mov")
        rows = self.plan("## S <!-- c03 -->\n[SPLIT: tela | A-ROLL]\n")["scenes"][0]["components"]
        self.assertEqual([(r["side"], r["name"], r["status"]) for r in rows], [("b", "c03", "found")])

    def test_non_split_presenter_rows_have_side_none(self):
        for body in ("[A-ROLL]", "[A-ROLL: t2]", "[UGC: moça abrindo a caixa]"):
            with self.subTest(body=body):
                row = self.plan(f"## A <!-- c01 -->\n{body}\n")["scenes"][0]["components"][0]
                self.assertIn("side", row)
                self.assertIsNone(row["side"])

    def test_invalid_split_side_take_is_a_problem_not_a_file(self):
        self.put("aroll/c04-take 2.mp4")
        plan = self.plan("## S <!-- c04 -->\n[SPLIT: mapa | A-ROLL: Take 2]\n")
        row = plan["scenes"][0]["components"][0]
        self.assertEqual((row["status"], row["path"], row["line"], row["side"]), ("invalid", None, 8, "b"))
        self.assertEqual(len(plan["problems"]), 1)
        self.assertIn("linha 8", plan["problems"][0])
        self.assertIn("take", plan["problems"][0])
        self.assertEqual([b["id"] for b in plan["beats"]], ["c04-a"])

    def test_reserved_take_on_split_side_is_invalid(self):
        for take in ("a", "B"):
            with self.subTest(take=take):
                plan = self.plan(f"## S <!-- c04 -->\n[SPLIT: mapa | A-ROLL: {take}]\n")
                self.assertEqual(plan["scenes"][0]["components"][0]["status"], "invalid")
                self.assertIn("reservado", plan["problems"][0])

    def test_reserved_take_on_layout_is_rejected(self):
        for take in ("a", "b", "A"):
            with self.subTest(take=take):
                with self.assertRaises(roteiro.RoteiroError) as caught:
                    self.doc(f"## A <!-- c01 -->\n[A-ROLL: {take}]\n")
                self.assertIn("linha 8", str(caught.exception))
                self.assertIn(f'take "{take.lower()}" é reservado para o lado do SPLIT', str(caught.exception))

    def test_end_anchor_is_exposed(self):
        scene = self.plan("## A <!-- c01 -->\n[A-ROLL]\nUm dois.\n[SFX: whoosh]\n")["scenes"][0]
        self.assertEqual(scene["anchors"], [{"directive": "SFX", "line": 10, "anchor": "fim", "word_offset": 2}])

    def test_plan_is_json_stable_across_runs(self):
        self.put("assets/sfx/whoosh.wav")
        body = (
            "## A <!-- c01 -->\n[SPLIT: A-ROLL | UGC: moça]\n[hf:zoom-in: 1.2]\nFala [risos].\n[SFX: whoosh]\n"
            "## B\n[BROLL: cidade]\nOutra.\n"
        )
        first = json.dumps(self.plan(body), sort_keys=True, ensure_ascii=False)
        second = json.dumps(self.plan(body), sort_keys=True, ensure_ascii=False)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(first), self.plan(body))


class AspectMissingTests(unittest.TestCase):
    def test_missing_format_says_not_defined(self):
        for rules, brief in (
            (None, None),
            ({}, None),
            ({"video_format": "reels"}, {}),
            ({"video_format": "reels"}, {"video": {}}),
        ):
            with self.subTest(rules=rules, brief=brief):
                problems = roteiro_plan.aspect_problems(META, rules, brief)
                self.assertEqual(len(problems), 1)
                self.assertNotIn("None", problems[0])
                self.assertIn("não definido", problems[0])


class PlanContractTests(unittest.TestCase):
    """Forma do plano que os exporters leem: versão, meta, take explícito, aspas das extensões e hash versionado."""

    project: Path
    setUp = ScenePlanTests.setUp
    put = ScenePlanTests.put
    doc = ScenePlanTests.doc
    plan = ScenePlanTests.plan

    def test_plan_has_version_and_meta(self):
        plan = self.plan("## A <!-- c01 -->\n[A-ROLL]\nOi.\n")
        self.assertEqual(plan["plan_version"], 2)
        self.assertEqual(
            plan["meta"], {"aspecto": "9:16", "legenda": True, "duracao_alvo_s": 45, "genero": "reels", "tema": "t"}
        )
        bare = roteiro.parse('---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n', plugins=frozenset())
        self.assertIsNone(roteiro_plan.scene_plan(self.project, bare)["meta"]["duracao_alvo_s"])

    def test_aroll_rows_expose_the_take(self):
        cases = {
            "[A-ROLL: t2]": [("aroll", "t2")],
            "[A-ROLL]": [("aroll", None)],
            "[UGC: moça]": [("aroll", None)],
            "[SPLIT: mapa | A-ROLL: t3]": [("aroll", "t3")],
            "[SPLIT: A-ROLL: t2 | A-ROLL]": [("aroll", "t2"), ("aroll", None)],
        }
        for directive, expected in cases.items():
            with self.subTest(directive=directive):
                rows = self.plan(f"## A <!-- c01 -->\n{directive}\n")["scenes"][0]["components"]
                self.assertEqual([(r["kind"], r["take"]) for r in rows], expected)
        rows = self.plan("## A <!-- c01 -->\n[A-ROLL]\n[SFX: whoosh]\n")["scenes"][0]["components"]
        self.assertIsNone(rows[1]["take"])

    def test_extension_rows_carry_quoted_and_change_the_hash(self):
        quoted = self.plan('## A <!-- c01 -->\n[A-ROLL]\n[hf:zoom-in: "1.2"]\n')["scenes"][0]
        plain = self.plan("## A <!-- c01 -->\n[A-ROLL]\n[hf:zoom-in: 1.2]\n")["scenes"][0]
        self.assertEqual((quoted["extensions"][0]["quoted"], plain["extensions"][0]["quoted"]), ([True], [False]))
        self.assertNotEqual(quoted["content_hash"], plain["content_hash"])

    def test_hash_inputs_carry_a_version_marker(self):
        from getbrolls import roteiro_review

        body = "## A <!-- c01 -->\n[BROLL: praia]\nOi.\n"
        doc = self.doc(body)
        fingerprint = roteiro_plan.scene_fingerprint(doc.scenes[0], self.project)
        self.assertEqual(fingerprint["v"], 1)
        self.assertEqual(self.plan(body)["scenes"][0]["content_hash"], roteiro_plan.digest(fingerprint))
        meta = {k: v for k, v in doc.meta.items() if k != "status"}
        expected = roteiro_plan.digest({"v": 1, "meta": meta, "scenes": [fingerprint]})
        self.assertEqual(roteiro_review.review_hash(doc, self.project), expected)

    def test_full_turning_into_brand_changes_the_hash(self):
        body = "## F <!-- c04 -->\n[FULL: praia]\nTchau.\n"
        before = self.plan(body)["scenes"][0]["content_hash"]
        self.put("assets/marca/praia.png")
        self.assertNotEqual(before, self.plan(body)["scenes"][0]["content_hash"])


if __name__ == "__main__":
    unittest.main()
