"""Eventos de log da análise de mídia e do roteiro.

Os comandos de projeto escrevem em `brolls/getbrolls.log`; os que só leem (`roteiro check` e `plan`)
aparecem no espelho do stderr (`GB_LOG_STDERR=1`). Cada teste confere o evento e que nada sensível
chegou à linha: caminho da máquina, caminho de mídia, tema, fala do roteiro ou texto do erro.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _logs import LogCase, events
from _paths import CLI_ARGV
from _symlink import needs_symlink
from test_analysis_store import PRODUCER, StoreTestCase, _can_symlink, marker, transcript
from test_roteiro_cli import REVIEW, CliCase

from getbrolls import analysis, roteiro_commands
from getbrolls.errors import LockedError

HEX16 = r"^[0-9a-f]{16}$"


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


class AnalysisLogTests(StoreTestCase, LogCase):
    def args(self, action, **extra):
        return argparse.Namespace(project=str(self.project), action=action, **extra)

    def test_registering_logs_the_id_the_role_and_the_size_but_never_the_path(self):
        with self.captured() as cm:
            entry = self.register()
        rows = self.rows(cm)
        row = rows.one("analysis_media")
        self.assertRegex(row["media_id"], HEX16)
        self.assertEqual(
            {
                "media_id": entry["media_id"],
                "role": "aroll",
                "hashed": "True",
                "status": "not_run_by_this_script",
                "size": str(self.clip.stat().st_size),
            },
            {key: row[key] for key in ("media_id", "role", "hashed", "status", "size")},
        )
        self.assertTrue(row["ms"].isdigit())
        self.assertEqual(logging.INFO, rows.level_of("analysis_media"))
        self.assert_clean(rows, "c01.mp4", "aroll/", self.project, self.tmp)

    def test_an_unchanged_media_and_a_new_role_skip_the_hash_and_say_so(self):
        self.register()
        with self.captured() as cm:
            self.register()
        self.assertEqual("False", self.rows(cm).one("analysis_media")["hashed"])
        with self.captured() as cm:
            self.register(role="broll")
        row = self.rows(cm).one("analysis_media")
        self.assertEqual({"hashed": "False", "role": "broll"}, {key: row[key] for key in ("hashed", "role")})

    def test_a_component_and_markers_are_logged_with_the_plugin_id_and_counts(self):
        media_id = self.register()["media_id"]
        with self.captured() as cm:
            analysis.write_component(self.project, media_id, "transcript", transcript(), producer=PRODUCER)
        rows = self.rows(cm)
        row = rows.one("analysis_component")
        self.assertEqual(
            {"media_id": media_id, "component": "transcript", "plugin": "whisper_local", "status": "done"},
            {key: row[key] for key in ("media_id", "component", "plugin", "status")},
        )
        self.assert_clean(rows, "Olá", "large-v3", self.project)
        with self.captured() as cm:
            analysis.write_markers(
                self.project,
                {"tool": "marcador", "model": None, "version": None},
                [marker("m_0123456789abcdef", media_id)],
            )
        row = self.rows(cm).one("analysis_markers")
        self.assertEqual(
            {"plugin": "marcador", "markers": "1", "total": "1"},
            {key: row[key] for key in ("plugin", "markers", "total")},
        )

    def test_check_logs_the_result_with_the_problem_codes_only(self):
        media_id = self.register()["media_id"]
        analysis.write_component(self.project, media_id, "transcript", transcript(), producer=PRODUCER)
        with self.captured() as cm:
            analysis.run(self.args("check"))
        row = self.rows(cm).one("analysis_check")
        self.assertEqual(
            {"ok": "True", "problems": "0", "codes": None}, {key: row[key] for key in ("ok", "problems", "codes")}
        )
        (self.project / "analysis" / "media" / media_id / "transcript.json").write_text("{}", encoding="utf-8")
        with self.captured() as cm:
            report = analysis.run(self.args("check"))
        rows = self.rows(cm)
        row = rows.one("analysis_check")
        self.assertEqual("False", row["ok"])
        self.assertEqual(str(len(report["problems"])), row["problems"])
        self.assertEqual(logging.WARNING, rows.level_of("analysis_check"))
        self.assertRegex(row["codes"], r"^[A-Z_,]+$")
        self.assert_clean(rows, "transcript.json", self.project)

    def test_media_refusals_have_their_reasons_and_the_error_code(self):
        (self.project / "aroll" / "nota.txt").write_text("não é mídia", encoding="utf-8")
        (self.project / "solta").mkdir()
        (self.project / "solta" / "c02.mp4").write_bytes(b"sem papel")
        cases = (
            (lambda: analysis.ensure_media(self.project, "aroll/nota.txt", probe=False), "not_media"),
            (lambda: analysis.ensure_media(self.project, "aroll/nao-existe.mp4", probe=False), "media_missing"),
            (lambda: analysis.ensure_media(self.project, "../fora.mp4", probe=False), "bad_path"),
            (lambda: analysis.ensure_media(self.project, "solta/c02.mp4", probe=False), "no_role"),
            (lambda: analysis.ensure_media(self.project, "aroll/c01.mp4", "inventado", probe=False), "unknown_role"),
        )
        for call, reason in cases:
            with self.subTest(reason=reason), self.captured() as cm, self.assertRaises(ValueError):
                call()
            rows = self.rows(cm)
            self.assertEqual("INVALID_DATA", rows.one("analysis_refused", reason=reason)["error_code"])
            self.assert_clean(rows, "nota.txt", "c02.mp4", "nao-existe", "inventado", self.project)

    def test_the_same_bytes_in_two_places_and_the_component_rules_are_refused_by_reason(self):
        media_id = self.register()["media_id"]
        shutil.copyfile(self.clip, self.project / "aroll" / "c01-copia.mp4")
        with self.captured() as cm, self.assertRaises(ValueError):
            self.register("aroll/c01-copia.mp4")
        self.rows(cm).one("analysis_refused", reason="twin_exists")
        cases = (
            (
                lambda: analysis.write_component(self.project, media_id, "media", {}, producer=PRODUCER),
                "core_component",
            ),
            (
                lambda: analysis.write_component(self.project, media_id, "inventado", {}, producer=PRODUCER),
                "unknown_component",
            ),
            (
                lambda: analysis.write_component(self.project, "0" * 16, "transcript", transcript(), producer=PRODUCER),
                "media_not_indexed",
            ),
            (
                lambda: analysis.write_component(self.project, "curto", "transcript", transcript(), producer=PRODUCER),
                "bad_media_id",
            ),
        )
        for call, reason in cases:
            with self.subTest(reason=reason), self.captured() as cm, self.assertRaises(ValueError):
                call()
            self.rows(cm).one("analysis_refused", reason=reason)

    def test_a_document_outside_the_contract_is_refused_with_the_first_code_and_a_count(self):
        media_id = self.register()["media_id"]
        bad = transcript(status="failed", reason=None)
        with self.captured() as cm, self.assertRaises(ValueError):
            analysis.write_component(self.project, media_id, "transcript", bad, producer=PRODUCER)
        row = self.rows(cm).one("analysis_refused", reason="contract")
        self.assertRegex(row["code"], r"^[A-Z_]+$")
        self.assertGreaterEqual(int(row["problems"]), 1)

    @needs_symlink
    def test_a_linked_analysis_folder_is_refused_as_a_link(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link")
        outside = self.tmp / "fora"
        outside.mkdir()
        (self.project / "analysis").symlink_to(outside, target_is_directory=True)
        with self.captured() as cm, self.assertRaises(ValueError):
            self.register()
        self.rows(cm).one("analysis_refused", reason="link")

    def test_a_busy_analysis_lock_is_logged(self):
        self.register()
        with (
            analysis.analysis_lock(self.project),
            patch.object(analysis, "LOCK_WAIT_S", 0.05),
            self.captured() as cm,
            self.assertRaises(LockedError),
            analysis.analysis_lock(self.project),
        ):
            pass
        row = self.rows(cm).one("lock_busy")
        self.assertEqual({"lock": ".lock", "error_code": "LOCKED"}, {key: row[key] for key in ("lock", "error_code")})


class RoteiroSkeletonLogTests(LogCase):
    def test_a_genre_that_does_not_exist_and_a_multiline_theme_are_refused_with_their_reasons(self):
        with self.captured() as cm, self.assertRaises(ValueError):
            roteiro_commands.skeleton("inventado", "TEMA-SECRETO-XYZ")
        rows = self.rows(cm)
        self.assertEqual(
            "INVALID_DATA", rows.one("roteiro_refused", action="new", reason="genre_unknown")["error_code"]
        )
        self.assert_clean(rows, "TEMA-SECRETO-XYZ")
        with self.captured() as cm, self.assertRaises(ValueError):
            roteiro_commands.skeleton("reels", "linha um\nlinha dois")
        self.rows(cm).one("roteiro_refused", action="new", reason="tema_invalid")


class RoteiroLogTests(CliCase):
    TEMA = "TEMA-SECRETO-XYZ"

    def log(self):
        return project_log(self.project)

    def setup_project(self):
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", self.TEMA)

    def test_new_logs_the_genre_and_the_scene_count_but_never_the_theme(self):
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", self.TEMA)
        row = events(self.log(), "roteiro_new")[0]
        self.assertEqual(
            {"genero": "reels", "scenes": "4", "backup": "False", "rules_seeded": "False"},
            {key: row[key] for key in ("genero", "scenes", "backup", "rules_seeded")},
        )
        text = "\n".join(self.log())
        for sentinel in (self.TEMA, str(self.project)):
            self.assertNotIn(sentinel, text)

    def test_new_over_an_existing_roteiro_and_a_bad_genre_are_refused_with_reasons(self):
        self.setup_project()
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", self.TEMA, expect=1)
        rows = events(self.log(), "roteiro_refused")
        self.assertEqual(
            {"action": "new", "reason": "exists", "error_code": "INVALID_DATA"},
            {key: rows[-1][key] for key in ("action", "reason", "error_code")},
        )

    def test_check_and_plan_are_read_only_events_in_the_mirror(self):
        self.setup_project()
        done, logged = mirrored("roteiro", "--action", "check", project=self.project)
        self.assertEqual(0, done.returncode, done.stderr)
        row = events(logged, "roteiro_check")[0]
        self.assertEqual(
            {"scenes": "4", "skeleton_scenes": "4", "reviewed": "False"},
            {key: row[key] for key in ("scenes", "skeleton_scenes", "reviewed")},
        )
        self.assertEqual([], [line for line in self.log() if "event=roteiro_check" in line], "check não grava o log")
        self.fill_skeleton()
        self.write_brief()
        self.review()
        done, logged = mirrored("roteiro", "--action", "plan", project=self.project)
        result = json.loads(done.stdout)
        plan = events(logged, "roteiro_plan")[0]
        self.assertEqual(
            {"new": str(len(result["new"])), "problems": "0", "reviewed": "True"},
            {key: plan[key] for key in ("new", "problems", "reviewed")},
        )
        self.assertNotIn("Você não precisa editar 4 horas", "\n".join(logged))

    def test_review_and_sync_refusals_name_their_reasons_and_leave_the_text_out(self):
        self.setup_project()
        self.write_brief()
        sha = self.cli("roteiro", "--action", "check")["review"]["sha256"]
        self.cli("roteiro", "--action", "review", *REVIEW, "--expect", sha, expect=1)
        row = events(self.log(), "roteiro_refused")[-1]
        self.assertEqual(
            {"action": "review", "reason": "skeleton", "scenes": "4"},
            {key: row[key] for key in ("action", "reason", "scenes")},
        )
        self.fill_skeleton()
        self.cli("roteiro", "--action", "review", *REVIEW, "--expect", "0" * 64, expect=1)
        self.assertEqual("review_changed", events(self.log(), "roteiro_refused")[-1]["reason"])
        self.cli("roteiro", "--action", "sync", expect=1)
        self.assertEqual("review_missing", events(self.log(), "roteiro_refused")[-1]["reason"])
        text = "\n".join(self.log())
        for sentinel in (REVIEW[1], REVIEW[3], "timeline cheia", "o que a pessoa vê"):
            self.assertNotIn(sentinel, text)

    def test_a_roteiro_the_parser_refuses_is_logged_with_a_count_and_not_its_lines(self):
        self.setup_project()
        self.edit("## ", "<div hidden>Texto escondido</div>\n## ")
        done, logged = mirrored("roteiro", "--action", "check", project=self.project)
        self.assertEqual(1, done.returncode, done.stderr)
        row = events(logged, "roteiro_refused")[0]
        self.assertEqual(
            {"action": "parse", "reason": "invalid", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("action", "reason", "error_code")},
        )
        self.assertGreaterEqual(int(row["problems"]), 1)
        self.assertNotIn("Texto escondido", "\n".join(logged))

    def test_a_project_without_a_roteiro_is_refused_as_missing(self):
        done, logged = mirrored("roteiro", "--action", "check", project=self.project)
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertEqual("missing", events(logged, "roteiro_refused")[0]["reason"])

    def test_an_unreadable_state_file_is_refused_by_reason(self):
        self.setup_project()
        (self.project / "brolls").mkdir(exist_ok=True)
        (self.project / "brolls" / "roteiro-state.json").write_text("{nao json", encoding="utf-8")
        self.fill_skeleton()
        self.write_brief()
        self.review()
        self.cli("roteiro", "--action", "sync", expect=1)
        self.assertEqual("state_unreadable", events(self.log(), "roteiro_refused")[-1]["reason"])


if __name__ == "__main__":
    unittest.main()
