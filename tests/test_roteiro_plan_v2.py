"""Plano de cena, forma 2: vagas por layout, papel do FULL, palavras e tema; hashes iguais aos da forma 1."""

import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import roteiro, roteiro_plan, roteiro_review

HEAD = '---\ntype: roteiro\ngenero: reels\ntema: "IA editando reels"\n---\n'
# Mesmo texto usado para fixar os hashes da forma 1 (antes de `slots`/`words`/`tema`).
PINNED = (
    HEAD
    + "## Gancho <!-- c01 -->\n[A-ROLL]\nEu digo o tema.\n\n"
    + "## Problema <!-- c02 -->\n[BROLL: timeline cheia]\n[SFX: whoosh]\nHoras cortando.\n\n"
    + '## Prova <!-- c03 -->\n[SPLIT: tela do get-brolls | A-ROLL: t2]\n[LETTERING: "3x mais rápido" | destaque]\n'
    + "Ele acha e corta.\n[MUSICA: lofi]\n\n"
    + '## Cartela <!-- c04 -->\n[FULL: "Comenta BROLL"]\n\n'
    + "## CTA <!-- c05 -->\n[FULL: logo]\nSegue.\n"
)
V1_REVIEW_HASH = "23504ab691e438cc240692e9ec51c46508fea6498e676272f881bf7e29dee689"
V1_CONTENT_HASHES = [
    "22041ccf372dfc38697d22e0de44b3bc080c0ea64a9cc132b18e443e26260982",
    "cd0d1fe07917566cb73826545231273289bcacd0963a50a5bc31be928dc98454",
    "42a23280ae07316e33c6b46f0b84d2d417a2d5dd64478c661d921bde5e11b9a0",
    "69a20c133e32adc63853ac0a8aa8398feb327beb0fec70da54534aea00786041",
    "47482e72405db36e2df27b9ffebccaaab2d4a0c552a7b5ed4f8ba9ea87a7f9bf",
]


def slot(name, role, text, **extra):
    row = {"slot": name, "role": role, "text": text, "beat_id": None, "take": None, "prompt": None, "component": None}
    return {**row, **extra}


class PlanV2Tests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-plan-v2-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def scene(self, body, plugins=frozenset()):
        doc = roteiro.parse(HEAD + body, plugins=plugins)
        return roteiro_plan.scene_plan(self.project, doc)["scenes"][0]

    def test_version_and_meta_carry_tema(self):
        plan = roteiro_plan.scene_plan(self.project, roteiro.parse(PINNED, plugins=frozenset()))
        self.assertEqual(2, plan["plan_version"])
        self.assertEqual("IA editando reels", plan["meta"]["tema"])
        self.assertEqual(
            ["aspecto", "legenda", "duracao_alvo_s", "genero", "tema", "cliente", "direcao"], list(plan["meta"])
        )

    def test_hashes_are_the_same_as_plan_version_one(self):
        doc = roteiro.parse(PINNED, plugins=frozenset())
        plan = roteiro_plan.scene_plan(self.project, doc)
        self.assertEqual(V1_REVIEW_HASH, roteiro_review.review_hash(doc, self.project))
        self.assertEqual(V1_CONTENT_HASHES, [s["content_hash"] for s in plan["scenes"]])
        self.assertEqual(1, roteiro_plan.HASH_VERSION)

    def test_words_count_spoken_words_without_notes(self):
        row = self.scene("## A <!-- c01 -->\n[A-ROLL]\nEu digo [risos] o tema, ele acha.\n[pausa]\nCorta!\n")
        self.assertEqual(7, row["words"])
        self.assertEqual(0, self.scene('## F <!-- c01 -->\n[FULL: "Só texto"]\n')["words"])
        self.assertEqual(roteiro.spoken_words("a b [c] d"), 3)

    def test_aroll_and_ugc_have_one_main_presenter(self):
        self.assertEqual(
            [slot("main", "presenter", "A-ROLL", component=0)],
            self.scene("## A <!-- c01 -->\n[A-ROLL]\nOi.\n")["layout"]["slots"],
        )
        self.assertEqual(
            [slot("main", "presenter", "A-ROLL: t2", take="t2", component=0)],
            self.scene("## A <!-- c01 -->\n[A-ROLL: T2]\nOi.\n")["layout"]["slots"],
        )
        self.assertEqual(
            [slot("main", "presenter", "UGC: moça no café", prompt="moça no café", component=0)],
            self.scene("## U <!-- c01 -->\n[UGC: moça no café]\nOi.\n")["layout"]["slots"],
        )

    def test_broll_slot_carries_the_scene_beat(self):
        row = self.scene("## B <!-- c02 -->\n[BROLL: timeline cheia]\nOi.\n")
        self.assertEqual([slot("main", "broll", "timeline cheia", beat_id="c02")], row["layout"]["slots"])
        self.assertIsNone(row["layout"]["full_role"])
        unsynced = self.scene("## B\n[BROLL: timeline cheia]\nOi.\n")
        self.assertIsNone(unsynced["layout"]["slots"][0]["beat_id"])

    def test_split_sides_follow_beat_rules(self):
        cases = {
            "[SPLIT: tela do app | A-ROLL]": [
                slot("a", "broll", "tela do app", beat_id="c03-a"),
                slot("b", "presenter", "A-ROLL", component=0),
            ],
            '[SPLIT: "Texto" | mapa]': [slot("a", "card", "Texto"), slot("b", "broll", "mapa", beat_id="c03-b")],
            "[SPLIT: A-ROLL: t2 | UGC: moça]": [
                slot("a", "presenter", "A-ROLL: t2", take="t2", component=0),
                slot("b", "presenter", "UGC: moça", prompt="moça", component=1),
            ],
        }
        for directive, expected in cases.items():
            with self.subTest(directive=directive):
                row = self.scene(f"## S <!-- c03 -->\n{directive}\n[SFX: whoosh]\nOi.\n")
                self.assertEqual(expected, row["layout"]["slots"])

    def test_split_presenter_component_points_at_its_aroll_row(self):
        row = self.scene("## S <!-- c03 -->\n[SPLIT: mapa | A-ROLL: t3]\nOi.\n")
        index = row["layout"]["slots"][1]["component"]
        self.assertEqual(("aroll", "b", "t3"), tuple(row["components"][index][k] for k in ("kind", "side", "take")))

    def test_full_role_decides_the_single_slot(self):
        (self.project / "assets" / "marca").mkdir(parents=True)
        (self.project / "assets" / "marca" / "selo.png").write_bytes(b"x")
        cases = {
            "[FULL: logo]": ("marca", slot("main", "brand", "logo", component=0)),
            "[FULL: selo]": ("marca", slot("main", "brand", "selo", component=0)),
            '[FULL: "Comenta BROLL"]': ("cartela", slot("main", "card", "Comenta BROLL")),
            "[FULL: cidade à noite, 1998]": ("beat", slot("main", "broll", "cidade à noite, 1998", beat_id="c04")),
        }
        for directive, (role, expected) in cases.items():
            with self.subTest(directive=directive):
                layout = self.scene(f"## F <!-- c04 -->\n{directive}\n")["layout"]
                self.assertEqual(role, layout["full_role"])
                self.assertEqual([expected], layout["slots"])


if __name__ == "__main__":
    unittest.main()
