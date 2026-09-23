"""`gb plugins`: inventário sem executar código, enable em dois passos, check de pasta."""

import json
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from test_sdk_loader import MANIFEST, LoaderTestCase


class PluginsCommandTests(LoaderTestCase):
    def env(self):
        return {"GB_HOME": str(self.home)}

    def test_list_shows_disabled_plugin_without_running_it(self):
        self.install(code="raise SystemExit('não devia rodar')\n")
        out = run_cli("plugins", "--action", "list", env=self.env())
        self.assertEqual([("demo", "disabled")], [(p["id"], p["status"]) for p in out["plugins"]])

    def test_enable_needs_yes_then_provider_shows_up(self):
        self.install()
        preview = run_cli("plugins", "--action", "enable", "--id", "demo", env=self.env())
        self.assertFalse(preview["enabled"])
        done = run_cli("plugins", "--action", "enable", "--id", "demo", "--yes", env=self.env())
        self.assertTrue(done["enabled"])
        providers = run_cli("providers", env=self.env())
        self.assertEqual("demo", providers["demo"]["plugin"])
        doctor = run_cli("doctor", env=self.env())
        self.assertEqual("enabled", doctor["plugins"][0]["status"])

    def test_check_reports_collision_without_enabling(self):
        folder = self.install({**MANIFEST, "id": "demo"})
        out = run_cli("plugins", "--action", "check", "--path", folder, env=self.env())
        self.assertTrue(out["ok"])
        self.assertFalse((self.home / "plugins.json").exists())

    def test_missing_id_is_a_clear_error(self):
        err = run_cli("plugins", "--action", "enable", expect=2, env=self.env())
        self.assertIn("--id", json.dumps(err, ensure_ascii=False))

    def test_doctor_without_plugins_keeps_its_shape(self):
        doctor = run_cli("doctor", env=self.env())
        self.assertNotIn("plugins", doctor)

    def test_doctor_survives_a_corrupt_plugins_json(self):
        self.install()
        (self.home / "plugins.json").write_bytes(b"{")
        doctor = run_cli("doctor", env=self.env())
        self.assertNotIn("plugins", doctor)
        self.assertIn("plugins.json", doctor["plugins_error"])

    def test_list_note_says_status_is_pre_load(self):
        """Finding 1: `plugins --action list` nunca roda código; a nota deixa claro que o
        status ali é pré-carga e manda para `doctor` o resultado real do carregamento."""
        self.install()
        out = run_cli("plugins", "--action", "list", env=self.env())
        self.assertIn("note", out)
        self.assertIn("doctor", out["note"])

    def test_provider_import_sys_exit_does_not_crash_the_cli(self):
        """Finding 2: `sys.exit(0)` no import do plugin não pode sair do processo com
        stdout vazio — `providers`/`doctor` continuam respondendo com os built-ins."""
        self.install(code="import sys\n\nsys.exit(0)\n")
        env = {**self.env(), "GB_PLUGINS": "demo"}
        out = run_cli("providers", env=env)
        self.assertIn("youtube", out)
        doctor = run_cli("doctor", env=env)
        self.assertEqual("failed", doctor["plugins"][0]["status"])
        self.assertIn("SystemExit", doctor["plugins"][0]["reason"])

    def test_doctor_overlays_failed_status_and_reason_over_the_preload_inventory(self):
        """Finding 1: `plugins.json` marca o plugin habilitado (pré-carga: "enabled"), mas o
        register() dele estoura — o doctor tem que mostrar o resultado real do carregamento."""
        self.install(code="def register(api):\n    raise RuntimeError('boom')\n")
        doctor = run_cli("doctor", env={**self.env(), "GB_PLUGINS": "demo"})
        self.assertEqual("failed", doctor["plugins"][0]["status"])
        self.assertIn("RuntimeError", doctor["plugins"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
