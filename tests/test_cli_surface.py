"""Structural golden test of the whole argparse surface (`getbrolls.cli.build_parser()`).

Unlike `tests/test_cli_help.py` (which checks every argument *has* help text),
this pins the actual shape of the CLI contract — every subcommand, every
option/positional, its flags/dest, required-ness, nargs, choices, default and
action type — as a fixture (`tests/fixtures/cli_surface.json`), so an
unintentional change to the CLI surface fails loudly and an intentional one
has a one-command way to update the fixture.

This intentionally does NOT compare rendered `--help` text: argparse's
`HelpFormatter` output differs by Python version and terminal width, which
would make the fixture flaky across the 3.11/3.12/3.13 interpreters this
project supports. The structure walked here was verified by hand to be
byte-identical across 3.11, 3.12, 3.13 and 3.14, with one documented
exception: a positional argument's internal `required` attribute (computed
by argparse from `nargs`, never set by this project) changed default value
between 3.11 and 3.12+ with no behavior difference — `build_surface()` below
reports it as `null` for positionals instead of comparing it, for exactly
that reason.
"""

import argparse
import json
import os
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.cli import build_parser

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cli_surface.json"


def json_safe(value):
    """Recursively coerce an argparse attribute into a JSON-serializable value.

    `argparse.SUPPRESS` is already the literal string "==SUPPRESS==" in
    CPython, so a suppressed default or help text naturally serializes to
    that string with no special-casing needed here.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)


def describe_action(action):
    # `required` on a *positional* action is computed internally by argparse from
    # `nargs`, not declared by build_parser(); its default value there changed
    # between Python versions (e.g. 3.11 vs 3.12+) with no behavior difference.
    # Only an optional (flagged) action carries a `required` this project sets.
    is_positional = not action.option_strings
    return {
        "option_strings": sorted(action.option_strings),
        "dest": action.dest,
        "required": None if is_positional else bool(action.required),
        "nargs": json_safe(action.nargs),
        "choices": sorted(str(c) for c in action.choices) if action.choices is not None else None,
        "default": json_safe(action.default),
        "action": type(action).__name__,
        "help": json_safe(action.help),
    }


def describe_parser(parser):
    """Every option/positional of one parser, excluding the subparsers action
    itself (that recursion is driven separately, by name, in build_surface).
    """
    return [describe_action(action) for action in parser._actions if not isinstance(action, argparse._SubParsersAction)]


def build_surface():
    """Deterministic JSON-serializable walk of the whole CLI surface: the
    root parser's own options, plus every subcommand (sorted by name) with
    its help/summary string and its own options/positionals.
    """
    parser = build_parser()
    sub_action = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    names_and_help = {choice.dest: choice.help for choice in sub_action._choices_actions}
    return {
        "top_level": describe_parser(parser),
        "subcommands": {
            name: {"help": names_and_help[name], "options": describe_parser(sub_action.choices[name])}
            for name in sorted(sub_action.choices)
        },
    }


class CliSurfaceGoldenTests(unittest.TestCase):
    def test_surface_matches_the_committed_fixture(self):
        current = build_surface()
        if os.environ.get("GB_UPDATE_CLI_SURFACE") == "1":
            FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            FIXTURE.write_text(
                json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.skipTest("GB_UPDATE_CLI_SURFACE=1: fixture rewritten, not compared this run.")
        expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(
            expected,
            current,
            "CLI surface changed (new/removed/renamed subcommand or option, or a changed "
            "dest/required/nargs/choices/default/action/help). If this change is intentional, "
            "regenerate the fixture with:\n"
            "  GB_UPDATE_CLI_SURFACE=1 python3 -m unittest discover -s tests -p 'test_cli_surface.py'\n"
            "then review and commit the resulting tests/fixtures/cli_surface.json diff.",
        )

    def test_fixture_is_valid_json_with_at_least_twenty_subcommands(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertIn("subcommands", data)
        self.assertGreaterEqual(len(data["subcommands"]), 20)


if __name__ == "__main__":
    unittest.main()
