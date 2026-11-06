"""Prefixos de flag da 2.5.0 continuam valendo: flag nova da 2.6 nunca deixa um deles ambíguo (§2, padrão b)."""

import argparse
import json
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls.cli import build_parser

FIXTURE = ROOT / "tests" / "fixtures" / "cli_flags_v250.json"
MIN_PREFIX = 3  # `--x`: o argparse não abrevia menos que isso


def _options(parser):
    return [
        option
        for action in parser._actions  # pylint: disable=protected-access
        for option in action.option_strings
        if option.startswith("--")
    ]


def _unique_prefixes(flag, flags):
    """Prefixos de `flag` que, na 2.5.0, só cabiam nela (a flag inteira inclusive)."""
    return [
        flag[:size]
        for size in range(MIN_PREFIX, len(flag) + 1)
        if [other for other in flags if other.startswith(flag[:size])] == [flag]
    ]


def _resolves(prefix, flag, current):
    """O argparse de hoje leva `prefix` a `flag`: igual a ela, ou o único começo, sem outra flag exata."""
    if prefix == flag:
        return flag in current
    return prefix not in current and [option for option in current if option.startswith(prefix)] == [flag]


class V250PrefixTests(unittest.TestCase):
    def setUp(self):
        self.v250 = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.parser = build_parser()
        sub = next(a for a in self.parser._actions if isinstance(a, argparse._SubParsersAction))  # pylint: disable=protected-access
        self.subparsers = sub.choices

    def test_every_v250_subcommand_still_exists(self):
        self.assertLessEqual(set(self.v250["subcommands"]), set(self.subparsers))

    def test_v250_unique_prefixes_still_resolve(self):
        cases = [("(raiz)", self.v250["top_level"], _options(self.parser))]
        cases += [
            (name, flags, _options(self.subparsers[name])) for name, flags in sorted(self.v250["subcommands"].items())
        ]
        for name, flags, current in cases:
            for flag in flags:
                for prefix in _unique_prefixes(flag, flags):
                    with self.subTest(command=name, prefix=prefix):
                        self.assertTrue(_resolves(prefix, flag, current), f"{prefix} deixou de levar a {flag}")

    def test_a_real_abbreviated_line_still_parses(self):
        args = self.parser.parse_args(["search", "--proj", "p", "--que", "gato", "--lim", "3"])
        self.assertEqual(("p", "gato", 3), (args.project, args.query, args.limit))
