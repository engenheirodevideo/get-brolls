"""Comando sugerido que roda: a CLI desta instalação no checkout, no pacote e no Windows."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT, command_text, split_command, suggested_argv
from test_guidance import ABSOLUTE, LADDER_STATES
from test_sdk_loader import LoaderTestCase

from getbrolls import _paths, brief, guidance, serve, social
from getbrolls.cli import build_parser
from getbrolls.sdk.api import PluginApi
from getbrolls.sdk.manifest import read_manifest
from getbrolls.sdk.registry import Registry

OURS = str(Path(sys.executable).parent / "getbrolls")
FOREIGN = "/x/bin/getbrolls"


def wheel_install():
    """Instalação de pacote fingida: só a origem importa para o prefixo da CLI."""
    package = Path(tempfile.gettempdir()) / "site-packages" / "getbrolls"
    return _paths.Install("wheel", package, package / "_data", None)


def command_for(step, project):
    """`guidance.command_for` de um degrau que sempre tem comando."""
    command = guidance.command_for(step, project)
    assert command is not None
    return command


def as_wheel(stack, which):
    """Faz o processo se ver como pacote instalado, com `getbrolls` no PATH ou não."""
    stack.enter_context(mock.patch.object(_paths, "install", return_value=wheel_install()))
    stack.enter_context(mock.patch.object(_paths.shutil, "which", return_value=which))


class CheckoutCommand(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "o prefixo da 2.5 é o do POSIX")
    def test_checkout_command_is_byte_identical_to_2_5(self):
        script = ROOT / "scripts" / "gb.py"
        self.assertEqual(
            f'python3 "{script}" verify --project /tmp/p',
            command_for("verify", "/tmp/p"),
        )


class EveryInstallRuns(unittest.TestCase):
    def test_every_rung_parses_in_checkout_and_wheel(self):
        parser = build_parser()
        for label, which in (("checkout", None), ("wheel-ours", OURS), ("wheel-foreign", FOREIGN), ("wheel", None)):
            with ExitStack() as stack:
                if label != "checkout":
                    as_wheel(stack, which)
                for step, state in LADDER_STATES.items():
                    action = guidance.next_action(state)
                    if action["command"] is None:
                        continue
                    with self.subTest(install=label, step=step):
                        parsed = parser.parse_args(suggested_argv(action["command"]))
                        self.assertEqual(ABSOLUTE, parsed.project)

    def test_wheel_prefix_names_the_console_script_only_when_it_is_ours(self):
        with ExitStack() as stack:
            as_wheel(stack, OURS)
            self.assertTrue(command_for("verify", "/tmp/p").startswith("getbrolls verify "))
        for which in (FOREIGN, None):
            with self.subTest(which=which), ExitStack() as stack:
                as_wheel(stack, which)
                command = command_for("verify", "/tmp/p")
                self.assertEqual(["-P", "-m", "getbrolls", "verify"], split_command(command)[1:5])

    def test_brief_beat_commands_use_the_same_prefix(self):
        beat = {
            "id": "abertura",
            "intent": "literal",
            "target": "ponte ao amanhecer",
            "narration": "a cidade acorda",
            "allowed_sources": ["commons"],
            "resolved": {},
        }
        with mock.patch.object(
            brief,
            "search_plan",
            return_value={"provider": "commons", "query": "ponte", "media_image": False, "note": None},
        ):
            for label, which in (("checkout", None), ("wheel", OURS)):
                with self.subTest(install=label), ExitStack() as stack:
                    if label != "checkout":
                        as_wheel(stack, which)
                    commands = brief.beat_commands("/tmp/p", beat)
                    self.assertTrue(commands["search"].startswith(_paths.cli_prefix_text() + " "), commands["search"])
                    parsed = build_parser().parse_args(suggested_argv(commands["search"]))
                    self.assertEqual("abertura", parsed.shot)


class ModuleText(unittest.TestCase):
    def test_instagram_pairs_text_isolates_the_package(self):
        self.assertEqual("scripts/getbrolls/instagram_pairs.py", social._instagram_pairs_command())  # pylint: disable=protected-access
        with ExitStack() as stack:
            as_wheel(stack, OURS)
            self.assertEqual("python -P -m getbrolls.instagram_pairs", social._instagram_pairs_command())  # pylint: disable=protected-access


class WindowsCommand(unittest.TestCase):
    def test_windows_command_is_double_quoted_and_round_trips(self):
        project = r"D:\Projetos Ana\projeto do video"
        text = command_text("verify", "--project", project, os_name="nt")
        self.assertIn(f'"{project}"', text)
        self.assertEqual(["verify", "--project", project], split_command(text, os_name="nt")[2:])
        with mock.patch.object(_paths, "_os_name", return_value="nt"):
            command = command_for("verify", project)
            self.assertTrue(command.startswith('python "'), command)
            self.assertNotIn("'", command)
            argv = split_command(command)
            self.assertEqual(["verify", "--project", project], argv[2:])


class _FinishedProcess:
    pid = 4242

    @staticmethod
    def poll():
        return 0

    def terminate(self):
        """Nada a encerrar: o processo fingido já saiu."""


class ServeBackground(unittest.TestCase):
    def spawn(self, stack):
        tmp = Path(tempfile.mkdtemp(prefix="gb-serve-argv-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "brolls").mkdir()
        (tmp / "brolls" / "review.html").write_text("<html></html>", encoding="utf-8")
        popen = stack.enter_context(mock.patch.object(serve.subprocess, "Popen", return_value=_FinishedProcess()))
        stack.enter_context(mock.patch.dict(os.environ, {"PYTHONPATH": "/antes"}))
        with self.assertRaises(ValueError):
            serve.start_background(tmp, port=0)
        return popen.call_args

    def test_serve_background_spawns_the_running_install(self):
        with ExitStack() as stack:
            call = self.spawn(stack)
            argv = call.args[0]
            self.assertEqual(_paths.cli_argv(), argv[: len(_paths.cli_argv())])
            self.assertEqual("serve", argv[len(_paths.cli_argv())])
            scripts = str(ROOT / "scripts")
            self.assertEqual(scripts + os.pathsep + "/antes", call.kwargs["env"]["PYTHONPATH"])

    def test_serve_background_from_the_package_drops_the_inherited_pythonpath(self):
        """No pacote, um `PYTHONPATH` herdado poria outro `getbrolls` na frente do instalado."""
        with ExitStack() as stack:
            as_wheel(stack, OURS)
            call = self.spawn(stack)
            argv = call.args[0]
            self.assertEqual([sys.executable, "-P", "-m", "getbrolls", "serve"], argv[:5])
            self.assertNotIn("PYTHONPATH", call.kwargs["env"])


class PluginApiCliArgv(LoaderTestCase):
    def test_plugin_api_cli_argv_runs_this_install(self):
        api = PluginApi(read_manifest(self.install()), Registry())
        argv = api.cli_argv()
        argv.append("lixo")
        self.assertNotIn("lixo", api.cli_argv())
        done = subprocess.run(
            [*api.cli_argv(), "--version"], capture_output=True, text=True, encoding="utf-8", check=False
        )
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
