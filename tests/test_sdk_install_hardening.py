"""Install/pin endurecidos: VCS aninhado (Minor 3 + RT-12), ":"/"\\\\" e clone separado
(Minor 4), varredura com conferência (Minor 5), README do scaffold (Minor 6), `.git`
que não é pasta, lista de arquivos na prévia e bytecode de plugin (notas do red-team)."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from test_repository import _logical_units
from test_sdk_install import HAS_GIT, InstallTestCase, git, head, write_plugin
from test_sdk_loader import MANIFEST, PLUGIN_CODE

from getbrolls.sdk import install as install_mod
from getbrolls.sdk import loader, scaffold
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

    def test_gitfile_is_not_treated_as_a_repository(self):
        source = write_plugin(self.work / "demo_src")
        (source / ".git").write_text("gitdir: /em/outro/lugar\n", encoding="utf-8")
        preview = install_mod.install(str(source), confirm=False)
        self.assertIsNone(preview["plugin"]["commit"])
        self.assertEqual(str(source.resolve()), preview["plugin"]["source"])


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
    """Minor 5: `.old-*` só volta ao lugar se o manifesto dele lê e o id bate."""

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
    """Minor 6: o README gerado pelo scaffold nunca manda `--yes` sem `--expect`."""

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
    """Nota do red-team: plugin com módulo irmão não pode suspender a si mesmo com `__pycache__`."""

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


@unittest.skipUnless(HAS_GIT, "git required")
class GitTestHelperIsolationTests(InstallTestCase):
    """Minor 13: o helper `git()` dos testes ignora gpgsign e hooks do config de quem roda."""

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
    def repo(self):
        folder = write_plugin(self.work / "demo_repo")
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
        for path in ("vendor/.hg/hgrc", ".svn/entries"):
            with self.subTest(path=path):
                repo = self.repo()
                self.add_blob(repo, path, "x\n")
                with self.assertRaises(ValueError):
                    install_mod.install(str(repo), confirm=False)
                self.assertEqual([], self.leftover_staging())
                shutil.rmtree(repo)

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


if __name__ == "__main__":
    unittest.main()
