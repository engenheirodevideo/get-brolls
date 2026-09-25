"""C M-8: `print` de plugin nunca corrompe o JSON do stdout da CLI.
B-10: uma subclasse de KeyboardInterrupt levantada pelo plugin é falha dele."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _paths import CLI
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.sdk.registry import get_registry

NOISY_MANIFEST = {**MANIFEST, "contributes": {"providers": ["demo"], "commands": ["oi"]}}
NOISY_PLUGIN = """
print("HELLO FROM PLUGIN IMPORT")

from getbrolls.sdk.contracts import ProviderCapabilities


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True)

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        print("HELLO FROM SEARCH")
        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


def oi(args, ctx):
    print("HELLO FROM COMMAND")
    return {"ok": True}


def register(api):
    print("HELLO FROM REGISTER")
    api.provider(Fonte(api))
    api.command("oi", oi, "diz oi")
"""

INTERRUPT_PLUGIN = """
class Falso(KeyboardInterrupt):
    pass


def register(api):
    raise Falso()
"""


class PluginStdoutTests(LoaderTestCase):
    def run_gb(self, *args):
        env = {**os.environ, "GB_HOME": str(self.home)}
        done = subprocess.run(
            [sys.executable, str(CLI), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
            timeout=120,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout), done.stderr

    def test_plugin_prints_go_to_stderr_and_json_stays_parseable(self):
        self.install(NOISY_MANIFEST, NOISY_PLUGIN)
        pin_plugins("demo")
        project = Path(tempfile.mkdtemp(prefix="gb-m8-"))
        self.addCleanup(__import__("shutil").rmtree, project, ignore_errors=True)
        for args, marker in (
            (("providers",), "HELLO FROM REGISTER"),
            (("doctor",), "HELLO FROM PLUGIN IMPORT"),
            (("search", "--provider", "demo", "--query", "mar", "--project", str(project)), "HELLO FROM SEARCH"),
            (("x", "demo", "oi"), "HELLO FROM COMMAND"),
        ):
            with self.subTest(args=args):
                out, err = self.run_gb(*args)
                self.assertIsInstance(out, dict)
                self.assertIn(marker, err)


class KeyboardInterruptSubclassTests(LoaderTestCase):
    def test_subclass_from_register_fails_only_the_plugin(self):
        self.install({**MANIFEST, "contributes": {}}, INTERRUPT_PLUGIN)
        pin_plugins("demo")
        with patch.dict(os.environ, {}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("youtube", reg.provider_names())


if __name__ == "__main__":
    unittest.main()
