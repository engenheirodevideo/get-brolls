"""Eventos de log do que cria o projeto: `init`, `migrate` e o layout (onde moram os clipes).

Os comandos de projeto escrevem em `brolls/getbrolls.log`; os que só leem aparecem no espelho do
stderr (`GB_LOG_STDERR=1`). Cada teste confere o evento e que nada sensível chegou à linha: caminho
da máquina, nome da pasta ou do cliente, nome de arquivo.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _logs import LogCase, events
from _paths import CLI_ARGV
from test_layout_clips import TempProject, make_layout_one, symlink_or_skip

from getbrolls import clients, layout

UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def project_log(project):
    path = Path(project) / "brolls" / "getbrolls.log"
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


def mirrored(*args, project=None, env=None):
    """Roda a CLI com o espelho do stderr ligado; devolve `(processo, linhas de log antes do envelope)`."""
    command = [*CLI_ARGV, *map(str, args)]
    if project is not None:
        command += ["--project", str(project)]
    environment = {**os.environ, "GB_LOG_STDERR": "1", **(env or {})}
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=environment, check=False)
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    logged = [line for line in lines if " event=" in line]
    return done, logged


class InitLogTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-init-log-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.project = self.base / "video-secreto"

    def test_a_fresh_init_logs_the_creation_once_the_tree_exists(self):
        result = run_cli("init", project=self.project)
        lines = project_log(self.project)
        created = events(lines, "init_created")
        self.assertEqual(1, len(created))
        row = created[0]
        self.assertEqual(
            {
                "projeto_id": result["id"],
                "layout": "1",
                "folders": str(len(result["folders"])),
                "recipe": "False",
                "client_set": "False",
                "canvas_set": "False",
                "fps_set": "False",
            },
            {
                key: row[key]
                for key in ("projeto_id", "layout", "folders", "recipe", "client_set", "canvas_set", "fps_set")
            },
        )
        self.assertRegex(row["projeto_id"], UUID)
        self.assertEqual("ok", events(lines, "command_end")[-1]["status"])
        text = "\n".join(lines)
        for sentinel in (str(self.project), "video-secreto", str(self.base)):
            self.assertNotIn(sentinel, text)

    @unittest.skipIf(os.name == "nt", "permissões POSIX")
    def test_the_file_the_init_leaves_is_private(self):
        run_cli("init", project=self.project)
        self.assertEqual(0o600, (self.project / "brolls" / "getbrolls.log").stat().st_mode & 0o777)

    def test_flags_are_logged_as_booleans_only(self):
        (self.base / "Clientes").mkdir()
        clients.add("acme-secreto", str(self.base / "Clientes"))
        self.addCleanup(clients.remove, "acme-secreto")
        run_cli("init", "--client", "acme-secreto", "--canvas", "1080x1920", "--fps", "30", project=self.project)
        row = events(project_log(self.project), "init_created")[0]
        self.assertEqual(
            {"client_set": "True", "canvas_set": "True", "fps_set": "True"},
            {key: row[key] for key in ("client_set", "canvas_set", "fps_set")},
        )
        text = "\n".join(project_log(self.project))
        for sentinel in ("acme-secreto", "1080x1920"):
            self.assertNotIn(sentinel, text)

    def test_a_second_init_is_refused_and_the_refusal_lands_in_the_log(self):
        run_cli("init", project=self.project)
        run_cli("init", project=self.project, expect=1)
        refusal = events(project_log(self.project), "init_refused")[0]
        self.assertEqual(
            {"reason": "project_exists", "error_code": "INVALID_DATA"},
            {key: refusal[key] for key in ("reason", "error_code")},
        )
        self.assertEqual("error", events(project_log(self.project), "command_end")[-1]["status"])

    def test_a_refused_fresh_init_leaves_neither_a_folder_nor_a_log(self):
        run_cli("init", "--client", "ghost", project=self.project, expect=1)
        self.assertFalse(self.project.exists())

    def test_the_mirror_still_shows_what_a_refused_fresh_init_decided(self):
        done, logged = mirrored("init", "--tema", "x", project=self.project)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertFalse(self.project.exists())
        self.assertEqual("tema_without_recipe", events(logged, "init_refused")[0]["reason"])

    def test_gb_log_level_off_keeps_the_new_project_without_a_log(self):
        run_cli("init", project=self.project, env={"GB_LOG_LEVEL": "off"})
        self.assertFalse((self.project / "brolls" / "getbrolls.log").exists())

    def test_an_existing_manifest_is_refused_with_its_reason(self):
        (self.project / "brolls").mkdir(parents=True)
        (self.project / "brolls" / "manifest.json").write_text('{"schema_version": 1, "items": []}', encoding="utf-8")
        run_cli("init", project=self.project, expect=1)
        self.assertEqual("manifest_exists", events(project_log(self.project), "init_refused")[0]["reason"])


class MigrateLogTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-migrate-log-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.project = self.base / "video-secreto"
        (self.project / "brolls" / "clips").mkdir(parents=True)
        (self.project / "brolls" / "clips" / "a.mp4").write_bytes(b"clip")
        self.manifest({"schema_version": 1, "project_id": "5d0b7e4a-0000-4000-8000-000000000001", "items": []})

    def manifest(self, data):
        (self.project / "brolls" / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    def test_plan_is_a_read_only_event_visible_in_the_mirror_and_apply_is_in_the_log(self):
        done, logged = mirrored("migrate", "--action", "plan", project=self.project)
        self.assertEqual(0, done.returncode, done.stderr)
        plan = events(logged, "migrate_plan")[0]
        self.assertEqual(
            {"legacy_clips": "1", "id_source": "manifest", "client_set": "False"},
            {key: plan[key] for key in ("legacy_clips", "id_source", "client_set")},
        )
        self.assertFalse((self.project / "brolls" / "getbrolls.log").exists(), "plan não cria o log")
        applied = run_cli("migrate", "--action", "apply", project=self.project)
        lines = project_log(self.project)
        row = events(lines, "migrate_applied")[0]
        self.assertEqual(applied["id"], row["projeto_id"])
        self.assertEqual(
            {"legacy_clips": "1", "id_source": "manifest"}, {key: row[key] for key in ("legacy_clips", "id_source")}
        )
        self.assertEqual("ok", events(lines, "command_end")[-1]["status"])
        text = "\n".join(lines)
        for sentinel in (str(self.project), "video-secreto", "a.mp4"):
            self.assertNotIn(sentinel, text)

    def test_running_apply_again_is_a_noop_event(self):
        run_cli("migrate", "--action", "apply", project=self.project)
        run_cli("migrate", "--action", "apply", project=self.project)
        noop = events(project_log(self.project), "migrate_noop")[0]
        self.assertEqual({"action": "apply", "layout": "1"}, {key: noop[key] for key in ("action", "layout")})

    def test_where_the_id_comes_from_is_logged_without_the_id_text(self):
        self.manifest({"schema_version": 1, "project_id": "ID-ANTIGO-SECRETO", "items": []})
        _done, logged = mirrored("migrate", "--action", "plan", project=self.project)
        self.assertEqual("derived", events(logged, "migrate_plan")[0]["id_source"])
        self.assertNotIn("ID-ANTIGO-SECRETO", "\n".join(logged))
        self.manifest({"schema_version": 1, "items": []})
        _done, logged = mirrored("migrate", "--action", "plan", project=self.project)
        self.assertEqual("generated", events(logged, "migrate_plan")[0]["id_source"])

    def test_a_project_without_brolls_gets_the_log_only_after_the_apply(self):
        bare = self.base / "so-roteiro"
        bare.mkdir()
        _done, logged = mirrored("migrate", "--action", "plan", project=bare)
        self.assertTrue(events(logged, "migrate_plan"))
        self.assertFalse((bare / "brolls").exists())
        run_cli("migrate", "--action", "apply", project=bare)
        self.assertTrue(events(project_log(bare), "migrate_applied"))

    def test_the_refusals_have_their_reasons(self):
        (self.project / "project.json").write_text("{nao json", encoding="utf-8")
        run_cli("migrate", "--action", "apply", project=self.project, expect=1)
        row = events(project_log(self.project), "migrate_refused")[0]
        self.assertEqual(
            {"reason": "project_json_unusable", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("reason", "error_code")},
        )
        (self.project / "project.json").unlink()
        (self.project / "brolls" / ".pending-transaction.json").write_text("{}", encoding="utf-8")
        run_cli("migrate", "--action", "apply", project=self.project, expect=1)
        self.assertEqual("pending_transaction", events(project_log(self.project), "migrate_refused")[-1]["reason"])
        done, logged = mirrored("migrate", "--action", "plan", project=self.base / "nao-existe")
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertEqual("not_a_folder", events(logged, "migrate_refused")[0]["reason"])


class LayoutLogTests(TempProject, LogCase):
    def test_a_clip_in_both_folders_with_no_matching_hash_is_a_logged_conflict(self):
        make_layout_one(self.project)
        (self.project / "broll" / "a.mp4").write_bytes(b"um")
        self.legacy().mkdir(parents=True)
        (self.legacy() / "a.mp4").write_bytes(b"dois")
        with self.captured() as cm, self.assertRaises(layout.ClipConflictError):
            layout.clip_file(self.project, "clips/a.mp4", sha256="0" * 64)
        rows = self.rows(cm)
        row = rows.one("clip_conflict")
        self.assertEqual(
            {"copies": "2", "sha_known": "True", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("copies", "sha_known", "error_code")},
        )
        self.assertEqual(logging.WARNING, rows.level_of("clip_conflict"))
        with self.captured() as cm, self.assertRaises(layout.ClipConflictError):
            layout.clip_file(self.project, "clips/a.mp4")
        self.assertEqual("False", self.rows(cm).one("clip_conflict")["sha_known"])
        self.assert_clean(rows, "a.mp4", self.project)

    def test_a_broll_folder_that_is_a_link_is_refused_for_the_clips(self):
        make_layout_one(self.project)
        target = self.project / "alvo-fora"
        target.mkdir()
        (self.project / "broll").rmdir()
        symlink_or_skip(self, self.project / "broll", target)
        with self.captured() as cm, self.assertRaises(ValueError):
            layout.clips_dir(self.project)
        row = self.rows(cm).one("layout_refused", reason="broll_link")
        self.assertEqual(
            {"action": "clips", "error_code": "INVALID_DATA"}, {key: row[key] for key in ("action", "error_code")}
        )

    def test_a_broken_project_json_next_to_broll_refuses_to_pick_a_clip_folder(self):
        (self.project / "broll").mkdir(parents=True)
        (self.project / "project.json").write_text("{nao json", encoding="utf-8")
        with self.captured() as cm, self.assertRaises(ValueError):
            layout.clips_dir(self.project)
        self.rows(cm).one("layout_refused", action="clips", reason="project_json_unusable")

    def test_writing_project_json_twice_and_a_linked_work_folder_are_refused(self):
        layout.write_project(self.project, layout.new_project_doc())
        with self.captured() as cm, self.assertRaises(ValueError):
            layout.write_project(self.project, layout.new_project_doc())
        self.rows(cm).one("layout_refused", action="write", reason="project_json_exists")
        fresh = self.project / "sub-novo"
        fresh.mkdir()
        outside = self.project / "alvo-outro"
        outside.mkdir()
        symlink_or_skip(self, fresh / "analysis", outside)
        with self.captured() as cm, self.assertRaises(ValueError):
            layout.refuse_linked_folders(fresh)
        self.rows(cm).one("layout_refused", action="init", reason="linked_folder")


if __name__ == "__main__":
    unittest.main()
