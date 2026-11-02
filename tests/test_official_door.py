"""A porta única dos plugins oficiais: `environment --action list` mostra a ponte; `plugins` não a mexe."""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.sdk import environments, official, official_pins

DOOR = "`hyperframes` é plugin oficial e vem com o getbrolls: veja `environment --action list`"


class EnvironmentListTests(LoaderTestCase):
    def env(self, **extra):
        return {"GB_HOME": str(self.home), **extra}

    def test_the_shipped_bridge_is_listed_with_its_pin(self):
        done = run_cli("environment", "--action", "list", env=self.env())
        self.assertEqual(
            [
                {
                    "id": "hyperframes",
                    "version": "2.6.0",
                    "kind": "bridge",
                    "readback": "files",
                    "origin": "official",
                    "sha256": official_pins.PINS["hyperframes"],
                    "status": "enabled",
                    "reason": None,
                }
            ],
            done["environments"],
        )
        caps = run_cli("capabilities", "--json", env=self.env())
        self.assertEqual(
            [{k: v for k, v in row.items() if k != "reason"} for row in done["environments"]], caps["environments"]
        )

    def test_the_session_selection_turns_it_off_and_says_so(self):
        done = run_cli("environment", "--action", "list", env=self.env(GB_PLUGINS="off"))
        self.assertEqual("disabled", done["environments"][0]["status"])
        self.assertIn("GB_PLUGINS", done["environments"][0]["reason"])

    def test_a_tampered_bridge_is_listed_invalid(self):
        shipped = Path(tempfile.mkdtemp(prefix="gb-official-"))
        self.addCleanup(shutil.rmtree, shipped, ignore_errors=True)
        shutil.copytree(ROOT / "plugins_official" / "hyperframes", shipped / "hyperframes")
        (shipped / "hyperframes" / "README.md").write_text("mudou", encoding="utf-8")
        with patch.object(official, "official_root", return_value=shipped):
            row = environments.rows()[0]
        self.assertEqual("invalid", row["status"])
        self.assertIn("Reinstale", row["reason"])


class DoorTests(LoaderTestCase):
    def env(self):
        return {"GB_HOME": str(self.home)}

    def test_plugins_never_touches_an_official_plugin(self):
        for argv in (
            ("--action", "enable", "--id", "hyperframes"),
            ("--action", "disable", "--id", "hyperframes"),
            ("--action", "update", "--id", "hyperframes"),
            ("--action", "install", "--id", "hyperframes@qualquer"),
            ("--action", "remove", "--id", "hyperframes"),
        ):
            with self.subTest(action=argv[1]):
                refused = run_cli("plugins", *argv, expect=2, env=self.env())
                self.assertEqual("USAGE_ERROR", refused["error_code"])
                self.assertIn(DOOR, refused["error"])
        self.assertFalse((self.home / "plugins.json").exists())

    def test_remove_still_takes_a_leftover_folder_or_state(self):
        self.install({**MANIFEST, "id": "hyperframes"})
        preview = run_cli("plugins", "--action", "remove", "--id", "hyperframes", env=self.env())
        self.assertFalse(preview["removed"])
        state = {"enabled": {}, "last_pins": {"hyperframes": {"version": "0.1.0", "sha256": "0" * 64}}}
        shutil.rmtree(self.home / "plugins" / "hyperframes")
        (self.home / "plugins.json").write_text(json.dumps(state), encoding="utf-8")
        done = run_cli("plugins", "--action", "remove", "--id", "hyperframes", "--yes", env=self.env())
        self.assertTrue(done["removed"])
        self.assertNotIn(
            "hyperframes", json.loads((self.home / "plugins.json").read_text(encoding="utf-8"))["last_pins"]
        )

    def test_other_ids_keep_their_usual_answers(self):
        refused = run_cli("plugins", "--action", "enable", "--id", "nao_instalado", expect=1, env=self.env())
        self.assertNotIn("plugin oficial", refused["error"])
