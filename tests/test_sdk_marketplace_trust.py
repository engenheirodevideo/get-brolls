"""Confiança no marketplace: tier declarado, confirmação de permissões e commit fora da ponta.

- `tier` é o que o índice declara; `tier_verified` só é verdadeiro para o índice oficial
  (nome `getbrolls-plugins` E origem fixada num dos repositórios oficiais). Fora dele, a
  saída diz "declarado pelo marketplace, não verificado" e o tier nunca muda regra;
- instalar pelo marketplace um plugin que pede QUALQUER permissão exige o mesmo valor
  derivado do update que acrescenta permissão: o sha256 do índice sozinho não instala;
- `ref` dada com um commit que não é a ponta dela vira aviso na prévia.
"""

import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_install import git, head, write_plugin
from test_sdk_install_commit import rev
from test_sdk_loader import MANIFEST
from test_sdk_marketplace_client import build_entry
from test_sdk_marketplace_install import REF, MarketInstallCase

from getbrolls.sdk import install as install_mod
from getbrolls.sdk import marketplace
from getbrolls.sdk import marketplace_install as mki

NOT_VERIFIED = "declarado pelo marketplace, não verificado"


class TierTests(MarketInstallCase):
    def official_named(self, tier="official"):
        """Marketplace local que se chama `getbrolls-plugins` e declara `tier`."""
        repo = self.index_repo(name=marketplace.OFFICIAL_INDEX_NAME, folder="oficial")
        entry = build_entry(repo, head(repo), tier=tier)
        self.commit_index(repo, marketplace.OFFICIAL_INDEX_NAME, [entry])
        return repo

    def test_official_repo_constants(self):
        self.assertEqual("getbrolls-plugins", marketplace.OFFICIAL_INDEX_NAME)
        for url in (
            "https://github.com/engenheirodevideo/getbrolls-plugins",
            "https://github.com/engenheirodevideo/getbrolls-plugins.git",
            "https://github.com/engenheirodevideo/get-brolls-plugins",
            "https://github.com/engenheirodevideo/get-brolls-plugins.git",
        ):
            self.assertIn(url, marketplace.OFFICIAL_INDEX_REPOS)

    def test_a_spoofed_official_tier_is_shown_as_declared(self):
        repo = self.official_named()
        added = marketplace.add(str(repo))
        self.assertTrue(any("oficial" in warning for warning in added["warnings"]))
        self.assertFalse(added["marketplace"]["official"])
        row = marketplace.search("demo")["results"][0]
        self.assertEqual(("official", False, NOT_VERIFIED), (row["tier"], row["tier_verified"], row["tier_note"]))
        preview = mki.install(f"demo@{marketplace.OFFICIAL_INDEX_NAME}", confirm=False, expect=None)
        block = preview["marketplace"]
        self.assertEqual(("official", False, NOT_VERIFIED), (block["tier"], block["tier_verified"], block["tier_note"]))
        # O campo que um resumo humano mostra nunca diz "official" sem a ressalva.
        self.assertEqual("official (declarado, não verificado)", row["tier_label"])
        self.assertEqual("official (declarado, não verificado)", block["tier_label"])
        self.assertFalse(marketplace.summary()[0]["official"])

    def test_the_official_index_is_verified(self):
        repo = self.official_named()
        with patch.object(marketplace, "OFFICIAL_INDEX_REPOS", (str(repo.resolve()),)):
            added = marketplace.add(str(repo))
            self.assertEqual([], added["warnings"])
            self.assertTrue(added["marketplace"]["official"])
            row = marketplace.search("demo")["results"][0]
            self.assertEqual((True, None), (row["tier_verified"], row["tier_note"]))
            self.assertEqual(row["tier"], row["tier_label"])
            preview = mki.install(f"demo@{marketplace.OFFICIAL_INDEX_NAME}", confirm=False, expect=None)
            self.assertTrue(preview["marketplace"]["tier_verified"])
            mki.install(f"demo@{marketplace.OFFICIAL_INDEX_NAME}", confirm=True, expect=preview["expect"])
            origin = run_cli("plugins", "--action", "list", env=self.env())["plugins"][0]["origin"]
        self.assertEqual("official", origin["tier"])
        self.assertFalse(origin["tier_verified"], "fora do patch o repositório local não é o oficial")
        self.assertEqual(NOT_VERIFIED, origin["tier_note"])

    def test_same_name_from_another_source_is_not_verified(self):
        repo = self.official_named()
        with patch.object(marketplace, "OFFICIAL_INDEX_REPOS", ("https://example.com/outro.git",)):
            marketplace.add(str(repo))
            self.assertFalse(marketplace.search("demo")["results"][0]["tier_verified"])

    def test_list_and_doctor_carry_tier_and_tier_verified(self):
        entry = self.market(tier="verified")
        preview = mki.install(REF, confirm=False, expect=None)
        mki.install(REF, confirm=True, expect=preview["expect"])
        self.assertNotEqual(entry["content_sha256"], preview["expect"])
        listed = run_cli("plugins", "--action", "list", env=self.env())["plugins"][0]["origin"]
        doctor = run_cli("doctor", env=self.env())["plugins"][0]["origin"]
        for origin in (listed, doctor):
            self.assertEqual(
                ("verified", False, NOT_VERIFIED), (origin["tier"], origin["tier_verified"], origin["tier_note"])
            )
            self.assertEqual("verified (declarado, não verificado)", origin["tier_label"])
        capabilities = run_cli("capabilities", env=self.env())
        self.assertFalse(capabilities["marketplaces"][0]["official"])

    def test_spoofed_tier_is_never_auto_update_eligible(self):
        self.market(tier="official")
        preview = mki.install(REF, confirm=False, expect=None)
        mki.install(REF, confirm=True, expect=preview["expect"])
        write_plugin(self.repo / "plugins" / "demo", {**MANIFEST, "version": "0.2.0"})
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "0.2.0")
        self.commit_index(self.repo, "exemplo", [build_entry(self.repo, head(self.repo), tier="official")])
        marketplace.refresh("exemplo")
        row = mki.update_all(confirm=False, expect=None)["plugins"][0]
        self.assertEqual(("official", False), (row["tier"], row["tier_verified"]))
        self.assertFalse(row["auto_update_eligible"])


class PermissionConfirmationTests(MarketInstallCase):
    def test_install_of_a_plugin_with_permissions_needs_the_derived_value(self):
        entry = self.market(tier="official")
        preview = mki.install(REF, confirm=False, expect=None)
        self.assertNotEqual(entry["content_sha256"], preview["expect"])
        self.assertEqual(entry["content_sha256"], preview["plugin"]["sha256"])
        self.assertEqual(["demo.example"], preview["permissions_added"]["network"])
        self.assertIn(f"--expect {preview['expect']}", preview["next"])
        self.assertIn("permiss", preview["note"])
        with self.assertRaises(ValueError) as ctx:
            mki.install(REF, confirm=True, expect=entry["content_sha256"])
        self.assertIn("permiss", str(ctx.exception))
        self.assertEqual([], self.plugin_dirs())
        done = mki.install(REF, confirm=True, expect=preview["expect"])
        self.assertTrue(done["installed"])

    def test_install_without_permissions_keeps_the_index_sha(self):
        repo = self.work / "sem_permissoes"
        write_plugin(repo / "plugins" / "demo", {**MANIFEST, "permissions": {}})
        git(repo, "init", "--quiet")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "plugin")
        entry = build_entry(repo, head(repo))
        self.commit_index(repo, "exemplo", [entry])
        marketplace.add(str(repo))
        preview = mki.install(REF, confirm=False, expect=None)
        self.assertEqual(entry["content_sha256"], preview["expect"])
        self.assertTrue(mki.install(REF, confirm=True, expect=entry["content_sha256"])["installed"])


class TipWarningTests(MarketInstallCase):
    def test_ref_whose_tip_is_another_commit_warns_in_the_preview(self):
        repo = self.index_repo(folder="ponta")
        git(repo, "branch", "estavel")  # ponta de estavel = commit do índice, não o do plugin
        entry = build_entry(repo, rev(repo, "HEAD~1"))
        self.commit_index(repo, "exemplo", [{**entry, "source": {**entry["source"], "ref": "estavel"}}])
        marketplace.add(str(repo))
        preview = mki.install(REF, confirm=False, expect=None)
        self.assertTrue(any("não é a ponta de estavel" in w for w in preview["plugin"]["warnings"]))

    def test_install_source_ref_tip_check(self):
        repo = write_plugin(self.work / "repo")
        git(repo, "init", "--quiet")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "0.1.0")
        first = head(repo)
        write_plugin(repo, {**MANIFEST, "version": "0.2.0"})
        git(repo, "commit", "--quiet", "-am", "0.2.0")
        git(repo, "branch", "estavel")
        old = install_mod.install(str(repo), confirm=False, commit=first, ref="estavel")
        self.assertTrue(any("não é a ponta de estavel" in w for w in old["plugin"]["warnings"]))
        tip = install_mod.install(str(repo), confirm=False, commit=head(repo), ref="estavel")
        self.assertFalse(any("ponta" in w for w in tip["plugin"].get("warnings", [])))


if __name__ == "__main__":
    unittest.main()
