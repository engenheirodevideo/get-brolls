"""O `event=op` de ponta a ponta: o log do `serve`, a rotação entre processos, a duração e o comando.

Cada teste roda o caso real (um processo da CLI ou dois gravadores ao mesmo tempo) que falhava
antes da correção correspondente.
"""

import contextlib
import errno
import json
import logging
import os
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _logs
from _listing import visible_names
from _paths import CLI_ARGV, ROOT, WHEEL_MODE, WHEEL_PYTHON

from getbrolls import logs, runtime

POSIX = os.name != "nt"


# Uma suíte rodada em segundo plano herda SIGINT ignorado, e um Python que nasce assim nunca vira o
# Ctrl+C em KeyboardInterrupt. O filho volta o SIGINT ao padrão do Python antes de rodar a CLI, sem
# `preexec_fn` (nenhum código Python entre o fork e o exec).
_SIGINT_CHILD = (
    "import runpy, signal; signal.signal(signal.SIGINT, signal.default_int_handler); "
    "runpy.run_module('getbrolls', run_name='__main__', alter_sys=True)"
)
FIRST_LINE_TIMEOUT_S = 60
# No Windows `Popen.send_signal` não entrega SIGINT: só SIGTERM, CTRL_C_EVENT e CTRL_BREAK_EVENT, e os dois eventos
# de console pedem um console que o processo e o filho compartilhem (o runner do CI roda sem ele).
NO_CHILD_SIGINT = "este sistema não entrega SIGINT a um processo filho (Windows: só CTRL_C_EVENT e CTRL_BREAK_EVENT)"


def _first_line(stream, timeout):
    """A primeira linha de `stream` em até `timeout` segundos, ou `None` se ela não vier."""
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(stream.readline()), daemon=True).start()
    try:
        return lines.get(timeout=timeout)
    except queue.Empty:
        return None


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve_foreground(test, env_text):
    """Roda `serve` em primeiro plano com `env_text` como `.env`, para com SIGINT e devolve o log (ou None)."""
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        project = work / "proj"
        (project / "brolls").mkdir(parents=True)
        (project / "brolls" / "review.html").write_text("<html></html>", encoding="utf-8")
        env_file = work / "test.env"
        env_file.write_text(env_text, encoding="utf-8")
        env = {**os.environ, "GB_ENV_FILE": str(env_file), "GB_HOME": str(work / "home")}
        env.pop("GB_LOG_LEVEL", None)
        if not WHEEL_MODE:
            env["PYTHONPATH"] = str(ROOT / "scripts")
        # No modo wheel o `getbrolls` é o pacote instalado na venv do wheel (`-P`: a pasta atual fica fora do
        # `sys.path`), não o do Python que roda a suíte.
        python = (str(WHEEL_PYTHON), "-P") if WHEEL_MODE else (sys.executable,)
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [*python, "-c", _SIGINT_CHILD, "serve", "--project", str(project), "--port", str(_free_port())],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        collected = None
        try:
            assert proc.stdout is not None
            first = _first_line(proc.stdout, FIRST_LINE_TIMEOUT_S)
            test.assertIsNotNone(first, "o servidor não imprimiu a linha com a porta a tempo")
            if '"port"' not in (first or ""):
                # A saída inteira, não só a linha `Traceback`: o erro que importa está nas linhas seguintes.
                proc.kill()
                collected = proc.communicate()[0]
                test.fail(f"a primeira linha não traz a porta:\n{first}{collected}")
        finally:
            if collected is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.communicate(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise
        log = project / "brolls" / "getbrolls.log"
        return log.read_text(encoding="utf-8") if log.exists() else None


@unittest.skipUnless(POSIX, NO_CHILD_SIGINT)
class ForegroundServeHonoursTheEnvTests(unittest.TestCase):
    """o `serve` em primeiro plano lê o `.env` (e o perfil) antes de ligar o log."""

    def serve(self, env_text):
        return serve_foreground(self, env_text)

    def test_log_level_off_in_the_env_file_writes_no_log(self):
        self.assertIsNone(self.serve("GB_LOG_LEVEL=OFF\n"))

    def test_without_it_the_server_lifecycle_is_logged(self):
        text = self.serve("")
        self.assertIsNotNone(text)
        self.assertIn("operation=server.start", text or "")


RACE_WRITER = """
import sys
sys.path.insert(0, sys.argv[1])
from getbrolls import effects, logs
logs.MAX_BYTES = 6000
logs.BACKUP_COUNT = 3000
logs.configure(sys.argv[2], read_only=False)
for index in range(int(sys.argv[4])):
    with effects.op("fs.write", f"{sys.argv[3]}-{index}"):
        pass
logs.shutdown()
"""


@unittest.skipUnless(POSIX, "rotação entre processos no POSIX; no Windows o arquivo aberto não gira")
class ProjectLogRotationTests(unittest.TestCase):
    """o servidor do `serve` grava o log do projeto sem a trava do projeto; nenhuma linha se perde."""

    def test_two_writers_rotating_the_project_log_lose_no_line(self):
        lines = 600
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            (project / "brolls").mkdir()
            env = {**os.environ, "GB_HOME": str(project / "home"), "GB_LOG_LEVEL": "INFO", "GB_LOG_STDERR": ""}
            writers = [
                subprocess.Popen(  # pylint: disable=consider-using-with
                    [sys.executable, "-c", RACE_WRITER, str(ROOT / "scripts"), str(project), who, str(lines)], env=env
                )
                for who in ("serve", "cmd")
            ]
            for writer in writers:
                self.assertEqual(0, writer.wait(timeout=300))
            seen = []
            for path in (project / "brolls").glob("getbrolls.log*"):
                seen += [row["target"] for row in _logs.events(path.read_text(encoding="utf-8").splitlines(), "op")]
        expected = sorted(f"{who}-{index}" for who in ("serve", "cmd") for index in range(lines))
        self.assertEqual(expected, sorted(seen))


class ProjectLogLockFailureTests(unittest.TestCase):
    """Uma pasta onde o `flock` não funciona (NFS sem trava, SMB) ainda gira o log; só a trava ocupada
    depois da espera deixa a linha sem girar."""

    def write(self, flock_error, lines=400):
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            (work / "brolls").mkdir()
            with (
                mock.patch.object(logs, "MAX_BYTES", 3000),
                mock.patch.object(logs, "ROTATION_LOCK_WAIT_S", 0.01),
                mock.patch.object(logs.fcntl, "flock", side_effect=flock_error),
            ):
                logs.configure(work, read_only=False)
                try:
                    for index in range(lines):
                        logs.event(logs.get("x"), logging.INFO, "linha", i=index, pad="x" * 40)
                finally:
                    logs.shutdown()
            names = sorted(path.name for path in (work / "brolls").glob("getbrolls.log*"))
            return names, (work / "brolls" / "getbrolls.log").stat().st_size

    @unittest.skipIf(logs.fcntl is None, "flock só no POSIX")
    def test_a_folder_that_cannot_be_locked_still_rotates(self):
        for name in ("ENOLCK", "ENOTSUP", "EBADF", "EINVAL"):
            with self.subTest(errno=name):
                names, size = self.write(OSError(getattr(errno, name), name))
                self.assertIn("getbrolls.log.1", names)
                self.assertLess(size, 3000)

    @unittest.skipIf(logs.fcntl is None, "flock só no POSIX")
    def test_a_lock_that_stays_busy_appends_without_rotating(self):
        names, size = self.write(BlockingIOError(errno.EAGAIN, "ocupada"), lines=200)
        self.assertEqual(["getbrolls.log"], names)
        self.assertGreater(size, 3000)

    @unittest.skipIf(logs.fcntl is None, "flock só no POSIX")
    def test_a_folder_that_does_not_open_for_the_lock_still_rotates(self):
        real = os.open

        def refuse_folder(path, flags, *args, **kwargs):
            if flags & getattr(os, "O_DIRECTORY", 0) and str(path).endswith("brolls"):
                raise PermissionError(errno.EACCES, "sem leitura")
            return real(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", refuse_folder):
            names, size = self.write(None)
        self.assertIn("getbrolls.log.1", names)
        self.assertLess(size, 3000)


class ProjectLogWithoutFlockTests(unittest.TestCase):
    """Sem `fcntl` (Windows), o log do projeto gira sob `brolls/.getbrolls.log.lock`, como o da instalação."""

    def write(self, lines, hold=False):
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            (work / "brolls").mkdir()
            with (
                mock.patch.object(logs, "fcntl", None),
                mock.patch.object(logs, "MAX_BYTES", 3000),
                mock.patch.object(logs, "ROTATION_LOCK_WAIT_S", 0.01),
                runtime.try_lock(work / "brolls" / logs.ROTATION_LOCK, 0) if hold else contextlib.nullcontext(),
            ):
                logs.configure(work, read_only=False)
                try:
                    for index in range(lines):
                        logs.event(logs.get("x"), logging.INFO, "linha", i=index, pad="x" * 40)
                finally:
                    logs.shutdown()
            folder = work / "brolls"
            return sorted(p.name for p in folder.glob("getbrolls.log*")), sorted(p.name for p in folder.iterdir())

    def test_rotation_runs_under_the_lock_file_next_to_the_log(self):
        backups, everything = self.write(400)
        self.assertIn("getbrolls.log.1", backups)
        self.assertIn(logs.ROTATION_LOCK, everything)
        self.assertNotIn(logs.ROTATION_LOCK, backups)
        self.assertNotRegex(logs.ROTATION_LOCK, r"^getbrolls\.log\.\d+$")

    def test_a_held_lock_appends_without_rotating(self):
        backups, _ = self.write(200, hold=True)
        self.assertEqual(["getbrolls.log"], backups)


class DoctorShowsTheLogSaltTests(unittest.TestCase):
    """O `doctor` diz o estado do sal do log (`install.log_salt`) sem criar nada."""

    def doctor(self, home):
        done = subprocess.run(
            [*CLI_ARGV, "doctor"],
            env={**os.environ, "GB_HOME": str(home)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=120,
        )
        return json.loads(done.stdout)["install"]["log_salt"]

    def test_each_state_and_nothing_created(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "home"
            self.assertEqual("missing", self.doctor(home))
            self.assertFalse((home / ".log-salt").exists())
            home.mkdir(exist_ok=True)
            (home / ".log-salt").write_text("torto\n", encoding="utf-8")
            self.assertEqual("malformed", self.doctor(home))
            (home / ".log-salt").write_text("ab" * 32 + "\n", encoding="utf-8")
            self.assertEqual("ok", self.doctor(home))


SALT_CHILD = """
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ["GB_HOME"] = sys.argv[2]
from getbrolls import effects
print(effects.redact_target("Cliente Acme"))
"""


class MalformedSaltRepairRaceTests(unittest.TestCase):
    """Muitos processos consertando o mesmo sal malformado ao mesmo tempo terminam com o mesmo sal."""

    PROCESSES = 12
    ROUNDS = 8

    def test_every_repairer_ends_with_the_salt_that_persisted(self):
        for round_number in range(self.ROUNDS):
            with self.subTest(round=round_number), tempfile.TemporaryDirectory() as raw:
                home = Path(raw)
                (home / ".log-salt").write_text("zz-not-hex\n", encoding="ascii")
                children = [
                    subprocess.Popen(  # pylint: disable=consider-using-with
                        [sys.executable, "-c", SALT_CHILD, str(ROOT / "scripts"), str(home)],
                        stdout=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                    )
                    for _ in range(self.PROCESSES)
                ]
                hashes = {child.communicate(timeout=120)[0].strip() for child in children}
                self.assertEqual(1, len(hashes), hashes)
                self.assertEqual([".log-salt"], visible_names(home))
                self.assertRegex((home / ".log-salt").read_text(encoding="ascii").strip(), r"^[0-9a-f]{64}$")


class DurationAndCommandTests(unittest.TestCase):
    """toda linha que mede tempo traz `duration_ms`; as linhas do `serve` dizem `command=serve`."""

    def test_every_measured_time_also_comes_as_duration_ms(self):
        with self.assertLogs("getbrolls", level="INFO") as captured:
            logs.event(logs.get("x"), logging.INFO, "a", ms=7)
            logs.event(logs.get("x"), logging.INFO, "b", lock=".command.lock", waited_ms=12)
            logs.event(logs.get("x"), logging.INFO, "c", count=1)
        rows = _logs.Rows(captured).all()
        self.assertEqual(["7", "12", None], [row.get("duration_ms") for row in rows])
        self.assertEqual("12", rows[1]["waited_ms"])

    @unittest.skipUnless(POSIX, NO_CHILD_SIGINT)
    def test_the_lines_of_the_foreground_server_name_the_serve_command(self):
        text = serve_foreground(self, "")
        rows = _logs.events((text or "").splitlines(), "op")
        self.assertTrue(rows)
        self.assertEqual({"serve"}, {row["command"] for row in rows})


class TimedReservedFieldsTests(unittest.TestCase):
    """`logs.timed` com um campo que ele mesmo grava recusa com clareza, nunca `TypeError`."""

    def test_a_reserved_field_is_refused_before_the_block_runs(self):
        for field in ("ms", "duration_ms", "outcome", "error", "error_code"):
            ran = []
            with (
                self.subTest(field=field),
                self.assertRaises(ValueError) as caught,
                logs.timed(logs.get("x"), "passo", **{field: 1}),
            ):
                ran.append(True)
            self.assertIn(field, str(caught.exception))
            self.assertEqual([], ran)

    def test_other_fields_still_go_through(self):
        with self.assertLogs("getbrolls", level="INFO") as captured, logs.timed(logs.get("x"), "passo", count=2):
            pass
        row = _logs.Rows(captured).one("passo")
        self.assertEqual(("2", "ok"), (row["count"], row["outcome"]))


if __name__ == "__main__":
    unittest.main()
