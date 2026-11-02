"""Superfície pública congelada: o `__all__` do SDK e o contrato da CLI não mudam durante a reestruturação.

Esta guarda existe para a divisão de módulos da 2.6.0: mover código é livre, mas o que plugins e
scripts enxergam não muda. O SDK é conferido nome a nome (lista, ordem, identidade dos objetos). A CLI é
conferida contra o retrato em `tests/sdk_surface/cli_surface.json`: os 41 comandos (11 de ferramenta e 30 de
projeto), as opções de cada um (inclusive as legadas escondidas), os textos de `SUMMARIES` e os códigos de saída.
Mudança intencional de contrato atualiza o retrato no mesmo commit, com a linha em `docs/CLI_CONTRACT.md`.
"""

import argparse
import importlib
import json
import re
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import cli, runtime
from getbrolls import sdk as public_sdk
from getbrolls.sdk import contracts, registry

SNAPSHOT = Path(__file__).resolve().parent / "sdk_surface" / "cli_surface.json"
SCAFFOLD = ROOT / "scripts" / "getbrolls" / "sdk" / "scaffold.py"

SDK_ALL = [
    "RESOLVER_KINDS",
    "SDK_API",
    "CommandContext",
    "CommandSpec",
    "ExportResult",
    "Exporter",
    "ExporterSpec",
    "MediaRequest",
    "PluginError",
    "Preset",
    "Provider",
    "ProviderCapabilities",
    "Resolver",
    "ResolverHit",
    "ResolverSpec",
    "Route",
    "RouteResult",
    "get_registry",
]
SDK_API_VERSION = 1
TOOLCHAIN_COMMANDS = 11
PROJECT_COMMANDS = 30
EXIT_CODES = {"INTERNAL_ERROR": 3, "INTERRUPTED": 130, "LOCKED": 1, "PREREQUISITE_MISSING": 4, "USAGE_ERROR": 2}
EXIT_FOR_OTHER_ERRORS = 1


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    found: dict[str, argparse.ArgumentParser] = {}
    for action in parser._actions:  # pylint: disable=protected-access
        if isinstance(action, argparse._SubParsersAction):  # pylint: disable=protected-access
            found.update(action.choices)
    return found


def _parser_surface(parser: argparse.ArgumentParser) -> dict:
    """Opções (ordenadas) e subcomandos de um parser, recursivamente."""
    options = sorted({flag for action in parser._actions for flag in action.option_strings})  # pylint: disable=protected-access
    children = _subparsers(parser)
    return {
        "options": options,
        "subcommands": {name: _parser_surface(child) for name, child in children.items()},
    }


def current_cli_surface() -> dict:
    """O retrato da CLI de hoje, no mesmo formato do arquivo congelado."""
    commands = _subparsers(cli.build_parser())
    return {
        "commands": [{"name": name, **_parser_surface(parser)} for name, parser in commands.items()],
        "summaries": dict(sorted(cli.SUMMARIES.items())),
        "exit_codes": dict(sorted(runtime.ERROR_EXIT.items())),
        "exit_for_other_errors": runtime.EXIT_FOR_OTHER_ERRORS,
    }


class SdkSurfaceTests(unittest.TestCase):
    def test_all_is_exactly_the_eighteen_public_names_in_order(self):
        self.assertEqual(SDK_ALL, list(public_sdk.__all__))
        self.assertEqual(18, len(public_sdk.__all__))

    def test_every_name_is_the_same_object_as_in_its_defining_module(self):
        for name in public_sdk.__all__:
            source = registry if name == "get_registry" else contracts
            with self.subTest(name=name):
                self.assertIs(getattr(source, name), getattr(public_sdk, name))

    def test_the_api_version_is_unchanged(self):
        self.assertEqual(SDK_API_VERSION, public_sdk.SDK_API)

    def test_the_testing_helpers_and_scaffold_imports_still_work(self):
        self.assertTrue(hasattr(importlib.import_module("getbrolls.sdk.testing"), "__name__"))

        lines = re.findall(
            r"^from getbrolls\.sdk import ([A-Za-z_, ]+)$", SCAFFOLD.read_text(encoding="utf-8"), re.MULTILINE
        )
        names = {name.strip() for line in lines for name in line.split(",") if name.strip() != "testing"}
        self.assertTrue(
            {"ProviderCapabilities", "PluginError", "RouteResult", "ExportResult"} <= names,
            "templates do not import them",
        )
        for name in sorted(names):
            with self.subTest(name=name):
                self.assertTrue(hasattr(public_sdk, name))

    def test_the_example_and_official_plugins_import_only_the_sdk(self):
        pattern = re.compile(r"^\s*(?:from|import)\s+getbrolls\S*", re.MULTILINE)
        for folder in ("examples", "plugins_official"):
            for path in sorted((ROOT / folder).rglob("*.py")):
                for match in pattern.findall(path.read_text(encoding="utf-8")):
                    with self.subTest(path=path.name, line=match):
                        self.assertRegex(match.split()[1], r"^getbrolls\.sdk(\.|$)|^getbrolls$")


class CliContractSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        cls.current = current_cli_surface()

    def test_there_are_forty_one_commands_eleven_toolchain_and_thirty_project(self):
        names = [command["name"] for command in self.frozen["commands"]]
        self.assertEqual(TOOLCHAIN_COMMANDS + PROJECT_COMMANDS, len(names))
        self.assertEqual(
            [
                "providers",
                "doctor",
                "plugins",
                "x",
                "setup",
                "capabilities",
                "profile",
                "client",
                "template",
                "recipe",
                "environment",
            ],
            names[:TOOLCHAIN_COMMANDS],
        )
        self.assertEqual(names, [command["name"] for command in self.current["commands"]])

    def test_every_command_keeps_its_options_hidden_legacy_ones_included(self):
        by_name = {command["name"]: command for command in self.current["commands"]}
        for command in self.frozen["commands"]:
            with self.subTest(command=command["name"]):
                self.assertEqual(command, by_name[command["name"]])

    def test_summaries_are_unchanged_and_cover_every_command(self):
        self.assertEqual(self.frozen["summaries"], self.current["summaries"])
        self.assertEqual({c["name"] for c in self.frozen["commands"]}, set(self.frozen["summaries"]))

    def test_exit_codes_are_unchanged(self):
        self.assertEqual(EXIT_CODES, self.current["exit_codes"])
        self.assertEqual(self.frozen["exit_codes"], self.current["exit_codes"])
        self.assertEqual(EXIT_FOR_OTHER_ERRORS, self.current["exit_for_other_errors"])
        self.assertEqual(
            {0, 1, 2, 3, 4, 130}, {0, EXIT_FOR_OTHER_ERRORS, *EXIT_CODES.values()}, "o contrato lista 0/1/2/3/4/130"
        )

    def test_the_entry_points_are_unchanged(self):
        self.assertTrue(callable(cli.entrypoint))
        self.assertTrue((ROOT / "scripts" / "gb.py").is_file())
        self.assertTrue((ROOT / "scripts" / "getbrolls" / "__main__.py").is_file())
        self.assertIn('getbrolls = "getbrolls.cli:entrypoint"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
