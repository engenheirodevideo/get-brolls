"""`renamed`: um nome que saiu recusa com o caminho novo, sem alias."""

import json
import subprocess
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import (  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
    CLI_ARGV,
    ROOT,
)

from getbrolls import renamed, runtime
from getbrolls.errors import UsageError


class UsageTests(unittest.TestCase):
    def test_usage_names_the_old_and_the_new_and_is_a_usage_error(self):
        exc = renamed.usage("profile show", "profile --action show", reason="profile_positional", tail=" Só isso.")
        self.assertIsInstance(exc, UsageError)
        self.assertEqual("`profile show` saiu: agora é `profile --action show`. Só isso.", str(exc))
        self.assertEqual("USAGE_ERROR", runtime.error_code_for(exc))


class UnavailableTests(unittest.TestCase):
    def test_a_name_with_no_replacement_yet_is_refused_without_naming_one(self):
        exc = renamed.unavailable("old thing", reason="r", tail=" Hoje existe só x.")
        self.assertIsInstance(exc, UsageError)
        self.assertEqual("`old thing` saiu e não tem substituto nesta versão. Hoje existe só x.", str(exc))
        self.assertNotIn("agora é", str(exc))
        self.assertEqual("USAGE_ERROR", runtime.error_code_for(exc))

    def test_adopt_hint_names_migrate_as_adopting_not_converting(self):
        hint = renamed.adopt_hint("--project", "meu-video")
        self.assertIn("migrate --action plan --project meu-video", hint)
        self.assertNotIn("converter", hint)


class MessageTests(unittest.TestCase):
    def test_the_builders_are_pure_and_usage_uses_them(self):
        self.assertEqual("`a` saiu: agora é `b`. T", renamed.message("a", "b", " T"))
        self.assertEqual("`a` was renamed: it is now `b`.", renamed.message_en("a", "b"))
        self.assertEqual(renamed.message("a", "b"), str(renamed.usage("a", "b", reason="r")))

    def test_refuse_logs_one_event_with_the_reason_only(self):
        found = renamed.Rename("old thing", "new thing", "some_reason", tail=" Dica.", suggestion="--x")
        with self.assertLogs("getbrolls", level="WARNING") as captured:
            exc = renamed.refuse(found)
        self.assertEqual("`old thing` saiu: agora é `new thing`. Dica.", str(exc))
        self.assertEqual("USAGE_ERROR", runtime.error_code_for(exc))
        self.assertEqual(1, len(captured.records))
        text = captured.output[0]
        self.assertIn("event=renamed_refused", text)
        self.assertIn("reason=some_reason", text)
        self.assertNotIn("old thing", text)

    def test_refuse_at_parse_opens_the_log_read_only_and_closes_it(self):
        found = renamed.Rename("a", "b", "r")
        with patch.object(renamed.logs, "configure") as configure, patch.object(renamed.logs, "shutdown") as shutdown:
            exc = renamed.refuse_at_parse(found)
        self.assertEqual("`a` saiu: agora é `b`.", str(exc))
        configure.assert_called_once_with(None, read_only=True)
        shutdown.assert_called_once_with()


class OldShapeTests(unittest.TestCase):
    def setUp(self):
        renamed.reset_old_shape_warnings()
        self.addCleanup(renamed.reset_old_shape_warnings)
        self.event = {"warnings": []}
        token = runtime.ACTIVE.set(self.event)
        self.addCleanup(runtime.ACTIVE.reset, token)

    def test_old_shape_warns_once_with_the_fix(self):
        renamed.old_shape(
            "assets/composicoes/ do projeto", "Renomeie a pasta `assets/composicoes/` para `assets/composicao/`."
        )
        renamed.old_shape("assets/composicoes/ do projeto", "outra coisa")
        self.assertEqual(1, len(self.event["warnings"]))
        warning = self.event["warnings"][0]
        self.assertEqual("OLD_SHAPE", warning["code"])
        self.assertIn("assets/composicoes/ do projeto é da forma antiga", warning["message"])
        self.assertIn("Renomeie a pasta `assets/composicoes/` para `assets/composicao/`.", warning["message"])
        self.assertNotIn("migrate", warning["message"])

    def test_each_place_warns_once_for_the_same_old_shape(self):
        renamed.old_shape("components/", "Dica.", where="/a")
        renamed.old_shape("components/", "Dica.", where="/b")
        renamed.old_shape("components/", "Dica.", where="/a")
        self.assertEqual(2, len(self.event["warnings"]))

    def test_a_call_with_no_active_event_does_not_silence_the_next(self):
        token = runtime.ACTIVE.set(None)
        try:
            renamed.old_shape("components/", "Dica.", where="/a")
        finally:
            runtime.ACTIVE.reset(token)
        self.assertEqual([], self.event["warnings"])
        renamed.old_shape("components/", "Dica.", where="/a")
        self.assertEqual(1, len(self.event["warnings"]))

    def test_reset_makes_the_same_old_shape_warn_again(self):
        renamed.old_shape("components/", "Dica.", where="/a")
        renamed.old_shape("components/", "Dica.", where="/a")
        self.assertEqual(1, len(self.event["warnings"]))
        renamed.reset_old_shape_warnings()
        renamed.old_shape("components/", "Dica.", where="/a")
        self.assertEqual(2, len(self.event["warnings"]))


def _init_line(*args):
    """`init` com `args`, sem `--project`: (código de saída, JSON do stderr)."""
    done = subprocess.run([*CLI_ARGV, "init", *args], capture_output=True, text=True, encoding="utf-8", check=False)
    return done.returncode, json.loads(done.stderr)


class InitTemplateAtParseTests(unittest.TestCase):
    """`init --template` é recusado como `saiu`, mesmo quando falta o resto da linha (`--project` ou `--slug`)."""

    def test_the_rename_wins_over_the_missing_project(self):
        for line in (
            ["--template", "acme.foo@1", "--client", "acme", "--tema", "T"],
            ["--template=acme.foo@1", "--client", "acme", "--tema", "T"],
            ["--templ", "acme.foo@1", "--client", "acme", "--tema", "T"],
            ["--client", "acme", "--tema", "T", "--template", "acme.foo@1"],
        ):
            with self.subTest(line=line):
                code, found = _init_line(*line)
                self.assertEqual(2, code)
                self.assertEqual("USAGE_ERROR", found["error_code"])
                self.assertTrue(found["error"].startswith("`init --template` saiu: agora é `init --recipe RECEITA`"))
                self.assertIn("recipe --action list", found["error"])
                self.assertNotIn("migrate", found["error"])
                self.assertEqual("init", found["operation"])

    def test_a_short_prefix_is_the_tema_flag_and_never_names_the_removed_one(self):
        code, found = _init_line("--tem", "T", "--client", "acme", "--slug", "s")
        self.assertEqual(2, code)
        self.assertNotIn("--template", json.dumps(found))
        self.assertNotIn("ambígu", found["error"])
        self.assertIn("--tema só vale com --recipe", found["error"])
