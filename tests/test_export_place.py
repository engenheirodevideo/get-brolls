"""Mídia no export: clipe por hardlink sem chmod, mídia da pessoa por clone/cópia, plugin pela cópia injetada."""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import export_place


def source_of(path, method):
    info = Path(path).lstat()
    return {
        "path": str(path), "st_dev": info.st_dev, "st_ino": info.st_ino, "st_mtime_ns": info.st_mtime_ns,
        "st_size": info.st_size, "method": method,
    }  # fmt: skip


class PlaceTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-place-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.staging = self.root / "staging"
        env = mock.patch.dict(os.environ, {"GB_DELIVERY_COPY": ""})
        env.start()
        self.addCleanup(env.stop)

    def file(self, name, data=b"conteudo", mode=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if mode is not None:
            path.chmod(mode)
        return path


class ClipTests(PlaceTestCase):
    def test_clip_is_hardlinked_and_its_mode_is_untouched(self):
        clip = self.file("brolls/clips/a.mp4", mode=0o444)
        before = stat.S_IMODE(clip.stat().st_mode)
        method, copied = export_place.place(source_of(clip, "hardlink"), self.staging / "assets/clips/c02-main.mp4")
        dest = self.staging / "assets/clips/c02-main.mp4"
        self.assertEqual(("hardlink", 0), (method, copied))
        self.assertTrue(clip.samefile(dest))
        self.assertEqual(before, stat.S_IMODE(clip.stat().st_mode))
        self.assertFalse(dest.is_symlink())

    def test_forced_copy_makes_an_independent_file(self):
        clip = self.file("brolls/clips/a.mp4")
        with mock.patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
            method, copied = export_place.place(source_of(clip, "hardlink"), self.staging / "assets/clips/x.mp4")
        self.assertEqual(("copy", len(b"conteudo")), (method, copied))
        self.assertFalse(clip.samefile(self.staging / "assets/clips/x.mp4"))

    def test_no_hardlink_falls_back_to_copy_never_symlink(self):
        clip = self.file("brolls/clips/a.mp4")
        with mock.patch.object(export_place.delivery.os, "link", side_effect=OSError("sem hardlink")):
            method, _ = export_place.place(source_of(clip, "hardlink"), self.staging / "assets/clips/y.mp4")
        self.assertEqual("copy", method)
        self.assertFalse((self.staging / "assets/clips/y.mp4").is_symlink())


class PersonMediaTests(PlaceTestCase):
    def test_person_media_is_never_the_same_inode(self):
        voice = self.file("aroll/c01.mov", mode=0o644)
        method, _ = export_place.place(source_of(voice, "clone"), self.staging / "assets/aroll/c01.mov")
        dest = self.staging / "assets/aroll/c01.mov"
        self.assertIn(method, ("clone", "reflink-auto", "copy"))
        self.assertFalse(voice.samefile(dest))
        self.assertEqual(b"conteudo", dest.read_bytes())
        if os.name == "nt":  # no Windows, arquivo gravável é 0o666: basta seguir gravável (não congelado)
            self.assertTrue(voice.stat().st_mode & stat.S_IWRITE)
        else:
            self.assertEqual(0o644, stat.S_IMODE(voice.stat().st_mode))

    def test_clone_is_only_attempted_where_a_cp_can_clone(self):
        cases = (("darwin", None, True), ("linux", "/usr/bin/cp", True), ("linux", None, False), ("win32", None, False))
        for platform, cp, expected in cases:
            with (
                self.subTest(platform=platform, cp=cp),
                mock.patch.object(export_place.sys, "platform", platform),
                mock.patch.object(export_place.shutil, "which", return_value=cp),
            ):
                self.assertEqual(expected, export_place.can_clone())

    def test_failed_clone_removes_the_partial_and_copies(self):
        voice = self.file("aroll/c01.mov")
        dest = self.staging / "c01.mov"
        dest.parent.mkdir(parents=True)

        def failing(argv, **kwargs):
            Path(argv[-1]).write_bytes(b"parcial")
            return subprocess.CompletedProcess(argv, 1)

        with (
            mock.patch.object(export_place.sys, "platform", "linux"),
            mock.patch.object(export_place.shutil, "which", return_value="/usr/bin/cp"),
            mock.patch.object(export_place.subprocess, "run", side_effect=failing) as run,
        ):
            self.assertEqual("copy", export_place.clone_or_copy(voice, dest))
        self.assertEqual(["/usr/bin/cp", "--reflink=auto", "--", str(voice), str(dest)], run.call_args.args[0])
        self.assertEqual(b"conteudo", dest.read_bytes())

    def test_macos_uses_cp_c_and_missing_cp_copies(self):
        voice = self.file("aroll/c01.mov")
        dest = self.staging / "c01.mov"
        dest.parent.mkdir(parents=True)
        with (
            mock.patch.object(export_place.sys, "platform", "darwin"),
            mock.patch.object(export_place.subprocess, "run", side_effect=FileNotFoundError("/bin/cp")) as run,
        ):
            self.assertEqual("copy", export_place.clone_or_copy(voice, dest))
        self.assertEqual(["/bin/cp", "-c", "--", str(voice), str(dest)], run.call_args.args[0])
        self.assertNotIn("timeout", run.call_args.kwargs)
        self.assertEqual(b"conteudo", dest.read_bytes())

    def test_broken_cp_falls_back_to_a_full_copy(self):
        voice = self.file("aroll/c01.mov")
        dest = self.staging / "c01.mov"
        dest.parent.mkdir(parents=True)
        with (
            mock.patch.object(export_place.sys, "platform", "darwin"),
            mock.patch.object(export_place.subprocess, "run", side_effect=subprocess.SubprocessError("quebrou")),
        ):
            self.assertEqual("copy", export_place.clone_or_copy(voice, dest))
        self.assertEqual(b"conteudo", dest.read_bytes())

    def test_relative_paths_never_reach_cp(self):
        voice = self.file("aroll/c01.mov")
        with (
            mock.patch.object(export_place.sys, "platform", "darwin"),
            mock.patch.object(export_place.subprocess, "run") as run,
            self.assertRaisesRegex(ValueError, "caminho absoluto"),
        ):
            export_place.clone_or_copy(voice, Path("relativo.mov"))
        run.assert_not_called()

    def test_a_clone_counts_the_full_size_because_cp_may_have_copied(self):
        voice = self.file("aroll/c01.mov")

        def cloned(argv, **kwargs):
            shutil.copyfile(argv[-2], argv[-1])
            return subprocess.CompletedProcess(argv, 0)

        for platform, which, method in (("darwin", None, "clone"), ("linux", "/usr/bin/cp", "reflink-auto")):
            dest = self.staging / f"assets/aroll/{platform}.mov"
            with (
                self.subTest(platform=platform),
                mock.patch.object(export_place.sys, "platform", platform),
                mock.patch.object(export_place.shutil, "which", return_value=which),
                mock.patch.object(export_place.subprocess, "run", side_effect=cloned),
            ):
                self.assertEqual((method, len(b"conteudo")), export_place.place(source_of(voice, "clone"), dest))

    def test_windows_copies_without_a_subprocess(self):
        voice = self.file("aroll/c01.mov")
        dest = self.staging / "c01.mov"
        dest.parent.mkdir(parents=True)
        with (
            mock.patch.object(export_place.sys, "platform", "win32"),
            mock.patch.object(export_place.subprocess, "run") as run,
        ):
            self.assertEqual("copy", export_place.clone_or_copy(voice, dest))
        run.assert_not_called()

    def test_changed_or_linked_source_stops_the_export(self):
        voice = self.file("aroll/c01.mov")
        source = source_of(voice, "clone")
        voice.unlink()
        self.file("aroll/c01.mov", b"outro arquivo")
        with self.assertRaisesRegex(ValueError, "c01.mov mudou durante o export: repita"):
            export_place.place(source, self.staging / "assets/aroll/c01.mov")
        other = self.file("aroll/real.mov")
        voice.unlink()
        try:
            voice.symlink_to(other)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        with self.assertRaisesRegex(ValueError, "mudou durante o export"):
            export_place.place(source_of(other, "clone") | {"path": str(voice)}, self.staging / "x.mov")

    def test_vanished_source_says_so(self):
        voice = self.file("aroll/c01.mov")
        source = source_of(voice, "clone")
        voice.unlink()
        with self.assertRaisesRegex(ValueError, "c01.mov sumiu durante o export"):
            export_place.place(source, self.staging / "assets/aroll/c01.mov")


class PluginMediaTests(PlaceTestCase):
    def test_plugin_media_goes_through_the_injected_copy(self):
        calls = []
        source = {
            "path": "/x/lofi.mp3",
            "st_dev": 1,
            "st_ino": 2,
            "st_mtime_ns": None,
            "st_size": 7,
            "method": "plugin",
        }

        def copier(src, dest):
            calls.append((src["st_ino"], Path(dest).name))
            Path(dest).write_bytes(b"musica!")

        method, copied = export_place.place(source, self.staging / "assets/musica/lofi.mp3", copier)
        self.assertEqual(("copy", 7), (method, copied))
        self.assertEqual([(2, "lofi.mp3")], calls)

    def test_plugin_media_without_the_sdk_copy_is_refused(self):
        source = {"path": "/x/lofi.mp3", "st_dev": 1, "st_ino": 2, "st_size": 7, "method": "plugin"}
        with self.assertRaisesRegex(ValueError, "cópia pelo descritor"):
            export_place.place(source, self.staging / "assets/musica/lofi.mp3")

    def test_plugin_copy_with_another_size_is_undone(self):
        source = {"path": "/x/lofi.mp3", "st_dev": 1, "st_ino": 2, "st_size": 7, "method": "plugin"}
        dest = self.staging / "assets/musica/lofi.mp3"

        def copier(src, target):
            Path(target).write_bytes(b"outra musica, maior")

        with self.assertRaisesRegex(ValueError, "lofi.mp3 mudou durante o export"):
            export_place.place(source, dest, copier)
        self.assertFalse(dest.exists())


class ExistingDestTests(PlaceTestCase):
    def test_existing_file_or_planted_link_at_the_destination_is_refused(self):
        clip = self.file("brolls/clips/a.mp4")
        outside = self.file("fora/alvo.mp4", b"nao mexa")
        dest = self.staging / "assets/clips/a.mp4"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(b"da pessoa")
        for method in ("hardlink", "clone"):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "já existe no export") as caught:
                export_place.place(source_of(clip, method), dest)
            self.assertNotIn("deliver", str(caught.exception))
        self.assertEqual(b"da pessoa", dest.read_bytes())
        dest.unlink()
        try:
            dest.symlink_to(outside)
        except OSError:
            self.skipTest("este sistema não cria symlink")
        with self.assertRaisesRegex(ValueError, "já existe no export"):
            export_place.place(source_of(clip, "clone"), dest)
        with self.assertRaisesRegex(ValueError, "já existe no export"):
            export_place.place(source_of(clip, "plugin"), dest, lambda src, target: Path(target).write_bytes(b"x"))
        self.assertTrue(dest.is_symlink())
        self.assertEqual(b"nao mexa", outside.read_bytes())


class RecheckTests(PlaceTestCase):
    """A fonte é conferida antes e depois de pôr: nunca entra no export um arquivo diferente do plano."""

    def test_same_inode_with_another_size_is_refused(self):
        voice = self.file("aroll/c01.mov")
        source = source_of(voice, "clone")
        with voice.open("ab") as out:
            out.write(b" e mais")
        self.assertEqual(source["st_ino"], voice.lstat().st_ino)
        with self.assertRaisesRegex(ValueError, "c01.mov mudou durante o export"):
            export_place.place(source, self.staging / "assets/aroll/c01.mov")
        self.assertFalse((self.staging / "assets/aroll/c01.mov").exists())

    def test_same_inode_and_size_with_another_date_is_refused(self):
        voice = self.file("aroll/c01.mov")
        source = source_of(voice, "clone")
        os.utime(voice, ns=(source["st_mtime_ns"], source["st_mtime_ns"] + 5_000_000_000))
        with self.assertRaisesRegex(ValueError, "c01.mov mudou durante o export"):
            export_place.place(source, self.staging / "assets/aroll/c01.mov")

    def test_hardlink_to_another_file_is_undone(self):
        clip = self.file("brolls/clips/a.mp4")
        other = self.file("brolls/clips/b.mp4", b"outro clipe")
        dest = self.staging / "assets/clips/a.mp4"

        def swapped(src, target, **kwargs):
            os.link(other, target)
            return "hardlink"

        with (
            mock.patch.object(export_place.delivery, "link_or_copy", side_effect=swapped),
            self.assertRaisesRegex(ValueError, "a.mp4 mudou durante o export"),
        ):
            export_place.place(source_of(clip, "hardlink"), dest)
        self.assertFalse(dest.exists())
        self.assertEqual(b"outro clipe", other.read_bytes())

    def test_copy_with_other_bytes_is_undone(self):
        voice = self.file("aroll/c01.mov")
        dest = self.staging / "assets/aroll/c01.mov"

        def short(src, target):
            Path(target).write_bytes(b"curto")
            return "copy"

        with (
            mock.patch.object(export_place, "clone_or_copy", side_effect=short),
            self.assertRaisesRegex(ValueError, "c01.mov mudou durante o export"),
        ):
            export_place.place(source_of(voice, "clone"), dest)
        self.assertFalse(dest.exists())
        self.assertEqual(b"conteudo", voice.read_bytes())

    def test_source_changed_while_copying_is_undone(self):
        voice = self.file("aroll/c01.mov")
        dest = self.staging / "assets/aroll/c01.mov"
        real = export_place.clone_or_copy

        def racing(src, target):
            method = real(src, target)
            Path(src).write_bytes(b"regravado durante a copia")
            return method

        with (
            mock.patch.object(export_place, "clone_or_copy", side_effect=racing),
            self.assertRaisesRegex(ValueError, "c01.mov mudou durante o export"),
        ):
            export_place.place(source_of(voice, "clone"), dest)
        self.assertFalse(dest.exists())


PLAN_MEDIA = {
    "aroll:c01": {"available": True, "ext": ".mov"},
    "aroll:c02": {"available": False, "ext": None},
    "clip:p:1": {"available": True, "ext": ".mp4"},
    "clip:p:2": {"available": True, "ext": ".mp4"},
}
PLAN_SOURCES = {"aroll:c01": {}, "clip:p:1": {}}


class RequestTests(unittest.TestCase):
    def check(self, requests):
        return export_place.check_requests(requests, PLAN_MEDIA, PLAN_SOURCES)

    def test_good_requests_pass_through(self):
        requests = [("aroll:c01", "assets/aroll/c01.MOV"), ("clip:p:1", "assets/clips/c02-main.mp4")]
        self.assertEqual(requests, self.check(requests))

    def test_each_rule_is_refused(self):
        cases = {
            "não está no plano": [("aroll:c09", "assets/aroll/c09.mov")],
            "não está disponível": [("aroll:c02", "assets/aroll/c02.mov")],
            "fora de assets/": [("aroll:c01", "aroll/c01.mov")],
            "trecho vazio": [("aroll:c01", "assets/../c01.mov")],
            "não tem a extensão": [("aroll:c01", "assets/aroll/c01.mp4")],
            "mesmo destino": [("aroll:c01", "assets/x/A.mov"), ("aroll:c01", "assets/x/a.mov")],
        }
        for fragment, requests in cases.items():
            with self.subTest(fragment=fragment), self.assertRaisesRegex(ValueError, fragment):
                self.check(requests)

    def test_dot_dotdot_and_empty_segments_are_refused(self):
        for dest in (
            "assets/../c01.mov",
            "assets/./c01.mov",
            "assets//c01.mov",
            "/assets/c01.mov",
            "assets/x/../c01.mov",
        ):
            with self.subTest(dest=dest), self.assertRaisesRegex(ValueError, "trecho vazio, '.' ou '..'"):
                self.check([("aroll:c01", dest)])

    def test_available_in_the_plan_but_without_a_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, "clip:p:2.*não está disponível"):
            self.check([("clip:p:2", "assets/clips/c02.mp4")])


class SameAsBeforeTests(PlaceTestCase):
    def test_changed_source_is_detected_for_the_warning(self):
        voice = self.file("aroll/c01.mov")
        info = voice.lstat()
        row = {"source_ino": info.st_ino, "source_mtime_ns": info.st_mtime_ns, "source_size": info.st_size}
        source = {"path": str(voice)}
        self.assertTrue(export_place.same_as_before(source, row))
        voice.write_bytes(b"gravei de novo, mais longo")
        self.assertFalse(export_place.same_as_before(source, row))
        voice.unlink()
        self.assertFalse(export_place.same_as_before(source, row))


if __name__ == "__main__":
    unittest.main()
