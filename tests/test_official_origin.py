"""Origem oficial: a ponte que vem no pacote, confiada pelo pin do core e carregada só sob demanda."""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.errors import DataRootError, PrerequisiteError
from getbrolls.sdk import bridges, loader, official
from getbrolls.sdk.api import PluginApi
from getbrolls.sdk.manifest import read_manifest
from getbrolls.sdk.registry import Registry

OFFICIAL = ROOT / "plugins_official"


class OfficialOriginTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.shipped = Path(tempfile.mkdtemp(prefix="gb-official-"))
        self.addCleanup(shutil.rmtree, self.shipped, ignore_errors=True)
        shutil.copytree(OFFICIAL / "hyperframes", self.shipped / "hyperframes")
        patcher = patch.object(official, "official_root", return_value=self.shipped)
        patcher.start()
        self.addCleanup(patcher.stop)

    def row(self):
        (row, _folder, _manifest) = official.entries()[0]
        return row

    def test_the_shipped_bridge_is_enabled_without_any_enable(self):
        row = self.row()
        self.assertEqual(("hyperframes", "enabled", True), (row["id"], row["status"], row["official"]))
        self.assertFalse((self.home / "plugins.json").exists())
        registry = Registry()
        result = bridges.run_package(registry, "hyperframes", {"id": "001"})
        self.assertEqual(["hyperframes/LEIA-ME.md"], list(result["files"]))
        self.assertEqual("enabled", registry.plugins["hyperframes"]["status"])

    def test_a_tampered_official_file_is_invalid_and_never_runs(self):
        tampers = {
            "byte": lambda folder: (folder / "README.md").write_text("mudou", encoding="utf-8"),
            "extra file": lambda folder: (folder / "extra.py").write_text("x = 1\n", encoding="utf-8"),
            "loose pyc": lambda folder: (folder / "plugin.pyc").write_bytes(b"x"),
            "nested cache": lambda folder: (folder / "sub" / "__pycache__").mkdir(parents=True),
            "nested vcs": lambda folder: (folder / "sub" / ".git").mkdir(parents=True),
            "broken manifest": lambda folder: (folder / "getbrolls-plugin.json").write_text("{", encoding="utf-8"),
            "other bridge": lambda folder: (folder / "getbrolls-plugin.json").write_text(
                (folder / "getbrolls-plugin.json").read_text(encoding="utf-8").replace('["hyperframes"]', "[]"),
                encoding="utf-8",
            ),
            "missing folder": shutil.rmtree,
        }
        if os.name != "nt":
            tampers["link"] = lambda folder: (folder / "fora").symlink_to(self.home)
        for name, tamper in tampers.items():
            with self.subTest(tamper=name):
                shutil.rmtree(self.shipped / "hyperframes", ignore_errors=True)
                shutil.copytree(OFFICIAL / "hyperframes", self.shipped / "hyperframes")
                tamper(self.shipped / "hyperframes")
                row = self.row()
                self.assertEqual("invalid", row["status"])
                self.assertIn("Reinstale", row["reason"])
                registry = Registry()
                with patch.object(loader, "register_plugin") as ran, self.assertRaises(PrerequisiteError):
                    bridges.run_package(registry, "hyperframes", {})
                ran.assert_not_called()
                self.assertIsNone(registry.environment("hyperframes"))

    def test_the_installer_cache_keeps_the_bridge_enabled(self):
        cache = self.shipped / "hyperframes" / "__pycache__"
        cache.mkdir()
        (cache / "plugin.cpython-311.pyc").write_bytes(b"\x00")
        self.assertEqual("enabled", self.row()["status"])

    def test_a_change_between_the_listing_and_the_exec_never_runs(self):
        real = official.check_folder
        calls = []

        def second_call_sees_a_change(ident, folder):
            calls.append(ident)
            return ("um arquivo mudou", None) if len(calls) > 1 else real(ident, folder)

        registry = Registry()
        with patch.object(official, "check_folder", side_effect=second_call_sees_a_change):
            row = official.load(registry, "hyperframes")
        self.assertEqual("invalid", row["status"])
        self.assertIsNone(registry.environment("hyperframes"))

    def test_the_session_selection_can_turn_it_off(self):
        with patch.dict(os.environ, {"GB_PLUGINS": "off"}):
            row = self.row()
            self.assertEqual(("disabled", loader.GB_PLUGINS_REASON), (row["status"], row["reason"]))
            with self.assertRaises(PrerequisiteError) as caught:
                bridges.run_package(Registry(), "hyperframes", {})
        self.assertIn("GB_PLUGINS", str(caught.exception))

    def test_plugins_json_state_for_an_official_id_is_ignored(self):
        state = {"enabled": {"hyperframes": {"version": "0.1.0", "sha256": "0" * 64}}, "last_pins": {}}
        (self.home / "plugins.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual("enabled", self.row()["status"])

    def test_missing_package_data_is_invalid_with_the_reinstall_phrase(self):
        with patch.object(official, "official_root", side_effect=DataRootError("sem dados")):
            row = self.row()
        self.assertEqual("invalid", row["status"])
        self.assertIn("faltam os dados do pacote", row["reason"])

    def test_an_unreadable_or_incompatible_bridge_never_runs(self):
        with patch.object(official, "check_folder", side_effect=PermissionError):
            self.assertIn("não consegui ler: PermissionError", self.row()["reason"])
        with patch.object(official, "compatibility_problem", return_value="exige outro getbrolls"):
            self.assertEqual(("incompatible", "exige outro getbrolls"), (self.row()["status"], self.row()["reason"]))

    def test_a_bridge_that_fails_to_register_is_failed_and_leaves_nothing(self):
        registry = Registry()
        with patch.object(loader, "register_plugin", side_effect=RuntimeError("quebrou")):
            row = official.load(registry, "hyperframes")
        self.assertEqual("failed", row["status"])
        self.assertIsNone(registry.environment("hyperframes"))
        self.assertEqual("failed", registry.plugins["hyperframes"]["status"])

    def test_the_official_data_folder_is_its_own(self):
        manifest = read_manifest(self.shipped / "hyperframes", official=True)
        folder = PluginApi(manifest, Registry(), official=True).data_dir
        self.assertEqual(self.home / "plugin-data" / "_official" / "hyperframes", folder)


class ImpostorTests(LoaderTestCase):
    def test_a_user_copy_with_an_official_id_never_loads_and_says_how_to_remove_it(self):
        self.install({**MANIFEST, "id": "hyperframes"})
        self.install()
        rows = {row["id"]: row for row in loader.inventory()}
        self.assertEqual("invalid", rows["hyperframes"]["status"])
        self.assertIn("plugins --action remove --id hyperframes", rows["hyperframes"]["reason"])
        self.assertEqual("disabled", rows["demo"]["status"])
        self.assertEqual("enabled", official.entries()[0][0]["status"])

    def test_remove_takes_the_user_copy_and_leaves_the_official_one(self):
        folder = self.install({**MANIFEST, "id": "hyperframes"})
        (self.home / "plugin-data" / "hyperframes").mkdir(parents=True)
        done = run_cli("plugins", "--action", "remove", "--id", "hyperframes", "--yes", env={"GB_HOME": str(self.home)})
        self.assertTrue(done["removed"])
        self.assertFalse(folder.exists())
        self.assertTrue((OFFICIAL / "hyperframes" / "plugin.py").is_file())

    def test_plugins_list_shows_the_official_bridge_apart(self):
        done = run_cli("plugins", "--action", "list", env={"GB_HOME": str(self.home)})
        self.assertEqual([], done["plugins"])
        self.assertEqual(
            [("hyperframes", True, "enabled")], [(r["id"], r["official"], r["status"]) for r in done["official"]]
        )
