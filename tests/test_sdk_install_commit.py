"""`plugins install` de git fixado por commit: ref resolvida, fetch pelo sha, git endurecido."""

import json
import os
import subprocess
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from test_sdk_install import HAS_GIT, InstallTestCase, _git_test_env, git, head, write_plugin
from test_sdk_loader import MANIFEST, PLUGIN_CODE

from getbrolls.sdk import git_source, loader
from getbrolls.sdk import install as install_mod


def rev(folder, name):
    done = subprocess.run(
        ["git", "rev-parse", name], cwd=folder, check=True, capture_output=True, text=True, env=_git_test_env()
    )
    return done.stdout.strip()


@unittest.skipUnless(HAS_GIT, "git required")
class CommitPinTests(InstallTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.tip = ""

    def repo(self, name="demo_repo"):
        """Repositório com dois commits: 0.1.0 (primeiro) e 0.2.0 (ponta)."""
        folder = write_plugin(self.work / name)
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        self.first = head(folder)
        write_plugin(folder, {**MANIFEST, "version": "0.2.0"}, PLUGIN_CODE + "\n# v0.2.0\n")
        git(folder, "commit", "--quiet", "-am", "v0.2.0")
        self.tip = head(folder)
        return folder

    def test_install_pins_an_older_commit_not_the_tip(self):
        repo = self.repo()
        preview = install_mod.install(str(repo), confirm=False, commit=self.first)
        self.assertEqual("0.1.0", preview["plugin"]["version"])
        self.assertEqual(self.first, preview["plugin"]["commit"])
        done = install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"], commit=self.first)
        self.assertTrue(done["installed"])
        self.assertEqual(self.first, self.state()["sources"]["demo"]["commit"])
        self.assertEqual([], self.leftover_staging())

    def test_ref_resolves_branch_and_tag_and_records_ref(self):
        repo = self.repo()
        git(repo, "branch", "estavel", self.first)
        git(repo, "tag", "-a", "-m", "anotada", "v0.1.0", self.first)
        git(repo, "tag", "leve", self.tip)
        for ref, commit in (("estavel", self.first), ("v0.1.0", self.first), ("leve", self.tip)):
            with self.subTest(ref=ref):
                preview = install_mod.install(str(repo), confirm=False, ref=ref)
                self.assertEqual(commit, preview["plugin"]["commit"])
                self.assertEqual(ref, preview["plugin"]["ref"])
        preview = install_mod.install(str(repo), confirm=False, ref="refs/tags/v0.1.0")
        install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"], ref="refs/tags/v0.1.0")
        origin = self.state()["sources"]["demo"]
        self.assertEqual((self.first, "refs/tags/v0.1.0"), (origin["commit"], origin["ref"]))

    def test_ambiguous_ref_is_refused(self):
        repo = self.repo()
        git(repo, "branch", "dupla", self.first)
        git(repo, "tag", "dupla", self.tip)
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(repo), confirm=False, ref="dupla")
        self.assertIn("ambígua", str(ctx.exception))
        self.assertIn("refs/heads/dupla", str(ctx.exception))
        preview = install_mod.install(str(repo), confirm=False, ref="refs/heads/dupla")
        self.assertEqual(self.first, preview["plugin"]["commit"])

    def test_missing_ref_is_refused(self):
        repo = self.repo()
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(repo), confirm=False, ref="nao-existe")
        self.assertIn("não existe", str(ctx.exception))
        self.assertEqual([], self.leftover_staging())

    def test_abbreviated_or_uppercase_commit_is_refused(self):
        repo = self.repo()
        for bad in (self.first[:12], self.first.upper()):
            with self.subTest(commit=bad), self.assertRaises(ValueError) as ctx:
                install_mod.install(str(repo), confirm=False, commit=bad)
            self.assertIn("40", str(ctx.exception))
        self.assertEqual([], self.leftover_staging())

    def test_unknown_commit_is_refused_with_clear_message(self):
        repo = self.repo()
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(repo), confirm=False, commit="0" * 40)
        self.assertIn("não encontrado na origem", str(ctx.exception))
        self.assertFalse((self.home / "plugins" / "demo").exists())
        self.assertEqual([], self.leftover_staging())

    def test_ref_injection_is_refused_before_git_runs(self):
        repo = self.repo()
        marker = self.work / "x"
        for bad in (f"--upload-pack=touch {marker}", "-x", "a..b"):
            with (
                self.subTest(ref=bad),
                patch.object(git_source.subprocess, "run") as run,
                self.assertRaises(ValueError),
            ):
                install_mod.install(str(repo), confirm=False, ref=bad)
            run.assert_not_called()
        self.assertFalse(marker.exists())

    def test_local_git_folder_installs_committed_tree_and_warns(self):
        repo = self.repo()
        (repo / "plugin.py").write_text(PLUGIN_CODE + "\n# não commitado\n", encoding="utf-8")
        preview = install_mod.install(str(repo), confirm=False)
        self.assertEqual(self.tip, preview["plugin"]["commit"])
        warning = next(w for w in preview["plugin"]["warnings"] if "repositório git" in w)
        self.assertIn(self.tip[:12], warning)
        self.assertIn("não foi commitado", warning)
        install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"])
        installed = (self.home / "plugins" / "demo" / "plugin.py").read_text(encoding="utf-8")
        self.assertNotIn("não commitado", installed)

    def test_bare_repository_folder_is_a_git_origin(self):
        repo = self.repo()
        bare = self.work / "demo_bare.git"
        git(self.work, "clone", "--quiet", "--bare", str(repo), str(bare))
        preview = install_mod.install(str(bare), confirm=False, commit=self.first)
        self.assertEqual("0.1.0", preview["plugin"]["version"])
        self.assertEqual(str(bare.resolve()), preview["plugin"]["source"])

    def test_fetch_fallback_is_used_when_fetch_by_sha_fails(self):
        repo = self.repo()
        real = git_source.git_text
        calls = []

        def refuse_fetch_by_sha(args, **kwargs):
            if args[0] == "fetch":
                calls.append(args[-1])
                if args[-1] in (self.first, self.tip):
                    raise ValueError("git falhou (exit 128): Server does not allow request for unadvertised object")
            return real(args, **kwargs)

        with patch.object(git_source, "git_text", side_effect=refuse_fetch_by_sha):
            preview = install_mod.install(str(repo), confirm=False)
            self.assertEqual(self.tip, preview["plugin"]["commit"])
            self.assertEqual([self.tip, "HEAD"], calls)
            # O commit pedido não é a ponta da ref: sem fetch por sha, recusa claramente.
            with self.assertRaises(ValueError) as ctx:
                install_mod.install(str(repo), confirm=False, commit=self.first)
        self.assertIn("não encontrado na origem", str(ctx.exception))
        self.assertIn("ponta de HEAD", str(ctx.exception))
        self.assertEqual([], self.leftover_staging())

    def test_git_hardening_flags_are_on_every_git_call(self):
        repo = self.repo()
        seen = []
        real = subprocess.run

        def spy(args, **kwargs):
            seen.append(list(args))
            return real(args, **kwargs)  # pylint: disable=subprocess-run-check

        with patch.object(git_source.subprocess, "run", side_effect=spy):
            install_mod.install(str(repo), confirm=False)
        git_calls = [args for args in seen if "config" not in args]
        self.assertTrue(any("fetch" in args for args in git_calls))
        for args in git_calls:
            joined = " ".join(args)
            for flag in (
                "protocol.allow=never",
                "protocol.https.allow=always",
                "protocol.ssh.allow=always",
                "protocol.ext.allow=never",
                "transfer.fsckObjects=true",
                "init.templateDir=",
                f"core.hooksPath={os.devnull}",
            ):
                self.assertIn(flag, joined)
        # `file://` só é liberado para falar com a pasta local que a pessoa indicou.
        remote_calls = [args for args in git_calls if "fetch" in args or "ls-remote" in args]
        self.assertTrue(all("protocol.file.allow=always" in args for args in remote_calls))

    def test_remote_url_never_enables_the_file_transport(self):
        seen = []

        def fake(args, **_kwargs):
            seen.append(list(args))
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: sem rede no teste")

        with patch.object(git_source.subprocess, "run", side_effect=fake), self.assertRaises(ValueError):
            install_mod.install("https://example.invalid/demo.git", confirm=False)
        self.assertTrue(seen)
        for args in seen:
            self.assertNotIn("protocol.file.allow=always", args)
            self.assertIn("protocol.allow=never", args)

    def test_init_template_is_not_copied_into_the_clone(self):
        repo = self.repo()
        clones = []
        real = git_source.git_text

        def spy(args, **kwargs):
            out = real(args, **kwargs)
            if args[0] == "init":
                clones.append(sorted(p.name for p in (install_mod.Path(args[-1]) / ".git").iterdir()))
            return out

        with patch.object(git_source, "git_text", side_effect=spy):
            install_mod.install(str(repo), confirm=False)
        self.assertEqual(1, len(clones))
        self.assertNotIn("hooks", clones[0])


@unittest.skipUnless(HAS_GIT, "git required")
class UpdateFromOriginTests(InstallTestCase):
    def setUp(self):
        super().setUp()
        self.first = self.tip = ""

    def repo(self):
        folder = write_plugin(self.work / "demo_repo")
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        git(folder, "branch", "estavel")
        self.first = head(folder)
        return folder

    def install_at(self, repo, **pin):
        preview = install_mod.install(str(repo), confirm=False, **pin)
        return install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"], **pin)

    def test_update_reresolves_recorded_ref_and_shows_permissions_added(self):
        repo = self.repo()
        self.install_at(repo, ref="estavel")
        wider = {
            **MANIFEST,
            "version": "0.2.0",
            "permissions": {
                "network": ["demo.example", "novo.example"],
                "env": ["DEMO_TOKEN"],
                "paths": ["~/Midia"],
                "project_write": ["analysis"],
            },
        }
        write_plugin(repo, wider)
        git(repo, "commit", "--quiet", "-am", "v0.2.0")
        self.tip = head(repo)
        # A branch gravada ainda aponta para o primeiro commit: nada muda.
        same = install_mod.update("demo", confirm=False)
        self.assertEqual(self.first, same["plugin"]["commit"])
        self.assertFalse(same["diff"]["permissions_increased"])
        git(repo, "branch", "-f", "estavel", self.tip)
        preview = install_mod.update("demo", confirm=False)
        self.assertEqual((self.tip, "estavel"), (preview["plugin"]["commit"], preview["plugin"]["ref"]))
        self.assertEqual(
            {"network": ["novo.example"], "env": [], "paths": ["~/Midia"], "project_write": ["analysis"]},
            preview["diff"]["permissions_added"],
        )
        self.assertTrue(preview["diff"]["permissions_increased"])
        install_mod.update("demo", confirm=True, expect=preview["plugin"]["sha256"])
        self.assertEqual((self.tip, "estavel"), tuple(self.state()["sources"]["demo"][k] for k in ("commit", "ref")))

    def test_update_with_commit_pins_a_specific_commit(self):
        repo = self.repo()
        write_plugin(repo, {**MANIFEST, "version": "0.2.0"})
        git(repo, "commit", "--quiet", "-am", "v0.2.0")
        self.install_at(repo)
        self.assertEqual("0.2.0", self.state()["enabled"]["demo"]["version"])
        preview = install_mod.update("demo", confirm=False, commit=self.first)
        self.assertEqual({"from": "0.2.0", "to": "0.1.0"}, preview["diff"]["version"])
        install_mod.update("demo", confirm=True, expect=preview["plugin"]["sha256"], commit=self.first)
        self.assertEqual(self.first, self.state()["sources"]["demo"]["commit"])
        self.assertEqual("0.1.0", self.state()["enabled"]["demo"]["version"])

    def test_update_of_a_folder_origin_refuses_commit(self):
        folder = write_plugin(self.work / "plain")
        self.install_at(folder)
        with self.assertRaises(ValueError) as ctx:
            install_mod.update("demo", confirm=False, commit="0" * 40)
        self.assertIn("repositório git", str(ctx.exception))

    def test_verify_hook_failure_leaves_no_staging(self):
        repo = self.repo()
        seen = []

        def refuse(manifest, sha):
            seen.append((manifest["id"], sha))
            raise ValueError("o índice diz outro sha256")

        with self.assertRaises(ValueError) as ctx:
            install_mod.install_from(git_source.GitSource(str(repo.resolve())), False, verify=refuse)
        self.assertIn("outro sha256", str(ctx.exception))
        self.assertEqual("demo", seen[0][0])
        self.assertEqual([], self.leftover_staging())
        self.assertFalse((self.home / "plugins" / "demo").exists())

        self.install_at(repo)
        with self.assertRaises(ValueError):
            install_mod.update_from("demo", git_source.GitSource(str(repo.resolve())), False, verify=refuse)
        self.assertEqual([], self.leftover_staging())

    def test_origin_extra_is_recorded_and_limited_to_known_keys(self):
        repo = self.repo()
        spec = git_source.GitSource(str(repo.resolve()), self.first)
        extra = {"marketplace": "loja", "tier": "verified", "index_commit": "a" * 40}
        preview = install_mod.install_from(spec, False, origin_extra=extra)
        install_mod.install_from(spec, True, preview["plugin"]["sha256"], origin_extra=extra)
        origin = self.state()["sources"]["demo"]
        self.assertEqual(
            ("loja", "verified", "a" * 40), (origin["marketplace"], origin["tier"], origin["index_commit"])
        )
        self.assertEqual("enabled", loader.inventory()[0]["status"])
        with self.assertRaises(ValueError):
            install_mod.update_from("demo", spec, False, origin_extra={"source": "/outro"})


class OriginStateTests(InstallTestCase):
    def test_old_plugins_json_origin_without_new_keys_still_valid(self):
        state = {"enabled": {}, "sources": {"demo": {"source": "/x", "commit": None}}}
        (self.home / "plugins.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(state, loader.read_state())
        full = {"source": "/x", "commit": "a" * 40, "ref": "main", "subdir": "p", "marketplace": None}
        (self.home / "plugins.json").write_text(
            json.dumps({"enabled": {}, "sources": {"demo": full}}), encoding="utf-8"
        )
        self.assertEqual(full, loader.read_state()["sources"]["demo"])
        for key in ("ref", "subdir", "marketplace", "tier", "index_commit"):
            bad = {"enabled": {}, "sources": {"demo": {"source": "/x", "commit": None, key: 1}}}
            (self.home / "plugins.json").write_text(json.dumps(bad), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(ValueError):
                loader.read_state()

    def test_permissions_added_covers_every_permission_key(self):
        before = {"network": ["a.example"], "env": [], "paths": ["~/Midia/sfx"], "project_write": []}
        after = {"network": ["a.example"], "env": ["X_TOKEN"], "paths": ["~/Midia"], "project_write": ["analysis"]}
        self.assertEqual(
            {"network": [], "env": ["X_TOKEN"], "paths": ["~/Midia"], "project_write": ["analysis"]},
            loader.permissions_added(before, after),
        )
        self.assertEqual(
            {"network": ["a.example"], "env": [], "paths": ["~/Midia/sfx"], "project_write": []},
            loader.permissions_added(None, before),
        )


if __name__ == "__main__":
    unittest.main()
