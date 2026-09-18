"""Contrato de autoexplicação da CLI: help, versão, veredito do doctor e erros úteis."""

import argparse
import json
import subprocess
import sys
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import CLI, ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import __version__
from getbrolls.cli import FORMAT_GATE_SUBCOMMANDS, SUMMARIES, build_parser
from getbrolls.commands import doctor_summary


def subparsers(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("parser sem subcomandos")


class HelpContractTests(unittest.TestCase):
    def test_every_subcommand_declares_help(self):
        action = subparsers(build_parser())
        described = {choice.dest for choice in action._choices_actions}
        self.assertEqual(set(action.choices), described)
        for choice in action._choices_actions:
            self.assertTrue(choice.help, f"subcomando sem help: {choice.dest}")
            self.assertEqual(SUMMARIES[choice.dest], choice.help)

    def test_every_subcommand_argument_declares_help(self):
        missing = []
        for name, parser in subparsers(build_parser()).choices.items():
            for argument in parser._actions:
                if isinstance(argument, argparse._HelpAction):
                    continue
                if not argument.help:
                    missing.append(f"{name} {argument.option_strings}")
        self.assertEqual([], missing, "argumentos sem help")

    def test_root_options_declare_help(self):
        for argument in build_parser()._actions:
            if isinstance(argument, (argparse._HelpAction, argparse._SubParsersAction)):
                continue
            self.assertTrue(argument.help, argument.option_strings)

    def test_version_flag_prints_package_version(self):
        done = subprocess.run(
            [sys.executable, str(CLI), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(f"get-brolls {__version__}", done.stdout.strip())


class DoctorVerdictTests(unittest.TestCase):
    def payload(self):
        done = subprocess.run(
            [sys.executable, str(CLI), "doctor"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout)

    def test_doctor_reports_skill_version_and_python(self):
        payload = self.payload()
        self.assertEqual(__version__, payload["get_brolls"])
        self.assertEqual(sys.version.split()[0], payload["python"])
        self.assertNotIn("runtime", payload)

    def test_doctor_summary_comes_first_with_three_lists(self):
        payload = self.payload()
        self.assertEqual("summary", next(iter(payload)))
        summary = payload["summary"]
        self.assertEqual(["ok", "missing", "optional"], list(summary))
        for key in ("ok", "missing", "optional"):
            self.assertIsInstance(summary[key], list)
        for entry in summary["missing"]:
            self.assertTrue(entry["fix"])
            self.assertTrue(entry["note"])
        self.assertEqual(set(), set(summary["ok"]) & {entry["item"] for entry in summary["missing"]})

    def test_missing_playwright_names_installer_and_impact(self):
        summary = doctor_summary({"playwright-cli": False, "ffmpeg": True})
        entry = next(e for e in summary["missing"] if e["item"] == "playwright-cli")
        self.assertIn("install.sh", entry["fix"])
        self.assertIn("Instagram indisponível sem ele", entry["note"])
        self.assertIn("ffmpeg", summary["ok"])

    def test_unset_provider_keys_are_optional_not_missing(self):
        import os
        from unittest import mock

        environment = {
            key: value for key, value in os.environ.items() if key not in ("PEXELS_API_KEY", "PIXABAY_API_KEY")
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            summary = doctor_summary({"ffmpeg": True})
        optional = {entry["item"] for entry in summary["optional"]}
        self.assertIn("PEXELS_API_KEY", optional)
        self.assertIn("PIXABAY_API_KEY", optional)
        self.assertNotIn("PEXELS_API_KEY", {entry["item"] for entry in summary["missing"]})


class ActionableErrorTests(unittest.TestCase):
    def test_missing_ytdlp_names_the_installer(self):
        from getbrolls import social
        from getbrolls.http import ProviderError

        original_local = social.local_ytdlp
        original_which = social.shutil.which
        social.local_ytdlp = lambda root=None: None
        social.shutil.which = lambda name: None
        try:
            with self.assertRaises(ProviderError) as raised:
                social.command()
        finally:
            social.local_ytdlp = original_local
            social.shutil.which = original_which
        message = str(raised.exception)
        self.assertIn("install.sh", message)
        self.assertIn("install.ps1", message)
        self.assertIn("/plugin update", message)

    def test_absent_ffmpeg_is_distinguishable_from_bad_range(self):
        from getbrolls import media

        with self.assertRaises(ValueError) as absent:
            media.run(["ffmpeg-inexistente-getbrolls", "-version"])
        self.assertIn("não encontrado", str(absent.exception))
        self.assertIn("doctor", str(absent.exception))

        with self.assertRaises(ValueError) as failed:
            media.run([sys.executable, "-c", "raise SystemExit(3)"])
        self.assertNotIn("não encontrado", str(failed.exception))
        self.assertIn("doctor", str(failed.exception))


# `--confirm-format-change` só aparece em `--help` onde tem efeito.
#
# `execute()` (commands.py) só chega a `sync_formats` depois de passar pelos retornos
# antecipados de `serve`, `queue`, `init-rules`, `init-brief`, `brief`, `learn`,
# `library` e `rules`, e por cima de `READ_ONLY_CONSULTS` (`references`, `inspect`).
# Nesses, a flag continua aceita — scripts e agentes já a passam para eles hoje — mas
# some do texto de ajuda porque nunca teve efeito ali.

# Subcomandos que aceitam `--project` mas retornam antes de `sync_formats` (ou estão em
# `READ_ONLY_CONSULTS`): a flag continua aceita por compatibilidade, mas escondida.
NO_OP_SUBCOMMANDS = (
    "serve",
    "queue",
    "init-rules",
    "init-brief",
    "brief",
    "learn",
    "library",
    "rules",
    "references",
    "inspect",
)


def _help_text(subcommand):
    return subparsers(build_parser()).choices[subcommand].format_help()


class ConfirmFormatChangeHelpTests(unittest.TestCase):
    def test_flag_appears_in_help_of_every_format_gate_subcommand(self):
        for subcommand in FORMAT_GATE_SUBCOMMANDS:
            with self.subTest(subcommand=subcommand):
                self.assertIn("--confirm-format-change", _help_text(subcommand))

    def test_flag_is_hidden_from_help_of_no_op_subcommands(self):
        for subcommand in NO_OP_SUBCOMMANDS:
            with self.subTest(subcommand=subcommand):
                text = _help_text(subcommand)
                self.assertNotIn("--confirm-format-change", text)

    def test_flag_still_parses_on_a_no_op_subcommand(self):
        parser = build_parser()
        for subcommand in NO_OP_SUBCOMMANDS:
            with self.subTest(subcommand=subcommand):
                extra = []
                if subcommand == "queue":
                    extra = ["--action", "status"]
                elif subcommand == "inspect":
                    extra = ["--url", "https://example.org/a"]
                elif subcommand == "library":
                    extra = ["--search", "termo"]
                try:
                    parsed = parser.parse_args(
                        [subcommand, "--project", "/tmp/whatever", "--confirm-format-change", *extra]
                    )
                except SystemExit as exc:
                    self.fail(f"{subcommand} rejeitou --confirm-format-change: SystemExit({exc.code})")
                self.assertTrue(getattr(parsed, "confirm_format_change", False))


if __name__ == "__main__":
    unittest.main()
