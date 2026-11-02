"""O schema do candidato descreve o que os caminhos reais de comando gravam."""

import json
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _offline import OFFLINE_YTDLP
from _paths import ROOT

from getbrolls import brief as brief_module
from getbrolls import providers
from getbrolls.sdk import schemas
from getbrolls.sdk.jsonschema import errors

PEXELS_PAYLOAD = {
    "videos": [
        {
            "id": 101,
            "url": "https://www.pexels.com/video/101/",
            "image": "https://images.pexels.com/videos/101/poster.jpg",
            "duration": 12,
            "user": {"name": "Autor", "url": "https://www.pexels.com/@autor"},
            "video_files": [
                {"file_type": "video/mp4", "link": "https://videos.pexels.com/101.mp4", "width": 1920, "height": 1080}
            ],
        }
    ]
}
PIXABAY_PAYLOAD = {
    "hits": [
        {
            "id": 202,
            "pageURL": "https://pixabay.com/videos/id-202/",
            "tags": "mar, onda",
            "user": "autora",
            "duration": 8,
            "videos": {
                "large": {
                    "url": "https://cdn.pixabay.com/202.mp4",
                    "width": 1920,
                    "height": 1080,
                    "thumbnail": "https://cdn.pixabay.com/202.jpg",
                }
            },
        }
    ]
}


def assert_matches(test, item):
    problems = errors(item, schemas.load("candidate"))
    test.assertEqual([], problems, json.dumps(item, ensure_ascii=False)[:600])


class CandidateSchemaContractTests(unittest.TestCase):
    def test_schema_top_level_is_closed_and_has_ext(self):
        schema = schemas.load("candidate")
        self.assertIs(False, schema["additionalProperties"])
        self.assertEqual("object", schema["properties"]["ext"]["type"])

    def test_stock_rows_match(self):
        for item in providers._pexels_rows(PEXELS_PAYLOAD) + providers._pixabay_rows(PIXABAY_PAYLOAD):
            assert_matches(self, item)

    def test_url_resolution_matches(self):
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.instagram.com/reel/ABC123/",
            "https://www.tiktok.com/@perfil/video/123456",
        ):
            assert_matches(self, providers.resolve(url))

    def test_unknown_top_level_field_is_refused(self):
        item = providers.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        item["campo_solto"] = 1
        self.assertIn("$.campo_solto: campo não previsto no schema", errors(item, schemas.load("candidate")))

    @skip_unless_ffmpeg
    def test_full_local_lifecycle_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "original.mp4"
            synth_video(src, size="640x360", duration=3, rate=10)
            c = run_cli("resolve", "--file", src, "--shot", "abertura", project=root)
            base = ["--candidate", c["id"]]
            run_cli("preview", *base, "--start", 0.5, "--end", 1.5, "--narration", "Linha um", project=root)
            run_cli(
                "approve",
                *base,
                "--start",
                0.5,
                "--end",
                1.5,
                "--by",
                "Fixture",
                "--statement",
                "Aprovo.",
                project=root,
            )
            run_cli("permit", *base, "--evidence", "Vídeo sintético de teste", project=root)
            run_cli("fetch", *base, project=root)
            yt = run_cli(
                "resolve",
                "--url",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "--shot",
                "fecho",
                project=root,
                env=OFFLINE_YTDLP,
            )
            run_cli("reject", "--candidate", yt["id"], project=root)
            run_cli("deliver", project=root)
            manifest = json.loads((root / "brolls" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(2, len(manifest["items"]))
        for item in manifest["items"]:
            assert_matches(self, item)


class BriefSchemaContractTests(unittest.TestCase):
    def test_template_brief_matches(self):

        with tempfile.TemporaryDirectory() as tmp:
            raw = (ROOT / "docs" / "BRIEF.md").read_text(encoding="utf-8")
            (Path(tmp) / "BRIEF.md").write_text(raw, encoding="utf-8")
            data = brief_module.load_brief(tmp)
        self.assertEqual([], errors(data, schemas.load("brief")))

    def test_sources_are_names_not_a_closed_list(self):
        schema = schemas.load("brief")
        beat = schema["properties"]["beats"]["items"]
        for sources in (
            schema["properties"]["defaults"]["properties"]["allowed_sources"]["items"],
            beat["properties"]["allowed_sources"]["items"],
        ):
            self.assertNotIn("enum", sources)
            self.assertEqual("^[a-z][a-z0-9_]{1,31}$", sources["pattern"])

    def test_schema_is_closed_at_top_and_beat_and_reserves_ext(self):
        """O schema (documental) é fechado e reserva `ext`; o runtime não o aplica —
        veja `test_runtime_accepts_and_ignores_unknown_keys`."""
        schema = schemas.load("brief")
        self.assertIs(False, schema["additionalProperties"])
        beat = schema["properties"]["beats"]["items"]
        self.assertIs(False, beat["additionalProperties"])
        self.assertEqual("object", schema["properties"]["ext"]["type"])
        self.assertEqual("object", beat["properties"]["ext"]["type"])

    def test_runtime_accepts_and_ignores_unknown_keys(self):
        """O runtime é permissivo: chave desconhecida (e `ext`) no topo, em `video` ou
        num beat não recusa o BRIEF.md e não aparece no brief normalizado."""

        with tempfile.TemporaryDirectory() as tmp:
            raw = (ROOT / "docs" / "BRIEF.md").read_text(encoding="utf-8")
            (Path(tmp) / "BRIEF.md").write_text(raw, encoding="utf-8")
            data = brief_module.load_brief(tmp)
        baseline, _ = brief_module.validate_brief(json.loads(json.dumps(data)))
        data["ext"] = {"meu_plugin": {"x": 1}}
        data["campo_novo"] = "qualquer coisa"
        data["video"]["campo_novo"] = 1
        data["beats"][0]["ext"] = {"meu_plugin": {"y": 2}}
        data["beats"][0]["campo_novo"] = [1, 2]
        normalized, _ = brief_module.validate_brief(data)
        self.assertEqual(baseline, normalized)
        self.assertNotIn("ext", normalized)
        self.assertNotIn("ext", normalized["beats"][0]["resolved"])


if __name__ == "__main__":
    unittest.main()
