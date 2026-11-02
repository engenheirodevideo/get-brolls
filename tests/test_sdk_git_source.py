"""`GitSource`/`FolderSource`: forma de `--source`, `--commit` e `--ref`, sem rodar git."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from test_sdk_install import HAS_GIT, _git_test_env, git, head

from getbrolls import runtime
from getbrolls.sdk import git_source
from getbrolls.sdk.git_source import FolderSource, GitSource, parse_source

SHA = "0123456789abcdef0123456789abcdef01234567"


class CommitAndRefTests(unittest.TestCase):
    def test_only_the_full_lowercase_forty_hex_commit_is_accepted(self):
        self.assertEqual(SHA, git_source.validate_commit(SHA))
        for bad in (SHA[:12], SHA.upper(), SHA + "0" * 24, SHA[:-1] + "g", "", "-" + SHA[1:], None, 7):
            with self.subTest(commit=bad), self.assertRaises(ValueError) as ctx:
                git_source.validate_commit(bad)
            self.assertIn("40", str(ctx.exception))

    def test_ordinary_refs_are_accepted(self):
        for good in ("main", "HEAD", "v1.2.0", "feature/x-y", "refs/tags/v1", "refs/heads/main", "release+1"):
            with self.subTest(ref=good):
                self.assertEqual(good, git_source.validate_ref(good))

    def test_option_injection_and_malformed_refs_are_refused(self):
        for bad in (
            "--upload-pack=touch x",
            "-x",
            "a..b",
            "a//b",
            "a/",
            "a.",
            "x.lock",
            "sub/y.lock/z",
            ".hidden",
            "a/.b",
            "/abs",
            "a b",
            "a~1",
            "a^",
            "a:b",
            "a@{1}",
            "a\\b",
            "",
            "x" * 201,
            None,
        ):
            with self.subTest(ref=bad), self.assertRaises(ValueError):
                git_source.validate_ref(bad)


class ParseSourceTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="gb-gitsrc-"))
        self.addCleanup(runtime.force_rmtree, self.work)

    def test_urls_become_git_sources_with_commit_and_ref(self):
        spec = parse_source("https://example.com/demo.git", commit=SHA, ref="main")
        self.assertEqual(GitSource("https://example.com/demo.git", SHA, "main", None), spec)
        self.assertTrue(git_source.is_remote("https://example.com/demo.git"))
        self.assertEqual(GitSource("git@example.com:org/demo.git"), parse_source("git@example.com:org/demo.git"))
        self.assertTrue(git_source.is_ssh("git@example.com:org/demo.git"))

    def test_unsupported_transports_and_credentials_are_refused(self):
        for bad in (
            "file:///tmp/demo",
            "ext::sh -c touch% x",
            "http://example.com/demo.git",
            "ssh://example.com/demo.git",
            "-uhttps://example.com/x",
            "https://user:secret@example.com/demo.git",
            "https://example.com/demo.git?token=x",
            "git@example.com:demo.git#ref",
            "/nao/existe/demo",
        ):
            with self.subTest(source=bad), self.assertRaises(ValueError) as ctx:
                parse_source(bad)
            self.assertNotIn("secret", str(ctx.exception))

    def test_bad_commit_or_ref_is_refused_before_anything_else(self):
        with self.assertRaises(ValueError):
            parse_source("https://example.com/demo.git", commit=SHA[:7])
        with self.assertRaises(ValueError):
            parse_source("https://example.com/demo.git", ref="--upload-pack=x")

    def test_plain_folder_is_a_folder_source_and_refuses_git_only_options(self):
        folder = self.work / "plugin"
        folder.mkdir()
        self.assertEqual(FolderSource(str(folder.resolve())), parse_source(str(folder)))
        for extra in ({"commit": SHA}, {"ref": "main"}, {"subdir": "x"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError) as ctx:
                parse_source(str(folder), **extra)
            self.assertIn("pasta", str(ctx.exception))

    def test_folder_with_a_real_git_dir_or_bare_layout_is_a_repository(self):
        worktree = self.work / "worktree"
        (worktree / ".git").mkdir(parents=True)
        self.assertEqual(GitSource(str(worktree.resolve())), parse_source(str(worktree)))
        bare = self.work / "bare.git"
        (bare / "objects").mkdir(parents=True)
        (bare / "refs").mkdir()
        (bare / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        self.assertIsInstance(parse_source(str(bare), ref="main"), GitSource)
        self.assertFalse(git_source.is_remote(str(bare)))

    def test_unconfirmed_gitfile_is_refused_not_copied_as_a_folder(self):
        folder = self.work / "linked"
        folder.mkdir()
        (folder / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            parse_source(str(folder))
        self.assertIn(".git", str(ctx.exception))

    def test_ssh_host_starting_with_dash_is_refused_like_the_index(self):
        for bad in ("git@-oProxyCommand=x:repo.git", "git@-x:org/repo.git", "https:///sem-host.git"):
            with self.subTest(source=bad), self.assertRaises(ValueError):
                git_source.validate_url(bad)
            with self.subTest(source=bad), self.assertRaises(ValueError):
                parse_source(bad)

    @unittest.skipIf(os.name == "nt", "':' não vale em nome de pasta no Windows")
    def test_a_local_folder_named_like_a_url_never_shadows_the_url(self):
        url = "https://example.com/demo.git"
        shadow = self.work / "https:" / "example.com" / "demo.git"
        shadow.mkdir(parents=True)
        (shadow / ".git").mkdir()
        previous = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, previous)
        self.assertTrue(Path(url).is_dir(), "a pasta existe com o mesmo texto da URL")
        self.assertEqual(GitSource(url), parse_source(url))


@unittest.skipUnless(HAS_GIT, "git required")
class DescendsFromTests(unittest.TestCase):
    """O commit novo que só uma tag alcança não pode virar "volta atrás" porque o
    clone da história o buscou raso (`--depth 1`) e perdeu o pai dele."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="gb-history-test-"))
        self.addCleanup(runtime.force_rmtree, self.work)
        self.repo = self.work / "origem"
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "--initial-branch", "main")
        self.commit("a.txt", "A")
        self.first = head(self.repo)

    def commit(self, name, text):
        (self.repo / name).write_text(text, encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", text)

    def test_child_reachable_only_from_a_tag_is_not_a_rollback(self):
        git(self.repo, "checkout", "--quiet", "-b", "lado")
        self.commit("b.txt", "B")
        child = head(self.repo)
        git(self.repo, "tag", "v2")
        git(self.repo, "checkout", "--quiet", "main")
        git(self.repo, "branch", "--quiet", "-D", "lado")
        spec = GitSource(str(self.repo.resolve()))
        self.assertTrue(git_source.descends_from(spec, self.first, child))

    def test_the_tag_only_commit_comes_through_the_clone_remote(self):
        """O fetch da história pede o commit ao remote `origin` do clone parcial, não à URI crua:
        com a URI, o git novo grava um segundo promisor remote no clone, e o git 2.20 a 2.23 morre
        com "--filter can only be used with the remote configured in extensions.partialclone"."""
        git(self.repo, "config", "uploadpack.allowFilter", "true")  # o filtro vale de verdade
        git(self.repo, "checkout", "--quiet", "-b", "lado")
        self.commit("b.txt", "B")
        child = head(self.repo)
        git(self.repo, "tag", "v2")
        git(self.repo, "checkout", "--quiet", "main")
        git(self.repo, "branch", "--quiet", "-D", "lado")
        kept = []
        with patch.object(git_source, "force_rmtree", kept.append):
            self.assertTrue(git_source.descends_from(GitSource(str(self.repo.resolve())), self.first, child))
        for folder in kept:
            self.addCleanup(runtime.force_rmtree, folder)
        promisors = subprocess.run(
            ["git", "config", "--get-regexp", r"^remote\..*\.promisor$"],
            cwd=kept[0] / "history.git",
            check=True,
            capture_output=True,
            text=True,
            env=_git_test_env(),
        ).stdout.split()
        self.assertEqual(["remote.origin.promisor", "true"], promisors)

    def test_a_commit_outside_the_history_is_still_a_rollback(self):
        git(self.repo, "checkout", "--quiet", "--orphan", "reescrita")
        self.commit("c.txt", "C")
        rewritten = head(self.repo)
        git(self.repo, "tag", "reescrita-v1")
        git(self.repo, "checkout", "--quiet", "main")
        git(self.repo, "branch", "--quiet", "-D", "reescrita")
        spec = GitSource(str(self.repo.resolve()))
        self.assertFalse(git_source.descends_from(spec, self.first, rewritten))


@unittest.skipUnless(HAS_GIT, "git required")
class OutsideTheCurrentRepoTests(unittest.TestCase):
    """O git que roda antes de existir um clone (`ls-remote`, `init`, `clone --bare`) não lê a configuração
    do repositório em que a pessoa está: um `url.<x>.insteadOf` dali redirecionaria a busca."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="gb-cwd-test-")).resolve()
        self.addCleanup(runtime.force_rmtree, self.work)
        self.repo = self.work / "origem"
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "--initial-branch", "main")
        (self.repo / "a.txt").write_text("A", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "A")
        self.first = head(self.repo)
        (self.repo / "b.txt").write_text("B", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "B")
        self.second = head(self.repo)
        hostile = self.work / "hostil"
        hostile.mkdir()
        git(hostile, "init", "--quiet")
        git(hostile, "config", f"url.{(self.work / 'sumiu').as_uri()}/.insteadOf", "file:///")
        previous = Path.cwd()
        os.chdir(hostile)
        self.addCleanup(os.chdir, previous)
        self.spec = GitSource(str(self.repo))

    def test_ls_remote_ignores_the_current_repo_config(self):
        self.assertEqual(("HEAD", self.second), git_source.resolve_ref(self.spec))

    def test_fetch_ignores_the_current_repo_config(self):
        self.assertEqual(self.second, git_source.fetch(self.spec, self.work / "clone"))

    def test_history_clone_ignores_the_current_repo_config(self):
        self.assertTrue(git_source.descends_from(self.spec, self.first, self.second))

    def _probe(self, home):
        """`has_core_ssh_command` with a HOME of its own, recording the call `effects.run` receives."""
        calls = []
        real_run = git_source.effects.run

        def spy(command, **kwargs):
            calls.append(kwargs)
            return real_run(command, **kwargs)

        env = {**git_source.git_env(), "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
        with patch.object(git_source.effects, "run", side_effect=spy):
            found = git_source.has_core_ssh_command(env)
        return found, calls, env

    def test_the_ssh_command_probe_runs_outside_the_current_repo(self):
        home = self.work / "home"
        home.mkdir()
        found, calls, env = self._probe(home)
        self.assertFalse(found)
        self.assertEqual(1, len(calls))
        cwd = Path(calls[0]["cwd"])
        self.assertTrue(cwd.name.startswith("gb-git-"))
        self.assertNotIn(self.work, cwd.resolve().parents)
        self.assertEqual(str(cwd.parent), calls[0]["env"]["GIT_CEILING_DIRECTORIES"])
        self.assertNotIn("GIT_CEILING_DIRECTORIES", env, "the caller's env is not changed")
        self.assertFalse(cwd.exists(), "the neutral folder goes away after the probe")

    def test_the_ssh_command_probe_still_reads_the_global_config(self):
        home = self.work / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[core]\n\tsshCommand = ssh -i chave\n", encoding="utf-8")
        found, _calls, _env = self._probe(home)
        self.assertTrue(found)

    def test_git_env_for_ssh_does_not_carry_the_probe_ceiling(self):
        with patch.dict(os.environ, {"HOME": str(self.work)}, clear=False):
            env = git_source.git_env(ssh=True)
        self.assertNotIn("GIT_CEILING_DIRECTORIES", env)


if __name__ == "__main__":
    unittest.main()
