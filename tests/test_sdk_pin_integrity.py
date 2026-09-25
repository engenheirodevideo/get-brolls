"""B-01/B-02/B-03: o que roda é o que o pin cobre — sem lixo de SO materializado,
sem link simbólico e sem bytecode ao lado da fonte revisada."""

import os
import shutil
import unittest

from test_sdk_install import HAS_GIT, InstallTestCase, git, write_plugin
from test_sdk_loader import LoaderTestCase

from getbrolls.sdk import install as install_mod
from getbrolls.sdk import loader
from getbrolls.sdk.registry import get_registry, reset_registry

JUNK_EXEC_CODE = """
from pathlib import Path

_ns = {}
exec(compile((Path(__file__).parent / "sub" / ".DS_Store").read_text(), "ds", "exec"), _ns)


def register(api):
    pass
"""


@unittest.skipUnless(HAS_GIT, "git required")
class GitMaterializationTests(InstallTestCase):
    def repo(self, name="demo_repo"):
        return write_plugin(self.work / name)

    def commit(self, folder):
        git(folder, "init", "--quiet")
        git(folder, "add", "-f", ".")
        git(folder, "commit", "--quiet", "-m", "c")

    def test_os_junk_names_are_never_written(self):
        repo = self.repo()
        (repo / "sub").mkdir()
        (repo / "sub" / ".DS_Store").write_text('V = "reviewed"\n', encoding="utf-8")
        (repo / "Thumbs.db").write_bytes(b"x")
        (repo / "desktop.ini").write_text("[x]\n", encoding="utf-8")
        self.commit(repo)
        preview = install_mod.install(str(repo), confirm=False)
        self.assertNotIn("sub/.DS_Store", preview["plugin"]["files"]["names"])
        install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"])
        installed = self.home / "plugins" / "demo"
        for junk in ("sub/.DS_Store", "Thumbs.db", "desktop.ini"):
            with self.subTest(junk=junk):
                self.assertFalse((installed / junk).exists())
        self.assertEqual("enabled", loader.inventory()[0]["status"])

    def test_junk_swapped_after_preview_never_runs(self):
        """Repro do B-01: o `.DS_Store` que o plugin executa não chega a `plugins/`."""
        repo = write_plugin(self.work / "junkexec", code=JUNK_EXEC_CODE)
        (repo / "sub").mkdir()
        (repo / "sub" / ".DS_Store").write_text('V = "reviewed"\n', encoding="utf-8")
        self.commit(repo)
        preview = install_mod.install(str(repo), confirm=False)
        (repo / "sub" / ".DS_Store").write_text('V = "swapped-after-preview"\n', encoding="utf-8")
        git(repo, "add", "-f", ".")
        git(repo, "commit", "--quiet", "-m", "swap")
        install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"])
        self.assertFalse((self.home / "plugins" / "demo" / "sub" / ".DS_Store").exists())

    def test_bytecode_in_git_is_refused(self):
        for rel in ("__pycache__/helper.cpython-314.pyc", "helper.pyc", "lib/velho.pyo"):
            with self.subTest(rel=rel):
                repo = self.repo("repo_" + rel.replace("/", "_").replace(".", "_"))
                target = repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"not reviewed")
                self.commit(repo)
                with self.assertRaises(ValueError) as caught:
                    install_mod.install(str(repo), confirm=False)
                self.assertIn("bytecode", str(caught.exception))
                self.assertFalse((self.home / "plugins" / "demo").exists())


class FolderInstallBytecodeTests(InstallTestCase):
    def test_bytecode_in_a_folder_source_is_refused(self):
        for rel in ("__pycache__/plugin.cpython-311.pyc", "velho.pyc"):
            with self.subTest(rel=rel):
                source = write_plugin(self.work / ("src_" + rel.replace("/", "_").replace(".", "_")))
                (source / rel).parent.mkdir(parents=True, exist_ok=True)
                (source / rel).write_bytes(b"bytecode velho")
                with self.assertRaises(ValueError) as caught:
                    install_mod.install(str(source), confirm=False)
                self.assertIn("bytecode", str(caught.exception))
                self.assertEqual([], [p.name for p in (self.home / "plugins").iterdir()])


class LoadTimeContentTests(LoaderTestCase):
    def enabled(self):
        folder = self.install()
        self.assertTrue(loader.enable("demo", confirm=True)["enabled"])
        reset_registry()
        return folder

    def assert_invalid(self, word):
        reset_registry()
        row = loader.inventory()[0]
        self.assertEqual("invalid", row["status"])
        self.assertIn(word, row["reason"])
        self.assertNotIn("demo", get_registry().provider_names())
        self.assertIn("youtube", get_registry().provider_names())
        with self.assertRaises(ValueError):
            loader.enable("demo", confirm=True)

    def test_planted_bytecode_makes_the_plugin_invalid(self):
        for rel in ("__pycache__/plugin.cpython-311.pyc", "extra.pyc", "sub/velho.pyo"):
            with self.subTest(rel=rel):
                folder = self.enabled()
                (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                (folder / rel).write_bytes(b"x")
                self.assert_invalid("bytecode")
                top = folder / rel.split("/")[0]
                if top.is_dir():
                    shutil.rmtree(top)
                else:
                    top.unlink()

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_makes_the_plugin_invalid(self):
        folder = self.enabled()
        outside = self.home / "ext"
        outside.mkdir()
        (outside / "helper.py").write_text("V = 1\n", encoding="utf-8")
        (folder / "lib").symlink_to(outside, target_is_directory=True)
        self.assert_invalid("link simbólico")

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlinked_file_makes_the_plugin_invalid(self):
        folder = self.enabled()
        (folder / "atalho.py").symlink_to(folder / "plugin.py")
        self.assert_invalid("link simbólico")

    def test_top_level_git_is_still_skipped(self):
        folder = self.enabled()
        (folder / ".git" / "objects").mkdir(parents=True)
        (folder / ".git" / "objects" / "x.pyc").write_bytes(b"x")
        reset_registry()
        self.assertEqual("enabled", loader.inventory()[0]["status"])


if __name__ == "__main__":
    unittest.main()


class InstallPinsWhatWasConfirmedTests(InstallTestCase):
    """B-11: o pin do install é o sha256 confirmado no staging, não um novo hash depois da troca."""

    def test_content_swapped_after_the_move_is_suspended(self):
        from pathlib import Path
        from unittest.mock import patch

        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        original_replace = Path.replace

        def replace_then_tamper(self_path, target):
            moved = original_replace(self_path, target)
            if Path(target).name == "demo":
                with (Path(target) / "plugin.py").open("a", encoding="utf-8") as handle:
                    handle.write("\n# trocado entre a troca e o pin\n")
            return moved

        with patch.object(Path, "replace", replace_then_tamper):
            install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        self.assertEqual(preview["plugin"]["sha256"], self.state()["enabled"]["demo"]["sha256"])
        self.assertEqual("suspended", loader.inventory()[0]["status"])
