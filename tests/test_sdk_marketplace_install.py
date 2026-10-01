"""`plugins --action install --id <id>@<marketplace>`: instalar pelo índice fixado.

O índice só pré-preenche o `--expect`: sem `--yes` a resposta é sempre a prévia,
o sha256 materializado tem que bater com o `content_sha256` da entrada e o
manifesto tem que bater com ela. Repositórios git locais num temporário; nenhum
teste fala com a rede.
"""

import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_install import git, head, write_plugin
from test_sdk_loader import MANIFEST
from test_sdk_marketplace_client import MarketplaceTestCase, build_entry

from getbrolls.errors import UsageError
from getbrolls.sdk import git_source, loader, marketplace
from getbrolls.sdk import install as install_mod
from getbrolls.sdk import marketplace_install as mki
from getbrolls.sdk.manifest import current_platform

REF = "demo@exemplo"


class MarketInstallCase(MarketplaceTestCase):
    def market(self, mutate=None, renames=None, tier="community"):
        """Marketplace `exemplo` com `demo`; `mutate(entry)` devolve a entrada que vai para o índice."""
        self.markets = getattr(self, "markets", 0) + 1  # pylint: disable=attribute-defined-outside-init
        repo = self.index_repo(folder=f"index_{self.markets}")
        commit = head(repo)
        entry = build_entry(repo, commit, tier=tier)
        entries = [mutate(entry) if mutate else entry]
        self.commit_index(repo, "exemplo", [e for e in entries if e is not None], renames)
        marketplace.add(str(repo))
        self.repo = repo  # pylint: disable=attribute-defined-outside-init
        return entry

    def plugin_dirs(self):
        root = loader.plugins_root()
        return sorted(child.name for child in root.iterdir()) if root.is_dir() else []


class InstallTests(MarketInstallCase):
    def test_install_without_yes_is_preview_even_with_expect(self):
        entry = self.market()
        preview = mki.install(REF, confirm=False, expect=entry["content_sha256"])
        self.assertFalse(preview["installed"])
        self.assertEqual([], self.plugin_dirs())
        pin, _ = marketplace.load_index("exemplo")
        self.assertEqual({"name": "exemplo", "commit": pin.commit, "tier": "community"}, preview["marketplace"])
        self.assertEqual(entry["content_sha256"], preview["expect"])
        self.assertEqual(entry["content_sha256"], preview["plugin"]["sha256"])
        self.assertIn(f"--id {REF} --yes --expect {entry['content_sha256']}", preview["next"])
        self.assertIn("pessoa", preview["note"])

    def test_yes_without_expect_refused(self):
        self.market()
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=True, expect=None)
        self.assertIn("--expect", str(ctx.exception))
        self.assertEqual([], self.plugin_dirs())

    def test_yes_with_expect_installs_and_records_marketplace_origin(self):
        entry = self.market()
        done = mki.install(REF, confirm=True, expect=entry["content_sha256"])
        self.assertTrue(done["installed"])
        origin = loader.read_state()["sources"]["demo"]
        pin, _ = marketplace.load_index("exemplo")
        self.assertEqual("exemplo", origin["marketplace"])
        self.assertEqual("community", origin["tier"])
        self.assertEqual(pin.commit, origin["index_commit"])
        self.assertEqual(entry["source"]["commit"], origin["commit"])
        self.assertEqual("plugins/demo", origin["subdir"])
        self.assertEqual(pin.source, origin["source"])
        self.assertIn("demo", loader.read_state()["enabled"])

    def test_index_sha_mismatch_refused(self):
        self.market(lambda e: {**e, "content_sha256": "0" * 64})
        for confirm in (False, True):
            with self.assertRaises(ValueError) as ctx:
                mki.install(REF, confirm=confirm, expect="0" * 64)
            self.assertIn("content_sha256", str(ctx.exception))
        self.assertEqual([], self.plugin_dirs(), "nada fica, nem staging")

    def test_index_understating_permissions_refused(self):
        self.market(lambda e: {**e, "permissions": {**e["permissions"], "network": []}})
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        self.assertIn("permissions", str(ctx.exception))
        self.assertEqual([], self.plugin_dirs())

    def test_description_divergence_is_only_a_warning(self):
        self.market(lambda e: {**e, "description": "Outra descrição"})
        preview = mki.install(REF, confirm=False, expect=None)
        self.assertTrue(any("description" in warning for warning in preview["plugin"]["warnings"]))

    def test_yanked_refused_deprecated_warns_renamed_hints(self):
        self.market(lambda e: {**e, "yanked": True})
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        self.assertIn("retirad", str(ctx.exception))
        marketplace.remove("exemplo")
        self.market(lambda e: {**e, "deprecated": {"reason": "sem manutenção", "replacement": None}})
        preview = mki.install(REF, confirm=False, expect=None)
        self.assertTrue(any("sem manutenção" in warning for warning in preview["plugin"]["warnings"]))
        with self.assertRaises(ValueError) as ctx:
            mki.install("nada@exemplo", confirm=False, expect=None)
        self.assertIn("search", str(ctx.exception))
        marketplace.remove("exemplo")
        self.market(renames={"velho": "demo"})
        with self.assertRaises(ValueError) as ctx:
            mki.install("velho@exemplo", confirm=False, expect=None)
        self.assertIn("demo@exemplo", str(ctx.exception))

    def test_incompatible_platform_refused_before_fetch(self):
        other = next(name for name in ("darwin", "linux", "windows") if name != current_platform())
        self.market(lambda e: {**e, "platforms": [other]})
        with self.no_network(), self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        self.assertIn("platforms", str(ctx.exception))
        marketplace.remove("exemplo")
        self.market(lambda e: {**e, "requires_getbrolls": ">=99"})
        with self.no_network(), self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        self.assertIn(">=99", str(ctx.exception))

    def test_dot_repo_resolves_to_marketplace_source_only(self):
        entry = self.market()
        pin, _ = marketplace.load_index("exemplo")
        self.assertEqual(pin.source, marketplace.resolve_repo(".", pin))
        seen = {}

        def fake_install_from(spec, confirm, expect=None, **hooks):
            seen.update(spec=spec, hooks=hooks, confirm=confirm, expect=expect)
            return {"installed": False, "plugin": {"sha256": entry["content_sha256"]}, "note": ""}

        with patch.object(install_mod, "install_from", side_effect=fake_install_from):
            mki.install(REF, confirm=False, expect=None)
        self.assertEqual(
            git_source.GitSource(pin.source, entry["source"]["commit"], None, "plugins/demo"), seen["spec"]
        )
        self.assertEqual(
            {"marketplace": "exemplo", "tier": "community", "index_commit": pin.commit},
            seen["hooks"]["origin_extra"],
        )

    def test_restricted_marketplace_refused(self):
        self.market()
        marketplace.set_policy(frozenset({"outro"}))
        with self.no_network(), self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        self.assertIn("perfil", str(ctx.exception))

    def test_bad_reference_is_a_usage_error(self):
        for ref in ("demo", "demo@", "@exemplo", "demo@engenheirodevideo", "a@b@c"):
            with self.assertRaises(UsageError):
                mki.install(ref, confirm=False, expect=None)

    def test_already_installed_refused(self):
        entry = self.market()
        mki.install(REF, confirm=True, expect=entry["content_sha256"])
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        self.assertIn("update", str(ctx.exception))


class InstallCliTests(MarketInstallCase):
    def test_source_and_marketplace_ref_together_is_usage_error(self):
        self.market()
        run_cli("plugins", "--action", "install", "--id", REF, "--source", str(self.repo), expect=2, env=self.env())
        run_cli("plugins", "--action", "install", "--id", REF, "--commit", "a" * 40, expect=2, env=self.env())
        run_cli("plugins", "--action", "install", "--id", REF, "--subdir", "plugins", expect=2, env=self.env())
        self.assertEqual([], self.plugin_dirs())

    def test_cli_preview_then_confirm(self):
        entry = self.market()
        preview = run_cli("plugins", "--action", "install", "--id", REF, env=self.env())
        self.assertFalse(preview["installed"])
        self.assertEqual(entry["content_sha256"], preview["expect"])
        done = run_cli(
            "plugins", "--action", "install", "--id", REF, "--yes", "--expect", preview["expect"], env=self.env()
        )
        self.assertTrue(done["installed"])
        self.assertEqual("exemplo", loader.read_state()["sources"]["demo"]["marketplace"])

    def test_plain_id_without_source_keeps_the_old_message(self):
        err = run_cli("plugins", "--action", "install", "--id", "demo", expect=1, env=self.env())
        self.assertIn("--source", err["error"])

    def test_second_plugin_in_the_same_index(self):
        repo = self.index_repo()
        write_plugin(repo / "plugins" / "extra", {**MANIFEST, "id": "extra"})
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "extra")
        commit = head(repo)
        self.commit_index(repo, "exemplo", [build_entry(repo, commit), build_entry(repo, commit, "extra")])
        marketplace.add(str(repo))
        preview = mki.install("extra@exemplo", confirm=False, expect=None)
        self.assertEqual("extra", preview["plugin"]["id"])
        self.assertEqual("plugins/extra", preview["plugin"]["subdir"])


class MarketUpdateCase(MarketInstallCase):
    def installed(self, tier="community"):
        entry = self.market(tier=tier)
        mki.install(REF, confirm=True, expect=entry["content_sha256"])
        return entry

    def publish(self, manifest=None, tier="community", mutate=None, refresh=True, renames=None):
        """Nova versão de `demo` no repositório do índice e o índice apontando para ela."""
        write_plugin(self.repo / "plugins" / "demo", manifest or {**MANIFEST, "version": "0.2.0"})
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "demo novo")
        entry = build_entry(self.repo, head(self.repo), tier=tier)
        entry = mutate(entry) if mutate else entry
        self.commit_index(self.repo, "exemplo", [entry] if entry else [], renames)
        if refresh:
            marketplace.refresh("exemplo")
        return entry

    def wider(self):
        return {
            **MANIFEST,
            "version": "0.2.0",
            "permissions": {**MANIFEST["permissions"], "network": ["demo.example", "outro.example"]},
        }


class UpdateTests(MarketUpdateCase):
    def test_update_uses_pinned_index_and_is_offline_until_materialize(self):
        self.installed()
        entry = self.publish(refresh=False)
        self.assertTrue(mki.update("demo", confirm=False, expect=None)["up_to_date"], "índice fixado não mudou")
        marketplace.refresh("exemplo")
        with patch.object(git_source, "resolve_ref", side_effect=AssertionError("ls-remote")):
            preview = mki.update("demo", confirm=False, expect=None)
        self.assertFalse(preview["updated"])
        self.assertEqual({"from": "0.1.0", "to": "0.2.0"}, preview["diff"]["version"])
        self.assertEqual(entry["content_sha256"], preview["expect"])
        self.assertIn(f"--id demo --yes --expect {entry['content_sha256']}", preview["next"])
        self.assertEqual("0.1.0", loader.read_state()["enabled"]["demo"]["version"], "prévia não troca nada")
        done = mki.update("demo", confirm=True, expect=preview["expect"])
        self.assertTrue(done["updated"])
        origin = loader.read_state()["sources"]["demo"]
        self.assertEqual(entry["source"]["commit"], origin["commit"])
        self.assertEqual(marketplace.load_index("exemplo")[0].commit, origin["index_commit"])

    def test_update_up_to_date(self):
        self.installed()
        with self.no_network():
            result = mki.update("demo", confirm=False, expect=None)
        self.assertEqual({"updated": False, "up_to_date": True}, {k: result[k] for k in ("updated", "up_to_date")})

    def test_permission_increase_needs_its_own_preview(self):
        self.installed()
        entry = self.publish(self.wider())
        preview = mki.update("demo", confirm=False, expect=None)
        self.assertTrue(preview["diff"]["permissions_increased"])
        self.assertEqual(["outro.example"], preview["diff"]["permissions_added"]["network"])
        self.assertNotEqual(entry["content_sha256"], preview["expect"], "o sha do índice não basta")
        self.assertIn("permiss", preview["note"])
        with self.assertRaises(ValueError) as ctx:
            mki.update("demo", confirm=True, expect=entry["content_sha256"])
        self.assertIn("permiss", str(ctx.exception))
        self.assertEqual("0.1.0", loader.read_state()["enabled"]["demo"]["version"])
        done = mki.update("demo", confirm=True, expect=preview["expect"])
        self.assertTrue(done["updated"])
        self.assertEqual("0.2.0", loader.read_state()["enabled"]["demo"]["version"])

    def test_update_refuses_sha_mismatch_and_yanked_target(self):
        self.installed()
        self.publish(mutate=lambda e: {**e, "content_sha256": "0" * 64})
        with self.assertRaises(ValueError) as ctx:
            mki.update("demo", confirm=False, expect=None)
        self.assertIn("content_sha256", str(ctx.exception))
        self.publish({**MANIFEST, "version": "0.3.0"}, mutate=lambda e: {**e, "yanked": True})
        with self.assertRaises(ValueError) as ctx:
            mki.update("demo", confirm=False, expect=None)
        self.assertIn("yanked", str(ctx.exception))

    def test_renamed_entry_never_swaps_id(self):
        self.installed()
        write_plugin(self.repo / "plugins" / "novo", {**MANIFEST, "id": "novo"})
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "novo")
        self.commit_index(self.repo, "exemplo", [build_entry(self.repo, head(self.repo), "novo")], {"demo": "novo"})
        marketplace.refresh("exemplo")
        with self.assertRaises(ValueError) as ctx:
            mki.update("demo", confirm=False, expect=None)
        self.assertIn("novo", str(ctx.exception))
        self.assertEqual(["demo"], self.plugin_dirs())

    def test_removed_or_restricted_marketplace_refused(self):
        self.installed()
        marketplace.set_policy(frozenset())
        with self.assertRaises(ValueError) as ctx:
            mki.update("demo", confirm=False, expect=None)
        self.assertIn("perfil", str(ctx.exception))
        marketplace.set_policy(None)
        marketplace.remove("exemplo")
        with self.assertRaises(ValueError) as ctx:
            mki.update("demo", confirm=False, expect=None)
        self.assertIn("marketplace removido", str(ctx.exception))

    def test_disabled_plugin_stays_disabled_after_marketplace_update(self):
        self.installed()
        loader.disable("demo")
        self.publish()
        preview = mki.update("demo", confirm=False, expect=None)
        done = mki.update("demo", confirm=True, expect=preview["expect"])
        self.assertTrue(done["updated"])
        self.assertFalse(done["enabled"])
        self.assertNotIn("demo", loader.read_state()["enabled"])


class UpdateAllTests(MarketUpdateCase):
    def test_all_is_preview_only_with_per_id_commands(self):
        self.installed(tier="verified")
        folder = write_plugin(self.work / "solto", {**MANIFEST, "id": "solto"})
        install_mod.install(str(folder), confirm=True, expect=install_mod.content_digest(str(folder))[0])
        self.publish(self.wider(), tier="verified")
        with self.no_network():
            result = mki.update_all(confirm=False, expect=None)
        self.assertFalse(result["applied"])
        self.assertEqual(["demo"], [row["id"] for row in result["plugins"]])
        row = result["plugins"][0]
        self.assertEqual(("0.1.0", "0.2.0", "verified"), (row["from"], row["to"], row["tier"]))
        self.assertTrue(row["permissions_increased"])
        self.assertFalse(row["auto_update_eligible"], "permissão nova nunca é automática")
        self.assertIn("plugins --action update --id demo", row["command"])
        self.assertNotIn("--yes", row["command"])
        self.assertEqual(["solto"], result["outside_marketplace"])
        for confirm, expect in ((True, None), (True, "a" * 64), (False, "a" * 64)):
            with self.assertRaises(UsageError):
                mki.update_all(confirm=confirm, expect=expect)

    def test_community_is_never_auto_update_eligible(self):
        self.installed()
        self.publish()
        row = mki.update_all(confirm=False, expect=None)["plugins"][0]
        self.assertFalse(row["permissions_increased"])
        self.assertFalse(row["auto_update_eligible"])
        self.assertEqual("community", row["tier"])

    def test_all_lists_up_to_date_and_skipped(self):
        self.installed(tier="official")
        self.assertEqual(["demo"], mki.update_all(confirm=False, expect=None)["up_to_date"])
        self.publish(mutate=lambda e: {**e, "yanked": True})
        skipped = mki.update_all(confirm=False, expect=None)["skipped"]
        self.assertEqual(["demo"], [row["id"] for row in skipped])
        self.assertIn("yanked", skipped[0]["reason"])


class UpdateCliTests(MarketUpdateCase):
    def test_cli_update_dispatches_on_marketplace_origin(self):
        self.installed()
        self.publish(self.wider())
        preview = run_cli("plugins", "--action", "update", "--id", "demo", env=self.env())
        self.assertTrue(preview["diff"]["permissions_increased"])
        run_cli("plugins", "--action", "update", "--id", "demo", "--commit", "a" * 40, expect=2, env=self.env())
        done = run_cli(
            "plugins", "--action", "update", "--id", "demo", "--yes", "--expect", preview["expect"], env=self.env()
        )
        self.assertTrue(done["updated"])

    def test_cli_all_flag_rules(self):
        self.installed()
        out = run_cli("plugins", "--action", "update", "--all", env=self.env())
        self.assertFalse(out["applied"])
        run_cli("plugins", "--action", "update", "--all", "--id", "demo", expect=2, env=self.env())
        run_cli("plugins", "--action", "update", "--all", "--yes", expect=2, env=self.env())
        run_cli("plugins", "--action", "list", "--all", expect=2, env=self.env())


class OriginRowsTests(MarketUpdateCase):
    def test_list_shows_origin_for_installed_plugins(self):
        entry = self.installed(tier="verified")
        folder = write_plugin(self.work / "solto", {**MANIFEST, "id": "solto"})
        install_mod.install(str(folder), confirm=True, expect=install_mod.content_digest(str(folder))[0])
        rows = {row["id"]: row for row in run_cli("plugins", "--action", "list", env=self.env())["plugins"]}
        pin, _ = marketplace.load_index("exemplo")
        demo = rows["demo"]["origin"]
        self.assertEqual(
            {
                "source": pin.source,
                "commit": entry["source"]["commit"],
                "ref": None,
                "subdir": "plugins/demo",
                "marketplace": "exemplo",
                "tier": "verified",
                "index_commit": pin.commit,
                "marketplace_notice": None,
            },
            demo,
        )
        self.assertIsNone(rows["solto"]["origin"]["marketplace"])
        self.assertIsNone(rows["solto"]["origin"]["commit"])

    def test_list_and_doctor_warn_about_a_yanked_installed_plugin(self):
        self.installed()
        self.publish(mutate=lambda e: {**e, "yanked": True})
        listed = run_cli("plugins", "--action", "list", env=self.env())["plugins"][0]
        self.assertIn("yanked", listed["origin"]["marketplace_notice"])
        doctor = run_cli("doctor", env=self.env())
        self.assertIn("yanked", doctor["plugins"][0]["origin"]["marketplace_notice"])
        self.assertEqual(["exemplo"], [row["name"] for row in doctor["marketplaces"]])
        self.assertEqual("exemplo", doctor["plugins"][0]["origin"]["marketplace"])

    def test_list_flags_a_removed_marketplace(self):
        self.installed()
        marketplace.remove("exemplo")
        listed = run_cli("plugins", "--action", "list", env=self.env())["plugins"][0]
        self.assertIn("removido", listed["origin"]["marketplace_notice"])
        self.assertNotIn("marketplaces", run_cli("doctor", env=self.env()))


if __name__ == "__main__":
    unittest.main()
