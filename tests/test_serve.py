"""Issue #32: `gb.py serve` sobe um servidor local só-leitura para brolls/review.html."""

import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

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
            with self._running(server):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/review.html", timeout=5) as response:
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


if __name__ == "__main__":
    unittest.main()


class ServeMissingStoryboardEnvelopeTests(unittest.TestCase):
    def test_missing_review_html_is_a_normal_error_without_creating_brolls(self):
        import subprocess, sys, json, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "gb.py"), "serve", "--project", tmp],
                capture_output=True, text=True,
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
        (brolls / "review.html").write_text(
            "<html><body>storyboard</body></html>", encoding="utf-8"
        )
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
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            with self._serving(root) as (server, port):
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/review.html", timeout=5
                ) as response:
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
                for token in ("", "outro-token", server.save_token + "x"):
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
            started = serve.start_background(root, port=0)
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
            pid_file.write_text(
                _json.dumps({"pid": 999999999, "port": 8767, "urls": []}), encoding="utf-8"
            )
            self.assertFalse(serve.state(root)["running"])
            result = serve.stop(root)
            self.assertFalse(result["stopped"])
            self.assertFalse(pid_file.exists())

    def test_state_is_read_only_on_a_project_without_a_pid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(Path(tmp))
            self.assertEqual(False, serve.state(root)["running"])
            self.assertEqual(
                {"review.html"}, {p.name for p in (root / "brolls").iterdir()}
            )
