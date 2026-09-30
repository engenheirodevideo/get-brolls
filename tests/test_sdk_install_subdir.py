"""`--subdir`, recusa de ponteiro LFS e de nome fora de NFC, `content_digest` e `fetch_file`."""

import json
import os
import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from test_sdk_install import HAS_GIT, InstallTestCase, _git_test_env, git, head, write_plugin
from test_sdk_loader import MANIFEST

from getbrolls.sdk import git_source, loader
from getbrolls.sdk import install as install_mod
from getbrolls.sdk.files import LFS_POINTER_PREFIX, is_lfs_pointer
from getbrolls.sdk.git_source import GitSource
from getbrolls.sdk.manifest import current_platform

LFS_POINTER = LFS_POINTER_PREFIX + b"oid sha256:" + b"a" * 64 + b"\nsize 12345\n"


def add_blob(repo, path, content):
    """Commita `content` em `path` direto no índice (sem passar pelo disco)."""
    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repo,
            check=True,
            capture_output=True,
            input=content,
            env=_git_test_env(),
        )
        .stdout.decode("ascii")
        .strip()
    )
    subprocess.run(
        # Sem `core.precomposeunicode` (padrão do git no macOS), que trocaria um nome
        # NFD do argv pela forma NFC antes de gravar no índice.
        ["git", "-c", "core.precomposeunicode=false", "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
        cwd=repo,
        check=True,
        capture_output=True,
        env=_git_test_env(),
    )
    git(repo, "commit", "--quiet", "-m", f"add {path}")


class LfsPointerTests(unittest.TestCase):
    def test_only_a_small_blob_with_the_spec_line_is_a_pointer(self):
        self.assertTrue(is_lfs_pointer(LFS_POINTER, len(LFS_POINTER)))
        self.assertFalse(is_lfs_pointer(LFS_POINTER, 2048))
        self.assertFalse(is_lfs_pointer(b"version 1\n", 10))


class SubdirValidationTests(unittest.TestCase):
    def test_subdir_traversal_absolute_backslash_and_vcs_components_are_refused(self):
        for bad in (
            "../x",
            "/abs",
            "a\\b",
            "a/.git/b",
            ".GIT",
            "sub/.Hg",
            "x/.svn.",
            "a/./b",
            "a//b",
            "a/",
            "C:/x",
            "a b",
            "ação",
            "x" * 256,
            "",
        ):
            with self.subTest(subdir=bad), self.assertRaises(ValueError):
                git_source.parse_source("https://example.com/demo.git", subdir=bad)
        self.assertEqual("plugins/demo_1", git_source.validate_repo_path("plugins/demo_1"))
        self.assertEqual(".github-x/y", git_source.validate_repo_path(".github-x/y"))


@unittest.skipUnless(HAS_GIT, "git required")
class SubdirInstallTests(InstallTestCase):
    def monorepo(self):
        """Repositório com o plugin em `plugins/demo` e outro arquivo fora dele."""
        root = self.work / "mono"
        write_plugin(root / "plugins" / "demo")
        (root / "README.md").write_text("raiz\n", encoding="utf-8")
        git(root, "init", "--quiet")
        git(root, "add", ".")
        git(root, "commit", "--quiet", "-m", "mono")
        return root

    def test_subdir_installs_only_that_tree_and_hash_equals_folder_digest_of_a_plain_copy(self):
        root = self.monorepo()
        preview = install_mod.install(str(root), confirm=False, subdir="plugins/demo")
        plain = self.work / "plain_copy"
        write_plugin(plain)
        self.assertEqual(loader.folder_digest(plain), preview["plugin"]["sha256"])
        self.assertEqual("plugins/demo", preview["plugin"]["subdir"])
        self.assertEqual(["getbrolls-plugin.json", "plugin.py"], preview["plugin"]["files"]["names"])
        install_mod.install(str(root), confirm=True, expect=preview["plugin"]["sha256"], subdir="plugins/demo")
        origin = self.state()["sources"]["demo"]
        self.assertEqual(
            {"source": str(root.resolve()), "commit": head(root), "ref": None, "subdir": "plugins/demo"}, origin
        )
        self.assertFalse((self.home / "plugins" / "demo" / "README.md").exists())

    def test_subdir_that_is_a_file_or_missing_is_refused(self):
        root = self.monorepo()
        for subdir, reason in (("README.md", "não é uma pasta"), ("plugins/nada", "não existe")):
            with self.subTest(subdir=subdir), self.assertRaises(ValueError) as ctx:
                install_mod.install(str(root), confirm=False, subdir=subdir)
            self.assertIn(reason, str(ctx.exception))
        self.assertEqual([], self.leftover_staging())

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_subdir_that_is_a_symlink_is_refused(self):
        root = self.monorepo()
        (root / "atalho").symlink_to(root / "plugins" / "demo")
        git(root, "add", "atalho")
        git(root, "commit", "--quiet", "-m", "link")
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(root), confirm=False, subdir="atalho")
        self.assertIn("não é uma pasta", str(ctx.exception))

    def test_subdir_with_plain_folder_is_refused(self):
        folder = write_plugin(self.work / "plain" / "demo")
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(folder.parent), confirm=False, subdir="demo")
        self.assertIn("--source <pasta>/<subpasta>", str(ctx.exception))

    def test_lfs_pointer_blob_is_refused_in_git_and_in_folder_copy(self):
        root = self.monorepo()
        add_blob(root, "plugins/demo/video.mp4", LFS_POINTER)
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(root), confirm=False, subdir="plugins/demo")
        self.assertIn("Git LFS", str(ctx.exception))
        folder = write_plugin(self.work / "plain_lfs")
        (folder / "video.mp4").write_bytes(LFS_POINTER)
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(folder), confirm=False)
        self.assertIn("Git LFS", str(ctx.exception))
        self.assertEqual([], self.leftover_staging())

    def test_non_nfc_path_is_refused(self):
        decomposed = unicodedata.normalize("NFD", "canção.txt")
        self.assertNotEqual("canção.txt", decomposed)
        root = self.monorepo()
        add_blob(root, f"plugins/demo/{decomposed}", b"x\n")
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(root), confirm=False, subdir="plugins/demo")
        self.assertIn("NFC", str(ctx.exception))
        folder = write_plugin(self.work / "plain_nfd")
        (folder / decomposed).write_text("x\n", encoding="utf-8")
        names = [p.name for p in folder.iterdir()]
        if decomposed not in names:
            self.skipTest("o sistema de arquivos normalizou o nome")
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(folder), confirm=False)
        self.assertIn("NFC", str(ctx.exception))

    def test_composed_accented_name_is_accepted(self):
        root = self.monorepo()
        add_blob(root, "plugins/demo/canção.txt", b"x\n")
        preview = install_mod.install(str(root), confirm=False, subdir="plugins/demo")
        self.assertIn("canção.txt", preview["plugin"]["files"]["names"])


@unittest.skipUnless(HAS_GIT, "git required")
class ContentDigestTests(InstallTestCase):
    def repo(self):
        folder = write_plugin(self.work / "demo_repo")
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        return folder

    def test_content_digest_is_deterministic_and_leaves_nothing_behind(self):
        repo = self.repo()
        spec = GitSource(str(repo.resolve()), head(repo))
        temp_root = Path(tempfile.gettempdir())
        before = {p.name for p in temp_root.iterdir() if p.name.startswith("gb-digest-")}
        first = install_mod.content_digest(spec)
        second = install_mod.content_digest(spec)
        self.assertEqual(first, second)
        self.assertEqual("demo", first[1]["id"])
        preview = install_mod.install(str(repo), confirm=False)
        self.assertEqual(preview["plugin"]["sha256"], first[0])
        after = {p.name for p in temp_root.iterdir() if p.name.startswith("gb-digest-")}
        self.assertEqual(before, after)
        plugins = self.home / "plugins"
        self.assertEqual([], [p.name for p in plugins.iterdir()] if plugins.exists() else [])

    def test_content_digest_does_not_require_this_platform(self):
        other = "windows" if current_platform() != "windows" else "linux"
        folder = write_plugin(self.work / "other_os", {**MANIFEST, "platforms": [other]})
        with self.assertRaises(ValueError):
            install_mod.install(str(folder), confirm=False)
        sha, manifest = install_mod.content_digest(str(folder))
        self.assertEqual([other], manifest["platforms"])
        self.assertEqual(loader.folder_digest(folder), sha)

    def test_content_digest_ignores_autocrlf_and_filters(self):
        repo = self.repo()
        git(repo, "config", "core.autocrlf", "true")
        (repo / ".gitattributes").write_text("* text eol=crlf\n", encoding="utf-8")
        (repo / "notas.txt").write_bytes(b"linha 1\nlinha 2\n")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "crlf")
        # Refaz o checkout: a árvore de trabalho passa a ter CRLF; o blob continua LF.
        (repo / "notas.txt").unlink()
        git(repo, "checkout", "--", "notas.txt")
        self.assertIn(b"\r\n", (repo / "notas.txt").read_bytes())

        raw = self.work / "raw_blobs"
        for name in ("getbrolls-plugin.json", "plugin.py", ".gitattributes", "notas.txt"):
            blob = subprocess.run(
                ["git", "cat-file", "blob", f"HEAD:{name}"],
                cwd=repo,
                check=True,
                capture_output=True,
                env=_git_test_env(),
            ).stdout
            (raw / name).parent.mkdir(parents=True, exist_ok=True)
            (raw / name).write_bytes(blob)
        worktree_copy = self.work / "worktree_copy"
        for name in ("getbrolls-plugin.json", "plugin.py", ".gitattributes", "notas.txt"):
            worktree_copy.mkdir(exist_ok=True)
            (worktree_copy / name).write_bytes((repo / name).read_bytes())

        sha, _manifest = install_mod.content_digest(GitSource(str(repo.resolve())))
        self.assertEqual(loader.folder_digest(raw), sha)
        self.assertNotEqual(loader.folder_digest(worktree_copy), sha)


@unittest.skipUnless(HAS_GIT, "git required")
class FetchFileTests(InstallTestCase):
    def test_fetch_file_returns_the_committed_bytes_within_the_cap(self):
        repo = write_plugin(self.work / "index_repo")
        (repo / "getbrolls-marketplace.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        git(repo, "init", "--quiet")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "index")
        spec = GitSource(str(repo.resolve()))
        commit, data = install_mod.fetch_file(spec, "getbrolls-marketplace.json", 1024)
        self.assertEqual(head(repo), commit)
        self.assertEqual({"schema_version": 1}, json.loads(data))

        real_blob = install_mod._git_blob
        with (
            patch.object(install_mod, "_git_blob", side_effect=AssertionError("não devia ler")),
            self.assertRaises(ValueError) as ctx,
        ):
            install_mod.fetch_file(spec, "getbrolls-marketplace.json", 5)
        self.assertIn("passa de 5 bytes", str(ctx.exception))
        self.assertIs(real_blob, install_mod._git_blob)

        for bad in ("nada.json", "../x", ".git/config"):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                install_mod.fetch_file(spec, bad, 1024)
        leftovers = [p for p in Path(tempfile.gettempdir()).iterdir() if p.name.startswith("gb-fetch-")]
        self.assertEqual([], [p for p in leftovers if (p / "clone").exists()])

    def test_fetch_file_refuses_a_folder_source(self):
        with self.assertRaises(ValueError):
            install_mod.fetch_file(git_source.FolderSource(str(self.work)), "x.json", 10)


if __name__ == "__main__":
    unittest.main()
