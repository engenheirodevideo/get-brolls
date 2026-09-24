"""`plugins install/update`: pasta ou git, dois passos, origem e commit no plugins.json."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase

from getbrolls.sdk import loader

HAS_GIT = shutil.which("git") is not None


def write_plugin(folder, manifest=MANIFEST, code=PLUGIN_CODE):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "getbrolls-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "plugin.py").write_text(code, encoding="utf-8")
    return folder


def git(folder, *args):
    subprocess.run(
        ["git", "-c", "user.name=Teste", "-c", "user.email=teste@example.invalid", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def head(folder):
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=folder, check=True, capture_output=True, text=True)
    return done.stdout.strip()


class InstallTestCase(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.work = Path(tempfile.mkdtemp(prefix="gb-src-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def env(self):
        return {"GB_HOME": str(self.home)}

    def state(self):
        return json.loads((self.home / "plugins.json").read_text(encoding="utf-8"))


class FolderInstallTests(InstallTestCase):
    def test_install_from_a_folder_is_two_steps(self):
        source = write_plugin(self.work / "demo_src")
        (source / "__pycache__").mkdir()
        (source / "__pycache__" / "plugin.cpython-311.pyc").write_bytes(b"bytecode velho")
        preview = run_cli("plugins", "--action", "install", "--source", source, env=self.env())
        self.assertFalse(preview["installed"])
        self.assertEqual("demo", preview["plugin"]["id"])
        self.assertEqual(["demo.example"], preview["plugin"]["permissions"]["network"])
        self.assertIsNone(preview["plugin"]["commit"])
        self.assertFalse((self.home / "plugins" / "demo").exists())
        self.assertEqual([], [p.name for p in (self.home / "plugins").iterdir()])

        done = run_cli("plugins", "--action", "install", "--source", source, "--yes", env=self.env())
        self.assertTrue(done["installed"])
        self.assertTrue((self.home / "plugins" / "demo" / "plugin.py").is_file())
        self.assertFalse((self.home / "plugins" / "demo" / "__pycache__").exists())
        state = self.state()
        self.assertEqual(done["plugin"]["sha256"], state["enabled"]["demo"]["sha256"])
        self.assertEqual({"source": str(source.resolve()), "commit": None}, state["sources"]["demo"])
        self.assertEqual("demo", run_cli("providers", env=self.env())["demo"]["plugin"])

    def test_already_installed_is_refused(self):
        source = write_plugin(self.work / "demo_src")
        run_cli("plugins", "--action", "install", "--source", source, "--yes", env=self.env())
        err = run_cli("plugins", "--action", "install", "--source", source, expect=2, env=self.env())
        self.assertIn("--action update", err["error"])

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_in_the_source_is_refused(self):
        source = write_plugin(self.work / "demo_src")
        (source / "atalho.txt").symlink_to(Path(tempfile.gettempdir()))
        err = run_cli("plugins", "--action", "install", "--source", source, expect=2, env=self.env())
        self.assertIn("link simbólico", err["error"])

    def test_bad_sources_are_refused_without_echoing_credentials(self):
        for source in ("nao/existe", "https://usuario:segredo@example.com/plugin.git", "ftp://example.com/p"):
            with self.subTest(source=source):
                err = run_cli("plugins", "--action", "install", "--source", source, expect=2, env=self.env())
                self.assertNotIn("segredo", json.dumps(err, ensure_ascii=False))
        run_cli("plugins", "--action", "install", expect=2, env=self.env())

    def test_update_needs_a_recorded_origin(self):
        self.install()
        err = run_cli("plugins", "--action", "update", "--id", "demo", expect=2, env=self.env())
        self.assertIn("plugins --action install", err["error"])

    def test_old_plugins_json_without_sources_is_still_valid(self):
        self.install()
        loader.enable("demo", confirm=True)
        self.assertNotIn("sources", self.state())
        self.assertEqual("enabled", loader.inventory()[0]["status"])
        (self.home / "plugins.json").write_text(
            json.dumps({"enabled": {}, "sources": {"demo": {"source": 1}}}), encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            loader.read_state()


@unittest.skipUnless(HAS_GIT, "git required")
class GitInstallTests(InstallTestCase):
    def repo(self):
        folder = write_plugin(self.work / "demo_repo")
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        return folder

    def test_install_from_a_local_git_repo_records_the_commit(self):
        repo = self.repo()
        (repo / "rascunho.py").write_text("não commitado\n", encoding="utf-8")
        done = run_cli("plugins", "--action", "install", "--source", repo, "--yes", env=self.env())
        installed = self.home / "plugins" / "demo"
        self.assertEqual(head(repo), done["plugin"]["commit"])
        self.assertFalse((installed / ".git").exists())
        self.assertFalse((installed / "rascunho.py").exists())
        self.assertEqual({"source": str(repo.resolve()), "commit": head(repo)}, self.state()["sources"]["demo"])

    def test_update_shows_the_diff_first_then_replaces_and_repins(self):
        repo = self.repo()
        run_cli("plugins", "--action", "install", "--source", repo, "--yes", env=self.env())
        first_pin = self.state()["enabled"]["demo"]["sha256"]
        manifest = {**MANIFEST, "version": "0.2.0", "permissions": {"network": ["demo.example"], "env": []}}
        write_plugin(repo, manifest, PLUGIN_CODE + "\n# v0.2.0\n")
        (repo / "LEIAME.md").write_text("novo\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "v0.2.0")

        preview = run_cli("plugins", "--action", "update", "--id", "demo", env=self.env())
        self.assertFalse(preview["updated"])
        self.assertEqual({"from": "0.1.0", "to": "0.2.0"}, preview["diff"]["version"])
        self.assertEqual(["DEMO_TOKEN"], preview["diff"]["permissions"]["from"]["env"])
        self.assertEqual([], preview["diff"]["permissions"]["to"]["env"])
        self.assertEqual(["LEIAME.md"], preview["diff"]["files"]["added"])
        self.assertEqual(["getbrolls-plugin.json", "plugin.py"], preview["diff"]["files"]["changed"])
        self.assertEqual(first_pin, self.state()["enabled"]["demo"]["sha256"])
        self.assertEqual("0.1.0", loader.inventory()[0]["version"])

        done = run_cli("plugins", "--action", "update", "--id", "demo", "--yes", env=self.env())
        self.assertTrue(done["updated"])
        state = self.state()
        self.assertEqual("0.2.0", state["enabled"]["demo"]["version"])
        self.assertEqual(loader.folder_digest(self.home / "plugins" / "demo"), state["enabled"]["demo"]["sha256"])
        self.assertEqual(head(repo), state["sources"]["demo"]["commit"])
        self.assertEqual(["demo"], [p.name for p in (self.home / "plugins").iterdir()])
        self.assertEqual("enabled", loader.inventory()[0]["status"])


if __name__ == "__main__":
    unittest.main()
