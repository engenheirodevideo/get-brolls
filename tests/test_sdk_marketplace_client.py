"""Marketplaces de plugins: `marketplace-add|list|remove|update` e `search`, fixados por commit.

O índice de teste é um repositório git local (`getbrolls-marketplace.json` na raiz e
`plugins/demo`), montado num temporário: nenhum teste fala com a rede.
"""

import hashlib
import json
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_install import HAS_GIT, InstallTestCase, git, head, write_plugin
from test_sdk_loader import MANIFEST

from getbrolls import runtime
from getbrolls.errors import UsageError
from getbrolls.sdk import git_source, loader, marketplace
from getbrolls.sdk import install as install_mod
from getbrolls.sdk import marketplace_index as mi


def build_entry(repo, commit, manifest_id="demo", tier="community"):
    spec = git_source.GitSource(str(repo), commit, None, f"plugins/{manifest_id}")
    sha, manifest = install_mod.content_digest(spec)
    source = {"repo": ".", "ref": None, "commit": commit, "subdir": f"plugins/{manifest_id}"}
    return mi.entry_from_manifest(manifest, source=source, content_sha256=sha, tier=tier, maintainers=["Teste"])


def write_index(repo, name, entries, renames=None):
    doc = {
        "schema": "getbrolls.marketplace_index/1",
        "name": name,
        "description": f"Índice {name}",
        "plugins": entries,
        "renames": renames or {},
    }
    (repo / mi.INDEX_NAME).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@unittest.skipUnless(HAS_GIT, "git required")
class MarketplaceTestCase(InstallTestCase):
    def setUp(self):
        super().setUp()
        marketplace.set_policy(marketplace.PROFILE_POLICY)
        self.addCleanup(marketplace.set_policy, marketplace.PROFILE_POLICY)

    def index_repo(self, name="exemplo", folder="index_repo"):
        """Repositório de índice: commit 1 com `plugins/demo`, commit 2 com o índice apontando para ele."""
        repo = self.work / folder
        write_plugin(repo / "plugins" / "demo", {**MANIFEST, "description": "Fonte de demonstração"})
        git(repo, "init", "--quiet")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "plugin")
        plugin_commit = head(repo)
        write_index(repo, name, [build_entry(repo, plugin_commit)])
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "index")
        return repo

    def commit_index(self, repo, name, entries, renames=None):
        write_index(repo, name, entries, renames)
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "index update")
        return head(repo)

    def no_network(self):
        return patch.object(git_source, "run_git", side_effect=AssertionError("git não pode rodar aqui"))


class AddListRemoveTests(MarketplaceTestCase):
    def test_add_pins_commit_and_caches_index(self):
        repo = self.index_repo()
        done = marketplace.add(str(repo))
        self.assertTrue(done["added"])
        self.assertEqual(1, done["plugins"])
        record = marketplace.read_state()["marketplaces"]["exemplo"]
        self.assertEqual(head(repo), record["commit"])
        self.assertEqual(str(repo.resolve()), record["source"])
        self.assertIsNone(record["ref"])
        cache = marketplace.cache_path("exemplo")
        self.assertEqual(self.home / "marketplaces" / "exemplo" / mi.INDEX_NAME, cache)
        self.assertEqual(hashlib.sha256(cache.read_bytes()).hexdigest(), record["index_sha256"])
        self.assertEqual((repo / mi.INDEX_NAME).read_bytes(), cache.read_bytes())
        state = json.loads(marketplace.state_path().read_text(encoding="utf-8"))
        self.assertEqual("getbrolls.marketplaces/1", state["schema"])
        self.assertEqual(self.home / "marketplaces.json", marketplace.state_path())
        pin, index = marketplace.load_index("exemplo")
        self.assertEqual(head(repo), pin.commit)
        self.assertEqual("demo", index["plugins"][0]["id"])

    def test_add_with_ref_and_commit(self):
        repo = self.index_repo()
        first = head(repo)
        git(repo, "branch", "estavel")
        self.commit_index(repo, "exemplo", [])
        marketplace.add(str(repo), ref="estavel")
        self.assertEqual(first, marketplace.read_state()["marketplaces"]["exemplo"]["commit"])
        self.assertEqual("estavel", marketplace.read_state()["marketplaces"]["exemplo"]["ref"])
        marketplace.remove("exemplo")
        marketplace.add(str(repo), commit=first)
        self.assertEqual(first, marketplace.read_state()["marketplaces"]["exemplo"]["commit"])

    def test_add_refuses_plain_folder_reserved_name_and_duplicate_name(self):
        plain = write_plugin(self.work / "pasta")
        (plain / mi.INDEX_NAME).write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            marketplace.add(str(plain))
        self.assertIn("repositório git", str(ctx.exception))
        reserved = self.index_repo(name="exemplo", folder="reservado")
        self.commit_index(reserved, "engenheirodevideo", [])
        with self.assertRaises(ValueError) as ctx:
            marketplace.add(str(reserved))
        self.assertIn("reservado", str(ctx.exception))
        marketplace.add(str(self.index_repo()))
        other = self.index_repo(folder="outro")
        with self.assertRaises(ValueError) as ctx:
            marketplace.add(str(other))
        self.assertIn("já existe", str(ctx.exception))
        self.assertEqual(["exemplo"], sorted(marketplace.read_state()["marketplaces"]))

    def test_add_refuses_missing_or_invalid_index(self):
        repo = self.work / "vazio"
        write_plugin(repo)
        git(repo, "init", "--quiet")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "x")
        with self.assertRaises(ValueError):
            marketplace.add(str(repo))
        (repo / mi.INDEX_NAME).write_text('{"schema": "getbrolls.marketplace_index/1"}', encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "y")
        with self.assertRaises(mi.MarketplaceIndexError):
            marketplace.add(str(repo))
        self.assertFalse(marketplace.state_path().exists())

    def test_url_with_credentials_refused(self):
        for source in (
            "https://user:pass@example.com/idx.git",
            "https://example.com/idx.git?x=1",
            "file:///tmp/idx",
            "ext::sh -c x",
            "http://example.com/idx.git",
        ):
            with self.subTest(source=source), self.no_network(), self.assertRaises(ValueError):
                marketplace.add(source)
        self.assertFalse(marketplace.state_path().exists())

    def test_list_is_sorted_and_offline(self):
        marketplace.add(str(self.index_repo(name="zeta", folder="z")))
        marketplace.add(str(self.index_repo(name="alfa", folder="a")))
        with self.no_network():
            listed = marketplace.listing()
        self.assertEqual(["alfa", "zeta"], [row["name"] for row in listed["marketplaces"]])
        row = listed["marketplaces"][0]
        self.assertTrue(row["allowed"])
        self.assertEqual(1, row["plugins"])
        self.assertEqual(40, len(row["commit"]))
        self.assertIsNone(row["problem"])

    def test_tampered_cache_is_detected(self):
        marketplace.add(str(self.index_repo()))
        cache = marketplace.cache_path("exemplo")
        cache.write_bytes(cache.read_bytes().replace(b"Teste", b"Outro"))
        with self.assertRaises(ValueError) as ctx:
            marketplace.load_index("exemplo")
        self.assertIn("marketplace-update", str(ctx.exception))
        row = marketplace.listing()["marketplaces"][0]
        self.assertIsNone(row["plugins"])
        self.assertIn("sha256", row["problem"])
        cache.unlink()
        with self.assertRaises(ValueError):
            marketplace.load_index("exemplo")
        marketplace.refresh("exemplo")
        self.assertEqual("exemplo", marketplace.load_index("exemplo")[0].name)

    def test_corrupt_marketplaces_json_refuses_mutation(self):
        repo = self.index_repo()
        path = marketplace.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        for garbage in (
            "{nao json",
            '{"schema": "getbrolls.marketplaces/9", "marketplaces": {}}',
            '{"schema": "getbrolls.marketplaces/1", "marketplaces": {"x": {"source": 1}}}',
        ):
            with self.subTest(garbage=garbage):
                path.write_text(garbage, encoding="utf-8")
                with self.assertRaises(ValueError):
                    marketplace.add(str(repo))
                with self.assertRaises(ValueError):
                    marketplace.remove("x")
                self.assertEqual(garbage, path.read_text(encoding="utf-8"))
        self.assertFalse((self.home / "marketplaces").exists())

    def test_remove_keeps_installed_plugins(self):
        marketplace.add(str(self.index_repo()))
        state = loader.read_state()
        state.setdefault("sources", {})["demo"] = {"source": "x", "commit": "a" * 40, "marketplace": "exemplo"}
        loader.write_state(state)
        done = marketplace.remove("exemplo")
        self.assertTrue(done["removed"])
        self.assertEqual(["demo"], done["installed_plugins"])
        self.assertFalse(marketplace.cache_path("exemplo").parent.exists())
        self.assertEqual({}, marketplace.read_state()["marketplaces"])
        self.assertIn("demo", loader.read_state()["sources"])
        with self.assertRaises(ValueError):
            marketplace.remove("exemplo")
        with self.assertRaises(ValueError):
            marketplace.remove("../fora")


class RefreshTests(MarketplaceTestCase):
    def test_update_moves_pin_and_reports_diff(self):
        repo = self.index_repo()
        marketplace.add(str(repo))
        old = head(repo)
        write_plugin(repo / "plugins" / "demo", {**MANIFEST, "version": "0.2.0"})
        write_plugin(repo / "plugins" / "novo", {**MANIFEST, "id": "novo"})
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "0.2.0")
        plugins = head(repo)
        demo = build_entry(repo, plugins)
        novo = {**build_entry(repo, plugins, "novo"), "yanked": True}
        state = loader.read_state()
        state["enabled"]["demo"] = {"sha256": "c" * 64, "version": "0.1.0"}
        state.setdefault("sources", {})["demo"] = {"source": "x", "commit": old, "marketplace": "exemplo"}
        loader.write_state(state)
        new = self.commit_index(repo, "exemplo", [demo, novo])
        done = marketplace.refresh("exemplo")
        self.assertEqual(1, len(done["marketplaces"]))
        result = done["marketplaces"][0]
        self.assertEqual((old, new), (result["from"], result["to"]))
        self.assertTrue(result["changed"])
        self.assertEqual(["novo"], result["diff"]["added"])
        self.assertEqual([], result["diff"]["removed"])
        self.assertEqual([{"id": "demo", "from": "0.1.0", "to": "0.2.0"}], result["diff"]["updated"])
        self.assertEqual(["novo"], result["diff"]["yanked"])
        self.assertEqual(["demo"], result["installed_updates"])
        self.assertEqual(new, marketplace.read_state()["marketplaces"]["exemplo"]["commit"])
        again = marketplace.refresh()
        self.assertFalse(again["marketplaces"][0]["changed"])

    def test_update_with_commit_rolls_back(self):
        repo = self.index_repo()
        first = head(repo)
        marketplace.add(str(repo))
        self.commit_index(repo, "exemplo", [])
        marketplace.refresh("exemplo")
        self.assertEqual([], marketplace.load_index("exemplo")[1]["plugins"])
        done = marketplace.refresh("exemplo", commit=first)
        self.assertEqual(first, done["marketplaces"][0]["to"])
        self.assertEqual(["demo"], done["marketplaces"][0]["diff"]["added"])
        self.assertEqual(first, marketplace.read_state()["marketplaces"]["exemplo"]["commit"])
        with self.assertRaises(UsageError):
            marketplace.refresh(None, commit=first)
        with self.assertRaises(ValueError):
            marketplace.refresh("exemplo", commit="abc")

    def test_update_refuses_an_index_that_renamed_itself(self):
        repo = self.index_repo()
        marketplace.add(str(repo))
        self.commit_index(repo, "outro-nome", [])
        with self.assertRaises(ValueError) as ctx:
            marketplace.refresh("exemplo")
        self.assertIn("outro-nome", str(ctx.exception))
        self.assertNotEqual(head(repo), marketplace.read_state()["marketplaces"]["exemplo"]["commit"])


class PolicyTests(MarketplaceTestCase):
    def test_policy_hides_restricted_marketplace(self):
        marketplace.add(str(self.index_repo(name="alfa", folder="a")))
        marketplace.add(str(self.index_repo(name="beta", folder="b")))
        marketplace.set_policy(frozenset({"alfa"}))
        self.assertTrue(marketplace.allowed("alfa"))
        self.assertFalse(marketplace.allowed("beta"))
        self.assertEqual(["alfa"], [pin.name for pin, _ in marketplace.pinned_indexes()])
        rows = {row["name"]: row["allowed"] for row in marketplace.listing()["marketplaces"]}
        self.assertEqual({"alfa": True, "beta": False}, rows)
        with self.assertRaises(ValueError):
            marketplace.refresh("beta")
        skipped = marketplace.refresh()
        self.assertEqual(
            {"alfa": False, "beta": True}, {r["name"]: r.get("skipped", False) for r in skipped["marketplaces"]}
        )
        marketplace.set_policy(frozenset())
        self.assertEqual([], marketplace.pinned_indexes())
        with self.assertRaises(ValueError) as ctx:
            marketplace.add(str(self.index_repo(name="gama", folder="g")))
        self.assertIn("perfil", str(ctx.exception))
        marketplace.set_policy(None)
        self.assertEqual(["alfa", "beta"], [pin.name for pin, _ in marketplace.pinned_indexes()])

    def test_default_policy_reads_the_profile_ceiling(self):
        marketplace.add(str(self.index_repo()))
        with patch("getbrolls.profile.marketplace_ceiling", return_value=frozenset({"outro"})):
            self.assertFalse(marketplace.allowed("exemplo"))
            self.assertEqual([], marketplace.pinned_indexes())
        with patch("getbrolls.profile.marketplace_ceiling", return_value=None):
            self.assertTrue(marketplace.allowed("exemplo"))


class CliTests(MarketplaceTestCase):
    def test_cli_add_list_update_remove(self):
        repo = self.index_repo()
        added = run_cli("plugins", "--action", "marketplace-add", "--source", str(repo), env=self.env())
        self.assertTrue(added["added"])
        listed = run_cli("plugins", "--action", "marketplace-list", env=self.env())
        self.assertEqual(["exemplo"], [row["name"] for row in listed["marketplaces"]])
        updated = run_cli("plugins", "--action", "marketplace-update", "--marketplace", "exemplo", env=self.env())
        self.assertFalse(updated["marketplaces"][0]["changed"])
        removed = run_cli("plugins", "--action", "marketplace-remove", "--marketplace", "exemplo", env=self.env())
        self.assertTrue(removed["removed"])

    def test_marketplace_list_is_read_only(self):
        self.assertIn(("plugins", "marketplace-list"), runtime.READ_ONLY_ACTIONS)
        self.assertNotIn(("plugins", "marketplace-add"), runtime.READ_ONLY_ACTIONS)

    def test_cli_flag_misuse_is_a_usage_error(self):
        for args in (
            ("--action", "marketplace-add"),
            ("--action", "marketplace-add", "--source", "x", "--subdir", "a"),
            ("--action", "marketplace-remove"),
            ("--action", "marketplace-list", "--commit", "a" * 40),
            ("--action", "list", "--marketplace", "exemplo"),
            ("--action", "install", "--source", "x", "--marketplace", "exemplo"),
            ("--action", "marketplace-update", "--ref", "main"),
            ("--action", "marketplace-update", "--commit", "a" * 40),
        ):
            with self.subTest(args=args):
                run_cli("plugins", *args, expect=2, env=self.env())


if __name__ == "__main__":
    unittest.main()
