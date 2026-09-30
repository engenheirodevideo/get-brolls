"""`analysis/`: índice por conteúdo, gravação sem seguir link, trava própria e o comando `analysis`."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import analysis, capabilities, ledger, runtime
from getbrolls import analysis_contract as ac
from getbrolls.cli import build_parser
from getbrolls.sdk.exporters import find_local_paths

PRODUCER = {"tool": "whisper_local", "model": "large-v3", "version": "0.3.0"}


def _can_symlink(folder):
    probe = Path(folder) / "probe-link"
    try:
        probe.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def transcript(status="done", reason=None, words=None):
    words = (
        [{"text": "Olá", "start": 0.1, "end": 0.4, "probability": None, "speaker": None}] if words is None else words
    )
    return {
        "status": status,
        "reason": reason,
        "language": "pt",
        "text": " ".join(w["text"] for w in words),
        "word_count": len(words),
        "words": words,
    }


def marker(ident, media_id, start=1.0):
    return {
        "id": ident,
        "media_id": media_id,
        "scene": None,
        "start": start,
        "end": None,
        "name": "gancho",
        "color": "GREEN",
        "comment": None,
        "producer": {"tool": "x", "model": None, "version": None},
    }


def snapshot(folder):
    return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*"))


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-analysis-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "video"
        (self.project / "aroll").mkdir(parents=True)
        self.clip = self.project / "aroll" / "c01.mp4"
        self.clip.write_bytes(b"voz da cena um" * 100)

    def register(self, rel="aroll/c01.mp4", role=None):
        return analysis.ensure_media(self.project, rel, role, probe=False)

    def index(self):
        return json.loads((self.project / "analysis" / "index.json").read_text(encoding="utf-8"))


class RegisterTests(StoreTestCase):
    def test_register_twice_hashes_once(self):
        calls = []
        real = ledger.digest

        def counting(path):
            calls.append(path)
            return real(path)

        with mock.patch.object(analysis.ledger, "digest", side_effect=counting):
            first = self.register()
            second = self.register()
        self.assertEqual(1, len(calls))
        self.assertTrue(first["hashed"])
        self.assertFalse(second["hashed"])
        self.assertEqual(ledger.digest(self.clip)[:16], first["media_id"])
        self.assertEqual(first["media_id"], analysis.media_id_for_bytes(self.clip))
        self.assertEqual(first["media_id"], second["media_id"])
        entry = self.index()["media"][0]
        self.assertEqual(("aroll/c01.mp4", "aroll"), (entry["path"], entry["role"]))
        self.assertEqual([], ac.check_doc(self.index(), "analysis_index"))
        media_doc = ac.read_json(self.project, f"analysis/media/{first['media_id']}/media.json")
        self.assertEqual([], ac.check_doc(media_doc, "media"))
        self.assertEqual(ledger.digest(self.clip), media_doc["sha256"])

    def test_touched_file_is_hashed_again_and_keeps_its_media_id(self):
        """mtime mudou: a amostra não prova nada, então o sha256 inteiro roda de novo."""
        first = self.register()
        os.utime(self.clip, ns=(5_000_000_000_000_000_000, 5_000_000_000_000_000_000))
        self.assertEqual("stale", analysis.match(self.project, "aroll/c01.mp4")[0])
        again = self.register()
        self.assertTrue(again["hashed"])
        self.assertEqual(first["media_id"], again["media_id"])
        self.assertEqual(5_000_000_000_000_000_000, self.index()["media"][0]["quick_key"]["mtime_ns"])
        media_doc = ac.read_json(self.project, f"analysis/media/{first['media_id']}/media.json")
        self.assertEqual(5_000_000_000_000_000_000, media_doc["quick_key"]["mtime_ns"])

    def test_same_size_and_mtime_skip_the_full_hash(self):
        first = self.register()
        with mock.patch.object(analysis.ledger, "digest", side_effect=AssertionError("hash completo")):
            again = self.register()
        self.assertEqual((False, first["media_id"]), (again["hashed"], again["media_id"]))

    def test_changed_content_hashes_again_and_replaces_the_entry(self):
        first = self.register()
        self.clip.write_bytes(b"outra fala, outro take" * 100)
        seen = []
        again = analysis.ensure_media(self.project, "aroll/c01.mp4", probe=False, progress=lambda *a: seen.append(a))
        self.assertEqual([("aroll/c01.mp4", self.clip.stat().st_size)], seen)
        self.assertTrue(again["hashed"])
        self.assertNotEqual(first["media_id"], again["media_id"])
        self.assertEqual([again["media_id"]], [e["media_id"] for e in self.index()["media"]])

    def test_mid_file_edit_outside_the_sample_changes_the_media_id(self):
        big = self.project / "aroll" / "c02.mp4"
        with big.open("wb") as stream:
            stream.write(b"a" * (3 * analysis.SAMPLE_BYTES))
        first = self.register("aroll/c02.mp4")
        with big.open("r+b") as stream:
            stream.seek(analysis.SAMPLE_BYTES + 10)
            stream.write(b"b")  # meio do arquivo: fora da amostra
        stat = big.stat()
        os.utime(big, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        again = self.register("aroll/c02.mp4")
        self.assertTrue(again["hashed"])
        self.assertEqual(ledger.digest(big)[:16], again["media_id"])
        self.assertNotEqual(first["media_id"], again["media_id"])

    def test_old_media_folder_of_changed_content_is_an_orphan_not_deleted(self):
        first = self.register()
        self.clip.write_bytes(b"outro take" * 100)
        again = self.register()
        old = self.project / "analysis" / "media" / first["media_id"]
        self.assertTrue(old.is_dir())
        report = analysis.check_all(self.project)
        self.assertFalse(report["ok"])
        self.assertIn(
            (f"analysis/media/{first['media_id']}", "ORPHAN_MEDIA"), {(p["path"], p["code"]) for p in report["problems"]}
        )
        self.assertNotIn(again["media_id"], json.dumps(report["problems"]))

    def test_recorded_role_wins_over_the_folder(self):
        """Papel gravado com --role continua quando a mídia é registrada de novo sem --role."""
        self.assertEqual("narration", self.register(role="narration")["role"])
        self.clip.write_bytes(b"regravado" * 100)
        self.assertEqual("narration", self.register()["role"])
        self.assertEqual("footage", self.register(role="footage")["role"])

    def test_a_file_that_is_not_media_is_refused(self):
        notes = self.project / "aroll" / "notas.txt"
        notes.write_text("não é mídia", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "notas.txt"):
            self.register("aroll/notas.txt")
        self.assertFalse((self.project / "analysis").exists())

    def test_role_is_inferred_or_required(self):
        for rel, role in (
            ("broll/a.mp4", "broll"),
            ("brolls/clips/b.mp4", "broll"),
            ("assets/musica/t.wav", "music"),
            ("assets/sfx/w.wav", "sfx"),
        ):
            path = self.project / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(rel.encode())
            self.assertEqual(role, self.register(rel)["role"], rel)
        other = self.project / "extra" / "n.wav"
        other.parent.mkdir()
        other.write_bytes(b"narra")
        with self.assertRaisesRegex(ValueError, "--role"):
            self.register("extra/n.wav")
        self.assertEqual("narration", self.register("extra/n.wav", "narration")["role"])
        paths = [e["path"] for e in self.index()["media"]]
        self.assertEqual(sorted(paths), paths)

    def test_unsafe_paths_are_refused(self):
        outside = self.tmp / "fora.mp4"
        outside.write_bytes(b"fora")
        for rel in (str(outside), "../fora.mp4", "aroll/../aroll/c01.mp4", "aroll\\c01.mp4", "aroll/nada.mp4", "aroll"):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                self.register(rel, "aroll")
        self.assertFalse((self.project / "analysis").exists())

    def test_linked_media_is_refused(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        (self.project / "aroll" / "link.mp4").symlink_to(self.clip)
        with self.assertRaisesRegex(ValueError, "link"):
            self.register("aroll/link.mp4")

    def test_same_bytes_at_two_live_paths_is_refused(self):
        self.register()
        shutil.copyfile(self.clip, self.project / "aroll" / "c01-copia.mp4")
        with self.assertRaisesRegex(ValueError, "aroll/c01.mp4"):
            self.register("aroll/c01-copia.mp4")

    def test_moved_file_takes_the_old_entry(self):
        first = self.register()
        self.clip.rename(self.project / "aroll" / "c09.mp4")
        moved = self.register("aroll/c09.mp4")
        self.assertEqual(first["media_id"], moved["media_id"])
        self.assertEqual(["aroll/c09.mp4"], [e["path"] for e in self.index()["media"]])

    def test_probe_without_ffprobe_is_unavailable_with_a_reason(self):
        with mock.patch.object(analysis.media, "run", side_effect=analysis.PrerequisiteError("ffprobe não encontrado")):
            done = analysis.ensure_media(self.project, "aroll/c01.mp4")
        doc = ac.read_json(self.project, f"analysis/media/{done['media_id']}/media.json")
        self.assertEqual("unavailable", doc["status"])
        self.assertIn("ffprobe", doc["reason"])
        self.assertEqual([], ac.check_doc(doc, "media"))

    def test_probe_facts_are_recorded(self):
        answer = json.dumps(
            {
                "format": {"duration": "12.48"},
                "streams": [
                    {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920,
                     "avg_frame_rate": "30000/1001"},
                    {"codec_type": "audio", "codec_name": "aac"},
                ],
            }
        )  # fmt: skip
        with mock.patch.object(analysis.media, "run", return_value=answer):
            done = analysis.ensure_media(self.project, "aroll/c01.mp4")
        doc = ac.read_json(self.project, f"analysis/media/{done['media_id']}/media.json")
        self.assertEqual(
            ("done", 12.48, "30000/1001", 1080, 1920),
            (doc["status"], doc["duration_s"], doc["fps"], doc["width"], doc["height"]),
        )
        self.assertEqual((True, "h264", "aac"), (doc["has_audio"], doc["video_codec"], doc["audio_codec"]))


class LinkTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()

    def test_linked_analysis_folder_is_refused_and_nothing_lands_outside(self):
        (self.project / "analysis").symlink_to(self.elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "link"):
            self.register()
        self.assertEqual([], snapshot(self.elsewhere))

    def test_linked_media_folder_is_refused(self):
        done = self.register()
        folder = self.project / "analysis" / "media" / done["media_id"]
        shutil.rmtree(folder)
        folder.symlink_to(self.elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "link"):
            analysis.write_component(self.project, done["media_id"], "transcript", transcript(), producer=PRODUCER)
        self.assertEqual([], snapshot(self.elsewhere))

    def test_linked_component_file_is_replaced_not_followed(self):
        done = self.register()
        target = self.elsewhere / "alvo.json"
        target.write_text("{}", encoding="utf-8")
        link = self.project / "analysis" / "media" / done["media_id"] / "transcript.json"
        link.symlink_to(target)
        analysis.write_component(self.project, done["media_id"], "transcript", transcript(), producer=PRODUCER)
        self.assertEqual("{}", target.read_text(encoding="utf-8"))
        self.assertFalse(link.is_symlink())


class LinkTestsWithoutDirFd(LinkTests):
    """Mesmas recusas pelo caminho sem `dir_fd` (o do Windows): `lstat` por pasta e identidade."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(analysis, "_dir_fd_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_register_and_write_work(self):
        done = self.register()
        analysis.write_component(self.project, done["media_id"], "transcript", transcript(), producer=PRODUCER)
        self.assertTrue(analysis.check_all(self.project)["ok"])


class ComponentTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.media_id = self.register()["media_id"]

    def test_write_forces_the_header_and_updates_the_index(self):
        doc = {
            **transcript(),
            "schema": "getbrolls.transcript/1",
            "media_id": "0" * 16,
            "created": "x",
            "time_unit": "ms",
        }
        analysis.write_component(self.project, self.media_id, "transcript", doc, producer=PRODUCER)
        stored = analysis.read_component(self.project, self.media_id, "transcript")
        assert stored is not None
        self.assertEqual((self.media_id, "s", PRODUCER), (stored["media_id"], stored["time_unit"], stored["producer"]))
        self.assertEqual("done", self.index()["media"][0]["components"]["transcript"])
        self.assertEqual([], ac.check_doc(stored, "transcript"))

    def test_invalid_documents_are_refused_with_the_code(self):
        cases = (
            (transcript(status="failed"), "MISSING_REASON"),
            ({**transcript(), "word_count": 9}, "WORD_COUNT_MISMATCH"),
            ({**transcript(), "note": "/Users/x"}, "ABSOLUTE_PATH"),
            ({**transcript(), "api_key": "k"}, "SECRET_FIELD"),
            ({**transcript(), "text": float("nan")}, "NONFINITE_NUMBER"),
        )
        for doc, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                analysis.write_component(self.project, self.media_id, "transcript", doc, producer=PRODUCER)
        self.assertIsNone(analysis.read_component(self.project, self.media_id, "transcript"))

    def test_unknown_media_and_the_core_media_file_are_refused(self):
        with self.assertRaisesRegex(ValueError, "register"):
            analysis.write_component(self.project, "a" * 16, "transcript", transcript(), producer=PRODUCER)
        with self.assertRaisesRegex(ValueError, "media"):
            analysis.write_component(self.project, self.media_id, "media", {}, producer=PRODUCER)
        with self.assertRaises(ValueError):
            analysis.write_component(self.project, "../x", "transcript", transcript(), producer=PRODUCER)

    def test_markers_from_two_producers_coexist(self):
        analysis.write_markers(self.project, PRODUCER, [marker("m_aaaaaaaaaa", self.media_id)])
        other = {"tool": "cortes", "model": None, "version": "1.0"}
        analysis.write_markers(self.project, other, [marker("m_bbbbbbbbbb", self.media_id, 2.0)])
        analysis.write_markers(self.project, PRODUCER, [marker("m_cccccccccc", self.media_id, 3.0)])
        doc = ac.read_json(self.project, "analysis/markers.json")
        self.assertEqual([], ac.check_doc(doc, "markers"))
        found = {(m["id"], m["producer"]["tool"]) for m in doc["markers"]}
        self.assertEqual({("m_bbbbbbbbbb", "cortes"), ("m_cccccccccc", "whisper_local")}, found)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_ID"):
            analysis.write_markers(self.project, PRODUCER, [marker("m_bbbbbbbbbb", self.media_id)])
        with self.assertRaisesRegex(ValueError, "register"):
            analysis.write_markers(self.project, PRODUCER, [marker("m_dddddddddd", "b" * 16)])

    def test_check_all_reports_a_corrupted_file_with_its_code(self):
        self.assertTrue(analysis.check_all(self.project)["ok"])
        path = self.project / "analysis" / "media" / self.media_id / "media.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**doc, "media_id": "c" * 16}), encoding="utf-8")
        (self.project / "analysis" / "markers.json").write_text("{", encoding="utf-8")
        report = analysis.check_all(self.project)
        self.assertFalse(report["ok"])
        codes = {(p["path"], p["code"]) for p in report["problems"]}
        self.assertIn((f"analysis/media/{self.media_id}/media.json", "IDENTITY_MISMATCH"), codes)
        self.assertIn(("analysis/markers.json", "INVALID_JSON"), codes)

    def test_lookup_never_hashes_and_sees_stale_entries(self):
        with mock.patch.object(analysis.ledger, "digest", side_effect=AssertionError("hash")):
            found = analysis.lookup(self.project, "aroll/c01.mp4")
            assert found is not None
            self.assertEqual(self.media_id, found["media_id"])
            self.assertIsNone(analysis.lookup(self.project, "aroll/c02.mp4"))
            self.clip.write_bytes(b"trocado")
            self.assertIsNone(analysis.lookup(self.project, "aroll/c01.mp4"))
            self.assertEqual("stale", analysis.match(self.project, "aroll/c01.mp4")[0])

    def test_lock_file_that_is_a_link_is_never_followed(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        target = self.tmp / "fora.lock"
        lock = self.project / "analysis" / ".lock"
        lock.unlink(missing_ok=True)
        lock.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "link"), runtime.exclusive_lock(lock, "ocupado"):
            pass
        with self.assertRaisesRegex(ValueError, "link"):
            analysis.write_component(self.project, self.media_id, "transcript", transcript(), producer=PRODUCER)
        self.assertFalse(os.path.lexists(target))

    def test_busy_lock_is_a_clear_error(self):
        held = runtime.exclusive_lock(self.project / "analysis" / ".lock", "ocupado")
        with (
            mock.patch.object(analysis, "LOCK_WAIT_S", 0),
            held,
            self.assertRaisesRegex(ValueError, "Outra gravação em analysis/"),
        ):
            analysis.write_component(self.project, self.media_id, "transcript", transcript(), producer=PRODUCER)


class CommandTests(StoreTestCase):
    def test_list_and_check_create_nothing_without_analysis(self):
        before = snapshot(self.project)
        listed = run_cli("analysis", "--action", "list", project=self.project)
        checked = run_cli("analysis", "--action", "check", project=self.project)
        self.assertEqual([], listed["media"])
        self.assertEqual((True, []), (checked["ok"], checked["problems"]))
        self.assertEqual(before, snapshot(self.project))

    def test_register_list_and_check_output_has_no_local_path(self):
        registered = run_cli("analysis", "--action", "register", "--path", "aroll/c01.mp4", project=self.project)
        listed = run_cli("analysis", "--action", "list", project=self.project)
        checked = run_cli("analysis", "--action", "check", project=self.project)
        self.assertEqual("aroll/c01.mp4", registered["path"])
        self.assertEqual([registered["media_id"]], [m["media_id"] for m in listed["media"]])
        self.assertEqual("fresh", listed["media"][0]["state"])
        self.assertTrue(checked["ok"])
        self.assertEqual([], find_local_paths([registered, listed, checked]))
        self.assertNotIn(str(self.tmp), json.dumps([registered, listed, checked], ensure_ascii=False))

    def test_register_needs_a_path_and_takes_no_project_lock(self):
        refused = run_cli("analysis", "--action", "register", project=self.project, expect=2)
        self.assertEqual("USAGE_ERROR", refused["error_code"])
        self.assertIn("--path", refused["error"])
        (self.project / "brolls").mkdir(exist_ok=True)
        with runtime.exclusive_lock(self.project / "brolls" / ".command.lock", "ocupado"):
            done = run_cli("analysis", "--action", "register", "--path", "aroll/c01.mp4", project=self.project)
        self.assertTrue(done["hashed"])

    def test_lock_held_by_another_process_exits_1(self):
        (self.project / "analysis").mkdir()
        with runtime.exclusive_lock(self.project / "analysis" / ".lock", "ocupado"):
            refused = run_cli(
                "analysis", "--action", "register", "--path", "aroll/c01.mp4", project=self.project, expect=1
            )
        self.assertIn("Outra gravação em analysis/", refused["error"])

    def test_register_of_a_non_media_file_exits_1(self):
        (self.project / "aroll" / "notas.txt").write_text("texto", encoding="utf-8")
        refused = run_cli("analysis", "--action", "register", "--path", "aroll/notas.txt", project=self.project, expect=1)
        self.assertIn("mídia", refused["error"])
        self.assertFalse((self.project / "analysis").exists())

    def test_check_with_problems_exits_1_and_still_prints_the_report(self):
        import subprocess

        from _paths import CLI_ARGV

        analysis.ensure_media(self.project, "aroll/c01.mp4", probe=False)
        (self.project / "analysis" / "markers.json").write_text("{", encoding="utf-8")
        done = subprocess.run(
            [*CLI_ARGV, "analysis", "--action", "check", "--project", str(self.project)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )  # fmt: skip
        self.assertEqual(1, done.returncode, done.stderr)
        report = json.loads(done.stdout)
        self.assertFalse(report["ok"])
        self.assertIn("analysis/markers.json", json.dumps(report["problems"]))

    def test_read_only_actions_are_declared(self):
        self.assertIn(("analysis", "list"), runtime.READ_ONLY_ACTIONS)
        self.assertIn(("analysis", "check"), runtime.READ_ONLY_ACTIONS)
        self.assertNotIn(("analysis", "register"), runtime.READ_ONLY_ACTIONS)
        described = {c["name"]: c for c in capabilities.describe(build_parser())["commands"]}["analysis"]
        self.assertEqual("by_action", described["read_only"])
        self.assertEqual(["check", "list"], described["read_only_actions"])


if __name__ == "__main__":
    unittest.main()
