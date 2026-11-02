"""Trava da biblioteca: dono pelo inode, recriação sem hardlink, sobras `.stale.*` e leitura do índice."""

import errno
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
import _logs
from _cli import run_cli
from test_library import LibraryBase

from getbrolls import library, runtime
from getbrolls.errors import LockedError

# pylint: disable=protected-access
# `_locked` and `_create_lock` are the lock under test: there is no public seam for the race.


class LockTakeoverBase(LibraryBase):
    def _lock(self, age):
        """`index.lock` with an mtime `age` seconds in the past."""
        path = library.library_dir() / "index.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        old = time.time() - age
        os.utime(path, (old, old))
        return path

    def _graves(self):
        return sorted(p.name for p in library.library_dir().glob("index.lock.stale.*"))

    def _renewed_on_rename(self, later_stat=None):
        """`Path.stat` where the renamed lock is fresh; later grave stats call `later_stat`."""
        real_stat = Path.stat
        seen = []

        def stat(self, *args, **kwargs):
            if ".stale." not in self.name:
                return real_stat(self, *args, **kwargs)
            seen.append(self)
            if len(seen) == 1:
                os.utime(self, None)
            elif later_stat is not None:
                later_stat()
            return real_stat(self, *args, **kwargs)

        return stat, seen


class RecreationFailureTests(LockTakeoverBase):
    """Only `FileExistsError` means "the name is taken"; any other failure never enters."""

    def test_a_failed_second_look_at_the_renamed_lock_does_not_let_this_process_in(self):
        path = self._lock(library.STALE_LOCK_S + 30)

        def eio():
            raise OSError(errno.EIO, "Input/output error")

        stat, seen = self._renewed_on_rename(later_stat=eio)
        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.2),
            patch.object(Path, "stat", stat),
            patch.object(library.effects, "link", side_effect=OSError(errno.EPERM, "no hardlinks")),
            self.assertLogs("getbrolls.library", level="WARNING") as logged,
            self.assertRaises(LockedError),
        ):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertTrue(seen, "the takeover must have happened")
        self.assertFalse(library.index_path().exists(), "the other holder's lock was displaced: nobody may enter")
        self.assertTrue(path.exists(), "the other holder's lock is back under its name")
        events = _logs.events(logged.output, "library_lock")
        self.assertEqual(1, len([e for e in events if e.get("fresh_lock_restored")]), events)
        self.assertEqual([], [e for e in events if e.get("fresh_lock_kept")], events)

    def test_a_recreation_that_fails_for_another_reason_waits_and_never_takes_the_empty_name(self):
        path = self._lock(library.STALE_LOCK_S + 30)
        stat, _seen = self._renewed_on_rename()
        real_create = library._create_lock
        failed = []

        def create(target):
            if self._graves() and not failed:
                # The recreation of the other holder's lock fails with EIO; later attempts would succeed.
                failed.append(target)
                raise OSError(errno.EIO, "Input/output error")
            return real_create(target)

        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.2),
            patch.object(Path, "stat", stat),
            patch.object(library.effects, "link", side_effect=OSError(errno.EPERM, "no hardlinks")),
            patch.object(library, "_create_lock", create),
            self.assertLogs("getbrolls.library", level="WARNING") as logged,
            self.assertRaisesRegex(LockedError, "Input/output error"),
        ):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertEqual([path], failed)
        self.assertFalse(library.index_path().exists(), "entering here runs alongside the displaced holder")
        self.assertFalse(path.exists(), "nothing was created under the name after the failed recreation")
        self.assertEqual(1, len(self._graves()), "the other holder's lock is set aside, never deleted")
        aside = [e for e in _logs.events(logged.output, "library_lock") if e.get("fresh_lock_aside")]
        self.assertEqual(1, len(aside))
        self.assertEqual("Input/output error", aside[0]["reason"])


class ReleaseOwnershipTests(LockTakeoverBase):
    """The release removes only the lock this process created (same `st_dev`/`st_ino`)."""

    def test_the_release_keeps_a_lock_that_another_process_put_under_the_name(self):
        path = library.library_dir() / "index.lock"
        with (
            self.assertLogs("getbrolls.library", level="WARNING") as logged,
            library._locked(),
        ):
            # A third process displaced this lock and now holds its own under the same name.
            path.replace(path.with_name("index.lock.stale.1.aaaaaaaa"))
            other = path.with_name("index.lock.other")
            other.write_text("", encoding="utf-8")
            other.replace(path)
            theirs = path.stat()
        self.assertTrue(path.exists(), "the other process's lock must survive this release")
        self.assertEqual((theirs.st_dev, theirs.st_ino), (path.stat().st_dev, path.stat().st_ino))
        kept = [e for e in _logs.events(logged.output, "library_lock") if e.get("foreign_lock_kept")]
        self.assertEqual(1, len(kept))

    def test_the_release_removes_its_own_lock_even_after_a_hardlink_restore(self):
        path = library.library_dir() / "index.lock"
        with library._locked():
            grave = path.with_name("index.lock.stale.1.bbbbbbbb")
            path.replace(grave)
            os.link(grave, path)
            grave.unlink()
        self.assertFalse(path.exists())

    def test_the_release_of_a_lock_already_gone_is_quiet(self):
        path = library.library_dir() / "index.lock"
        with library._locked():
            path.unlink()
        self.assertFalse(path.exists())


class OwnerlessRecreationTests(LockTakeoverBase):
    """A recreated lock keeps the original age, so an ownerless one expires like the original."""

    def test_a_recreated_lock_keeps_the_age_of_the_lock_it_replaces_and_later_expires(self):
        path = self._lock(library.STALE_LOCK_S + 30)
        real_stat = Path.stat
        renewed = time.time() - 30
        seen = []

        def stat(self, *args, **kwargs):
            if ".stale." in self.name and not seen:
                seen.append(self)
                os.utime(self, (renewed, renewed))
            return real_stat(self, *args, **kwargs)

        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.05),
            patch.object(Path, "stat", stat),
            patch.object(library.effects, "link", side_effect=OSError(errno.EPERM, "no hardlinks")),
            self.assertLogs("getbrolls.library", level="WARNING"),
            self.assertRaises(LockedError),
        ):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertAlmostEqual(renewed, path.stat().st_mtime, delta=1.0)
        # Nobody owns it (its holder released another inode): once its original age passes the limit, it is taken.
        old = time.time() - library.STALE_LOCK_S - 1
        os.utime(path, (old, old))
        library.learn_query("foguete decolando", "youtube", "hit")
        self.assertFalse(path.exists())
        self.assertTrue(library.index_path().exists())


class StaleLeftoverSweepTests(LockTakeoverBase):
    """Sobras `index.lock.stale.*` velhas saem com a trava na mão; as recentes ficam (outro processo na troca)."""

    def _leftover(self, name, age):
        path = library.library_dir() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        old = time.time() - age
        os.utime(path, (old, old))
        return path

    def test_old_leftovers_are_swept_and_recent_ones_are_kept(self):
        old = self._leftover("index.lock.stale.11.aaaaaaaa", library.STALE_GRAVE_S + 60)
        recent = self._leftover("index.lock.stale.12.bbbbbbbb", library.STALE_LOCK_S - 10)
        between = self._leftover("index.lock.stale.13.cccccccc", library.STALE_LOCK_S + 5)
        unrelated = self._leftover("index.lock.other", library.STALE_GRAVE_S + 60)
        library.learn_query("foguete decolando", "youtube", "hit")
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists(), "a recent leftover may be in the middle of another process's takeover")
        self.assertTrue(between.exists())
        self.assertTrue(unrelated.exists())

    def test_a_folder_with_a_leftover_name_is_never_touched(self):
        folder = library.library_dir() / "index.lock.stale.14.dddddddd"
        folder.mkdir(parents=True)
        old = time.time() - library.STALE_GRAVE_S - 60
        os.utime(folder, (old, old))
        library.learn_query("foguete decolando", "youtube", "hit")
        self.assertTrue(folder.is_dir())

    @unittest.skipUnless(hasattr(os, "symlink") and os.utime in os.supports_follow_symlinks, "needs symlinks")
    def test_a_link_with_a_leftover_name_is_never_removed(self):
        target = library.library_dir() / "kept.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("kept", encoding="utf-8")
        link = library.library_dir() / "index.lock.stale.18.aaaabbbb"
        link.symlink_to(target)
        old = time.time() - library.STALE_GRAVE_S - 60
        os.utime(link, (old, old), follow_symlinks=False)
        library.learn_query("foguete decolando", "youtube", "hit")
        self.assertTrue(link.is_symlink(), "an old link under a leftover name is not a regular file")
        self.assertEqual("kept", target.read_text(encoding="utf-8"))

    def test_a_leftover_that_cannot_be_removed_does_not_fail_the_write(self):
        old = self._leftover("index.lock.stale.15.eeeeeeee", library.STALE_GRAVE_S + 60)
        real_unlink = library.effects.unlink

        def unlink(path, *args, **kwargs):
            if ".stale." in Path(path).name:
                raise PermissionError(errno.EACCES, "Permission denied")
            return real_unlink(path, *args, **kwargs)

        with patch.object(library.effects, "unlink", unlink):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertTrue(old.exists())
        self.assertTrue(library.index_path().exists())

    def test_a_folder_that_cannot_be_listed_does_not_fail_the_write(self):
        old = self._leftover("index.lock.stale.17.abababab", library.STALE_GRAVE_S + 60)
        with patch.object(Path, "glob", side_effect=PermissionError(errno.EACCES, "Permission denied")):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertTrue(old.exists())
        self.assertTrue(library.index_path().exists())

    def test_the_sweep_runs_only_while_holding_the_lock(self):
        old = self._leftover("index.lock.stale.16.ffffffff", library.STALE_GRAVE_S + 60)
        self._lock(1)
        with patch.object(library, "LOCK_TIMEOUT_S", 0.05), self.assertRaises(LockedError):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertTrue(old.exists())


class IndexReadRefusalTests(LibraryBase):
    """`load_index` lê pelo `json_read`: arquivo hostil é a mesma recusa de JSON inválido, nunca INTERNAL_ERROR."""

    HOSTILE = (
        ("deep", b"[" * 100_000 + b"]" * 100_000),
        ("not_utf8", b'{"schema_version": 2, "queries": [], "x": "\xff"}'),
        ("nan", b'{"schema_version": 2, "assets": [], "queries": [], "providers": {}, "preferences": [], "n": NaN}'),
        ("lone_surrogate", b'{"schema_version": 2, "preferences": ["\\ud800"]}'),
    )

    def _write(self, raw):
        path = library.index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_a_hostile_index_is_refused_as_invalid_json_and_left_untouched(self):
        for name, raw in self.HOSTILE:
            with self.subTest(case=name):
                path = self._write(raw)
                with self.assertRaisesRegex(ValueError, "está com JSON inválido"):
                    library.load_index()
                self.assertEqual(raw, path.read_bytes())

    def test_a_too_large_index_is_refused_without_reading_it_whole(self):
        path = self._write(b"[" + b" " * 64 + b"]")
        with patch.object(library.json_read, "MAX_BYTES", 32), self.assertRaisesRegex(ValueError, "JSON inválido"):
            library.load_index()
        self.assertTrue(path.exists())

    def test_library_search_on_a_too_deep_index_is_invalid_data(self):
        self._write(self.HOSTILE[0][1])
        error = run_cli(
            "library", "--search", "foguete", project=self.project, env={"GB_HOME": str(self.home)}, expect=1
        )
        self.assertEqual("INVALID_DATA", error["error_code"])

    def test_hints_turn_a_hostile_index_into_the_unreadable_warning(self):
        self._write(self.HOSTILE[2][1])
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        try:
            self.assertEqual([], library.hints("foguete"))
        finally:
            runtime.ACTIVE.reset(token)
        self.assertEqual(["LIBRARY_INDEX_UNREADABLE"], [w["code"] for w in event["warnings"]])

    def test_a_valid_index_loads_and_saves_byte_identical(self):
        library.learn_query("foguete decolando", "youtube", "hit")
        library.learn_preference("Prefiro plano aberto de foguete, sem corte seco.")
        before = library.index_path().read_bytes()
        library.save_index(library.load_index())
        self.assertEqual(before, library.index_path().read_bytes())
        self.assertEqual(json.loads(before), library.load_index())


if __name__ == "__main__":
    unittest.main()
