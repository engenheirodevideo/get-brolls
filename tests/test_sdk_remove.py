"""`plugins --action remove`: dois passos, estado limpo, `plugin-data` preservado."""

import json
import os
import time
import unittest
import uuid

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_install import InstallTestCase, write_plugin

from getbrolls.sdk import install as install_mod
from getbrolls.sdk import loader, remove


class RemoveTests(InstallTestCase):
    def installed(self):
        source = write_plugin(self.work / "demo_src")
        preview = install_mod.install(str(source), confirm=False)
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        return self.home / "plugins" / "demo"

    def test_remove_is_two_steps_and_clears_state_but_keeps_plugin_data(self):
        folder = self.installed()
        loader.disable("demo")
        data = self.home / "plugin-data" / "demo"
        data.mkdir(parents=True)
        (data / "cache.json").write_text("{}", encoding="utf-8")

        preview = run_cli("plugins", "--action", "remove", "--id", "demo", env=self.env())
        self.assertFalse(preview["removed"])
        self.assertEqual({"id": "demo", "folder": "demo", "version": "0.1.0", "status": "disabled"}, preview["plugin"])
        self.assertEqual((False, True), (preview["state"]["enabled"], preview["state"]["last_pin"]))
        self.assertEqual(str((self.work / "demo_src").resolve()), preview["state"]["source"]["source"])
        self.assertEqual(str(data), preview["kept"]["plugin_data"])
        self.assertIn("--yes", preview["note"])
        self.assertTrue(folder.is_dir())

        done = run_cli("plugins", "--action", "remove", "--id", "demo", "--yes", env=self.env())
        self.assertTrue(done["removed"])
        self.assertFalse(folder.exists())
        state = self.state()
        for key in ("enabled", "last_pins", "sources"):
            self.assertNotIn("demo", state.get(key, {}))
        self.assertTrue((data / "cache.json").exists())
        self.assertEqual([], [p.name for p in (self.home / "plugins").iterdir()])

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_remove_of_symlinked_folder_unlinks_without_touching_target(self):
        target = write_plugin(self.work / "fora" / "demo")
        root = self.home / "plugins"
        root.mkdir(parents=True)
        (root / "demo").symlink_to(target, target_is_directory=True)
        remove.remove("demo", confirm=True)
        self.assertFalse(os.path.lexists(root / "demo"))
        self.assertTrue((target / "plugin.py").exists())

    def test_remove_refuses_bad_id_and_corrupt_plugins_json(self):
        for bad in ("../x", "Demo", "", "a/b"):
            with self.subTest(plugin_id=bad), self.assertRaises(ValueError):
                remove.remove(bad, confirm=True)
        folder = self.installed()
        (self.home / "plugins.json").write_text("{nao é json", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            remove.remove("demo", confirm=True)
        self.assertIn("plugins.json", str(ctx.exception))
        self.assertTrue(folder.is_dir())

    def test_orphan_state_entry_can_be_removed(self):
        state = {"enabled": {}, "sources": {"demo": {"source": "/sumiu", "commit": None}}}
        (self.home / "plugins.json").write_text(json.dumps(state), encoding="utf-8")
        preview = remove.remove("demo", confirm=False)
        self.assertIsNone(preview["plugin"])
        self.assertEqual({"source": "/sumiu", "commit": None}, preview["state"]["source"])
        remove.remove("demo", confirm=True)
        self.assertNotIn("demo", self.state().get("sources", {}))
        with self.assertRaises(ValueError) as ctx:
            remove.remove("demo", confirm=False)
        self.assertIn("não encontrado", str(ctx.exception))

    def test_leftover_removed_dir_is_swept_not_restored(self):
        root = self.home / "plugins"
        old = int(time.time()) - install_mod.STALE_STAGING_MAX_AGE_S - 10
        leftover = write_plugin(root / f".removed-{old}-{uuid.uuid4().hex}")
        young = write_plugin(root / f".removed-{int(time.time())}-{uuid.uuid4().hex}")
        install_mod._sweep_stale_staging()
        self.assertFalse(leftover.exists())
        self.assertTrue(young.exists())
        self.assertFalse((root / "demo").exists())

    def test_removed_plugin_no_longer_loads_or_lists(self):
        self.installed()
        self.assertEqual(["demo"], [row["id"] for row in loader.inventory()])
        remove.remove("demo", confirm=True)
        self.assertEqual([], loader.inventory())
        listed = run_cli("plugins", "--action", "list", env=self.env())
        self.assertEqual([], listed["plugins"])
        err = run_cli("plugins", "--action", "remove", env=self.env(), expect=1)
        self.assertIn("--id", err["error"])
        err = run_cli("plugins", "--action", "remove", "--id", "demo", "--expect", "x", env=self.env(), expect=2)
        self.assertEqual("USAGE_ERROR", err["error_code"])


if __name__ == "__main__":
    unittest.main()
