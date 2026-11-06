"""Log do app da instalação: `$GB_HOME/logs/getbrolls.log` para quem muda a instalação sem projeto.

Espelha o log do projeto (`brolls/getbrolls.log`): mesmo handler privado e rotativo, mesma
redação, mesma linha, e a mesma regra de leitura — comando que só lê nunca cria o arquivo.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import CLI_ARGV  # insere scripts/ em sys.path como efeito de import

from getbrolls import logs, runtime
from getbrolls.sdk import cli as plugins_cli


def _args(command, **values):
    return argparse.Namespace(command=command, **values)


def _raw_run(*args, env=None):
    environment = dict(os.environ)
    environment.update(env or {})
    return subprocess.run(
        [*CLI_ARGV, *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


class HomeScratch(unittest.TestCase):
    """Cada teste ganha um `GB_HOME` só dele, para ler o que a CLI deixou nele."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gb-home-log-")).resolve()
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.env = {"GB_HOME": str(self.home)}

    @property
    def log(self):
        return self.home / "logs" / "getbrolls.log"

    def text(self):
        return self.log.read_text(encoding="utf-8")


class LogLocationTests(HomeScratch):
    def test_a_command_that_changes_the_installation_writes_the_log_under_gb_home(self):
        run_cli("plugins", "--action", "enable", "--id", "ghost", env=self.env, expect=1)
        self.assertTrue(self.log.is_file())
        text = self.text()
        self.assertIn("event=command_start", text)
        self.assertIn("command=plugins", text)
        self.assertIn("event=command_end", text)
        self.assertIn("status=error", text)

    def test_the_line_format_is_the_same_as_the_project_log(self):
        run_cli("plugins", "--action", "enable", "--id", "ghost", env=self.env, expect=1)
        first = self.text().splitlines()[0]
        self.assertRegex(first, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z level=INFO logger=getbrolls\.cli event=")

    def test_the_error_envelope_points_at_the_installation_log(self):
        failed = run_cli("plugins", "--action", "enable", "--id", "ghost", env=self.env, expect=1)
        self.assertEqual(str(self.log), failed["app_log"])
        self.assertEqual(str(self.home / "diagnostics.jsonl"), failed["log"])

    def test_the_envelope_has_no_app_log_before_the_file_exists(self):
        failed = run_cli(
            "plugins", "--action", "enable", "--id", "ghost", env={**self.env, "GB_LOG_LEVEL": "off"}, expect=1
        )
        self.assertIsNone(failed["app_log"])

    def test_a_project_command_keeps_writing_the_project_log_and_nothing_under_gb_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_cli("init-rules", project=tmp, env=self.env)
            self.assertTrue((Path(tmp).resolve() / "brolls" / "getbrolls.log").is_file())
        self.assertFalse((self.home / "logs").exists())

    @unittest.skipIf(os.name == "nt", "permissões POSIX")
    def test_the_folder_is_private_and_the_file_is_0600(self):
        run_cli("plugins", "--action", "enable", "--id", "ghost", env=self.env, expect=1)
        self.assertEqual(0o700, (self.home / "logs").stat().st_mode & 0o777)
        self.assertEqual(0o600, self.log.stat().st_mode & 0o777)

    def test_gb_log_level_off_creates_nothing(self):
        run_cli("plugins", "--action", "enable", "--id", "ghost", env={**self.env, "GB_LOG_LEVEL": "off"}, expect=1)
        self.assertFalse((self.home / "logs").exists())

    def test_stdout_and_stderr_keep_the_contract(self):
        done = _raw_run("client", "--action", "remove", "--slug", "ninguem", env=self.env)
        self.assertEqual(1, done.returncode)
        self.assertEqual("", done.stdout)
        self.assertIn("error_code", json.loads(done.stderr))  # uma linha JSON só, nenhuma linha de log

    def test_the_stderr_mirror_still_prints_the_log_lines_before_the_envelope(self):
        done = _raw_run("client", "--action", "remove", "--slug", "ninguem", env={**self.env, "GB_LOG_STDERR": "1"})
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        self.assertGreater(len(lines), 1)
        self.assertIn("error_code", json.loads(lines[-1]))
        self.assertIn("event=command_start", lines[0])


class ReadOnlyTests(HomeScratch):
    def test_commands_that_only_read_never_create_the_log_or_its_folder(self):
        cases = (
            ("plugins", "--action", "list"),
            ("plugins", "--action", "marketplace-list"),
            ("plugins", "--action", "search", "--query", "demo"),
            ("client", "--action", "list"),
            ("recipe", "--action", "list"),
            ("profile", "--action", "show"),
            ("setup", "--where"),
            ("setup", "--check"),
            ("capabilities",),
            ("providers",),
            ("doctor",),
        )
        for case in cases:
            with self.subTest(command=" ".join(case)):
                done = _raw_run(*case, env={**self.env, "GB_LOG_LEVEL": "DEBUG"})
                self.assertIn(done.returncode, (0, 4), done.stderr)
                self.assertFalse((self.home / "logs").exists(), "criou o log de um comando de leitura")

    def test_a_failing_read_only_command_does_not_create_it_either(self):
        run_cli("client", "--action", "show", "--slug", "ninguem", env=self.env, expect=1)
        self.assertFalse((self.home / "logs").exists())


class WritesInstallationTests(unittest.TestCase):
    """Quais comandos sem projeto mudam a instalação (e portanto abrem o log dela)."""

    def test_the_writing_actions(self):
        writers = (
            _args("plugins", action="enable"),
            _args("plugins", action="disable"),
            _args("plugins", action="install"),
            _args("plugins", action="update"),
            _args("plugins", action="remove"),
            _args("plugins", action="marketplace-add"),
            _args("plugins", action="marketplace-update"),
            _args("plugins", action="marketplace-remove"),
            _args("client", action="add"),
            _args("client", action="remove"),
            _args("profile", action="trust", yes=True),
            _args("profile", action="untrust", yes=False),
            _args("setup", check=False, where=None, upgrade=None),
            _args("setup", check=False, where=None, upgrade="ytdlp"),
        )
        for args in writers:
            with self.subTest(args=vars(args)):
                self.assertTrue(runtime.writes_installation(args))

    def test_the_reading_actions(self):
        readers = (
            _args("plugins", action="list"),
            _args("plugins", action="check"),
            _args("plugins", action="new"),
            _args("plugins", action="search"),
            _args("plugins", action="marketplace-list"),
            _args("client", action="list"),
            _args("client", action="show"),
            _args("template", action="freeze"),
            _args("recipe", action="list"),
            _args("recipe", action="show"),
            _args("profile", action="show"),
            _args("profile", action="trust", yes=False),  # a prévia só lê o getbrolls.toml
            _args("setup", check=True, where=None, upgrade=None),
            _args("setup", check=False, where="all", upgrade=None),
            _args("doctor"),
            _args("capabilities"),
            _args("providers"),
            _args("x"),
            _args("status", project="p"),
            _args("search", project="p"),
        )
        for args in readers:
            with self.subTest(args=vars(args)):
                self.assertFalse(runtime.writes_installation(args))

    def test_every_writing_plugins_action_takes_the_plugins_lock(self):
        for action in sorted(plugins_cli.WRITING_ACTIONS):
            with self.subTest(action=action):
                self.assertTrue(runtime.writes_installation(_args("plugins", action=action)))


class ConfigureTests(HomeScratch):
    def tearDown(self):
        logs.shutdown()

    def configure(self, *, installation=True, read_only=True, project=None, level="DEBUG"):
        env = {"GB_HOME": str(self.home), "GB_LOG_LEVEL": level, "GB_LOG_STDERR": ""}
        with patch.dict(os.environ, env):
            logs.configure(project, read_only=read_only, installation=installation)

    def test_the_installation_handler_is_attached_even_when_the_project_is_read_only(self):
        """`profile trust --project P` e `setup` são 'só leitura' do projeto, mas gravam a instalação."""
        self.configure(project=str(self.home / "projeto"), read_only=True, installation=True)
        logs.event(logs.get("probe"), logging.INFO, "probe", n=1)
        self.assertIn("event=probe", self.text())
        self.assertFalse((self.home / "projeto").exists())

    def test_a_project_handler_wins_and_the_installation_log_stays_untouched(self):
        project = self.home / "projeto"
        self.configure(project=str(project), read_only=False, installation=True)
        logs.event(logs.get("probe"), logging.INFO, "probe", n=1)
        self.assertTrue((project / "brolls" / "getbrolls.log").is_file())
        self.assertFalse((self.home / "logs").exists())

    def test_without_the_flag_no_file_is_created(self):
        self.configure(installation=False, read_only=False)
        logs.event(logs.get("probe"), logging.INFO, "probe", n=1)
        self.assertFalse((self.home / "logs").exists())

    def enable(self, project, level="DEBUG"):
        with patch.dict(os.environ, {"GB_HOME": str(self.home), "GB_LOG_LEVEL": level, "GB_LOG_STDERR": ""}):
            logs.enable_project(str(project))

    def test_enable_project_turns_the_project_file_on_once_the_tree_exists(self):
        """`init` e `migrate` começam com o arquivo desligado e o ligam depois de criar `brolls/`."""
        project = self.home / "projeto"
        self.configure(project=str(project), read_only=True, installation=False)
        logs.event(logs.get("probe"), logging.INFO, "before")
        self.assertFalse((project / "brolls").exists())
        self.enable(project)
        logs.event(logs.get("probe"), logging.INFO, "after")
        self.enable(project)  # idempotente: não duplica o handler
        logs.event(logs.get("probe"), logging.INFO, "again")
        text = (project / "brolls" / "getbrolls.log").read_text(encoding="utf-8")
        self.assertNotIn("event=before", text)
        self.assertEqual(1, text.count("event=after"))
        self.assertEqual(1, text.count("event=again"))
        self.assertFalse((self.home / "logs").exists(), "o log da instalação não entra no meio")

    def test_enable_project_respects_gb_log_level_off(self):
        project = self.home / "projeto"
        self.configure(project=str(project), read_only=True, installation=False)
        self.enable(project, level="off")
        logs.event(logs.get("probe"), logging.INFO, "after")
        self.assertFalse((project / "brolls").exists())

    def test_the_level_filters_the_installation_log_too(self):
        self.configure(level="WARNING")
        logs.event(logs.get("probe"), logging.INFO, "quiet")
        logs.event(logs.get("probe"), logging.WARNING, "loud")
        text = self.text()
        self.assertIn("event=loud", text)
        self.assertNotIn("event=quiet", text)

    def test_secrets_and_the_home_folder_never_reach_the_file(self):
        self.configure()
        home = str(Path.home())
        logs.event(
            logs.get("probe"),
            logging.INFO,
            "probe",
            note="token=installation-secret https://example.org/x?sig=installation-sig",
            where=f"{home}/projeto",
        )
        text = self.text()
        for leaked in ("installation-secret", "installation-sig", "example.org", home):
            self.assertNotIn(leaked, text)

    def test_rotation_keeps_three_private_backups(self):
        with patch.object(logs, "MAX_BYTES", 400):
            self.configure()
            logger = logs.get("rotation")
            for index in range(500):
                logs.event(logger, logging.INFO, "filler", i=index, pad="x" * 40)
        folder = self.home / "logs"
        backups = [folder / f"getbrolls.log.{n}" for n in (1, 2, 3)]
        self.assertTrue(all(path.is_file() for path in backups), backups)
        self.assertFalse((folder / "getbrolls.log.4").exists())
        if os.name != "nt":
            for path in (self.log, *backups):
                self.assertEqual(0o600, path.stat().st_mode & 0o777, path.name)

    def test_shutdown_releases_the_file(self):
        self.configure()
        logs.event(logs.get("probe"), logging.INFO, "probe")
        logs.shutdown()
        self.assertEqual([], [h for h in logging.getLogger("getbrolls").handlers if isinstance(h, logging.FileHandler)])
        shutil.rmtree(self.home / "logs")  # no Windows, um arquivo aberto impediria

    def test_an_unwritable_home_never_raises(self):
        blocked = self.home / "arquivo"
        blocked.write_text("não é uma pasta", encoding="utf-8")
        with patch.dict(os.environ, {"GB_HOME": str(blocked), "GB_LOG_LEVEL": "DEBUG", "GB_LOG_STDERR": ""}):
            logs.configure(None, read_only=True, installation=True)  # não pode levantar
            logs.event(logs.get("probe"), logging.INFO, "probe")  # nem isto

    def test_configure_twice_in_another_home_follows_the_new_home(self):
        other = Path(tempfile.mkdtemp(prefix="gb-home-log-")).resolve()
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        self.configure()
        logs.event(logs.get("probe"), logging.INFO, "first")
        with patch.dict(os.environ, {"GB_HOME": str(other), "GB_LOG_LEVEL": "DEBUG", "GB_LOG_STDERR": ""}):
            logs.configure(None, read_only=True, installation=True)
            logs.event(logs.get("probe"), logging.INFO, "second")
        self.assertIn("event=first", self.text())
        self.assertNotIn("event=second", self.text())
        self.assertIn("event=second", (other / "logs" / "getbrolls.log").read_text(encoding="utf-8"))


class ReportedPathTests(HomeScratch):
    def test_log_path_is_none_until_the_file_exists_and_then_the_installation_log(self):
        with patch.dict(os.environ, self.env):
            self.assertIsNone(logs.log_path(None))
            run_cli("plugins", "--action", "enable", "--id", "ghost", env=self.env, expect=1)
            self.assertEqual(self.log, logs.log_path(None))

    def test_doctor_reports_where_the_log_goes(self):
        payload = run_cli("doctor", env=self.env)
        self.assertEqual(str(self.log), payload["install"]["app_log"])
        self.assertFalse(self.log.exists())  # só informa: doctor não cria nada

    def test_setup_where_reports_where_the_log_goes(self):
        payload = run_cli("setup", "--where", env=self.env)
        self.assertEqual(str(self.log), payload["app_log"])
        self.assertFalse(self.log.exists())

    def test_setup_where_for_one_part_keeps_its_shape(self):
        payload = run_cli("setup", "--where", "venv", env=self.env)
        self.assertNotIn("app_log", payload)

    def test_the_runtime_helper_agrees_with_logs(self):
        with patch.dict(os.environ, self.env):
            self.assertEqual(self.log, logs.home_log_path())


if __name__ == "__main__":
    unittest.main()
