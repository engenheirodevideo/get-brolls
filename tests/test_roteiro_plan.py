"""Plano de cena: componentes, beats com id fixo por lado, âncoras e impressão digital."""

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


if __name__ == "__main__":
    unittest.main()
