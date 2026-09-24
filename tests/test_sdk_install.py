"""`plugins install/update`: pasta ou git, dois passos, origem e commit no plugins.json.

Cobre também a rodada de correções de segurança: nunca fazer `checkout` de git
(I1), link simbólico recusado no conteúdo materializado, não na origem (I2),
`--yes` exige `--expect <sha256>` batendo com a prévia (I3), manifesto validado
e árvore limitada antes de copiar/clonar tudo (I4), `plugins.json` corrompido
recusa antes de qualquer mutação (M1), troca de pasta do `update` desfaz se a
segunda metade falhar (M2), `update` não liga de volta um plugin desabilitado
(M3), variáveis de ambiente perigosas do git são removidas e SSH usa
`BatchMode` (M4), e URL git com query/fragmento é recusada (M5).
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase

from getbrolls.sdk import install as install_mod
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

    def leftover_staging(self):
        root = self.home / "plugins"
        if not root.exists():
            return []
        return [p.name for p in root.iterdir() if p.name.startswith((".install-", ".old-"))]


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
        self.assertIsInstance(preview["plugin"]["sha256"], str)
        self.assertFalse((self.home / "plugins" / "demo").exists())
        self.assertEqual([], [p.name for p in (self.home / "plugins").iterdir()])

        done = run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            source,
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        self.assertTrue(done["installed"])
        self.assertTrue((self.home / "plugins" / "demo" / "plugin.py").is_file())
        self.assertFalse((self.home / "plugins" / "demo" / "__pycache__").exists())
        state = self.state()
        self.assertEqual(done["plugin"]["sha256"], state["enabled"]["demo"]["sha256"])
        self.assertEqual({"source": str(source.resolve()), "commit": None}, state["sources"]["demo"])
        self.assertEqual("demo", run_cli("providers", env=self.env())["demo"]["plugin"])

    def test_already_installed_is_refused(self):
        source = write_plugin(self.work / "demo_src")
        preview = run_cli("plugins", "--action", "install", "--source", source, env=self.env())
        run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            source,
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        err = run_cli("plugins", "--action", "install", "--source", source, expect=2, env=self.env())
        self.assertIn("--action update", err["error"])

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_in_the_source_is_refused(self):
        source = write_plugin(self.work / "demo_src")
        (source / "atalho.txt").symlink_to(Path(tempfile.gettempdir()))
        err = run_cli("plugins", "--action", "install", "--source", source, expect=2, env=self.env())
        self.assertIn("link simbólico", err["error"])
        self.assertFalse((self.home / "plugins" / "demo").exists())

    def test_bad_sources_are_refused_without_echoing_credentials(self):
        for source in (
            "nao/existe",
            "https://usuario:segredo@example.com/plugin.git",
            "ftp://example.com/p",
            "https://example.com/plugin.git?x=1",
            "https://example.com/plugin.git#frag",
            "git@example.com:org/repo.git?x=1",
        ):
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

    # -- I3: --yes exige --expect batendo com o sha256 da prévia -----------------

    def test_yes_needs_a_matching_expect(self):
        source = write_plugin(self.work / "demo_src")
        preview = run_cli("plugins", "--action", "install", "--source", source, env=self.env())
        sha = preview["plugin"]["sha256"]

        missing = run_cli("plugins", "--action", "install", "--source", source, "--yes", expect=2, env=self.env())
        self.assertIn("--expect", missing["error"])

        wrong = run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            source,
            "--yes",
            "--expect",
            "0" * 64,
            expect=2,
            env=self.env(),
        )
        self.assertIn("sha256", wrong["error"])
        self.assertFalse((self.home / "plugins" / "demo").exists())

        done = run_cli("plugins", "--action", "install", "--source", source, "--yes", "--expect", sha, env=self.env())
        self.assertTrue(done["installed"])

    # -- I4: manifesto validado e árvore limitada antes de copiar tudo ------------

    def test_install_refuses_a_source_over_the_file_cap(self):
        source = write_plugin(self.work / "demo_src")
        (source / "extra.txt").write_text("mais um arquivo\n", encoding="utf-8")
        with patch.object(install_mod, "MAX_FILES", 2), self.assertRaises(ValueError) as ctx:
            install_mod.install(str(source), confirm=False)
        self.assertIn("arquivos", str(ctx.exception))
        self.assertFalse((self.home / "plugins" / "demo").exists())

    # -- M1: plugins.json corrompido recusa antes de qualquer mutação ------------

    def test_install_refuses_up_front_when_plugins_json_is_corrupt(self):
        source = write_plugin(self.work / "demo_src")
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "plugins.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            install_mod.install(str(source), confirm=True, expect="0" * 64)
        plugins_dir = self.home / "plugins"
        self.assertEqual([], [p.name for p in plugins_dir.iterdir()] if plugins_dir.exists() else [])

    # -- M2: troca do update desfaz se a segunda metade falhar --------------------

    def test_update_restores_the_retired_folder_if_the_swap_fails(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])

        write_plugin(source, {**MANIFEST, "version": "0.2.0"}, PLUGIN_CODE + "\n# v0.2\n")
        preview2 = install_mod.update("demo", confirm=False)
        before = (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8")

        real_replace = os.replace
        fail_on_call = 2
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == fail_on_call:
                raise OSError("falha simulada no replace")
            return real_replace(src, dst)

        with patch("getbrolls.sdk.install.os.replace", side_effect=flaky), self.assertRaises(OSError):
            install_mod.update("demo", confirm=True, expect=preview2["plugin"]["sha256"])

        self.assertEqual(before, (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8"))
        self.assertEqual([], self.leftover_staging())

    def test_stale_staging_dirs_are_swept_at_the_start(self):
        source = write_plugin(self.work / "demo_src")
        (self.home / "plugins").mkdir(parents=True, exist_ok=True)
        stale_install = self.home / "plugins" / ".install-lixo"
        stale_install.mkdir()
        (stale_install / "resto.txt").write_text("x", encoding="utf-8")
        stale_old = self.home / "plugins" / ".old-lixo"
        stale_old.mkdir()

        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])

        self.assertFalse(stale_install.exists())
        self.assertFalse(stale_old.exists())

    # -- M3: update não liga de volta um plugin desabilitado ----------------------

    def test_update_keeps_a_disabled_plugin_disabled(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        loader.disable("demo")
        self.assertEqual("disabled", loader.inventory()[0]["status"])

        write_plugin(source, {**MANIFEST, "version": "0.2.0"}, PLUGIN_CODE + "\n# v0.2\n")
        preview2 = install_mod.update("demo", confirm=False)
        done = install_mod.update("demo", confirm=True, expect=preview2["plugin"]["sha256"])

        self.assertTrue(done["updated"])
        self.assertFalse(done["enabled"])
        self.assertNotIn("demo", self.state().get("enabled", {}))
        self.assertEqual("disabled", loader.inventory()[0]["status"])
        self.assertEqual("0.2.0", loader.inventory()[0]["version"])

    # -- M4: variáveis perigosas removidas, SSH em BatchMode ----------------------

    def test_git_env_strips_dangerous_vars_and_sets_ssh_batch_mode(self):
        dangerous = {
            "GIT_DIR": "/tmp/x",
            "GIT_WORK_TREE": "/tmp/y",
            "GIT_INDEX_FILE": "/tmp/z",
            "GIT_OBJECT_DIRECTORY": "/tmp/o",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/tmp/a",
            "GIT_CONFIG_PARAMETERS": "x",
        }
        with patch.dict(os.environ, dangerous, clear=False):
            os.environ.pop("GIT_SSH_COMMAND", None)
            env = install_mod._git_env(ssh=True)
        for key in dangerous:
            self.assertNotIn(key, env)
        self.assertEqual("ssh -o BatchMode=yes", env["GIT_SSH_COMMAND"])
        self.assertEqual("1", env["GIT_LFS_SKIP_SMUDGE"])
        self.assertEqual("1", env["GIT_CONFIG_NOSYSTEM"])

        with patch.dict(os.environ, {"GIT_SSH_COMMAND": "custom-ssh"}):
            env2 = install_mod._git_env(ssh=True)
        self.assertEqual("custom-ssh", env2["GIT_SSH_COMMAND"])

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GIT_SSH_COMMAND", None)
            env3 = install_mod._git_env(ssh=False)
        self.assertNotIn("GIT_SSH_COMMAND", env3)


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
        preview = run_cli("plugins", "--action", "install", "--source", repo, env=self.env())
        done = run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            repo,
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        installed = self.home / "plugins" / "demo"
        self.assertEqual(head(repo), done["plugin"]["commit"])
        self.assertFalse((installed / ".git").exists())
        self.assertFalse((installed / "rascunho.py").exists())
        self.assertEqual({"source": str(repo.resolve()), "commit": head(repo)}, self.state()["sources"]["demo"])

    def test_update_shows_the_diff_first_then_replaces_and_repins(self):
        repo = self.repo()
        preview0 = run_cli("plugins", "--action", "install", "--source", repo, env=self.env())
        run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            repo,
            "--yes",
            "--expect",
            preview0["plugin"]["sha256"],
            env=self.env(),
        )
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
        self.assertIsInstance(preview["plugin"]["sha256"], str)
        self.assertEqual(first_pin, self.state()["enabled"]["demo"]["sha256"])
        self.assertEqual("0.1.0", loader.inventory()[0]["version"])

        done = run_cli(
            "plugins",
            "--action",
            "update",
            "--id",
            "demo",
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=self.env(),
        )
        self.assertTrue(done["updated"])
        state = self.state()
        self.assertEqual("0.2.0", state["enabled"]["demo"]["version"])
        self.assertEqual(loader.folder_digest(self.home / "plugins" / "demo"), state["enabled"]["demo"]["sha256"])
        self.assertEqual(head(repo), state["sources"]["demo"]["commit"])
        self.assertEqual(["demo"], [p.name for p in (self.home / "plugins").iterdir()])
        self.assertEqual("enabled", loader.inventory()[0]["status"])

    # -- I1: filtro git (smudge) nunca roda, com ou sem --yes ---------------------

    def test_git_filter_smudge_never_runs(self):
        repo = self.repo()
        (repo / ".gitattributes").write_text("*.py filter=pwn\n", encoding="utf-8")
        git(repo, "add", ".gitattributes")
        git(repo, "commit", "--quiet", "-m", "gitattributes")

        marker = self.work / "pwned.marker"
        global_config = self.work / "malicious-gitconfig"
        global_config.write_text(
            f'[filter "pwn"]\n\tsmudge = touch "{marker}" && cat\n\trequired = true\n',
            encoding="utf-8",
        )
        env = {**self.env(), "GIT_CONFIG_GLOBAL": str(global_config)}

        preview = run_cli("plugins", "--action", "install", "--source", repo, env=env)
        self.assertFalse(marker.exists())

        done = run_cli(
            "plugins",
            "--action",
            "install",
            "--source",
            repo,
            "--yes",
            "--expect",
            preview["plugin"]["sha256"],
            env=env,
        )
        self.assertTrue(done["installed"])
        self.assertFalse(marker.exists())
        self.assertEqual(PLUGIN_CODE, (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8"))

    # -- I2: link simbólico commitado e depois apagado do worktree local ---------

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_committed_then_removed_from_worktree_is_still_refused(self):
        repo = self.repo()
        link = repo / "atalho.py"
        link.symlink_to(repo / "plugin.py")
        git(repo, "add", "atalho.py")
        git(repo, "commit", "--quiet", "-m", "symlink")
        link.unlink()  # some do disco local, mas continua no histórico (HEAD) do repo

        err = run_cli("plugins", "--action", "install", "--source", repo, expect=2, env=self.env())
        self.assertIn("link simbólico", err["error"])
        self.assertFalse((self.home / "plugins" / "demo").exists())

    # -- I4 (git): árvore limitada antes de materializar tudo ---------------------

    def test_install_from_git_refuses_over_the_file_cap(self):
        repo = self.repo()
        with patch.object(install_mod, "MAX_FILES", 1), self.assertRaises(ValueError) as ctx:
            install_mod.install(str(repo), confirm=False)
        self.assertIn("arquivos", str(ctx.exception))

    # -- M5: URL git com query/fragmento é recusada -------------------------------

    def test_git_url_with_query_or_fragment_is_refused(self):
        for source in ("https://example.com/demo.git?token=segredo", "git@example.com:demo.git#ref"):
            with self.subTest(source=source):
                err = run_cli("plugins", "--action", "install", "--source", source, expect=2, env=self.env())
                self.assertIn("query", err["error"])


if __name__ == "__main__":
    unittest.main()
