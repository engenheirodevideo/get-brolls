"""Install/pin endurecidos: VCS aninhado, ":"/"\\\\" e clone separado, varredura com
conferência, README do scaffold, `.git` que não é pasta, lista de arquivos na prévia e
bytecode de plugin."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from test_repository import _logical_units
from test_sdk_install import HAS_GIT, InstallTestCase, git, head, write_plugin
from test_sdk_loader import MANIFEST, PLUGIN_CODE

from getbrolls.sdk import git_source, loader, scaffold
from getbrolls.sdk import install as install_mod
from getbrolls.sdk.files import counted_files
from getbrolls.sdk.registry import get_registry, reset_registry


class VcsComponentTests(unittest.TestCase):
    def test_every_vcs_component_and_windows_alias_is_refused(self):
        for bad in (
            "x/.hg/store",
            ".svn/entries",
            "a/.HG./x",
            "a/.svn /x",
            "a/HG~1/x",
            "a/SVN~1/x",
            ".git/config",
            "a/b:c/d.py",
            "a/.git::$INDEX_ALLOCATION/config",
            "a\\b.py",
        ):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                install_mod._refuse_git_path_component(bad)
        for good in ("plugin.py", "hgrc.txt", "svnkit/a.py", ".gitattributes", "docs/.hgignore"):
            install_mod._refuse_git_path_component(good)


class FolderInstallHardeningTests(InstallTestCase):
    def test_nested_vcs_folder_is_refused_and_junk_is_not_copied(self):
        source = write_plugin(self.work / "demo_src")
        (source / "desktop.ini").write_text("[.ShellClassInfo]\n", encoding="utf-8")
        (source / "Thumbs.db").write_bytes(b"x")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        installed = self.home / "plugins" / "demo"
        self.assertFalse((installed / "desktop.ini").exists())
        self.assertFalse((installed / "Thumbs.db").exists())

        other = write_plugin(self.work / "outro_src", {**MANIFEST, "id": "outro"})
        (other / "vendor" / ".hg").mkdir(parents=True)
        (other / "vendor" / ".hg" / "hgrc").write_text("[hooks]\n", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            install_mod.install(str(other), confirm=False)
        self.assertIn(".hg", str(caught.exception))

    def test_preview_lists_the_files(self):
        source = write_plugin(self.work / "demo_src")
        (source / "LEIAME.md").write_text("oi\n", encoding="utf-8")
        preview = install_mod.install(str(source), confirm=False)
        files = preview["plugin"]["files"]
        self.assertEqual(3, files["count"])
        self.assertEqual(["LEIAME.md", "getbrolls-plugin.json", "plugin.py"], files["names"])
        self.assertFalse(files["truncated"])

    def test_unconfirmed_gitfile_is_refused(self):
        source = write_plugin(self.work / "demo_src")
        (source / ".git").write_text("gitdir: /em/outro/lugar\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(source), confirm=False)
        self.assertIn(".git", str(ctx.exception))
        self.assertEqual([], self.leftover_staging())


class CountedFilesOrderTests(InstallTestCase):
    """A ordem de `files.counted_files` (e por tabela `folder_digest`/prévia de
    arquivos) não pode depender de `Path.__lt__` — no `WindowsPath` real essa
    comparação é insensível a maiúsculas, então `getbrolls-plugin.json` viria antes
    de `LEIAME.md`. Simulamos essa comparação insensível via patch para provar que
    a ordem do código continua por ponto de código mesmo assim."""

    @staticmethod
    def _casefold_lt(path_obj, other):
        return str(path_obj).casefold() < str(other).casefold()

    def test_order_is_code_point_stable_even_if_path_comparison_is_case_insensitive(self):
        folder = self.work / "mixed_case"
        folder.mkdir()
        for name in ("getbrolls-plugin.json", "LEIAME.md", "plugin.py"):
            (folder / name).write_text("x", encoding="utf-8")

        with patch.object(Path, "__lt__", self._casefold_lt):
            names = [rel.as_posix() for rel, _path in counted_files(folder)]
            digest_under_case_insensitive_cmp = loader.folder_digest(folder)

        self.assertEqual(["LEIAME.md", "getbrolls-plugin.json", "plugin.py"], names)
        self.assertEqual(digest_under_case_insensitive_cmp, loader.folder_digest(folder))

    def test_sub_directory_sorts_before_sibling_file_matching_the_previous_posix_order(self):
        """Ordenar pela string POSIX inteira (`rel.as_posix()`)
        inverteria esse par — `.` vem antes de `/` na comparação de string —, o
        que mudaria o pin no POSIX também, não só no Windows. `rel.parts` (tupla
        por componente) reproduz a MESMA ordem que `sorted(Path...)` sempre
        deu: `sub/x.py` antes de `sub.py`, porque a tupla compara
        `"sub"` com `"sub.py"` primeiro (e `"sub"` é prefixo, logo "menor")."""
        folder = self.work / "sub_vs_file"
        folder.mkdir()
        (folder / "sub").mkdir()
        (folder / "sub" / "x.py").write_text("x", encoding="utf-8")
        (folder / "sub.py").write_text("x", encoding="utf-8")

        names = [rel.as_posix() for rel, _path in counted_files(folder)]
        self.assertEqual(["sub/x.py", "sub.py"], names)


class LoaderVcsTests(InstallTestCase):
    def test_nested_vcs_in_an_installed_plugin_makes_it_invalid(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "vendor" / ".svn").mkdir(parents=True)
        (folder / "vendor" / ".svn" / "x.py").write_text("print('fora do hash')\n", encoding="utf-8")
        row = loader.inventory()[0]
        self.assertEqual("invalid", row["status"])
        self.assertIn(".svn", row["reason"])

    def test_only_the_top_level_git_is_left_out_of_the_digest(self):
        folder = self.install()
        before = loader.folder_digest(folder)
        (folder / ".hg").mkdir()
        (folder / ".hg" / "hgrc").write_text("[x]\n", encoding="utf-8")
        self.assertNotEqual(before, loader.folder_digest(folder))


class SweepTests(InstallTestCase):
    """`.old-*` só volta ao lugar se o manifesto dele lê e o id bate."""

    def stale_old(self, plugin_id):
        (self.home / "plugins").mkdir(parents=True, exist_ok=True)
        epoch = int(time.time()) - (install_mod.STALE_STAGING_MAX_AGE_S + 60)
        return self.home / "plugins" / f".old-{epoch}-{plugin_id}-{uuid.uuid4().hex}"

    def test_empty_or_foreign_old_folder_is_deleted_not_restored(self):
        empty = self.stale_old("demo")
        empty.mkdir()
        foreign = self.stale_old("alheio")
        write_plugin(foreign, {**MANIFEST, "id": "outro"})
        install_mod._sweep_stale_staging()
        self.assertFalse(empty.exists())
        self.assertFalse(foreign.exists())
        self.assertFalse((self.home / "plugins" / "demo").exists())
        self.assertFalse((self.home / "plugins" / "alheio").exists())

    def test_matching_old_folder_is_restored(self):
        retired = self.stale_old("demo")
        write_plugin(retired)
        install_mod._sweep_stale_staging()
        self.assertTrue((self.home / "plugins" / "demo" / "plugin.py").is_file())

    def test_double_failure_rollback_names_both_folders_and_keeps_the_old_content(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        write_plugin(source, {**MANIFEST, "version": "0.2.0"}, PLUGIN_CODE + "\n# v0.2\n")
        preview2 = install_mod.update("demo", confirm=False)

        real_replace = os.replace
        calls = {"n": 0}
        first_failing_call = 2  # a 1ª (retirar a pasta atual) passa; a troca nova e o rollback falham

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] >= first_failing_call:
                raise OSError("falha simulada")
            return real_replace(src, dst)

        with patch("getbrolls.sdk.install.os.replace", side_effect=flaky), self.assertRaises(ValueError) as caught:
            install_mod.update("demo", confirm=True, expect=preview2["plugin"]["sha256"])
        self.assertIn("desfazê-la também falhou", str(caught.exception))
        retired = [p for p in (self.home / "plugins").iterdir() if p.name.startswith(".old-")]
        self.assertEqual(1, len(retired))
        self.assertEqual(PLUGIN_CODE, (retired[0] / "plugin.py").read_text(encoding="utf-8"))


class ScaffoldReadmeTests(unittest.TestCase):
    """O README gerado pelo scaffold nunca manda `--yes` sem `--expect`."""

    def test_generated_readme_pairs_yes_with_expect(self):
        action_re = __import__("re").compile(r"--action\s+(install|update)\b")
        for kind in scaffold.KINDS:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                out = scaffold.new("meu_plugin", kind, tmp)
                text = (Path(out["created"]) / "README.md").read_text(encoding="utf-8")
                for unit in _logical_units(text):
                    if action_re.search(unit) and "--yes" in unit:
                        self.assertIn("--expect", unit, unit)


class BytecodeTests(InstallTestCase):
    """Plugin com módulo irmão não pode suspender a si mesmo com `__pycache__`."""

    def test_two_module_plugin_stays_enabled_after_use(self):
        code = (
            "import sys\nfrom pathlib import Path\n\n"
            "sys.path.insert(0, str(Path(__file__).parent))\n"
            "import ajudante_demo_bytecode\n\n" + PLUGIN_CODE
        )
        folder = self.install(code=code)
        (folder / "ajudante_demo_bytecode.py").write_text("VALOR = 1\n", encoding="utf-8")
        loader.enable("demo", confirm=True)
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = False
        self.addCleanup(setattr, sys, "dont_write_bytecode", previous)
        self.addCleanup(sys.modules.pop, "ajudante_demo_bytecode", None)
        self.addCleanup(lambda: sys.path.remove(str(folder)) if str(folder) in sys.path else None)
        self.assertIn("demo", get_registry().provider_names())
        self.assertFalse((folder / "__pycache__").exists())
        reset_registry()
        self.assertEqual("enabled", loader.inventory()[0]["status"])


class BytecodeOnlyWhenPluginLoadsTests(InstallTestCase):
    """`disable_bytecode()` só mexe em `sys.dont_write_bytecode` quando existe uma
    linha `enabled` para carregar — antes rodava no topo de `load_enabled` mesmo
    sem plugin nenhum, desligando o cache de `.pyc` para os módulos do próprio
    core que `providers`/`search`/`doctor` importam de leve (lazy) depois, numa
    instalação sem plugin."""

    def test_no_enabled_plugin_leaves_the_flag_untouched(self):
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = False
        self.addCleanup(setattr, sys, "dont_write_bytecode", previous)
        get_registry()
        self.assertFalse(sys.dont_write_bytecode)

    def test_an_enabled_plugin_still_turns_it_on(self):
        self.install(code=PLUGIN_CODE)
        loader.enable("demo", confirm=True)
        reset_registry()
        previous = sys.dont_write_bytecode
        sys.dont_write_bytecode = False
        self.addCleanup(setattr, sys, "dont_write_bytecode", previous)
        get_registry()
        self.assertTrue(sys.dont_write_bytecode)


@unittest.skipUnless(HAS_GIT, "git required")
class GitTestHelperIsolationTests(InstallTestCase):
    """O helper `git()` dos testes ignora gpgsign e hooks do config de quem roda."""

    def test_helper_commit_survives_a_hostile_global_config(self):
        hooks = self.work / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        config = self.work / "hostil.gitconfig"
        config.write_text(f"[commit]\n\tgpgsign = true\n[core]\n\thooksPath = {hooks.as_posix()}\n", encoding="utf-8")
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            folder = write_plugin(self.work / "repo_hostil")
            git(folder, "init", "--quiet")
            git(folder, "add", ".")
            git(folder, "commit", "--quiet", "-m", "ok")
        self.assertEqual(40, len(head(folder)))


@unittest.skipUnless(HAS_GIT, "git required")
class GitHardeningTests(InstallTestCase):
    def repo(self, name="demo_repo"):
        folder = write_plugin(self.work / name)
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        return folder

    def add_blob(self, repo, path, text):
        blob = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=repo, check=True, capture_output=True, text=True, input=text
        ).stdout.strip()
        subprocess.run(
            ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        git(repo, "commit", "--quiet", "-m", f"add {path}")

    def test_hg_and_svn_from_git_history_are_refused(self):
        for index, path in enumerate(("vendor/.hg/hgrc", ".svn/entries")):
            with self.subTest(path=path):
                # Uma pasta por subteste: um repositório que sobrou do anterior (o Git
                # para Windows deixa objetos somente-leitura) nunca contamina o próximo.
                repo = self.repo(f"demo_repo_{index}")
                self.add_blob(repo, path, "x\n")
                with self.assertRaises(ValueError):
                    install_mod.install(str(repo), confirm=False)
                self.assertEqual([], self.leftover_staging())

    def test_materialized_tree_never_shares_the_clone_folder(self):
        repo = self.repo()
        seen = {}
        real_write = install_mod._write_tree_entry

        def spy(dest, root, path, mode, content):
            seen.setdefault("git_in_dest", (Path(dest) / ".git").exists())
            return real_write(dest, root, path, mode, content)

        with patch.object(install_mod, "_write_tree_entry", side_effect=spy):
            preview = install_mod.install(str(repo), confirm=False)
        self.assertIs(False, seen["git_in_dest"])
        self.assertIsInstance(preview["plugin"]["commit"], str)
        self.assertEqual([], self.leftover_staging())


@unittest.skipUnless(HAS_GIT, "git required")
class WorktreeSourceTests(InstallTestCase):
    """Uma worktree do git (`.git` é um arquivo `gitdir: …`) é repositório, conferida pelo
    próprio git; link no lugar do `.git` e gitfile quebrado continuam fora."""

    def worktree(self):
        repo = write_plugin(self.work / "principal")
        git(repo, "init", "--quiet")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "plugin")
        tree = self.work / "ramo"
        git(repo, "worktree", "add", "--quiet", "-b", "ramo", str(tree))
        write_plugin(tree, {**MANIFEST, "version": "0.2.0"})
        git(tree, "commit", "--quiet", "-am", "ramo")
        return repo, tree

    def test_worktree_folder_installs_by_commit(self):
        repo, tree = self.worktree()
        self.assertTrue((tree / ".git").is_file())
        self.assertIsInstance(git_source.parse_source(str(tree)), git_source.GitSource)
        preview = install_mod.install(str(tree), confirm=False)
        self.assertEqual(head(tree), preview["plugin"]["commit"])
        self.assertNotEqual(head(repo), preview["plugin"]["commit"])
        self.assertEqual("0.2.0", preview["plugin"]["version"])
        self.assertEqual(str(tree.resolve()), preview["plugin"]["source"])
        done = install_mod.install(str(tree), confirm=True, expect=preview["plugin"]["sha256"])
        self.assertTrue(done["installed"])
        self.assertFalse((loader.plugins_root() / "demo" / ".git").exists())

    def test_subfolder_of_a_worktree_with_a_copied_gitfile_is_refused(self):
        _repo, tree = self.worktree()
        inner = write_plugin(tree / "dentro")
        (inner / ".git").write_text((tree / ".git").read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(ValueError):
            git_source.parse_source(str(inner))

    def test_symlinked_git_file_is_refused(self):
        _repo, tree = self.worktree()
        linked = write_plugin(self.work / "ligado")
        try:
            (linked / ".git").symlink_to(tree / ".git")
        except (OSError, NotImplementedError):
            self.skipTest("sem link simbólico neste sistema")
        with self.assertRaises(ValueError):
            git_source.parse_source(str(linked))

    def test_worktree_with_a_relative_back_link_is_a_repository(self):
        _repo, tree = self.worktree()
        git_dir = Path((tree / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip())
        back = git_dir / "gitdir"
        relative = os.path.relpath(tree.resolve() / ".git", git_dir.resolve())
        self.assertFalse(Path(relative).is_absolute())
        back.write_text(relative + "\n", encoding="utf-8")
        self.assertIsInstance(git_source.parse_source(str(tree)), git_source.GitSource)

    def test_gitfile_without_back_link_counts_only_for_a_submodule(self):
        _repo, tree = self.worktree()
        git_dir = Path((tree / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip())
        (git_dir / "gitdir").unlink()
        with self.assertRaises(ValueError):
            git_source.parse_source(str(tree))

    def test_submodule_folder_is_a_repository(self):
        sub = write_plugin(self.work / "sub_origem")
        git(sub, "init", "--quiet")
        git(sub, "add", ".")
        git(sub, "commit", "--quiet", "-m", "sub")
        sup = self.work / "super"
        sup.mkdir()
        git(sup, "init", "--quiet")
        git(sup, "-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(sub), "plug")
        git(sup, "commit", "--quiet", "-m", "sub")
        folder = sup / "plug"
        self.assertTrue((folder / ".git").is_file())
        self.assertIsInstance(git_source.parse_source(str(folder)), git_source.GitSource)


if __name__ == "__main__":
    unittest.main()
