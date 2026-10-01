"""`GitSource`/`FolderSource`: forma de `--source`, `--commit` e `--ref`, sem rodar git."""

import os
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

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


if __name__ == "__main__":
    unittest.main()
