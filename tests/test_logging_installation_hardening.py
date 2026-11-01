"""O log da instalação não confia na pasta que encontra, e várias CLIs o escrevem ao mesmo tempo.

`$GB_HOME` e `logs/` só nascem com a primeira linha, os dois 0700. Um `logs/` que é link, não é
pasta ou é de outra pessoa é recusado: a linha cai e o comando segue igual. O arquivo abre sem
seguir link e a rotação só renomeia, nunca segue nem muda o modo de um link. Conferir se o
`app_log` existe nunca levanta, e a rotação entre processos não perde linha nem gira a mesma
geração duas vezes.
"""

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _logs import events
from _paths import CLI_ARGV, ROOT

from getbrolls import _paths, logs, runtime

# pylint: disable=protected-access
# O handler e a rotação são internos de `logs`; aqui eles são exercitados direto, como dois
# processos que escrevem o mesmo arquivo.

POSIX = os.name != "nt"
ROOT_USER = POSIX and os.geteuid() == 0


def _can_symlink(folder):
    probe = Path(folder) / "probe-link"
    try:
        probe.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def _raw_run(*args, env):
    return subprocess.run(
        [*CLI_ARGV, *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **env},
        check=False,
    )


def _envelope(done):
    """O JSON de erro: a última linha não vazia do stderr (as de antes são do espelho do log)."""
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    return json.loads(lines[-1])


class Scratch(unittest.TestCase):
    """Uma pasta só do teste, com o `GB_HOME` dentro dela (ainda não criado)."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-home-hardening-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.home = self.base / "gbhome"
        self.env = {"GB_HOME": str(self.home)}

    @property
    def folder(self):
        return self.home / "logs"

    @property
    def log(self):
        return self.folder / "getbrolls.log"

    def configure(self, **extra):
        env = {"GB_HOME": str(self.home), "GB_LOG_LEVEL": "DEBUG", "GB_LOG_STDERR": "", **extra}
        with patch.dict(os.environ, env):
            logs.configure(None, read_only=True, installation=True)
        self.addCleanup(logs.shutdown)

    def line(self, name="probe", **fields):
        logs.event(logs.get("probe"), logging.INFO, name, **fields)

    def refused_command(self, **extra):
        """Um comando que muda a instalação e falha do jeito de sempre (cliente que não existe)."""
        done = _raw_run("client", "--action", "remove", "--slug", "ninguem", env={**self.env, **extra})
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        envelope = _envelope(done)
        self.assertEqual("INVALID_DATA", envelope["error_code"])
        return envelope


class FreshHomeTests(Scratch):
    @unittest.skipUnless(POSIX, "permissões POSIX")
    def test_a_missing_gb_home_and_its_logs_folder_are_created_0700(self):
        previous = os.umask(0o022)
        try:
            run_cli("plugins", "--action", "enable", "--id", "ghost", env=self.env, expect=1)
        finally:
            os.umask(previous)
        self.assertEqual(0o700, stat.S_IMODE(self.home.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(self.folder.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(self.log.stat().st_mode))

    def test_nothing_is_created_before_the_first_line(self):
        self.configure()
        self.assertFalse(self.home.exists())
        self.line()
        self.assertIn("event=probe", self.log.read_text(encoding="utf-8"))

    def test_a_level_that_filters_every_line_creates_nothing(self):
        self.configure(GB_LOG_LEVEL="ERROR")
        self.line()
        self.assertFalse(self.home.exists())


class RefusedFolderTests(Scratch):
    def setUp(self):
        super().setUp()
        self.home.mkdir()
        self.outside = self.base / "fora"
        self.outside.mkdir()

    def test_a_logs_link_is_refused_and_nothing_is_written_through_it(self):
        if not _can_symlink(self.base):
            self.skipTest("este sistema não cria symlink")
        self.folder.symlink_to(self.outside, target_is_directory=True)
        envelope = self.refused_command()
        self.assertEqual([], list(self.outside.iterdir()), "o log escreveu fora do GB_HOME")
        self.assertIsNone(envelope["app_log"])

    def test_a_log_file_link_is_never_followed_nor_chmodded(self):
        if not _can_symlink(self.base):
            self.skipTest("este sistema não cria symlink")
        self.folder.mkdir(mode=0o700)
        victim = self.outside / "vitima.txt"
        victim.write_text("conteúdo da vítima\n", encoding="utf-8")
        victim.chmod(0o644)
        before = stat.S_IMODE(victim.stat().st_mode)
        self.log.symlink_to(victim)
        envelope = self.refused_command()
        self.assertEqual("conteúdo da vítima\n", victim.read_text(encoding="utf-8"))
        self.assertEqual(before, stat.S_IMODE(victim.stat().st_mode))
        self.assertIsNone(envelope["app_log"])

    @unittest.skipUnless(POSIX, "permissões POSIX")
    def test_an_existing_wider_logs_folder_is_tightened_to_0700(self):
        self.folder.mkdir()
        self.folder.chmod(0o755)
        self.refused_command()
        self.assertEqual(0o700, stat.S_IMODE(self.folder.stat().st_mode))
        self.assertIn("event=client_refused", self.log.read_text(encoding="utf-8"))

    def test_a_file_in_place_of_the_folder_never_breaks_the_command(self):
        self.folder.write_text("não é pasta", encoding="utf-8")
        envelope = self.refused_command()
        self.assertEqual("não é pasta", self.folder.read_text(encoding="utf-8"))
        self.assertIsNone(envelope["app_log"])

    @unittest.skipUnless(POSIX, "dono POSIX")
    def test_a_logs_folder_of_another_user_is_refused(self):
        self.folder.mkdir(mode=0o700)
        self.log.write_text("linha antiga\n", encoding="utf-8")
        someone_else = os.geteuid() + 1
        with patch.object(os, "geteuid", return_value=someone_else):
            self.configure()
            self.line()
            with patch.dict(os.environ, self.env):
                self.assertIsNone(logs.log_path(None))
        self.assertEqual("linha antiga\n", self.log.read_text(encoding="utf-8"))

    def test_a_backup_link_is_moved_by_the_rotation_never_followed(self):
        if not _can_symlink(self.base):
            self.skipTest("este sistema não cria symlink")
        self.folder.mkdir(mode=0o700)
        victim = self.outside / "vitima.txt"
        victim.write_text("conteúdo da vítima\n", encoding="utf-8")
        victim.chmod(0o644)
        before = stat.S_IMODE(victim.stat().st_mode)
        (self.folder / "getbrolls.log.1").symlink_to(victim)
        with patch.object(logs, "MAX_BYTES", 400):
            self.configure()
            for index in range(40):
                self.line("filler", i=index, pad="x" * 40)
        self.assertEqual("conteúdo da vítima\n", victim.read_text(encoding="utf-8"))
        self.assertEqual(before, stat.S_IMODE(victim.stat().st_mode))


def _handler(path, max_bytes=400):
    handler = logs._PrivateFileHandler(path, max_bytes=max_bytes, backup_count=3, private_home=True)
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _emit(handler, message):
    handler.handle(logging.makeLogRecord({"msg": message, "levelno": logging.INFO, "levelname": "INFO"}))


class RotationTests(Scratch):
    def setUp(self):
        super().setUp()
        self.folder.mkdir(parents=True, mode=0o700)
        for index in (1, 2, 3):
            (self.folder / f"getbrolls.log.{index}").write_text(f"geração {index}\n", encoding="utf-8")
        self.log.write_text("atual\n", encoding="utf-8")

    def backup(self, index):
        return (self.folder / f"getbrolls.log.{index}").read_text(encoding="utf-8")

    def lines(self):
        return self.log.read_text(encoding="utf-8").splitlines()

    def test_a_rotation_that_cannot_move_the_active_file_keeps_every_line_and_every_backup(self):
        """No Windows, renomear um arquivo aberto por outro programa falha: nada gira e nada cai."""
        original = Path.replace

        def busy(path, target):
            if path.name == "getbrolls.log":
                raise PermissionError(13, "em uso por outro processo")
            return original(path, target)

        handler = _handler(self.log)
        with patch.object(Path, "replace", busy):
            for index in range(20):
                _emit(handler, f"linha {index} " + "x" * 40)
        self.assertEqual(["atual", *(f"linha {index} " + "x" * 40 for index in range(20))], self.lines())
        self.assertEqual(["geração 1\n", "geração 2\n", "geração 3\n"], [self.backup(n) for n in (1, 2, 3)])
        self.assertFalse((self.folder / "getbrolls.log.rotating").exists())

    def test_a_writer_follows_a_rotation_made_by_another_one(self):
        """Dois handlers no mesmo arquivo são dois processos: nenhum escreve no backup do outro."""
        first, second = _handler(self.log), _handler(self.log)
        _emit(second, "b-antes")
        index = 0
        while self.backup(1) == "geração 1\n":
            _emit(first, f"a{index} " + "x" * 40)
            index += 1
        rotated = self.backup(1)
        _emit(second, "b-depois")
        self.assertIn("b-depois", self.lines())
        self.assertEqual(rotated, self.backup(1), "o segundo escreveu no arquivo já girado")
        self.assertIn("b-antes", rotated)
        self.assertEqual(("geração 1\n", "geração 2\n"), (self.backup(2), self.backup(3)))
        self.assertFalse((self.folder / "getbrolls.log.4").exists())

    def test_no_rotation_happens_without_the_lock_and_no_line_is_dropped(self):
        handler = _handler(self.log)
        with (
            patch.object(logs, "ROTATION_LOCK_WAIT_S", 0.05),
            runtime.try_lock(self.folder / logs.ROTATION_LOCK, 0) as held,
        ):
            self.assertTrue(held)
            for index in range(20):
                _emit(handler, f"travado {index} " + "x" * 40)
        self.assertEqual(20, sum(line.startswith("travado") for line in self.lines()))
        self.assertEqual("geração 1\n", self.backup(1), "girou sem a trava")
        _emit(handler, "livre")
        self.assertEqual(["livre"], self.lines())
        self.assertIn("travado 19", self.backup(1))

    def test_writers_in_separate_processes_lose_no_line(self):
        script = textwrap.dedent(
            """
            import logging, sys
            sys.path.insert(0, sys.argv[1])
            from getbrolls import logs
            logs.MAX_BYTES = 6000
            logs.configure(None, read_only=True, installation=True)
            for index in range(30):
                logs.event(logs.get("probe"), logging.INFO, "line", p=sys.argv[2], i=index, pad="x" * 60)
            logs.shutdown()
            """
        )
        env = {**os.environ, "GB_HOME": str(self.home), "GB_LOG_LEVEL": "INFO", "GB_LOG_STDERR": ""}
        workers = [
            subprocess.Popen(  # pylint: disable=consider-using-with
                [sys.executable, "-c", script, str(ROOT / "scripts"), str(n)], env=env
            )
            for n in range(4)
        ]
        for worker in workers:
            self.assertEqual(0, worker.wait(timeout=120))
        files = [path for path in (self.log, *(self.folder / f"getbrolls.log.{n}" for n in (1, 2, 3))) if path.exists()]
        rows = events([line for path in files for line in path.read_text(encoding="utf-8").splitlines()], "line")
        seen = sorted((row["p"], int(row["i"])) for row in rows)
        self.assertEqual(sorted((str(p), i) for p in range(4) for i in range(30)), seen)
        self.assertFalse(self.backup(1).startswith("geração"), "nenhuma rotação aconteceu")
        self.assertFalse((self.folder / "getbrolls.log.4").exists())
        self.assertFalse((self.folder / "getbrolls.log.rotating").exists())


class ExistenceCheckTests(Scratch):
    def test_log_path_never_raises(self):
        def denied(*_args, **_kwargs):
            raise PermissionError(13, "sem permissão")

        # Python 3.11 e 3.12: `Path.is_file()` relança EACCES; o `stat` negado não pode virar traceback.
        with patch.dict(os.environ, self.env), patch.object(os, "lstat", denied), patch.object(os, "stat", denied):
            self.assertIsNone(logs.log_path(None))
            self.assertIsNone(logs.log_path(str(self.base)))

    def test_a_log_file_link_is_not_reported(self):
        if not _can_symlink(self.base):
            self.skipTest("este sistema não cria symlink")
        self.folder.mkdir(parents=True, mode=0o700)
        target = self.base / "outro.log"
        target.write_text("x\n", encoding="utf-8")
        self.log.symlink_to(target)
        with patch.dict(os.environ, self.env):
            self.assertIsNone(logs.log_path(None))

    def tighten(self, path):
        path.chmod(0)
        self.addCleanup(path.chmod, 0o700)

    @unittest.skipUnless(POSIX and not ROOT_USER, "permissões POSIX, sem root")
    def test_the_strict_checks_raise_on_an_unreadable_folder_in_every_python(self):
        # Desde o 3.14, `Path.exists()`/`is_dir()`/`is_file()` dizem `False` ao EACCES; as
        # conferências estritas levantam como no 3.11, e ausência de verdade segue `False`.
        locked = self.base / "trancada"
        locked.mkdir()
        self.tighten(locked)
        for check in (_paths.strict_exists, _paths.strict_is_dir, _paths.strict_is_file):
            with self.subTest(check=check.__name__):
                with self.assertRaises(PermissionError):
                    check(locked / "x")
                self.assertFalse(check(self.base / "nao-existe" / "x"))
                self.assertFalse(check(self.base / "x\0y"))

    @unittest.skipUnless(POSIX and not ROOT_USER, "permissões POSIX, sem root")
    def test_an_unreadable_logs_folder_still_ends_in_the_json_envelope(self):
        self.folder.mkdir(parents=True, mode=0o700)
        self.tighten(self.folder)
        for args in (
            ("client", "--action", "show", "--slug", "ninguem"),
            ("client", "--action", "remove", "--slug", "x"),
        ):
            with self.subTest(args=args):
                done = _raw_run(*args, env=self.env)
                self.assertEqual(1, done.returncode, done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                envelope = _envelope(done)
                self.assertEqual("INVALID_DATA", envelope["error_code"])
                self.assertIsNone(envelope["app_log"])

    @unittest.skipUnless(POSIX and not ROOT_USER, "permissões POSIX, sem root")
    def test_an_unreadable_gb_home_still_ends_in_the_json_envelope(self):
        self.home.mkdir()
        self.tighten(self.home)
        done = _raw_run("plugins", "--action", "remove", "--id", "nope", env=self.env)
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        envelope = _envelope(done)
        # A recusa sai antes do log (nem o `.env` de $GB_HOME dá para conferir), como E/S da
        # pessoa que não é o `.env`: o envelope antecipado, sem `app_log`.
        self.assertEqual("IO_ERROR", envelope["error_code"])
        self.assertIn("$GB_HOME/.env (", envelope["error"])
        self.assertNotIn(str(self.home), envelope["error"])
        self.assertNotIn("app_log", envelope)

    @unittest.skipUnless(POSIX and not ROOT_USER, "permissões POSIX, sem root")
    def test_an_unreadable_project_folder_still_ends_in_one_json_envelope(self):
        # Python 3.11 e 3.12: `Path.is_dir()` relança EACCES. Uma pasta de projeto sem
        # permissão é erro de E/S da pessoa: nem a recusa do comando de etapa, nem o destino
        # do diagnóstico, nem o log da auditoria podem virar traceback.
        project = self.base / "trancada"
        project.mkdir()
        self.tighten(project)
        tree = self.base / "arvore-trancada" / "brolls"
        tree.mkdir(parents=True)
        self.tighten(tree)
        for folder in (project, project / "p", tree.parent):
            for command in ("status", "references", "review", "deliver", "approve", "init"):
                with self.subTest(command=command, folder=folder.name):
                    args = ("--candidate", "x", "--by", "Ana Teste") if command == "approve" else ()
                    done = _raw_run(command, *args, "--project", folder, env=self.env)
                    self.assertNotIn("Traceback", done.stderr)
                    self.assertEqual(1, done.returncode, done.stderr)
                    self.assertEqual("IO_ERROR", _envelope(done)["error_code"])


if __name__ == "__main__":
    unittest.main()
