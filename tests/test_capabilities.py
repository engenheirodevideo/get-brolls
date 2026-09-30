"""`capabilities --json`: o manifesto de comandos é derivado do parser, nunca escrito à mão."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import __version__, capabilities, cli
from getbrolls.cli import build_parser

BROKEN_CODE = "raise SystemExit('não devia rodar')\n"
COMMAND_MANIFEST = {**MANIFEST, "contributes": {"commands": ["contar", "lista"]}}


def subcommands():
    parser = build_parser()
    action = next(a for a in parser._actions if a.choices and a.dest == "command")  # pylint: disable=protected-access
    return dict(action.choices or {})


def by_name(manifest):
    return {row["name"]: row for row in manifest["commands"]}


class ManifestShapeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = capabilities.describe(build_parser())

    def test_top_level_shape(self):
        self.assertEqual(1, self.manifest["schema_version"])
        self.assertEqual("getbrolls", self.manifest["name"])
        self.assertEqual(__version__, self.manifest["version"])
        self.assertEqual("getbrolls", self.manifest["prog"])
        self.assertEqual(
            {"schema_version", "name", "version", "prog", "invocation", "output", "global_options", "commands"}
            | {"exit_codes", "error_codes", "plugin_commands"},
            set(self.manifest),
        )

    def test_every_subcommand_is_described(self):
        names = [row["name"] for row in self.manifest["commands"]]
        self.assertEqual(sorted(subcommands()), names)
        self.assertIn("capabilities", names)

    def test_flags_match_the_parser(self):
        for name, parser in subcommands().items():
            expected = {flag for a in parser._actions for flag in a.option_strings} - {"-h", "--help"}  # pylint: disable=protected-access
            described = {flag for opt in by_name(self.manifest)[name]["options"] for flag in opt["flags"]}
            self.assertEqual(expected, described, name)

    def test_summaries_and_option_fields(self):
        roteiro = by_name(self.manifest)["roteiro"]
        self.assertEqual(cli.SUMMARIES["roteiro"], roteiro["summary"])
        action = next(o for o in roteiro["options"] if o["dest"] == "action")
        self.assertTrue(action["required"])
        self.assertEqual(["check", "new", "plan", "review", "sync"], action["choices"])
        self.assertEqual("string", action["type"])
        self.assertTrue(action["takes_value"])
        self.assertTrue(by_name(self.manifest)["roteiro"]["requires_project"])
        self.assertFalse(by_name(self.manifest)["doctor"]["requires_project"])

    def test_read_only_mirrors_runtime_tables(self):
        commands = by_name(self.manifest)
        self.assertEqual("by_action", commands["queue"]["read_only"])
        self.assertEqual(["status"], commands["queue"]["read_only_actions"])
        self.assertIs(True, commands["status"]["read_only"])
        self.assertIs(True, commands["capabilities"]["read_only"])
        self.assertIs(False, commands["fetch"]["read_only"])
        self.assertEqual([], commands["fetch"]["read_only_actions"])

    def test_exit_codes_come_from_cli_constants(self):
        self.assertEqual(
            [{"code": code, "name": name, "meaning": meaning} for code, name, meaning in cli.EXIT_CODES],
            self.manifest["exit_codes"],
        )
        exits = {row["code"]: row["exit"] for row in self.manifest["error_codes"]}
        self.assertEqual(2, exits["USAGE_ERROR"])
        self.assertEqual(4, exits["PREREQUISITE_MISSING"])
        self.assertEqual(1, exits["INVALID_DATA"])

    def test_hidden_options_are_flagged(self):
        serve = by_name(self.manifest)["serve"]
        hidden = next(o for o in serve["options"] if "--confirm-format-change" in o["flags"])
        self.assertTrue(hidden["hidden"])
        self.assertIsNone(hidden["help"])

    def test_no_machine_paths(self):
        text = json.dumps(self.manifest)
        self.assertNotIn(str(Path.home()), text)
        self.assertNotIn(os.sep + "Users" + os.sep, text)


class PluginCommandTests(LoaderTestCase):
    def test_no_plugins_gives_an_empty_list(self):
        self.assertEqual([], capabilities.describe(build_parser())["plugin_commands"])

    def test_plugin_commands_come_from_the_manifest_without_running_code(self):
        self.install(COMMAND_MANIFEST, code=BROKEN_CODE)
        pin_plugins("demo", home=self.home)
        rows = capabilities.describe(build_parser())["plugin_commands"]
        self.assertEqual(
            [
                {"plugin": "demo", "command": "contar", "status": "enabled", "argv": ["x", "demo", "contar"]},
                {"plugin": "demo", "command": "lista", "status": "enabled", "argv": ["x", "demo", "lista"]},
            ],
            rows,
        )

    def test_disabled_plugins_are_not_listed(self):
        self.install(COMMAND_MANIFEST)
        self.assertEqual([], capabilities.describe(build_parser())["plugin_commands"])
        pin_plugins("demo", home=self.home)
        os.environ["GB_PLUGINS"] = "off"
        self.assertEqual([], capabilities.describe(build_parser())["plugin_commands"])


class CapabilitiesCliTests(unittest.TestCase):
    def test_output_is_deterministic_and_on_stdout(self):
        cwd = Path(tempfile.mkdtemp(prefix="gb-cwd-"))
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        before = Path.cwd()
        os.chdir(cwd)
        self.addCleanup(os.chdir, before)
        first = run_cli("capabilities", "--json")
        second = run_cli("capabilities", "--json")
        self.assertEqual(first, second)
        self.assertEqual("getbrolls", first["name"])
        self.assertEqual([], list(cwd.iterdir()))

    def test_json_flag_is_optional(self):
        self.assertEqual(run_cli("capabilities"), run_cli("capabilities", "--json"))


if __name__ == "__main__":
    unittest.main()
