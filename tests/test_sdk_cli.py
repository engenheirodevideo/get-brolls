"""`gb plugins`: inventário sem executar código, enable em dois passos, check de pasta."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import CLI
from _plugin_pins import pin_plugins
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

    def test_check_surfaces_the_plugins_own_error_instead_of_internal_error(self):
        """Finding 6: `register()` estourando RuntimeError não pode virar INTERNAL_ERROR
        (exit 3, bug interno) — é erro do plugin, exit 2, com tipo e mensagem visíveis."""
        folder = self.install(code="def register(api):\n    raise RuntimeError('boom')\n")
        err = run_cli("plugins", "--action", "check", "--path", folder, expect=2, env=self.env())
        self.assertNotEqual("INTERNAL_ERROR", err.get("error_code"))
        self.assertIn("RuntimeError", err["error"])
        self.assertIn("boom", err["error"])

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
        pin_plugins("demo")
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
        pin_plugins("demo")
        doctor = run_cli("doctor", env={**self.env(), "GB_PLUGINS": "demo"})
        self.assertEqual("failed", doctor["plugins"][0]["status"])
        self.assertIn("RuntimeError", doctor["plugins"][0]["reason"])

    def test_preset_selected_via_env_file_is_accepted(self):
        """Finding 4 + B-07: `--preset` não trava em `choices=` calculado ANTES do `.env`
        ser lido; `GB_PLUGINS` num `--env-file` só filtra plugins já habilitados (com pin).
        Offline: o candidato vem da busca do próprio plugin de teste, nunca do YouTube."""
        self.install()
        pin_plugins("demo")
        project = Path(tempfile.mkdtemp(prefix="gb-project-"))
        self.addCleanup(__import__("shutil").rmtree, project, ignore_errors=True)
        env_file = project / "plugins.env"
        env_file.write_text("GB_PLUGINS=demo\n", encoding="utf-8")

        found = run_cli("search", "--provider", "demo", "--query", "mar", project=project, env=self.env())
        candidate = found["items"][0]["id"]
        result = run_cli(
            "--env-file",
            str(env_file),
            "permit",
            "--candidate",
            candidate,
            "--preset",
            "demo",
            project=project,
            env=self.env(),
        )
        self.assertEqual("permitted", result["rights"]["status"])
        env_file.write_text("GB_PLUGINS=off\n", encoding="utf-8")
        # Fora da seleção, o nome vira erro de uso (A M1): sai do argparse, antes do projeto.
        done = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--env-file",
                str(env_file),
                "permit",
                "--candidate",
                candidate,
                "--preset",
                "demo",
                "--project",
                str(project),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, **self.env()},
            check=False,
            timeout=120,
        )
        self.assertEqual(2, done.returncode)
        self.assertIn("invalid choice: 'demo'", done.stderr)


if __name__ == "__main__":
    unittest.main()
