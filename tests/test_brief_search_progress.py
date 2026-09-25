"""Busca do beat que volta vazia não vira o mesmo comando para sempre.

M-3: o degrau `brief-search` sempre sugeria a primeira fonte pesquisável do beat.
Quando ela voltava sem nada, `status` repetia o comando idêntico em laço e nunca
tentava a próxima fonte permitida. Agora a busca vazia fica registrada no projeto,
o degrau passa para a fonte seguinte e, esgotadas todas, diz isso à pessoa.
"""

import copy
import shlex
import tempfile
import unittest
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)
from test_brief import VALID, write_brief

from getbrolls import cli, providers
from getbrolls.ledger import Ledger


def one_beat_brief(project, sources):
    data = copy.deepcopy(VALID)
    data["beats"] = [dict(data["beats"][0], allowed_sources=sources)]
    write_brief(project, data)
    # `status` lê um projeto existente; a árvore `brolls/` nasce no primeiro comando.
    Ledger(project)


def status_do(project):
    return cli.main(["status", "--project", project])["summary"]["do"]


def run(command):
    return cli.main(shlex.split(command)[2:])


class EmptySearchesMoveOn(unittest.TestCase):
    def test_following_do_through_two_empty_searches_reaches_the_person(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            commands, providers_asked = [], []
            with patch.object(
                providers,
                "search",
                side_effect=lambda name, *a, **k: providers_asked.append(name) or [],
            ):
                for _ in range(2):
                    action = status_do(tmp)
                    self.assertEqual("brief-search", action["step"])
                    commands.append(action["command"])
                    run(action["command"])
                final = status_do(tmp)
            # Duas buscas, duas fontes diferentes: nenhum comando repetido.
            self.assertEqual(["commons", "nasa"], providers_asked)
            self.assertEqual(2, len(set(commands)), commands)
            self.assertIn("--provider commons", commands[0])
            self.assertIn("--provider nasa", commands[1])
            # Todas as fontes vazias: a pessoa decide, e nada de comando que falha de novo.
            self.assertEqual("brief-exhausted", final["step"])
            self.assertIsNone(final["command"])
            self.assertTrue(final["blocking_human"])
            self.assertIn("abertura", final["for_human"])
            self.assertIn("commons", final["for_human"])
            self.assertIn("nasa", final["for_human"])
            self.assertIn("allowed_sources", final["for_human"])

    def test_a_search_that_found_something_is_not_recorded_as_empty(self):
        from getbrolls.models import candidate

        found = [candidate("commons", "1", "Achado", "https://commons.wikimedia.org/wiki/File:A.jpg")]
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            command = status_do(tmp)["command"]
            with patch.object(providers, "search", return_value=found):
                run(command)
            self.assertNotEqual("brief-search", status_do(tmp)["step"])

    def test_dry_run_does_not_count_as_a_tried_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            command = status_do(tmp)["command"]
            with patch.object(providers, "search", return_value=[]):
                run(command + " --dry-run")
            self.assertEqual(command, status_do(tmp)["command"])

    def test_the_brief_command_also_moves_to_the_next_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            one_beat_brief(tmp, ["commons", "nasa"])
            with patch.object(providers, "search", return_value=[]):
                run(status_do(tmp)["command"])
            beat = cli.main(["brief", "--project", tmp])["beats"][0]
            self.assertIn("--provider nasa", beat["commands"]["search"])


if __name__ == "__main__":
    unittest.main()
