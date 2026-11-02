"""Leitor único de arquivo de fora (`getbrolls.reader`): link, teto, permissão, `--project-dir`, cópia e texto."""

import contextlib
import errno
import hashlib
import os
import pickle
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _fake_secrets import joined
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

from getbrolls import reader, runtime
from getbrolls.errors import Refusal, UsageError

POSIX = os.name != "nt"
CAN_CHMOD = POSIX and os.geteuid() != 0
FD_DIR = Path("/dev/fd")
FIRST_PASS = 2  # `lstat` da raiz e de `project` na descida; o terceiro já é a reconferência


class ReaderCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-reader-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.outside = Path(tempfile.mkdtemp(prefix="gb-reader-out-")).resolve()
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)
        (self.outside / "segredo.txt").write_text("de fora", encoding="utf-8")

    def write(self, rel, data=b"conteudo"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def refused(self, reason, call, *args, **kwargs):
        with self.assertRaises(reader.Refused) as caught:
            call(*args, **kwargs)
        self.assertEqual(reason, caught.exception.reason)
        return caught.exception


class PathRulesTests(ReaderCase):
    def test_a_relative_posix_path_splits_and_the_root_is_empty(self):
        self.assertEqual(("a", "b c.json"), reader.parts_of("a/b c.json"))
        self.assertEqual((), reader.parts_of(""))

    def test_every_hostile_spelling_is_bad_name(self):
        for rel in ("/etc/x", "a/../b", "a\\b", "C:x", "a//b", "a/./b", "x\x00", "e\u0301", "a.", "a ", "CON.txt",
                    "a\u202eb", "/".join(["p"] * 17), "x" * 256):  # fmt: skip
            with self.subTest(rel=rel):
                self.refused("bad_name", reader.parts_of, rel)
        self.refused("bad_name", reader.parts_of, b"a")


class OpenUnderTests(ReaderCase):
    def test_reads_the_bytes_up_to_the_ceiling(self):
        self.write("project/hyperframes.json", b"12345")
        self.assertEqual(b"12345", reader.open_under(self.root, "project/hyperframes.json", max_bytes=5))
        self.refused("too_big", reader.open_under, self.root, "project/hyperframes.json", max_bytes=4)

    def test_a_huge_file_is_refused_before_any_read(self):
        with (self.root / "grande.mp4").open("wb") as stream:
            stream.truncate(2 * 1024**3)  # 2 GB esparso: nenhum byte é lido
        with mock.patch.object(reader.os, "read", side_effect=AssertionError("leu")):
            self.refused("too_big", reader.open_under, self.root, "grande.mp4", max_bytes=reader.MAX_COMPONENT_BYTES)

    def test_a_file_that_grows_after_the_check_is_cut_at_the_ceiling(self):
        self.write("cresce.txt", b"x" * 10)
        real = reader.os.fstat

        def small(fd):
            info = real(fd)
            return os.stat_result((*info[:6], 1, *info[7:]))

        with mock.patch.object(reader.os, "fstat", side_effect=small):
            self.refused("too_big", reader.open_under, self.root, "cresce.txt", max_bytes=4)

    def test_a_name_it_cannot_even_look_at_is_unreadable(self):
        # O POSIX olha cada nome com `os.stat(dir_fd=...)`, o Windows com `os.lstat`: nenhum dos dois deixa olhar.
        with (
            mock.patch.object(reader.os, "stat", side_effect=PermissionError),
            mock.patch.object(reader.os, "lstat", side_effect=PermissionError),
            self.assertRaises(reader.UnreadableError),
        ):
            reader.open_under(self.root, "nada.txt", max_bytes=10)

    def test_missing_is_missing_never_refused(self):
        with self.assertRaises(reader.MissingError) as caught:
            reader.open_under(self.root, "project/index.html", max_bytes=10)
        self.assertEqual("INVALID_DATA", runtime.error_code_for(caught.exception))
        self.assertNotIsInstance(caught.exception, Refusal)

    @needs_symlink
    def test_a_link_in_any_piece_is_refused(self):
        (self.root / "project").mkdir()
        (self.root / "project" / "fora.txt").symlink_to(self.outside / "segredo.txt")
        (self.root / "atalho").symlink_to(self.outside)
        (self.root / "dentro").symlink_to(self.root / "project")
        self.refused("link", reader.open_under, self.root, "project/fora.txt", max_bytes=100)
        self.refused("link", reader.open_under, self.root, "atalho/segredo.txt", max_bytes=100)
        self.refused("link", reader.open_under, self.root, "dentro/fora.txt", max_bytes=100)

    def test_a_file_in_the_middle_is_not_dir_and_the_root_is_not_regular(self):
        self.write("project", b"arquivo")
        self.refused("not_dir", reader.open_under, self.root, "project/index.html", max_bytes=100)
        self.refused("not_regular", reader.open_under, self.root, "", max_bytes=100)

    @unittest.skipUnless(POSIX and hasattr(os, "mkfifo"), "FIFO só no POSIX")
    def test_a_fifo_never_blocks_and_is_not_regular(self):
        os.mkfifo(self.root / "cano")
        self.refused("not_regular", reader.open_under, self.root, "cano", max_bytes=100)

    @unittest.skipUnless(POSIX, "hardlink no POSIX")
    def test_a_hardlinked_text_is_refused_and_media_may_have_two_names(self):
        target = self.write("a.txt", b"dois nomes")
        os.link(target, self.root / "b.txt")
        self.refused("hardlinked", reader.open_under, self.root, "a.txt", max_bytes=100)
        self.assertEqual(b"dois nomes", reader.open_under(self.root, "a.txt", max_bytes=100, single_link=False))

    @unittest.skipUnless(CAN_CHMOD, "permissões POSIX, sem root")
    def test_a_folder_without_permission_is_unreadable_never_missing(self):
        self.write("project/index.html", b"x")
        (self.root / "project").chmod(0)
        self.addCleanup((self.root / "project").chmod, 0o700)
        with self.assertRaises(reader.UnreadableError) as caught:
            reader.open_under(self.root, "project/index.html", max_bytes=10)
        self.assertEqual("IO_ERROR", runtime.error_code_for(caught.exception))
        self.assertEqual(errno.EACCES, caught.exception.errno)
        with self.assertRaises(reader.UnreadableError) as listed:
            reader.list_under(self.root, "project")
        self.assertEqual(errno.EACCES, listed.exception.errno)

    @unittest.skipUnless(CAN_CHMOD, "permissões POSIX, sem root")
    def test_a_file_without_permission_is_unreadable(self):
        self.write("x.txt", b"x").chmod(0)
        with self.assertRaises(reader.UnreadableError) as caught:
            reader.open_under(self.root, "x.txt", max_bytes=10)
        self.assertEqual(errno.EACCES, caught.exception.errno)

    def test_the_refusal_carries_its_reason_code_and_survives_pickle(self):
        refusal = reader.Refused("link", "project/x")
        self.assertIsInstance(refusal, Refusal)
        self.assertEqual(("UNSAFE_INPUT", "INVALID_DATA"), (refusal.reason_code, runtime.error_code_for(refusal)))
        again = pickle.loads(pickle.dumps(refusal))  # noqa: S301 - round trip do nosso próprio objeto
        self.assertEqual((str(refusal), "link", "project/x"), (str(again), again.reason, again.rel))
        self.assertNotIn(str(self.root), str(refusal))


class TextAndJsonTests(ReaderCase):
    def test_text_drops_the_bom_and_refuses_bad_utf8_and_nul(self):
        self.write("a.md", "\ufeffolá".encode())
        self.assertEqual("olá", reader.text_under(self.root, "a.md"))
        self.write("b.md", b"\xff\xfe")
        self.refused("not_text", reader.text_under, self.root, "b.md")
        self.write("c.md", b"a\x00b")
        self.refused("not_text", reader.text_under, self.root, "c.md")

    def test_json_refuses_repeats_nan_depth_and_non_objects(self):
        self.write("ok.json", b'{"a": [1, {"b": null}]}')
        self.assertEqual({"a": [1, {"b": None}]}, reader.json_under(self.root, "ok.json"))
        for name, raw in (
            ("dup.json", b'{"a": 1, "a": 2}'),
            ("nan.json", b'{"a": NaN}'),
            ("inf.json", b'{"a": -Infinity}'),
            ("list.json", b"[1]"),
            ("deep.json", b'{"a": ' + b"[" * 32 + b"]" * 32 + b"}"),
            ("broken.json", b'{"a": '),
        ):
            with self.subTest(name=name):
                self.write(name, raw)
                self.refused("bad_json", reader.json_under, self.root, name)

    def test_json_refuses_non_finite_floats_and_lone_surrogates(self):
        for text in ('{"a": 1e999}', '{"a": -1e999}', '{"a": [1, 1e400]}', '{"a": "\\ud800"}', '{"\\udfff": 1}',
                     '{"a": {"b": ["x", "\\ud83d"]}}'):  # fmt: skip
            with self.subTest(text=text):
                self.write("c.json", text.encode("utf-8"))
                self.refused("bad_json", reader.json_under, self.root, "c.json")

    def test_finite_floats_pass_and_huge_literals_are_bad_json(self):
        self.write("c.json", b'{"a": 1e308, "b": -0.0, "c": 1E5, "d": 5e-324, "e": "\\ud83d\\ude00"}')
        value = reader.json_under(self.root, "c.json")
        self.assertEqual((1e308, -0.0, 1e5, 5e-324, "😀"), (value["a"], value["b"], value["c"], value["d"], value["e"]))
        self.addCleanup(sys.set_int_max_str_digits, sys.get_int_max_str_digits())
        sys.set_int_max_str_digits(4300)  # the default limit, whatever the interpreter was started with
        self.write("c.json", b'{"a": ' + b"9" * 5000 + b"}")
        self.refused("bad_json", reader.json_under, self.root, "c.json")


class UnreadableHintsTests(ReaderCase):
    def test_the_hint_follows_the_errno(self):
        self.assertIn("permissão", reader.hint_for(errno.EACCES))
        self.assertIn("limite de arquivos abertos", reader.hint_for(errno.EMFILE))
        self.assertIn("disco", reader.hint_for(errno.EIO))
        self.assertIn("link", reader.hint_for(errno.ELOOP))
        self.assertEqual("confira a pasta", reader.hint_for(None))
        exc = reader.UnreadableError("a/b", "OSError", errno.EMFILE)
        self.assertEqual(errno.EMFILE, exc.errno)
        self.assertEqual(
            "a/b: não consegui ler (OSError); feche outros programas ou aumente o limite de arquivos abertos.",
            str(exc),
        )

    def test_an_io_error_mid_read_is_unreadable_with_its_rel(self):
        self.write("x.txt", b"abc")
        with (
            mock.patch.object(reader.os, "read", side_effect=OSError(errno.EIO, "Input/output error")),
            self.assertRaises(reader.UnreadableError) as caught,
        ):
            reader.open_under(self.root, "x.txt", max_bytes=10)
        self.assertEqual(("x.txt", errno.EIO), (caught.exception.rel, caught.exception.errno))
        self.assertIn("disco", str(caught.exception))

    @unittest.skipUnless(FD_DIR.is_dir(), "lista de descritores abertos em /dev/fd")
    def test_a_failed_fstat_is_unreadable_and_closes_the_descriptor(self):
        # the Windows branch (`_BY_FD` False) kept the fd open when `os.fstat` raised
        self.write("x.txt", b"abc")
        before = len(list(FD_DIR.iterdir()))
        with (
            mock.patch.object(reader, "_BY_FD", False),
            mock.patch.object(reader.os, "fstat", side_effect=OSError(errno.EIO, "Input/output error")),
            self.assertRaises(reader.UnreadableError) as caught,
        ):
            reader.open_under(self.root, "x.txt", max_bytes=10)
        self.assertEqual(("x.txt", errno.EIO), (caught.exception.rel, caught.exception.errno))
        self.assertEqual(before, len(list(FD_DIR.iterdir())))

    def test_a_failed_fstat_after_the_open_is_unreadable_on_either_path(self):
        self.write("x.txt", b"abc")
        for by_fd in (True, False):
            with self.subTest(by_fd=by_fd):
                if by_fd and not reader._BY_FD:
                    # sem `dir_fd` (Windows) a descida por descritor não existe: ligar a chave só abriria a pasta
                    self.skipTest("descida por descritor só onde há dir_fd (POSIX)")
                with (
                    mock.patch.object(reader, "_BY_FD", by_fd),
                    mock.patch.object(reader.os, "fstat", side_effect=OSError(errno.EIO, "Input/output error")),
                    self.assertRaises(reader.UnreadableError) as caught,
                ):
                    reader.open_under(self.root, "x.txt", max_bytes=10)
                self.assertEqual(("x.txt", errno.EIO), (caught.exception.rel, caught.exception.errno))

    def test_a_descriptor_shortage_while_opening_is_unreadable_with_the_descriptor_hint(self):
        self.write("d/x.txt")
        real_open = os.open

        def short(path, flags, *args, **kwargs):
            if str(path).endswith("x.txt"):
                raise OSError(errno.EMFILE, "Too many open files")
            return real_open(path, flags, *args, **kwargs)

        with (
            mock.patch.object(reader.os, "open", side_effect=short),
            self.assertRaises(reader.UnreadableError) as caught,
        ):
            reader.open_under(self.root, "d/x.txt", max_bytes=10)
        self.assertEqual((errno.EMFILE, "d/x.txt"), (caught.exception.errno, caught.exception.rel))
        self.assertIn("limite de arquivos abertos", str(caught.exception))

    @unittest.skipUnless(POSIX, "descida por descritor só no POSIX")
    def test_when_the_follow_up_stat_fails_too_its_errno_wins(self):
        self.write("d/x.txt")
        real_open, real_stat = os.open, os.stat

        def refuse(path, flags, *args, **kwargs):
            if str(path) == "x.txt":
                raise OSError(errno.EACCES, "Permission denied")
            return real_open(path, flags, *args, **kwargs)

        def broken(path, *args, **kwargs):
            if str(path) == "x.txt":
                raise OSError(errno.EIO, "Input/output error")
            return real_stat(path, *args, **kwargs)

        with (
            mock.patch.object(reader.os, "open", side_effect=refuse),
            mock.patch.object(reader.os, "stat", side_effect=broken),
            self.assertRaises(reader.UnreadableError) as caught,
        ):
            reader.open_under(self.root, "d/x.txt", max_bytes=10)
        self.assertEqual((errno.EIO, "d/x.txt"), (caught.exception.errno, caught.exception.rel))
        self.assertIn("disco", str(caught.exception))

    def test_a_scan_failure_keeps_its_errno_and_the_error_survives_pickle(self):
        self.write("d/x.txt")
        with (
            mock.patch.object(reader.os, "scandir", side_effect=OSError(errno.EIO, "Input/output error")),
            self.assertRaises(reader.UnreadableError) as caught,
        ):
            reader.list_under(self.root, "d")
        self.assertEqual(("d", errno.EIO), (caught.exception.rel, caught.exception.errno))
        for exc in (caught.exception, reader.UnreadableError("a", "OSError")):
            again = pickle.loads(pickle.dumps(exc))  # noqa: S301 - round trip do nosso próprio objeto
            self.assertEqual(
                (str(exc), exc.rel, exc.type_name, exc.errno), (str(again), again.rel, again.type_name, again.errno)
            )


class ListUnderTests(ReaderCase):
    @needs_symlink
    def test_lists_in_name_order_and_never_follows_a_link(self):
        self.write("project/index.html", b"<html>")
        self.write("project/renders/final.mp4", b"1234")
        (self.root / "project" / "fora").symlink_to(self.outside)
        listing = reader.list_under(self.root, "project")
        self.assertTrue(listing.complete)
        self.assertEqual(
            [
                ("project/fora", "link", None),
                ("project/index.html", "file", 6),
                ("project/renders", "dir", None),
                ("project/renders/final.mp4", "file", 4),
            ],
            [(entry.rel, entry.kind, entry.size) for entry in listing.entries],
        )
        self.assertNotIn("segredo.txt", repr(listing))

    def test_the_ceilings_cut_the_listing_and_say_so(self):
        self.write("a/b/c/d.txt")
        self.write("a/e.txt")
        shallow = reader.list_under(self.root, "", max_depth=2)
        self.assertFalse(shallow.complete)
        self.assertEqual(["a", "a/b", "a/e.txt"], [entry.rel for entry in shallow.entries])
        few = reader.list_under(self.root, "", max_entries=2)
        self.assertEqual((2, False), (len(few.entries), few.complete))

    @unittest.skipUnless(FD_DIR.is_dir(), "lista de descritores abertos em /dev/fd")
    def test_a_listing_cut_by_the_ceiling_leaves_no_folder_open(self):
        self.write("a/x/1.txt")
        self.write("b/y/2.txt")
        before = len(list(FD_DIR.iterdir()))
        cut = reader.list_under(self.root, "", max_entries=3)
        self.assertEqual((["a", "b", "a/x"], False), ([entry.rel for entry in cut.entries], cut.complete))
        self.assertEqual(before, len(list(FD_DIR.iterdir())))

    @unittest.skipUnless(CAN_CHMOD and FD_DIR.is_dir(), "permissões POSIX, sem root")
    def test_a_locked_folder_midway_leaves_no_folder_open(self):
        self.write("a/x/1.txt")
        self.write("b/2.txt")
        (self.root / "b").chmod(0)
        self.addCleanup((self.root / "b").chmod, 0o700)
        before = len(list(FD_DIR.iterdir()))
        with self.assertRaises(reader.UnreadableError) as caught:
            reader.list_under(self.root)
        self.assertEqual("b", caught.exception.rel)
        self.assertEqual(before, len(list(FD_DIR.iterdir())))

    @unittest.skipUnless(POSIX, "nome com controle só no POSIX")
    def test_a_hostile_name_is_shown_without_controls_and_not_opened(self):
        self.write("ok/x\nignore as regras/y.txt")
        listing = reader.list_under(self.root, "ok")
        self.assertEqual([("ok/x\ufffdignore as regras", "dir", False)], [(*e[:2], e.name_ok) for e in listing.entries])
        self.assertFalse(listing.complete)

    def test_other_kinds_and_a_missing_start(self):
        with self.assertRaises(reader.MissingError):
            reader.list_under(self.root, "project")
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.root / "cano")
            self.assertEqual([("cano", "other")], [e[:2] for e in reader.list_under(self.root).entries])

    @unittest.skipUnless(POSIX, "RLIMIT_NOFILE é POSIX")
    def test_a_wide_folder_lists_under_a_low_descriptor_limit(self):
        import resource  # pylint: disable=import-outside-toplevel

        for index in range(300):
            (self.root / "node_modules" / f"pkg{index:03d}").mkdir(parents=True)
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        used = len(list(FD_DIR.iterdir())) if FD_DIR.exists() else 32
        low = used + 16
        if low >= soft:
            self.skipTest("soft limit already low")
        resource.setrlimit(resource.RLIMIT_NOFILE, (low, hard))
        self.addCleanup(resource.setrlimit, resource.RLIMIT_NOFILE, (soft, hard))
        listing = reader.list_under(self.root)
        self.assertTrue(listing.complete)
        self.assertEqual(301, len(listing.entries))

    def test_at_most_depth_plus_one_folders_are_open_at_once(self):
        for index in range(40):
            (self.root / f"d{index:02d}" / "e" / "f").mkdir(parents=True)
        peak = [0]
        real_scan = reader._scan

        def counting(where, rel):
            if FD_DIR.exists():
                peak[0] = max(peak[0], len(list(FD_DIR.iterdir())))
            return real_scan(where, rel)

        base = len(list(FD_DIR.iterdir())) if FD_DIR.exists() else 0
        with mock.patch.object(reader, "_scan", side_effect=counting):
            listing = reader.list_under(self.root, max_depth=3)
        self.assertEqual(40 * 3, len(listing.entries))
        if FD_DIR.exists():
            self.assertLessEqual(peak[0] - base, 2)  # uma pasta e a cópia do descritor que o scandir faz

    def test_the_listing_stops_scanning_at_the_entry_ceiling(self):
        for index in range(6):
            (self.root / f"d{index}" / "x").mkdir(parents=True)
        scanned = []
        opened = []
        real_scan = reader._scan
        real_open_folder = reader._open_folder

        def noting(where, rel):
            scanned.append(rel)
            return real_scan(where, rel)

        def opening(root, parts, rel, identity):
            opened.append(rel)
            return real_open_folder(root, parts, rel, identity)

        with (
            mock.patch.object(reader, "_scan", side_effect=noting),
            mock.patch.object(reader, "_open_folder", side_effect=opening),
        ):
            listing = reader.list_under(self.root, max_entries=4)
        self.assertFalse(listing.complete)
        self.assertEqual(4, len(listing.entries))
        self.assertEqual([""], scanned)  # a raiz encheu a lista; nenhuma subpasta foi lida
        self.assertEqual([""], opened)  # nem aberta

    def test_a_subfolder_gone_before_its_visit_is_missing(self):
        (self.root / "a" / "b").mkdir(parents=True)
        real_scan = reader._scan

        def removing(where, rel):
            found = real_scan(where, rel)
            if rel == "":
                shutil.rmtree(self.root / "a")
            return found

        with mock.patch.object(reader, "_scan", side_effect=removing), self.assertRaises(reader.MissingError) as caught:
            reader.list_under(self.root)
        self.assertEqual("a", caught.exception.rel)

    @unittest.skipUnless(reader._BY_FD, "a pasta abre por `os.open` só na descida por descritor (POSIX)")
    def test_a_descriptor_shortage_is_unreadable_with_its_hint_and_leaves_nothing_open(self):
        (self.root / "a" / "b").mkdir(parents=True)
        real_open = os.open

        def short(path, flags, *args, **kwargs):
            if path == "a":
                raise OSError(errno.EMFILE, "Too many open files")
            return real_open(path, flags, *args, **kwargs)

        before = len(list(FD_DIR.iterdir())) if FD_DIR.exists() else None
        with (
            mock.patch.object(reader.os, "open", side_effect=short),
            self.assertRaises(reader.UnreadableError) as caught,
        ):
            reader.list_under(self.root)
        self.assertEqual(("a", errno.EMFILE), (caught.exception.rel, caught.exception.errno))
        self.assertIn("limite de arquivos abertos", str(caught.exception))
        if before is not None:
            self.assertEqual(before, len(list(FD_DIR.iterdir())))

    def test_a_subfolder_replaced_by_another_folder_before_its_visit_is_changed(self):
        (self.root / "a" / "b").mkdir(parents=True)
        real_scan = reader._scan

        def replacing(where, rel):
            found = real_scan(where, rel)
            if rel == "":
                (self.root / "a").rename(self.root / "a.old")  # a velha segue viva: o inode não se repete
                (self.root / "a" / "c").mkdir(parents=True)
            return found

        with mock.patch.object(reader, "_scan", side_effect=replacing):
            exc = self.refused("changed", reader.list_under, self.root)
        self.assertEqual("a", exc.rel)


class ProjectDirTests(ReaderCase):
    def test_a_relative_folder_inside_passes_even_before_it_exists(self):
        self.assertEqual("videos/meu-video", reader.project_dir(self.root, "videos/meu-video"))
        (self.root / "videos" / "meu-video").mkdir(parents=True)
        self.assertEqual("videos/meu-video", reader.project_dir(self.root, "videos\\meu-video\\"))

    def test_outside_absolute_or_through_a_file_is_a_usage_error(self):
        self.write("arquivo")
        for value in ("", ".", "/tmp/x", "C:\\x", "../fora", "a/../../b", "arquivo/x"):
            with self.subTest(value=value), self.assertRaises(UsageError) as caught:
                reader.project_dir(self.root, value)
            self.assertNotIn(str(self.root), str(caught.exception))

    @needs_symlink
    def test_a_link_in_the_way_is_a_usage_error(self):
        (self.root / "videos").symlink_to(self.outside)
        with self.assertRaises(UsageError):
            reader.project_dir(self.root, "videos/x")


class WithoutDirFdTests(ReaderCase):
    """O caminho do Windows (sem `dir_fd`), exercitado aqui com `_BY_FD` desligado."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(reader, "_BY_FD", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reads_lists_and_checks_the_project_dir(self):
        self.write("project/renders/final.mp4", b"1234")
        self.assertEqual(b"1234", reader.open_under(self.root, "project/renders/final.mp4", max_bytes=10))
        self.assertEqual(
            ["project/renders", "project/renders/final.mp4"],
            [entry.rel for entry in reader.list_under(self.root, "project").entries],
        )
        self.assertEqual("project/renders", reader.project_dir(self.root, "project/renders"))
        self.assertEqual("project/novo", reader.project_dir(self.root, "project/novo"))
        with self.assertRaises(reader.MissingError):
            reader.open_under(self.root, "project/nada.txt", max_bytes=10)

    @needs_symlink
    def test_a_link_or_a_file_in_the_way_is_refused(self):
        self.write("project/index.html")
        (self.root / "project" / "fora.txt").symlink_to(self.outside / "segredo.txt")
        (self.root / "atalho").symlink_to(self.outside)
        self.refused("link", reader.open_under, self.root, "project/fora.txt", max_bytes=100)
        self.refused("link", reader.open_under, self.root, "atalho/segredo.txt", max_bytes=100)
        self.refused("not_dir", reader.open_under, self.root, "project/index.html/x", max_bytes=100)
        with self.assertRaises(UsageError):
            reader.project_dir(self.root, "atalho")

    def test_a_folder_swapped_during_the_read_is_changed(self):
        self.write("project/a.txt", b"x")
        real = reader._lstat
        calls = []

        def swapped(path, rel):
            calls.append(path)
            info = real(path, rel)
            if len(calls) > FIRST_PASS and path == self.root / "project":
                return os.stat_result((info.st_mode, info.st_ino + 1, *info[2:]))
            return info

        with mock.patch.object(reader, "_lstat", side_effect=swapped):
            self.refused("changed", reader.open_under, self.root, "project/a.txt", max_bytes=10)

    def test_a_descriptor_shortage_listing_a_subfolder_is_unreadable_with_its_hint(self):
        (self.root / "a" / "b").mkdir(parents=True)
        real_scandir = os.scandir

        def short(where):
            if isinstance(where, Path) and where.name == "a":
                raise OSError(errno.EMFILE, "Too many open files")
            return real_scandir(where)

        before = len(list(FD_DIR.iterdir())) if FD_DIR.exists() else None
        with (
            mock.patch.object(reader.os, "scandir", side_effect=short),
            self.assertRaises(reader.UnreadableError) as caught,
        ):
            reader.list_under(self.root)
        self.assertEqual(("a", errno.EMFILE), (caught.exception.rel, caught.exception.errno))
        self.assertIn("limite de arquivos abertos", str(caught.exception))
        if before is not None:
            self.assertEqual(before, len(list(FD_DIR.iterdir())))

    def test_a_failed_fstat_after_the_open_is_unreadable_with_its_rel(self):
        self.write("x.txt", b"abc")
        with (
            mock.patch.object(reader.os, "fstat", side_effect=OSError(errno.EIO, "Input/output error")),
            self.assertRaises(reader.UnreadableError) as caught,
        ):
            reader.open_under(self.root, "x.txt", max_bytes=10)
        self.assertEqual(("x.txt", errno.EIO), (caught.exception.rel, caught.exception.errno))
        self.assertIn("disco", str(caught.exception))

    def test_a_leaf_swapped_between_lstat_and_open_is_changed(self):
        self.write("a.txt", b"x")
        real = reader._lstat

        def other(path, rel):
            info = real(path, rel)
            return os.stat_result((info.st_mode, info.st_ino + 1, *info[2:])) if path.name == "a.txt" else info

        with mock.patch.object(reader, "_lstat", side_effect=other):
            self.refused("changed", reader.open_under, self.root, "a.txt", max_bytes=10)

    @unittest.skipUnless(CAN_CHMOD, "permissões POSIX, sem root")
    def test_without_permission_is_unreadable(self):
        self.write("x.txt").chmod(0)
        with self.assertRaises(reader.UnreadableError) as caught:
            reader.open_under(self.root, "x.txt", max_bytes=10)
        self.assertEqual(errno.EACCES, caught.exception.errno)
        (self.root / "p").mkdir()
        (self.root / "p").chmod(0)
        self.addCleanup((self.root / "p").chmod, 0o700)
        with self.assertRaises(reader.UnreadableError) as folder:
            reader.open_under(self.root, "p/x.txt", max_bytes=10)
        self.assertEqual(errno.EACCES, folder.exception.errno)
        with self.assertRaises(reader.UnreadableError) as listed:
            reader.list_under(self.root, "p")
        self.assertEqual(errno.EACCES, listed.exception.errno)

    @needs_symlink
    def test_a_subfolder_swapped_for_a_link_before_its_visit_is_refused(self):
        (self.root / "a" / "b").mkdir(parents=True)
        real_scan = reader._scan

        def swapping(where, rel):
            found = real_scan(where, rel)
            if rel == "":
                shutil.rmtree(self.root / "a")
                (self.root / "a").symlink_to(self.outside, target_is_directory=True)
            return found

        with mock.patch.object(reader, "_scan", side_effect=swapping):
            exc = self.refused("link", reader.list_under, self.root)
        self.assertEqual("a", exc.rel)

    def test_a_subfolder_replaced_by_another_folder_before_its_visit_is_changed(self):
        (self.root / "a" / "b").mkdir(parents=True)
        real_scan = reader._scan

        def replacing(where, rel):
            found = real_scan(where, rel)
            if rel == "":
                (self.root / "a").rename(self.root / "a.old")  # a velha segue viva: o inode não se repete
                (self.root / "a" / "c").mkdir(parents=True)
            return found

        with mock.patch.object(reader, "_scan", side_effect=replacing):
            exc = self.refused("changed", reader.list_under, self.root)
        self.assertEqual("a", exc.rel)

    def test_a_scan_without_inode_numbers_still_checks_the_subfolder(self):
        """No Windows o `stat` do `scandir` vem com `st_ino` 0: a identidade vem do `lstat`."""
        (self.root / "a" / "b").mkdir(parents=True)
        real_scandir = os.scandir

        class Zeroed:
            def __init__(self, item):
                self.name, self.path, self.item = item.name, item.path, item

            def __getattr__(self, attribute):
                return getattr(self.item, attribute)

            def stat(self, *, follow_symlinks=True):
                info = self.item.stat(follow_symlinks=follow_symlinks)
                return os.stat_result((info.st_mode, 0, 0, *tuple(info)[3:10]))

        @contextlib.contextmanager
        def zeroed(where):
            with real_scandir(where) as found:
                yield [Zeroed(item) for item in found]

        with mock.patch.object(reader.os, "scandir", side_effect=zeroed):
            listing = reader.list_under(self.root)
            self.assertEqual((["a", "a/b"], True), ([entry.rel for entry in listing.entries], listing.complete))
            real_scan = reader._scan

            def replacing(where, rel):
                found = real_scan(where, rel)
                if rel == "":
                    (self.root / "a").rename(self.root / "a.old")  # a velha segue viva: o inode não se repete
                    (self.root / "a" / "c").mkdir(parents=True)
                return found

            with mock.patch.object(reader, "_scan", side_effect=replacing):
                self.refused("changed", reader.list_under, self.root)


class CopyUnderTests(ReaderCase):
    def test_copies_with_the_sha_of_the_bytes_written_and_never_over_a_file(self):
        self.write("logo.png", b"\x89PNG-bytes")
        dest = self.outside / "copia.png"
        copied = reader.copy_under(self.root, "logo.png", dest, max_bytes=100)
        self.assertEqual((hashlib.sha256(b"\x89PNG-bytes").hexdigest(), 10), copied)
        self.assertEqual(b"\x89PNG-bytes", dest.read_bytes())
        if POSIX:
            self.assertEqual(0o644, dest.stat().st_mode & 0o777)
        with self.assertRaises(ValueError) as caught:
            reader.copy_under(self.root, "logo.png", dest, max_bytes=100)
        self.assertNotIsInstance(caught.exception, reader.Refused)

    def test_a_file_over_the_ceiling_leaves_no_copy(self):
        self.write("whoosh.wav", b"x" * 20)
        dest = self.outside / "whoosh.wav"
        self.refused("too_big", reader.copy_under, self.root, "whoosh.wav", dest, max_bytes=10)
        real = reader.os.fstat

        def small(fd):
            info = real(fd)
            return os.stat_result((*info[:6], 1, *info[7:]))

        with mock.patch.object(reader.os, "fstat", side_effect=small):
            self.refused("too_big", reader.copy_under, self.root, "whoosh.wav", dest, max_bytes=10)
        self.assertFalse(dest.exists())

    def test_a_destination_it_cannot_create_is_an_io_error(self):
        self.write("a.txt")
        with self.assertRaises(OSError) as caught:
            reader.copy_under(self.root, "a.txt", self.outside / "nao-existe" / "a.txt", max_bytes=100)
        self.assertEqual("IO_ERROR", runtime.error_code_for(caught.exception))

    @needs_symlink
    def test_a_link_is_never_copied(self):
        (self.root / "fora.txt").symlink_to(self.outside / "segredo.txt")
        dest = self.outside / "copia.txt"
        self.refused("link", reader.copy_under, self.root, "fora.txt", dest, max_bytes=100)
        self.assertFalse(dest.exists())

    def test_a_copy_without_dir_fd_and_a_folder_swapped_during_it(self):
        self.write("project/a.txt", b"x")
        with mock.patch.object(reader, "_BY_FD", False):
            self.assertEqual(
                1, reader.copy_under(self.root, "project/a.txt", self.outside / "c.txt", max_bytes=9).bytes
            )
            real = reader._lstat
            calls = []

            def swapped(path, rel):
                calls.append(path)
                info = real(path, rel)
                if len(calls) > FIRST_PASS and path == self.root / "project":
                    return os.stat_result((info.st_mode, info.st_ino + 1, *info[2:]))
                return info

            dest = self.outside / "a.txt"
            with mock.patch.object(reader, "_lstat", side_effect=swapped):
                self.refused("changed", reader.copy_under, self.root, "project/a.txt", dest, max_bytes=9)
        self.assertFalse(dest.exists())

    @staticmethod
    def vanishing_lstat(failure):
        real = reader._lstat
        calls = []

        def vanished(path, rel):
            calls.append(path)
            if len(calls) > FIRST_PASS:
                raise failure
            return real(path, rel)

        return vanished

    def test_a_folder_gone_or_locked_after_the_copy_leaves_no_dest(self):
        for failure in (reader.MissingError("project"), reader.UnreadableError("project", "PermissionError")):
            with self.subTest(failure=type(failure).__name__):
                self.write("project/a.txt", b"x")
                vanished = self.vanishing_lstat(failure)
                dest = self.outside / "gone.txt"
                with (
                    mock.patch.object(reader, "_BY_FD", False),
                    mock.patch.object(reader, "_lstat", side_effect=vanished),
                    self.assertRaises(type(failure)),
                ):
                    reader.copy_under(self.root, "project/a.txt", dest, max_bytes=9)
                self.assertFalse(dest.exists())


class UntrustedTextTests(unittest.TestCase):
    def test_one_clean_paragraph_with_its_origin(self):
        raw = "Título\n\u202eolleh\u202c\tfim\x07 e\u2028mais"
        self.assertEqual(
            {"origin": "hyperframes", "body": "Título olleh fim e mais"},
            reader.untrusted_text(raw, origin="hyperframes"),
        )

    def test_secrets_urls_and_absolute_paths_never_survive(self):
        body = reader.untrusted_text(
            joined("token=", "abc123def456ghi789 em https://exemplo.com/x?k=1 e /srv/cliente-exemplo/x.txt"),
            origin="hyperframes",
        )["body"]
        self.assertNotIn("abc123def456ghi789", body)
        self.assertNotIn("exemplo.com", body)
        self.assertNotIn("/srv/cliente-exemplo", body)

    def test_quoted_secrets_in_json_and_dict_text_never_survive(self):
        for text, secret in (
            ('{"password": "hunter2"}', "hunter2"),
            (joined('{"api_key": ', '"abcd1234efgh"}'), "abcd1234efgh"),
            ("{'secret': 'zzzz9999'}", "zzzz9999"),
        ):
            with self.subTest(text=text):
                self.assertNotIn(secret, reader.untrusted_text(text, origin="hyperframes")["body"])

    def test_the_body_fits_the_limit_in_utf8_bytes(self):
        body = reader.untrusted_text(("ação " * 3000).encode(), origin="hyperframes")["body"]
        self.assertLessEqual(len(body.encode("utf-8")), reader.UNTRUSTED_LIMIT)
        self.assertTrue(body.endswith("…"))
        self.assertEqual("\ufffd", reader.untrusted_text(b"\xff", origin="hyperframes")["body"])

    def test_a_secret_sliding_across_the_edge_never_leaves_a_prefix(self):
        token = joined("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0")
        for offset in range(reader.UNTRUSTED_LIMIT - 120, reader.UNTRUSTED_LIMIT + 40, 4):
            with self.subTest(offset=offset):
                body = reader.untrusted_text("x" * offset + " token " + token + " fim", origin="env1")["body"]
                self.assertNotIn("ghp_", body)
                self.assertNotIn(token[:12], body)
                self.assertLessEqual(len(body.encode("utf-8")), reader.UNTRUSTED_LIMIT)

    def test_a_url_and_a_path_at_the_edge_never_survive(self):
        tails = (
            "https://ex.com/segredo?token=abc",
            "/Volumes/Acervo/cliente/x.mp4",
            "C:\\Users\\cliente\\Acervo\\x.mp4",
        )
        for tail in tails:
            for offset in range(reader.UNTRUSTED_LIMIT - 60, reader.UNTRUSTED_LIMIT + 10, 2):
                with self.subTest(tail=tail, offset=offset):
                    body = reader.untrusted_text("x" * offset + " " + tail, origin="env1")["body"]
                    self.assertNotIn("segredo", body)
                    self.assertNotIn("Acervo", body)

    def test_a_multibyte_character_at_the_edge_never_breaks_utf8(self):
        text = "x" * (reader.UNTRUSTED_LIMIT - 4) + "😀😀😀"
        body = reader.untrusted_text(text, origin="env1")["body"]
        body.encode("utf-8")  # must not raise
        self.assertTrue(body.endswith("…"))
        self.assertLessEqual(len(body.encode("utf-8")), reader.UNTRUSTED_LIMIT)

    def test_a_secret_cut_by_the_input_window_never_leaves_a_prefix_after_the_blanks_collapse(self):
        token = joined("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0")
        window = reader.UNTRUSTED_LIMIT * 4
        for blanks in (window - 6, window - 12, window - 30):
            with self.subTest(blanks=blanks):
                body = reader.untrusted_text("ok" + " " * blanks + token, origin="env1")["body"]
                inside = window - len("ok") - blanks  # characters of the token that the window keeps
                self.assertNotIn(token[:inside], body)
                self.assertNotIn("ghp_", body)
                self.assertTrue(body.startswith("ok"))

    def test_a_cut_secret_never_survives_however_much_the_blanks_collapse(self):
        token = joined("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0")
        window = reader.UNTRUSTED_LIMIT * 4
        for filler in (" ", "\n", "\x07", "\t \x00", "\u200b"):
            for lead in (0, 1500, 5000):
                with self.subTest(filler=filler, lead=lead):
                    text = "ok " * lead + filler * (window - len("ok ") * lead - 9) + token
                    body = reader.untrusted_text(text, origin="env1")["body"]
                    self.assertNotIn("ghp_", body)
                    self.assertNotIn(token[:9], body)

    def test_a_cut_secret_never_survives_when_the_redactor_shortens_what_came_before(self):
        token = joined("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0")
        window = reader.UNTRUSTED_LIMIT * 4
        aws = joined("AKI", "A") + "A" * 16
        cases = {
            "aws key then padding": aws + "x" * 16342 + "," + token + " more",
            "url up to a quote": "https://ex.com/" + "a" * 16347 + '"' + token + " more",
        }
        urls = ",".join('"https://cdn.ex.com/' + "a" * 140 + '"' for _ in range(100))
        head, tail = '{"u":[' + urls + '],"p":"', '","t":"'
        pad = window - 21 - len(head) - len(tail)
        cases["minified json with a hundred urls"] = head + "x" * pad + tail + token + '"} more'
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertGreater(len(text), window)
                body = reader.untrusted_text(text, origin="env1")["body"]
                self.assertNotIn("ghp_", body)
                self.assertNotIn(token[:9], body)
        quoted = '{"client_secret":"' + "Zq9" * 6000 + '"}'
        self.assertNotIn("Zq9", reader.untrusted_text(quoted, origin="env1")["body"])

    def test_a_cut_secret_after_a_spaced_url_list_never_leaves_a_prefix(self):
        token = joined("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0")
        window = reader.UNTRUSTED_LIMIT * 4
        head = ("https://cdn.ex.com/" + "a" * 140 + " ") * 100
        pad = window - 9 - len(head) - 1
        text = head + "x" * pad + " " + token + " more"
        self.assertEqual(window - 9, len(head) + pad + 1)
        body = reader.untrusted_text(text, origin="env1")["body"]
        self.assertNotIn("ghp_", body)
        self.assertTrue(body.startswith("[URL omitida]"))

    def test_a_token_split_by_an_invisible_character_is_still_redacted(self):
        token = joined("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0")
        for invisible in ("\u200b", "\u202e", "\ufeff", "\u2066", "\u00ad", "\x00"):
            with self.subTest(invisible=invisible):
                body = reader.untrusted_text("texto " + token[:4] + invisible + token[4:], origin="env1")["body"]
                self.assertNotIn("ghp_", body)
                self.assertNotIn(token[4:12], body)
        self.assertEqual("a olleh b", reader.untrusted_text("a \u202eolleh\u202c b", origin="env1")["body"])

    def test_a_token_split_at_every_point_by_an_invisible_character_is_redacted(self):
        token = joined("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0")
        for invisible in ("\u200b", "\u202e", "\ufeff", "\u00ad", "\x00"):
            for cut in range(1, len(token)):
                with self.subTest(invisible=invisible, cut=cut):
                    text = "texto " + token[:cut] + invisible + token[cut:] + " fim"
                    body = reader.untrusted_text(text, origin="env1")["body"]
                    self.assertNotIn("ghp_", body)
                    self.assertNotIn(token[4:12], body)

    def test_an_invisible_character_never_hides_a_secret_keyword_from_the_redactor(self):
        value = "Zk3pQ8wLmn4R"
        cases = (
            "x{z}password={v}",
            "x{z}token={v}",
            "x{z}secret={v}",
            "x{z}senha: {v}",
            "x{z}Token: {v}",
            "x{z}Cookie: sid={v}",
            "Bearer{z}{v}",
            "Basic{z}{v}",
            "ok x{z}api_key={v} fim",
            "x{z}Authorization: {v}",
            "x{z}Bearer {v}",
            "x{z}Basic {v}",
            "ab{z}Bearer {v}",
            "x{z}password{z}={v}",
            "x{z}token{z}: {v}",
            "x{z}Authorization{z}: {v}",
            "x{z}Bearer{z}{v}",
        )
        for invisible in ("\u200b", "\u202e", "\ufeff", "\x00"):
            for template in cases:
                with self.subTest(invisible=invisible, template=template):
                    body = reader.untrusted_text(template.format(z=invisible, v=value), origin="env1")["body"]
                    self.assertNotIn(value, body)
                    self.assertNotIn(value[:6], body)

    def test_an_invisible_character_never_glues_a_path_or_a_url_to_the_word_before(self):
        cases = (
            ("x\u200b/Volumes/Acervo/cliente/x.mp4", ("Acervo", "cliente")),
            ("x\u200bhttps://ex.com/segredo", ("segredo",)),
            ("x\x00https://ex.com/segredo", ("segredo",)),
            ("ok\u202e/Volumes/Acervo/cliente/x.mp4", ("Acervo", "cliente")),
            ("x\u200b\u200b\ufeffC:\\Users\\cliente\\Acervo\\x.mp4", ("Acervo", "cliente")),
        )
        for text, secrets in cases:
            with self.subTest(text=text):
                body = reader.untrusted_text(text, origin="env1")["body"]
                for secret in secrets:
                    self.assertNotIn(secret, body)

    def test_a_normal_long_text_still_ends_with_an_ellipsis_within_the_limit(self):
        for text in ("palavra " * 6000, "ação " * 6000):
            with self.subTest(text=text[:8]):
                body = reader.untrusted_text(text, origin="env1")["body"]
                self.assertTrue(body.endswith("…"))
                self.assertLessEqual(len(body.encode("utf-8")), reader.UNTRUSTED_LIMIT)
                self.assertGreater(len(body.encode("utf-8")), reader.UNTRUSTED_LIMIT - 8)

    def test_one_word_longer_than_the_window_has_no_body(self):
        self.assertEqual("", reader.untrusted_text("x" * 20000, origin="env1")["body"])

    def test_the_origin_is_an_id(self):
        for origin in ("", "Hyper Frames", "../x", None):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                reader.untrusted_text("x", origin=cast("str", origin))


if __name__ == "__main__":
    unittest.main()
