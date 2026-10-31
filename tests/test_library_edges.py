"""Biblioteca global: recusas de entrada, índice inválido, biblioteca desligada e trava velha."""

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
from test_library import LibraryBase, approved_candidate

from getbrolls import labels, library, runtime
from getbrolls.errors import LockedError
from getbrolls.memory import remember
from getbrolls.sdk import schemas


class LibraryInputRefusalTests(LibraryBase):
    def test_learn_query_refuses_empty_query_bad_outcome_and_empty_provider(self):
        cases = (
            (("  ", "youtube", "hit"), "--query"),
            (("foguete", "youtube", "talvez"), "hit ou miss"),
            (("foguete", "   ", "hit"), "--provider"),
        )
        for args, fragment in cases:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, fragment):
                library.learn_query(*args)
        self.assertFalse(library.index_path().exists())

    def test_learn_preference_refuses_text_shorter_than_five_characters(self):
        with self.assertRaisesRegex(ValueError, "5 caracteres"):
            library.learn_preference(" abc ")
        self.assertFalse(library.index_path().exists())

    def test_search_refuses_an_empty_term(self):
        with self.assertRaisesRegex(ValueError, "--search"):
            library.search("   ")


class LibraryOffTests(LibraryBase):
    def setUp(self):
        super().setUp()
        os.environ["GB_LIBRARY"] = "off"

    def test_learn_preference_when_off_writes_nothing_and_says_so(self):
        with self.assertLogs("getbrolls.library", level="INFO") as cm:
            answer = library.learn_preference("Prefiro plano aberto de foguete.", by="Bruno")
        self.assertFalse(answer["enabled"])
        self.assertIsNone(answer["entry"])
        self.assertEqual(library.OFF_NOTE, answer["note"])
        self.assertTrue(answer["rights_not_transferable"])
        self.assertFalse(library.index_path().exists())
        event = _logs.events(cm.output, "library")[0]
        self.assertEqual(("learn", "0", "True"), (event["action"], event["entries"], event["disabled"]))

    def test_learn_from_candidate_when_off_validates_first_then_writes_nothing(self):
        ledger, c = approved_candidate(self.project)
        # Sem decisão registrada a recusa vem antes de a biblioteca desligada importar.
        with self.assertRaisesRegex(ValueError, "remember"):
            library.learn_from_candidate(self.project, c["id"])
        remember(ledger, c, "approved", "Plano aberto exato da fala.", "Bruno")
        with self.assertLogs("getbrolls.library", level="INFO") as cm:
            answer = library.learn_from_candidate(self.project, c["id"])
        self.assertIsNone(answer["entry"])
        self.assertFalse(answer["enabled"])
        self.assertFalse(library.index_path().exists())
        self.assertEqual("True", _logs.events(cm.output, "library")[0]["disabled"])

    def test_hints_when_off_is_empty_and_logged_as_disabled(self):
        with self.assertLogs("getbrolls.library", level="INFO") as cm:
            self.assertEqual([], library.hints("foguete"))
        event = _logs.events(cm.output, "library")[0]
        self.assertEqual(("hints", "0", "True"), (event["action"], event["entries"], event["disabled"]))


class LibraryIndexValidityTests(LibraryBase):
    def _write_index(self, text):
        path = library.index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_an_index_that_is_not_an_object_is_refused_and_left_untouched(self):
        path = self._write_index("[1, 2]")
        with self.assertRaisesRegex(ValueError, "não é uma biblioteca da versão 2"):
            library.learn_query("foguete", "youtube", "hit")
        self.assertEqual("[1, 2]", path.read_text(encoding="utf-8"))

    def test_an_index_from_a_newer_version_is_refused_and_left_untouched(self):
        text = json.dumps({"schema_version": 9})
        path = self._write_index(text)
        with self.assertRaisesRegex(ValueError, "mais nova"):
            library.load_index()
        self.assertEqual(text, path.read_text(encoding="utf-8"))

    def _assert_not_a_regular_file(self, path):
        with self.assertRaises(OSError) as caught:
            library.load_index()
        message = labels.os_message(caught.exception)
        self.assertIn("não é um arquivo comum", message)
        self.assertNotIn("JSON inválido", message)
        self.assertNotIn("apague", message)
        error = run_cli(
            "library", "--search", "foguete", project=self.project, env={"GB_HOME": str(self.home)}, expect=1
        )
        self.assertEqual("IO_ERROR", error["error_code"])
        self.assertIn("não é um arquivo comum", error["error"])
        self.assertNotIn("apague", error["error"])
        self.assertTrue(path.exists())

    def test_a_directory_at_the_index_path_is_an_io_problem_not_invalid_json(self):
        path = library.index_path()
        path.mkdir(parents=True)
        self._assert_not_a_regular_file(path)
        self.assertTrue(path.is_dir())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO só existe em POSIX")
    def test_a_fifo_at_the_index_path_is_an_io_problem_not_invalid_json(self):
        path = library.index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(path)
        self._assert_not_a_regular_file(path)

    def test_a_second_distinct_asset_is_appended_instead_of_merged(self):
        ledger_a, a = approved_candidate(self.project, "https://www.youtube.com/watch?v=aaa")
        remember(ledger_a, a, "approved", "Plano aberto.", "Bruno")
        first = library.learn_from_candidate(self.project, a["id"])
        other = self.project.parent / "other"
        other.mkdir()
        ledger_b, b = approved_candidate(other, "https://www.youtube.com/watch?v=bbb")
        remember(ledger_b, b, "approved", "Outro plano.", "Bruno")
        second = library.learn_from_candidate(other, b["id"])
        self.assertNotEqual(first["entry"]["asset_id"], second["entry"]["asset_id"])
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual(2, len(data["assets"]))
        self.assertEqual([1, 1], [len(x["used_by"]) for x in data["assets"]])


MAX_PROBES = 2000


class LibraryLockTests(LibraryBase):
    def _lock(self):
        path = library.library_dir() / "index.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return path

    def test_a_stale_lock_left_by_a_dead_process_is_removed_and_the_write_goes_on(self):
        path = self._lock()
        old = time.time() - library.STALE_LOCK_S - 30
        os.utime(path, (old, old))
        library.learn_query("foguete decolando", "youtube", "hit")
        self.assertFalse(path.exists())
        data = json.loads(library.index_path().read_text(encoding="utf-8"))
        self.assertEqual("foguete decolando", data["queries"][0]["query"])

    def test_a_lock_that_cannot_be_inspected_counts_as_fresh_and_ends_in_locked(self):
        path = self._lock()
        real_stat = Path.stat

        def stat(self, *args, **kwargs):
            if self.name == "index.lock":
                raise PermissionError("sem acesso")
            return real_stat(self, *args, **kwargs)

        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.05),
            patch.object(Path, "stat", stat),
            self.assertLogs("getbrolls.library", level="WARNING") as cm,
            self.assertRaisesRegex(LockedError, "travado"),
        ):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertTrue(path.exists())
        self.assertFalse(library.index_path().exists())
        event = _logs.events(cm.output, "library_lock")[0]
        self.assertEqual("True", event["timeout"])

    def test_a_stale_lock_that_cannot_be_removed_ends_in_locked_and_does_not_spin(self):
        path = self._lock()
        old = time.time() - library.STALE_LOCK_S - 30
        os.utime(path, (old, old))
        real_stat, real_unlink, real_rename = Path.stat, Path.unlink, Path.rename
        probes = []

        def stat(self, *args, **kwargs):
            if self.name == "index.lock":
                probes.append(1)
                # A regression spins here without ever reaching the deadline: stop it, bounded.
                if len(probes) > MAX_PROBES:
                    raise AssertionError("busy-spin: the loop never reached the deadline")
            return real_stat(self, *args, **kwargs)

        def unlink(self, *args, **kwargs):
            if self.name.startswith("index.lock"):
                raise PermissionError(errno.EROFS, "Read-only file system")
            return real_unlink(self, *args, **kwargs)

        def rename(self, *args, **kwargs):
            if self.name.startswith("index.lock"):
                raise PermissionError(errno.EROFS, "Read-only file system")
            return real_rename(self, *args, **kwargs)

        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.2),
            patch.object(Path, "stat", stat),
            patch.object(Path, "unlink", unlink),
            patch.object(Path, "rename", rename),
            patch.object(Path, "replace", rename),
            self.assertRaisesRegex(LockedError, "travado.*Read-only file system") as raised,
        ):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertIn("index.lock", str(raised.exception))
        self.assertLess(len(probes), 500, "the wait must sleep between probes")
        self.assertTrue(path.exists())
        self.assertFalse(library.index_path().exists())

    def test_the_locked_text_is_unchanged_when_no_removal_failed(self):
        path = self._lock()
        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.0),
            self.assertRaises(LockedError) as raised,
            library._locked(),  # pylint: disable=protected-access
        ):
            pass
        self.assertEqual(
            f"{library.labels.shown(path)} está travado há tempo demais por outro getbrolls. "
            "Espere a outra execução terminar ou apague esse arquivo.",
            str(raised.exception),
        )

    def test_taking_over_a_stale_lock_never_removes_a_fresh_lock_created_meanwhile(self):
        path = self._lock()
        old = time.time() - library.STALE_LOCK_S - 30
        os.utime(path, (old, old))
        real_stat = Path.stat
        swapped = []

        def stat(self, *args, **kwargs):
            seen = real_stat(self, *args, **kwargs)
            if self.name == "index.lock" and not swapped:
                # Another process wins the takeover and holds a fresh lock before this one acts on the stale reading.
                swapped.append(1)
                real_replace = Path.replace
                fresh = self.with_name("index.lock.other")
                fresh.write_text("", encoding="utf-8")
                real_replace(fresh, self)
            return seen

        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.2),
            patch.object(Path, "stat", stat),
            self.assertRaises(LockedError),
        ):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertTrue(path.exists(), "the fresh lock of the other process must survive")
        self.assertLess(time.time() - path.stat().st_mtime, library.STALE_LOCK_S)
        self.assertFalse(library.index_path().exists())
        self.assertEqual(["index.lock"], [p.name for p in path.parent.glob("index.lock*")])

    def _takeover_of_a_fresh_lock(self, link):
        """Runs `learn_query` on a lock that looks stale but is renewed once renamed; `link` stands in for hardlinks."""
        path = self._lock()
        old = time.time() - library.STALE_LOCK_S - 30
        os.utime(path, (old, old))
        real_stat = Path.stat
        swapped = []

        def stat(self, *args, **kwargs):
            seen = real_stat(self, *args, **kwargs)
            if ".stale." in self.name and not swapped:
                # The renamed file is another process's fresh lock: the stale reading was outdated.
                swapped.append(self)
                os.utime(self, None)
                return real_stat(self, *args, **kwargs)
            return seen

        with (
            patch.object(library, "LOCK_TIMEOUT_S", 0.2),
            patch.object(Path, "stat", stat),
            patch.object(library.effects, "link", side_effect=link),
            self.assertLogs("getbrolls.library", level="WARNING") as logged,
            self.assertRaises(library.LockedError),
        ):
            library.learn_query("foguete decolando", "youtube", "hit")
        self.assertEqual(1, len(swapped))
        return path, _logs.events(logged.output, "library_lock")

    def test_a_fresh_lock_goes_back_to_its_name_when_hardlinks_fail_during_the_takeover(self):
        path, events = self._takeover_of_a_fresh_lock(OSError(errno.EPERM, "no hardlinks"))
        # The other process still holds the lock under its name, so this one waits instead of entering.
        self.assertLess(time.time() - path.stat().st_mtime, library.STALE_LOCK_S)
        self.assertEqual([], list(path.parent.glob("index.lock.stale.*")))
        restored = [e for e in events if e.get("fresh_lock_restored")]
        self.assertEqual(1, len(restored), events)
        self.assertEqual("no hardlinks", restored[0]["reason"])

    def test_a_fresh_lock_is_kept_aside_when_a_third_process_took_the_name(self):
        def third_takes_the_name(_src, dst, **_kwargs):
            Path(dst).touch()
            raise OSError(errno.EPERM, "no hardlinks")

        path, events = self._takeover_of_a_fresh_lock(third_takes_the_name)
        self.assertTrue(path.exists())
        graves = list(path.parent.glob("index.lock.stale.*"))
        self.assertEqual(1, len(graves), "the other process's lock is never deleted")
        kept = [e for e in events if e.get("fresh_lock_kept")]
        self.assertEqual(1, len(kept), events)


class LibraryIndexValueTypeTests(LibraryBase):
    BAD = (
        {"queries": None},
        {"assets": None},
        {"preferences": "x"},
        {"providers": []},
        {"queries": ["x"]},
        {"assets": [1]},
        {"preferences": [None]},
        {"queries": [{"query": "foguete"}]},
        {"providers": {"youtube": []}},
        {"providers": {"youtube": {"outcomes": None, "last_at": None}}},
    )

    def _write(self, extra):
        path = library.index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": schemas.version("library_index"), **extra}), encoding="utf-8")

    def test_load_index_refuses_wrong_value_types_as_an_invalid_library(self):
        for extra in self.BAD:
            with self.subTest(extra=extra):
                self._write(extra)
                with self.assertRaisesRegex(ValueError, "não é uma biblioteca"):
                    library.load_index()

    def test_hints_turn_a_wrongly_typed_index_into_the_unreadable_warning(self):
        for extra in self.BAD:
            with self.subTest(extra=extra):
                self._write(extra)
                event = {"warnings": [], "state_committed": False}
                token = runtime.ACTIVE.set(event)
                try:
                    result = library.hints("foguete")
                finally:
                    runtime.ACTIVE.reset(token)
                self.assertEqual([], result)
                self.assertEqual(["LIBRARY_INDEX_UNREADABLE"], [w["code"] for w in event["warnings"]])

    def test_library_search_on_a_wrongly_typed_index_is_invalid_data_not_an_internal_error(self):
        self._write({"queries": None})
        error = run_cli(
            "library", "--search", "foguete", project=self.project, env={"GB_HOME": str(self.home)}, expect=1
        )
        self.assertEqual("INVALID_DATA", error["error_code"])

    def test_a_well_formed_index_with_complete_entries_still_loads(self):
        library.learn_query("foguete decolando", "youtube", "hit")
        library.learn_preference("Prefiro plano aberto de foguete.")
        data = library.load_index()
        self.assertEqual(1, len(data["queries"]))
        self.assertEqual({"youtube"}, set(data["providers"]))


if __name__ == "__main__":
    unittest.main()
