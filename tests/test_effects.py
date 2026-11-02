"""`getbrolls.effects`: uma linha `event=op` por operação, com as chaves do esquema, o `error_code`
da CLI, o alvo redigido, e nunca derrubando a operação quando o log falha.

O emissor (`logs.event`) é trocado por um gravador: os testes conferem os campos que ele
recebeu, nunca o texto de uma linha.
"""

import contextlib
import errno
import hashlib
import http.server
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths as _test_paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from _fake_secrets import joined
from _listing import visible_names
from _override import override

from getbrolls import delivery, delivery_files, delivery_sweep, effects, effects_salt, logs, runtime
from getbrolls.bootstrap import StepResult, _StepFailedError
from getbrolls.errors import LockedError, PrerequisiteError, UsageError
from getbrolls.instagram_pairs import CollectError
from getbrolls.sdk.safe_copy import UnsafeFileError

# pylint: disable=protected-access

POSIX = os.name != "nt"


class Recorder:
    """Faz as vezes de `logs.event`: guarda `(nível, evento, campos)` de cada chamada."""

    def __init__(self):
        self.calls = []

    def __call__(self, _logger, level, name, **fields):
        self.calls.append((level, name, fields))

    def ops(self):
        return [fields for _level, name, fields in self.calls if name == "op"]

    def one(self):
        found = self.ops()
        if len(found) != 1:
            raise AssertionError(f"esperava 1 linha op, achei {len(found)}: {found}")
        return found[0]


class EffectsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-effects-")).resolve()
        self.addCleanup(_rmtree_quiet, self.tmp)
        self.recorder = Recorder()
        patcher = mock.patch.object(logs, "event", self.recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        # O logger do effects responde que quer a linha mesmo sem handler configurado.
        enabled = mock.patch.object(effects._logger, "isEnabledFor", return_value=True)
        enabled.start()
        self.addCleanup(enabled.stop)
        # Sem projeto configurado por outro teste: os caminhos do tmp não viram relativos a ele.
        state = mock.patch.dict(logs._state, {"project": None, "command": None})
        state.start()
        self.addCleanup(state.stop)


def target_of(value):
    """`effects.redact_target(value)`, que para um caminho, URL ou comando nunca é `None`."""
    text = effects.redact_target(value)
    if text is None:
        raise AssertionError(f"sem alvo para {value!r}")
    return text


def _rmtree_quiet(path):
    with contextlib.suppress(OSError):
        for child in sorted(Path(path).rglob("*"), reverse=True):
            if child.is_dir() and not child.is_symlink():
                child.rmdir()
            else:
                child.unlink()
        Path(path).rmdir()


class OneLinePerOperationTests(EffectsCase):
    def test_a_successful_operation_logs_one_line_with_every_required_key(self):
        target = self.tmp / "a.txt"
        effects.write_text(target, "olá", encoding="utf-8")
        row = self.recorder.one()
        self.assertEqual(list(effects.FIELDS), list(row)[: len(effects.FIELDS)])
        self.assertEqual(
            ("fs.write", "ok", None, None), (row["operation"], row["outcome"], row["error_code"], row["error"])
        )
        self.assertIsInstance(row["duration_ms"], int)
        self.assertNotIn("bytes", row)  # o tamanho de um arquivo de estado não vai para a linha
        self.assertEqual("olá", target.read_text(encoding="utf-8"))

    def test_info_on_success_and_warning_on_failure(self):
        effects.mkdir(self.tmp / "nova")
        with self.assertRaises(FileNotFoundError):
            effects.rmdir(self.tmp / "nao-existe")
        levels = [level for level, name, _fields in self.recorder.calls if name == "op"]
        self.assertEqual([20, 30], levels)

    def test_each_helper_logs_exactly_its_own_operation(self):
        source = self.tmp / "src.txt"
        source.write_text("x", encoding="utf-8")
        steps = {
            "fs.copy": lambda: effects.copy_file(source, self.tmp / "copia.txt"),
            "fs.replace": lambda: effects.replace(self.tmp / "copia.txt", self.tmp / "trocada.txt"),
            "fs.rename": lambda: effects.rename(self.tmp / "trocada.txt", self.tmp / "renomeada.txt"),
            "fs.link": lambda: effects.link(source, self.tmp / "link.txt"),
            "fs.unlink": lambda: effects.unlink(self.tmp / "link.txt"),
            "fs.mkdir": lambda: effects.mkdir(self.tmp / "pasta" / "funda", parents=True),
            "fs.rmdir": lambda: effects.rmdir(self.tmp / "pasta" / "funda"),
            "fs.rmtree": lambda: effects.rmtree(self.tmp / "pasta"),
            "proc.run": lambda: effects.run([sys.executable, "-c", "pass"], check=True),
        }
        for operation, step in steps.items():
            with self.subTest(operation=operation):
                self.recorder.calls.clear()
                step()
                self.assertEqual(operation, self.recorder.one()["operation"])

    def test_a_checked_process_that_fails_still_logs_its_exit_code(self):
        with self.assertRaises(subprocess.CalledProcessError):
            effects.run([sys.executable, "-c", "raise SystemExit(5)"], check=True)
        row = self.recorder.one()
        self.assertEqual(
            ("error", "INVALID_DATA", "CalledProcessError", 5),
            (row["outcome"], row["error_code"], row["error"], row["exit"]),
        )

    def test_a_process_line_carries_its_exit_code_and_still_counts_as_ran(self):
        effects.run([sys.executable, "-c", "raise SystemExit(3)"], check=False)
        row = self.recorder.one()
        self.assertEqual(("proc.run", "ok", 3), (row["operation"], row["outcome"], row["exit"]))

    def test_an_atomic_write_is_one_line_for_the_final_name(self):
        path = self.tmp / "brolls" / "manifest.json"
        path.parent.mkdir()
        with mock.patch.dict(logs._state, {"project": self.tmp}):
            effects.write_atomic(path, "{}\n", unique=True)
        row = self.recorder.one()
        self.assertEqual(("fs.write", "brolls/manifest.json"), (row["operation"], row["target"]))
        self.assertEqual(["manifest.json"], [p.name for p in path.parent.iterdir()])

    def test_nothing_to_do_logs_nothing(self):
        existing = self.tmp / "ja-existe"
        existing.mkdir()
        effects.mkdir(existing, exist_ok=True)
        effects.unlink(self.tmp / "sumiu", missing_ok=True)
        effects.rmtree(self.tmp / "nunca-houve", ignore_errors=True)
        with self.assertRaises(FileExistsError):
            effects.mkdir(existing)
        with self.assertRaises(FileNotFoundError):
            effects.unlink(self.tmp / "sumiu")
        self.assertEqual([], self.recorder.ops())

    def test_a_batch_folds_its_nested_operations_into_one_line_with_a_count(self):
        with effects.batch("ledger.commit", self.tmp / "manifest.json"):
            for name in ("a", "b", "c"):
                effects.write_atomic(self.tmp / f"{name}.json", "{}")
            effects.unlink(self.tmp / "a.json")
        row = self.recorder.one()
        self.assertEqual(("ledger.commit", 4), (row["operation"], row["count"]))

    def test_a_failure_inside_a_batch_is_never_silent_even_when_the_caller_suppresses_it(self):
        folder = self.tmp / "brolls"
        folder.mkdir()
        with effects.batch("fs.rmtree", folder):
            effects.write_atomic(folder / "a.json", "{}")
            with contextlib.suppress(OSError):
                effects.unlink(folder)  # uma pasta: unlink falha
        rows = self.recorder.ops()
        self.assertEqual(["fs.unlink", "fs.rmtree"], [row["operation"] for row in rows])
        self.assertEqual("error", rows[0]["outcome"])
        self.assertEqual(30, next(level for level, name, _ in self.recorder.calls if name == "op"))
        self.assertEqual((1, 1), (rows[1]["count"], rows[1]["errors"]))

    def test_an_expected_failure_before_a_fallback_is_an_info_skip_with_its_reason(self):
        with self.assertRaises(FileExistsError), effects.op("fs.copy", self.tmp / "x", fallback=(FileExistsError,)):
            raise FileExistsError(errno.EEXIST, "existe")
        with self.assertRaises(OSError), effects.op("fs.rename", self.tmp / "y", fallback=(errno.ENOTEMPTY,)):
            raise OSError(errno.ENOTEMPTY, "cheia")
        with self.assertRaises(PermissionError), effects.op("fs.unlink", self.tmp / "z", fallback=(FileExistsError,)):
            raise PermissionError(errno.EACCES, "negado")
        rows = self.recorder.ops()
        self.assertEqual(
            [("skipped", "eexist", None), ("skipped", "enotempty", None), ("error", None, "IO_ERROR")],
            [(row["outcome"], row.get("fallback"), row["error_code"]) for row in rows],
        )
        self.assertEqual([20, 20, 30], [level for level, name, _ in self.recorder.calls if name == "op"])

    def test_a_hardlink_across_volumes_falls_back_to_a_copy_without_an_error_line(self):
        source = self.tmp / "clip.mp4"
        source.write_bytes(b"video")
        with mock.patch.object(os, "link", side_effect=OSError(errno.EXDEV, "outro volume")):
            method = delivery.link_or_copy(source, self.tmp / "entrega" / "clip.mp4", allow_symlink=False)
        self.assertEqual("copy", method)
        rows = self.recorder.ops()
        self.assertEqual(
            ("fs.link", "skipped", "exdev"), (rows[1]["operation"], rows[1]["outcome"], rows[1]["fallback"])
        )
        self.assertFalse([row for row in rows if row["outcome"] == "error"], rows)

    def test_a_thaw_retry_is_not_an_error(self):
        path = self.tmp / "congelado.mp4"
        path.write_bytes(b"x")
        real = Path.unlink
        calls = []

        def windows_like(target, missing_ok=False):
            calls.append(target)
            if len(calls) == 1:
                raise PermissionError(errno.EACCES, "somente leitura")
            return real(target, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", windows_like):
            delivery_sweep.thaw_unlink(path)
        rows = self.recorder.ops()
        self.assertEqual([("skipped", "eacces"), ("ok", None)], [(row["outcome"], row.get("fallback")) for row in rows])

    def test_a_thaw_whose_chmod_fails_still_logs_an_error(self):
        path = self.tmp / "congelado.mp4"
        path.write_bytes(b"x")
        with (
            mock.patch.object(Path, "unlink", side_effect=PermissionError(errno.EACCES, "somente leitura")),
            mock.patch.object(Path, "chmod", side_effect=PermissionError(errno.EPERM, "não é seu")),
            self.assertRaises(PermissionError),
        ):
            delivery_sweep.thaw_unlink(path)
        rows = self.recorder.ops()
        self.assertEqual(
            [("skipped", "eacces"), ("error", None)], [(row["outcome"], row.get("fallback")) for row in rows]
        )

    def test_a_real_permission_error_when_taking_the_name_is_an_error(self):
        folder = self.tmp / "entrega"
        folder.mkdir()
        part = folder / "clip.mp4.part"
        part.write_bytes(b"x")
        with (
            mock.patch.object(Path, "replace", side_effect=PermissionError(errno.EACCES, "pasta trancada")),
            self.assertRaises(PermissionError),
        ):
            delivery_files._take_name(part, folder / "clip.mp4", linked=self.tmp / "src.mp4")
        row = self.recorder.one()
        self.assertEqual(("fs.replace", "error", "IO_ERROR"), (row["operation"], row["outcome"], row["error_code"]))

    def test_the_windows_retry_when_taking_the_name_is_a_skip_then_ok(self):
        folder = self.tmp / "entrega"
        folder.mkdir()
        part, dest, source = folder / "clip.mp4.part", folder / "clip.mp4", self.tmp / "src.mp4"
        part.write_bytes(b"novo")
        source.write_bytes(b"velho")
        dest.hardlink_to(source)
        real = Path.replace
        calls = []

        def windows_like(path, target):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError(errno.EACCES, "somente leitura")
            return real(path, target)

        with mock.patch.object(Path, "replace", windows_like):
            delivery_files._take_name(part, dest, linked=source)
        rows = [row for row in self.recorder.ops() if row["operation"] == "fs.replace"]
        self.assertEqual([("skipped", "eacces"), ("ok", None)], [(row["outcome"], row.get("fallback")) for row in rows])
        self.assertEqual(b"novo", dest.read_bytes())

    def test_a_link_that_fails_for_an_unexpected_reason_is_an_error(self):
        source = self.tmp / "clip.mp4"
        source.write_bytes(b"video")
        with mock.patch.object(os, "link", side_effect=OSError(errno.EIO, "disco")):
            delivery.link_or_copy(source, self.tmp / "entrega" / "clip.mp4", allow_symlink=False)
        link = next(row for row in self.recorder.ops() if row["operation"] == "fs.link")
        self.assertEqual(("error", "IO_ERROR"), (link["outcome"], link["error_code"]))

    def test_a_skip_by_the_caller_is_an_ok_level_line(self):
        with effects.op("server.stop", "127.0.0.1:1") as handle:
            handle.skip()
        self.assertEqual("skipped", self.recorder.one()["outcome"])
        self.assertEqual(20, self.recorder.calls[0][0])

    def test_only_known_extras_reach_the_line(self):
        with effects.op("fs.write", self.tmp / "x", bytes=3, count=2, sha12="abc", query="q=segredo") as handle:
            handle.set(exit=0, env="PATH=/x")
        row = self.recorder.one()
        self.assertTrue({"bytes", "count", "sha12", "exit"} <= set(row))
        self.assertFalse({"query", "env"} & set(row))
        self.assertLessEqual(set(row), set(effects.FIELDS) | effects.EXTRAS)

    def test_the_command_comes_from_the_running_cli_command(self):
        token = runtime.ACTIVE.set({"operation": "fetch", "warnings": []})
        try:
            effects.mkdir(self.tmp / "dentro")
        finally:
            runtime.ACTIVE.reset(token)
        effects.mkdir(self.tmp / "fora")
        self.assertEqual(["fetch", None], [row["command"] for row in self.recorder.ops()])

    def test_a_temporary_folder_logs_its_creation_and_removal_by_its_parent(self):
        with effects.temp_dir(prefix="gb-x-", folder=self.tmp) as raw:
            made = Path(raw)
            (made / "a.txt").write_text("x", encoding="utf-8")
            self.assertTrue(made.is_dir())
        self.assertFalse(made.exists())
        with self.assertRaises(KeyError), effects.temp_dir(folder=self.tmp) as raw:
            second = Path(raw)
            raise KeyError("bug")
        self.assertFalse(second.exists())
        rows = self.recorder.ops()
        self.assertEqual(["fs.mkdir", "fs.rmtree"] * 2, [row["operation"] for row in rows])
        self.assertEqual({effects.redact_target(self.tmp)}, {row["target"] for row in rows})

    def test_kill_logs_its_target_and_pid_and_demands_a_target(self):
        with mock.patch.object(effects.os, "kill") as killed:
            effects.kill(4321, 15, target="serve")
            with self.assertRaises(TypeError):
                effects.kill(4321, 15)  # pyright: ignore[reportCallIssue]  # pylint: disable=missing-kwoa  # sem alvo
        killed.assert_called_once_with(4321, 15)
        row = self.recorder.one()
        self.assertEqual(("proc.kill", "serve", 4321), (row["operation"], row["target"], row["pid"]))

    def test_a_plugin_load_block_logs_the_plugin_id(self):
        with effects.load_plugin("demo_export"):
            pass
        row = self.recorder.one()
        self.assertEqual(("plugin.load", "demo_export"), (row["operation"], row["target"]))

    def test_a_server_stopped_with_ctrl_c_logs_an_ok_stop_and_still_interrupts(self):
        server = mock.Mock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            effects.serve(server, address="127.0.0.1:8767")
        row = self.recorder.one()
        self.assertEqual(("server.stop", "127.0.0.1:8767", "ok"), (row["operation"], row["target"], row["outcome"]))

    def test_a_server_shut_down_from_another_thread_logs_an_ok_stop_and_returns(self):
        # `server.shutdown()` makes `serve_forever` return normally: no interrupt to re-raise.
        server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
        self.addCleanup(server.server_close)
        stopper = threading.Timer(0.05, server.shutdown)
        stopper.start()
        self.addCleanup(stopper.join)
        self.assertIsNone(effects.serve(server, address="127.0.0.1:8767"))
        row = self.recorder.one()
        self.assertEqual(("server.stop", "127.0.0.1:8767", "ok"), (row["operation"], row["target"], row["outcome"]))

    @mock.patch.dict(os.environ)
    def test_open_url_logs_one_net_line_with_the_host_for_a_string_and_for_a_request(self):
        # O proxy do ambiente não pode desviar o GET de loopback.
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
            os.environ.pop(name, None)

        class Handler(http.server.BaseHTTPRequestHandler):
            @override
            def do_GET(self):  # pylint: disable=invalid-name
                self.send_response(200)
                self.send_header("Content-Length", "5")
                self.end_headers()
                self.wfile.write(b"hello")

            def log_message(self, format, *args):  # noqa: A002  # pylint: disable=redefined-builtin
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/clip"
        for label, request in (("string", url), ("request", urllib.request.Request(url))):
            with self.subTest(request=label):
                self.recorder.calls.clear()
                with effects.open_url(request, timeout=5) as response:
                    self.assertEqual(b"hello", response.read())
                row = self.recorder.one()
                self.assertEqual(("net.get", "ok"), (row["operation"], row["outcome"]))
                self.assertIn("127.0.0.1", row["target"])

    def test_open_url_logs_the_operation_the_caller_names(self):
        response = mock.MagicMock()
        with mock.patch.object(effects.urllib.request, "urlopen", return_value=response):
            self.assertIs(response, effects.open_url("http://127.0.0.1:1/clip", timeout=5, operation="net.download"))
        row = self.recorder.one()
        self.assertEqual(("net.download", "ok"), (row["operation"], row["outcome"]))

    def test_every_operation_name_used_by_a_helper_is_known(self):
        self.assertTrue(effects.OPERATIONS)
        for name in effects.OPERATIONS:
            with self.subTest(name=name):
                area, _, verb = name.partition(".")
                self.assertTrue(area and verb and name == name.lower())


class ErrorCodeTests(EffectsCase):
    """O `error_code` da linha é o que a CLI daria à exceção, se ela chegasse lá como está."""

    def failing(self, exc):
        with self.assertRaises(type(exc)) as caught, effects.op("fs.write", self.tmp / "x"):
            raise exc
        self.assertIs(exc, caught.exception)
        return self.recorder.one()

    def test_exception_class_maps_to_the_cli_code_and_outcome(self):
        cases = [
            (PermissionError(errno.EACCES, "negado"), "IO_ERROR", "error"),
            (FileNotFoundError(errno.ENOENT, "sumiu"), "IO_ERROR", "error"),
            (ValueError("dado ruim"), "INVALID_DATA", "refused"),
            (UsageError("uso"), "USAGE_ERROR", "refused"),
            (PrerequisiteError("falta ffmpeg"), "PREREQUISITE_MISSING", "refused"),
            (LockedError("ocupado"), "LOCKED", "refused"),
            (KeyError("bug"), "INTERNAL_ERROR", "error"),
            # Ferramenta externa que falhou ou estourou o tempo: erro de operação, não bug.
            (subprocess.TimeoutExpired(["x"], 1), "INVALID_DATA", "error"),
            (subprocess.CalledProcessError(2, ["yt-dlp"]), "INVALID_DATA", "error"),
            # Recusas do core que não são `ValueError`.
            (UnsafeFileError("link"), "INVALID_DATA", "refused"),
            (CollectError("falhou"), "INVALID_DATA", "refused"),
            # Um passo do pip que falhou é falha, não recusa: a classe diz `outcome = "error"`.
            (_StepFailedError("pip falhou", StepResult(1, ()), code="venv_failed"), "INVALID_DATA", "error"),
            (RuntimeError("bug"), "INTERNAL_ERROR", "error"),
            (KeyboardInterrupt(), "INTERRUPTED", "error"),
        ]
        for exc, code, outcome in cases:
            with self.subTest(exc=type(exc).__name__):
                self.recorder.calls.clear()
                row = self.failing(exc)
                self.assertEqual((code, outcome, type(exc).__name__), (row["error_code"], row["outcome"], row["error"]))

    def test_a_value_error_inside_usage_errors_logs_usage_error(self):
        with runtime.usage_errors(), contextlib.suppress(Exception):
            self.failing(ValueError("flag"))
        self.assertEqual("USAGE_ERROR", self.recorder.ops()[0]["error_code"])

    def test_the_line_never_carries_the_message_of_the_exception(self):
        self.failing(OSError(errno.EIO, "/caminho/secreto token=abc123"))
        self.assertNotIn("abc123", repr(self.recorder.calls))
        self.assertNotIn("secreto", repr(self.recorder.calls))


class RedactionTests(EffectsCase):
    def test_a_path_inside_the_project_is_relative_to_it(self):
        with mock.patch.dict(logs._state, {"project": self.tmp}):
            self.assertEqual("brolls/manifest.json", target_of(self.tmp / "brolls" / "manifest.json"))
            self.assertEqual("brolls", target_of(str(self.tmp / "brolls")))

    def test_a_name_the_core_does_not_give_itself_is_hashed_with_its_extension(self):
        # A pasta do beat nasce do título do candidato e o clipe do nome do shot: nada disso vai ao log.
        path = self.tmp / "entrega" / "01-show-ac-dc-ao-vivo" / "abertura-clip.MP4"
        with mock.patch.dict(logs._state, {"project": self.tmp}):
            shown = target_of(path)
            again = target_of(str(path))
        self.assertRegex(shown, r"^entrega/#[0-9a-f]{8}/#[0-9a-f]{8}\.mp4$")
        self.assertEqual(shown, again)
        self.assertNotIn("ac-dc", shown)
        self.assertNotIn("abertura", shown)

    def test_a_path_inside_gb_home_is_relative_to_it(self):
        home = Path(os.environ["GB_HOME"])
        self.assertEqual("$GB_HOME/plugins.json", target_of(home / "plugins.json"))
        self.assertRegex(target_of(home / "plugins" / "demo_secreto"), r"^\$GB_HOME/plugins/#[0-9a-f]{8}$")

    def test_a_path_outside_the_project_and_gb_home_is_only_a_hash(self):
        outside = self.tmp / "Clientes" / "cliente-secreto" / "clip.mov"
        shown = target_of(outside)
        self.assertRegex(shown, r"^#[0-9a-f]{8}\.mov$")
        self.assertEqual(shown, target_of(str(outside)))
        self.assertNotIn("cliente-secreto", shown)

    def test_a_path_in_the_home_folder_is_never_spelled_out(self):
        with mock.patch.object(Path, "home", return_value=self.tmp / "pessoa"):
            shown = target_of(self.tmp / "pessoa" / "Videos" / "ferias.mp4")
        self.assertRegex(shown, r"^#[0-9a-f]{8}\.mp4$")

    def test_a_url_is_only_its_host(self):
        # O caminho de uma URL pode ser a credencial (webhook do Slack, link do Dropbox, id do Drive,
        # token no caminho): como em todo o resto do log, só o host.
        cases = {
            "https://user:senha@api.example.com/v1/videos/123?api_key=SEGREDO&token=abc#frag": "api.example.com",
            "https://www.dropbox.com/s/a1b2c3d4e5f6g7h/Cliente_Acme_final.mp4?dl=1": "www.dropbox.com",
            joined(
                "https://hooks.slack.com/", "services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
            ): "hooks.slack.com",
            "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view": "drive.google.com",
            "https://api.example.com/v1/sk-proj-abcdefghijklmnopqrstuvwxyz0123/x": "api.example.com",
            "https://api.example.com/bot123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getMe": "api.example.com",
            "HTTPS://user:pw@Example.COM/a": "example.com",
            "http://127.0.0.1:8767/__ping": "127.0.0.1",
        }
        for url, host in cases.items():
            with self.subTest(url=url):
                self.assertEqual(host, target_of(url))

    def test_a_command_is_the_name_of_its_executable_only(self):
        argv = ["/opt/tools/bin/yt-dlp", "--add-header", "Authorization: Bearer SEGREDO", "https://x.test/?sig=1"]
        self.assertEqual("yt-dlp", target_of(argv))

    def test_a_list_command_is_an_exact_path_never_split_into_words(self):
        """argv[0] of a list is one path: a blank in a folder name is part of it, a backslash is no escape."""
        cases = {
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe": "ffmpeg",
            r"C:/ProgramData/chocolatey/bin/ffmpeg.EXE": "ffmpeg",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe": "ffmpeg",
            r"C:\Program Files (x86)\ff\ffmpeg.exe": "ffmpeg",
            r"\\server\my share\bin\ffmpeg.exe": "ffmpeg",
            r"\\server\share\bin\ffprobe.exe": "ffprobe",
            r".\ffmpeg.exe": "ffmpeg",
            r"bin\ffmpeg.exe": "ffmpeg",
            r"..\tools\yt-dlp.exe": "yt-dlp",
            r"node_modules\.bin\playwright-cli.cmd": "playwright-cli.cmd",
            "/Users/Ana Silva/bin/yt-dlp": "yt-dlp",
            "/usr/local/bin/ffmpeg": "ffmpeg",
            "/usr/bin/python3.14": "python3.14",
            "ffmpeg": "ffmpeg",
            "C:\\": "-",
            "C:/": "-",
            r"\\server\share": "-",
            "": "-",
        }
        for command, tool in cases.items():
            for kind in (list, tuple):
                with self.subTest(command=command, kind=kind.__name__):
                    shown = target_of(kind([command, "-version"]))
                    self.assertEqual(tool, shown)
                    self.assertEqual(tool, effects._executable(command))
                    for fragment in ("Program", "Silva", "Ana", "server", "my share", "chocolatey"):
                        self.assertNotIn(fragment, shown)

    def test_only_a_shell_text_is_split_into_words(self):
        cases = {
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe -i x": "ffmpeg",
            r'"C:\Program Files\ff\ffmpeg.exe" -i x': "ffmpeg",
            r'"C:\Program Files (x86)\ff\ffmpeg.exe" -i x': "ffmpeg",
            r"C:\Tools\yt-dlp.exe --cookies x": "yt-dlp",
            r"bin\ffmpeg.exe -i x": "ffmpeg",
            r".\ffmpeg.exe -i x": "ffmpeg",
            r"\\server\share\bin\ffprobe.exe -v": "ffprobe",
            "/opt/tools/bin/yt-dlp --cookies x": "yt-dlp",
            "echo --api-key=SEGREDO": "echo",
            'ffmpeg -i "/srv/cliente/segredo.mov': "ffmpeg",
            "C:/tools/ffmpeg.EXE 'sem fim": "ffmpeg",
            "   ": "-",
            "": "-",
            "C:\\ -x": "-",
        }
        for command, tool in cases.items():
            with self.subTest(command=command):
                self.assertEqual(tool, effects._executable(command, shell_text=True))
                shown = target_of([effects.ShellText(command)])
                self.assertEqual(tool, shown)
                self.assertNotIn("SEGREDO", shown)
                self.assertNotIn("cliente", shown)
                self.assertNotIn("segredo", shown)

    def test_run_marks_a_string_command_as_shell_text_and_a_path_as_a_path(self):
        with (
            mock.patch.object(effects.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
            mock.patch.object(effects, "op", wraps=effects.op) as spy,
        ):
            effects.run("echo --api-key=SEGREDO", shell=True)  # noqa: S604
            effects.run(Path("/Users/Ana Silva/bin/yt-dlp"))
            effects.run(["/Users/Ana Silva/bin/yt-dlp", "-x"])
            seen = [call.args[1] for call in spy.call_args_list]
        self.assertIsInstance(seen[0][0], effects.ShellText)
        self.assertNotIsInstance(seen[1][0], effects.ShellText)
        self.assertNotIsInstance(seen[2][0], effects.ShellText)
        self.assertEqual(["echo", "yt-dlp", "yt-dlp"], [row["target"] for row in self.recorder.ops()])
        self.assertNotIn("Silva", repr(self.recorder.calls))

    def test_a_logged_operation_carries_only_the_redacted_target(self):
        argv = [sys.executable, "-c", "pass", "--password=SEGREDO"]
        effects.run(argv, check=True)
        row = self.recorder.one()
        self.assertEqual(Path(sys.executable).name.removesuffix(".exe"), row["target"])
        self.assertNotIn("SEGREDO", repr(self.recorder.calls))

    def test_a_shell_string_command_logs_only_its_first_word(self):
        effects.run("echo --api-key=SEGREDO", shell=True, check=False, capture_output=True)  # noqa: S604
        row = self.recorder.one()
        self.assertEqual("echo", row["target"])
        self.assertNotIn("SEGREDO", repr(self.recorder.calls))
        self.assertEqual("yt-dlp", target_of((effects.ShellText("/opt/bin/yt-dlp --cookies x"),)))

    def test_a_plain_text_target_that_is_not_an_id_is_hashed(self):
        for text in (
            joined("Cliente Acme segredo ghp_", "ABCDEF1234567890abcdef1234567890abcd"),
            joined("ghp_", "ABCDEF1234567890abcdef1234567890abcd"),
            "sk-live-SECRET1234567890abcdefgh",
            joined("xoxb-", "1234-5678-abcdefghijkl"),
            "token=abc123",
        ):
            with self.subTest(text=text):
                shown = target_of(text)
                self.assertRegex(shown, r"^#[0-9a-f]{8}$")
        for text in ("demo_export", "127.0.0.1:8767", "getbrolls.profile", "serve-12", "local:abc:shot:x"):
            with self.subTest(text=text):
                self.assertEqual(text, target_of(text))

    def test_an_id_stays_as_it_is_and_a_long_text_is_hashed(self):
        self.assertEqual("demo_export", target_of("demo_export"))
        self.assertRegex(target_of("x" * 500), r"^#[0-9a-f]{8}$")  # longo demais para um id: só o hash

    def test_redaction_never_raises(self):
        class Broken(os.PathLike):
            def __fspath__(self):
                raise RuntimeError("quebrado")

        self.assertEqual("-", target_of(Broken()))


class SaltedHashTests(EffectsCase):
    """O hash de um nome é um HMAC com sal desta instalação, e só o último nome guarda a extensão."""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "gbhome"
        env = mock.patch.dict(os.environ, {"GB_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.project = self.tmp / "proj"
        salts = mock.patch.dict(effects_salt._salts, clear=True)
        salts.start()
        self.addCleanup(salts.stop)
        effects_salt._salt_warned.clear()
        state = mock.patch.dict(logs._state, {"project": self.project})
        state.start()
        self.addCleanup(state.stop)

    def salt_file(self):
        return self.home / effects_salt.SALT_FILE

    def test_the_salt_is_created_once_private_and_never_logged(self):
        self.assertFalse(self.salt_file().exists())
        first = target_of(self.project / "entrega" / "joao.silva")
        salt = self.salt_file().read_text(encoding="utf-8").strip()
        self.assertRegex(salt, r"^[0-9a-f]{64}$")
        if POSIX:
            self.assertEqual(0o600, stat.S_IMODE(self.salt_file().stat().st_mode))
        self.assertEqual(first, target_of(self.project / "entrega" / "joao.silva"))
        effects.mkdir(self.project / "entrega" / "joao.silva", parents=True)
        self.assertNotIn(salt, repr(self.recorder.calls))

    def test_the_hash_is_not_a_plain_sha256_of_the_name(self):
        shown = target_of(self.project / "entrega" / "joao-silva")
        plain = "#" + hashlib.sha256(b"joao-silva").hexdigest()[:8]
        self.assertNotEqual(f"entrega/{plain}", shown)
        self.assertRegex(shown, r"^entrega/#[0-9a-f]{8}$")

    def test_another_installation_gets_other_hashes(self):
        shown = target_of(self.project / "entrega" / "joao-silva")
        with mock.patch.dict(os.environ, {"GB_HOME": str(self.tmp / "outra")}):
            self.assertNotEqual(shown, target_of(self.project / "entrega" / "joao-silva"))

    def test_an_unreadable_salt_never_falls_back_to_an_unsalted_hash(self):
        self.home.mkdir()
        self.salt_file().write_text("curto\n", encoding="utf-8")
        shown = target_of(self.project / "entrega" / "joao-silva")
        self.assertNotEqual("entrega/#" + hashlib.sha256(b"joao-silva").hexdigest()[:8], shown)
        self.assertRegex(self.salt_file().read_text(encoding="utf-8"), r"^[0-9a-f]{64}\n$")  # trocado, não editado

    def warnings(self):
        return [fields["reason"] for _level, name, fields in self.recorder.calls if name == "log_salt_unavailable"]

    def fresh_process(self):
        effects_salt._salts.clear()
        effects_salt._salt_warned.clear()

    def test_a_linked_salt_is_never_followed_and_warns_once(self):
        self.home.mkdir()
        elsewhere = self.tmp / "outro-sal"
        elsewhere.write_text("ab" * 32 + "\n", encoding="ascii")
        self.salt_file().symlink_to(elsewhere)
        target_of(self.project / "entrega" / "a")
        target_of(self.project / "entrega" / "b")
        self.assertEqual(["link"], self.warnings())
        self.assertTrue(self.salt_file().is_symlink())
        self.assertEqual("link", effects.salt_state())

    def test_a_malformed_salt_warns_and_is_replaced_by_a_good_one(self):
        self.home.mkdir()
        self.salt_file().write_text("nao-e-sal\n", encoding="utf-8")
        self.assertEqual("malformed", effects.salt_state())
        first = target_of(self.project / "entrega" / "a")
        self.assertEqual(["malformed"], self.warnings())
        self.assertRegex(self.salt_file().read_text(encoding="ascii").strip(), r"^[0-9a-f]{64}$")
        self.fresh_process()
        self.assertEqual(first, target_of(self.project / "entrega" / "a"))
        self.assertEqual("ok", effects.salt_state())

    def repair_into(self, results):
        results.append(effects_salt._create_salt(self.salt_file(), swap=True))

    def test_without_flock_two_racing_repairers_keep_the_same_salt(self):
        """Sem `fcntl` (Windows), o conserto do sal passa por uma trava em arquivo no `$GB_HOME`."""
        self.home.mkdir()
        real_aside = effects_salt._set_aside

        def slow_aside(path):
            time.sleep(0.02)  # alarga a janela entre conferir e trocar, onde a corrida acontece
            real_aside(path)

        with mock.patch.object(logs, "fcntl", None), mock.patch.object(effects_salt, "_set_aside", slow_aside):
            for round_number in range(15):
                self.salt_file().write_text("nao-e-sal\n", encoding="utf-8")
                results = []
                workers = [threading.Thread(target=self.repair_into, args=(results,)) for _ in range(2)]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join()
                final = effects_salt._inspect_salt(self.salt_file())
                with self.subTest(round=round_number):
                    self.assertEqual([final, final], results)
        self.assertFalse([p for p in self.home.iterdir() if p.name.startswith(effects_salt.SALT_FILE + ".")])
        lock = self.home / effects_salt.SALT_REPAIR_LOCK
        self.assertTrue(lock.exists())
        self.assertFalse(lock.name.startswith(effects_salt.SALT_FILE))

    def test_a_repair_removes_the_malformed_file_and_old_leftovers(self):
        self.home.mkdir()
        self.salt_file().write_text("nao-e-sal\n", encoding="utf-8")
        (self.home / f"{effects_salt.SALT_FILE}.bad-antigo").write_text("x", encoding="utf-8")
        target_of(self.project / "entrega" / "a")
        self.assertEqual([effects_salt.SALT_FILE], visible_names(self.home))
        self.assertEqual("ok", effects.salt_state())

    @unittest.skipUnless(POSIX and os.geteuid() != 0, "permissões POSIX, sem root")
    def test_a_read_only_home_warns_and_uses_a_salt_of_the_process(self):
        self.home.mkdir()
        self.home.chmod(0o500)
        self.addCleanup(self.home.chmod, 0o700)
        shown = target_of(self.project / "entrega" / "joao-silva")
        self.assertRegex(shown, r"^entrega/#[0-9a-f]{8}$")
        self.assertEqual(["readonly"], self.warnings())
        self.assertEqual("readonly", effects.salt_state())
        self.assertEqual([], sorted(self.home.iterdir()))

    @unittest.skipUnless(POSIX and os.geteuid() != 0, "permissões POSIX, sem root")
    def test_doctor_tells_a_home_that_cannot_hold_the_salt_from_one_that_will(self):
        self.assertEqual("missing", effects.salt_state())  # `$GB_HOME` ainda não existe: nasce na 1ª linha
        locked = self.tmp / "trancada"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        with mock.patch.dict(os.environ, {"GB_HOME": str(locked / "home")}):
            self.assertEqual("readonly", effects.salt_state())
        self.assertFalse(self.home.exists())

    def test_without_hardlinks_the_salt_is_created_in_place_and_stays_stable(self):
        with mock.patch.object(Path, "hardlink_to", side_effect=OSError(errno.ENOTSUP, "sem links")):
            first = target_of(self.project / "entrega" / "a")
            self.fresh_process()
            second = target_of(self.project / "entrega" / "a")
        self.assertEqual(first, second)
        self.assertEqual([effects_salt.SALT_FILE], sorted(p.name for p in self.home.iterdir()))
        self.assertEqual([], self.warnings())
        if POSIX:
            self.assertEqual(0o600, stat.S_IMODE(self.salt_file().stat().st_mode))

    def test_a_reader_waits_for_a_salt_still_being_written(self):
        self.home.mkdir()
        self.salt_file().write_text("", encoding="ascii")
        salt = "cd" * 32

        def finish():
            time.sleep(0.05)
            self.salt_file().write_text(salt + "\n", encoding="ascii")

        writer = threading.Thread(target=finish)
        writer.start()
        state, found = effects_salt._inspect_salt(self.salt_file())
        writer.join()
        self.assertEqual(("ok", bytes.fromhex(salt)), (state, found))

    def test_only_the_last_name_keeps_an_extension_and_only_a_known_one(self):
        cases = {
            ("entrega", "joao.silva"): r"^entrega/#[0-9a-f]{8}$",
            ("entrega", "joao.silva", "clip.MP4"): r"^entrega/#[0-9a-f]{8}/#[0-9a-f]{8}\.mp4$",
            ("entrega", "Cliente.Acme", "nota.segredo"): r"^entrega/#[0-9a-f]{8}/#[0-9a-f]{8}$",
            ("entrega", "legendas.srt"): r"^entrega/#[0-9a-f]{8}\.srt$",
        }
        for parts, pattern in cases.items():
            with self.subTest(parts=parts):
                shown = target_of(self.project.joinpath(*parts))
                self.assertRegex(shown, pattern)
                self.assertNotIn("silva", shown)
                self.assertNotIn("Acme", shown)


class LoggingNeverBreaksTheOperationTests(unittest.TestCase):
    def test_a_failing_emitter_does_not_break_nor_change_the_operation(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "a.txt"
            with (
                mock.patch.object(logs, "event", side_effect=RuntimeError("log quebrado")),
                mock.patch.object(effects._logger, "isEnabledFor", return_value=True),
            ):
                effects.write_text(path, "conteúdo", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    effects.write_text(path, "outro", exclusive=True, encoding="utf-8")
            self.assertEqual("conteúdo", path.read_text(encoding="utf-8"))

    def test_an_unwritable_log_file_drops_the_line_and_the_operation_goes_on(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "proj"
            (project / "brolls").mkdir(parents=True)
            # Uma pasta no lugar do arquivo de log: o handler não consegue abrir, a linha cai.
            (project / "brolls" / logs.LOG_FILENAME).mkdir()
            logs.configure(project, read_only=False)
            try:
                effects.write_text(project / "brolls" / "x.txt", "ok", encoding="utf-8")
            finally:
                logs.shutdown()
            self.assertEqual("ok", (project / "brolls" / "x.txt").read_text(encoding="utf-8"))

    def test_a_disabled_logger_skips_redaction_entirely(self):
        with (
            tempfile.TemporaryDirectory() as raw,
            mock.patch.object(effects._logger, "isEnabledFor", return_value=False),
            mock.patch.object(effects, "redact_target", side_effect=AssertionError("redigiu sem precisar")),
        ):
            effects.mkdir(Path(raw) / "nova")


if __name__ == "__main__":
    unittest.main()
