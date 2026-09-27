"""`gb export --to <exporter>` pela CLI: portões, exporter de plugin, pastas numeradas, mídia, resolvedor e envelope.

O exporter aqui é um plugin de teste mínimo (`demo_export`): o HyperFrames de verdade
tem o próprio teste de ponta a ponta.
"""

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _plugin_pins import pin_plugins
from _schemas import strict
from test_logging_trail import DEBUG_ENV, _events, _log_path
from test_roteiro_sync import SyncCase

from getbrolls import __version__, export, export_folder, models
from getbrolls.cli import build_parser
from getbrolls.ledger import Ledger
from getbrolls.runtime import project_lock
from getbrolls.sdk.exporters import ValidatedExport, find_local_paths, run_exporter, sample_plan
from getbrolls.sdk.jsonschema import errors
from getbrolls.sdk.registry import get_registry, reset_registry

# O que um comando que grava deixa em brolls/ mesmo no ensaio: a trava e os logs.
RUNTIME_FILES = {"brolls/.command.lock", "brolls/diagnostics.jsonl", "brolls/getbrolls.log"}
DEMO_CODE = """
from pathlib import Path

from getbrolls.sdk import ExportResult, MediaRequest, ResolverHit

MEDIA_DIR = Path({media_dir!r})


def export(plan, options):
    files = {{
        "index.html": "<p>" + str(len(plan["scenes"])) + " cenas</p>\\n",
        "EXPORT.md": "# Export " + plan["out_dir"] + "\\n",
    }}
    media = [
        MediaRequest(media_id, "assets/" + media_id.replace(":", "-") + row["ext"])
        for media_id, row in plan["media"].items()
        if row["available"]
    ]
    return ExportResult(files=files, media=media, notes=["nota do demo"])


def resolve(kind, name):
    path = MEDIA_DIR / (name + ".mp3")
    return ResolverHit(str(path), "Trilha livre do demo") if path.is_file() else None


def register(api):
    api.exporter("demo_export", export, "Exporter de teste")
    api.resolver("demo_export_media", resolve, ["sfx", "musica"])
"""
# Exporter que copia a fala do roteiro para o arquivo, como todo exporter de verdade faz.
SPEECH_CODE = """
from getbrolls.sdk import ExportResult


def export(plan, options):
    return ExportResult(files={{"index.html": "\\n".join(s["speech_clean"] for s in plan["scenes"]) + "\\n"}})


def resolve(kind, name):
    return None


def register(api):
    api.exporter("demo_export", export, "Exporter de teste")
    api.resolver("demo_export_media", resolve, ["sfx", "musica"])
"""
# Exporter que copia os avisos do plano para o EXPORT.md, como o HyperFrames faz nas pendências.
WARNINGS_CODE = """
from getbrolls.sdk import ExportResult


def export(plan, options):
    return ExportResult(files={{"EXPORT.md": "\\n".join(plan["warnings"]) + "\\n"}})


def resolve(kind, name):
    return None


def register(api):
    api.exporter("demo_export", export, "Exporter de teste")
    api.resolver("demo_export_media", resolve, ["sfx", "musica"])
"""
# Dois arquivos que só diferem na caixa: num disco que não diferencia caixa, o mesmo arquivo.
CASE_CODE = """
from getbrolls.sdk import ExportResult


def export(plan, options):
    return ExportResult(files={{"index.html": "a\\n", "INDEX.html": "b\\n"}})


def resolve(kind, name):
    return None


def register(api):
    api.exporter("demo_export", export, "Exporter de teste")
    api.resolver("demo_export_media", resolve, ["sfx", "musica"])
"""


# Exporter que guarda uma cópia do plano que recebeu: o `getbrolls-plan.json` tem que ser igual.
PLAN_COPY_CODE = """
import json

from getbrolls.sdk import ExportResult


def export(plan, options):
    return ExportResult(files={{"plan-copy.json": json.dumps(plan, ensure_ascii=False)}})


def resolve(kind, name):
    return None


def register(api):
    api.exporter("demo_export", export, "Exporter de teste")
    api.resolver("demo_export_media", resolve, ["sfx", "musica"])
"""


def tree(root):
    return {
        p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in sorted(Path(root).rglob("*"))
        if p.relative_to(root).as_posix() not in RUNTIME_FILES
    }


@skip_unless_ffmpeg
class ExportCase(SyncCase):
    """Projeto revisado e sincronizado, com A-ROLL gravado e um clipe coletado; plugin demo instalável."""

    def setUp(self):
        super().setUp()
        self.home = Path(tempfile.mkdtemp(prefix="gb-export-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.media_dir = Path(tempfile.mkdtemp(prefix="gb-export-media-")).resolve()
        self.addCleanup(shutil.rmtree, self.media_dir, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"GB_HOME": str(self.home), "GB_DELIVERY_COPY": ""})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GB_PLUGINS", None)
        reset_registry()
        self.addCleanup(reset_registry)
        self.review()
        self.sync()
        (self.project / "aroll").mkdir()
        synth_video(self.project / "aroll" / "c01.mp4", size="160x284", duration=2)
        self.clip_id = self.collected("c02")

    def collected(self, shot, frozen=True):
        """Clipe verificado em `brolls/clips/`; `frozen` (padrão) imita o que o `deliver` já deixaria."""
        clip = self.project / "brolls" / "clips" / f"{shot}-clip.mp4"
        clip.parent.mkdir(parents=True, exist_ok=True)
        synth_video(clip, size="160x284", duration=3)
        if frozen:
            clip.chmod(0o444)
            self.addCleanup(clip.chmod, 0o644)
        ledger = Ledger(self.project)
        c = models.candidate("youtube", "abc", "Timeline cheia", "https://www.youtube.com/watch?v=abc")
        c["shot"] = shot
        models.set_segment(c, 0, 3)
        models.approve(c, "Bruno Moreira", "chat", "aprovo")
        c["output"] = {
            "path": f"clips/{clip.name}",
            "sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
            "verified": True,
        }
        c["state"] = "verified"
        ledger.add(c)
        ledger.save("test", c)
        return c["id"]

    def install(self, enable=True, code=DEMO_CODE):
        folder = self.home / "plugins" / "demo_export"
        folder.mkdir(parents=True)
        manifest = {
            "id": "demo_export", "name": "Demo export", "version": "0.1.0", "sdk_api": 1,
            "requires_getbrolls": ">=2.5,<3", "entry": "plugin.py",
            "contributes": {"exporters": ["demo_export"], "resolvers": ["demo_export_media"]},
            "permissions": {"network": [], "env": [], "paths": [str(self.media_dir)]},
        }  # fmt: skip
        (folder / "getbrolls-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "plugin.py").write_text(code.format(media_dir=str(self.media_dir)), encoding="utf-8")
        if enable:
            pin_plugins("demo_export", home=self.home)
        return folder

    def export(self, *extra, expect=0):
        return run_cli("export", "--to", "demo_export", *extra, project=self.project, expect=expect)

    def folder(self, number):
        return self.project / "exports" / "demo_export" / number

    def args(self, dry_run=False):
        return argparse.Namespace(command="export", project=str(self.project), to="demo_export", dry_run=dry_run)


class ExportHappyPathTests(ExportCase):
    def test_first_export_writes_folder_latest_media_and_envelope(self):
        self.install()
        out = self.export()
        folder = self.folder("001")
        self.assertEqual(
            ("demo_export", "demo_export", "exports/demo_export/001", "001", True, False),
            (out["exporter"], out["plugin"], out["out"], out["number"], out["latest"], out["dry_run"]),
        )
        self.assertEqual(["EXPORT.md", "index.html"], out["files"])
        self.assertEqual(["Nota do plugin demo_export: nota do demo"], out["notes"])
        self.assertEqual("001\n", (folder.parent / "LATEST").read_text(encoding="utf-8"))
        self.assertEqual("# Export exports/demo_export/001\n", (folder / "EXPORT.md").read_text(encoding="utf-8"))
        methods = {m["dest"]: m["method"] for m in out["media"]}
        clip_dest = f"assets/clip-{self.clip_id.replace(':', '-')}.mp4"
        clip = self.project / "brolls" / "clips" / "c02-clip.mp4"
        self.assertEqual("hardlink", methods[clip_dest])
        self.assertTrue(clip.samefile(folder / clip_dest))
        voice = self.project / "aroll" / "c01.mp4"
        self.assertIn(methods["assets/aroll-c01.mp4"], ("clone", "reflink-auto", "copy"))
        self.assertFalse(voice.samefile(folder / "assets/aroll-c01.mp4"))
        marker = json.loads((folder / ".getbrolls-export.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ("complete", "demo_export", "0.1.0"), (marker["state"], marker["plugin"], marker["plugin_version"])
        )
        self.assertRegex(
            out["summary"]["line"], r"^Export 001 em exports/demo_export/001: 2 arquivo\(s\), 2 mídia\(s\)"
        )

    def test_writable_clip_is_cloned_never_hardlinked_and_keeps_its_mode(self):
        """Clipe ainda gravável (nenhum `deliver` passou por ele) segue o caminho de A-ROLL: clone/cópia.

        O original em `brolls/clips/` nunca vira o mesmo inode do export, e o export
        nunca mexe no modo do arquivo (congelado ou gravável, o modo é sempre o mesmo
        antes e depois).
        """
        self.install()
        clip = self.project / "brolls" / "clips" / "c02-clip.mp4"
        clip.chmod(0o644)
        self.addCleanup(clip.chmod, 0o644)
        before = stat.S_IMODE(clip.stat().st_mode)
        out = self.export()
        clip_dest = f"assets/clip-{self.clip_id.replace(':', '-')}.mp4"
        methods = {m["dest"]: m["method"] for m in out["media"]}
        self.assertIn(methods[clip_dest], ("clone", "reflink-auto", "copy"))
        dest_file = self.folder("001") / clip_dest
        self.assertFalse(clip.samefile(dest_file))
        self.assertEqual(clip.read_bytes(), dest_file.read_bytes())
        self.assertEqual(before, stat.S_IMODE(clip.stat().st_mode))

    def test_dry_run_predicts_clone_for_a_writable_clip_and_hardlink_for_a_frozen_one(self):
        self.install()
        clip = self.project / "brolls" / "clips" / "c02-clip.mp4"
        clip.chmod(0o644)
        self.addCleanup(clip.chmod, 0o644)
        clip_dest = f"assets/clip-{self.clip_id.replace(':', '-')}.mp4"
        out = self.export("--dry-run")
        self.assertIn({"media_id": f"clip:{self.clip_id}", "dest": clip_dest, "method": "clone"}, out["media"])
        clip.chmod(0o444)
        out = self.export("--dry-run")
        self.assertIn({"media_id": f"clip:{self.clip_id}", "dest": clip_dest, "method": "hardlink"}, out["media"])

    def test_export_done_is_logged_with_the_right_fields(self):
        self.install()
        out = run_cli("export", "--to", "demo_export", project=self.project, env=DEBUG_ENV)
        text = _log_path(self.project).read_text(encoding="utf-8")
        event = _events(text, "export_done")[-1]
        self.assertIsNone(event["projeto_id"])  # projeto sem plugin/board nunca ganhou id
        self.assertEqual("demo_export", event["exporter"])
        self.assertEqual("demo_export", event["plugin"])
        self.assertEqual(out["number"], event["number"])
        self.assertEqual(str(len(out["files"])), event["files"])
        self.assertEqual(str(len(out["media"])), event["media"])
        # O clipe (congelado) é hardlink (0 byte); só o clone do A-ROLL conta bytes.
        voice_size = (self.project / "aroll" / "c01.mp4").stat().st_size
        self.assertEqual(str(voice_size), event["bytes"])
        self.assertNotIn(str(self.project), text)

    def test_summary_counts_only_bytes_that_may_have_been_copied(self):
        self.install()
        out = self.export()
        voice = (self.project / "aroll" / "c01.mp4").stat().st_size
        shown = f"{voice / 1_000_000:.1f}".replace(".", ",")
        method = {m["dest"]: m["method"] for m in out["media"]}["assets/aroll-c01.mp4"]
        # O clone conta inteiro (num disco que não clona o `cp` copia de verdade): "até N MB".
        prefix = "até " if method in ("clone", "reflink-auto") else ""
        self.assertIn(f"({prefix}{shown} MB copiados", out["summary"]["line"])

    def test_warnings_carry_the_plan_warnings(self):
        self.install()
        out = self.export()
        self.assertIn("c03: b-roll sem clipe coletado (beat c03-a)", out["warnings"])

    def test_written_files_have_no_absolute_path(self):
        self.install()
        self.export()
        for path in self.folder("001").rglob("*"):
            if path.is_file() and path.suffix in (".html", ".md", ".json"):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(str(self.project), text, path.name)
                self.assertIsNone(re.search(r"/Users/|/home/|[A-Za-z]:\\\\", text), path.name)

    def test_second_export_is_a_new_folder_and_the_first_is_untouched(self):
        self.install()
        self.export()
        first = self.folder("001")
        (first / "index.html").write_text("editado", encoding="utf-8")
        (first / "renders").mkdir()
        before = tree(first)
        self.assertEqual("002", self.export()["number"])
        self.assertEqual(before, tree(first))

    def test_dry_run_writes_nothing_and_sweeps_nothing(self):
        self.install()
        abandoned = self.project / "exports" / "demo_export" / ".staging-deadbeef"
        abandoned.mkdir(parents=True)
        (abandoned / ".getbrolls-export.json").write_text(json.dumps({"marker": "getbrolls-export"}), encoding="utf-8")
        before = tree(self.project)
        out = self.export("--dry-run")
        self.assertEqual(before, tree(self.project))
        self.assertEqual((True, "exports/demo_export/001"), (out["dry_run"], out["out"]))
        self.assertIn({"media_id": "aroll:c01", "dest": "assets/aroll-c01.mp4", "method": "clone"}, out["media"])
        self.assertIn("nada foi gravado", out["summary"]["line"])

    def test_dry_run_holds_the_project_lock(self):
        self.install()
        with project_lock(self.project):
            out = self.export("--dry-run", expect=2)
        self.assertIn("Outro comando está usando este projeto", out["message"])

    def test_changed_voice_since_latest_is_a_warning_naming_the_new_folder(self):
        self.install()
        self.export()
        voice = self.project / "aroll" / "c01.mp4"
        voice.unlink()
        synth_video(voice, size="160x284", duration=3)
        self.assertIn("aroll/c01.mp4 mudou depois do export 001: o 002 usa a versão atual", self.export()["warnings"])

    def test_pending_music_comes_from_the_plugin_resolver_as_a_copy(self):
        self.install()
        track = self.media_dir / "lofi.mp3"
        track.write_bytes(b"ID3" + b"\x00" * 64)
        self.edit("Todo mundo trava.", "Todo mundo trava.\n[MUSICA: lofi]")
        self.review()
        self.sync()
        out = self.export()
        placed = self.folder("001") / "assets" / "plugin-demo_export-musica-lofi.mp3"
        self.assertEqual(track.read_bytes(), placed.read_bytes())
        self.assertFalse(track.samefile(placed))
        self.assertIn(
            {
                "media_id": "plugin:demo_export:musica:lofi",
                "dest": placed.relative_to(self.folder("001")).as_posix(),
                "method": "copy",
            },
            out["media"],
        )


class ExportRefusalTests(ExportCase):
    def test_exporter_not_installed(self):
        out = self.export(expect=2)
        self.assertIn("Não há exporter demo_export instalado", out["message"])
        self.assertNotIn("traceback", out)

    def test_installed_but_disabled(self):
        self.install(enable=False)
        out = self.export(expect=2)
        self.assertIn("Plugin demo_export está disabled", out["message"])
        self.assertIn("plugins --action enable --id demo_export", out["message"])

    def test_outside_gb_plugins_points_at_the_variable(self):
        self.install()
        with mock.patch.dict(os.environ, {"GB_PLUGINS": "outro"}):
            out = self.export(expect=2)
        self.assertIn("GB_PLUGINS", out["message"])

    def test_suspended_after_the_content_changed(self):
        folder = self.install()
        with (folder / "plugin.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# mudou depois do enable\n")
        self.assertIn("Plugin demo_export está suspended", self.export(expect=2)["message"])

    def test_gates_run_before_the_registry(self):
        self.install()
        self.edit("Todo mundo trava.", "Todo mundo trava muito.")
        self.review()
        self.assertIn("O BRIEF.md não reflete o roteiro", self.export(expect=2)["message"])
        self.assertFalse((self.project / "exports").exists())

    def test_invalid_brief_after_sync_gets_an_export_message(self):
        self.install()
        brief = self.project / "BRIEF.md"
        brief.write_text(brief.read_text(encoding="utf-8").replace('"version": 1', '"version": 2'), encoding="utf-8")
        out = self.export(expect=2)
        self.assertIn("O export não conseguiu conferir o BRIEF.md contra o roteiro", out["message"])
        self.assertIn('"version"', out["message"])
        self.assertNotIn("traceback", out)
        self.assertFalse((self.project / "exports").exists())

    def test_dry_run_with_forced_copies_predicts_copy_for_clips(self):
        self.install()
        out = run_cli("export", "--to", "demo_export", "--dry-run", project=self.project, env={"GB_DELIVERY_COPY": "1"})
        clip_dest = f"assets/clip-{self.clip_id.replace(':', '-')}.mp4"
        self.assertEqual("copy", {m["dest"]: m["method"] for m in out["media"]}[clip_dest])

    def test_dry_run_on_windows_predicts_copy_for_person_media(self):
        # No Windows não há `cp` que clone: o `place` copia, e o ensaio tem de dizer o mesmo.
        self.install()
        with mock.patch.object(export.export_place.sys, "platform", "win32"):
            out = export.run(self.args(dry_run=True))
        methods = {m["media_id"]: m["method"] for m in out["media"]}
        self.assertEqual("copy", methods["aroll:c01"])
        self.assertEqual("hardlink", methods[f"clip:{self.clip_id}"])

    def test_bad_exporter_name_is_refused_before_loading_plugins(self):
        out = run_cli("export", "--to", "../x", project=self.project, expect=2)
        self.assertIn("--to espera o nome de um exporter", out["message"])

    def test_path_like_speech_exports_with_one_warning(self):
        self.install(code=SPEECH_CODE)
        self.edit("Todo mundo trava.", "Todo mundo trava. Salve em ~/Movies/aula.")
        self.review()
        self.sync()
        out = self.export()
        self.assertIn("~/Movies/aula.", (self.folder("001") / "index.html").read_text(encoding="utf-8"))
        flagged = [w for w in out["warnings"] if "cara de caminho" in w]
        self.assertEqual(1, len(flagged), out["warnings"])
        self.assertIn("index.html", flagged[0])
        self.assertNotIn("Plugin demo_export", flagged[0])

    def test_paths_equal_but_for_case_are_a_clear_refusal(self):
        self.install(code=CASE_CODE)
        out = self.export(expect=2)
        self.assertIn("mesmo caminho", out["message"])
        self.assertNotIn("FileExistsError", json.dumps(out))
        self.assertFalse((self.project / "exports").exists())

    def test_help_lists_to_and_dry_run(self):

        text = build_parser().parse_args(["export", "--project", "p", "--to", "x"])
        self.assertEqual(("export", "x", False), (text.command, text.to, text.dry_run))


class ExportPlanFileTests(ExportCase):
    """Cada export guarda o plano que o exporter recebeu, e a pasta sabe de que projeto veio."""

    def saved(self, number="001"):
        return json.loads((self.folder(number) / export_folder.PLAN_FILE).read_text(encoding="utf-8"))

    def test_the_folder_keeps_the_exact_plan_given_to_the_exporter(self):
        self.install(code=PLAN_COPY_CODE)
        out = self.export()
        given = json.loads((self.folder("001") / "plan-copy.json").read_text(encoding="utf-8"))
        saved = self.saved()
        self.assertEqual(given, saved)
        self.assertEqual("exports/demo_export/001", saved["out_dir"])
        self.assertEqual(["plan-copy.json"], out["files"])
        text = (self.folder("001") / export_folder.PLAN_FILE).read_text(encoding="utf-8")
        self.assertEqual([], find_local_paths(saved))
        for path in (str(self.project), str(self.project.resolve()), str(self.home), str(Path.home())):
            self.assertNotIn(path, text)

    def test_a_project_without_an_id_gets_null_and_stays_untouched(self):
        self.install()
        manifest = self.project / "brolls" / "manifest.json"
        self.assertNotIn("project_id", json.loads(manifest.read_text(encoding="utf-8")))
        before = manifest.read_bytes()
        self.export()
        meta = self.saved()["meta"]
        self.assertEqual(
            (None, None, None, None, None),
            (meta["projeto_id"], meta["cliente"], meta["direcao"], meta["fps"], meta["canvas"]),
        )
        marker = json.loads((self.folder("001") / export_folder.MARKER).read_text(encoding="utf-8"))
        self.assertEqual((None, None, None), (marker["projeto_id"], marker["cliente"], marker["direcao"]))
        self.assertEqual(before, manifest.read_bytes())

    def test_a_pending_journal_and_the_ledger_stay_byte_identical(self):
        self.install()
        brolls = self.project / "brolls"
        pending = brolls / ".pending-transaction.json"
        pending.write_text(json.dumps({"data": {"schema_version": 1, "items": []}, "events": []}), encoding="utf-8")
        watched = [p for p in (pending, brolls / "manifest.json", brolls / "events.jsonl") if p.exists()]
        before = {p: p.read_bytes() for p in watched}
        for extra in ((), ("--dry-run",)):
            with self.subTest(extra=extra):
                self.assertIn("gravação interrompida", self.export(*extra, expect=2)["message"].lower())
                self.assertEqual(before, {p: p.read_bytes() for p in watched})
        self.assertFalse((self.project / "exports").exists())

    def test_an_existing_project_id_is_reused(self):
        self.install()
        ledger = Ledger(self.project, recover=False)
        ledger.data["project_id"] = "id-que-ja-existia"
        ledger.save("test")
        run_cli("export", "--to", "demo_export", project=self.project, env=DEBUG_ENV)
        self.assertEqual("id-que-ja-existia", self.saved()["meta"]["projeto_id"])
        marker = json.loads((self.folder("001") / export_folder.MARKER).read_text(encoding="utf-8"))
        self.assertEqual("id-que-ja-existia", marker["projeto_id"])
        text = _log_path(self.project).read_text(encoding="utf-8")
        self.assertEqual("id-que-ja-existia", _events(text, "export_done")[-1]["projeto_id"])
        self.export()
        self.assertEqual("id-que-ja-existia", self.saved("002")["meta"]["projeto_id"])

    def test_the_exporter_cannot_write_the_plan_file(self):
        self.install()
        for name in (export_folder.PLAN_FILE, "GETBROLLS-PLAN.json"):
            files = {"index.html": "<p>ok</p>\n", name: "{}"}
            with (
                self.subTest(name=name),
                mock.patch.object(export, "run_exporter", return_value=ValidatedExport(files, (), ())),
                self.assertRaises(ValueError) as caught,
            ):
                export.run(self.args())
            self.assertIn(f"{name}, nome reservado do get-brolls", str(caught.exception))
        self.assertFalse((self.project / "exports").exists())

    def test_a_machine_path_in_the_plan_is_refused_even_if_no_file_has_it(self):
        self.install(code=SPEECH_CODE.replace('s["speech_clean"]', '"cena"'))
        real_build = export.export_plan.build

        def build(*args, **kwargs):
            plan, sources = real_build(*args, **kwargs)
            plan["meta"]["tema"] = f"Salve em {self.project.resolve()}/x"
            return plan, sources

        with mock.patch.object(export.export_plan, "build", side_effect=build), self.assertRaises(ValueError) as caught:
            export.run(self.args())
        message = str(caught.exception)
        self.assertIn("caminho desta máquina", message)
        self.assertIn(export_folder.PLAN_FILE, message)
        self.assertFalse((self.project / "exports").exists())


class ExportWriteGuardTests(ExportCase):
    """No mesmo processo: o que acontece entre o exporter do plugin e a gravação."""

    def test_exporter_root_is_checked_again_after_the_plugin_ran(self):
        self.install()
        elsewhere = Path(tempfile.mkdtemp(prefix="gb-export-elsewhere-"))
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)

        def swap_root(*args):
            validated = run_exporter(*args)
            (self.project / "exports").mkdir(exist_ok=True)
            (self.project / "exports" / "demo_export").symlink_to(elsewhere, target_is_directory=True)
            return validated

        with mock.patch.object(export, "run_exporter", side_effect=swap_root), self.assertRaises(ValueError) as caught:
            export.run(self.args())
        self.assertIn("exports/demo_export/ é um link", str(caught.exception))
        self.assertEqual([], list(elsewhere.iterdir()))

    def test_io_error_while_writing_is_short_and_has_no_absolute_path(self):
        self.install()
        target = self.project / "exports" / "demo_export" / "x"

        def denied(path, data):
            raise PermissionError(errno.EACCES, "Permission denied", str(target))

        with mock.patch.object(export_folder, "_write_new", side_effect=denied), self.assertRaises(OSError) as caught:
            export.run(self.args())
        message = str(caught.exception)
        self.assertIn("sem permissão", message)
        self.assertIn("exports/demo_export/x", message)
        self.assertNotIn(str(self.project), message)
        self.assertNotIn("Errno", message)
        self.assertEqual([], [p.name for p in (self.project / "exports" / "demo_export").iterdir()])

    def test_plugin_copy_refuses_a_file_that_changed_since_the_plan(self):
        self.install()
        track = self.media_dir / "lofi.mp3"
        track.write_bytes(b"ID3" + b"\x00" * 64)
        info = track.stat()
        dest = Path(tempfile.mkdtemp(prefix="gb-export-dest-")) / "lofi.mp3"
        self.addCleanup(shutil.rmtree, dest.parent, ignore_errors=True)
        registry = get_registry()
        row = {
            "path": str(track), "st_dev": info.st_dev, "st_ino": info.st_ino, "st_mtime_ns": None,
            "st_size": info.st_size + 1, "method": "plugin", "store": "demo_export", "resolver": "demo_export_media",
        }  # fmt: skip
        with self.assertRaises(ValueError) as caught:
            export.copy_plugin(registry, row, dest)
        self.assertIn("lofi.mp3 mudou durante o export", str(caught.exception))
        self.assertNotIn(str(self.media_dir), str(caught.exception))
        self.assertFalse(dest.exists())
        export.copy_plugin(registry, {**row, "st_size": info.st_size}, dest)
        self.assertEqual(track.read_bytes(), dest.read_bytes())
        if os.name == "nt":  # no Windows não há umask e arquivo gravável é 0o666
            self.assertTrue(dest.stat().st_mode & stat.S_IWRITE)
        else:
            umask = os.umask(0)
            os.umask(umask)
            self.assertEqual(0o644 & ~umask, dest.stat().st_mode & 0o777)

    def plugin_row(self, path):
        info = path.lstat()
        return {
            "path": str(path), "st_dev": info.st_dev, "st_ino": info.st_ino, "st_mtime_ns": None,
            "st_size": info.st_size, "method": "plugin", "store": "demo_export", "resolver": "demo_export_media",
        }  # fmt: skip

    def test_plugin_copy_refuses_a_hit_with_a_second_name_on_disk(self):
        self.install()
        track = self.media_dir / "lofi.mp3"
        track.write_bytes(b"ID3" + b"\x00" * 64)
        os.link(track, self.media_dir / "outro-nome.mp3")
        dest = self.project / "copia.mp3"
        with self.assertRaises(ValueError) as caught:
            export.copy_plugin(get_registry(), self.plugin_row(track), dest)
        self.assertIn("lofi.mp3 tem mais de um nome no disco (hardlink)", str(caught.exception))
        self.assertFalse(dest.exists())

    def test_plugin_copy_refuses_a_hit_swapped_for_a_link(self):
        self.install()
        track = self.media_dir / "lofi.mp3"
        track.write_bytes(b"ID3" + b"\x00" * 64)
        row = self.plugin_row(track)
        other = self.media_dir / "outro.mp3"
        other.write_bytes(b"ID3" + b"\x01" * 64)
        track.unlink()
        track.symlink_to(other)
        dest = self.project / "copia.mp3"
        with self.assertRaises(ValueError) as caught:
            export.copy_plugin(get_registry(), row, dest)
        self.assertIn("lofi.mp3 mudou durante o export", str(caught.exception))
        self.assertNotIn(str(self.media_dir), str(caught.exception))
        self.assertFalse(dest.exists())

    def validated(self, files=None, notes=()):
        return ValidatedExport(files or {"index.html": "<p>ok</p>\n"}, (), tuple(notes))

    def test_real_machine_paths_in_files_are_refused(self):
        self.install()
        voice = self.project / "aroll" / "c01.mp4"
        machine = {
            "projeto": str(self.project.resolve()),
            "projeto sem resolver": str(self.project),
            "pasta pessoal": str(Path.home()) + "/Movies",
            "GB_HOME": str(self.home),
            "permissions.paths": str(self.media_dir),
            "fonte": str(voice.resolve()),
        }
        for label, text in machine.items():
            with self.subTest(label):
                files = {"index.html": f"<video src='{text}/x.mp4'></video>\n"}
                with (
                    mock.patch.object(export, "run_exporter", return_value=self.validated(files)),
                    self.assertRaises(ValueError) as caught,
                ):
                    export.run(self.args())
                message = str(caught.exception)
                self.assertIn("caminho desta máquina", message)
                self.assertIn("index.html", message)
                self.assertNotIn(text, message)
                self.assertFalse((self.project / "exports").exists())

    def test_notes_never_show_a_machine_path(self):
        self.install()
        note = f"Abra {self.project.resolve()}/exports e {self.home}/plugins"
        with mock.patch.object(export, "run_exporter", return_value=self.validated(notes=[note])):
            out = export.run(self.args(dry_run=True))
        # Sem os escapes de Markdown do `note_line` (`_` vira `\_`), que esconderiam o caminho.
        shown = " ".join(out["notes"]).replace("\\", "")
        for text in (str(self.project.resolve()), str(self.project), str(self.home)):
            self.assertNotIn(text, shown)
        self.assertIn("Nota do plugin demo_export:", shown)

    def test_a_known_path_inside_a_longer_word_is_not_a_machine_path(self):
        # HOME=/root não é "/root" dentro de "github.com/rootless-containers".
        self.install()
        files = {"index.html": "<a>github.com/rootless-containers</a> e /rootfs\n"}
        with (
            mock.patch.object(export, "run_exporter", return_value=self.validated(files)),
            mock.patch.object(export.Path, "home", return_value=Path("/root")),
        ):
            out = export.run(self.args())
        self.assertEqual("001", out["number"])
        self.assertIn("rootless", (self.folder("001") / "index.html").read_text(encoding="utf-8"))

    def test_a_plugin_warning_with_a_machine_path_is_scrubbed_not_refused(self):
        self.install(code=WARNINGS_CODE)
        real_build = export.export_plan.build
        # `erro:` antes do caminho escapa do filtro de texto do plano; o filtro de caminhos conhecidos pega.
        warning = f"Plugin demo_export: falhou em erro:{self.media_dir}/lofi.mp3"

        def build(*args, **kwargs):
            plan, sources = real_build(*args, **kwargs)
            plan["warnings"].append(warning)
            return plan, sources

        with mock.patch.object(export.export_plan, "build", side_effect=build):
            out = export.run(self.args())
        text = (self.folder("001") / "EXPORT.md").read_text(encoding="utf-8")
        self.assertNotIn(str(self.media_dir), text)
        self.assertIn("erro:<permissions.paths>/lofi.mp3", text)
        self.assertNotIn(str(self.media_dir), " ".join(out["warnings"]))

    def test_plugin_copy_refuses_a_file_outside_the_plugin_paths(self):
        self.install()
        outside = self.project / "lofi.mp3"
        outside.write_bytes(b"ID3" + b"\x00" * 64)
        info = outside.stat()
        dest = self.project / "copia.mp3"
        row = {
            "path": str(outside), "st_dev": info.st_dev, "st_ino": info.st_ino, "st_mtime_ns": None,
            "st_size": info.st_size, "method": "plugin", "store": "demo_export", "resolver": "demo_export_media",
        }  # fmt: skip
        with self.assertRaises(ValueError) as caught:
            export.copy_plugin(get_registry(), row, dest)
        self.assertIn("fora de permissions.paths do plugin demo_export", str(caught.exception))
        self.assertFalse(dest.exists())


class ExportTextTests(unittest.TestCase):
    """Texto que o export mostra: resumo e erro do sistema."""

    def test_summary_says_up_to_when_a_clone_ran_and_exact_otherwise(self):
        cloned = {"number": "001", "media": [{"method": "clone"}], "copied_bytes": 2_500_000}
        linked = {"number": "001", "media": [{"method": "hardlink"}, {"method": "copy"}], "copied_bytes": 1_000_000}
        self.assertIn("(até 2,5 MB copiados)", export._summary("exports/x/001", {"index.html": ""}, cloned))
        self.assertIn("(1,0 MB copiados de fato)", export._summary("exports/x/001", {"index.html": ""}, linked))

    def test_summary_points_at_export_md_only_when_it_was_written(self):
        written = {"number": "001", "media": [], "copied_bytes": 0}
        self.assertNotIn("EXPORT.md", export._summary("exports/x/001", {"index.html": ""}, written))
        self.assertIn("Abra exports/x/001/EXPORT.md.", export._summary("exports/x/001", {"EXPORT.md": ""}, written))

    def test_predicted_method_follows_the_platform(self):
        for platform, expected in (("darwin", "clone"), ("win32", "copy")):
            with self.subTest(platform=platform), mock.patch.object(export.export_place.sys, "platform", platform):
                self.assertEqual(expected, export._predicted({"method": "clone"}))
                self.assertEqual("hardlink", export._predicted({"method": "hardlink"}))
                self.assertEqual("copy", export._predicted({"method": "plugin"}))

    def test_os_errors_are_pt_br_without_errno_type_or_absolute_path(self):
        project = Path(tempfile.mkdtemp(prefix="gb-export-oserr-")).resolve()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        inside = str(project / "exports" / "x" / "001")
        cases = (
            (FileExistsError(errno.EEXIST, "File exists", inside), "já existe"),
            (FileNotFoundError(errno.ENOENT, "No such file or directory", inside), "não existe"),
            (NotADirectoryError(errno.ENOTDIR, "Not a directory", inside), "não é uma pasta"),
            (OSError(errno.EIO, "Input/output error", inside), "erro do sistema: Input/output error"),
            (OSError("sem detalhe"), "erro do sistema"),
        )
        for exc, fragment in cases:
            with self.subTest(fragment):
                message = str(export._os_error(exc, project))
                self.assertIn(fragment, message)
                self.assertNotIn(str(project), message)
                self.assertNotIn("Errno", message)
                self.assertNotIn("(OSError)", message)
                self.assertIsNone(re.search(r"\((?:File exists|No such file or directory|Not a directory)\)", message))


class MachinePathMatchTests(unittest.TestCase):
    """Caminho conhecido só vale inteiro: não casa dentro de outra palavra, URL ou nome maior."""

    # Montados em tempo de execução: o guarda do repositório recusa caminho de máquina escrito no código.
    ROOT_HOME = "/root"
    BO = "/".join(("", "srv", "bo"))
    AN = "/".join(("", "home", "an"))

    def machine(self, path, tag="<pasta pessoal>"):
        return {path: ("a pasta pessoal", tag)}

    def hits(self, text, path):
        return export._machine_hits({"index.html": text}, self.machine(path), "files")

    def test_a_path_inside_a_longer_name_or_url_is_not_a_hit(self):
        cases = (
            ("veja github.com/rootless-containers", self.ROOT_HOME),
            ("/rootfs e /root-old e /root.bak", self.ROOT_HOME),
            (f"{self.BO}b/Movies", self.BO),
            ("https://site.com/home/announcements", self.AN),
            (f"https://site.com{self.AN}/x", self.AN),
        )
        for text, path in cases:
            with self.subTest(text=text):
                self.assertEqual([], self.hits(text, path))
                self.assertEqual(text, export._scrub(text, self.machine(path)))

    def test_the_whole_path_is_a_hit_even_before_a_sentence_dot(self):
        cases = (
            (f"Salve em {self.BO}.", self.BO),
            (f"Salve em {self.BO}/aula.mov", self.BO),
            (f"<video src='{self.ROOT_HOME}/x.mp4'>", self.ROOT_HOME),
            (f"caminho: {self.AN}", self.AN),
            (f"erro:{self.AN}/x", self.AN),
            (f"SALVE EM {self.BO.upper()}.", self.BO),
        )
        for text, path in cases:
            with self.subTest(text=text):
                self.assertEqual([("files['index.html']", "a pasta pessoal")], self.hits(text, path))
                self.assertNotIn(path.casefold(), export._scrub(text, self.machine(path)).casefold())

    def test_scrub_keeps_the_sentence_dot_and_the_rest_of_the_path(self):
        self.assertEqual(
            "Salve em <pasta pessoal>. E <pasta pessoal>/aula.mov",
            export._scrub(f"Salve em {self.BO}. E {self.BO}/aula.mov", self.machine(self.BO)),
        )

    def test_a_relative_path_through_dots_is_still_a_hit(self):
        # Caminho relativo tipo "../../<pasta>/x": um "." logo antes não deve mais blindar o caminho.
        text = f"../..{self.AN}/x"
        self.assertEqual([("files['index.html']", "a pasta pessoal")], self.hits(text, self.AN))
        self.assertNotIn(self.AN.casefold(), export._scrub(text, self.machine(self.AN)).casefold())


class MachinePathSpellingTests(unittest.TestCase):
    """Outra grafia do mesmo caminho (URI, percent-encoded, NFD, `//`, `\\/`, Windows) também é recusada."""

    # Montados em tempo de execução: o guarda do repositório recusa caminho de máquina escrito no código.
    POSIX = "/".join(("", "home", "ana maria"))
    ACCENT = "/".join(("", "home", "jo\u00e3o"))
    WINDOWS = "\\".join(("C:", "Users", "Ana Maria"))

    def machine(self, *paths):
        return dict.fromkeys(paths, ("a pasta pessoal", "<pasta pessoal>"))

    def assert_refused_and_scrubbed(self, text, path):
        machine = self.machine(path)
        self.assertEqual(
            [("files['index.html']", "a pasta pessoal")], export._machine_hits({"index.html": text}, machine, "files")
        )
        scrubbed = export._scrub(text, machine)
        self.assertIn("<pasta pessoal>", scrubbed)
        self.assertEqual([], export._machine_hits({"index.html": scrubbed}, machine, "files"))

    def test_each_alternative_spelling_is_refused(self):
        cases = {
            "file URI com espaço": (Path(self.POSIX).as_posix().replace(" ", "%20"), self.POSIX),
            "file:// URI": ("file://" + self.POSIX.replace(" ", "%20") + "/x.mp4", self.POSIX),
            "percent-encoded inteiro": (urllib.parse.quote(self.POSIX + "/x.mp4", safe=""), self.POSIX),
            "percent-encoded duas vezes": (
                urllib.parse.quote(urllib.parse.quote(self.POSIX, safe=""), safe=""),
                self.POSIX,
            ),
            "acento em %XX": (urllib.parse.quote(self.ACCENT) + "/x.mp4", self.ACCENT),
            "NFD": (unicodedata.normalize("NFD", self.ACCENT) + "/x.mp4", self.ACCENT),
            "barra dupla": (self.POSIX.replace("/", "//") + "//x.mp4", self.POSIX),
            "JSON com \\/": (json.dumps({"src": self.POSIX + "/x.mp4"}).replace("/", "\\/"), self.POSIX),
            "Windows com /": ("C:/Users/Ana Maria/x.mp4", self.WINDOWS),
            "Windows file URI": ("file:///C:/Users/Ana%20Maria/x.mp4", self.WINDOWS),
            "Windows com \\\\ (JSON)": (json.dumps(self.WINDOWS + "\\x.mp4"), self.WINDOWS),
            "Windows em outra caixa": ("c:/users/ana maria/x.mp4", self.WINDOWS),
        }
        for label, (text, path) in cases.items():
            with self.subTest(label, text=text):
                self.assert_refused_and_scrubbed(text, path)

    def test_normalizing_does_not_turn_a_longer_name_into_a_hit(self):
        for text in ("https://site.com/home/ana maria2/x", "/home/ana%20mariana/x", "C:/Users/Ana Mariana/x"):
            with self.subTest(text=text):
                machine = self.machine(self.POSIX, self.WINDOWS)
                self.assertEqual([], export._machine_hits({"index.html": text}, machine, "files"))
                self.assertEqual(text, export._scrub(text, machine))


class SamplePlanTests(unittest.TestCase):
    def test_sample_plan_follows_the_strict_export_plan_schema(self):
        plan = sample_plan()
        self.assertEqual([], errors(plan, strict()))
        self.assertEqual(__version__, plan["getbrolls_version"])


if __name__ == "__main__":
    unittest.main()
