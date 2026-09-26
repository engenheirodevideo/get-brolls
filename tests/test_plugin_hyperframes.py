"""Exporter HyperFrames (exemplo do SDK): do plano fixo ao projeto — contrato do HyperFrames, escape e pureza."""

import builtins
import copy
import json
import re
import shutil
import subprocess
import types
import unittest
from html.parser import HTMLParser
from pathlib import PurePosixPath
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT

from getbrolls.sdk import ExportResult, MediaRequest
from getbrolls.sdk.exporters import find_local_paths, validate_export_result

PLUGIN = ROOT / "examples" / "plugins" / "hyperframes" / "plugin.py"
FIXTURES = ROOT / "tests" / "fixtures"


def load_plugin():
    """Carrega o `plugin.py` do exemplo como o loader do core: da fonte, sem gravar `__pycache__` na pasta."""
    module = types.ModuleType("getbrolls_example_hyperframes")
    module.__file__ = str(PLUGIN)
    code = compile(PLUGIN.read_bytes(), str(PLUGIN), "exec", dont_inherit=True)
    exec(code, module.__dict__)  # noqa: S102 - runs the repository's own example plugin, the same way the loader does
    return module


hf = load_plugin()


def fixture(name="export_plan_full.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Elements(HTMLParser):
    """Toda tag com atributos, e o texto de cada `<script>` e comentário."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.scripts, self.comments, self._in_script = [], [], [], False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self._in_script = tag == "script"

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.scripts.append(data)

    def handle_comment(self, data):
        self.comments.append(data)

    def all(self, tag):
        return [attrs for name, attrs in self.tags if name == tag]

    def timed(self):
        return [(tag, attrs) for tag, attrs in self.tags if "data-start" in attrs]


class Tree(HTMLParser):
    """Cada tag com seus atributos e a pilha de ancestrais (tag, atributos) no momento em que abriu."""

    VOID = frozenset({"img", "meta", "br", "input", "link", "source", "hr"})

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.nodes, self._stack = [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs), tuple(self._stack)))
        if tag not in self.VOID:
            self._stack.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs), tuple(self._stack)))

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self._stack and self._stack.pop()[0] != tag:
            pass

    def by_id(self, element_id):
        return next((tag, attrs, parents) for tag, attrs, parents in self.nodes if attrs.get("id") == element_id)


def z_index(text, selector):
    """`z-index` da regra CSS `selector { … }` no texto gerado."""
    found = re.search(re.escape(selector) + r" \{[^}]*z-index: (\d+)", text)
    assert found is not None, selector
    return int(found.group(1))


def js_const(text, name):
    """Valor JSON da `const NAME = [...];` de um `<script>` gerado."""
    found = re.search(rf"const {name} = (\[.*?\]);\n", text)
    assert found is not None, name
    return json.loads(found.group(1).replace("<\\/", "</"))


def generate(plan=None):
    return hf.generate(fixture() if plan is None else plan)


class RootTests(unittest.TestCase):
    def test_canvas_by_aspect(self):
        for aspect, (w, h, resolution) in {"9:16": (1080, 1920, "portrait"), "16:9": (1920, 1080, "landscape")}.items():
            with self.subTest(aspect=aspect):
                plan = fixture()
                plan["meta"]["aspecto"] = aspect
                page = Elements(generate(plan)["files"]["index.html"])
                self.assertEqual(resolution, page.all("html")[0]["data-resolution"])
                root = page.all("div")[0]
                self.assertEqual((str(w), str(h), "30"), (root["data-width"], root["data-height"], root["data-fps"]))

    def test_root_duration_is_total_and_hosts_use_global_time(self):
        plan = fixture()
        page = Elements(generate(plan)["files"]["index.html"])
        root = page.all("div")[0]
        self.assertEqual(("main", plan["total_s"]), (root["data-composition-id"], float(root["data-duration"])))
        hosts = {a["data-composition-id"]: a for a in page.all("div") if "data-composition-src" in a}
        for scene in plan["scenes"]:
            host = hosts[f"scene-{scene['id']}"]
            self.assertEqual(f"el-scene-{scene['id']}", host["id"])
            self.assertEqual(f"compositions/scene-{scene['id']}.html", host["data-composition-src"])
            self.assertEqual(
                (scene["start_s"], scene["duration_s"]), (float(host["data-start"]), float(host["data-duration"]))
            )
        self.assertEqual(("captions", "0"), (hosts["captions"]["data-track-kind"], hosts["captions"]["data-start"]))
        self.assertIn('window.__timelines["main"] = tl;', page.scripts[-1])

    def test_host_inner_and_timeline_ids_match(self):
        files = generate()["files"]
        for path, text in files.items():
            if not path.startswith("compositions/"):
                continue
            with self.subTest(path=path):
                page = Elements(text)
                cid = PurePosixPath(path).stem
                inner = [a for a in page.all("div") if "data-composition-id" in a]
                self.assertEqual([cid], [a["data-composition-id"] for a in inner])
                self.assertEqual("root", inner[0]["id"])
                self.assertIn(f'window.__timelines["{cid}"] = tl;', text)
                self.assertIn("<template", text)
                self.assertLess(text.index("<template"), text.index("<style>"))

    def test_project_config_files(self):
        plan = fixture()
        files = generate(plan)["files"]
        self.assertEqual(
            {
                "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
                "media": {"autoProxy": True},
            },
            json.loads(files["hyperframes.json"]),
        )
        meta = json.loads(files["meta.json"])
        self.assertEqual(
            ("getbrolls-ia-editando-reels", plan["meta"]["tema"], plan["generated_at"]),
            (meta["id"], meta["name"], meta["createdAt"]),
        )
        package = json.loads(files["package.json"])
        self.assertEqual("getbrolls-ia-editando-reels-003", package["name"])
        self.assertTrue(package["private"])
        for script in package["scripts"].values():
            self.assertIn(f"hyperframes@{hf.HYPERFRAMES_VERSION}", script)
        self.assertEqual(
            "npx --yes hyperframes@0.8.73 render . -o ../../../renders/hyperframes-003.mp4",
            package["scripts"]["render"],
        )
        self.assertNotIn("devDependencies", package)


class MediaTests(unittest.TestCase):
    def test_media_rules_in_every_html(self):
        result = generate()
        dests = {m["dest"] for m in result["media"]}
        for path, text in result["files"].items():
            if not path.endswith(".html"):
                continue
            with self.subTest(path=path):
                page = Elements(text)
                self.assertNotIn("crossorigin", text)
                self.assertNotIn("../", text)
                self.assertIsNone(re.search(r"/Users/|/home/|[A-Za-z]:\\\\", text))
                for video in page.all("video"):
                    self.assertIn("muted", video)
                    self.assertIn("playsinline", video)
                for tag in ("video", "audio", "img"):
                    for attrs in page.all(tag):
                        self.assertIn(attrs["src"], dests)
                        self.assertFalse(attrs["src"].startswith("compositions/"))
                for _, attrs in page.timed():
                    float(attrs["data-start"])
        audio_ids = [a["id"] for a in Elements(result["files"]["index.html"]).all("audio")]
        self.assertEqual(len(audio_ids), len(set(audio_ids)))

    def test_subcomposition_times_are_local_and_media_basis_is_local(self):
        plan = fixture()
        files = generate(plan)["files"]
        for scene in plan["scenes"]:
            with self.subTest(scene=scene["id"]):
                page = Elements(files[f"compositions/scene-{scene['id']}.html"])
                for tag, attrs in page.timed():
                    if attrs.get("data-composition-id"):
                        continue
                    self.assertGreaterEqual(float(attrs["data-start"]), 0)
                    self.assertLess(float(attrs["data-start"]), scene["duration_s"])
                    if tag in ("video", "audio"):
                        self.assertEqual("local", attrs["data-hf-media-start-basis"])

    def test_requests_are_unique_under_assets_with_the_source_extension(self):
        plan = fixture()
        media = generate(plan)["media"]
        folded = [m["dest"].casefold() for m in media]
        self.assertEqual(len(folded), len(set(folded)))
        for request in media:
            row = plan["media"][request["media_id"]]
            self.assertTrue(row["available"], request)
            self.assertTrue(request["dest"].startswith("assets/"))
            self.assertEqual(row["ext"], PurePosixPath(request["dest"]).suffix)
            self.assertRegex(PurePosixPath(request["dest"]).stem, r"^[a-z0-9-]+$")
        self.assertIn({"media_id": "aroll:c01", "dest": "assets/aroll/c01.mov"}, media)
        # O 1º clipe (4,5 s) cobre a cena de 2,0 s: o segundo nem é pedido.
        self.assertIn({"media_id": "clip:pexels:1", "dest": "assets/clips/c02-main.mp4"}, media)
        self.assertNotIn("clip:pixabay:2", [m["media_id"] for m in media])

    def test_export_returns_sdk_types(self):
        result = hf.export(fixture(), {"args": {}})
        self.assertIs(type(result), ExportResult)
        self.assertTrue(all(type(m) is MediaRequest for m in result.media))
        self.assertEqual(generate()["files"], result.files)


class VoiceAndAudioTests(unittest.TestCase):
    def audio(self, plan=None):
        return {a["id"]: a for a in Elements(generate(plan)["files"]["index.html"]).all("audio")}

    def test_one_voice_per_scene_only_available_with_audio(self):
        audio = self.audio()
        voices = sorted(k for k in audio if k.startswith("voice-"))
        # c02/c07/c08 sem arquivo; c04-b sem trilha de áudio; c05/c06 sem fala.
        self.assertEqual(["voice-c01", "voice-c03", "voice-c04"], voices)
        self.assertEqual("assets/aroll/c04-a-t2.mov", audio["voice-c04"]["src"])
        self.assertEqual(
            ("0", "5.2", "2"), tuple(audio["voice-c01"][k] for k in ("data-start", "data-duration", "data-track-index"))
        )

    def test_split_with_two_presenters_shows_both_but_only_a_speaks(self):
        page = Elements(generate()["files"]["compositions/scene-c04.html"])
        self.assertEqual(["assets/aroll/c04-a-t2.mov", "assets/aroll/c04-b.mov"], [v["src"] for v in page.all("video")])

    def test_ducking_only_under_available_voice_and_starts_at_zero(self):
        plan = fixture()
        music = self.audio(plan)["music-1"]
        self.assertEqual("0.5", music["data-volume"])
        lane = json.loads(music["data-automation"])["lanes"][0]
        points = lane["points"]
        self.assertEqual(("volume", 0.0), (lane["target"], points[0]["t"]))
        start = float(music["data-start"])
        ducked = [start + p["t"] for p in points if p["v"] == hf.MUSIC_DUCK]
        voiced = [(s["start_s"], s["start_s"] + s["duration_s"]) for s in plan["scenes"] if s["id"] in ("c03", "c04")]
        for t in ducked:
            self.assertTrue(any(a - 0.001 <= t <= b + 0.001 for a, b in voiced), t)
        self.assertTrue(all(0 <= p["t"] <= float(music["data-duration"]) for p in points))
        self.assertLessEqual(len(points), 512)

    def test_ducking_merges_close_windows(self):
        points = hf.ducking(0.0, 10.0, [(1.0, 2.0), (2.3, 3.0)])
        self.assertEqual([0.5, 0.5, 0.125, 0.125, 0.5], [p["v"] for p in hf.ducking(0.0, 10.0, [(1.0, 3.0)])])
        self.assertEqual(points[0], {"t": 0.0, "v": 0.5})
        expected = [(0.0, 0.5), (0.85, 0.5), (1.0, 0.125), (3.0, 0.125), (3.4, 0.5)]
        self.assertEqual(expected, [(p["t"], p["v"]) for p in points])
        self.assertEqual(expected, [(p["t"], p["v"]) for p in hf.ducking(0.0, 10.0, [(1.0, 3.0)])])

    def test_sfx_on_alternating_tracks_and_pending_sfx_is_a_note(self):
        result = generate()
        audio = self.audio()
        self.assertEqual(("0.35", "4"), (audio["sfx-c01-1"]["data-volume"], audio["sfx-c01-1"]["data-track-index"]))
        self.assertIn('c05: SFX "pop" pendente', result["notes"])

    def test_no_voice_audio_when_the_voice_has_no_audio_track(self):
        plan = fixture()
        plan["media"]["aroll:c01"]["has_audio"] = False
        self.assertNotIn("voice-c01", self.audio(plan))


class LayoutTests(unittest.TestCase):
    def scene(self, scene_id, plan=None):
        return Elements(generate(plan)["files"][f"compositions/scene-{scene_id}.html"])

    def test_missing_presenter_card_says_what_to_record(self):
        self.assertEqual([], self.scene("c07").all("video"))
        text = generate()["files"]["compositions/scene-c07.html"]
        self.assertIn("GRAVAR aroll/c07.*", text)
        self.assertIn("moça no café", text)
        self.assertIn("Testei e amei.", text)

    def test_unreadable_presenter_card_names_the_file(self):
        plan = fixture()
        plan["media"]["aroll:c03"].update(available=False, problem="unreadable")
        self.assertIn("NÃO CONSEGUI LER aroll/c03.mp4", generate(plan)["files"]["compositions/scene-c03.html"])

    def test_short_presenter_leaves_a_card_and_a_note(self):
        plan = fixture()
        plan["media"]["aroll:c03"]["duration_s"] = 3.0
        result = generate(plan)
        page = Elements(result["files"]["compositions/scene-c03.html"])
        self.assertEqual("3", next(v for v in page.all("video") if "aroll" in v["src"])["data-duration"])
        self.assertIn("c03: lado b termina em 3,0 s de 4,0 s", result["notes"])

    def test_broll_chain_then_card_for_the_rest(self):
        plan = fixture()
        plan["media"]["clip:pexels:1"]["duration_s"] = 0.5
        plan["media"]["clip:pixabay:2"]["duration_s"] = 0.5
        result = generate(plan)
        page = Elements(result["files"]["compositions/scene-c02.html"])
        videos = page.all("video")
        self.assertEqual([("0", "0.5"), ("0.5", "0.5")], [(v["data-start"], v["data-duration"]) for v in videos])
        cards = [a for a in page.all("div") if "card" in a.get("class", "").split()]
        self.assertEqual(("1", "1"), (cards[0]["data-start"], cards[0]["data-duration"]))
        self.assertIn("c02: b-roll cobre 1,0 s de 2,0 s", result["notes"])

    def test_missing_broll_card_and_brief_hint(self):
        result = generate()
        self.assertIn("B-ROLL: tela do get-brolls", result["files"]["compositions/scene-c03.html"])
        self.assertIn("brief --beat c03-a --project", result["files"]["EXPORT.md"])

    def test_split_halves_by_aspect(self):
        page = self.scene("c03")
        slots = [a["style"] for a in page.all("div") if "slot" in a.get("class", "").split()]
        self.assertTrue(any("top:960px" in s and "height:960px" in s for s in slots), slots)
        plan = fixture()
        plan["meta"]["aspecto"] = "16:9"
        wide = [a["style"] for a in self.scene("c03", plan).all("div") if "slot" in a.get("class", "").split()]
        self.assertTrue(any("left:960px" in s and "width:960px" in s for s in wide), wide)

    def test_brand_contains_and_pending_brand_is_a_card(self):
        self.assertEqual("object-fit:contain;", self.scene("c05").all("img")[0]["style"])
        self.assertIn("MARCA: logo", generate()["files"]["compositions/scene-c08.html"])

    def test_lettering_window_and_style_class(self):
        plan = fixture()
        page = self.scene("c01", plan)
        lettering = next(a for a in page.all("div") if "lettering" in a.get("class", "").split())
        self.assertIn("lettering--destaque", lettering["class"])
        c01 = plan["scenes"][0]
        start = round(c01["layers"][0]["at_s"] - c01["start_s"], 3)
        self.assertEqual(
            (start, round(c01["duration_s"] - start, 3)),
            (float(lettering["data-start"]), float(lettering["data-duration"])),
        )
        self.assertIn("top:1410px", lettering["style"])
        late = self.scene("c08", plan)
        siga = next(a for a in late.all("div") if "lettering" in a.get("class", "").split())
        self.assertEqual(1.0, float(siga["data-duration"]))

    def test_cards_sit_in_the_upper_third(self):
        text = generate()["files"]["compositions/scene-c06.html"]
        self.assertIn("top:300px", text)  # centro 560 - metade do bloco (260)

    def test_extensions_only_from_this_plugin(self):
        result = generate()
        scene = result["files"]["compositions/scene-c02.html"]
        self.assertIn("hyperframes:zoom-in 1.2 em", scene)
        self.assertNotIn("brilho", scene)
        self.assertIn(
            "npx --yes hyperframes@0.8.73 add zoom-in --dir exports/hyperframes/003", result["files"]["EXPORT.md"]
        )

    def test_comp_is_a_root_comment_and_a_next_step(self):
        result = generate()
        page = Elements(result["files"]["index.html"])
        self.assertTrue(any('getbrolls COMP "grafico"' in c for c in page.comments))
        self.assertIn('c03: COMP "grafico"', result["files"]["EXPORT.md"])


class CaptionTests(unittest.TestCase):
    def transcript(self, plan=None):
        text = generate(plan)["files"]["compositions/captions.html"]
        found = re.search(r"const TRANSCRIPT = (\[.*?\]);\n", text)
        assert found is not None
        return json.loads(found.group(1).replace("<\\/", "</")), text

    def test_transcript_uses_sidecar_words_and_spreads_the_rest(self):
        plan = fixture()
        words, _ = self.transcript(plan)
        self.assertEqual(plan["scenes"][0]["words_timed"], words[:2])
        c02 = plan["scenes"][1]
        spread = [w for w in words if c02["start_s"] <= w["start"] < c02["start_s"] + c02["duration_s"]]
        self.assertEqual(c02["speech_clean"].split(), [w["text"] for w in spread])
        self.assertAlmostEqual(c02["start_s"] + c02["duration_s"], spread[-1]["end"], places=3)

    def test_card_windows_and_hard_kill(self):
        plan = fixture()
        _, text = self.transcript(plan)
        c06 = next(s for s in plan["scenes"] if s["id"] == "c06")
        self.assertIn(
            f"const CARD_WINDOWS = [[{c06['start_s']}, {round(c06['start_s'] + c06['duration_s'], 3)}]]", text
        )
        self.assertIn('tl.set(el, { opacity: 0, visibility: "hidden" }, group.end);', text)
        self.assertIn("fitTextFontSize", text)

    def test_transcript_const_only_in_captions(self):
        for path, text in generate()["files"].items():
            if path != "compositions/captions.html":
                self.assertNotIn("const TRANSCRIPT", text, path)

    def test_no_captions_when_legenda_is_false(self):
        plan = fixture()
        plan["meta"]["legenda"] = False
        files = generate(plan)["files"]
        self.assertNotIn("compositions/captions.html", files)
        self.assertNotIn("el-captions", files["index.html"])


class SafetyTests(unittest.TestCase):
    def test_untrusted_text_is_escaped_in_html_js_and_markdown(self):
        plan = fixture()
        plan["scenes"][0]["words_timed"] = None  # a fala com </script> vai para o TRANSCRIPT
        files = generate(plan)["files"]
        self.assertNotIn("<script>alert(1)", files["index.html"] + files["compositions/scene-c01.html"])
        self.assertIn("IA &lt;editando&gt; reels", files["index.html"])
        captions = files["compositions/captions.html"]
        self.assertEqual(1, captions.count("</script>"))  # só o fechamento do próprio bloco
        self.assertIn('"<\\/script>"', captions)
        self.assertIn("Gancho \\<script\\>alert\\(1\\)\\</script\\>", files["EXPORT.md"])
        self.assertIn("3x mais &lt;rápido&gt;", files["compositions/scene-c01.html"])
        self.assertEqual('"<\\/b> <\\!-- \\u2028"', hf.js("</b> <!-- \u2028"))

    def test_credits_are_markdown_inert(self):
        plan = fixture()
        plan["media"]["clip:pexels:1"]["credit"] = "Título <b>x</b> [l](http://e) www.site"
        text = generate(plan)["files"]["EXPORT.md"]
        self.assertIn("Título \\<b\\>x\\</b\\> \\[l\\]\\(http\\://e\\) www\\.site", text)
        self.assertNotIn("[l](http://e)", text)

    def test_no_randomness_or_clock_or_infinite_repeat(self):
        for path, text in generate()["files"].items():
            for forbidden in ("Math.random", "Date.now", "performance.now", "repeat: -1", "repeat:-1"):
                self.assertNotIn(forbidden, text, path)

    def test_pure_same_bytes_and_never_opens_files(self):
        plan = fixture()
        original = copy.deepcopy(plan)
        with mock.patch.object(builtins, "open", side_effect=AssertionError("o exporter abriu um arquivo")):
            first = hf.generate(plan)
            second = hf.generate(plan)
        self.assertEqual(first, second)
        self.assertEqual(original, plan)

    def test_notes_are_capped(self):
        plan = fixture()
        scene = plan["scenes"][4]
        scene["layers"] = [dict(scene["layers"][0], name=f"x{i}") for i in range(80)]
        self.assertLessEqual(len(generate(plan)["notes"]), 50)


class HostileTextTests(unittest.TestCase):
    """Texto do plano tentando sair do contexto: `<title>`, `<script>`, comentário, NUL e surrogates."""

    def test_title_cannot_open_a_script(self):
        plan = fixture()
        plan["meta"]["tema"] = "</title><script>alert(1)</script>"
        index = generate(plan)["files"]["index.html"]
        self.assertEqual(2, index.count("<script"))  # só o GSAP e a timeline da raiz
        self.assertIn("<title>&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;</title>", index)

    def test_transcript_string_survives_script_close_and_line_separators(self):
        plan = fixture()
        hostile = "a\u2028b\u2029c</script><!--<script>"
        plan["scenes"][0]["words_timed"] = [{"text": hostile, "start": 0.0, "end": 0.4}]
        captions = generate(plan)["files"]["compositions/captions.html"]
        self.assertEqual(1, captions.count("</script>"))
        self.assertEqual(0, captions.count("<!--"))
        self.assertNotIn("\u2028", captions)
        self.assertNotIn("\u2029", captions)
        found = re.search(r"const TRANSCRIPT = (\[.*?\]);\n", captions)
        assert found is not None
        words = json.loads(found.group(1).replace("<\\/", "</").replace("<\\!--", "<!--"))
        self.assertEqual(hostile, words[0]["text"])

    def test_comment_text_cannot_close_the_comment(self):
        plan = fixture()
        plan["scenes"][1]["extensions"][0]["name"] = "x-->y--!><b>"
        plan["scenes"][1]["extensions"][0]["args"] = ["---->"]
        plan["scenes"][2]["layers"][0]["name"] = 'g"--><script>'
        files = generate(plan)["files"]
        for path in ("compositions/scene-c02.html", "index.html"):
            with self.subTest(path=path):
                text = files[path]
                self.assertEqual(text.count("<!--"), text.count("-->"))
                self.assertNotIn("--!>", text)
                self.assertNotIn("<b>", text)
                for body in Elements(text).comments:
                    self.assertNotIn("<", body)
                    self.assertNotIn(">", body)
                    self.assertNotIn("--", body)
        self.assertEqual(2, files["index.html"].count("<script"))

    def test_lone_surrogates_and_nul_never_reach_the_output(self):
        plan = fixture()
        plan["meta"]["tema"] = "tema \ud83d\x00fim"
        plan["scenes"][0]["words_timed"] = [{"text": "oi\ud800", "start": 0.0, "end": 0.4}]
        plan["scenes"][1]["speech_clean"] = "fala \udfff solta"
        plan["scenes"][0]["layers"][0]["text"] = "letra \udc00"
        plan["media"]["clip:pexels:1"]["credit"] = "crédito \ud800"
        plan["warnings"].append("aviso \udbff")
        result = hf.export(plan, {"args": {}})
        for path, text in result.files.items():
            with self.subTest(path=path):
                text.encode("utf-8")
                self.assertNotIn("\x00", text)
        for note in result.notes:
            note.encode("utf-8")
        self.assertEqual("tema \ufffdfim", json.loads(result.files["meta.json"])["name"])
        self.assertIn('"oi\ufffd"', result.files["compositions/captions.html"])
        self.assertIn("letra \ufffd", result.files["compositions/scene-c01.html"])
        self.assertIn("crédito \ufffd", result.files["EXPORT.md"])
        self.assertIn("aviso \ufffd", result.files["EXPORT.md"])
        validate_export_result(result, "hyperframes")

    def test_result_passes_the_sdk_export_validator(self):
        for name in ("export_plan_full.json", "export_plan_min.json"):
            with self.subTest(fixture=name):
                result = hf.export(fixture(name), {"args": {}})
                checked = validate_export_result(result, "hyperframes")
                self.assertEqual(result.files, checked.files)
                self.assertEqual([(m.media_id, m.dest) for m in result.media], list(checked.media))
                self.assertEqual([], find_local_paths(result.files))


class DuckingTests(unittest.TestCase):
    """Música abaixa sob a voz e só volta depois dela; nunca termina subindo dentro de uma fala."""

    def pairs(self, *args):
        return [(p["t"], p["v"]) for p in hf.ducking(*args)]

    def test_voice_until_the_end_holds_the_duck(self):
        self.assertEqual(
            [(0.0, 0.5), (0.85, 0.5), (1.0, 0.125), (4.8, 0.125), (5.0, 0.125)], self.pairs(0.0, 5.0, [(1.0, 4.8)])
        )
        self.assertEqual([(0.0, 0.5), (0.85, 0.5), (1.0, 0.125), (5.0, 0.125)], self.pairs(0.0, 5.0, [(1.0, 5.0)]))
        self.assertEqual([(0.0, 0.5), (0.85, 0.5), (1.0, 0.125), (5.0, 0.125)], self.pairs(0.0, 5.0, [(1.0, 9.0)]))

    def test_voice_from_the_start_and_outside_the_music(self):
        self.assertEqual([(0.0, 0.125), (2.0, 0.125), (2.4, 0.5)], self.pairs(0.0, 5.0, [(0.0, 2.0)]))
        self.assertEqual([(0.0, 0.125), (2.0, 0.125), (2.4, 0.5)], self.pairs(3.0, 5.0, [(1.0, 5.0)]))
        self.assertEqual([(0.0, 0.5)], self.pairs(5.0, 5.0, [(1.0, 2.0), (10.0, 11.0)]))

    def test_close_windows_merge_inside_ducking_in_any_order(self):
        merged = [(0.0, 0.5), (0.85, 0.5), (1.0, 0.125), (3.0, 0.125), (3.4, 0.5)]
        self.assertEqual(merged, self.pairs(0.0, 10.0, [(2.3, 3.0), (1.0, 2.0)]))
        apart = [(0.0, 0.5), (0.85, 0.5), (1.0, 0.125), (2.0, 0.125), (2.4, 0.5), (2.85, 0.5), (3.0, 0.125)]
        self.assertEqual([*apart, (4.0, 0.125), (4.4, 0.5)], self.pairs(0.0, 10.0, [(1.0, 2.0), (3.0, 4.0)]))

    def test_full_fixture_music_lane(self):
        plan = fixture()
        audio = {a["id"]: a for a in Elements(generate(plan)["files"]["index.html"]).all("audio")}
        music = audio["music-1"]
        start = float(music["data-start"])
        points = [(p["t"], p["v"]) for p in json.loads(music["data-automation"])["lanes"][0]["points"]]
        # Música de 7,2 s a 22,1 s; voz em c03 + c04 (7,2–15,1 s, juntas): abaixa desde o início.
        self.assertEqual((7.2, "14.9"), (start, music["data-duration"]))
        self.assertEqual([(0.0, 0.125), (7.9, 0.125), (8.3, 0.5)], points)
        voiced = [(7.2, 15.1)]
        for t, v in points:
            if any(a < start + t < b for a, b in voiced):
                self.assertEqual(hf.MUSIC_DUCK, v, t)


class StackingTests(unittest.TestCase):
    def test_captions_sit_above_every_card_and_lettering(self):
        files = generate()["files"]
        scene = files["compositions/scene-c07.html"]
        top = max(z_index(scene, ".card"), z_index(scene, ".lettering"))
        self.assertGreater(z_index(files["index.html"], "#el-captions"), top)
        captions = files["compositions/captions.html"]
        self.assertGreater(z_index(captions, "#root"), top)
        self.assertGreater(z_index(captions, ".captions-group"), top)


class StructureTests(unittest.TestCase):
    def test_timed_media_never_has_a_timed_ancestor(self):
        for path, text in generate()["files"].items():
            if not path.endswith(".html"):
                continue
            with self.subTest(path=path):
                for tag, attrs, parents in Tree(text).nodes:
                    if tag not in ("video", "audio", "img") or "data-start" not in attrs:
                        continue
                    timed = [p for p in parents if "data-start" in p[1] and "data-composition-id" not in p[1]]
                    self.assertEqual([], timed, attrs.get("id"))

    def test_zoom_scales_an_inner_wrapper_so_split_halves_never_overlap(self):
        text = generate()["files"]["compositions/scene-c04.html"]  # dois apresentadores: as duas metades com vídeo
        self.assertRegex(text, r"\.slot \{[^}]*overflow: hidden")
        tree = Tree(text)
        slots = [(attrs, parents) for tag, attrs, parents in tree.nodes if "slot" in attrs.get("class", "").split()]
        self.assertEqual(
            ["left:0px;top:0px;width:1080px;height:960px;", "left:0px;top:960px;width:1080px;height:960px;"],
            sorted({attrs["style"] for attrs, _ in slots}),
        )
        targets = re.findall(r'tl\.fromTo\("#([^"]+)", \{ scale: 1 \}', text)
        slot_ids = {attrs["id"] for attrs, _ in slots}
        for target in targets:
            with self.subTest(target=target):
                self.assertNotIn(target, slot_ids)
                _, attrs, parents = tree.by_id(target)
                self.assertNotIn("data-start", attrs)
                if "slot-zoom" in attrs.get("class", ""):
                    self.assertIn(parents[-1][1]["id"], slot_ids)


class DestTests(unittest.TestCase):
    def test_folder_from_the_media_id_is_slugged(self):
        plan = fixture()
        plan["media"]["asset:../Év il:whoosh"] = plan["media"].pop("asset:sfx:whoosh")
        plan["scenes"][0]["layers"][1]["media_id"] = "asset:../Év il:whoosh"
        dests = {m["media_id"]: m["dest"] for m in generate(plan)["media"]}
        self.assertEqual("assets/ev-il/whoosh.wav", dests["asset:../Év il:whoosh"])


class PendingTests(unittest.TestCase):
    def pending(self, plan=None):
        text = generate(plan)["files"]["EXPORT.md"]
        return text[text.index("## Pendências") : text.index("## Créditos")]

    def test_core_warnings_are_not_repeated(self):
        pending = self.pending()
        for subject in ("c02: fala sem narração gravada", "c08: fala sem narração gravada", 'c05: SFX "pop" pendente'):
            with self.subTest(subject=subject):
                self.assertEqual(1, pending.count(subject), pending)

    def test_missing_presenter_is_not_called_missing_narration(self):
        pending = self.pending()
        self.assertNotIn("c07: fala sem narração", pending)
        self.assertIn("c07: A-ROLL não gravado", pending)
        plan = fixture()
        plan["warnings"] = []
        pending = self.pending(plan)
        self.assertNotIn("c07: fala sem narração", pending)
        self.assertIn("c07: A-ROLL pendente — GRAVAR aroll/c07.\\*", pending)

    def test_every_credit_is_escaped_exactly_once(self):
        # O plano traz `credit` cru de toda fonte (clipe, componente, loja de plugin): o exporter escapa tudo.
        plan = fixture()
        hostile = {
            "clip:pexels:1": "Clipe <b>x</b> [l](http://e) *a* www.site",
            "asset:sfx:whoosh": "Som <i>y</i> [m](http://f) _b_",
            "plugin:hyperframes:musica:lofi": "Licença informada pelo plugin hyperframes: bgm [x](http://a) `c`",
        }
        for media_id, credit in hostile.items():
            plan["media"][media_id]["credit"] = credit
        text = generate(plan)["files"]["EXPORT.md"]
        for media_id, credit in hostile.items():
            with self.subTest(media_id=media_id):
                self.assertEqual(1, text.count(hf.md(credit)))
                self.assertNotIn(credit, text)
        self.assertNotIn("\\\\", text)


@unittest.skipUnless(shutil.which("node"), "Node.js ausente: o agrupamento das legendas roda em JS")
class CaptionGroupTests(unittest.TestCase):
    """O agrupamento embutido em `captions.html`, rodado no Node: grupos de 2 a 5 palavras."""

    def groups(self, words, cards=()):
        script = (
            hf.CAPTION_GROUPING_JS
            + f"\nconsole.log(JSON.stringify(groupWords({json.dumps(words)}, {json.dumps(list(cards))})));"
        )
        done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60, check=True)
        return [g["text"].split(" ") for g in json.loads(done.stdout)]

    @staticmethod
    def words(*spec):
        return [{"text": text, "start": start, "end": round(start + 0.3, 3)} for text, start in spec]

    def test_the_grouping_in_captions_is_this_one(self):
        self.assertIn(hf.CAPTION_GROUPING_JS, generate()["files"]["compositions/captions.html"])

    def test_six_words_split_four_and_two_never_five_and_one(self):
        words = self.words(*[(f"w{i}", i * 0.3) for i in range(6)])
        self.assertEqual([4, 2], [len(g) for g in self.groups(words)])

    def test_a_short_pause_never_leaves_a_word_alone(self):
        words = self.words(("Oi", 0.0), ("tudo", 0.5), ("bem", 0.8), ("contigo", 1.1))
        self.assertEqual([["Oi", "tudo", "bem", "contigo"]], self.groups(words))

    def test_punctuation_ends_a_group_of_two_or_more(self):
        words = self.words(("Eu", 0.0), ("digo.", 0.3), ("Corta", 0.6), ("e", 0.9), ("entrega.", 1.2), ("Fim", 1.5))
        self.assertEqual([["Eu", "digo."], ["Corta", "e"], ["entrega.", "Fim"]], self.groups(words))

    def test_only_a_lone_final_word_stands_alone(self):
        self.assertEqual([["só"]], self.groups(self.words(("só", 0.0))))
        words = self.words(("a", 0.0), ("b", 0.3), ("c", 1.0), ("d", 3.0))
        self.assertEqual([["a", "b", "c"], ["d"]], self.groups(words, [[2.0, 2.9]]))

    def test_full_fixture_groups(self):
        plan = fixture()
        text = generate(plan)["files"]["compositions/captions.html"]
        sizes = [len(g) for g in self.groups(js_const(text, "TRANSCRIPT"), js_const(text, "CARD_WINDOWS"))]
        self.assertTrue(all(n in range(2, 6) for n in sizes[:-1]), sizes)
        self.assertIn(sizes[-1], range(1, 6))


class ExportDocTests(unittest.TestCase):
    def test_export_md_next_steps_from_the_project_root(self):
        plan = fixture()
        text = generate(plan)["files"]["EXPORT.md"]
        self.assertNotIn("cd ", text)
        for command in ("lint", "check", "preview"):
            self.assertIn(f"npx --yes hyperframes@0.8.73 {command} exports/hyperframes/003", text)
        self.assertIn('-o "$PWD/renders/hyperframes-003-rascunho.mp4"', text)
        self.assertIn("`exports/hyperframes/003`", text)
        for fixed in (
            "Render **fora** do export",
            "Legendas mais precisas",
            "**nunca** mexe nesta",
            "`assets/clips/` compartilha o arquivo com `brolls/clips/`",
            "Take contínuo",
        ):
            self.assertIn(fixed, text)

    def test_pinned_cli_is_the_tested_one_everywhere(self):
        # A versão fixada é a que rodou lint/check de verdade; os docs mandam rodar a mesma.
        self.assertEqual("0.8.73", hf.HYPERFRAMES_VERSION)
        guide = (ROOT / "references" / "roteiro.md").read_text(encoding="utf-8")
        pins = set(re.findall(r"hyperframes@([0-9.]+)", guide))
        self.assertEqual({hf.HYPERFRAMES_VERSION}, pins)

    def test_pending_lists_plan_warnings_and_narration(self):
        text = generate()["files"]["EXPORT.md"]
        self.assertIn("c02: fala sem narração gravada", text)
        self.assertIn("c04: duas vozes na mesma cena", text)

    def test_unplayable_audio_is_a_pending_item(self):
        plan = fixture()
        plan["media"]["asset:sfx:whoosh"]["ext"] = ".aiff"
        self.assertIn("converta para .wav ou .mp3", generate(plan)["files"]["EXPORT.md"])

    def test_min_fixture_generates_too(self):
        result = generate(fixture("export_plan_min.json"))
        self.assertEqual(
            {"index.html", "compositions/scene-c01.html", "compositions/scene-c02.html", "compositions/scene-c03.html",
             "compositions/captions.html", "hyperframes.json", "meta.json", "package.json", "EXPORT.md"},
            set(result["files"]),
        )  # fmt: skip


if __name__ == "__main__":
    unittest.main()
