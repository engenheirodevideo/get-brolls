"""Jornada do roteiro à exportação: rules → roteiro → revisão humana → sync → coleta do b-roll → export.

O exporter é um plugin local mínimo (`demo_export`); o HyperFrames de verdade tem o próprio teste. Aqui vale o
caminho da pessoa e as recusas do meio dele: sem revisão humana, com o roteiro mudado depois dela, com o plugin
desligado. Cada recusa não grava nada em `exports/`, e repetir o comando depois do conserto funciona.
"""

import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _journeys import BRIEF, JourneyCase, brief_with, install_plugin, write_brief
from _media import skip_unless_ffmpeg, synth_video

CLIP = "demo:1:shot:c02"
ROTEIRO = (
    '---\ntype: roteiro\ngenero: reels\ntema: "mar"\n---\n\n'
    "## Gancho\n[A-ROLL]\nO mar estava calmo.\n\n"
    "## Virada\n[BROLL: mar calmo ao amanhecer]\nEntão o céu fechou.\n"
)
EXPORTER = """
from getbrolls.sdk import ExportResult, MediaRequest


def export(plan, options):
    speech = "\\n".join(scene["speech_clean"] for scene in plan["scenes"]) + "\\n"
    media = [
        MediaRequest(media_id, "assets/" + media_id.replace(":", "-") + row["ext"])
        for media_id, row in plan["media"].items()
        if row["available"]
    ]
    return ExportResult(files={"index.html": speech}, media=media, notes=["nota do demo"])


def resolve(kind, name):
    return None


def register(api):
    api.exporter("demo_export", export, "Exporter de teste")
    api.resolver("demo_export_media", resolve, ["sfx", "musica"])
"""
EXPORTER_MANIFEST = {
    "id": "demo_export",
    "name": "Demo export",
    "version": "0.1.0",
    "sdk_api": 1,
    "requires_getbrolls": ">=2.5,<3",
    "entry": "plugin.py",
    "contributes": {"exporters": ["demo_export"], "resolvers": ["demo_export_media"]},
    "permissions": {"network": [], "env": [], "paths": []},
}


@skip_unless_ffmpeg
class RoteiroToExportJourney(JourneyCase):
    def setUp(self):
        super().setUp()
        self.enable_demo()
        self.gb("init-rules", "--format", "reels")
        video = {**BRIEF["video"], "delivery": {"format": "reels", "duration_s": 45, "platform": None}}
        write_brief(self.project, brief_with(video=video, beats=[]))
        (self.project / "ROTEIRO.md").write_text(ROTEIRO, encoding="utf-8")

    def export(self, *extra, expect=0):
        return self.gb("export", "--to", "demo_export", *extra, expect=expect)

    def reviewed_and_synced(self):
        sha = self.gb("roteiro", "--action", "check")["review"]["sha256"]
        self.gb(
            "roteiro", "--action", "review", "--by", "Bruno", "--statement", "aprovado, pode seguir", "--expect", sha
        )
        return self.gb("roteiro", "--action", "sync")

    def install_exporter(self, enable=True):
        install_plugin(self.home, EXPORTER_MANIFEST, EXPORTER)
        if enable:
            self.gb("plugins", "--action", "enable", "--id", "demo_export", "--yes", project=False)

    def exports(self):
        folder = self.project / "exports"
        return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) if folder.exists() else []

    def test_the_reviewed_script_with_a_collected_clip_becomes_a_numbered_export(self):
        # Sem revisão humana nada anda: o export recusa e não cria a pasta.
        unreviewed = self.export(expect=1)
        self.assertEqual("INVALID_DATA", unreviewed["error_code"])
        self.assertIn("revisão humana", unreviewed["error"])
        self.assertEqual([], self.exports())
        # O sync também exige a revisão: recusa e não escreve nada no BRIEF.md.
        brief_before = (self.project / "BRIEF.md").read_bytes()
        no_sync = self.gb("roteiro", "--action", "sync", expect=1)
        self.assertIn("revisão humana", no_sync["error"])
        self.assertEqual(brief_before, (self.project / "BRIEF.md").read_bytes())
        synced = self.reviewed_and_synced()
        self.assertEqual(["c02"], synced["new"])
        self.assertEqual(["ROTEIRO.md", "BRIEF.md", "brolls/roteiro-state.json"], synced["written"])
        # Plugin ainda desligado: recusa, e o conserto é o que a mensagem manda fazer.
        self.install_exporter(enable=False)
        refused = self.export(expect=1)
        self.assertIn("disabled", refused["error"])
        self.assertIn("plugins --action enable --id demo_export", refused["error"])
        self.assertEqual([], self.exports())
        self.gb("plugins", "--action", "enable", "--id", "demo_export", "--yes", project=False)
        # Ensaio: mostra o que faria e não grava; o aviso diz que falta o clipe do b-roll.
        dry = self.export("--dry-run")
        self.assertTrue(dry["dry_run"])
        self.assertIn("c02: b-roll sem clipe coletado (beat c02)", dry["warnings"])
        self.assertEqual([], self.exports())
        # Coleta do b-roll do beat c02 pelo caminho comum e a gravação do A-ROLL.
        found = self.search("mar calmo", "c02")
        self.assertEqual(CLIP, found["items"][0]["id"])
        fetched = self.collect(CLIP, start=0, end=3)
        self.gb("deliver")
        (self.project / "aroll").mkdir()
        synth_video(self.project / "aroll" / "c01.mp4", size="160x284", duration=2)
        out = self.export()
        self.assertEqual(
            ("demo_export", "exports/demo_export/001", "001", True, False),
            (out["exporter"], out["out"], out["number"], out["latest"], out["dry_run"]),
        )
        self.assertEqual(["index.html"], out["files"])
        self.assertEqual(["Nota do plugin demo_export: nota do demo"], out["notes"])
        self.assertNotIn("c02: b-roll sem clipe coletado (beat c02)", out["warnings"])
        folder = self.project / out["out"]
        self.assertEqual(
            "O mar estava calmo.\nEntão o céu fechou.\n", (folder / "index.html").read_text(encoding="utf-8")
        )
        self.assertEqual("001\n", (folder.parent / "LATEST").read_text(encoding="utf-8"))
        clip = self.project / "brolls" / fetched["output"]["path"]
        copied = {row["dest"] for row in out["media"]}
        self.assertIn("assets/clip-demo-1-shot-c02.mp4", copied)
        self.assertEqual(clip.read_bytes(), (folder / "assets/clip-demo-1-shot-c02.mp4").read_bytes())
        self.assertIn("assets/aroll-c01.mp4", copied)
        # Nova exportação é nova pasta; a primeira fica como estava.
        first = (folder / "index.html").read_bytes()
        again = self.export()
        self.assertEqual("002", again["number"])
        self.assertEqual(first, (folder / "index.html").read_bytes())
        self.assertEqual("002\n", (folder.parent / "LATEST").read_text(encoding="utf-8"))

    def test_editing_the_script_after_the_review_blocks_export_until_it_is_reviewed_again(self):
        self.reviewed_and_synced()
        self.install_exporter()
        self.assertEqual("001", self.export()["number"])
        exported = self.exports()
        text = (self.project / "ROTEIRO.md").read_text(encoding="utf-8")
        (self.project / "ROTEIRO.md").write_text(text.replace("calmo.", "calmo demais."), encoding="utf-8")
        blocked = self.export(expect=1)
        self.assertIn("mudou depois dela", blocked["error"])
        self.assertEqual(exported, self.exports())
        self.reviewed_and_synced()
        second = self.export()
        self.assertEqual("002", second["number"])
        self.assertIn("calmo demais.", (self.project / second["out"] / "index.html").read_text(encoding="utf-8"))

    def test_an_exporter_that_was_never_installed_is_refused_by_name(self):
        self.reviewed_and_synced()
        error = self.export(expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("demo_export", error["error"])
        self.assertEqual([], self.exports())
        self.install_exporter()
        self.assertEqual("001", self.export()["number"])


if __name__ == "__main__":
    unittest.main()
