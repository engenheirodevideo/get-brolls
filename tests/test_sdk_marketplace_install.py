"""`plugins --action install --id <id>@<marketplace>`: instalar pelo índice fixado.

O índice só pré-preenche o `--expect`: sem `--yes` a resposta é sempre a prévia,
o sha256 materializado tem que bater com o `content_sha256` da entrada e o
manifesto tem que bater com ela. Repositórios git locais num temporário; nenhum
teste fala com a rede.
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_install import git, head, write_plugin
from test_sdk_loader import MANIFEST, PLUGIN_CODE
from test_sdk_marketplace_client import MarketplaceTestCase, build_entry

from getbrolls import runtime
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

    def confirm_install(self, ref=REF):
        """Prévia e confirmação com o `--expect` que a prévia mostra (derivado: o plugin pede permissões)."""
        preview = mki.install(ref, confirm=False, expect=None)
        return mki.install(ref, confirm=True, expect=preview["expect"])

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
        self.assertEqual(
            {"name": "exemplo", "commit": pin.commit, "tier": "community", "tier_verified": False},
            {key: preview["marketplace"][key] for key in ("name", "commit", "tier", "tier_verified")},
        )
        self.assertNotEqual(entry["content_sha256"], preview["expect"], "o plugin pede permissões")
        self.assertEqual(entry["content_sha256"], preview["plugin"]["sha256"])
        self.assertIn(f"--id {REF} --yes --expect {preview['expect']}", preview["next"])
        self.assertIn("pessoa", preview["note"])

    def test_yes_without_expect_refused(self):
        self.market()
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=True, expect=None)
        self.assertIn("--expect", str(ctx.exception))
        self.assertEqual([], self.plugin_dirs())

    def test_yes_with_expect_installs_and_records_marketplace_origin(self):
        entry = self.market()
        done = self.confirm_install()
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
        entry = self.market(lambda e: {**e, "content_sha256": "0" * 64})
        derived = mki._confirmation({**entry, "content_sha256": "0" * 64}, None)[0]  # pylint: disable=protected-access
        for confirm in (False, True):
            with self.assertRaises(ValueError) as ctx:
                mki.install(REF, confirm=confirm, expect=derived)
            self.assertIn("content_sha256", str(ctx.exception))
        self.assertEqual([], self.plugin_dirs(), "nada fica, nem staging")

    def test_index_understating_permissions_refused(self):
        self.market(lambda e: {**e, "permissions": {**e["permissions"], "network": []}})
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        self.assertIn("permissions", str(ctx.exception))
        self.assertEqual([], self.plugin_dirs())

    def test_an_incompatible_entry_is_refused_with_the_reason(self):
        self.market(lambda e: {**e, "campo_de_versao_nova": True})
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        self.assertIn("não serve nesta versão", str(ctx.exception))
        self.assertIn("campo_de_versao_nova", str(ctx.exception))
        self.assertEqual([], self.plugin_dirs())

    def test_an_entry_with_an_official_id_is_refused_with_the_reason_in_any_tier(self):
        for tier in ("official", "community"):
            with self.subTest(tier=tier):
                self.market(lambda e, tier=tier: {**e, "id": "hyperframes", "tier": tier})
                with self.assertRaises(ValueError) as ctx:
                    mki.install("hyperframes@exemplo", confirm=False, expect=None)
                self.assertIn("não serve nesta versão", str(ctx.exception))
                self.assertIn("reservado para plugin oficial do getbrolls", str(ctx.exception))
                self.assertEqual([], self.plugin_dirs())
                marketplace.remove("exemplo")

    def test_reading_the_index_warns_once_per_incompatible_entry(self):
        marketplace._NOTED.clear()  # pylint: disable=protected-access  # outro teste do processo já avisou
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        self.market(lambda e: {**e, "campo_de_versao_nova": True})
        marketplace.load_index("exemplo")
        marketplace.load_index("exemplo")
        codes = [row["code"] for row in event["warnings"]]
        self.assertEqual(1, codes.count("MARKETPLACE_ENTRY_INCOMPATIBLE"))

    def watch(self):
        """Um evento de comando ativo e o aviso de "já avisei" zerado, como num processo novo."""
        marketplace._NOTED.clear()  # pylint: disable=protected-access
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        return event

    def test_a_credential_in_an_incompatible_entry_reaches_neither_the_warning_nor_the_refusal(self):
        event = self.watch()
        self.market(lambda e: {**e, "source": {**e["source"], "repo": "https://user:SECRETTOKEN@evil.com/x.git"}})
        marketplace.load_index("exemplo")
        shown = [row["message"] for row in event["warnings"]]
        self.assertTrue(any(row["code"] == "MARKETPLACE_ENTRY_INCOMPATIBLE" for row in event["warnings"]))
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=False, expect=None)
        for text in (*shown, str(ctx.exception)):
            self.assertNotIn("SECRETTOKEN", text)
            self.assertNotIn("evil.com", text)

    def test_control_characters_in_an_unknown_key_do_not_reach_the_reason_or_the_warning(self):
        event = self.watch()
        self.market(lambda e: {**e, "\x1b[31mvermelho\x07": 1})
        row = marketplace.load_index("exemplo")[1]["incompatible"][0]
        self.assertNotIn("\x1b", row["reason"])
        self.assertNotIn("\x07", row["reason"])
        self.assertIn("vermelho", row["reason"])
        for warning in event["warnings"]:
            self.assertNotIn("\x1b", warning["message"])

    def test_the_reason_does_not_carry_the_location_prefix(self):
        self.market(self.odd_entry)
        reason = marketplace.load_index("exemplo")[1]["incompatible"][0]["reason"]
        self.assertNotIn("Índice", reason)
        self.assertNotIn("plugins[", reason)

    @staticmethod
    def odd_entry(entry):
        return {**entry, "campo_de_versao_nova": True}

    def test_the_warning_label_is_one_clean_line(self):
        event = self.watch()
        index = {
            "name": "exemplo",
            "incompatible": [{"position": 0, "id": "a\x1b[31m\nb", "reason": "motivo"}],
        }
        marketplace._note_incompatible(index)  # pylint: disable=protected-access
        message = event["warnings"][0]["message"]
        self.assertNotIn("\x1b", message)
        self.assertNotIn("\n", message)

    def test_two_incompatible_entries_warn_twice(self):
        event = self.watch()
        index = {
            "name": "exemplo",
            "incompatible": [
                {"position": 0, "id": "um", "reason": "mesmo motivo"},
                {"position": 1, "id": "um", "reason": "mesmo motivo"},
            ],
        }
        marketplace._note_incompatible(index)  # pylint: disable=protected-access
        self.assertEqual(2, len(event["warnings"]))

    def test_a_different_entry_at_the_same_position_warns_again(self):
        event = self.watch()
        first = {"name": "exemplo", "incompatible": [{"position": 0, "id": "um", "reason": "motivo a"}]}
        second = {"name": "exemplo", "incompatible": [{"position": 0, "id": "dois", "reason": "motivo b"}]}
        marketplace._note_incompatible(first)  # pylint: disable=protected-access
        marketplace._note_incompatible(first)  # pylint: disable=protected-access
        marketplace._note_incompatible(second)  # pylint: disable=protected-access
        self.assertEqual(2, len(event["warnings"]))

    def test_fetching_the_index_warns(self):
        event = self.watch()
        self.market(self.odd_entry)  # `marketplace-add` busca o índice
        self.assertEqual(1, [row["code"] for row in event["warnings"]].count("MARKETPLACE_ENTRY_INCOMPATIBLE"))

    def test_reading_the_cache_in_a_fresh_process_warns(self):
        self.market(self.odd_entry)
        event = self.watch()  # processo novo: nada avisado ainda
        marketplace.load_index("exemplo")
        self.assertEqual(1, [row["code"] for row in event["warnings"]].count("MARKETPLACE_ENTRY_INCOMPATIBLE"))

    def test_marketplace_add_keeps_an_index_whose_entries_are_all_unreadable_and_warns(self):
        event = self.watch()
        self.market(self.odd_entry)
        self.assertEqual(0, len(marketplace.load_index("exemplo")[1]["plugins"]))
        self.assertIn("exemplo", marketplace.read_state()["marketplaces"])
        self.assertIn("MARKETPLACE_ENTRY_INCOMPATIBLE", [row["code"] for row in event["warnings"]])

    def test_an_entry_whose_id_is_not_a_string_is_set_aside_without_a_label(self):
        self.market(lambda e: {**e, "id": 5})
        row = marketplace.load_index("exemplo")[1]["incompatible"][0]
        self.assertIsNone(row["id"])

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
        self.market()
        self.confirm_install()
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
        self.assertEqual(entry["content_sha256"], preview["plugin"]["sha256"])
        done = run_cli(
            "plugins", "--action", "install", "--id", REF, "--yes", "--expect", preview["expect"], env=self.env()
        )
        self.assertTrue(done["installed"])
        self.assertEqual("exemplo", loader.read_state()["sources"]["demo"]["marketplace"])

    def test_plain_id_without_source_is_a_usage_error_naming_both_forms(self):
        err = run_cli("plugins", "--action", "install", "--id", "demo", expect=2, env=self.env())
        self.assertIn("--source", err["error"])
        self.assertIn("ID@MARKETPLACE", err["error"])

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
        self.confirm_install()
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

    def odd(self, entry):
        """A mesma entrada com uma chave que esta versão não conhece."""
        return {**entry, "campo_de_versao_nova": True}

    def test_an_installed_plugin_whose_entry_became_incompatible_is_not_reported_as_removed(self):
        self.installed()
        self.publish(mutate=self.odd, refresh=False)
        diff = marketplace.refresh("exemplo")["marketplaces"][0]["diff"]
        self.assertEqual([], diff["removed"])

    def test_update_of_an_incompatible_entry_says_so_instead_of_no_longer_listed(self):
        self.installed()
        self.publish(mutate=self.odd)
        with self.assertRaises(ValueError) as ctx:
            mki.update("demo", confirm=False, expect=None)
        self.assertIn("não serve nesta versão", str(ctx.exception))
        self.assertNotIn("não traz mais", str(ctx.exception))

    def test_the_origin_notice_of_an_incompatible_entry_is_not_no_longer_listed(self):
        self.installed()
        self.publish(mutate=self.odd)
        notice = mki.plugin_origins()["demo"]["marketplace_notice"]
        self.assertIn("não serve nesta versão", notice)
        self.assertNotIn("não traz mais", notice)

    def test_disabled_plugin_stays_disabled_after_marketplace_update(self):
        self.installed()
        loader.disable("demo")
        self.publish()
        preview = mki.update("demo", confirm=False, expect=None)
        done = mki.update("demo", confirm=True, expect=preview["expect"])
        self.assertTrue(done["updated"])
        self.assertFalse(done["enabled"])
        self.assertNotIn("demo", loader.read_state()["enabled"])

    def update_answers(self):
        """A resposta a "tem update de demo?" do marketplace-update, do update --id e do update --all."""
        refreshed = marketplace.refresh("exemplo")["marketplaces"][0]["installed_updates"]
        single = mki.update("demo", confirm=False, expect=None)
        listed = [row["id"] for row in mki.update_all(confirm=False, expect=None)["plugins"]]
        return "demo" in refreshed, not single.get("up_to_date", False), "demo" in listed

    def test_update_available_is_one_answer(self):
        """O conteúdo decide (versão + content_sha256 contra o pin); commit sozinho não é update."""
        self.installed()
        folder = loader.plugins_root() / "demo"
        # Mesmo commit, conteúdo instalado diferente do índice (a pessoa mexeu e religou): é update.
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mexido à mão\n", encoding="utf-8")
        loader.enable("demo", confirm=True, expect=loader.pin_digests(folder)[0])
        self.assertEqual((True, True, True), self.update_answers())
        preview = mki.update("demo", confirm=False, expect=None)
        self.assertTrue(mki.update("demo", confirm=True, expect=preview["expect"])["updated"])
        self.assertEqual((False, False, False), self.update_answers())
        # Commit novo no índice, mesmo conteúdo do plugin: não é update.
        (self.repo / "LEIAME.md").write_text("só o repositório mudou\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "leiame")
        entry = build_entry(self.repo, head(self.repo))
        self.assertNotEqual(loader.read_state()["sources"]["demo"]["commit"], entry["source"]["commit"])
        self.commit_index(self.repo, "exemplo", [entry])
        self.assertEqual((False, False, False), self.update_answers())
        # Versão nova: os três dizem que tem.
        self.publish(refresh=False)
        self.assertEqual((True, True, True), self.update_answers())

    def test_without_any_pin_the_installed_folder_answers(self):
        """Sem pin nenhum (plugins.json antigo ou editado à mão), quem responde é o conteúdo da
        pasta instalada: versão nova no índice é update nos três, e o update não fica oferecido em
        laço depois de aplicado."""
        self.installed()
        state = loader.read_state()
        state["enabled"].pop("demo")
        state.get("last_pins", {}).pop("demo", None)
        loader.write_state(state)
        self.assertEqual((False, False, False), self.update_answers())
        self.publish(refresh=False)
        self.assertEqual((True, True, True), self.update_answers())
        preview = mki.update("demo", confirm=False, expect=None)
        self.assertEqual({"from": "0.1.0", "to": "0.2.0"}, preview["diff"]["version"])
        self.assertTrue(mki.update("demo", confirm=True, expect=preview["expect"])["updated"])
        self.assertNotIn("demo", loader.read_state()["enabled"])
        self.assertEqual((False, False, False), self.update_answers())


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
        pin, _ = marketplace.load_index("exemplo")
        # A lista mostra a origem local sob a pasta pessoal como `~/…`. No Windows o
        # temporário já fica sob a pasta pessoal; aqui ela passa a conter a origem também
        # no POSIX (o Windows lê USERPROFILE, não HOME), e o esperado sai do mesmo jeito.
        home = {"HOME": str(Path(pin.source).parent.parent)}
        rows = {row["id"]: row for row in run_cli("plugins", "--action", "list", env={**self.env(), **home})["plugins"]}
        with patch.dict(os.environ, home):
            shown = marketplace.portable_text(pin.source)
        if os.name != "nt":
            # Here the personal folder is the temporary folder itself: the tie keeps `$TMPDIR` (labels.anchors).
            self.assertTrue(shown.startswith(("~/", "$TMPDIR/")), shown)
        demo = rows["demo"]["origin"]
        self.assertEqual(
            {
                "source": shown,
                "commit": entry["source"]["commit"],
                "ref": None,
                "subdir": "plugins/demo",
                "marketplace": "exemplo",
                "tier": "verified",
                "index_commit": pin.commit,
                "tier_verified": False,
                "tier_note": "declarado pelo marketplace, não verificado",
                "tier_label": "verified (declarado, não verificado)",
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
