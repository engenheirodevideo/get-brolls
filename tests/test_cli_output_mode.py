"""`--json` em todo comando e `GB_OUTPUT=json` (§2 do contrato): a saída já é JSON; a flag não muda nada."""

import argparse
import os
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import cli, cli_parser, config


class JsonFlagTests(unittest.TestCase):
    def test_every_command_accepts_json(self):
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))  # pylint: disable=protected-access
        for name, subparser in sorted(sub.choices.items()):
            with self.subTest(command=name):
                flags = {o for a in subparser._actions for o in a.option_strings}  # pylint: disable=protected-access
                self.assertIn("--json", flags)

    def test_json_changes_nothing_in_the_output(self):
        self.assertEqual(run_cli("providers"), run_cli("providers", "--json"))

    def test_json_stays_out_of_the_usage_line_of_the_2_5_commands(self):
        """O `usage` do erro de uso de um comando da 2.5 sai byte a byte como antes (o ouro confere)."""
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))  # pylint: disable=protected-access
        for name in ("status", "search", "approve", "queue", "fetch", "deliver"):
            with self.subTest(command=name):
                self.assertNotIn("--json", sub.choices[name].format_usage())
                self.assertNotIn("--json", sub.choices[name].format_help())
        self.assertIn("--json", sub.choices["doctor"].format_usage())

    def test_machine_output_reads_the_flag_and_gb_output(self):
        plain = argparse.Namespace(json=False)
        self.assertFalse(cli_parser.machine_output(plain, environ={}))
        self.assertTrue(cli_parser.machine_output(argparse.Namespace(json=True), environ={}))
        self.assertTrue(cli_parser.machine_output(plain, environ={"GB_OUTPUT": "json"}))
        for padded in (" json", "json ", "\tjson\n"):
            with self.subTest(padded=padded):
                self.assertTrue(cli_parser.machine_output(plain, environ={"GB_OUTPUT": padded}))
        for value in ("", "human", "JSON", "1"):
            with self.subTest(value=value):
                self.assertFalse(cli_parser.machine_output(plain, environ={"GB_OUTPUT": value}))

    def test_machine_output_reads_the_process_environment_by_default(self):
        plain = argparse.Namespace(json=False)
        with patch.dict(os.environ, {"GB_OUTPUT": "json"}):
            self.assertTrue(cli_parser.machine_output(plain))
        with patch.dict(os.environ, {"GB_OUTPUT": "human"}):
            self.assertFalse(cli_parser.machine_output(plain))

    def test_gb_output_is_a_core_key(self):
        self.assertIn("GB_OUTPUT", config.KEYS)
