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
