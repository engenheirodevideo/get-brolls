"""O guarda de rede dos testes: yt-dlp de mentira por padrão e DNS recusado com `GB_TEST_OFFLINE=1`."""

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _offline
from _paths import ROOT

TESTS = ROOT / "tests"
# Os arquivos que resolviam URL do YouTube com o yt-dlp de verdade (ou do PATH) e chegavam à rede.
GUARDED_FILES = ("test_cli.py", "test_permit_presets.py", "test_inspect.py", "test_social_recovery.py")


def run_child(argv, env, timeout=600):
    """`(código, cauda da saída)` de um processo filho, sem quebrar com a saída que não é UTF-8.

    No Windows o filho escreve na página de código do console (cp1252), e a cauda só serve para o
    diagnóstico: o byte inválido vira U+FFFD em vez de derrubar a leitura e deixar `stdout` vazio."""
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
        check=False,
        timeout=timeout,
    )
    return done.returncode, ((done.stdout or "") + (done.stderr or ""))[-1500:]


def import_isolation(env):
    """O que `import _isolation` deixa em `GB_YTDLP_PATH` e `GB_YTDLP_SLEEP` num processo novo com `env` extra."""
    clean = {k: v for k, v in os.environ.items() if k not in ("GB_YTDLP_PATH", "GB_YTDLP_SLEEP", "GB_EVAL_NETWORK")}
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, _isolation; print(os.environ.get('GB_YTDLP_PATH')); print(os.environ.get('GB_YTDLP_SLEEP'))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**clean, "PYTHONPATH": str(TESTS), **env},
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


class OfflineYtDlpTests(unittest.TestCase):
    def test_the_stub_is_the_default_for_every_test_even_when_the_machine_has_a_real_yt_dlp(self):
        self.assertEqual([sys.executable, "0,0,0"], import_isolation({}))
        self.assertEqual([sys.executable, "0,0,0"], import_isolation({"GB_YTDLP_PATH": "/usr/local/bin/yt-dlp"}))

    def test_the_stub_is_exactly_the_one_of_the_offline_module(self):
        self.assertEqual(_offline.OFFLINE_YTDLP, {"GB_YTDLP_PATH": sys.executable, "GB_YTDLP_SLEEP": "0,0,0"})

    def test_the_network_run_opts_out(self):
        self.assertEqual(
            ["/usr/local/bin/yt-dlp", "1,2,3"],
            import_isolation(
                {"GB_EVAL_NETWORK": "1", "GB_YTDLP_PATH": "/usr/local/bin/yt-dlp", "GB_YTDLP_SLEEP": "1,2,3"}
            ),
        )
        self.assertEqual(["None", "None"], import_isolation({"GB_EVAL_NETWORK": "1"}))


class DnsGuardTests(unittest.TestCase):
    def setUp(self):
        self.real = Mock(return_value=[("lookup",)])
        self.enterContext(patch.object(socket, "getaddrinfo", self.real))
        # O ciclo de vida cobre o teste inteiro; a limpeza é feita por addCleanup.
        folder = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(folder.cleanup)
        self.log = Path(folder.name) / "dns.log"

    def test_a_name_is_refused_as_a_machine_without_network_would_and_is_logged(self):
        _offline.install_dns_guard(self.log)
        with self.assertRaises(socket.gaierror):
            socket.getaddrinfo("www.youtube.com", 443)
        with self.assertRaises(OSError):
            socket.getaddrinfo("media.example.test", 443, type=socket.SOCK_STREAM)
        self.real.assert_not_called()
        self.assertEqual(["www.youtube.com", "media.example.test"], self.log.read_text(encoding="utf-8").split())

    def test_loopback_and_address_literals_still_work_because_they_need_no_dns(self):
        _offline.install_dns_guard(self.log)
        for host in ("127.0.0.1", "localhost", "::1", "93.184.216.34"):
            with self.subTest(host=host):
                self.assertEqual([("lookup",)], socket.getaddrinfo(host, 80))
        self.assertFalse(self.log.exists())

    def test_the_guard_works_without_a_log_file(self):
        _offline.install_dns_guard(None)
        with self.assertRaises(socket.gaierror):
            socket.getaddrinfo("www.youtube.com", 443)


class RunChildTests(unittest.TestCase):
    """A leitura da saída do filho aguenta a página de código do console do Windows."""

    def test_a_child_writing_cp1252_bytes_keeps_its_code_and_a_readable_tail(self):
        script = "import sys; sys.stdout.buffer.write('ação OK\\n'.encode('cp1252')); sys.stderr.write('fim')"
        code, tail = run_child([sys.executable, "-c", script], dict(os.environ), timeout=60)
        self.assertEqual(0, code, tail)
        self.assertEqual("a\ufffd\ufffdo OK\nfim", tail)

    def test_a_failing_child_with_no_output_keeps_its_code(self):
        code, tail = run_child([sys.executable, "-c", "raise SystemExit(3)"], dict(os.environ), timeout=60)
        self.assertEqual((3, ""), (code, tail))

    def test_the_tail_keeps_only_the_last_characters(self):
        code, tail = run_child([sys.executable, "-c", "print('x' * 2000 + 'END')"], dict(os.environ), timeout=60)
        self.assertEqual(0, code)
        self.assertEqual(1500, len(tail))
        self.assertTrue(tail.endswith("END\n"))


class FourFilesOfflineTests(unittest.TestCase):
    """Os quatro arquivos passam com a rede "desligada" e nenhum deles pede um nome ao DNS."""

    def run_file(self, name, env):
        return run_child([sys.executable, "-m", "unittest", "discover", "-s", str(TESTS), "-p", name], env)

    def test_the_guarded_files_pass_with_dns_refused_and_never_look_a_name_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "dns.log"
            env = {
                **os.environ,
                "GB_TEST_OFFLINE": "1",
                "GB_TEST_OFFLINE_LOG": str(log),
                "GB_HOME": str(Path(tmp) / "h"),
            }
            env.pop("GB_EVAL_NETWORK", None)
            with ThreadPoolExecutor(max_workers=len(GUARDED_FILES)) as pool:
                results = list(pool.map(lambda name: self.run_file(name, env), GUARDED_FILES))
            for name, (code, tail) in zip(GUARDED_FILES, results, strict=True):
                with self.subTest(file=name):
                    self.assertEqual(0, code, tail)
                    self.assertIn("OK", tail)
            self.assertFalse(log.exists() and log.read_text(encoding="utf-8").strip(), "o DNS foi consultado")


if __name__ == "__main__":
    unittest.main()
