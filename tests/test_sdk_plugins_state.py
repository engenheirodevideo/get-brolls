"""Estado dos plugins e marketplaces sob concorrência, cache ruim e falha no meio.

- toda ação de `plugins` que grava segura `$GB_HOME/.plugins.lock` (`LOCKED` se outro
  processo já segura) e as trocas atômicas usam um temporário com nome único;
- um cache de índice adulterado não derruba o `search` dos outros marketplaces;
- `marketplace-update` sem nome aplica o que der e relata o erro de cada um;
- `marketplace-update` mostra a troca de commit/sha de uma entrada e só volta o índice
  para um commit que não descende do fixado com `--allow-rollback`;
- `remove` que não consegue apagar tudo diz o que ficou.
"""

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT
from test_sdk_install import git, head, write_plugin
from test_sdk_loader import MANIFEST
from test_sdk_marketplace_client import MarketplaceTestCase, build_entry

from getbrolls.errors import UsageError
from getbrolls.sdk import install as install_mod
from getbrolls.sdk import loader, marketplace, remove

HOLD_LOCK = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from getbrolls import runtime
    with runtime.exclusive_lock(sys.argv[2], "ocupado"):
        print("ok", flush=True)
        time.sleep(30)
    """
)


class LockTests(MarketplaceTestCase):
    def hold_lock(self):
        lock = self.home / ".plugins.lock"
        self.home.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(  # pylint: disable=consider-using-with  # vive até o fim do teste
            [sys.executable, "-c", HOLD_LOCK, str(ROOT / "scripts"), str(lock)], stdout=subprocess.PIPE, text=True
        )
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        assert holder.stdout is not None
        self.assertEqual("ok", holder.stdout.readline().strip())
        return holder

    def test_writing_actions_are_locked_and_reading_ones_are_not(self):
        source = write_plugin(self.work / "demo_src")
        repo = self.index_repo()
        self.hold_lock()
        for args in (
            ("--action", "install", "--source", str(source)),
            ("--action", "enable", "--id", "demo"),
            ("--action", "disable", "--id", "demo"),
            ("--action", "remove", "--id", "demo"),
            ("--action", "marketplace-add", "--source", str(repo)),
            ("--action", "marketplace-update"),
            ("--action", "marketplace-remove", "--marketplace", "exemplo"),
        ):
            with self.subTest(args=args):
                err = run_cli("plugins", *args, expect=1, env=self.env())
                self.assertEqual("LOCKED", err["error_code"])
        run_cli("plugins", "--action", "list", env=self.env())
        run_cli("plugins", "--action", "marketplace-list", env=self.env())
        run_cli("plugins", "--action", "search", "--query", "x", env=self.env())

    def test_atomic_writes_use_a_unique_temporary_name(self):
        seen = []
        original = Path.replace

        def spy(path, target):
            seen.append(Path(path).name)
            return original(path, target)

        with patch.object(Path, "replace", spy):
            marketplace.add(str(self.index_repo()))
            loader.write_state({"enabled": {}})
        temps = [name for name in seen if name.endswith(".tmp")]
        self.assertTrue(temps)
        for name in temps:
            self.assertNotIn(name, ("marketplaces.json.tmp", "plugins.json.tmp", "getbrolls-marketplace.json.tmp"))
        self.assertEqual(len(temps), len(set(temps)))


class SearchAndRefreshTests(MarketplaceTestCase):
    def test_one_bad_cache_does_not_break_search(self):
        marketplace.add(str(self.index_repo(name="alfa", folder="a")))
        marketplace.add(str(self.index_repo(name="beta", folder="b")))
        cache = marketplace.cache_path("alfa")
        cache.write_bytes(cache.read_bytes() + b" ")
        found = marketplace.search("demo")
        self.assertEqual([("demo", "beta")], [(row["id"], row["marketplace"]) for row in found["results"]])
        self.assertEqual(["beta"], found["marketplaces"])
        self.assertEqual(["alfa"], [row["marketplace"] for row in found["problems"]])
        self.assertIn("sha256", found["problems"][0]["problem"])
        with self.assertRaises(ValueError):
            marketplace.search("demo", marketplace="alfa")

    def test_update_all_applies_what_works_and_reports_each_error(self):
        alfa = self.index_repo(name="alfa", folder="a")
        beta = self.index_repo(name="beta", folder="b")
        marketplace.add(str(alfa))
        marketplace.add(str(beta))
        self.commit_index(alfa, "outro-nome", [])
        new_beta = self.commit_index(beta, "beta", [])
        done = marketplace.refresh()
        rows = {row["name"]: row for row in done["marketplaces"]}
        self.assertIn("outro-nome", rows["alfa"]["error"])
        self.assertFalse(rows["alfa"]["changed"])
        self.assertTrue(rows["beta"]["changed"])
        self.assertEqual(["alfa"], done["failed"])
        self.assertEqual(new_beta, marketplace.read_state()["marketplaces"]["beta"]["commit"])
        with self.assertRaises(ValueError):
            marketplace.refresh("alfa")

    def test_update_shows_commit_and_sha_changes_of_an_entry(self):
        repo = self.index_repo()
        marketplace.add(str(repo))
        old = marketplace.load_index("exemplo")[1]["plugins"][0]
        (repo / "plugins" / "demo" / "LEIAME.md").write_text("novo\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "mesma versão, outro conteúdo")
        entry = build_entry(repo, head(repo))
        self.commit_index(repo, "exemplo", [entry])
        diff = marketplace.refresh("exemplo")["marketplaces"][0]["diff"]
        self.assertEqual(
            [
                {
                    "id": "demo",
                    "from": {"commit": old["source"]["commit"], "content_sha256": old["content_sha256"]},
                    "to": {"commit": entry["source"]["commit"], "content_sha256": entry["content_sha256"]},
                }
            ],
            diff["changed"],
        )

    def test_rollback_to_an_older_index_commit_needs_allow_rollback(self):
        repo = self.index_repo()
        first = head(repo)
        marketplace.add(str(repo))
        self.commit_index(repo, "exemplo", [])
        marketplace.refresh("exemplo")
        with self.assertRaises(UsageError) as ctx:
            marketplace.refresh("exemplo", commit=first)
        self.assertIn("--allow-rollback", str(ctx.exception))
        self.assertNotEqual(first, marketplace.read_state()["marketplaces"]["exemplo"]["commit"])
        err = run_cli(
            "plugins", "--action", "marketplace-update", "--marketplace", "exemplo", "--commit", first,
            expect=2, env=self.env(),
        )  # fmt: skip
        self.assertIn("--allow-rollback", err["error"])
        done = run_cli(
            "plugins", "--action", "marketplace-update", "--marketplace", "exemplo", "--commit", first,
            "--allow-rollback", env=self.env(),
        )  # fmt: skip
        self.assertTrue(done["marketplaces"][0]["rollback"])
        self.assertEqual(first, marketplace.read_state()["marketplaces"]["exemplo"]["commit"])
        run_cli("plugins", "--action", "list", "--allow-rollback", expect=2, env=self.env())

    def test_rewritten_history_counts_as_rollback(self):
        repo = self.index_repo()
        marketplace.add(str(repo))
        git(repo, "commit", "--quiet", "--amend", "-m", "história reescrita")
        with self.assertRaises(UsageError):
            marketplace.refresh("exemplo")
        self.assertTrue(marketplace.refresh("exemplo", allow_rollback=True)["marketplaces"][0]["rollback"])


class RemoveLeftoverTests(MarketplaceTestCase):
    def test_remove_that_cannot_delete_everything_says_what_was_left(self):
        source = write_plugin(self.work / "demo_src", {**MANIFEST})
        install_mod.install(str(source), confirm=True, expect=install_mod.content_digest(str(source))[0])
        with patch.object(remove, "force_rmtree"):
            done = remove.remove("demo", confirm=True)
        self.assertTrue(done["removed"])
        self.assertIsNotNone(done["leftover"])
        self.assertTrue(done["leftover"].startswith(".removed-"))
        self.assertIn("sobrou", done["note"])
        self.assertNotIn("demo", loader.read_state()["enabled"])
        self.assertFalse((loader.plugins_root() / "demo").exists())

    def test_remove_whose_move_fails_changes_nothing(self):
        source = write_plugin(self.work / "demo_src", {**MANIFEST})
        install_mod.install(str(source), confirm=True, expect=install_mod.content_digest(str(source))[0])
        with patch.object(Path, "replace", side_effect=PermissionError("x")), self.assertRaises(ValueError) as ctx:
            remove.remove("demo", confirm=True)
        self.assertIn("nada foi mudado", str(ctx.exception))
        self.assertIn("demo", loader.read_state()["enabled"])
        self.assertTrue((loader.plugins_root() / "demo").is_dir())
        self.assertEqual(json.loads((self.home / "plugins.json").read_text(encoding="utf-8")), loader.read_state())


if __name__ == "__main__":
    unittest.main()
