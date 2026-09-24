"""Estado e configuração do plugin em `$GB_HOME/plugin-data/<id>/`."""

import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
