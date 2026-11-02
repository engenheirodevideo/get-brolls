"""`analysis/`: recusas de gravação, índice ou componente corrompido, corrida e `check` sobre lixo no disco."""

import errno
import hashlib
import io
import json
import os
import shutil
import types
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import test_analysis_store as store
from _logs import LogCase
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import analysis
from getbrolls import analysis_contract as ac

PRODUCER = store.PRODUCER
# Os testes que forçam a gravação por `dir_fd` só valem onde `os.open`, `mkdir`, `rename` e `unlink` o aceitam
# (o Windows não): lá a gravação por `lstat` é a que roda, e tem os testes dela.
HAS_DIR_FD = all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.rename, os.unlink))
DIR_FD_REASON = "este sistema não aceita dir_fd em os.open, os.mkdir, os.rename e os.unlink"


def files_under(folder):
    return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*") if p.is_file())


class WriteFailureTests(store.StoreTestCase):
    """Falhas no meio da troca do arquivo: nada fica pela metade."""

    def setUp(self):
        super().setUp()
        self.media_id = self.register()["media_id"]
        self.target = self.project / "analysis" / "media" / self.media_id / "transcript.json"

    def write(self):
        return analysis.write_component(
            self.project, self.media_id, "transcript", store.transcript(), producer=PRODUCER
        )

    def assert_clean_failure(self):
        self.assertFalse(self.target.exists())
        leftovers = [name for name in files_under(self.project) if name.endswith(".tmp")]
        self.assertEqual([], leftovers)

    @unittest.skipUnless(HAS_DIR_FD, DIR_FD_REASON)
    def test_failed_rename_removes_the_temp_file_and_reraises(self):
        with (
            mock.patch.object(analysis, "_dir_fd_available", return_value=True),
            mock.patch.object(analysis.os, "rename", side_effect=OSError(errno.EXDEV, "cross-device")),
            self.assertRaises(OSError) as raised,
        ):
            self.write()
        self.assertEqual(errno.EXDEV, raised.exception.errno)
        self.assert_clean_failure()

    def test_failed_replace_without_dir_fd_removes_the_temp_file(self):
        with (
            mock.patch.object(analysis, "_dir_fd_available", return_value=False),
            mock.patch.object(Path, "replace", side_effect=OSError(errno.EXDEV, "cross-device")),
            self.assertRaises(OSError),
        ):
            self.write()
        self.assert_clean_failure()

    def test_folder_swapped_during_the_write_is_refused_without_dir_fd(self):
        """A identidade de uma pasta do caminho mudou entre a ida e a conferência: recusa e não troca o arquivo."""
        real = analysis._checked_dir  # pylint: disable=protected-access

        def swapped(path, rel, create):
            identity = real(path, rel, create)
            return identity if create else (identity[0], identity[1] + 1)

        with (
            mock.patch.object(analysis, "_dir_fd_available", return_value=False),
            mock.patch.object(analysis, "_checked_dir", side_effect=swapped),
            self.assertRaisesRegex(ValueError, "link"),
        ):
            self.write()
        self.assert_clean_failure()

    def test_other_oserror_while_walking_is_not_taken_for_a_link(self):
        real_open = os.open

        def deny_media(path, flags, *args, **kwargs):
            if path == "media" and "dir_fd" in kwargs:
                raise PermissionError(errno.EACCES, "negado")
            return real_open(path, flags, *args, **kwargs)

        with (
            mock.patch.object(analysis, "_dir_fd_available", return_value=True),
            mock.patch.object(analysis.os, "open", side_effect=deny_media),
            self.assertRaises(PermissionError),
        ):
            self.write()
        self.assert_clean_failure()

    @unittest.skipUnless(HAS_DIR_FD, DIR_FD_REASON)
    def test_child_that_opens_but_is_not_a_directory_is_refused(self):
        """Sem `O_DIRECTORY` (plataforma que não tem), `media` como arquivo abre; a conferência recusa."""
        media = self.project / "analysis" / "media"
        shutil.rmtree(media)
        media.write_text("não sou pasta", encoding="utf-8")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        with (
            mock.patch.object(analysis, "_DIR_FLAGS", flags),
            mock.patch.object(analysis, "_dir_fd_available", return_value=True),
            self.assertRaisesRegex(ValueError, "link ou não é uma pasta comum"),
        ):
            self.write()
        self.assertEqual("não sou pasta", media.read_text(encoding="utf-8"))
        self.assertEqual([], [n for n in files_under(self.project) if n.endswith(".tmp")])

    def test_output_over_the_size_limit_is_refused_and_nothing_is_written(self):
        words = [
            {"text": f"p{n}", "start": n, "end": n + 0.5, "probability": None, "speaker": None} for n in range(200)
        ]
        with (
            mock.patch.object(analysis.contract, "MAX_FILE", 4096),
            self.assertRaisesRegex(ValueError, "nada foi gravado"),
        ):
            analysis.write_component(
                self.project, self.media_id, "transcript", store.transcript(words=words), producer=PRODUCER
            )
        self.assert_clean_failure()
        self.assertEqual(
            ["analysis/index.json", f"analysis/media/{self.media_id}/media.json"],
            [n for n in files_under(self.project) if n.startswith("analysis/") and not n.endswith(".lock")],
        )


class IndexAndContractRefusalTests(store.StoreTestCase, LogCase):
    def index_path(self):
        return self.project / "analysis" / "index.json"

    def test_unusable_index_is_refused_for_register_and_list_and_stays_untouched(self):
        self.register()
        for raw in ("{", json.dumps({"media": "nada"})):
            with self.subTest(raw=raw):
                self.index_path().write_text(raw, encoding="utf-8")
                with self.captured("WARNING") as manager:
                    for call in (self.register, lambda: analysis.listing(self.project)):
                        with self.assertRaisesRegex(ValueError, "não dá para usar"):
                            call()
                row = self.rows(manager).all("analysis_refused")[0]
                self.assertEqual("index_unusable", row["reason"])
                self.assertEqual(raw, self.index_path().read_text(encoding="utf-8"))

    def test_match_with_unusable_index_reports_absent(self):
        self.register()
        self.index_path().write_text("{", encoding="utf-8")
        self.assertEqual(("absent", None), analysis.match(self.project, "aroll/c01.mp4"))
        self.assertIsNone(analysis.lookup(self.project, "aroll/c01.mp4"))

    def test_match_of_a_deleted_media_is_stale_with_its_entry(self):
        self.register()
        self.clip.unlink()
        state, entry = analysis.match(self.project, "aroll/c01.mp4")
        self.assertEqual("stale", state)
        assert entry is not None
        self.assertEqual("aroll/c01.mp4", entry["path"])

    def test_index_that_breaks_the_contract_is_not_written(self):
        self.register()
        before = self.index_path().read_text(encoding="utf-8")
        index = self.index()
        index["media"].append(dict(index["media"][0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"):
            analysis._write_index(self.project, index)  # pylint: disable=protected-access
        self.assertEqual(before, self.index_path().read_text(encoding="utf-8"))

    def test_media_doc_that_breaks_the_contract_is_refused_before_any_write(self):
        facts = dict.fromkeys(("duration_s", "fps", "width", "height", "has_audio", "video_codec", "audio_codec"))
        with (
            mock.patch.object(analysis, "_probe", return_value=("failed", "", facts)),
            self.captured("WARNING") as manager,
            self.assertRaisesRegex(ValueError, "MISSING_REASON"),
        ):
            analysis.ensure_media(self.project, "aroll/c01.mp4")
        row = self.rows(manager).one("analysis_refused", reason="contract")
        self.assertEqual("MISSING_REASON", row["code"])
        self.assertFalse(self.index_path().exists())
        self.assertEqual([], [n for n in files_under(self.project) if n.startswith("analysis/media")])


class ProbeFactsTests(store.StoreTestCase):
    def facts(self, answer):
        with mock.patch.object(analysis.media, "run", return_value=json.dumps(answer)):
            done = analysis.ensure_media(self.project, "aroll/c01.mp4")
        return ac.read_json(self.project, f"analysis/media/{done['media_id']}/media.json")

    def test_unusable_facts_become_null(self):
        doc = self.facts(
            {
                "format": {"duration": "não é número"},
                "streams": [{"codec_type": "video", "avg_frame_rate": "0/0", "width": True, "height": 0}],
            }
        )
        self.assertEqual("done", doc["status"])
        for field in ("duration_s", "fps", "width", "height", "video_codec", "audio_codec"):
            self.assertIsNone(doc[field], field)
        self.assertFalse(doc["has_audio"])

    def test_missing_or_non_finite_duration_is_null(self):
        answers = [{"streams": []}] + [
            {"format": {"duration": raw}, "streams": []} for raw in ("nan", "inf", "-inf", "Infinity", "-1")
        ]
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertIsNone(self.facts(answer)["duration_s"])
                self.clip.write_bytes(os.urandom(64))  # outro conteúdo: novo registro

    def test_duration_falls_back_to_the_video_stream(self):
        doc = self.facts({"streams": [{"codec_type": "video", "duration": "3.14159"}]})
        self.assertEqual(3.142, doc["duration_s"])


class QuickKeyTests(store.StoreTestCase):
    def info(self):
        return os.lstat(self.clip)

    def test_key_hashes_the_sample(self):
        key = analysis.quick_key(self.clip, self.info(), "aroll/c01.mp4")
        self.assertEqual(self.clip.stat().st_size, key["size"])
        self.assertEqual(hashlib.sha256(self.clip.read_bytes()).hexdigest(), key["sample_sha256"])

    def test_unopenable_file_is_refused(self):
        with self.assertRaisesRegex(ValueError, "Não consegui ler aroll/c01.mp4"):
            analysis.quick_key(self.project / "aroll" / "nada.mp4", self.info(), "aroll/c01.mp4")

    def test_file_replaced_by_another_one_is_refused(self):
        other = self.project / "aroll" / "c02.mp4"
        other.write_bytes(b"outro arquivo")
        with self.assertRaisesRegex(ValueError, "mudou durante a leitura"):
            analysis.quick_key(self.clip, os.lstat(other), "aroll/c01.mp4")

    def test_read_error_is_refused(self):
        with (
            mock.patch.object(analysis, "_read_at", side_effect=OSError(errno.EIO, "io")),
            self.assertRaisesRegex(ValueError, "Não consegui ler"),
        ):
            analysis.quick_key(self.clip, self.info(), "aroll/c01.mp4")

    def test_file_that_shrinks_while_reading_hashes_what_was_read(self):
        with mock.patch.object(analysis.os, "read", return_value=b""):
            key = analysis.quick_key(self.clip, self.info(), "aroll/c01.mp4")
        self.assertEqual(hashlib.sha256(b"").hexdigest(), key["sample_sha256"])


class RegisterRaceTests(store.StoreTestCase, LogCase):
    def test_unreadable_file_during_the_full_hash_is_refused_and_nothing_is_indexed(self):
        with (
            mock.patch.object(analysis.ledger, "digest", side_effect=OSError(errno.EIO, "io")),
            self.assertRaisesRegex(ValueError, "Não consegui ler"),
        ):
            self.register()
        self.assertFalse((self.project / "analysis" / "index.json").exists())

    def test_file_changed_during_the_full_hash_is_refused(self):
        real = analysis.ledger.digest

        def growing(path):
            sha = real(path)
            with Path(path).open("ab") as stream:
                stream.write(b"mais bytes")
            return sha

        with (
            mock.patch.object(analysis.ledger, "digest", side_effect=growing),
            self.captured("WARNING") as manager,
            self.assertRaisesRegex(ValueError, "mudou enquanto era lido"),
        ):
            self.register()
        self.rows(manager).one("analysis_refused", reason="changed_while_reading")
        self.assertFalse((self.project / "analysis" / "index.json").exists())

    def test_role_change_when_the_entry_vanished_meanwhile_registers_it_anew(self):
        first = self.register()
        real = analysis._read_index  # pylint: disable=protected-access
        calls = []

        def vanishing(project):
            calls.append(1)
            index = real(project)
            if len(calls) > 1 and index is not None:
                return {**index, "media": []}
            return index

        with mock.patch.object(analysis, "_read_index", side_effect=vanishing):
            again = self.register(role="footage")
        self.assertTrue(again["hashed"])
        self.assertEqual(("footage", first["media_id"]), (again["role"], again["media_id"]))
        self.assertEqual(["footage"], [e["role"] for e in self.index()["media"]])

    def test_role_change_keeps_working_when_media_json_is_corrupt(self):
        first = self.register()
        media_json = self.project / "analysis" / "media" / first["media_id"] / "media.json"
        media_json.write_text("{", encoding="utf-8")
        again = self.register(role="footage")
        self.assertFalse(again["hashed"])
        self.assertEqual("footage", again["role"])
        self.assertEqual(["footage"], [e["role"] for e in self.index()["media"]])
        self.assertEqual("{", media_json.read_text(encoding="utf-8"))

    def test_role_change_updates_media_json_without_rehashing(self):
        first = self.register()
        with mock.patch.object(analysis.ledger, "digest", side_effect=AssertionError("hash completo")):
            again = self.register(role="footage")
        self.assertFalse(again["hashed"])
        doc = ac.read_json(self.project, f"analysis/media/{first['media_id']}/media.json")
        self.assertEqual("footage", doc["role"])


class ComponentReadWriteEdgeTests(store.StoreTestCase):
    def setUp(self):
        super().setUp()
        self.media_id = self.register()["media_id"]

    def component_path(self):
        return self.project / "analysis" / "media" / self.media_id / "transcript.json"

    def test_unknown_component_is_refused_on_read_and_write(self):
        with self.assertRaisesRegex(ValueError, "desconhecido: ocr"):
            analysis.read_component(self.project, self.media_id, "ocr")
        with self.assertRaisesRegex(ValueError, "desconhecido: ocr"):
            analysis.write_component(self.project, self.media_id, "ocr", {}, producer=PRODUCER)

    def test_component_of_another_media_is_a_mismatch(self):
        analysis.write_component(self.project, self.media_id, "transcript", store.transcript(), producer=PRODUCER)
        doc = json.loads(self.component_path().read_text(encoding="utf-8"))
        self.component_path().write_text(json.dumps({**doc, "media_id": "c" * 16}), encoding="utf-8")
        with self.assertRaises(ac.Invalid) as raised:
            analysis.read_component(self.project, self.media_id, "transcript")
        self.assertEqual("MEDIA_ID_MISMATCH", raised.exception.code)
        report = analysis.check_all(self.project)
        self.assertIn(
            (f"analysis/media/{self.media_id}/transcript.json", "MEDIA_ID_MISMATCH"),
            {(p["path"], p["code"]) for p in report["problems"]},
        )

    def test_non_object_component_is_refused(self):
        for doc in ([1, 2], "texto", 7):
            with self.subTest(doc=doc), self.assertRaisesRegex(ValueError, "objeto JSON"):
                analysis.write_component(self.project, self.media_id, "transcript", doc, producer=PRODUCER)
        self.assertFalse(self.component_path().exists())

    def test_markers_must_be_a_list_of_objects(self):
        for bad in ({"id": "m"}, [1], "x"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "lista de objetos"):
                analysis.write_markers(self.project, PRODUCER, bad)
        self.assertFalse((self.project / "analysis" / "markers.json").exists())

    def test_corrupt_markers_file_is_refused_and_left_untouched(self):
        path = self.project / "analysis" / "markers.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "não dá para usar"):
            analysis.write_markers(self.project, PRODUCER, [store.marker("m_aaaaaaaaaa", self.media_id)])
        self.assertEqual("{", path.read_text(encoding="utf-8"))


class CheckAllDiskGarbageTests(store.StoreTestCase):
    def setUp(self):
        super().setUp()
        self.media_id = self.register()["media_id"]
        self.folder = self.project / "analysis"

    def problems(self):
        return {(p["path"], p["code"]) for p in analysis.check_all(self.project)["problems"]}

    def test_unknown_entries_are_reported(self):
        (self.folder / "notas.txt").write_text("x", encoding="utf-8")
        media = self.folder / "media"
        (media / "solto.txt").write_text("x", encoding="utf-8")
        (media / "nome-errado").mkdir()
        (media / self.media_id / "extra.json").write_text("{}", encoding="utf-8")
        (media / self.media_id / "leia-me.txt").write_text("x", encoding="utf-8")
        self.assertEqual(
            {
                ("analysis/notas.txt", "UNKNOWN_FILE"),
                ("analysis/media/solto.txt", "UNKNOWN_FILE"),
                ("analysis/media/nome-errado", "UNKNOWN_FILE"),
                (f"analysis/media/{self.media_id}/extra.json", "UNKNOWN_FILE"),
                (f"analysis/media/{self.media_id}/leia-me.txt", "UNKNOWN_FILE"),
            },
            self.problems(),
        )

    def test_media_that_is_a_file_is_unknown(self):
        (self.folder / "media").replace(self.folder / "media-antiga")
        (self.folder / "media").write_text("x", encoding="utf-8")
        self.assertIn(("analysis/media", "UNKNOWN_FILE"), self.problems())

    def test_temp_and_lock_files_are_ignored(self):
        (self.folder / ".index.json.1.tmp").write_text("x", encoding="utf-8")
        (self.folder / ".lock").write_text("", encoding="utf-8")
        self.assertTrue(analysis.check_all(self.project)["ok"])

    def test_missing_index_is_reported_without_orphan_noise(self):
        (self.folder / "index.json").unlink()
        self.assertEqual(set(), {c for _, c in self.problems()} & {"ORPHAN_MEDIA", "MISSING_FILE"})
        self.assertTrue(analysis.check_all(self.project)["files"])

    def test_links_inside_analysis_are_reported_not_followed(self):
        if not store._can_symlink(self.tmp):  # pylint: disable=protected-access
            self.skipTest("sem permissão para criar link simbólico")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.folder / "atalho").symlink_to(elsewhere, target_is_directory=True)
        (self.folder / "media" / self.media_id / "scenes.json").symlink_to(elsewhere / "x.json")
        self.assertEqual(
            {
                ("analysis/atalho", "UNSAFE_LINK"),
                (f"analysis/media/{self.media_id}/scenes.json", "UNSAFE_LINK"),
            },
            self.problems(),
        )

    def test_analysis_that_is_a_link_or_a_file_is_reported(self):
        if not store._can_symlink(self.tmp):  # pylint: disable=protected-access
            self.skipTest("sem permissão para criar link simbólico")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        self.folder.replace(self.project / "analysis-real")
        self.folder.symlink_to(elsewhere, target_is_directory=True)
        report = analysis.check_all(self.project)
        self.assertEqual(
            (False, [], [{"path": "analysis", "code": "UNSAFE_LINK", "where": "$"}]),
            (report["ok"], report["files"], report["problems"]),
        )
        self.folder.unlink()
        self.folder.write_text("arquivo", encoding="utf-8")
        self.assertEqual(
            [{"path": "analysis", "code": "UNSAFE_LINK", "where": "$"}], analysis.check_all(self.project)["problems"]
        )


class ProgressTests(store.StoreTestCase):
    def test_big_media_writes_one_json_progress_line_to_stderr(self):
        buffer = io.StringIO()
        with mock.patch.object(analysis.sys, "stderr", buffer):
            analysis._progress("aroll/c01.mp4", analysis.PROGRESS_MIN_BYTES)  # pylint: disable=protected-access
        line = json.loads(buffer.getvalue())
        self.assertEqual(("analysis_register", "aroll/c01.mp4"), (line["progress"], line["path"]))
        self.assertEqual(analysis.PROGRESS_MIN_BYTES, line["size_bytes"])
        self.assertIn("sha256", line["message"])

    def test_small_media_writes_nothing(self):
        buffer = io.StringIO()
        with mock.patch.object(analysis.sys, "stderr", buffer):
            analysis._progress("aroll/c01.mp4", analysis.PROGRESS_MIN_BYTES - 1)  # pylint: disable=protected-access
        self.assertEqual("", buffer.getvalue())

    def test_register_command_reports_progress_before_hashing(self):
        buffer = io.StringIO()
        args = types.SimpleNamespace(project=str(self.project), action="register", path="aroll/c01.mp4", role=None)
        with (
            mock.patch.object(analysis, "PROGRESS_MIN_BYTES", 1),
            mock.patch.object(analysis.sys, "stderr", buffer),
            mock.patch.object(analysis.media, "run", side_effect=analysis.PrerequisiteError("sem ffprobe")),
        ):
            result = analysis.run(args)
        self.assertEqual("analysis_register", json.loads(buffer.getvalue())["progress"])
        self.assertIn("registrada como", result["summary"]["line"])


if __name__ == "__main__":
    unittest.main()
