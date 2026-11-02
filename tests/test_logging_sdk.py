"""Eventos de log do SDK novo: trava ocupada, fonte git, índice e marketplace, instalação e remoção.

Tudo offline: repositórios git locais num temporário. Cada teste confere o evento (nome, nível e
campos) e que nada sensível chegou à linha: caminho da máquina, URL, senha ou nome de arquivo.
"""

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _logs import LogCase, events, home_log_lines
from _paths import CLI_ARGV
from _symlink import needs_symlink
from test_sdk_install import HAS_GIT, InstallTestCase, git, head, write_plugin
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase
from test_sdk_marketplace_client import MarketplaceTestCase
from test_sdk_marketplace_install import REF, MarketInstallCase

from getbrolls import logs, runtime
from getbrolls.errors import LockedError, UsageError
from getbrolls.sdk import git_source, loader, marketplace, materialize
from getbrolls.sdk import install as install_mod
from getbrolls.sdk import marketplace_index as mi
from getbrolls.sdk import marketplace_install as mki
from getbrolls.sdk import remove as plugin_remove

HEX12 = r"[0-9a-f]{12}"


class LockBusyTests(LogCase):
    def collide(self, tmp, wait_s=0.0):
        """Toma a trava, tenta de novo com ela ocupada e devolve as linhas que a recusa deixou."""
        lock = Path(tmp) / ".demo.lock"
        with (
            runtime.exclusive_lock(lock, "ocupado"),
            self.captured() as cm,
            self.assertRaises(LockedError),
            runtime.exclusive_lock(lock, "ocupado", wait_s=wait_s),
        ):
            pass
        return self.rows(cm)

    def test_a_busy_lock_is_logged_with_its_name_and_the_error_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.collide(tmp)
            row = rows.one("lock_busy")
            self.assertEqual(".demo.lock", row["lock"])
            self.assertEqual("LOCKED", row["error_code"])
            self.assertEqual("0", row["waited_ms"])
            self.assertEqual(logging.WARNING, rows.level_of("lock_busy"))
            self.assert_clean(rows, tmp)

    def test_the_wait_is_part_of_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.collide(tmp, wait_s=0.2)
            self.assertGreaterEqual(int(rows.one("lock_busy")["waited_ms"]), 150)

    def test_a_free_lock_logs_nothing(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertNoLogs("getbrolls", level="DEBUG"),
            runtime.exclusive_lock(Path(tmp) / ".demo.lock", "ocupado"),
        ):
            pass


@unittest.skipUnless(HAS_GIT, "git required")
class GitSourceLogTests(InstallTestCase, LogCase):
    def repo(self):
        folder = write_plugin(self.work / "demo_repo")
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        return folder

    def fetch(self, spec):
        return git_source.fetch(spec, self.work / "clone")

    def test_a_pinned_fetch_logs_start_pin_and_end_with_the_duration(self):
        repo = self.repo()
        commit = head(repo)
        with self.captured() as cm:
            self.fetch(git_source.GitSource(str(repo), commit))
        rows = self.rows(cm)
        start = rows.one("git_fetch_start")
        self.assertEqual(
            {"host": "local", "pinned": "True", "ref_given": "False"},
            {k: start[k] for k in start if k in ("host", "pinned", "ref_given")},
        )
        verified = rows.one("git_pin_verified")
        self.assertEqual(
            {"commit": commit[:12], "pinned": "True", "via": "sha"},
            {k: verified[k] for k in ("commit", "pinned", "via")},
        )
        end = rows.one("git_fetch_end")
        self.assertEqual("ok", end["status"])
        self.assertEqual(commit[:12], end["commit"])
        self.assertTrue(end["ms"].isdigit())
        order = [row["event"] for row in rows.all() if row["event"].startswith("git_")]
        self.assertEqual(["git_fetch_start", "git_pin_verified", "git_fetch_end"], order)
        self.assert_clean(rows, repo, self.work, commit)

    def test_the_network_call_is_info_and_the_local_reads_are_debug(self):
        repo = self.repo()
        with self.captured() as cm:
            self.fetch(git_source.GitSource(str(repo), head(repo)))
        rows = self.rows(cm)
        fetch = rows.one("subprocess", tool="git", op="fetch")
        self.assertEqual(logging.INFO, rows.level_of("subprocess", op="fetch"))
        self.assertEqual({"status": "ok", "exit": "0"}, {key: fetch[key] for key in ("status", "exit")})
        self.assertEqual(logging.DEBUG, rows.level_of("subprocess", op="init"))
        self.assertEqual(logging.DEBUG, rows.level_of("subprocess", op="rev-parse"))

    def test_an_unpinned_fetch_resolves_the_ref_first(self):
        repo = self.repo()
        with self.captured() as cm:
            self.fetch(git_source.GitSource(str(repo)))
        rows = self.rows(cm)
        self.assertEqual("False", rows.one("git_fetch_start")["pinned"])
        self.assertEqual(logging.INFO, rows.level_of("subprocess", op="ls-remote"))
        self.assertEqual("False", rows.one("git_pin_verified")["pinned"])

    def test_a_commit_the_origin_does_not_have_is_refused_and_logged(self):
        repo = self.repo()
        with self.captured() as cm, self.assertRaises(ValueError):
            self.fetch(git_source.GitSource(str(repo), "a" * 40))
        rows = self.rows(cm)
        refusal = rows.one("git_source_refused", reason="ref_tip_mismatch")
        self.assertEqual("INVALID_DATA", refusal["error_code"])
        end = rows.one("git_fetch_end")
        self.assertEqual({"status": "error", "error": "ValueError"}, {key: end[key] for key in ("status", "error")})
        self.assertEqual(logging.WARNING, rows.level_of("git_fetch_end"))
        failed = rows.one("subprocess", op="fetch", status="error")
        self.assertEqual("128", failed["exit"])
        self.assertEqual(logging.WARNING, rows.level_of("subprocess", op="fetch", status="error"))
        self.assert_clean(rows, repo, self.work)

    def test_an_unknown_ref_is_refused_with_its_reason(self):
        repo = self.repo()
        with self.captured() as cm, self.assertRaises(ValueError):
            self.fetch(git_source.GitSource(str(repo), None, "ramo-secreto-do-cliente"))
        rows = self.rows(cm)
        self.assertEqual("INVALID_DATA", rows.one("git_source_refused", reason="ref_not_found")["error_code"])
        self.assert_clean(rows, "ramo-secreto-do-cliente")

    def test_a_ref_that_is_a_branch_and_a_tag_is_refused_as_ambiguous(self):
        repo = self.repo()
        git(repo, "branch", "dupla")
        git(repo, "tag", "dupla")
        with self.captured() as cm, self.assertRaises(ValueError):
            self.fetch(git_source.GitSource(str(repo), None, "dupla"))
        self.rows(cm).one("git_source_refused", reason="ref_ambiguous")

    def test_a_failed_git_process_is_a_warning_with_the_exit_code_and_no_command_line(self):
        repo = self.repo()
        with self.captured() as cm, self.assertRaises(ValueError):
            git_source.git_text(["rev-parse", "--verify", "ramo-que-nao-existe"], cwd=repo)
        rows = self.rows(cm)
        row = rows.one("subprocess", tool="git", op="rev-parse", status="error")
        self.assertEqual("128", row["exit"])
        self.assertEqual(logging.WARNING, rows.level_of("subprocess", op="rev-parse", status="error"))
        self.assert_clean(rows, "ramo-que-nao-existe", repo)

    def test_a_git_timeout_is_logged_as_a_timeout(self):
        timeout = subprocess.TimeoutExpired(["git"], 1)
        with (
            patch.object(git_source.subprocess, "run", side_effect=timeout),
            self.captured() as cm,
            self.assertRaises(ValueError),
        ):
            git_source.run_git(["-c", "core.fsmonitor=false", "ls-remote", "--", "https://example.org/x.git"])
        row = self.rows(cm).one("subprocess", tool="git", op="ls-remote")
        self.assertEqual({"status": "timeout", "exit": None}, {key: row[key] for key in ("status", "exit")})
        self.assert_clean(self.rows(cm), "example.org")

    def test_the_git_config_lookup_for_ssh_is_a_debug_subprocess_line(self):
        with self.captured() as cm:
            git_source.has_core_ssh_command(git_source.git_env())
        rows = self.rows(cm)
        row = rows.one("subprocess", tool="git", op="config")
        self.assertEqual("ok", row["status"])
        self.assertEqual(logging.DEBUG, rows.level_of("subprocess", op="config"))

    def test_refused_sources_log_their_reason_and_never_the_secret(self):
        cases = (
            ("http://example.org/segredo/demo.git", "protocol"),
            ("ext::sh -c touch-segredo", "protocol"),
            ("file:///tmp/segredo", "protocol"),
            ("https://usuario:SENHA-SECRETA@example.org/demo.git", "credentials"),
            ("https://example.org/demo.git?token=TOKEN-SECRETO", "query_or_fragment"),
            ("git@example.org:demo.git#REF-SECRETA", "query_or_fragment"),
            ("git@-evil:demo.git", "ssh_option"),
        )
        for raw, reason in cases:
            with self.subTest(source=raw), self.captured() as cm, self.assertRaises(ValueError):
                git_source.parse_source(raw)
            rows = self.rows(cm)
            refusal = rows.one("git_source_refused")
            self.assertEqual(reason, refusal["reason"])
            self.assertEqual("INVALID_DATA", refusal["error_code"])
            self.assertEqual(logging.WARNING, rows.level_of("git_source_refused"))
            self.assert_clean(rows, "SENHA-SECRETA", "TOKEN-SECRETO", "REF-SECRETA", "segredo", "example.org")

    def test_bad_pin_flags_are_refused_with_their_reasons(self):
        url = "https://example.org/demo.git"
        cases = (
            ({"commit": "abc"}, "commit_format"),
            ({"ref": "--option"}, "ref_format"),
            ({"subdir": "../fora"}, "repo_path_dots"),
            ({"subdir": ".git/x"}, "repo_path_vcs"),
            ({"subdir": "com espaco"}, "repo_path_format"),
        )
        for flags, reason in cases:
            with self.subTest(flags=flags), self.captured() as cm, self.assertRaises(ValueError):
                git_source.parse_source(url, **flags)
            self.assertEqual(reason, self.rows(cm).one("git_source_refused")["reason"])

    def test_a_gitfile_git_does_not_confirm_is_an_invalid_worktree(self):
        folder = write_plugin(self.work / "falsa_worktree")
        (folder / ".git").write_text("gitdir: /nao/existe/mesmo\n", encoding="utf-8")
        with self.captured() as cm, self.assertRaises(ValueError):
            git_source.parse_source(str(folder))
        rows = self.rows(cm)
        self.assertEqual("INVALID_DATA", rows.one("git_source_refused", reason="invalid_worktree")["error_code"])
        self.assert_clean(rows, folder, self.work)

    def test_pin_flags_on_a_plain_folder_are_refused(self):
        folder = write_plugin(self.work / "pasta_comum")
        with self.captured() as cm, self.assertRaises(ValueError):
            git_source.parse_source(str(folder), commit="a" * 40)
        self.rows(cm).one("git_source_refused", reason="pin_flags_on_folder")

    def test_a_tip_that_is_not_the_pinned_commit_is_a_warning_event(self):
        repo = self.repo()
        first = head(repo)
        (repo / "novo.txt").write_text("outro\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "mais um")
        spec = git_source.GitSource(str(repo), first, "HEAD")
        with self.captured() as cm:
            warning = git_source.tip_warning(spec, first)
        self.assertIsNotNone(warning)
        row = self.rows(cm).one("git_tip_warning")
        self.assertEqual({"reason": "not_tip", "commit": first[:12]}, {key: row[key] for key in ("reason", "commit")})

    def test_repo_host_never_carries_a_path(self):
        self.assertEqual("github.com", git_source.repo_host("https://github.com/acme-corp/segredo.git"))
        self.assertEqual("gitlab.example", git_source.repo_host("git@gitlab.example:acme/segredo.git"))
        self.assertEqual("local", git_source.repo_host(str(self.work)))


class MarketplaceIndexLogTests(LogCase):
    def index(self, **changes):
        doc = {
            "schema": "getbrolls.marketplace_index/1",
            "name": "exemplo",
            "description": "x",
            "plugins": [],
            "renames": {},
            **changes,
        }
        return json.dumps(doc).encode("utf-8")

    def test_a_valid_index_is_a_debug_line_with_counts_and_a_short_hash(self):
        data = self.index()
        with self.captured() as cm:
            mi.parse_index(data)
        rows = self.rows(cm)
        row = rows.one("marketplace_index")
        self.assertEqual(
            {
                "marketplace": "exemplo",
                "bytes": str(len(data)),
                "plugins": "0",
                "sha256": hashlib.sha256(data).hexdigest()[:12],
            },
            {key: row[key] for key in ("marketplace", "bytes", "plugins", "sha256")},
        )
        self.assertEqual(logging.DEBUG, rows.level_of("marketplace_index"))

    def test_every_way_an_index_is_refused_has_a_reason_and_the_error_code(self):
        deep = b"[" * 100 + b"]" * 100
        cases = (
            ("not_bytes", "texto"),
            ("too_large", b" " * (mi.INDEX_MAX_BYTES + 1)),
            ("not_utf8", b"\xff\xfe"),
            ("not_json", b"{"),
            ("not_json", b'{"a": 1, "a": 2}'),
            ("not_json", b'{"a": NaN}'),
            ("too_deep", deep),
            ("invalid", b"[]"),
            ("invalid", self.index(name="Nome Secreto Inválido")),
            ("invalid", self.index(name="engenheirodevideo")),
        )
        for reason, data in cases:
            with self.subTest(reason=reason, data=data[:20]), self.captured() as cm, self.assertRaises(ValueError):
                mi.parse_index(data)  # pyright: ignore[reportArgumentType]  # `not_bytes` passa texto de propósito
            rows = self.rows(cm)
            row = rows.one("marketplace_index_refused")
            self.assertEqual(reason, row["reason"])
            self.assertEqual("INVALID_DATA", row["error_code"])
            self.assertEqual(logging.WARNING, rows.level_of("marketplace_index_refused"))
            self.assert_clean(rows, "Nome Secreto")
            if reason != "not_bytes":
                self.assertTrue(row["bytes"].isdigit())


@unittest.skipUnless(HAS_GIT, "git required")
class MarketplaceLogTests(MarketplaceTestCase, LogCase):
    def test_add_logs_the_marketplace_and_nothing_that_identifies_the_machine(self):
        repo = self.index_repo()
        with self.captured() as cm:
            marketplace.add(str(repo))
        rows = self.rows(cm)
        row = rows.one("marketplace_added")
        self.assertEqual(
            {"marketplace": "exemplo", "plugins": "1", "official": "False", "host": "local", "commit": head(repo)[:12]},
            {key: row[key] for key in ("marketplace", "plugins", "official", "host", "commit")},
        )
        self.assertEqual(logging.INFO, rows.level_of("marketplace_added"))
        self.assert_clean(rows, repo, self.work, self.home)

    def test_remove_logs_how_many_installed_plugins_stay(self):
        marketplace.add(str(self.index_repo()))
        state = loader.read_state()
        state.setdefault("sources", {})["demo"] = {"source": "x", "commit": "a" * 40, "marketplace": "exemplo"}
        loader.write_state(state)
        with self.captured() as cm:
            marketplace.remove("exemplo")
        row = self.rows(cm).one("marketplace_removed")
        self.assertEqual(
            {"marketplace": "exemplo", "installed_plugins": "1"},
            {key: row[key] for key in ("marketplace", "installed_plugins")},
        )

    def test_update_logs_the_move_and_the_size_of_the_diff(self):
        repo = self.index_repo()
        marketplace.add(str(repo))
        old = head(repo)
        new = self.commit_index(repo, "exemplo", [])
        with self.captured() as cm:
            marketplace.refresh("exemplo")
        row = self.rows(cm).one("marketplace_updated")
        self.assertEqual(
            {
                "marketplace": "exemplo",
                "from_commit": old[:12],
                "to_commit": new[:12],
                "changed": "True",
                "rollback": "False",
                "added": "0",
                "removed": "1",
                "updated": "0",
                "yanked": "0",
                "installed_updates": "0",
            },
            {key: row[key] for key in row if key != "event"},
        )

    def test_an_update_that_changes_nothing_says_so(self):
        marketplace.add(str(self.index_repo()))
        with self.captured() as cm:
            marketplace.refresh()
        self.assertEqual("False", self.rows(cm).one("marketplace_updated")["changed"])

    def test_a_refused_rollback_is_logged_with_its_code_and_an_allowed_one_with_the_flag(self):
        repo = self.index_repo()
        first = head(repo)
        marketplace.add(str(repo))
        self.commit_index(repo, "exemplo", [])
        marketplace.refresh("exemplo")
        with self.captured() as cm, self.assertRaises(UsageError):
            marketplace.refresh("exemplo", commit=first)
        refusal = self.rows(cm).one("marketplace_refused", reason="rollback")
        self.assertEqual(
            {"action": "update", "marketplace": "exemplo", "error_code": "USAGE_ERROR"},
            {key: refusal[key] for key in ("action", "marketplace", "error_code")},
        )
        with self.captured() as cm:
            marketplace.refresh("exemplo", commit=first, allow_rollback=True)
        self.assertEqual("True", self.rows(cm).one("marketplace_updated")["rollback"])

    def test_duplicate_plain_folder_and_reserved_index_are_refused_with_their_reasons(self):
        plain = write_plugin(self.work / "pasta")
        (plain / mi.INDEX_NAME).write_text("{}", encoding="utf-8")
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.add(str(plain))
        self.rows(cm).one("marketplace_refused", action="add", reason="source_not_git")
        marketplace.add(str(self.index_repo()))
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.add(str(self.index_repo(folder="outro")))
        row = self.rows(cm).one("marketplace_refused", action="add", reason="already_exists")
        self.assertEqual(
            {"marketplace": "exemplo", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("marketplace", "error_code")},
        )
        reserved = self.index_repo(folder="reservado")
        self.commit_index(reserved, "engenheirodevideo", [])
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.add(str(reserved))
        self.rows(cm).one("marketplace_index_refused", reason="invalid")

    def test_an_index_that_renamed_itself_is_refused(self):
        repo = self.index_repo()
        marketplace.add(str(repo))
        self.commit_index(repo, "outro-nome", [])
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.refresh("exemplo")
        rows = self.rows(cm)
        self.assertEqual("exemplo", rows.one("marketplace_refused", reason="name_changed")["marketplace"])
        refusal = next(line for line in rows.lines() if "event=marketplace_refused" in line)
        self.assertNotIn("outro-nome", refusal)

    def test_the_policy_refusals_name_the_action(self):
        marketplace.add(str(self.index_repo(name="alfa", folder="a")))
        marketplace.add(str(self.index_repo(name="beta", folder="b")))
        marketplace.set_policy(frozenset({"alfa"}))
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.refresh("beta")
        row = self.rows(cm).one("marketplace_refused", reason="not_allowed")
        self.assertEqual(
            {"action": "update", "marketplace": "beta"}, {key: row[key] for key in ("action", "marketplace")}
        )
        marketplace.set_policy(frozenset())
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.add(str(self.index_repo(name="gama", folder="g")))
        self.rows(cm).one("marketplace_refused", action="add", reason="ceiling_empty")

    def test_a_cache_that_changed_or_vanished_is_logged_with_the_marketplace(self):
        marketplace.add(str(self.index_repo()))
        cache = marketplace.cache_path("exemplo")
        cache.write_bytes(cache.read_bytes().replace(b"Teste", b"Outro"))
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.load_index("exemplo")
        self.assertEqual("exemplo", self.rows(cm).one("marketplace_refused", reason="cache_changed")["marketplace"])
        cache.unlink()
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.load_index("exemplo")
        rows = self.rows(cm)
        rows.one("marketplace_refused", reason="cache_missing")
        self.assert_clean(rows, self.home)

    def test_one_failing_marketplace_in_a_batch_update_is_logged(self):
        marketplace.add(str(self.index_repo(name="alfa", folder="a")))
        marketplace.add(str(self.index_repo(name="beta", folder="b")))
        runtime.force_rmtree(self.work / "b")
        with self.captured() as cm:
            done = marketplace.refresh()
        self.assertEqual(["beta"], done["failed"])
        rows = self.rows(cm)
        self.assertEqual("ValueError", rows.one("marketplace_update_failed", marketplace="beta")["error"])
        self.rows(cm).one("marketplace_updated", marketplace="alfa")

    def test_a_corrupt_state_file_is_refused_without_naming_its_path(self):
        path = marketplace.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{nao json", encoding="utf-8")
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.add(str(self.index_repo()))
        rows = self.rows(cm)
        row = rows.one("marketplace_refused", reason="state_invalid")
        self.assertEqual("INVALID_DATA", row["error_code"])
        self.assert_clean(rows, self.home, path)

    def test_an_empty_search_and_a_missing_marketplace_are_refused(self):
        with self.captured() as cm, self.assertRaises(UsageError):
            marketplace.search("  ")
        self.assertEqual("USAGE_ERROR", self.rows(cm).one("marketplace_refused", reason="empty_query")["error_code"])
        with self.captured() as cm, self.assertRaises(ValueError):
            marketplace.remove("fantasma")
        self.assertEqual("fantasma", self.rows(cm).one("marketplace_refused", reason="not_found")["marketplace"])


@unittest.skipUnless(HAS_GIT, "git required")
class MarketplaceInstallLogTests(MarketInstallCase, LogCase):
    def test_the_preview_and_the_confirmation_are_logged_once_each(self):
        entry = self.market()
        with self.captured() as cm:
            preview = mki.install(REF, confirm=False, expect=None)
        preview_rows = self.rows(cm)
        row = preview_rows.one("plugin_preview", action="install")
        self.assertEqual(
            {
                "plugin": "demo",
                "version": "0.1.0",
                "source": "marketplace",
                "marketplace": "exemplo",
                "files": "2",
                "warnings": "0",
            },
            {key: row[key] for key in ("plugin", "version", "source", "marketplace", "files", "warnings")},
        )
        self.assertRegex(row["commit"], HEX12)
        self.assertGreaterEqual(int(row["permissions_added"]), 1)
        self.assert_clean(preview_rows, self.work, self.home, entry["content_sha256"])
        with self.captured() as cm:
            mki.install(REF, confirm=True, expect=preview["expect"])
        rows = self.rows(cm)
        confirmed = rows.one("plugin_confirmed", action="install", plugin="demo")
        self.assertRegex(confirmed["sha256"], HEX12)
        installed = rows.one("plugin_installed")
        self.assertEqual(
            {"plugin": "demo", "source": "marketplace", "marketplace": "exemplo"},
            {key: installed[key] for key in ("plugin", "source", "marketplace")},
        )
        self.assertEqual(1, len(rows.all("plugin_confirmed")), "a confirmação é anunciada uma vez só")

    def test_a_wrong_or_missing_expect_is_logged_as_a_refused_confirmation(self):
        self.market()
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install(REF, confirm=True, expect="0" * 64)
        row = self.rows(cm).one("plugin_confirm_refused")
        self.assertEqual(
            {"action": "install", "plugin": "demo", "reason": "expect_mismatch", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("action", "plugin", "reason", "error_code")},
        )
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install(REF, confirm=True, expect=None)
        row = self.rows(cm).one("plugin_confirm_refused")
        self.assertEqual(
            {"reason": "expect_missing", "error_code": "USAGE_ERROR"},
            {key: row[key] for key in ("reason", "error_code")},
        )

    def test_the_index_sha_is_not_enough_for_a_plugin_with_permissions(self):
        entry = self.market()
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install(REF, confirm=True, expect=entry["content_sha256"])
        row = self.rows(cm).one("marketplace_plugin_refused", reason="expect_derived")
        self.assertEqual({"action": "install", "plugin": "demo"}, {key: row[key] for key in ("action", "plugin")})

    def test_yanked_renamed_absent_and_incompatible_entries_are_refused_with_their_reasons(self):
        self.market(lambda e: {**e, "yanked": True})
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install(REF, confirm=False, expect=None)
        row = self.rows(cm).one("marketplace_plugin_refused", reason="yanked")
        self.assertEqual(
            {"action": "install", "plugin": "demo", "marketplace": "exemplo", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("action", "plugin", "marketplace", "error_code")},
        )
        marketplace.remove("exemplo")
        self.market(renames={"velho": "demo"})
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install("velho@exemplo", confirm=False, expect=None)
        self.assertEqual("velho", self.rows(cm).one("marketplace_plugin_refused", reason="renamed")["plugin"])
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install("nada@exemplo", confirm=False, expect=None)
        self.rows(cm).one("marketplace_plugin_refused", reason="absent", plugin="nada")
        marketplace.remove("exemplo")
        self.market(lambda e: {**e, "requires_getbrolls": ">=99"})
        with self.no_network(), self.captured() as cm, self.assertRaises(ValueError):
            mki.install(REF, confirm=False, expect=None)
        self.rows(cm).one("marketplace_plugin_refused", reason="incompatible", plugin="demo", marketplace="exemplo")

    def test_content_and_manifest_that_do_not_match_the_index_are_refused(self):
        self.market(lambda e: {**e, "content_sha256": "0" * 64})
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install(REF, confirm=False, expect=None)
        rows = self.rows(cm)
        rows.one("marketplace_plugin_refused", reason="content_mismatch", marketplace="exemplo")
        self.assert_clean(rows, "0" * 12)
        marketplace.remove("exemplo")
        self.market(lambda e: {**e, "permissions": {**e["permissions"], "network": []}})
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install(REF, confirm=False, expect=None)
        row = self.rows(cm).one("marketplace_plugin_refused", reason="manifest_mismatch")
        self.assertGreaterEqual(int(row["fields"]), 1)

    def test_a_marketplace_the_profile_does_not_allow_is_refused(self):
        self.market()
        marketplace.set_policy(frozenset())
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.install(REF, confirm=False, expect=None)
        self.rows(cm).one("marketplace_refused", action="install", reason="not_allowed", marketplace="exemplo")

    def test_an_installed_plugin_with_nothing_new_is_logged_as_current(self):
        self.market()
        self.confirm_install()
        with self.captured() as cm:
            done = mki.update("demo", confirm=False, expect=None)
        self.assertTrue(done["up_to_date"])
        row = self.rows(cm).one("marketplace_plugin_current")
        self.assertEqual(
            {"plugin": "demo", "marketplace": "exemplo", "version": "0.1.0"},
            {key: row[key] for key in ("plugin", "marketplace", "version")},
        )

    def test_the_batch_preview_logs_its_counts_and_a_confirmation_is_refused(self):
        self.market()
        self.confirm_install()
        with self.captured() as cm:
            mki.update_all(False, None)
        row = self.rows(cm).one("marketplace_update_all")
        self.assertEqual(
            {"plugins": "0", "up_to_date": "1", "skipped": "0", "outside": "0"},
            {key: row[key] for key in ("plugins", "up_to_date", "skipped", "outside")},
        )
        with self.captured() as cm, self.assertRaises(UsageError):
            mki.update_all(True, None)
        self.assertEqual(
            "USAGE_ERROR", self.rows(cm).one("marketplace_plugin_refused", reason="batch_confirm")["error_code"]
        )

    def test_an_update_of_a_plugin_that_did_not_come_from_a_marketplace_is_refused(self):
        self.market()
        with self.captured() as cm, self.assertRaises(ValueError):
            mki.update("demo", confirm=False, expect=None)
        self.rows(cm).one("marketplace_plugin_refused", action="update", reason="not_from_marketplace", plugin="demo")


@unittest.skipUnless(HAS_GIT, "git required")
class PluginInstallLogTests(InstallTestCase, LogCase):
    def repo(self):
        folder = write_plugin(self.work / "demo_repo")
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        return folder

    def commit_file(self, repo, name, content=b"x", message="arquivo"):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_bytes(content)
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", message)

    def test_a_git_install_logs_the_preview_the_confirmation_and_the_install(self):
        repo = self.repo()
        with self.captured() as cm:
            preview = install_mod.install(str(repo), confirm=False)
        rows = self.rows(cm)
        row = rows.one("plugin_preview", action="install")
        self.assertEqual(
            {"plugin": "demo", "source": "git", "marketplace": None, "commit": head(repo)[:12], "files": "2"},
            {key: row[key] for key in ("plugin", "source", "marketplace", "commit", "files")},
        )
        self.assert_clean(rows, repo, self.work, preview["plugin"]["sha256"])
        with self.captured() as cm:
            install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"])
        rows = self.rows(cm)
        self.assertEqual(preview["plugin"]["sha256"][:12], rows.one("plugin_confirmed", action="install")["sha256"])
        self.assertEqual("git", rows.one("plugin_installed")["source"])

    def test_a_folder_install_is_a_folder_source(self):
        source = write_plugin(self.work / "pasta_plugin")
        with self.captured() as cm:
            install_mod.install(str(source), confirm=False)
        row = self.rows(cm).one("plugin_preview")
        self.assertEqual({"source": "folder", "commit": None}, {key: row[key] for key in ("source", "commit")})

    def test_a_confirmation_without_or_with_the_wrong_expect_is_refused_and_logged(self):
        repo = self.repo()
        with self.captured() as cm, self.assertRaises(UsageError):
            install_mod.install(str(repo), confirm=True)
        self.rows(cm).one("plugin_confirm_refused", reason="expect_missing", error_code="USAGE_ERROR", plugin="demo")
        with self.captured() as cm, self.assertRaises(ValueError):
            install_mod.install(str(repo), confirm=True, expect="0" * 64)
        self.rows(cm).one("plugin_confirm_refused", reason="expect_mismatch", error_code="INVALID_DATA", plugin="demo")

    def test_installing_the_same_plugin_twice_is_refused_with_its_reason(self):
        repo = self.repo()
        preview = install_mod.install(str(repo), confirm=False)
        install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"])
        with self.captured() as cm, self.assertRaises(ValueError):
            install_mod.install(str(repo), confirm=False)
        row = self.rows(cm).one("plugin_refused", reason="already_installed")
        self.assertEqual(
            {"action": "install", "plugin": "demo", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("action", "plugin", "error_code")},
        )

    def test_a_git_lfs_pointer_is_refused_without_naming_the_file(self):
        repo = self.repo()
        pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 123456\n"
        self.commit_file(repo, "dados/modelo-secreto.bin", pointer)
        with self.captured() as cm, self.assertRaises(ValueError):
            install_mod.install(str(repo), confirm=False)
        rows = self.rows(cm)
        row = rows.one("plugin_content_refused", reason="lfs_pointer")
        self.assertEqual("INVALID_DATA", row["error_code"])
        self.assert_clean(rows, "modelo-secreto", repo, self.work)

    @needs_symlink
    def test_a_symlink_in_the_history_is_refused_by_reason(self):
        repo = self.repo()
        (repo / "atalho-secreto.py").symlink_to(repo / "plugin.py")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "link")
        (repo / "atalho-secreto.py").unlink()
        with self.captured() as cm, self.assertRaises(ValueError):
            install_mod.install(str(repo), confirm=False)
        rows = self.rows(cm)
        rows.one("plugin_content_refused", reason="link")
        self.assert_clean(rows, "atalho-secreto")

    def test_bytecode_and_oversized_and_too_many_files_are_refused_by_reason(self):
        repo = self.repo()
        self.commit_file(repo, "__pycache__/plugin.cpython-311.pyc", b"\x00", "bytecode")
        with self.captured() as cm, self.assertRaises(ValueError):
            install_mod.install(str(repo), confirm=False)
        self.rows(cm).one("plugin_content_refused", reason="bytecode")
        clean = self.work / "limpo"
        clean.mkdir()
        repo2 = write_plugin(clean / "demo_repo2")
        git(repo2, "init", "--quiet")
        self.commit_file(repo2, "grande.bin", b"x" * 50_000, "grande")
        with patch.object(materialize, "MAX_BYTES", 10_000), self.captured() as cm, self.assertRaises(ValueError):
            install_mod.install(str(repo2), confirm=False)
        self.rows(cm).one("plugin_content_refused", reason="oversized_blob")
        with patch.object(materialize, "MAX_FILES", 1), self.captured() as cm, self.assertRaises(ValueError):
            install_mod.install(str(repo2), confirm=False)
        self.rows(cm).one("plugin_content_refused", reason="too_many_files")

    def test_a_subdir_that_does_not_exist_is_refused(self):
        repo = self.repo()
        with self.captured() as cm, self.assertRaises(ValueError):
            install_mod.install(str(repo), confirm=False, subdir="nao/existe")
        rows = self.rows(cm)
        rows.one("plugin_content_refused", reason="subdir_missing")
        self.assert_clean(rows, "nao/existe")

    def test_a_name_that_is_not_nfc_is_refused_by_reason(self):
        with self.captured() as cm, self.assertRaises(ValueError):
            materialize._refuse_non_nfc("á.py", "na pasta")
        self.rows(cm).one("plugin_content_refused", reason="non_nfc")

    def test_an_update_logs_the_preview_the_confirmation_and_the_new_commit(self):
        repo = self.repo()
        preview0 = install_mod.install(str(repo), confirm=False)
        install_mod.install(str(repo), confirm=True, expect=preview0["plugin"]["sha256"])
        write_plugin(repo, {**MANIFEST, "version": "0.2.0"}, PLUGIN_CODE + "\n# v0.2.0\n")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "v0.2.0")
        with self.captured() as cm:
            preview = install_mod.update("demo", confirm=False)
        row = self.rows(cm).one("plugin_preview", action="update")
        self.assertEqual(
            {"plugin": "demo", "version": "0.2.0", "source": "git"},
            {key: row[key] for key in ("plugin", "version", "source")},
        )
        with self.captured() as cm:
            install_mod.update("demo", confirm=True, expect=preview["plugin"]["sha256"])
        rows = self.rows(cm)
        rows.one("plugin_confirmed", action="update", plugin="demo")
        updated = rows.one("plugin_updated")
        self.assertEqual(
            {"version": "0.2.0", "commit": head(repo)[:12], "source": "git"},
            {key: updated[key] for key in ("version", "commit", "source")},
        )

    def test_a_failed_swap_is_logged_with_whether_it_was_rolled_back(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        write_plugin(source, {**MANIFEST, "version": "0.2.0"}, PLUGIN_CODE + "\n# v0.2\n")
        preview2 = install_mod.update("demo", confirm=False)
        real_replace = os.replace
        fail_on_call = 2
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == fail_on_call:
                raise OSError("falha simulada")
            return real_replace(src, dst)

        with (
            patch("getbrolls.sdk.install.os.replace", side_effect=flaky),
            self.captured() as cm,
            self.assertRaises(OSError),
        ):
            install_mod.update("demo", confirm=True, expect=preview2["plugin"]["sha256"])
        row = self.rows(cm).one("plugin_swap_failed")
        self.assertEqual(
            {"plugin": "demo", "rolled_back": "True"}, {key: row[key] for key in ("plugin", "rolled_back")}
        )
        self.assertEqual(logging.WARNING, self.rows(cm).level_of("plugin_swap_failed"))

    def test_a_removal_whose_move_fails_is_logged_with_the_error_class(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        with (
            patch.object(Path, "replace", side_effect=PermissionError("x")),
            self.captured() as cm,
            self.assertRaises(ValueError),
        ):
            plugin_remove.remove("demo", confirm=True)
        row = self.rows(cm).one("plugin_remove_refused", reason="io_error")
        self.assertEqual(
            {"plugin": "demo", "error": "PermissionError", "error_code": "INVALID_DATA"},
            {key: row[key] for key in ("plugin", "error", "error_code")},
        )

    def test_an_update_of_a_plugin_without_a_recorded_origin_is_refused(self):
        with self.captured() as cm, self.assertRaises(ValueError):
            install_mod.update("demo", confirm=False)
        self.rows(cm).one("plugin_refused", action="update", reason="no_recorded_origin", plugin="demo")

    def test_an_update_whose_recorded_origin_is_gone_logs_one_refusal(self):
        """A pasta de origem sumiu: uma linha só, a do update, sem o `git_source_refused` de um
        `--source` que ninguém digitou."""
        source = write_plugin(self.work / "demo_src")
        sha = install_mod.install(str(source), confirm=False)["plugin"]["sha256"]
        install_mod.install(str(source), confirm=True, expect=sha)
        runtime.force_rmtree(source)
        with self.captured() as cm, self.assertRaises(ValueError):
            install_mod.update("demo", confirm=False)
        rows = self.rows(cm)
        row = rows.one("plugin_refused", action="update", reason="origin_unavailable", plugin="demo")
        self.assertEqual("INVALID_DATA", row["error_code"])
        self.assertEqual([], rows.all("git_source_refused"))
        self.assert_clean(rows, source, self.work)


class RemoveLogTests(LoaderTestCase, LogCase):
    def test_the_preview_is_logged_with_what_the_state_holds(self):
        self.install()
        loader.enable("demo", confirm=True)
        with self.captured() as cm:
            plugin_remove.remove("demo", confirm=False)
        row = self.rows(cm).one("plugin_remove_preview")
        self.assertEqual(
            {"plugin": "demo", "status": "enabled", "enabled": "True", "last_pin": "False", "has_source": "False"},
            {key: row[key] for key in ("plugin", "status", "enabled", "last_pin", "has_source")},
        )

    def test_an_invalid_id_and_a_missing_plugin_are_refused_with_their_reasons(self):
        with self.captured() as cm, self.assertRaises(ValueError):
            plugin_remove.remove("../segredo", confirm=False)
        rows = self.rows(cm)
        self.assertEqual("INVALID_DATA", rows.one("plugin_remove_refused", reason="invalid_id")["error_code"])
        self.assert_clean(rows, "segredo")
        with self.captured() as cm, self.assertRaises(ValueError):
            plugin_remove.remove("fantasma", confirm=False)
        rows = self.rows(cm)
        self.assertEqual("fantasma", rows.one("plugin_remove_refused", reason="not_found")["plugin"])
        self.assert_clean(rows, self.home)

    def test_a_confirmed_removal_is_logged(self):
        self.install()
        with self.captured() as cm:
            plugin_remove.remove("demo", confirm=True)
        self.assertEqual("demo", self.rows(cm).one("plugin_removed")["plugin"])


class RefusalCodeMatchesTheCliTests(LogCase):
    """A flag no formato errado é erro de uso: o `git_source_refused` diz o mesmo código do JSON de erro."""

    def run_refused(self, *args):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "GB_HOME": home, "GB_LOG_STDERR": "1", "GB_LOG_LEVEL": "INFO"}
            done = subprocess.run(
                [*CLI_ARGV, "plugins", *args], capture_output=True, text=True, encoding="utf-8", env=env, check=False
            )
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        return done, json.loads(lines[-1]), lines[:-1]

    def test_bad_flags_and_sources_are_logged_with_the_code_the_cli_returns(self):
        source = "https://example.com/x.git"
        cases = (
            (("--action", "install", "--source", source, "--commit", "zz"), "commit_format"),
            (("--action", "install", "--source", source, "--ref", "a..b"), "ref_format"),
            (("--action", "install", "--source", source, "--subdir", "../a"), "repo_path_dots"),
            (("--action", "install", "--source", "https://usuario:SENHA@example.com/x.git"), "credentials"),
            (("--action", "install", "--source", source + "?x=1"), "query_or_fragment"),
            (("--action", "marketplace-add", "--source", source, "--commit", "zz"), "commit_format"),
            (("--action", "marketplace-update", "--marketplace", "m", "--commit", "zz"), "commit_format"),
        )
        for args, reason in cases:
            with self.subTest(args=args):
                done, envelope, mirror = self.run_refused(*args)
                self.assertEqual(2, done.returncode, done.stderr)
                self.assertEqual("USAGE_ERROR", envelope["error_code"])
                refusal = events(mirror, "git_source_refused")
                self.assertEqual([reason], [row["reason"] for row in refusal], mirror)
                self.assertEqual(envelope["error_code"], refusal[0]["error_code"])
                self.assertEqual(envelope["error_code"], events(mirror, "command_end")[-1]["error_code"])
                self.assertNotIn("SENHA", "\n".join(mirror))

    def test_usage_errors_converts_and_logs_the_usage_code(self):
        with self.captured() as cm, self.assertRaises(UsageError), runtime.usage_errors():
            git_source.validate_commit("zz")
        self.assertEqual("USAGE_ERROR", self.rows(cm).one("git_source_refused")["error_code"])
        with self.captured() as cm, self.assertRaises(ValueError) as caught:
            git_source.validate_commit("zz")  # fora do bloco: o ValueError segue sendo erro de dados
        self.assertNotIsInstance(caught.exception, UsageError)
        self.assertEqual("INVALID_DATA", self.rows(cm).one("git_source_refused")["error_code"])

    def test_refused_as_only_changes_value_errors_and_the_innermost_block_wins(self):
        log = logging.getLogger("getbrolls.probe")
        with self.captured() as cm, logs.refused_as("USAGE_ERROR"):
            logs.refusal(log, "probe_refused", OSError("disco"), reason="io")
            with logs.handled():
                logs.refusal(log, "probe_refused", ValueError("x"), reason="handled")
            logs.refusal(log, "probe_refused", ValueError("x"), reason="outer")
        rows = self.rows(cm)
        self.assertEqual("IO_ERROR", rows.one("probe_refused", reason="io")["error_code"])
        self.assertIsNone(rows.one("probe_refused", reason="handled")["error_code"])
        self.assertEqual("USAGE_ERROR", rows.one("probe_refused", reason="outer")["error_code"])
        with self.captured() as cm:
            logs.refusal(log, "probe_refused", ValueError("x"), reason="after")
        self.assertEqual("INVALID_DATA", self.rows(cm).one("probe_refused")["error_code"])


@unittest.skipUnless(HAS_GIT, "git required")
class InstallationLogFileTests(MarketplaceTestCase):
    """Ponta a ponta: o que o `plugins` escreve em `$GB_HOME/logs/getbrolls.log`, sem vazar nada."""

    def run_plugins(self, *args, expect=0):
        return run_cli("plugins", *args, env={**self.env(), "GB_LOG_LEVEL": "DEBUG"}, expect=expect)

    def test_the_marketplace_and_install_flow_leaves_a_readable_trail_with_no_machine_data(self):
        repo = self.index_repo()
        self.run_plugins("--action", "marketplace-add", "--source", str(repo))
        preview = self.run_plugins("--action", "install", "--id", REF)
        self.run_plugins("--action", "install", "--id", REF, "--yes", "--expect", preview["expect"])
        self.run_plugins("--action", "update", "--id", "demo")
        self.run_plugins("--action", "remove", "--id", "demo", "--yes")
        self.run_plugins("--action", "marketplace-remove", "--marketplace", "exemplo")
        lines = home_log_lines(self.home)
        names = [
            row["event"]
            for row in events(lines)
            if row["event"]
            in {
                "marketplace_added",
                "plugin_preview",
                "plugin_confirmed",
                "plugin_installed",
                "marketplace_plugin_current",
                "plugin_removed",
                "marketplace_removed",
            }
        ]
        self.assertEqual(
            [
                "marketplace_added",
                "plugin_preview",
                "plugin_confirmed",
                "plugin_installed",
                "marketplace_plugin_current",
                "plugin_removed",
                "marketplace_removed",
            ],
            names,
        )
        text = "\n".join(lines)
        for sentinel in (str(repo), str(self.work), str(self.home), repo.name):
            self.assertNotIn(sentinel, text)
        self.assertEqual(["ok"] * 6, [row["status"] for row in events(lines, "command_end")])

    def test_a_refused_install_is_in_the_log_and_the_envelope_points_at_it(self):
        self.run_plugins("--action", "marketplace-add", "--source", str(self.index_repo()))
        failed = self.run_plugins("--action", "install", "--id", "nada@exemplo", expect=1)
        self.assertEqual(str(self.home / "logs" / "getbrolls.log"), failed["app_log"])
        lines = home_log_lines(self.home)
        refusal = events(lines, "marketplace_plugin_refused")[0]
        self.assertEqual(
            {
                "action": "install",
                "reason": "absent",
                "plugin": "nada",
                "marketplace": "exemplo",
                "error_code": "INVALID_DATA",
            },
            {key: refusal[key] for key in ("action", "reason", "plugin", "marketplace", "error_code")},
        )
        ended = events(lines, "command_end")[-1]
        self.assertEqual(
            {"status": "error", "error_code": "INVALID_DATA"}, {key: ended[key] for key in ("status", "error_code")}
        )

    def test_the_hash_of_the_index_matches_the_cache(self):
        repo = self.index_repo()
        self.run_plugins("--action", "marketplace-add", "--source", str(repo))
        self.run_plugins("--action", "marketplace-list")  # leitura: não grava
        digest = hashlib.sha256(marketplace.cache_path("exemplo").read_bytes()).hexdigest()[:12]
        shown = [row["sha256"] for row in events(home_log_lines(self.home), "marketplace_index")]
        self.assertIn(digest, shown)
        self.assertEqual(1, len(events(home_log_lines(self.home), "command_start")), "list não abre o log")


if __name__ == "__main__":
    unittest.main()
