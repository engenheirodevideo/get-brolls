"""`python -m getbrolls`: o módulo executável entrega o mesmo ponto de entrada da CLI."""

import importlib
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
import _paths as test_paths

from getbrolls import __version__


def module_command():
    """argv and environment of `python -m getbrolls` for the CLI under test (GB_TEST_CLI).

    Checkout: this Python with `scripts/` on PYTHONPATH. Wheel: the wheel's own Python, which
    `CLI_ARGV` already runs as `-P -m getbrolls`, with no PYTHONPATH (`_isolation` drops it)."""
    if test_paths.WHEEL_MODE:
        return list(test_paths.CLI_ARGV), dict(os.environ)
    return [sys.executable, "-m", "getbrolls"], {**os.environ, "PYTHONPATH": str(test_paths.ROOT / "scripts")}


class ModuleCommandTests(unittest.TestCase):
    def test_checkout_mode_runs_this_python_on_the_checkout(self):
        with patch.object(test_paths, "WHEEL_MODE", False):
            argv, env = module_command()
        self.assertEqual([sys.executable, "-m", "getbrolls"], argv)
        self.assertEqual(str(test_paths.ROOT / "scripts"), env["PYTHONPATH"])

    def test_wheel_mode_runs_the_installed_package_without_the_checkout(self):
        wheel = ("/venv/bin/python", "-P", "-m", "getbrolls")
        clean = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        with (
            patch.dict(os.environ, clean, clear=True),
            patch.object(test_paths, "WHEEL_MODE", True),
            patch.object(test_paths, "CLI_ARGV", wheel),
        ):
            argv, env = module_command()
        self.assertEqual(list(wheel), argv)
        self.assertNotIn("PYTHONPATH", env)


class MainModuleTests(unittest.TestCase):
    def run_module(self, *args):
        argv, env = module_command()
        return subprocess.run(
            [*argv, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            check=False,
        )

    def test_dash_m_getbrolls_prints_the_version_and_exits_zero(self):
        done = self.run_module("--version")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertTrue(done.stdout.startswith(f"getbrolls {__version__} "), done.stdout)

    def test_dash_m_getbrolls_exits_with_the_cli_usage_code_on_a_bad_command(self):
        done = self.run_module("comando-que-nao-existe")
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertEqual("", done.stdout)
        self.assertIn("USAGE_ERROR", done.stderr)

    def test_importing_the_module_does_not_run_the_cli(self):
        sys.modules.pop("getbrolls.__main__", None)
        self.addCleanup(sys.modules.pop, "getbrolls.__main__", None)
        with patch("getbrolls.cli.entrypoint") as entrypoint:
            module = importlib.import_module("getbrolls.__main__")
        entrypoint.assert_not_called()
        self.assertIs(entrypoint, module.entrypoint)


if __name__ == "__main__":
    unittest.main()
