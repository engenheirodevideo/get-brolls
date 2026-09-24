"""Estado e configuração do plugin em `$GB_HOME/plugin-data/<id>/`."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from test_sdk_loader import LoaderTestCase

from getbrolls.sdk import loader
from getbrolls.sdk.api import PluginApi
from getbrolls.sdk.manifest import read_manifest
from getbrolls.sdk.registry import Registry


class PluginDataTests(LoaderTestCase):
    def api(self):
        return PluginApi(read_manifest(self.install()), Registry())

    def test_data_dir_is_private_and_outside_the_plugin_folder(self):
        api = self.api()
        folder = api.data_dir
        self.assertEqual(self.home / "plugin-data" / "demo", folder)
        self.assertTrue(folder.is_dir())
        if os.name != "nt":
            self.assertEqual(0o700, folder.stat().st_mode & 0o777)

    def test_writing_state_does_not_suspend_the_plugin(self):
        self.install()
        loader.enable("demo", confirm=True)
        (self.api().data_dir / "cursor.json").write_text("{}", encoding="utf-8")
        self.assertEqual("enabled", loader.inventory()[0]["status"])

    @unittest.skipIf(os.name == "nt", "link simbólico pede privilégio extra no Windows")
    def test_data_dir_refuses_a_symlink_planted_at_plugin_folder(self):
        api = self.api()
        target = Path(tempfile.mkdtemp(prefix="gb-plugin-data-target-"))
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        target.chmod(0o755)
        (self.home / "plugin-data").mkdir(parents=True, exist_ok=True)
        (self.home / "plugin-data" / "demo").symlink_to(target)
        with self.assertRaises(ValueError) as caught:
            _ = api.data_dir
        self.assertIn("plugin-data/demo", str(caught.exception))
        self.assertNotIn(str(self.home), str(caught.exception))
        self.assertEqual(0o755, target.stat().st_mode & 0o777)

    @unittest.skipIf(os.name == "nt", "link simbólico pede privilégio extra no Windows")
    def test_data_dir_refuses_when_plugin_data_root_is_a_symlink(self):
        api = self.api()
        target = Path(tempfile.mkdtemp(prefix="gb-plugin-data-root-"))
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        target.chmod(0o755)
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "plugin-data").symlink_to(target)
        with self.assertRaises(ValueError) as caught:
            _ = api.data_dir
        self.assertIn("plugin-data/demo", str(caught.exception))
        self.assertNotIn(str(self.home), str(caught.exception))
        self.assertEqual(0o755, target.stat().st_mode & 0o777)

    def test_config_reads_settings_json(self):
        api = self.api()
        self.assertEqual({}, api.config())
        (api.data_dir / "settings.json").write_text('{"pasta": "/Volumes/NAS", "limite": 5}', encoding="utf-8")
        self.assertEqual({"pasta": "/Volumes/NAS", "limite": 5}, api.config())

    def test_invalid_settings_are_a_clear_error(self):
        api = self.api()
        for content in ("{", "[1, 2]", b"\xff\xfe"):
            with self.subTest(content=content):
                path = api.data_dir / "settings.json"
                if isinstance(content, bytes):
                    path.write_bytes(content)
                else:
                    path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError) as caught:
                    api.config()
                self.assertIn("plugin-data/demo/settings.json", str(caught.exception))
                self.assertNotIn(str(self.home), str(caught.exception))

    def test_config_wraps_a_directory_named_settings_json(self):
        api = self.api()
        (api.data_dir / "settings.json").mkdir()
        with self.assertRaises(ValueError) as caught:
            api.config()
        self.assertIn("plugin-data/demo/settings.json", str(caught.exception))
        self.assertNotIn(str(self.home), str(caught.exception))

    @unittest.skipIf(
        os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        "permissão de leitura não vale para root nem para Windows",
    )
    def test_config_wraps_an_unreadable_settings_file(self):
        api = self.api()
        path = api.data_dir / "settings.json"
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o000)
        self.addCleanup(path.chmod, 0o600)
        with self.assertRaises(ValueError) as caught:
            api.config()
        self.assertIn("plugin-data/demo/settings.json", str(caught.exception))
        self.assertNotIn(str(self.home), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
