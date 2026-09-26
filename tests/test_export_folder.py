"""Pastas de export numeradas: nunca apaga nem mistura, `LATEST`, staging, varredura e corrida."""

import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import export_folder, export_place

BASE = {
    "export_version": 1, "exporter": "hyperframes", "plugin": "hyperframes", "plugin_version": "0.1.0",
    "getbrolls_version": "2.5.0", "created": "2026-09-25T18:00:00Z",
}  # fmt: skip
FILES = {
    "index.html": "<html></html>\n",
    "compositions/scene-c01.html": "<template></template>\n",
    "EXPORT.md": "# ok\n",
}


def tree(root):
    """{caminho relativo: bytes} de tudo abaixo de `root` (pastas vazias viram entrada vazia)."""
    found = {}
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root).as_posix()
        found[rel] = path.read_bytes() if path.is_file() else None
    return found


class FolderTestCase(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-export-folder-")).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"GB_DELIVERY_COPY": ""})
        env.start()
        self.addCleanup(env.stop)
        self.clip = self.project / "brolls" / "clips" / "a.mp4"
        self.clip.parent.mkdir(parents=True)
        self.clip.write_bytes(b"clipe")
        self.clip.chmod(0o444)
        self.addCleanup(self.clip.chmod, 0o644)

    def root(self):
        return export_folder.exporter_root(self.project, "hyperframes")

    def source(self, path, method="hardlink"):
        info = Path(path).lstat()
        return {
            "path": str(path), "st_dev": info.st_dev, "st_ino": info.st_ino, "st_mtime_ns": info.st_mtime_ns,
            "st_size": info.st_size, "method": method,
        }  # fmt: skip

    def export(self, files=None, placements=None, place=export_place.place):
        root = self.root()
        number = export_folder.next_number(root)
        if placements is None:
            placements = [("clip:p:1", "assets/clips/c02-main.mp4", self.source(self.clip))]
        content = {"files": FILES if files is None else files, "placements": placements}
        return export_folder.write_export(root, number, content, BASE, place)


class NumberingTests(FolderTestCase):
    def test_first_export_is_001_with_latest_and_marker(self):
        result = self.export()
        folder = self.project / "exports" / "hyperframes" / "001"
        self.assertEqual("001", result["number"])
        self.assertTrue(result["latest"])
        self.assertEqual("001\n", (folder.parent / "LATEST").read_text(encoding="utf-8"))
        self.assertEqual("<html></html>\n", (folder / "index.html").read_text(encoding="utf-8"))
        marker = json.loads((folder / export_folder.MARKER).read_text(encoding="utf-8"))
        self.assertEqual(("getbrolls-export", "complete", "001"), (marker["marker"], marker["state"], marker["number"]))
        row = marker["media"]["assets/clips/c02-main.mp4"]
        self.assertEqual(
            ("clip:p:1", "hardlink", self.clip.stat().st_ino), (row["media_id"], row["method"], row["source_ino"])
        )
        self.assertEqual(
            [{"media_id": "clip:p:1", "dest": "assets/clips/c02-main.mp4", "method": "hardlink"}], result["media"]
        )
        self.assertEqual(0, result["copied_bytes"])
        self.assertEqual([], [p.name for p in folder.parent.iterdir() if p.name.startswith(".staging-")])

    def test_second_export_never_touches_the_first_even_after_edits(self):
        self.export()
        first = self.project / "exports" / "hyperframes" / "001"
        (first / "renders").mkdir()
        (first / "renders" / "draft.mp4").write_bytes(b"render")
        (first / "transcript.json").write_text("[]", encoding="utf-8")
        (first / ".thumbnails").mkdir()
        (first / ".DS_Store").write_bytes(b"\x00")
        (first / "compositions" / "scene-c01.html").write_text("editado à mão", encoding="utf-8")
        before = tree(first)
        result = self.export()
        self.assertEqual("002", result["number"])
        self.assertEqual(before, tree(first))
        self.assertEqual("002\n", (first.parent / "LATEST").read_text(encoding="utf-8"))

    def test_numbers_are_monotonic_even_after_deleting_a_folder(self):
        self.export()
        self.export()
        shutil.rmtree(self.project / "exports" / "hyperframes" / "002")
        self.assertEqual("003", self.export()["number"])

    def test_latest_that_is_garbage_or_a_folder_is_ignored_with_a_warning(self):
        self.export()
        latest = self.project / "exports" / "hyperframes" / "LATEST"
        latest.write_text("lixo\n", encoding="utf-8")
        self.assertEqual("002", self.export()["number"])
        latest.unlink()
        latest.mkdir()
        result = self.export()
        self.assertEqual("003", result["number"])
        self.assertIn("exports/hyperframes/LATEST não é um arquivo: não mexi nele", result["warnings"])
        self.assertTrue(latest.is_dir())

    def test_folder_created_in_the_middle_moves_to_the_next_number(self):
        root = self.root()
        number = export_folder.next_number(root)
        (root / "001").mkdir(parents=True)
        (root / "001" / "da-pessoa.txt").write_text("minha", encoding="utf-8")
        content = {"files": FILES, "placements": []}
        result = export_folder.write_export(root, number, content, BASE, export_place.place)
        self.assertEqual("002", result["number"])
        self.assertEqual("minha", (root / "001" / "da-pessoa.txt").read_text(encoding="utf-8"))
        self.assertIn("troque 001 por 002", result["warnings"][0])
        marker = json.loads((root / "002" / export_folder.MARKER).read_text(encoding="utf-8"))
        self.assertEqual("002", marker["number"])

    def test_five_taken_numbers_refuse_and_leave_nothing(self):
        root = self.root()
        for n in range(1, 6):
            (root / f"{n:03d}").mkdir(parents=True)
        content = {"files": FILES, "placements": []}
        with self.assertRaisesRegex(ValueError, "5 números seguidos"):
            export_folder.write_export(root, 1, content, BASE, export_place.place)
        self.assertEqual([f"{n:03d}" for n in range(1, 6)], sorted(p.name for p in root.iterdir()))


class FailureAndSweepTests(FolderTestCase):
    def test_failure_in_the_middle_removes_staging_and_creates_nothing(self):
        def broken(source, dest):
            raise ValueError("a.mp4 mudou durante o export: repita.")

        with self.assertRaisesRegex(ValueError, "mudou durante o export"):
            self.export(place=broken)
        root = self.project / "exports" / "hyperframes"
        self.assertEqual([], sorted(p.name for p in root.iterdir()))
        self.assertEqual(0o444, stat.S_IMODE(self.clip.stat().st_mode))

    def test_frozen_hardlink_that_resists_unlink_is_refrozen_at_the_source(self):
        real_unlink = Path.unlink
        state = {"raised": False}

        def stubborn(path, missing_ok=False):
            if path.name == "c02-main.mp4" and not state["raised"]:
                state["raised"] = True
                raise PermissionError("somente-leitura (Windows)")
            return real_unlink(path, missing_ok=missing_ok)

        def place_then_fail(source, dest):
            export_place.place(source, dest)
            raise ValueError("falhou depois de ligar o clipe")

        with (
            mock.patch.object(Path, "unlink", autospec=True, side_effect=stubborn),
            mock.patch.object(export_folder.delivery, "_freeze", wraps=export_folder.delivery._freeze) as freeze,
            self.assertRaises(ValueError),
        ):
            self.export(place=place_then_fail)
        freeze.assert_called_once_with(str(self.clip), "hardlink")
        self.assertTrue(self.clip.exists())
        self.assertEqual(0o444, stat.S_IMODE(self.clip.stat().st_mode))

    def test_sweep_never_thaws_an_unregistered_hardlink_to_a_file_outside(self):
        # Staging plantado com marcador válido e um hardlink para um arquivo somente-leitura alheio:
        # no Windows o `unlink` levanta `PermissionError`, e degelar tiraria a proteção do arquivo de fora.
        outside = self.project / "alheio.txt"
        outside.write_text("de outra pessoa", encoding="utf-8")
        outside.chmod(0o444)
        self.addCleanup(outside.chmod, 0o644)
        planted = self.root() / ".staging-cccc3333"
        planted.mkdir(parents=True)
        (planted / export_folder.MARKER).write_text(
            json.dumps({"marker": "getbrolls-export", "state": "staging"}), encoding="utf-8"
        )
        os.link(outside, planted / "alheio.txt")
        real_unlink = Path.unlink

        def windows_like(path, missing_ok=False):
            if path.name == "alheio.txt" and path.parent == planted:
                raise PermissionError("somente-leitura (Windows)")
            return real_unlink(path, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", autospec=True, side_effect=windows_like):
            result = self.export()
        self.assertEqual(0o444, stat.S_IMODE(outside.stat().st_mode))
        self.assertTrue((planted / "alheio.txt").exists())
        self.assertTrue(any(".staging-cccc3333 é um export abandonado" in w for w in result["warnings"]))

    def test_sweep_removes_only_staging_with_the_core_marker(self):
        root = self.root()
        ours = root / ".staging-aaaa1111"
        ours.mkdir(parents=True)
        (ours / export_folder.MARKER).write_text(
            json.dumps({"marker": "getbrolls-export", "state": "staging"}), encoding="utf-8"
        )
        (ours / "index.html").write_text("x", encoding="utf-8")
        theirs = root / ".staging-bbbb2222"
        theirs.mkdir()
        (theirs / "meu.txt").write_text("meu", encoding="utf-8")
        result = self.export()
        self.assertFalse(ours.exists())
        self.assertEqual("meu", (theirs / "meu.txt").read_text(encoding="utf-8"))
        self.assertIn(
            "exports/hyperframes/.staging-bbbb2222 não tem o marcador do get-brolls: "
            "ficou onde está (apague se for seu)",
            result["warnings"],
        )

    def test_paths_outside_the_folder_are_refused(self):
        for bad in ("../fora.html", "/abs.html", "a\\b.html"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Caminho de export inválido"):
                self.export(files={bad: "x"}, placements=[])

    def test_lone_surrogate_in_text_is_refused_with_a_clear_message(self):
        # Transcrição quebrada pode trazer um surrogate solto ("\ud800"): nunca UnicodeEncodeError.
        files = {**FILES, "compositions/scene-c01.html": "fala \ud800 cortada"}
        with self.assertRaisesRegex(ValueError, r"compositions/scene-c01\.html.*UTF-8"):
            self.export(files=files, placements=[])
        self.assertEqual([], sorted(p.name for p in self.root().iterdir()))

    def test_lone_surrogate_in_a_path_or_in_the_marker_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Caminho de export inválido"):
            self.export(files={"cena-\ud800.html": "x"}, placements=[])
        placements = [("clip:\udcff", "assets/clips/c02-main.mp4", self.source(self.clip))]
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            self.export(placements=placements)
        self.assertEqual([], sorted(p.name for p in self.root().iterdir()))
        self.assertEqual(0o444, stat.S_IMODE(self.clip.stat().st_mode))

    def test_links_and_files_in_place_of_the_export_folders_are_refused(self):
        exports = self.project / "exports"
        exports.mkdir()
        (exports / "hyperframes").write_text("não sou pasta", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exports/hyperframes/ é um arquivo"):
            self.root()
        shutil.rmtree(exports)
        elsewhere = self.project / "outro"
        elsewhere.mkdir()
        try:
            exports.symlink_to(elsewhere)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        with self.assertRaisesRegex(ValueError, "exports/ é um link"):
            self.root()


class ChangedSourceTests(FolderTestCase):
    def test_source_changed_since_latest_is_a_warning(self):
        voice = self.project / "aroll" / "c01.mov"
        voice.parent.mkdir()
        voice.write_bytes(b"voz")
        source = self.source(voice, "clone")
        self.export(placements=[("aroll:c01", "assets/aroll/c01.mov", source)])
        marker = export_folder.latest_marker(self.root())
        self.assertEqual([], export_folder.changed_sources(marker, {"aroll:c01": source}, self.project))
        voice.write_bytes(b"voz gravada de novo")
        warnings = export_folder.changed_sources(marker, {"aroll:c01": self.source(voice, "clone")}, self.project)
        self.assertEqual(["aroll/c01.mov mudou depois do export 001: este export usa a versão atual"], warnings)

    def test_no_latest_no_warning(self):
        self.assertIsNone(export_folder.latest_marker(self.root()))
        self.assertEqual([], export_folder.changed_sources(None, {}, self.project))

    def test_warning_names_the_new_folder_number_when_given(self):
        voice = self.project / "aroll" / "c01.mov"
        voice.parent.mkdir()
        voice.write_bytes(b"voz")
        self.export(placements=[("aroll:c01", "assets/aroll/c01.mov", self.source(voice, "clone"))])
        marker = export_folder.latest_marker(self.root())
        voice.write_bytes(b"voz gravada de novo")
        sources = {"aroll:c01": self.source(voice, "clone")}
        warnings = export_folder.changed_sources(marker, sources, self.project, number=3)
        self.assertEqual(["aroll/c01.mov mudou depois do export 001: o 003 usa a versão atual"], warnings)


def staging_of(path):
    """A pasta `.staging-*` que contém `path`."""
    return next(p for p in Path(path).parents if p.name.startswith(export_folder.STAGING_PREFIX))


def marked_staging(root, name=".staging-aaaa1111", media=None):
    staging = root / name
    staging.mkdir(parents=True)
    body = {"marker": "getbrolls-export", "state": "staging", "media": media or {}}
    (staging / export_folder.MARKER).write_text(json.dumps(body), encoding="utf-8")
    return staging


class CrashSafeRemovalTests(FolderTestCase):
    def locked_subfolder(self, staging):
        """Subpasta somente-leitura com um arquivo dentro: apagar esse arquivo falha no POSIX."""
        if os.name == "nt":
            self.skipTest("no Windows, pasta somente-leitura não impede apagar o arquivo de dentro")
        locked = staging / "compositions"
        locked.mkdir()
        (locked / "scene.html").write_text("x", encoding="utf-8")
        locked.chmod(0o555)
        self.addCleanup(locked.chmod, 0o755)
        return locked

    def test_sweep_failure_is_a_relative_warning_and_the_export_goes_on(self):
        root = self.root()
        staging = marked_staging(root)
        self.locked_subfolder(staging)
        result = self.export()
        self.assertEqual("001", result["number"])
        warning = next(w for w in result["warnings"] if ".staging-aaaa1111" in w)
        self.assertTrue(warning.startswith("exports/hyperframes/.staging-aaaa1111 "), warning)
        self.assertIn("apague à mão", warning)
        self.assertNotIn(str(self.project), " ".join(result["warnings"]))
        self.assertTrue((staging / export_folder.MARKER).is_file())

    def test_marker_is_removed_last_so_a_failed_removal_stays_marked(self):
        staging = marked_staging(self.root())
        self.locked_subfolder(staging)
        with self.assertRaises(OSError):
            export_folder.remove_staging(staging)
        self.assertTrue((staging / export_folder.MARKER).is_file())

    def test_numbered_folders_are_never_swept_even_with_a_staging_marker(self):
        root = self.root()
        numbered = marked_staging(root, name="002")
        (numbered / "index.html").write_text("da pessoa", encoding="utf-8")
        before = tree(numbered)
        self.assertEqual("003", self.export()["number"])
        self.assertEqual(before, tree(numbered))

    def test_stray_latest_temp_files_are_swept(self):
        root = self.root()
        root.mkdir(parents=True)
        stray = root / "LATEST.tmp-abcd1234"
        stray.write_text("009\n", encoding="utf-8")
        other = root / "LATEST.tmp-meu"
        other.write_text("meu", encoding="utf-8")
        self.export()
        self.assertFalse(stray.exists())
        self.assertTrue(other.exists())

    def test_thaw_that_still_fails_refreezes_the_source(self):
        def place_then_fail(source, dest):
            export_place.place(source, dest)
            self.clip.chmod(0o644)  # o que o `_thaw_unlink` faz no Windows antes de falhar de novo
            raise ValueError("falhou depois de ligar o clipe")

        real_unlink = Path.unlink

        def locked(path, missing_ok=False):
            if path.name == "c02-main.mp4":
                raise PermissionError("em uso (Windows)")
            return real_unlink(path, missing_ok=missing_ok)

        with (
            mock.patch.object(Path, "unlink", autospec=True, side_effect=locked),
            mock.patch.object(export_folder.delivery, "_thaw_unlink", side_effect=PermissionError("em uso")),
            self.assertRaisesRegex(ValueError, "falhou depois de ligar o clipe"),
        ):
            self.export(place=place_then_fail)
        self.assertEqual(0o444, stat.S_IMODE(self.clip.stat().st_mode))

    def test_staging_marker_records_the_relative_source_before_placing(self):
        seen = {}

        def spy(source, dest):
            marker = json.loads((staging_of(dest) / export_folder.MARKER).read_text(encoding="utf-8"))
            seen.update(marker)
            return export_place.place(source, dest)

        self.export(place=spy)
        self.assertEqual(("getbrolls-export", "staging"), (seen["marker"], seen["state"]))
        row = seen["media"]["assets/clips/c02-main.mp4"]
        self.assertEqual(("clip:p:1", "brolls/clips/a.mp4"), (row["media_id"], row["refreeze_source"]))

    def test_sweep_refreezes_the_source_recorded_in_the_staging_marker(self):
        media = {"assets/clips/c02-main.mp4": {"media_id": "clip:p:1", "refreeze_source": "brolls/clips/a.mp4"}}
        staging = marked_staging(self.root(), media=media)
        (staging / "assets" / "clips").mkdir(parents=True)
        os.link(self.clip, staging / "assets" / "clips" / "c02-main.mp4")
        real_unlink = Path.unlink
        state = {"raised": False}

        def stubborn(path, missing_ok=False):
            if path.name == "c02-main.mp4" and not state["raised"]:
                state["raised"] = True
                raise PermissionError("somente-leitura (Windows)")
            return real_unlink(path, missing_ok=missing_ok)

        with (
            mock.patch.object(Path, "unlink", autospec=True, side_effect=stubborn),
            mock.patch.object(export_folder.delivery, "_freeze", wraps=export_folder.delivery._freeze) as freeze,
        ):
            self.assertEqual([], export_folder.sweep_abandoned(self.root()))
        freeze.assert_called_once_with(str(self.clip), "hardlink")
        self.assertFalse(staging.exists())
        self.assertEqual(0o444, stat.S_IMODE(self.clip.stat().st_mode))

    def test_sweep_never_refreezes_a_recorded_path_that_is_not_the_staged_link(self):
        # Fora do projeto, ou dentro dele mas sem ser o mesmo inode do arquivo no staging.
        for index, recorded in enumerate(("../fora.mp4", "brolls/clips/a.mp4")):
            with self.subTest(recorded=recorded):
                media = {"assets/x.mp4": {"media_id": "m", "refreeze_source": recorded}}
                staging = marked_staging(self.root(), name=f".staging-0000000{index}", media=media)
                (staging / "assets").mkdir()
                (staging / "assets" / "x.mp4").write_bytes(b"x")
                with (
                    mock.patch.object(Path, "unlink", autospec=True, side_effect=[PermissionError("x"), None, None]),
                    mock.patch.object(export_folder.delivery, "_thaw_unlink"),
                    mock.patch.object(export_folder.delivery, "_freeze") as freeze,
                ):
                    export_folder.sweep_abandoned(self.root())
                freeze.assert_not_called()
                shutil.rmtree(staging)


class RenameTests(FolderTestCase):
    def setUp(self):
        super().setUp()
        sleep = mock.patch.object(export_folder.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_busy_folder_is_an_honest_message_not_a_collision(self):
        with (
            mock.patch.object(Path, "rename", autospec=True, side_effect=PermissionError("em uso")) as rename,
            self.assertRaisesRegex(ValueError, "Feche o preview ou o antivírus"),
        ):
            self.export()
        targets = {call.args[1].name for call in rename.call_args_list}
        self.assertEqual({"001"}, targets)
        self.assertEqual([], sorted(p.name for p in self.root().iterdir()))

    def test_busy_once_then_free_keeps_the_same_number(self):
        real_rename = Path.rename
        state = {"raised": False}

        def busy_once(path, target):
            if not state["raised"]:
                state["raised"] = True
                raise PermissionError("em uso")
            return real_rename(path, target)

        with mock.patch.object(Path, "rename", autospec=True, side_effect=busy_once):
            result = self.export()
        self.assertEqual("001", result["number"])
        self.assertFalse(any("troque" in w for w in result["warnings"]))

    def test_other_rename_errors_are_honest_and_leave_nothing(self):
        with (
            mock.patch.object(Path, "rename", autospec=True, side_effect=OSError(5, "Input/output error")),
            self.assertRaisesRegex(ValueError, r"Não consegui renomear a pasta do export \(Input/output error\)"),
        ):
            self.export()
        self.assertEqual([], sorted(p.name for p in self.root().iterdir()))


class LatestAndMarkerTests(FolderTestCase):
    def test_latest_write_failure_is_a_warning_and_cleans_its_temp_file(self):
        real_replace = Path.replace

        def refuse_latest(path, target):
            if Path(target).name == export_folder.LATEST:
                raise PermissionError(13, "Permission denied")
            return real_replace(path, target)

        with mock.patch.object(Path, "replace", autospec=True, side_effect=refuse_latest):
            result = self.export()
        root = self.root()
        self.assertEqual("001", result["number"])
        self.assertFalse(result["latest"])
        self.assertTrue((root / "001" / "index.html").is_file())
        warning = next(w for w in result["warnings"] if "LATEST" in w)
        self.assertTrue(warning.endswith(": não mexi nele"), warning)
        self.assertNotIn(str(self.project), warning)
        self.assertEqual(["001"], sorted(p.name for p in root.iterdir()))

    def test_latest_that_is_a_symlink_is_left_alone_with_a_warning(self):
        self.export()
        root = self.root()
        latest = root / export_folder.LATEST
        latest.unlink()
        target = self.project / "meu-latest.txt"
        target.write_text("001\n", encoding="utf-8")
        try:
            latest.symlink_to(target)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        result = self.export()
        self.assertEqual("002", result["number"])
        self.assertFalse(result["latest"])
        self.assertIn("exports/hyperframes/LATEST não é um arquivo: não mexi nele", result["warnings"])
        self.assertTrue(latest.is_symlink())
        self.assertEqual("001\n", target.read_text(encoding="utf-8"))

    def test_marker_has_no_absolute_paths(self):
        self.export()
        text = (self.root() / "001" / export_folder.MARKER).read_text(encoding="utf-8")
        self.assertNotIn(str(self.project), text)
        self.assertNotIn(Path(self.project).as_posix(), text)

    def test_marker_base_cannot_override_the_identity_keys(self):
        base = {**BASE, "marker": "outro", "state": "complete", "number": "999", "media": "x"}
        seen = {}

        def spy(source, dest):
            seen.update(json.loads((staging_of(dest) / export_folder.MARKER).read_text(encoding="utf-8")))
            return export_place.place(source, dest)

        root = self.root()
        content = {"files": FILES, "placements": [("clip:p:1", "assets/clips/c02-main.mp4", self.source(self.clip))]}
        export_folder.write_export(root, 1, content, base, spy)
        self.assertEqual(("getbrolls-export", "staging", "001"), (seen["marker"], seen["state"], seen["number"]))
        marker = json.loads((root / "001" / export_folder.MARKER).read_text(encoding="utf-8"))
        self.assertEqual(("getbrolls-export", "complete", "001"), (marker["marker"], marker["state"], marker["number"]))
        self.assertIn("assets/clips/c02-main.mp4", marker["media"])

    def test_plan_is_written_next_to_the_marker(self):
        root = self.root()
        content = {"files": FILES, "plan": '{"out_dir": "exports/hyperframes/001"}\n', "placements": []}
        export_folder.write_export(root, 1, content, BASE, export_place.place)
        saved = root / "001" / export_folder.PLAN_FILE
        self.assertEqual("getbrolls-plan.json", export_folder.PLAN_FILE)
        self.assertEqual(content["plan"], saved.read_text(encoding="utf-8"))

    def test_marker_names_are_reserved(self):
        names = (export_folder.MARKER, f"{export_folder.MARKER}.tmp", ".GETBROLLS-EXPORT.json")
        for name in (*names, export_folder.PLAN_FILE, "GetBrolls-Plan.JSON"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "nome reservado do get-brolls"):
                self.export(files={**FILES, name: "{}"}, placements=[])
        self.assertEqual([], sorted(p.name for p in self.root().iterdir()))

    def test_marker_rewrite_never_recreates_a_vanished_staging(self):
        def vanish(source, dest):
            shutil.rmtree(staging_of(dest))
            return ("copy", 0)

        with self.assertRaises(OSError):
            self.export(place=vanish)
        self.assertEqual([], sorted(p.name for p in self.root().iterdir()))

    def test_every_written_file_and_the_marker_are_fsynced(self):
        with mock.patch.object(export_folder.os, "fsync", wraps=os.fsync) as fsync:
            self.export()
        # 3 textos + marcador staging + regravação antes do hardlink + marcador complete + LATEST
        self.assertGreaterEqual(fsync.call_count, len(FILES) + 3)


if __name__ == "__main__":
    unittest.main()
