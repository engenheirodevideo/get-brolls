"""Pastas de export numeradas: nunca apaga nem mistura, `LATEST`, staging, varredura e corrida."""

import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

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
            "exports/hyperframes/.staging-bbbb2222 não tem o marcador do get-brolls: ficou onde está (apague se for seu)",
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


if __name__ == "__main__":
    unittest.main()
