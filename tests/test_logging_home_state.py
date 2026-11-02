"""Eventos de log do que muda a instalação: clientes, receitas e perfil confiável.

Cada teste confere o evento (nome, nível e campos) e que nada sensível chegou à linha: nome ou
slug de cliente, slug de template, caminho da máquina e texto digitado.
"""

import json
import logging
import os
import stat
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _logs import LogCase, events, home_log_lines
from _symlink import needs_symlink
from test_clients import ClientsTestCase, _can_symlink
from test_profile_cli import ProfileCliCase, sha, write
from test_recipe_init import RecipeInitCase
from test_recipe_store import RecipeCase

from getbrolls import clients, file_locks, private_files, profile, profile_trust, recipe_init, recipes
from getbrolls.errors import LockedError


class ClientLogTests(ClientsTestCase, LogCase):
    def test_add_and_remove_log_counts_and_never_the_name_the_slug_or_the_path(self):
        with self.captured() as cm:
            clients.add("acme-secreto", str(self.base), name="ACME Segredo Ltda")
        rows = self.rows(cm)
        added = rows.one("client_added")
        self.assertEqual({"created": "True", "clients": "1"}, {key: added[key] for key in ("created", "clients")})
        self.assertEqual(logging.INFO, rows.level_of("client_added"))
        self.assert_clean(rows, "acme-secreto", "ACME Segredo", self.base, self.tmp, self.home)
        with self.captured() as cm:
            clients.add("outro", str(self.base))
        self.assertEqual("2", self.rows(cm).one("client_added")["clients"])
        with self.captured() as cm:
            clients.remove("acme-secreto")
        rows = self.rows(cm)
        self.assertEqual("1", rows.one("client_removed")["clients"])
        self.assert_clean(rows, "acme-secreto")

    def test_registering_a_folder_that_already_has_a_client_says_it_was_not_created(self):
        clients.add("acme-secreto", str(self.base))
        clients.remove("acme-secreto")
        with self.captured() as cm:
            clients.add("acme-secreto", str(self.base))
        self.assertEqual("False", self.rows(cm).one("client_added")["created"])

    def test_every_refusal_carries_an_action_a_reason_and_the_error_code_but_no_client_data(self):
        clients.add("acme-secreto", str(self.base), name="ACME Segredo Ltda")
        (self.home / "plugins").mkdir()
        cases = (
            (lambda: clients.add("acme-secreto", str(self.base)), "add", "already_registered"),
            (lambda: clients.add("novo", "relativo/pasta"), "add", "root_not_absolute"),
            (lambda: clients.add("novo", str(self.tmp / "nao-existe")), "add", "root_missing"),
            (lambda: clients.add("novo", str(Path.home())), "add", "root_too_broad"),
            (lambda: clients.add("novo", str(self.home / "plugins")), "add", "inside_installation"),
            (lambda: clients.add("gb-home", str(self.tmp)), "add", "contains_home"),
            (lambda: clients.remove("fantasma-secreto"), "remove", "not_registered"),
            (lambda: clients.show("fantasma-secreto"), "load", "not_registered"),
        )
        for call, action, reason in cases:
            with self.subTest(reason=reason), self.captured() as cm, self.assertRaises(ValueError):
                call()
            rows = self.rows(cm)
            row = rows.one("client_refused", reason=reason)
            self.assertEqual(
                {"action": action, "error_code": "INVALID_DATA"}, {key: row[key] for key in ("action", "error_code")}
            )
            self.assertEqual(logging.WARNING, rows.level_of("client_refused", reason=reason))
            self.assert_clean(rows, "acme-secreto", "fantasma-secreto", "ACME Segredo", self.tmp)

    @needs_symlink
    def test_a_root_that_is_a_link_is_refused(self):
        if not _can_symlink(self.base):
            self.skipTest("sem permissão para criar link")
        real = self.tmp / "real"
        real.mkdir()
        link = self.tmp / "atalho"
        link.symlink_to(real, target_is_directory=True)
        with self.captured() as cm, self.assertRaises(ValueError):
            clients.add("novo", str(link))
        self.rows(cm).one("client_refused", reason="root_link")

    def test_a_corrupt_registry_is_refused_without_naming_the_file(self):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "clients.json").write_text("{nao json", encoding="utf-8")
        with self.captured() as cm, self.assertRaises(ValueError):
            clients.add("novo", str(self.base))
        rows = self.rows(cm)
        self.assertEqual("INVALID_DATA", rows.one("client_refused", reason="registry_invalid")["error_code"])
        self.assert_clean(rows, self.home)

    def test_a_registered_folder_that_vanished_is_logged_when_it_is_loaded(self):
        clients.add("acme-secreto", str(self.base))
        (self.base / "acme-secreto" / "client.json").unlink()
        with self.captured() as cm, self.assertRaises(ValueError):
            clients.load_client("acme-secreto")
        self.rows(cm).one("client_refused", reason="client_file_invalid")
        private_files.force_rmtree(self.base / "acme-secreto")
        with self.captured() as cm, self.assertRaises(ValueError):
            clients.load_client("acme-secreto")
        self.rows(cm).one("client_refused", reason="folder_missing")


class RecipeLogTests(RecipeCase, LogCase):
    def test_seal_logs_counts_and_never_the_slug_the_title_the_client_or_the_speech(self):
        with self.captured() as cm:
            result = recipes.seal_project(self.project, "reels-secreto", title="Título Secreto", by="Fulano Secreto")
        rows = self.rows(cm)
        row = rows.one("recipe_sealed")
        self.assertEqual(
            {"version": "1", "scenes": "2", "components": str(len(result["components"])), "warnings": "1"},
            {key: row[key] for key in ("version", "scenes", "components", "warnings")},
        )
        self.assertRegex(row["projeto_id"], r"^[0-9a-f-]{36}$")
        self.assert_clean(
            rows,
            "reels-secreto",
            "Título Secreto",
            "Fulano Secreto",
            "cliente-exemplo",
            self.tmp,
            "Fala secreta",
            "48213",
        )

    def test_show_logs_the_seal_and_the_integrity_check(self):
        self.seal()
        with self.captured() as cm:
            shown = recipes.show("cliente-exemplo.reels-split@1")
        rows = self.rows(cm)
        seal = rows.one("recipe_seal")
        self.assertEqual(
            {"result": "ok", "version": "1", "sha256": shown["recipe_sha256"][:12]},
            {key: seal[key] for key in ("result", "version", "sha256")},
        )
        check = rows.one("recipe_check")
        self.assertEqual(
            {"version": "1", "intact": "True", "problems": "0"},
            {key: check[key] for key in ("version", "intact", "problems")},
        )
        self.assert_clean(rows, "reels-split", "cliente-exemplo", self.tmp)

    def test_refusals_name_the_action_and_the_reason(self):
        with self.captured() as cm, self.assertRaises(ValueError):
            recipes.seal_project(self.project, "reels-secreto", client="outro-cliente")
        row = self.rows(cm).one("recipe_refused", reason="client_mismatch")
        self.assertEqual(
            {"action": "seal", "error_code": "INVALID_DATA"}, {key: row[key] for key in ("action", "error_code")}
        )
        with self.captured() as cm, self.assertRaises(ValueError):
            recipes.show("cliente-exemplo.nada@1")
        self.rows(cm).one("recipe_refused", action="show", reason="not_found")


class RecipeApplyLogTests(RecipeInitCase):
    """`init --recipe` pela CLI: o log do projeto nasce depois que o projeto existe."""

    def log_lines(self):
        path = self.new / "brolls" / "getbrolls.log"
        return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    def test_a_recipe_project_logs_the_creation_and_the_recipe_without_names_or_paths(self):
        out = self.init()
        lines = self.log_lines()
        created = events(lines, "init_created")[0]
        self.assertEqual(
            {
                "projeto_id": out["id"],
                "layout": "1",
                "folders": str(len(out["folders"])),
                "recipe": "True",
                "client_set": "True",
                "canvas_set": "False",
                "fps_set": "False",
            },
            {
                key: created[key]
                for key in ("projeto_id", "layout", "folders", "recipe", "client_set", "canvas_set", "fps_set")
            },
        )
        applied = events(lines, "recipe_applied")[0]
        self.assertEqual(
            (out["id"], "1", "2", str(len(out["items"])), "True"),
            (applied["projeto_id"], applied["version"], applied["scenes"], applied["items"], applied["rules_seeded"]),
        )
        self.assertEqual("ok", events(lines, "command_end")[-1]["status"])
        text = "\n".join(lines)
        for sentinel in ("reels-split", "cliente-exemplo", "Novo: vídeo #2", str(self.tmp)):
            self.assertNotIn(sentinel, text)

    def test_a_refused_recipe_init_leaves_no_log_and_no_folder_behind(self):
        path = self.writable(self.slug_dir() / "1" / recipes.RECIPE_FILE)
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.init(expect=1)
        self.assertFalse(self.new.exists())


class RecipeInitRefusalLogTests(RecipeInitCase, LogCase):
    """`init --recipe` recusado em processo: o evento leva a ação, o motivo e contagens, nada que a pessoa digitou."""

    def plan(self, project=None, tema="Tema Secreto"):
        return recipe_init.plan_instance(project or self.new, "cliente-exemplo.reels-split@1", "cliente-exemplo", tema)

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_a_recipe_that_is_not_intact_is_refused_for_init_with_the_problem_count(self):
        path = self.writable(self.slug_dir() / "1" / recipes.RECIPE_FILE)
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.captured() as cm, self.assertRaises(ValueError):
            self.plan()
        rows = self.rows(cm)
        row = rows.one("recipe_refused", reason="not_intact")
        self.assertEqual(
            {"action": "init", "problems": "1", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("action", "problems", "error_code")},
        )
        self.assert_clean(rows, "Tema Secreto", "video-02", "cliente-exemplo", "reels-split", self.tmp)

    def test_a_project_that_exists_is_refused_with_its_reason(self):
        self.init()
        with self.captured() as cm, self.assertRaises(ValueError):
            self.plan()
        rows = self.rows(cm)
        row = rows.one("recipe_refused", action="init", reason="project_exists")
        self.assertEqual("INVALID_DATA", row["error_code"])
        self.assert_clean(rows, "Tema Secreto", "video-02", "cliente-exemplo", "reels-split", self.tmp)


class ProfileLogTests(ProfileCliCase):
    def log_events(self, name=None):
        return events(home_log_lines(self.home), name)

    def test_confirmation_and_untrust_leave_a_trail_in_the_installation_log(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.json_out(self.run_cli("profile", "--action", "trust"))
        self.json_out(
            self.run_cli("profile", "--action", "trust", "--path", self.toml, "--yes", "--expect", sha(self.toml))
        )
        self.json_out(self.run_cli("profile", "--action", "untrust"))
        names = [row["event"] for row in self.log_events() if row["event"].startswith("profile_")]
        self.assertEqual(["profile_trusted", "profile_untrusted"], names)
        self.assertEqual(sha(self.toml)[:12], self.log_events("profile_trusted")[0]["sha256"])
        self.assertEqual("True", self.log_events("profile_untrusted")[0]["removed"])
        text = "\n".join(home_log_lines(self.home))
        for sentinel in (str(self.toml), str(self.ws), str(self.tmp), "cache_dir", sha(self.toml)):
            self.assertNotIn(sentinel, text)

    def test_show_is_read_only_and_creates_no_log(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.json_out(self.run_cli("profile", "--action", "show"))
        self.assertFalse((self.home / "logs").exists())

    def test_every_rename_refusal_is_logged_and_none_creates_a_file(self):
        """`renamed_refused` leva só o motivo; recusa não abre o log da instalação (como `command_refused`)."""
        write(self.toml, 'cache_dir = "c"\n')
        routes = {
            "parse": ("profile", "show"),
            "run": ("profile", "--action", "show", "velho.toml"),
            "run-trust-yes": ("profile", "--action", "trust", "--yes", "--expect", "X", "velho.toml"),
        }
        for name, args in routes.items():
            with self.subTest(route=name):
                fresh = self.tmp / f"home-{name}"
                done = self.run_cli(*args, env=self.env(GB_HOME=str(fresh), GB_LOG_STDERR="1"))
                self.assertEqual(2, done.returncode, done.stderr)
                rows = events(done.stderr.splitlines(), "renamed_refused")
                self.assertEqual(1, len(rows), done.stderr)
                self.assertEqual(("profile_positional", "USAGE_ERROR"), (rows[0]["reason"], rows[0]["error_code"]))
                self.assertNotIn("velho", done.stderr.split("renamed_refused")[1].splitlines()[0])
                self.assertFalse((fresh / "logs").exists(), "a recusa criou logs/")

    def test_the_trust_preview_only_reads_and_its_line_goes_to_the_stderr_mirror_only(self):
        """A prévia não grava nada: nem `$GB_HOME` nem `logs/` nascem dela."""
        write(self.toml, 'cache_dir = "c"\n')
        fresh = self.tmp / "nova-home"
        done = self.run_cli("profile", "--action", "trust", env=self.env(GB_HOME=str(fresh), GB_LOG_STDERR="1"))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertFalse(fresh.exists(), "a prévia criou o GB_HOME")
        preview = events(done.stderr.splitlines(), "profile_trust_preview")[0]
        # O aviso é o `cache_dir` do perfil cair dentro da pasta do toml.
        self.assertEqual(
            {"sha256": sha(self.toml)[:12], "requires": "False", "would_set": "1", "warnings": "1"},
            {key: preview[key] for key in ("sha256", "requires", "would_set", "warnings")},
        )

    def test_a_refused_untrust_is_logged_as_untrust(self):
        done = self.run_cli("profile", "--action", "untrust")  # nenhum getbrolls.toml no caminho
        self.assertEqual(2, done.returncode, done.stderr)
        row = self.log_events("profile_refused")[0]
        self.assertEqual(
            {"action": "untrust", "reason": "no_profile", "error_code": "USAGE_ERROR"},
            {key: row[key] for key in ("action", "reason", "error_code")},
        )

    def test_a_profile_error_with_a_project_points_at_the_installation_log(self):
        """`profile` grava a instalação mesmo com `--project`: o `app_log` do erro é o log dela."""
        project = self.ws / "p"
        self.json_out(self.run_cli("init-rules", "--project", project))
        project_log = project / "brolls" / "getbrolls.log"
        plain = self.tmp / "pasta-qualquer"
        plain.mkdir()
        for folder in (project, plain):
            with self.subTest(project=folder.name):
                done = self.run_cli("profile", "--action", "untrust", "--project", folder)
                self.assertEqual(2, done.returncode, done.stderr)
                self.assertEqual(str(self.home / "logs" / "getbrolls.log"), json.loads(done.stderr)["app_log"])
        self.assertEqual(2, len(self.log_events("profile_refused")))
        self.assertNotIn("profile_refused", project_log.read_text(encoding="utf-8"))

    def test_a_refused_confirmation_with_a_project_points_at_the_installation_log(self):
        project = self.ws / "p"
        self.json_out(self.run_cli("init-rules", "--project", project))  # antes do toml, que ainda não é confiável
        write(self.toml, 'cache_dir = "c"\n')
        done = self.run_cli("profile", "--action", "trust", "--project", project, "--yes", "--expect", "0" * 64)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual(str(self.home / "logs" / "getbrolls.log"), json.loads(done.stderr)["app_log"])
        self.assertEqual("expect_mismatch", self.log_events("profile_refused")[0]["reason"])

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_trust_and_untrust_on_a_fresh_gb_home_keep_it_0700(self):
        write(self.toml, 'cache_dir = "c"\n')
        previous = os.umask(0o022)
        try:
            for action in ("trust", "untrust"):
                with self.subTest(action=action):
                    fresh = self.tmp / f"home-{action}"
                    args = ("profile", "--action", "trust", "--path", self.toml, "--yes", "--expect", sha(self.toml))
                    if action == "untrust":
                        args = ("profile", "--action", "untrust", "--path", self.toml)
                    self.json_out(self.run_cli(*args, env=self.env(GB_HOME=str(fresh))))
                    self.assertEqual(0o700, stat.S_IMODE(fresh.stat().st_mode))
                    self.assertEqual(0o700, stat.S_IMODE((fresh / "logs").stat().st_mode))
        finally:
            os.umask(previous)

    def test_a_refused_trust_without_a_profile_is_logged_as_trust(self):
        done = self.run_cli("profile", "--action", "trust", "--yes", "--expect", "0" * 64)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual(
            ("trust", "no_profile"), tuple(self.log_events("profile_refused")[0][key] for key in ("action", "reason"))
        )

    def test_a_wrong_or_missing_expect_is_logged_with_the_usage_error_code(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.run_cli("profile", "--action", "trust", "--path", self.toml, "--yes", "--expect", "0" * 64)
        self.run_cli("profile", "--action", "trust", "--path", self.toml, "--yes")
        rows = self.log_events("profile_refused")
        self.assertEqual(
            [("trust", "expect_mismatch", "USAGE_ERROR"), ("trust", "expect_missing", "USAGE_ERROR")],
            [(row["action"], row["reason"], row["error_code"]) for row in rows],
        )

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_an_unsafe_profile_file_is_refused_by_reason(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.toml.chmod(0o666)
        self.run_cli("profile", "--action", "trust", "--path", self.toml, "--yes", "--expect", sha(self.toml))
        self.assertEqual("group_writable", self.log_events("profile_refused")[0]["reason"])
        self.toml.chmod(0o644)
        link = self.ws / "outro.toml"
        link.symlink_to(self.toml)
        self.run_cli("profile", "--action", "trust", "--path", link, "--yes", "--expect", sha(self.toml))
        self.assertEqual("link", self.log_events("profile_refused")[-1]["reason"])
        self.assertNotIn("outro.toml", "\n".join(home_log_lines(self.home)))

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_a_corrupt_trust_store_is_refused_and_named_by_its_reason_only(self):
        write(self.toml, 'cache_dir = "c"\n')
        store = self.home / "trusted-profiles.json"
        store.write_text("{nao json", encoding="utf-8")
        store.chmod(0o600)
        self.run_cli("profile", "--action", "trust", "--path", self.toml, "--yes", "--expect", sha(self.toml))
        row = self.log_events("profile_refused")[0]
        self.assertEqual(
            {"action": "store", "reason": "store_corrupt"}, {key: row[key] for key in ("action", "reason")}
        )
        self.assertNotIn("trusted-profiles", "\n".join(home_log_lines(self.home)))

    def test_the_busy_trust_store_is_logged_as_a_busy_lock(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.home.mkdir(exist_ok=True)
        lock_path = self.home / f"{profile.TRUST_FILE}.lock"
        with lock_path.open("a+", encoding="utf-8") as held, patch.object(profile_trust, "LOCK_TIMEOUT_S", 0.1):
            file_locks._acquire_lock(held)  # pylint: disable=protected-access
            try:
                with (
                    patch.dict(os.environ, {"GB_HOME": str(self.home)}),
                    self.assertLogs("getbrolls", "DEBUG") as cm,
                    self.assertRaises(LockedError),
                ):
                    profile.trust(
                        self.toml,
                        located=profile.Located(self.toml, "flag", False, (self.toml,)),
                        yes=True,
                        expect=sha(self.toml),
                    )
            finally:
                file_locks._release_lock(held)  # pylint: disable=protected-access
        row = events([record.getMessage() for record in cm.records], "lock_busy")[0]
        self.assertEqual(
            {"lock": lock_path.name, "error_code": "LOCKED"}, {key: row[key] for key in ("lock", "error_code")}
        )
        self.assertGreaterEqual(int(row["waited_ms"]), 100)


class EarlyRefusalLogTests(ProfileCliCase):
    """O perfil e o `.env` param o comando antes do log: nenhum arquivo, só o espelho do stderr."""

    def stderr_lines(self, *args):
        done = self.run_cli(*args, env=self.env(GB_LOG_STDERR="1"))
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        return done, lines

    def refused_row(self, lines):
        rows = events(lines[:-1], "command_refused")
        self.assertEqual(1, len(rows), lines)
        return rows[0]

    def test_an_untrusted_profile_is_mirrored_with_its_reason_and_creates_no_file(self):
        write(self.toml, 'cache_dir = "c"\n')
        (self.ws / "p").mkdir()
        done, lines = self.stderr_lines("status", "--project", self.ws / "p")
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual("USAGE_ERROR", json.loads(lines[-1])["error_code"])
        row = self.refused_row(lines)
        self.assertEqual(
            {"command": "status", "error_code": "USAGE_ERROR", "reason": "untrusted"},
            {key: row[key] for key in ("command", "error_code", "reason")},
        )
        self.assertFalse((self.home / "logs").exists())
        self.assertFalse((self.ws / "p" / "brolls").exists())
        self.assertNotIn(str(self.ws), "\n".join(lines[:-1]))

    def test_a_changed_profile_and_an_unmet_requires_have_their_own_reasons(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.trust()
        write(self.toml, 'cache_dir = "d"\n')
        (self.ws / "p").mkdir()
        _done, lines = self.stderr_lines("status", "--project", self.ws / "p")
        self.assertEqual("changed", self.refused_row(lines)["reason"])
        write(self.toml, 'requires = ">=99"\n')
        self.trust()
        done, lines = self.stderr_lines("status", "--project", self.ws / "p")
        self.assertEqual(4, done.returncode, done.stderr)
        row = self.refused_row(lines)
        self.assertEqual(
            {"error_code": "PREREQUISITE_MISSING", "reason": "requires"},
            {key: row[key] for key in ("error_code", "reason")},
        )

    def test_an_invalid_profile_is_refused_as_invalid(self):
        write(self.toml, "isto não é toml = = =\n")
        (self.ws / "p").mkdir()
        _done, lines = self.stderr_lines("status", "--project", self.ws / "p")
        self.assertEqual("invalid", self.refused_row(lines)["reason"])

    def test_a_refused_env_file_has_a_stable_reason(self):
        missing = self.ws / "nao-existe.env"
        process_only = self.ws / "processo.env"
        process_only.write_text("GB_ENV_FILE=/outro.env\n", encoding="utf-8")
        home_env = self.home / ".env"
        home_env.write_text(f"GB_HOME={self.home}\n", encoding="utf-8")
        cases = (
            (("--env-file", missing, "capabilities"), {}, "env_missing"),
            (("capabilities",), {"GB_ENV_FILE": str(missing)}, "env_missing"),
            (("--env-file", process_only, "capabilities"), {}, "env_process_only_key"),
            (("--env-file", home_env, "capabilities"), {}, "env_gb_home"),
        )
        for args, extra, reason in cases:
            with self.subTest(reason=reason, args=args):
                done = self.run_cli(*args, env=self.env(GB_LOG_STDERR="1", **extra))
                lines = [line for line in done.stderr.splitlines() if line.strip()]
                self.assertEqual(2, done.returncode, done.stderr)
                row = self.refused_row(lines)
                self.assertEqual(
                    {"command": "capabilities", "error_code": "USAGE_ERROR", "reason": reason},
                    {key: row[key] for key in ("command", "error_code", "reason")},
                )
                self.assertNotIn(str(self.ws), "\n".join(lines[:-1]))

    def test_without_the_mirror_nothing_is_printed_besides_the_envelope(self):
        write(self.toml, 'cache_dir = "c"\n')
        (self.ws / "p").mkdir()
        done = self.run_cli("status", "--project", self.ws / "p")
        self.assertEqual(1, len([line for line in done.stderr.splitlines() if line.strip()]))


if __name__ == "__main__":
    unittest.main()
