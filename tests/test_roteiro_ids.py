"""Ids de cena: nunca reaproveitados (roteiro, brief, manifesto, estado), readoção e recusa."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import roteiro, roteiro_ids

HEAD = '---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n'  # corpo começa na linha 6
EMPTY = {"next_id": 1, "scenes": {}}
PROVA = {"next_id": 4, "scenes": {"c03": {"title": "Prova", "layout": "SPLIT", "args": ["tela", "mapa"]}}}


def read(body):
    return roteiro.parse(HEAD + body, plugins=frozenset())


def shot(identifier):
    return {"id": f"youtube:{identifier}", "shot": identifier}


class AllocatorTests(unittest.TestCase):
    def test_next_id_is_above_everything_ever_seen(self):
        doc = read("## A <!-- c01 -->\n[A-ROLL]\n## Nova\n[BROLL: x]\n")
        beats = [{"id": "c02"}, {"id": "c03-b"}, {"id": "manual-9"}]
        state = {"next_id": 5, "scenes": {}}
        plan = roteiro_ids.plan_ids(doc, beats, [shot("c07-a")], state)
        self.assertEqual(plan["assign"], {8: "c08"})
        self.assertEqual(plan["next_id"], 9)
        self.assertIsNone(plan["refusal"])

    def test_manifest_only_shot_reserves_its_id(self):
        doc = read("## Nova\n[BROLL: x]\n")
        plan = roteiro_ids.plan_ids(doc, [], [shot("c04")], EMPTY)
        self.assertEqual(plan["assign"], {6: "c05"})

    def test_beatless_scene_id_is_never_reused_after_deletion(self):
        first = read("## Gancho\n[A-ROLL]\n")
        plan = roteiro_ids.plan_ids(first, [], [], EMPTY)
        self.assertEqual(plan["assign"], {6: "c01"})
        synced = read("## Gancho <!-- c01 -->\n[A-ROLL]\n")
        state = roteiro_ids.next_state(EMPTY, synced, plan["next_id"])
        self.assertEqual(state["scenes"]["c01"], {"title": "Gancho", "layout": "A-ROLL", "args": []})
        later = read("## Outra\n[A-ROLL]\n")
        self.assertEqual(roteiro_ids.plan_ids(later, [], [], state)["assign"], {6: "c02"})

    def test_ids_stop_at_c999(self):
        doc = read("## Nova\n[BROLL: x]\n")
        with self.assertRaises(ValueError) as ctx:
            roteiro_ids.plan_ids(doc, [], [], {"next_id": 1000, "scenes": {}})
        self.assertIn("c999", str(ctx.exception))

    def test_apply_ids_touches_only_the_heading(self):
        text = HEAD + "## Nova   \n[BROLL: x]\nFala.\n"
        out = roteiro_ids.apply_ids(text, {6: "c03"})
        self.assertEqual(out, HEAD + "## Nova <!-- c03 -->\n[BROLL: x]\nFala.\n")
        self.assertEqual(roteiro.parse(out, plugins=frozenset()).scenes[0].scene_id, "c03")


class ReadoptionTests(unittest.TestCase):
    def test_lost_comment_is_readopted_by_exact_signature(self):
        doc = read("## Nova\n[BROLL: y]\n## Prova\n[SPLIT: tela | mapa]\n")
        beats = [{"id": "c03-a"}, {"id": "c03-b"}]
        plan = roteiro_ids.plan_ids(doc, beats, [shot("c03-a")], PROVA)
        self.assertEqual(plan["readopted"], {8: "c03"})
        self.assertEqual(plan["assign"], {8: "c03", 6: "c04"})

    def test_ambiguous_readoption_refuses(self):
        doc = read("## Prova\n[SPLIT: tela | mapa]\n## Prova\n[SPLIT: tela | mapa]\n")
        plan = roteiro_ids.plan_ids(doc, [{"id": "c03-a"}], [shot("c03-a")], PROVA)
        self.assertEqual(plan["assign"], {})
        self.assertIn("c03", plan["refusal"])
        self.assertIn("linha 6, 8", plan["refusal"])

    def test_changed_scene_without_id_refuses_and_suggests_a_new_id(self):
        doc = read("## Prova\n[SPLIT: tela | outro mapa]\n")
        plan = roteiro_ids.plan_ids(doc, [{"id": "c03-a"}], [shot("c03-a")], PROVA)
        self.assertEqual(plan["assign"], {})
        self.assertIn("<!-- c03 -->", plan["refusal"])
        self.assertIn("<!-- c04 -->", plan["refusal"])

    def test_deliberate_deletion_is_not_refused(self):
        doc = read("## Outra <!-- c09 -->\n[BROLL: z]\n")
        plan = roteiro_ids.plan_ids(doc, [{"id": "c03-a"}], [shot("c03-a")], PROVA)
        self.assertIsNone(plan["refusal"])
        self.assertEqual(plan["assign"], {})

    def test_missing_scene_without_candidates_is_not_at_risk(self):
        doc = read("## Nova\n[BROLL: y]\n")
        plan = roteiro_ids.plan_ids(doc, [{"id": "c03-a"}], [], PROVA)
        self.assertEqual(plan["assign"], {6: "c04"})

    def test_every_known_scene_comes_back_when_all_comments_are_lost(self):
        state = {
            "next_id": 5,
            "scenes": {
                "c01": {"title": "Gancho", "layout": "A-ROLL", "args": []},
                "c02": {"title": "Prova", "layout": "SPLIT", "args": ["tela", "mapa"]},
                "c03": {"title": "Demo", "layout": "BROLL", "args": ["x"]},
                "c04": {"title": "CTA", "layout": "BROLL", "args": ["y"]},
            },
        }
        doc = read("## Gancho\n[A-ROLL]\n## Prova\n[SPLIT: tela | mapa]\n## Demo\n[BROLL: x]\n## CTA\n[BROLL: y]\n")
        beats = [{"id": "c02-a"}, {"id": "c02-b"}, {"id": "c03"}, {"id": "c04"}]
        plan = roteiro_ids.plan_ids(doc, beats, [shot("c02-a"), shot("c03")], state)
        expected = {6: "c01", 8: "c02", 10: "c03", 12: "c04"}
        self.assertEqual(plan["readopted"], expected)
        self.assertEqual(plan["assign"], expected)
        self.assertIsNone(plan["refusal"])
        self.assertEqual(plan["next_id"], 5)

    def test_two_missing_ids_with_one_signature_adopt_neither(self):
        twin = {"title": "Prova", "layout": "A-ROLL", "args": []}
        state = {"next_id": 3, "scenes": {"c01": twin, "c02": dict(twin)}}
        plan = roteiro_ids.plan_ids(read("## Prova\n[A-ROLL]\n"), [], [], state)
        self.assertEqual(plan["readopted"], {})
        self.assertEqual(plan["assign"], {6: "c03"})
        self.assertIsNone(plan["refusal"])

    def test_rival_at_risk_ids_with_one_signature_refuse(self):
        twin = {"title": "Prova", "layout": "A-ROLL", "args": []}
        state = {"next_id": 3, "scenes": {"c01": twin, "c02": dict(twin)}}
        plan = roteiro_ids.plan_ids(read("## Prova\n[A-ROLL]\n"), [], [shot("c01"), shot("c02")], state)
        self.assertEqual(plan["assign"], {})
        self.assertIn("c01, c02", plan["refusal"])

    def test_partial_readoption_then_orphan_refusal_writes_nothing(self):
        state = {
            "next_id": 3,
            "scenes": {
                "c01": {"title": "Gancho", "layout": "A-ROLL", "args": []},
                "c02": {"title": "Prova", "layout": "SPLIT", "args": ["tela", "mapa"]},
            },
        }
        text = HEAD + "## Gancho\n[A-ROLL]\n## Prova\n[SPLIT: tela | outro]\n"
        plan = roteiro_ids.plan_ids(roteiro.parse(text, plugins=frozenset()), [], [shot("c01"), shot("c02")], state)
        self.assertEqual(plan["readopted"], {6: "c01"})
        self.assertEqual(plan["assign"], {})
        self.assertIn("c02", plan["refusal"])
        self.assertEqual(roteiro_ids.apply_ids(text, plan["assign"]), text)

    def test_refusal_at_the_ceiling_points_to_the_limit_not_c1000(self):
        state = {"next_id": 1000, "scenes": PROVA["scenes"]}
        doc = read("## Prova\n[SPLIT: tela | outro mapa]\n")
        plan = roteiro_ids.plan_ids(doc, [{"id": "c03-a"}], [shot("c03-a")], state)
        self.assertEqual(plan["assign"], {})
        self.assertNotIn("c1000", plan["refusal"])
        self.assertIn(f"c{roteiro_ids.MAX_SCENE}", plan["refusal"])


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-ids-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_round_trip_is_atomic_and_missing_file_is_empty(self):
        self.assertEqual(roteiro_ids.read_state(self.project), EMPTY)
        self.assertFalse((self.project / "brolls").exists())
        state = {"next_id": 3, "scenes": {"c01": {"title": "A", "layout": "A-ROLL", "args": []}}}
        roteiro_ids.write_state(self.project, state)
        self.assertEqual(roteiro_ids.read_state(self.project), state)
        self.assertEqual([p.name for p in (self.project / "brolls").iterdir()], [roteiro_ids.STATE_FILE])

    def test_legacy_state_without_scenes_reads_as_empty_scenes(self):
        (self.project / "brolls").mkdir()
        roteiro_ids.state_path(self.project).write_text(json.dumps({"next_id": 4}), encoding="utf-8")
        state = roteiro_ids.read_state(self.project)
        self.assertEqual(state, {"next_id": 4, "scenes": {}})
        self.assertEqual(roteiro_ids.plan_ids(read("## Nova\n[A-ROLL]\n"), [], [], state)["assign"], {6: "c04"})

    def test_broken_state_is_a_clean_error(self):
        (self.project / "brolls").mkdir()
        for raw in ("{", json.dumps({"next_id": "3"}), json.dumps({"next_id": 0}), json.dumps([1])):
            with self.subTest(raw=raw):
                roteiro_ids.state_path(self.project).write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError) as ctx:
                    roteiro_ids.read_state(self.project)
                self.assertIn(roteiro_ids.STATE_FILE, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
