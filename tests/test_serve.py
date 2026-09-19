"""Issue #32: `gb.py serve` sobe um servidor local só-leitura para brolls/review.html."""

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import serve


class ServeStartTests(unittest.TestCase):
    def _project_with_review(self, root):
        brolls = root / "brolls"
        brolls.mkdir(parents=True)
        (brolls / "review.html").write_text("<html>storyboard</html>", encoding="utf-8")
        return root

    @contextmanager
    def _running(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_missing_review_html_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brolls").mkdir()
            with self.assertRaises(ValueError):
                serve.start(root)

    def test_start_binds_ephemeral_port_and_serves_review_html_with_no_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project_with_review(Path(tmp))
            server, port = serve.start(root, port=0)
            self.assertGreater(port, 0)
            self.assertEqual("127.0.0.1", server.server_address[0])
            with (
                self._running(server),
                urllib.request.urlopen(f"http://127.0.0.1:{port}/review.html", timeout=5) as response,
            ):
                self.assertEqual(200, response.status)
                self.assertIn(b"storyboard", response.read())
                self.assertEqual("no-store", response.headers.get("Cache-Control"))

    def test_get_outside_served_directory_is_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project_with_review(Path(tmp))
            # Um arquivo fora de brolls/ nunca deve ser alcançável pelo servidor.
            (root / "secret.txt").write_text("nope", encoding="utf-8")
            server, port = serve.start(root, port=0)
            with self._running(server):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/../secret.txt", timeout=5)
                self.assertEqual(404, ctx.exception.code)

    def test_second_start_on_busy_port_picks_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project_with_review(Path(tmp))
            first, first_port = serve.start(root, port=0)
            try:
                second, second_port = serve.start(root, port=first_port)
                try:
                    self.assertNotEqual(first_port, second_port)
                finally:
                    second.server_close()
            finally:
                first.server_close()


class ServeMissingStoryboardEnvelopeTests(unittest.TestCase):
    def test_missing_review_html_is_a_normal_error_without_creating_brolls(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "gb.py"),
                    "serve",
                    "--project",
                    tmp,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["error_code"], "INVALID_DATA")
            self.assertNotIn("traceback", payload)
            self.assertFalse((Path(tmp) / "brolls").exists())


class SaveEndpointTests(unittest.TestCase):
    """#44: a página salva as decisões direto no projeto, sem passar pela pasta de Downloads."""

    def _project(self, root):
        brolls = root / "brolls"
        brolls.mkdir(parents=True)
        (brolls / "review.html").write_text("<html><body>storyboard</body></html>", encoding="utf-8")
        return root

    @contextmanager
    def _serving(self, root):
        server, port = serve.start(root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, port
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def _post(self, port, body, token, path="/__save"):
        import json as _json

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=body if isinstance(body, bytes) else _json.dumps(body).encode(),
            headers={"Content-Type": "application/json", serve.TOKEN_HEADER: token},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_review_html_carries_the_session_token(self):

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/review.html", timeout=5) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("GETBROLLS_SAVE", page)
                self.assertIn(server.save_token, page)
                self.assertIn("storyboard", page)

    def test_post_with_the_token_writes_under_reviews(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                payload = {"type": "getbrolls-review", "items": [{"id": "local:a"}]}
                with self._post(port, payload, server.save_token) as response:
                    self.assertEqual(200, response.status)
                    answer = _json.loads(response.read().decode("utf-8"))
            saved = Path(answer["path"])
            self.assertTrue(saved.is_file())
            self.assertEqual((root / "brolls" / "reviews").resolve(), saved.parent)
            self.assertRegex(saved.name, r"^\d{8}-\d{6}(-\d+)?\.json$")
            self.assertEqual(payload, _json.loads(saved.read_text(encoding="utf-8")))

    def test_post_without_the_token_is_refused_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                assert server.save_token is not None
                for token in ("", "outro-token", server.save_token + "x"):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        self._post(port, {"items": []}, token)
                    self.assertEqual(403, ctx.exception.code)
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_an_uninitialised_token_refuses_instead_of_matching_the_empty_string(self):
        """`save_token=""` fazia `compare_digest("", "")` valer: fail-open, não fail-closed."""
        self.assertIsNone(serve._ExclusiveServer.save_token)
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                server.save_token = None
                for token in ("", "qualquer"):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        self._post(port, {"items": []}, token)
                    self.assertEqual(403, ctx.exception.code)
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_post_to_another_path_is_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self._post(port, {"items": []}, server.save_token, path="/../x.json")
                self.assertEqual(404, ctx.exception.code)

    def test_oversized_body_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                body = b"x" * (serve.MAX_SAVE_BYTES + 1)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self._post(port, body, server.save_token)
                self.assertEqual(413, ctx.exception.code)
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_invalid_json_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self._post(port, b"{nope", server.save_token)
                self.assertEqual(400, ctx.exception.code)
            self.assertFalse((root / "brolls" / "reviews").exists())


def _serve_log(root):
    """O log do servidor de fundo, para a falha dizer o que o filho reclamou."""
    log = Path(root) / "brolls" / serve.LOG_FILE
    if not log.is_file():
        return f"(sem {log})"
    return f"{log}:\n{log.read_text(encoding='utf-8', errors='replace')}"


def _start_background(test, root):
    """`start_background` com o log do filho anexado quando ele não sobe."""
    try:
        return serve.start_background(root, port=0)
    except ValueError as exc:  # pragma: no cover - só quando o CI falha
        raise test.failureException(f"{exc}\n\n{_serve_log(root)}") from exc


class BackgroundServeTests(unittest.TestCase):
    """`serve --background` roda igual no Windows: subprocesso solto + PID em arquivo."""

    def _project(self, root):
        brolls = root / "brolls"
        brolls.mkdir(parents=True)
        (brolls / "review.html").write_text("<html>storyboard</html>", encoding="utf-8")
        return root

    def test_background_writes_pid_file_and_stop_terminates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            started = _start_background(self, root)
            try:
                self.assertTrue(started["background"])
                pid_file = root / "brolls" / ".serve.pid"
                self.assertTrue(pid_file.is_file())
                self.assertTrue(serve.state(root)["running"])
                with urllib.request.urlopen(started["urls"][1], timeout=10) as response:
                    self.assertIn(b"storyboard", response.read())
            finally:
                stopped = serve.stop(root)
            self.assertTrue(stopped["stopped"])
            self.assertFalse((root / "brolls" / ".serve.pid").exists())
            self.assertFalse(serve.state(root)["running"])

    def test_stop_without_a_server_cleans_a_stale_pid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            import json as _json

            pid_file = root / "brolls" / ".serve.pid"
            pid_file.write_text(_json.dumps({"pid": 999999999, "port": 8767, "urls": []}), encoding="utf-8")
            self.assertFalse(serve.state(root)["running"])
            result = serve.stop(root)
            self.assertFalse(result["stopped"])
            self.assertFalse(pid_file.exists())

    def test_state_is_read_only_on_a_project_without_a_pid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            self.assertEqual(False, serve.state(root)["running"])
            self.assertEqual({"review.html"}, {p.name for p in (root / "brolls").iterdir()})


class ServerIdentityTests(unittest.TestCase):
    """#44 (revisão): PID sozinho não prova nada — o servidor precisa se identificar."""

    def _project(self, root):
        brolls = root / "brolls"
        brolls.mkdir(parents=True)
        (brolls / "review.html").write_text("<html>storyboard</html>", encoding="utf-8")
        return root

    def test_a_recycled_pid_is_never_killed_and_the_file_is_cleaned(self):
        import json as _json
        import subprocess as _subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            # Um processo vivo qualquer, que não é o nosso servidor: o PID existe,
            # mas ninguém responde ao ping com a nossa sessão.
            innocent = _subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                (root / "brolls" / ".serve.pid").write_text(
                    _json.dumps(
                        {
                            "pid": innocent.pid,
                            "port": 1,
                            "session": "sessao-que-nunca-existiu",
                            "urls": [],
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertFalse(serve.state(root)["running"])
                result = serve.stop(root)
                self.assertFalse(result["stopped"])
                self.assertEqual("stale_pid", result["reason"])
                self.assertFalse((root / "brolls" / ".serve.pid").exists())
                self.assertIsNone(innocent.poll(), "o processo inocente foi morto")
            finally:
                innocent.kill()
                innocent.wait(timeout=5)

    def test_a_wrong_session_in_the_pid_file_is_not_running(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            _start_background(self, root)
            try:
                self.assertTrue(serve.state(root)["running"])
                pid_file = root / "brolls" / ".serve.pid"
                record = _json.loads(pid_file.read_text(encoding="utf-8"))
                self.assertTrue(record["session"])
                pid_file.write_text(_json.dumps({**record, "session": "outra"}), encoding="utf-8")
                self.assertFalse(serve.state(root)["running"])
                pid_file.write_text(_json.dumps(record), encoding="utf-8")
            finally:
                serve.stop(root)

    def test_ping_answers_the_session_of_this_server(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            server, port = serve.start(root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/__ping", timeout=5) as response:
                    self.assertEqual(
                        server.session_id,
                        _json.loads(response.read().decode("utf-8"))["session"],
                    )
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


class RebindingTests(unittest.TestCase):
    """Um nome que resolve para 127.0.0.1 continua sendo outra origem."""

    def _project(self, root):
        brolls = root / "brolls"
        brolls.mkdir(parents=True)
        (brolls / "review.html").write_text("<html>storyboard</html>", encoding="utf-8")
        return root

    @contextmanager
    def _serving(self, root):
        server, port = serve.start(root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, port
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def _request(self, port, headers, method="GET", body=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}" + (serve.SAVE_PATH if method == "POST" else "/review.html"),
            data=body,
            headers=headers,
            method=method,
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_get_with_a_foreign_host_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (_server, port):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self._request(port, {"Host": "storyboard.evil.test"})
                self.assertEqual(403, ctx.exception.code)

    def test_post_with_a_foreign_host_or_origin_is_refused(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                body = _json.dumps({"items": []}).encode()
                for headers in (
                    {"Host": f"evil.test:{port}"},
                    {"Host": f"127.0.0.1:{port}", "Origin": "https://evil.test"},
                ):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        self._request(
                            port,
                            {
                                **headers,
                                "Content-Type": "application/json",
                                serve.TOKEN_HEADER: server.save_token,
                            },
                            method="POST",
                            body=body,
                        )
                    self.assertEqual(403, ctx.exception.code)
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_a_non_ascii_token_is_a_refusal_not_a_crash(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (_server, port):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    self._request(
                        port,
                        {
                            "Host": f"127.0.0.1:{port}",
                            "Content-Type": "application/json",
                            serve.TOKEN_HEADER: "tökén-nao-ascii",
                        },
                        method="POST",
                        body=_json.dumps({"items": []}).encode(),
                    )
                self.assertEqual(403, ctx.exception.code)


class SaveWriteHardeningTests(unittest.TestCase):
    def test_a_symlink_planted_at_the_target_name_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brolls = root / "brolls"
            (brolls / serve.REVIEWS_DIR).mkdir(parents=True)
            target = root / "fora-do-projeto.json"
            name = time.strftime("%Y%m%d-%H%M%S") + ".json"
            (brolls / serve.REVIEWS_DIR / name).symlink_to(target)
            with self.assertRaises(ValueError):
                serve.save_review(brolls, {"items": []})
            self.assertFalse(target.exists())

    def test_a_symlinked_reviews_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brolls = root / "brolls"
            brolls.mkdir(parents=True)
            elsewhere = root / "outro-lugar"
            elsewhere.mkdir()
            (brolls / serve.REVIEWS_DIR).symlink_to(elsewhere)
            with self.assertRaises(ValueError):
                serve.save_review(brolls, {"items": []})
            self.assertEqual([], list(elsewhere.iterdir()))


class LogRotationTests(unittest.TestCase):
    """O log da rodada anterior não pode sumir nem crescer sem teto."""

    def test_the_previous_log_is_kept_in_serve_log_1_on_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = Path(tmp) / "brolls"
            brolls.mkdir(parents=True)
            log = brolls / serve.LOG_FILE
            log.write_text("por que o servidor caiu ontem\n", encoding="utf-8")
            serve._rotate_log(log)
            rotated = brolls / serve.LOG_ROTATE_FILE
            self.assertEqual("por que o servidor caiu ontem\n", rotated.read_text(encoding="utf-8"))

    def test_only_the_last_megabyte_survives_the_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = Path(tmp) / "brolls"
            brolls.mkdir(parents=True)
            log = brolls / serve.LOG_FILE
            log.write_bytes(b"a" * (serve.LOG_KEEP_BYTES + 5000) + b"FIM")
            serve._rotate_log(log)
            kept = (brolls / serve.LOG_ROTATE_FILE).read_bytes()
            self.assertEqual(serve.LOG_KEEP_BYTES, len(kept))
            # O fim do log é o que interessa: é onde está o erro.
            self.assertTrue(kept.endswith(b"FIM"))

    def test_a_missing_or_empty_log_rotates_to_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = Path(tmp) / "brolls"
            brolls.mkdir(parents=True)
            log = brolls / serve.LOG_FILE
            serve._rotate_log(log)
            self.assertFalse((brolls / serve.LOG_ROTATE_FILE).exists())
            log.write_text("", encoding="utf-8")
            serve._rotate_log(log)
            self.assertFalse((brolls / serve.LOG_ROTATE_FILE).exists())


class SlowButAliveServerTests(unittest.TestCase):
    """Um servidor vivo e lento não pode ser tratado como PID podre e ficar órfão.

    O teto de 0,25 s existe para o `status`, que só lê. Quem vai agir sobre o
    processo — `stop()` e `start_background()` — paga 1,0 s: classificar como morto
    um servidor que estava só ocupado apagaria o PID file sem nunca mandar o SIGTERM.
    """

    def _project(self, root):
        brolls = root / "brolls"
        brolls.mkdir(parents=True)
        (brolls / "review.html").write_text("<html>storyboard</html>", encoding="utf-8")
        return root

    def slow_ping(self, answers_after=0.5):
        """Ping de um servidor que só responde depois de `answers_after` segundos."""
        real = serve._ping

        def ping(port, session, timeout=1.0):
            time.sleep(min(timeout, answers_after))
            # Com o teto abaixo do tempo de resposta, o socket estoura antes: False.
            return real(port, session, timeout=timeout) if timeout > answers_after else False

        return ping

    def test_stop_still_terminates_a_server_that_answers_after_half_a_second(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            started = _start_background(self, root)
            pid = json.loads((root / "brolls" / ".serve.pid").read_text(encoding="utf-8"))["pid"]
            try:
                with patch.object(serve, "_ping", self.slow_ping()):
                    # O `status` desiste aos 0,25 s — e não mexe em nada por isso.
                    self.assertFalse(serve.state(root)["running"])
                    self.assertTrue((root / "brolls" / ".serve.pid").is_file())
                    stopped = serve.stop(root)
            finally:
                if serve._alive(pid):  # pragma: no cover - só quando o teste falha
                    serve.stop(root)
            self.assertTrue(stopped["stopped"], stopped)
            self.assertIsNone(stopped["reason"])
            self.assertEqual(pid, stopped["pid"])
            self.assertFalse(serve._alive(pid), "o servidor lento ficou órfão")
            self.assertFalse((root / "brolls" / ".serve.pid").exists())
            self.assertTrue(started["background"])

    def test_start_background_sees_the_slow_server_instead_of_raising_a_second_one(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            first = _start_background(self, root)
            try:
                with patch.object(serve, "_ping", self.slow_ping()):
                    again = serve.start_background(root, port=0)
                self.assertTrue(again["already_running"])
                self.assertEqual(first["port"], again["port"])
            finally:
                serve.stop(root)

    def test_the_two_budgets_are_the_documented_ones(self):
        self.assertEqual(0.25, serve.PING_TIMEOUT_S)
        self.assertEqual(1.0, serve.PING_TIMEOUT_ACT_S)


class DeadPidCostsNothingTests(unittest.TestCase):
    """`status` é leitura barata: com o PID morto não se abre socket nenhum."""

    def dead_pid(self):
        import subprocess as _subprocess

        done = _subprocess.Popen([sys.executable, "-c", "pass"])
        done.wait(timeout=10)
        return done.pid

    def test_state_with_a_dead_pid_answers_under_a_quarter_second(self):
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brolls = root / "brolls"
            brolls.mkdir(parents=True)
            (brolls / ".serve.pid").write_text(
                _json.dumps(
                    {
                        # Porta que ninguém escuta e que, num firewall calado, seguraria
                        # o ping até o timeout.
                        "pid": self.dead_pid(),
                        "port": 9,
                        "session": "sessao-de-um-processo-morto",
                        "urls": [],
                    }
                ),
                encoding="utf-8",
            )
            started = time.monotonic()
            answer = serve.state(root)
            elapsed = time.monotonic() - started
            self.assertFalse(answer["running"])
            self.assertIsNone(answer["pid"])
            self.assertLessEqual(elapsed, 0.25, f"`state()` levou {elapsed:.3f} s com o PID morto")

    def test_the_ping_never_waits_longer_than_a_quarter_second_per_try(self):
        self.assertEqual(0.25, serve.PING_TIMEOUT_S)
        self.assertEqual(1, serve.PING_RETRIES)

    def test_a_dead_pid_does_not_open_a_socket_at_all(self):
        import json as _json
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brolls = root / "brolls"
            brolls.mkdir(parents=True)
            (brolls / ".serve.pid").write_text(
                _json.dumps({"pid": self.dead_pid(), "port": 9, "session": "x", "urls": []}),
                encoding="utf-8",
            )
            with patch.object(serve, "_ping", side_effect=AssertionError("o ping não devia acontecer")):
                self.assertFalse(serve.state(root)["running"])


if __name__ == "__main__":
    unittest.main()
