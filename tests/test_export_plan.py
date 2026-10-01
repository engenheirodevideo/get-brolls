"""Plano de export: tempo global, vagas com mídia lógica, voz, legendas, camadas, resolvedor injetado e fixtures."""

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT
from _schemas import strict

from getbrolls import analysis, assets, delivery, export_plan, export_voice, layout, roteiro, roteiro_plan, runtime
from getbrolls.sdk.exporters import find_local_paths
from getbrolls.sdk.jsonschema import errors

FIXTURES = ROOT / "tests" / "fixtures"
# Variante fechada do schema publicado: campo novo no plano só passa com o schema atualizado.
SCHEMA = strict()
PLUGINS = frozenset({"hyperframes", "outro"})
UPDATE = os.environ.get("GB_UPDATE_EXPORT_FIXTURES") == "1"
# Campos que mudam a cada rodada ou versão: fora da comparação com a fixture.
VOLATILE = ("generated_at", "getbrolls_version")

MIN_ROTEIRO = """---
type: roteiro
genero: reels
tema: "Reels mínimo"
---

## Gancho <!-- c01 -->
[A-ROLL]
Eu digo o tema.
[SFX: whoosh]
E ele acha.

## Problema <!-- c02 -->
[BROLL: timeline cheia]
Horas cortando.

## Cartela <!-- c03 -->
[FULL: "Comenta BROLL"]
"""

# Entrada hostil sem elemento HTML (o roteiro recusa `<` seguido de letra): `<`, `>` e `/` soltos.
FULL_ROTEIRO = """---
type: roteiro
genero: reels
tema: "IA <editando> reels"
---

## Gancho < script >alert(1)< /script > <!-- c01 -->
[A-ROLL]
Eu digo o tema < /script> e ele acha.
[LETTERING: "3x mais <3 rápido>" | destaque]
Corta e entrega.
[SFX: whoosh]

## Problema <!-- c02 -->
[BROLL: timeline cheia]
Horas cortando vídeo na mão.
[MUSICA: lofi]
[hyperframes:zoom-in: 1.2]
[outro:brilho]

## Prova <!-- c03 -->
[SPLIT: tela do get-brolls | A-ROLL]
Ele acha, corta e organiza.
[COMP: grafico]

## Dupla <!-- c04 -->
[SPLIT: A-ROLL: t2 | A-ROLL]
Nós dois falando.

## Marca <!-- c05 -->
[FULL: selo]
[SFX: pop]

## Cartela <!-- c06 -->
[FULL: "Comenta BROLL"]

## Depoimento <!-- c07 -->
[UGC: moça no café]
Testei e amei.

## CTA <!-- c08 -->
[FULL: logo]
Segue para mais.
[LETTERING: "Siga"]
"""

# Voz falsa por nome de arquivo: o ffprobe de verdade roda só nos testes marcados com FFmpeg.
PROBES = {
    "c01.MOV": {"duration_s": 5.2, "width": 1080, "height": 1920, "has_audio": True},
    "c01.mov": {"duration_s": 2.5, "width": 1080, "height": 1920, "has_audio": True},
    "c03.mp4": {"duration_s": 4.0, "width": 1080, "height": 1920, "has_audio": True},
    "c04-a-t2.mov": {"duration_s": 3.0, "width": 1080, "height": 1920, "has_audio": True},
    "c04-b.mov": {"duration_s": 3.9, "width": 1080, "height": 1920, "has_audio": False},
}


def fake_probe(path):
    name = Path(path).name
    if name not in PROBES:
        raise ValueError("ffprobe falhou")
    return dict(PROBES[name])


def clip(ident, shot, rel, **extra):
    base = {
        "id": ident, "shot": shot, "title": f"Clipe {ident}", "provider": ident.split(":")[0],
        "source_url": f"https://example.com/{ident.split(':')[-1]}",
        "output": {"path": rel, "sha256": "ab" * 32, "verified": True},
        "output_media": {"duration_s": 4.5, "width": 1080, "height": 1920},
        "approval": {"status": "approved"},
    }  # fmt: skip
    return {**base, **extra}


def fake_resolver(calls):
    def resolve(kind, name, extensions):
        calls.append((kind, name, tuple(extensions)))
        if name == "lofi":
            hit = {
                "path": "/fora/do/plano/lofi.mp3", "store": "hyperframes", "license": "media-use bgm [x](http://a)",
                "resolver": "hyperframes_media", "st_dev": 1, "st_ino": 2, "st_size": 3,
            }  # fmt: skip
            return hit, []
        return None, [f"Plugin hyperframes: {kind} {name} não achado"]

    return resolve


class Project:
    """Projeto sintético em disco: roteiro, voz, clipes e componentes."""

    def __init__(self, root):
        self.root = root

    def write(self, relative, data: bytes | str = b"x", mtime_ns=None):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.chmod(0o644)  # regrava mesmo num clipe congelado por uma rodada anterior deste helper
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def license(self, relative):
        data = {"origem": "freesound", "licenca": "CC0", "credito": Path(relative).stem.title()}
        self.write(Path(relative).with_suffix(".licenca.json"), json.dumps(data))

    def voice(self, relative, words=None):
        self.write(relative, b"voz", mtime_ns=1_000_000_000_000)
        if words is not None:
            sidecar = Path(relative).with_name(Path(relative).stem + ".transcript.json")
            self.write(sidecar, json.dumps(words), mtime_ns=2_000_000_000_000)

    def plan(self, text):
        self.write("ROTEIRO.md", text)
        return roteiro_plan.scene_plan(self.root, roteiro.parse(text, plugins=PLUGINS))


def min_project(root):
    project = Project(root)
    project.voice(
        "aroll/c01.mov", [{"text": "Eu", "start": 0.1, "end": 0.3}, {"text": "digo", "start": 0.3, "end": 0.6}]
    )
    project.write("assets/sfx/whoosh.wav")
    project.license("assets/sfx/whoosh.wav")
    project.write("brolls/clips/c02-pexels-123.mp4").chmod(0o444)  # congelado, como o `deliver` deixa
    items = [clip("pexels:123", "c02", "clips/c02-pexels-123.mp4")]
    return project.plan(MIN_ROTEIRO), items


def full_project(root):
    project = Project(root)
    project.voice(
        "aroll/c01.MOV", [{"text": "Eu", "start": 0.0, "end": 0.4}, {"text": "digo", "start": 0.4, "end": 0.9}]
    )
    project.voice("aroll/c03.mp4")
    project.voice("aroll/c04-a-t2.mov")
    project.voice("aroll/c04-b.mov")
    for name in ("whoosh.wav",):
        project.write(f"assets/sfx/{name}")
        project.license(f"assets/sfx/{name}")
    project.write("assets/marca/selo.png")
    project.license("assets/marca/selo.png")
    for rel in ("c02-a.mp4", "c02-b.mov", "c02-rej.mp4", "c02-unv.mp4"):
        project.write(f"brolls/clips/{rel}").chmod(0o444)  # congelado, como o `deliver` deixa
    rejected = clip("pexels:3", "c02", "clips/c02-rej.mp4", approval={"status": "rejected"})
    unverified = clip("pexels:4", "c02", "clips/c02-unv.mp4")
    unverified["output"]["verified"] = False
    items = [
        clip("pexels:1", "c02", "clips/c02-a.mp4"),
        rejected,
        unverified,
        clip("pixabay:2", "c02", "clips/c02-b.mov", title="Segundo <b>clipe</b>"),
    ]
    return project.plan(FULL_ROTEIRO), items


def build(root, maker, out_dir="exports/hyperframes/003", resolve_media=None):
    plan, items = maker(root)
    with mock.patch.object(export_plan, "probe_voice", fake_probe):
        return export_plan.build(root, plan, items, out_dir, resolve_media=resolve_media)


def stable(plan):
    return {key: value for key, value in plan.items() if key not in VOLATILE}


class ExportPlanTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-export-plan-"))
        # O clipe fica congelado (somente leitura, como o `deliver` deixa), e o Windows não
        # apaga arquivo somente leitura: `force_rmtree` devolve a escrita antes.
        self.addCleanup(runtime.force_rmtree, self.root)

    def full(self, calls=None):
        plan, sources = build(self.root, full_project, resolve_media=fake_resolver([] if calls is None else calls))
        return plan, sources, {s["id"]: s for s in plan["scenes"]}


class TimingAndVoiceTests(ExportPlanTestCase):
    def test_scene_starts_are_cumulative_and_real_voice_wins(self):
        plan, _, scenes = self.full()
        durations = [(s["id"], s["start_s"], s["duration_s"], s["duration_source"]) for s in plan["scenes"]]
        self.assertEqual(("c01", 0.0, 5.2, "aroll"), durations[0])
        self.assertEqual(("c02", 5.2, scenes["c02"]["estimate_s"], "estimate"), durations[1])
        for previous, current in zip(plan["scenes"], plan["scenes"][1:], strict=False):
            self.assertEqual(round(previous["start_s"] + previous["duration_s"], 3), current["start_s"])
        self.assertEqual(round(sum(s["duration_s"] for s in plan["scenes"]), 3), plan["total_s"])
        self.assertEqual("mixed", plan["timing"])

    def test_timing_is_estimate_without_any_voice_file(self):
        plan, items = full_project(self.root)
        shutil.rmtree(self.root / "aroll")
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, _ = export_plan.build(self.root, plan, items, "exports/hyperframes/001")
        self.assertEqual("estimate", result["timing"])

    def test_uppercase_extension_is_lowercased_and_source_is_cloned(self):
        plan, sources, scenes = self.full()
        voice = plan["media"]["aroll:c01"]
        self.assertEqual(
            (".mov", True, True, 5.2), (voice["ext"], voice["available"], voice["has_audio"], voice["duration_s"])
        )
        self.assertEqual(["aroll:c01"], scenes["c01"]["voice_media_ids"])
        self.assertEqual("clone", sources["aroll:c01"]["method"])
        self.assertTrue(sources["aroll:c01"]["path"].endswith("c01.MOV"))

    def test_sidecar_words_are_global_and_other_scenes_estimate(self):
        _, _, scenes = self.full()
        self.assertEqual("transcript", scenes["c01"]["words_source"])
        self.assertEqual(
            [{"text": "Eu", "start": 0.0, "end": 0.4}, {"text": "digo", "start": 0.4, "end": 0.9}],
            scenes["c01"]["words_timed"],
        )
        self.assertEqual(("estimate", None), (scenes["c03"]["words_source"], scenes["c03"]["words_timed"]))

    def test_analysis_transcript_times_a_scene_without_sidecar_and_never_hashes(self):
        plan, items = full_project(self.root)
        media_id = analysis.ensure_media(self.root, "aroll/c03.mp4", probe=False)["media_id"]
        spoken = [{"text": "Olá", "start": 0.1, "end": 0.5, "probability": 0.9, "speaker": None}]
        doc = {"status": "done", "reason": None, "language": "pt", "text": "Olá", "word_count": 1, "words": spoken}
        producer = {"tool": "whisper_local", "model": None, "version": None}
        analysis.write_component(self.root, media_id, "transcript", doc, producer=producer)
        with (
            mock.patch.object(export_plan, "probe_voice", fake_probe),
            mock.patch.object(analysis.ledger, "digest", side_effect=AssertionError("export não hasheia")),
        ):
            result, _ = export_plan.build(self.root, plan, items, "exports/hyperframes/001")
        c03 = {s["id"]: s for s in result["scenes"]}["c03"]
        start = c03["start_s"]
        self.assertEqual("transcript", c03["words_source"])
        self.assertEqual(
            [{"text": "Olá", "start": round(start + 0.1, 3), "end": round(start + 0.5, 3)}], c03["words_timed"]
        )
        self.assertEqual([], errors(result, SCHEMA))

    def test_split_with_two_presenters_only_side_a_speaks_and_longest_side_sets_time(self):
        plan, _, scenes = self.full()
        c04 = scenes["c04"]
        self.assertEqual(["aroll:c04-a-t2"], c04["voice_media_ids"])
        self.assertEqual(["aroll:c04-a-t2", "aroll:c04-b"], [s["media_id"] for s in c04["layout"]["slots"]])
        self.assertEqual((3.9, "aroll"), (c04["duration_s"], c04["duration_source"]))
        self.assertIn("c04: duas vozes na mesma cena: só o lado esquerdo tem som", plan["warnings"])

    def test_missing_presenter_and_missing_narration_are_expected_with_warnings(self):
        plan, _, scenes = self.full()
        self.assertEqual(["aroll:c02"], scenes["c02"]["voice_media_ids"])
        narration = plan["media"]["aroll:c02"]
        self.assertEqual(
            (False, "missing", "aroll/c02.(mp4|mov|m4v)"),
            (narration["available"], narration["problem"], narration["expected"]),
        )
        self.assertIn("c02: fala sem narração gravada (grave aroll/c02.mp4|mov|m4v)", plan["warnings"])
        self.assertEqual("missing", plan["media"]["aroll:c07"]["problem"])
        self.assertIn("c07: A-ROLL não gravado (grave aroll/c07.(mp4|mov|m4v))", plan["warnings"])
        self.assertEqual([], scenes["c06"]["voice_media_ids"])

    def test_voice_without_audio_track_and_unreadable_and_link(self):
        plan, items = full_project(self.root)
        PROBES["c03.mp4"]["has_audio"] = False
        self.addCleanup(PROBES["c03.mp4"].__setitem__, "has_audio", True)
        (self.root / "aroll" / "c07.mov").write_bytes(b"lixo")
        (self.root / "aroll" / "real-c08.mp4").write_bytes(b"x")
        try:
            (self.root / "aroll" / "c08.mp4").symlink_to(self.root / "aroll" / "real-c08.mp4")
        except OSError:
            self.skipTest("este sistema não cria symlink")
        text = FULL_ROTEIRO.replace("## CTA <!-- c08 -->\n[FULL: logo]", "## CTA <!-- c08 -->\n[A-ROLL]")
        plan = Project(self.root).plan(text)
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, sources = export_plan.build(self.root, plan, items, "exports/hyperframes/001")
        self.assertIn("c03: aroll/c03.mp4 não tem trilha de áudio", result["warnings"])
        self.assertEqual("unreadable", result["media"]["aroll:c07"]["problem"])
        self.assertIn("c07: não consegui ler aroll/c07.mov (ffprobe): confira o arquivo", result["warnings"])
        self.assertEqual("link", result["media"]["aroll:c08"]["problem"])
        self.assertNotIn("aroll:c08", sources)


class SlotAndClipTests(ExportPlanTestCase):
    def test_broll_takes_first_clip_and_the_rest_as_extras(self):
        plan, sources, scenes = self.full()
        slot = scenes["c02"]["layout"]["slots"][0]
        self.assertEqual(("clip:pexels:1", ["clip:pixabay:2"]), (slot["media_id"], slot["extra_media_ids"]))
        self.assertNotIn("clip:pexels:3", plan["media"])
        self.assertNotIn("clip:pexels:4", plan["media"])
        self.assertEqual(".mov", plan["media"]["clip:pixabay:2"]["ext"])
        self.assertEqual("hardlink", sources["clip:pexels:1"]["method"])
        self.assertEqual("Clipe pexels:1 — pexels (https://example.com/1)", plan["media"]["clip:pexels:1"]["credit"])

    def test_writable_clip_is_planned_as_clone_frozen_one_as_hardlink(self):
        """`brolls/clips/` congelado (como o `deliver` deixa) vira hardlink; ainda gravável, clone/cópia."""
        plan, items = full_project(self.root)
        clip_path = self.root / "brolls" / "clips" / "c02-a.mp4"
        self.assertEqual("hardlink", self.build(plan, items)[1]["clip:pexels:1"]["method"])
        clip_path.chmod(0o644)
        self.addCleanup(clip_path.chmod, 0o444)
        self.assertEqual("clone", self.build(plan, items)[1]["clip:pexels:1"]["method"])

    def build(self, plan, items):
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            return export_plan.build(self.root, plan, items, "exports/hyperframes/001", resolve_media=fake_resolver([]))

    def test_broll_without_clip_is_a_warning_and_no_media(self):
        plan, _, scenes = self.full()
        slots = scenes["c03"]["layout"]["slots"]
        self.assertEqual(("broll", "c03-a", None), (slots[0]["role"], slots[0]["beat_id"], slots[0]["media_id"]))
        self.assertIn("c03: b-roll sem clipe coletado (beat c03-a)", plan["warnings"])

    def test_vanished_clip_is_kept_as_changed_after_the_good_ones(self):
        plan, items = full_project(self.root)
        # Clipe congelado: no Windows, somente leitura só sai depois de degelar.
        delivery._thaw_unlink(self.root / "brolls" / "clips" / "c02-a.mp4")
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, _ = export_plan.build(self.root, plan, items, "exports/hyperframes/001")
        slot = result["scenes"][1]["layout"]["slots"][0]
        self.assertEqual(("clip:pixabay:2", ["clip:pexels:1"]), (slot["media_id"], slot["extra_media_ids"]))
        self.assertEqual("changed", result["media"]["clip:pexels:1"]["problem"])
        self.assertIn("c02: clipe pexels:1 fora do export (o arquivo sumiu)", result["warnings"])

    def test_clip_outside_clips_folder_or_link_is_refused(self):
        plan, items = full_project(self.root)
        items[0]["output"]["path"] = "../fora.mp4"
        delivery._thaw_unlink(self.root / "brolls" / "clips" / "c02-b.mov")
        try:
            (self.root / "brolls" / "clips" / "c02-b.mov").symlink_to(self.root / "ROTEIRO.md")
        except OSError:
            self.skipTest("este sistema não cria symlink")
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, _ = export_plan.build(self.root, plan, items, "exports/hyperframes/001")
        self.assertIn("c02: clipe pexels:1 fora do export (caminho fora de clips/)", result["warnings"])
        self.assertIn("c02: clipe pixabay:2 fora do export (é um link)", result["warnings"])

    def test_retired_beat_clip_is_skipped_with_the_export_event(self):
        plan, items = full_project(self.root)
        brief = {"version": 1, "beats": [{"id": "c02", "target": "x", "retired": True}]}
        (self.root / "BRIEF.md").write_text("```json\n" + json.dumps(brief) + "\n```\n", encoding="utf-8")
        with (
            mock.patch.object(export_plan, "probe_voice", fake_probe),
            mock.patch.object(export_plan.delivery.logs, "event") as event,
        ):
            result, _ = export_plan.build(self.root, plan, items, "exports/hyperframes/001")
        self.assertIsNone(result["scenes"][1]["layout"]["slots"][0]["media_id"])
        names = {call.args[2] for call in event.call_args_list}
        self.assertEqual({"export_skipped"}, names)

    def test_brand_found_pending_and_card(self):
        plan, _, scenes = self.full()
        self.assertEqual("asset:marca:selo", scenes["c05"]["layout"]["slots"][0]["media_id"])
        self.assertEqual(
            ("image", ".png", "Selo — CC0 (freesound)"),
            tuple(plan["media"]["asset:marca:selo"][k] for k in ("kind", "ext", "credit")),
        )
        logo = plan["media"][scenes["c08"]["layout"]["slots"][0]["media_id"]]
        self.assertEqual((False, "missing"), (logo["available"], logo["problem"]))
        self.assertTrue(logo["expected"].startswith("assets/marca/logo."))
        self.assertIn(
            'c08: marca "logo" pendente (ponha o arquivo em assets/marca/logo.<png|svg|webp|jpg|jpeg|mp4|mov>)',
            plan["warnings"],
        )
        self.assertFalse(any(w.startswith("c05: marca") for w in plan["warnings"]))
        card = scenes["c06"]["layout"]["slots"][0]
        self.assertEqual(("card", "Comenta BROLL", None), (card["role"], card["text"], card["media_id"]))

    def test_slots_drop_the_component_index(self):
        _, _, scenes = self.full()
        for scene in scenes.values():
            for slot in scene["layout"]["slots"]:
                self.assertEqual(
                    ["slot", "role", "text", "beat_id", "take", "prompt", "media_id", "extra_media_ids"], list(slot)
                )


class LayoutOneClipTests(ExportPlanTestCase):
    """Layout 1: o clipe mora em `broll/`; a antiga `brolls/clips/` ainda vale; o plano não muda de forma."""

    def layout_one(self, move=True):
        plan, items = min_project(self.root)
        layout.write_project(self.root, layout.new_project_doc())
        legacy = self.root / "brolls" / "clips" / "c02-pexels-123.mp4"
        if move:
            (self.root / "broll").mkdir()
            legacy.rename(self.root / "broll" / legacy.name)
        return plan, items

    def build_plan(self, plan, items):
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            return export_plan.build(self.root, plan, items, "exports/hyperframes/001")

    def test_clip_in_broll_is_exported_with_the_same_plan_row(self):
        plan0, items0 = min_project(self.root)
        before, _ = self.build_plan(plan0, items0)
        runtime.force_rmtree(self.root)
        self.root.mkdir()
        plan, items = self.layout_one()
        result, sources = self.build_plan(plan, items)
        self.assertEqual(before["media"]["clip:pexels:123"], result["media"]["clip:pexels:123"])
        self.assertEqual(before["export_version"], result["export_version"])
        self.assertEqual(self.root.resolve() / "broll" / "c02-pexels-123.mp4", Path(sources["clip:pexels:123"]["path"]))
        self.assertEqual("hardlink", sources["clip:pexels:123"]["method"])

    def test_clip_left_in_the_legacy_folder_is_still_exported(self):
        plan, items = self.layout_one(move=False)
        result, sources = self.build_plan(plan, items)
        self.assertTrue(result["media"]["clip:pexels:123"]["available"])
        self.assertEqual(
            self.root.resolve() / "brolls" / "clips" / "c02-pexels-123.mp4", Path(sources["clip:pexels:123"]["path"])
        )

    def test_linked_broll_folder_is_not_followed(self):
        plan, items = self.layout_one()
        outside = Path(tempfile.mkdtemp(prefix="gb-export-outside-"))
        self.addCleanup(runtime.force_rmtree, outside)
        target = outside / "broll"
        shutil.move(self.root / "broll", target)
        try:
            (self.root / "broll").symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        result, sources = self.build_plan(plan, items)
        self.assertEqual(
            (False, "link"),
            (result["media"]["clip:pexels:123"]["available"], result["media"]["clip:pexels:123"]["problem"]),
        )
        self.assertNotIn("clip:pexels:123", sources)
        self.assertIn("c02: clipe pexels:123 fora do export (a pasta broll/ é um link)", result["warnings"])

    def test_same_name_in_both_folders_without_a_matching_hash_is_left_out(self):
        plan, items = self.layout_one()
        (self.root / "brolls" / "clips" / "c02-pexels-123.mp4").write_bytes(b"outro")
        result, sources = self.build_plan(plan, items)
        self.assertEqual("changed", result["media"]["clip:pexels:123"]["problem"])
        self.assertNotIn("clip:pexels:123", sources)
        self.assertIn(
            "c02: clipe pexels:123 fora do export (o mesmo nome em broll/ e em brolls/clips/)", result["warnings"]
        )


class LayerTests(ExportPlanTestCase):
    def test_layers_carry_global_time_text_name_and_media(self):
        _, _, scenes = self.full()
        c01 = scenes["c01"]
        lettering, sfx = c01["layers"]
        self.assertEqual(
            ("LETTERING", "3x mais <3 rápido>", "destaque", None),
            (lettering["kind"], lettering["text"], lettering["name"], lettering["media_id"]),
        )
        # 11 palavras na cena (o "script" de < /script> conta), 8 antes da camada, voz real de 5,2 s.
        self.assertEqual(11, c01["words"])
        self.assertEqual(round(5.2 * 8 / 11, 3), lettering["at_s"])
        self.assertEqual(
            ("fim", 5.2, "asset:sfx:whoosh", "found"),
            (sfx["anchor"], sfx["at_s"], sfx["media_id"], sfx["component_status"]),
        )
        c05 = scenes["c05"]  # camada sem fala depois: âncora "fim", no fim da cena
        self.assertEqual(
            ("fim", round(c05["start_s"] + c05["duration_s"], 3)),
            (c05["layers"][0]["anchor"], c05["layers"][0]["at_s"]),
        )
        comp = scenes["c03"]["layers"][0]
        self.assertEqual(
            ("COMP", "grafico", None, "pending"),
            (comp["kind"], comp["name"], comp["media_id"], comp["component_status"]),
        )
        siga = scenes["c08"]["layers"][0]
        self.assertEqual(("Siga", None, None), (siga["text"], siga["name"], siga["component_status"]))

    def test_estimate_time_matches_words_per_second(self):
        _, _, scenes = self.full()
        c02 = scenes["c02"]
        self.assertEqual("estimate", c02["duration_source"])
        for placed in (*c02["layers"], *c02["extensions"]):
            self.assertAlmostEqual(c02["start_s"] + placed["word_offset"] / 2.5, placed["at_s"], places=3)

    def test_resolver_is_injected_only_for_pending_sfx_and_music(self):
        calls = []
        plan, sources, scenes = self.full(calls)
        self.assertEqual([("musica", "lofi"), ("sfx", "pop")], [(k, n) for k, n, _ in calls])
        self.assertIn(".mp3", calls[0][2])
        music = scenes["c02"]["layers"][0]
        self.assertEqual("plugin:hyperframes:musica:lofi", music["media_id"])
        row = plan["media"]["plugin:hyperframes:musica:lofi"]
        self.assertEqual(("plugin_store", "hyperframes", ".mp3"), (row["source"], row["store"], row["ext"]))
        self.assertEqual("Licença informada pelo plugin hyperframes: media-use bgm [x](http://a)", row["credit"])
        self.assertEqual(
            ("plugin", 1, 2),
            (
                sources[music["media_id"]]["method"],
                sources[music["media_id"]]["st_dev"],
                sources[music["media_id"]]["st_ino"],
            ),
        )
        # A cópia reabre o acerto nas raízes deste resolvedor, não nas de outro do mesmo plugin.
        self.assertEqual(
            ("hyperframes", "hyperframes_media"),
            (sources[music["media_id"]]["store"], sources[music["media_id"]]["resolver"]),
        )
        self.assertIsNone(scenes["c05"]["layers"][0]["media_id"])
        self.assertIn("c05: Plugin hyperframes: sfx pop não achado", plan["warnings"])
        self.assertIn('c05: SFX "pop" pendente (não achei em sfx)', plan["warnings"])

    def test_without_resolver_nothing_from_plugins(self):
        plan, _ = build(self.root, full_project)
        self.assertFalse([m for m in plan["media"] if m.startswith("plugin:")])

    def test_other_plugin_extension_is_a_warning_and_kept(self):
        plan, _, scenes = self.full()
        self.assertEqual(["hyperframes", "outro"], [e["plugin"] for e in scenes["c02"]["extensions"]])
        self.assertIn("c02: diretiva [outro:brilho] não é tratada pelo exporter hyperframes", plan["warnings"])
        self.assertNotIn("zoom-in", " ".join(plan["warnings"]))


class ContractTests(ExportPlanTestCase):
    def test_top_level_shape_and_versions(self):
        plan, _, _ = self.full()
        self.assertEqual(
            ["export_version", "exporter", "out_dir", "generated_at", "getbrolls_version", "plan_version", "meta",
             "total_s", "timing", "scenes", "media", "warnings"],
            list(plan),
        )  # fmt: skip
        self.assertEqual(
            (1, "hyperframes", "exports/hyperframes/003", 2),
            (plan["export_version"], plan["exporter"], plan["out_dir"], plan["plan_version"]),
        )
        self.assertRegex(plan["generated_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_plan_matches_the_schema_and_refs(self):
        plan, _, _ = self.full()
        self.assertEqual([], errors(plan, SCHEMA))
        self.assertEqual([], export_plan.check_refs(plan))

    def test_check_refs_finds_bad_ids_and_dangling_refs(self):
        plan, _, _ = self.full()
        plan["media"]["clip:com espaço"] = plan["media"].pop("clip:pixabay:2")
        problems = export_plan.check_refs(plan)
        self.assertIn("id de mídia fora do padrão: 'clip:com espaço'", problems)
        self.assertIn("c02: mídia 'clip:pixabay:2' não está na tabela", problems)

    def test_media_id_grammar(self):
        for good in ("clip:pexels:123", "aroll:c01-a-t2", "asset:sfx:minha%20trilha", "plugin:hf:musica:lofi"):
            self.assertTrue(export_plan.valid_media_id(good), good)
        for bad in ("clip:a/b", "aroll:c 1", "asset:x\\y", "outro:x", "clip:" + "x" * 200, "clip:\x00"):
            self.assertFalse(export_plan.valid_media_id(bad), bad)
        self.assertEqual("minha%20trilha", export_plan.id_name("Minha  Trilha"))

    def test_no_absolute_path_anywhere(self):
        plan, _, _ = self.full()
        raw = json.dumps(plan, ensure_ascii=False)
        for needle in (
            str(self.root),
            str(self.root.resolve()),
            os.environ["GB_HOME"],
            str(Path.home()),
            "/fora/do/plano",
        ):
            self.assertNotIn(needle, raw)
        self.assertIsNone(re.search(r"/Users/|/home/|[A-Za-z]:\\\\", raw))
        # O mesmo detector que o core aplica ao resultado do exporter (também olha as chaves).
        self.assertEqual([], find_local_paths(plan))

    def test_out_dir_must_be_a_numbered_export_folder(self):
        plan, items = min_project(self.root)
        for bad in ("exports/hyperframes", "/abs/exports/hyperframes/001", "exports/HF/001", "exports/hyperframes/01"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                export_plan.build(self.root, plan, items, bad)

    def test_same_input_same_plan(self):
        first = stable(build(self.root, full_project, resolve_media=fake_resolver([]))[0])
        second = stable(build(self.root, full_project, resolve_media=fake_resolver([]))[0])
        self.assertEqual(first, second)


class LinkedFolderTests(ExportPlanTestCase):
    """Pasta do projeto trocada por link (para fora): o export não segue; a biblioteca pessoal pode ser link."""

    def link_out(self, relative):
        outside = Path(tempfile.mkdtemp(prefix="gb-export-outside-"))
        self.addCleanup(runtime.force_rmtree, outside)
        folder = self.root / relative
        target = outside / folder.name
        shutil.move(folder, target)
        try:
            folder.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        return target

    def linked(self, relative):
        plan, items = full_project(self.root)
        target = self.link_out(relative)
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, sources = export_plan.build(
                self.root, plan, items, "exports/hyperframes/001", resolve_media=fake_resolver([])
            )
        self.assertEqual([], errors(result, SCHEMA))
        self.assertEqual([], export_plan.check_refs(result))
        for source in sources.values():
            self.assertFalse(Path(source["path"]).is_relative_to(target), source)
        return result, sources

    def assert_link(self, result, sources, media_id):
        row = result["media"][media_id]
        self.assertEqual((False, "link"), (row["available"], row["problem"]), media_id)
        self.assertNotIn(media_id, sources)

    def test_linked_aroll_folder_is_not_followed_nor_its_transcript(self):
        result, sources = self.linked("aroll")
        for media_id in ("aroll:c01", "aroll:c03", "aroll:c04-a-t2", "aroll:c04-b"):
            self.assert_link(result, sources, media_id)
        scenes = {s["id"]: s for s in result["scenes"]}
        self.assertEqual(("estimate", None), (scenes["c01"]["words_source"], scenes["c01"]["words_timed"]))
        self.assertEqual("estimate", result["timing"])
        self.assertIn(
            "c01: a pasta aroll/ é um link: o export não segue link; troque pela pasta real", result["warnings"]
        )

    def test_linked_assets_folder_is_not_followed(self):
        result, sources = self.linked("assets")
        self.assert_link(result, sources, "asset:sfx:whoosh")
        self.assert_link(result, sources, "asset:marca:selo")
        self.assertIn(
            "c01: a pasta assets/ é um link: o export não segue link; troque pela pasta real", result["warnings"]
        )

    def test_linked_asset_kind_folder_is_not_followed(self):
        result, sources = self.linked("assets/sfx")
        self.assert_link(result, sources, "asset:sfx:whoosh")
        self.assertTrue(result["media"]["asset:marca:selo"]["available"])
        self.assertIn(
            "c01: a pasta assets/sfx/ é um link: o export não segue link; troque pela pasta real", result["warnings"]
        )

    def test_linked_brolls_folder_is_not_followed(self):
        result, sources = self.linked("brolls")
        self.assert_link(result, sources, "clip:pexels:1")
        self.assert_link(result, sources, "clip:pixabay:2")
        self.assertIn("c02: clipe pexels:1 fora do export (a pasta brolls/ é um link)", result["warnings"])

    def test_linked_clips_folder_is_not_followed(self):
        result, sources = self.linked("brolls/clips")
        self.assert_link(result, sources, "clip:pexels:1")
        self.assertIn("c02: clipe pexels:1 fora do export (a pasta brolls/clips/ é um link)", result["warnings"])

    def test_personal_library_may_be_a_link_and_is_cloned(self):
        disk = Path(tempfile.mkdtemp(prefix="gb-export-disk-"))
        home = Path(tempfile.mkdtemp(prefix="gb-export-home-"))
        for folder in (disk, home):
            self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        (disk / "sfx").mkdir()
        (disk / "sfx" / "pop.wav").write_bytes(b"x")
        try:
            (home / "assets").symlink_to(disk, target_is_directory=True)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        with mock.patch.object(assets, "home_dir", return_value=home):
            plan, sources, _ = self.full()
        row = plan["media"]["asset:sfx:pop"]
        self.assertEqual((True, "personal", None), (row["available"], row["origin"], row["problem"]))
        self.assertEqual("clone", sources["asset:sfx:pop"]["method"])


class LongNameTests(ExportPlanTestCase):
    """Nome válido longo em escrita não latina não quebra o plano; id longo ganha sufixo de hash estável."""

    CJK = "编辑" * 15  # 30 caracteres
    GREEK = "αβγδεζηθικ" * 4  # 40 caracteres

    def test_non_latin_brand_and_sfx_names_fit_the_id_grammar(self):
        project = Project(self.root)
        project.write(f"assets/marca/{self.CJK}.png")
        project.write(f"assets/sfx/{self.GREEK}.wav")
        text = (
            f'---\ntype: roteiro\ngenero: reels\ntema: "x"\n---\n\n'
            f"## Marca <!-- c01 -->\n[FULL: {self.CJK}]\n[SFX: {self.GREEK}]\n"
        )
        plan = project.plan(text)
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, sources = export_plan.build(self.root, plan, [], "exports/hyperframes/001")
        brand, sfx = f"asset:marca:{self.CJK}", f"asset:sfx:{self.GREEK}"
        self.assertEqual(brand, result["scenes"][0]["layout"]["slots"][0]["media_id"])
        self.assertEqual(sfx, result["scenes"][0]["layers"][0]["media_id"])
        self.assertTrue(result["media"][brand]["available"] and result["media"][sfx]["available"])
        self.assertEqual({brand, sfx}, set(sources))
        self.assertEqual([], errors(result, SCHEMA))

    def test_id_name_escapes_only_space_and_percent(self):
        self.assertEqual(self.CJK, export_plan.id_name(self.CJK))
        self.assertEqual("acao%20rapida%2550", export_plan.id_name("Ação  Rápida%50"))

    def test_too_long_id_gets_a_stable_hash_suffix(self):
        prefix = "plugin:" + "s" * 32 + ":musica:"
        name = "a " * 39 + "a"
        other = "a " * 39 + "b"
        first = export_plan.named_id(prefix, name)
        self.assertTrue(export_plan.valid_media_id(first), first)
        self.assertTrue(first.startswith(prefix))
        self.assertEqual(first, export_plan.named_id(prefix, name))
        self.assertNotEqual(first, export_plan.named_id(prefix, other))
        self.assertEqual("asset:sfx:pop", export_plan.named_id("asset:sfx:", "Pop"))


class ResolverHygieneTests(ExportPlanTestCase):
    def test_plugin_warning_paths_are_scrubbed(self):
        # Montados em tempo de execução: o guarda do repositório recusa caminho de máquina escrito no código.
        posix = "/".join(("", "Users", "fulana", "x"))
        windows = "\\".join(("C:", "Users", "fulana", "a.mp3"))
        uri = "file://" + "/".join(("", "home", "fulana", "a.mp3"))

        def resolve(kind, name, extensions):
            text = f"Plugin hyperframes: {kind} {name}: falhou em {posix}/{name}.mp3, {windows}, ~/lib/a.mp3 e {uri}"
            return None, [text + " (veja https://example.com/a)"]

        plan, _ = build(self.root, full_project, resolve_media=resolve)
        raw = json.dumps(plan["warnings"], ensure_ascii=False)
        self.assertNotIn("fulana", raw)
        self.assertEqual([], find_local_paths(plan))
        self.assertIn("https://example.com/a", raw)
        self.assertIn("<caminho>", raw)

    def test_resolver_is_called_once_per_kind_and_name(self):
        calls = []
        plan, items = full_project(self.root)
        text = FULL_ROTEIRO.replace('[FULL: "Comenta BROLL"]', '[FULL: "Comenta BROLL"]\n[SFX: pop]\n[MUSICA: lofi]')
        plan = Project(self.root).plan(text)
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, _ = export_plan.build(
                self.root, plan, items, "exports/hyperframes/001", resolve_media=fake_resolver(calls)
            )
        self.assertEqual([("musica", "lofi"), ("sfx", "pop")], [(k, n) for k, n, _ in calls])
        self.assertEqual(1, sum("sfx pop não achado" in w for w in result["warnings"]))
        c06 = next(s for s in result["scenes"] if s["id"] == "c06")
        self.assertEqual([None, "plugin:hyperframes:musica:lofi"], [layer["media_id"] for layer in c06["layers"]])
        self.assertIn('c06: SFX "pop" pendente (não achei em sfx)', result["warnings"])


class MissingProbeTests(ExportPlanTestCase):
    def test_missing_ffprobe_is_one_warning_and_every_voice_is_estimated(self):
        plan, items = full_project(self.root)
        with mock.patch.object(export_voice.media.subprocess, "run", side_effect=FileNotFoundError):
            result, sources = export_plan.build(self.root, plan, items, "exports/hyperframes/001")
        self.assertEqual([], errors(result, SCHEMA))
        self.assertEqual(1, result["warnings"].count(export_plan.NO_FFPROBE))
        self.assertEqual(
            "ffprobe não encontrado: instale FFmpeg/ffprobe ou aponte GB_FFMPEG_PATH/GB_FFPROBE_PATH; "
            "verifique python3 scripts/gb.py doctor; as durações ficaram estimadas",
            export_plan.NO_FFPROBE,
        )
        self.assertFalse(any("não consegui ler" in w for w in result["warnings"]), result["warnings"])
        for name in ("aroll:c01", "aroll:c03", "aroll:c04-a-t2", "aroll:c04-b"):
            row = result["media"][name]
            self.assertEqual((False, "no_ffprobe"), (row["available"], row["problem"]), name)
            self.assertNotIn(name, sources)
        self.assertEqual({"estimate"}, {s["duration_source"] for s in result["scenes"]})
        self.assertEqual("missing", result["media"]["aroll:c02"]["problem"])


class CreditTests(ExportPlanTestCase):
    """`credit` é sempre texto cru, de toda fonte: quem escapa é o exporter, no formato dele."""

    HOSTILE = "T <b>x</b> [l](http://e) *a* www.site"

    def test_every_credit_in_the_plan_is_raw_text(self):
        _, items = full_project(self.root)
        project = Project(self.root)
        project.write("brolls/clips/c02-plug.mp4")
        items.append(clip("meuplug:9", "c02", "clips/c02-plug.mp4", title=self.HOSTILE, source_url="https://e.com/[x]"))
        data = {"origem": "banco [y](http://b)", "licenca": "CC0", "credito": self.HOSTILE}
        project.write("assets/sfx/whoosh.licenca.json", json.dumps(data))
        plan = project.plan(FULL_ROTEIRO)  # a licença entra no plano de cena: relido depois de trocar o sidecar
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, _ = export_plan.build(
                self.root, plan, items, "exports/hyperframes/001", resolve_media=fake_resolver([])
            )
        media = result["media"]
        self.assertEqual(f"{self.HOSTILE} — meuplug (https://e.com/[x])", media["clip:meuplug:9"]["credit"])
        self.assertEqual("Segundo <b>clipe</b> — pixabay (https://example.com/2)", media["clip:pixabay:2"]["credit"])
        self.assertEqual(f"{self.HOSTILE} — CC0 (banco [y](http://b))", media["asset:sfx:whoosh"]["credit"])
        self.assertEqual(
            "Licença informada pelo plugin hyperframes: media-use bgm [x](http://a)",
            media["plugin:hyperframes:musica:lofi"]["credit"],
        )
        self.assertNotIn("\\", json.dumps([row["credit"] for row in media.values()], ensure_ascii=False))

    def test_only_http_and_https_links_enter_the_credit(self):
        _, items = full_project(self.root)
        project = Project(self.root)
        urls = {
            "js": "javascript:alert(1)", "data": "data:text/html,<b>x</b>", "file": "file:///etc/passwd",
            "ftp": "ftp://e.com/x", "http": "http://e.com/x", "https": "HTTPS://e.com/y",
        }  # fmt: skip
        for name, url in urls.items():
            project.write(f"brolls/clips/c02-{name}.mp4")
            items.append(clip(f"meuplug:{name}", "c02", f"clips/c02-{name}.mp4", title="T", source_url=url))
        plan = project.plan(FULL_ROTEIRO)
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, _ = export_plan.build(
                self.root, plan, items, "exports/hyperframes/001", resolve_media=fake_resolver([])
            )
        shown = {name: result["media"][f"clip:meuplug:{name}"]["credit"] for name in urls}
        for name in ("js", "data", "file", "ftp"):
            with self.subTest(name=name):
                self.assertEqual("T — meuplug (link sem http/https omitido)", shown[name])
        self.assertEqual("T — meuplug (http://e.com/x)", shown["http"])
        self.assertEqual("T — meuplug (HTTPS://e.com/y)", shown["https"])


class MetaIdentityTests(ExportPlanTestCase):
    """`meta` nomeia projeto, cliente, direção, fps e quadro; só o id do projeto vem preenchido."""

    def test_meta_names_the_project_and_reserves_the_rest(self):
        plan, items = min_project(self.root)
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, _ = export_plan.build(self.root, plan, items, "exports/hyperframes/001", project_id="id-do-projeto")
        self.assertEqual(
            [
                "aspecto",
                "legenda",
                "duracao_alvo_s",
                "genero",
                "tema",
                "projeto_id",
                "cliente",
                "direcao",
                "fps",
                "canvas",
            ],
            list(result["meta"]),
        )
        meta = result["meta"]
        self.assertEqual(
            ("id-do-projeto", None, None, None, None),
            (meta["projeto_id"], meta["cliente"], meta["direcao"], meta["fps"], meta["canvas"]),
        )
        self.assertEqual([], errors(result, SCHEMA))

    def test_layers_and_scenes_reserve_ref_and_direction(self):
        plan, _, _ = self.full()
        self.assertTrue(any(scene["layers"] for scene in plan["scenes"]))
        for scene in plan["scenes"]:
            self.assertEqual([], scene["direction"])
            self.assertEqual([], [layer for layer in scene["layers"] if layer["ref"] is not None])
            self.assertTrue(all("ref" in layer for layer in scene["layers"]))

    def test_client_and_direction_come_from_the_frontmatter(self):
        project = Project(self.root)
        text = MIN_ROTEIRO.replace('tema: "Reels mínimo"', 'tema: "Reels mínimo"\ncliente: acme\ndirecao: rampa-e-whip')
        with mock.patch.object(export_plan, "probe_voice", fake_probe):
            result, _ = export_plan.build(self.root, project.plan(text), [], "exports/hyperframes/001")
        self.assertEqual(("acme", "rampa-e-whip"), (result["meta"]["cliente"], result["meta"]["direcao"]))
        self.assertEqual(
            [
                "aspecto",
                "legenda",
                "duracao_alvo_s",
                "genero",
                "tema",
                "projeto_id",
                "cliente",
                "direcao",
                "fps",
                "canvas",
            ],
            list(result["meta"]),
        )
        self.assertEqual([], errors(result, SCHEMA))

    def test_without_a_project_id_it_stays_null(self):
        plan, _ = build(self.root, min_project)
        self.assertIsNone(plan["meta"]["projeto_id"])
        self.assertEqual([], errors(plan, SCHEMA))


class FixtureTests(ExportPlanTestCase):
    """As fixtures do plugin HyperFrames saem deste builder: congeladas, e conferidas a cada rodada."""

    def check_fixture(self, name, maker, resolve_media=None):
        plan, _ = build(self.root, maker, resolve_media=resolve_media)
        path = FIXTURES / name
        if UPDATE:
            frozen = {**plan, "generated_at": "2026-09-25T18:00:00Z", "getbrolls_version": "2.5.0"}
            path.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fixture = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([], errors(fixture, SCHEMA))
        self.assertEqual([], export_plan.check_refs(fixture))
        self.assertEqual(stable(fixture), stable(plan), f"rode GB_UPDATE_EXPORT_FIXTURES=1 para regenerar {name}")

    def test_min_fixture_is_what_the_builder_makes(self):
        self.check_fixture("export_plan_min.json", min_project)

    def test_full_fixture_is_what_the_builder_makes(self):
        self.check_fixture("export_plan_full.json", full_project, fake_resolver([]))


class RealVoiceTests(ExportPlanTestCase):
    @skip_unless_ffmpeg
    def test_real_ffprobe_sets_the_duration(self):
        plan, items = min_project(self.root)
        (self.root / "aroll" / "c01.mov").unlink()
        synth_video(self.root / "aroll" / "c01.mov", duration=3)
        result, _ = export_plan.build(self.root, plan, items, "exports/hyperframes/001")
        self.assertAlmostEqual(3.0, result["scenes"][0]["duration_s"], delta=0.15)
        self.assertFalse(result["media"]["aroll:c01"]["has_audio"])
        self.assertEqual("estimate", result["scenes"][0]["words_source"])


if __name__ == "__main__":
    unittest.main()
