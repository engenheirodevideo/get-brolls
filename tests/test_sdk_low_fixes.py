"""Ajustes pequenos: a prévia do enable lista arquivos, o valor do settings.json nunca
aparece em mensagem e os textos apontam o conserto certo."""

import json
import os
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import brief, providers
from getbrolls.commands import doctor_plugin_problems
from getbrolls.http import ProviderError
from getbrolls.sdk import loader
from getbrolls.sdk.manifest import read_manifest
from getbrolls.sdk.registry import get_registry

CONFIG_SECRET = "cfg_secret_ABCDEF123"

CONFIG_LEAK_PLUGIN = """
from getbrolls.sdk import PluginError
from getbrolls.sdk.contracts import ProviderCapabilities


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True)

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        token = self.api.config()["token"]
        raise PluginError(f"A API recusou o token {token}; confira a conta.")

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


def register(api):
    api.provider(Fonte(api))
"""


class SmallFixesTests(LoaderTestCase):
    def test_settings_json_value_never_reaches_the_message(self):
        self.install({**MANIFEST, "contributes": {"providers": ["demo"]}}, CONFIG_LEAK_PLUGIN)
        data = self.home / "plugin-data" / "demo"
        data.mkdir(parents=True)
        (data / "settings.json").write_text(json.dumps({"token": CONFIG_SECRET}), encoding="utf-8")
        pin_plugins("demo")
        with patch.dict(os.environ, {}), self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        self.assertIn("[REDACTED]", str(caught.exception))
        self.assertNotIn(CONFIG_SECRET, str(caught.exception))

    def test_enable_preview_lists_the_files_it_pins(self):
        self.install()
        preview = loader.enable("demo", confirm=False)
        self.assertEqual(
            {"count": 2, "names": ["getbrolls-plugin.json", "plugin.py"], "truncated": False},
            preview["plugin"]["files"],
        )

    def test_import_failure_is_not_blamed_on_register(self):
        self.install(code="import nao_existe_modulo_xyz\n")
        pin_plugins("demo")
        reason = get_registry().plugins["demo"]["reason"]
        self.assertIn("no import ou no register()", reason)

    def test_wrong_expect_says_it_may_be_a_typo(self):
        with self.assertRaises(ValueError) as caught:
            loader.check_expect("deadbeef", "0" * 64)
        self.assertIn("copiado errado", str(caught.exception))

    def test_brief_names_enable_for_a_disabled_plugin_source(self):
        self.install()
        hint = brief._plugin_source_hint("demo")
        self.assertIn("plugins --action enable --id demo", hint)

    def test_doctor_summary_does_not_point_back_to_the_preload_list(self):
        line = doctor_plugin_problems([{"id": "demo", "status": "failed"}]) or ""
        self.assertIn("plugins[]", line)
        self.assertNotIn("plugins --action list", line)


class ResolvedRootBreadthTests(unittest.TestCase):
    """Raiz de `permissions.paths` que, resolvida, é a pasta pessoal, uma pasta
    acima dela ou a raiz do disco não vale — mesmo passando pela checagem de texto."""

    def api(self, paths):
        from getbrolls.sdk.api import PluginApi
        from getbrolls.sdk.registry import Registry

        manifest = {**MANIFEST, "permissions": {"network": [], "env": [], "paths": paths}}
        return PluginApi(manifest, Registry())

    def test_home_its_ancestors_and_links_to_them_are_ignored(self):
        import shutil
        import tempfile
        from pathlib import Path

        base = Path(tempfile.mkdtemp(prefix="gb-b09-")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        home = base / "casa" / "pessoa"
        (home / "Filmes").mkdir(parents=True)
        links = []
        if os.name != "nt":
            (base / "atalho").symlink_to(base / "casa", target_is_directory=True)
            links.append(str(base / "atalho"))
        with (
            patch.dict(os.environ, {"HOME": str(home), "USERPROFILE": str(home)}),
            self.assertLogs("getbrolls.sdk", level="WARNING"),
        ):
            roots = self.api([str(base / "casa"), str(home), *links, str(home / "Filmes")])._roots()
        self.assertEqual([home / "Filmes"], roots)


class EnablePermissionsDiffTests(LoaderTestCase):
    """O `enable` de um plugin cujo conteúdo mudou mostra de/para das permissões."""

    def test_changed_permissions_show_from_and_to(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        widened = {
            **MANIFEST,
            "permissions": {"network": ["demo.example", "outro.example"], "env": ["DEMO_TOKEN"], "paths": ["~/Movies"]},
        }
        (folder / "getbrolls-plugin.json").write_text(json.dumps(widened), encoding="utf-8")
        diff = loader.enable("demo", confirm=False)["diff"]
        self.assertEqual(MANIFEST["permissions"]["network"], diff["permissions"]["from"]["network"])
        self.assertEqual(["demo.example", "outro.example"], diff["permissions"]["to"]["network"])
        self.assertEqual(["~/Movies"], diff["permissions"]["to"]["paths"])
        self.assertEqual(["DEMO_TOKEN"], diff["permissions"]["to"]["env"])


class PreviewAndCheckMessagesTests(LoaderTestCase):
    def test_reference_note_tells_a_failed_download_from_a_missing_thumbnail(self):
        from getbrolls.commands import _plugin_reference_note

        c = {"provider": "demo", "preview": {"poster_path": None, "poster_url": "https://demo.example/p.jpg"}}
        self.assertIn("não pôde ser baixada", _plugin_reference_note(c))
        c["preview"]["poster_url"] = None
        self.assertIn("não mandou miniatura", _plugin_reference_note(c))
        self.assertEqual("", _plugin_reference_note({"provider": "nasa", "preview": {}}))

    def test_mount_point_roots_are_ignored_and_shown_in_the_previews(self):
        import tempfile
        from pathlib import Path

        from getbrolls.sdk import api as sdk_api

        mount = Path(tempfile.mkdtemp(prefix="gb-mount-")).resolve()
        self.addCleanup(mount.rmdir)
        folder = self.install({**MANIFEST, "permissions": {"network": [], "env": [], "paths": [str(mount)]}})
        real_ismount = os.path.ismount
        with patch.object(sdk_api.os.path, "ismount", lambda p: Path(p) == mount or real_ismount(p)):
            self.assertEqual([str(mount)], sdk_api.ignored_paths(read_manifest(folder)))
            preview = loader.enable("demo", confirm=False)["plugin"]
            checked = loader.trial_load(folder)
        for out in (preview, checked):
            self.assertTrue(any("ponto de montagem" in w and str(mount) in w for w in out["warnings"]))

    def test_check_refuses_what_install_would_refuse(self):
        folder = self.install()
        (folder / "__pycache__").mkdir()
        (folder / "__pycache__" / "plugin.cpython-314.pyc").write_bytes(b"x")
        with self.assertRaises(ValueError) as caught:
            loader.trial_load(folder)
        self.assertIn("bytecode", str(caught.exception))
        self.assertIn("Apague a pasta __pycache__", str(caught.exception))
        self.assertIn("isso basta — e rode o check de novo", str(caught.exception))

    def test_x_hint_for_a_plugin_left_out_by_gb_plugins(self):
        from getbrolls.sdk.plugin_commands import _missing

        def message(row):
            registry = type("R", (), {"plugins": {"demo": row}})()
            return _missing(registry, "demo", "contar")

        left_out = message({"id": "demo", "status": "disabled", "reason": loader.GB_PLUGINS_REASON})
        self.assertIn("Inclua demo em GB_PLUGINS", left_out)
        suspended = message(
            {"id": "demo", "status": "suspended", "reason": "O conteúdo do plugin mudou desde o enable; revise."}
        )
        self.assertNotIn("..", suspended)
        self.assertIn("plugins --action list", suspended)


if __name__ == "__main__":
    unittest.main()
