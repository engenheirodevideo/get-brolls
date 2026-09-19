"""Hardening regressions for `scripts/getbrolls/serve.py`: framing/sniff headers on
every response, an allowlisted static-file surface (previews/ and clips/ only —
everything else, including directory listings and dotfiles, answers 404), private
`.serve.pid`/`.serve.log` files, a secret-free environment for the detached
background server, and a validated ping target."""

import json
import os
import re
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import serve


def _project_with_review(root):
    brolls = root / "brolls"
    brolls.mkdir(parents=True)
    (brolls / "review.html").write_text("<html><body>storyboard</body></html>", encoding="utf-8")
    return root


@contextmanager
def _serving(root):
    server, port = serve.start(root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _serve_log(root):
    log = Path(root) / "brolls" / serve.LOG_FILE
    if not log.is_file():
        return f"(sem {log})"
    return f"{log}:\n{log.read_text(encoding='utf-8', errors='replace')}"


def _start_background(test, root):
    try:
        return serve.start_background(root, port=0)
    except ValueError as exc:  # pragma: no cover - só quando o CI falha
        raise test.failureException(f"{exc}\n\n{_serve_log(root)}") from exc


class SecurityHeadersTests(unittest.TestCase):
    """Item 1: clickjacking/sniff/referrer protection on every response."""

    def test_html_response_carries_the_framing_and_sniff_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            with _serving(root) as (_server, port):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/review.html", timeout=5) as response:
                    headers = response.headers
                self.assertEqual("DENY", headers.get("X-Frame-Options"))
                self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))
                self.assertEqual("nosniff", headers.get("X-Content-Type-Options"))
                self.assertEqual("no-referrer", headers.get("Referrer-Policy"))
                self.assertEqual("no-store", headers.get("Cache-Control"))

    def test_json_error_response_carries_the_same_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            with _serving(root) as (_server, port):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/manifest.json", timeout=5)
                self.assertEqual(404, ctx.exception.code)
                headers = ctx.exception.headers
                self.assertEqual("DENY", headers.get("X-Frame-Options"))
                self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))
                self.assertEqual("nosniff", headers.get("X-Content-Type-Options"))
                self.assertEqual("no-referrer", headers.get("Referrer-Policy"))

    def test_save_response_carries_the_same_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            with _serving(root) as (server, port):
                assert server.save_token is not None
                body = json.dumps({"items": []}).encode()
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}{serve.SAVE_PATH}",
                    data=body,
                    headers={"Content-Type": "application/json", serve.TOKEN_HEADER: server.save_token},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    headers = response.headers
                self.assertEqual("DENY", headers.get("X-Frame-Options"))
                self.assertEqual("nosniff", headers.get("X-Content-Type-Options"))
                self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))


def _project_with_previewed_candidate(tmp):
    """Um projeto real, com um candidato pré-visualizado, gerado pelo pipeline de CLI."""
    root = Path(tmp)
    src = root / "original.mp4"
    synth_video(src, size="160x90", duration=3, rate=10)
    resolved = run_cli("resolve", "--file", src, project=root)
    cid = resolved["id"]
    base = ["--candidate", cid, "--project", str(root)]
    run_cli("preview", *base, "--start", 0, "--end", 1)
    run_cli("review", project=root)
    return root


class AllowlistTests(unittest.TestCase):
    """Item 2: só o que review.html realmente referencia fica alcançável."""

    @skip_unless_ffmpeg
    def test_every_referenced_asset_loads_and_everything_else_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_previewed_candidate(tmp)
            brolls = root / "brolls"
            # Arquivos internos que precisam ficar inalcançáveis, mesmo existindo de
            # verdade na pasta servida.
            (brolls / "diagnostics.jsonl").write_text('{"secret":"tracebacks"}\n', encoding="utf-8")
            reviews = brolls / "reviews"
            reviews.mkdir(parents=True, exist_ok=True)
            (reviews / "x.json").write_text("{}", encoding="utf-8")
            (brolls / ".serve.pid").write_text('{"pid":1,"session":"s"}', encoding="utf-8")
            (brolls / ".hidden").write_text("x", encoding="utf-8")
            self.assertTrue((brolls / "manifest.json").is_file())
            self.assertTrue(any((brolls / "previews").iterdir()))

            with _serving(root) as (_server, port):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/review.html", timeout=5) as response:
                    self.assertEqual(200, response.status)
                    self.assertEqual("text/html; charset=utf-8", response.headers.get("Content-Type"))
                    page = response.read().decode("utf-8")

                referenced = set(re.findall(r'(?:src|href)="(previews/[^"?#]+|clips/[^"?#]+)"', page))
                self.assertTrue(referenced, "review.html não referenciou nenhum previews/ ou clips/")
                for rel in referenced:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/{rel}", timeout=5) as response:
                        self.assertEqual(200, response.status, rel)

                blocked = [
                    "manifest.json",
                    "diagnostics.jsonl",
                    ".serve.pid",
                    "reviews/x.json",
                    ".hidden",
                ]
                for path in blocked:
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}", timeout=5)
                    self.assertEqual(404, ctx.exception.code, path)

                # Listagem de diretório.
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/previews/", timeout=5)
                self.assertEqual(404, ctx.exception.code)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/reviews/", timeout=5)
                self.assertEqual(404, ctx.exception.code)


class SymlinkEscapeTests(unittest.TestCase):
    """Um symlink plantado dentro de previews/ não pode servir arquivo de fora."""

    @unittest.skipIf(os.name == "nt", "symlinks sem privilégio não são criáveis no Windows")
    def test_a_symlink_inside_previews_pointing_outside_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            outside = Path(tmp).parent / f"gb-outside-{os.getpid()}.txt"
            outside.write_text("segredo", encoding="utf-8")
            try:
                previews = root / "brolls" / "previews"
                previews.mkdir(parents=True)
                os.symlink(outside, previews / "escape.jpg")
                with _serving(root) as (_server, port):
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/previews/escape.jpg", timeout=5)
                    self.assertEqual(404, ctx.exception.code)
            finally:
                outside.unlink(missing_ok=True)


class PrivateFileModeTests(unittest.TestCase):
    """Item 3: `.serve.pid` e `.serve.log` nunca ficam legíveis por outros usuários."""

    @unittest.skipIf(os.name == "nt", "bits de permissão são um conceito POSIX")
    def test_pid_and_log_files_are_created_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            _start_background(self, root)
            try:
                pid_file = root / "brolls" / ".serve.pid"
                log_file = root / "brolls" / serve.LOG_FILE
                self.assertEqual(0, stat.S_IMODE(pid_file.stat().st_mode) & 0o077)
                self.assertEqual(0, stat.S_IMODE(log_file.stat().st_mode) & 0o077)
            finally:
                serve.stop(root)

    @unittest.skipIf(os.name == "nt", "bits de permissão são um conceito POSIX")
    def test_a_pre_existing_world_readable_pid_file_is_tightened_on_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            pid_file = root / "brolls" / ".serve.pid"
            log_file = root / "brolls" / serve.LOG_FILE
            pid_file.write_text("{}", encoding="utf-8")
            log_file.write_text("log antigo\n", encoding="utf-8")
            os.chmod(pid_file, 0o644)
            os.chmod(log_file, 0o644)
            _start_background(self, root)
            try:
                self.assertEqual(0, stat.S_IMODE(pid_file.stat().st_mode) & 0o077)
                self.assertEqual(0, stat.S_IMODE(log_file.stat().st_mode) & 0o077)
            finally:
                serve.stop(root)


class BackgroundEnvironmentTests(unittest.TestCase):
    """Item 4: chaves de API/segredo nunca alcançam o filho, que nunca as usa."""

    def test_provider_keys_and_secret_shaped_variables_are_stripped_from_the_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            brolls = root / "brolls"
            captured = {}

            def fake_popen(_command, **kwargs):
                captured["env"] = kwargs.get("env")
                (brolls / serve.LOG_FILE).write_text(
                    json.dumps({"port": 12345, "session": "sessao-falsa"}) + "\n", encoding="utf-8"
                )
                process = MagicMock()
                process.pid = 4242
                process.poll.return_value = None
                return process

            secret_vars = {
                "PEXELS_API_KEY": "segredo-pexels",
                "PIXABAY_API_KEY": "segredo-pixabay",
                "YOUTUBE_API_KEY": "segredo-youtube",
                "SOME_OTHER_TOKEN": "segredo-outro",
                "MY_APP_SECRET": "segredo-app",
            }
            with (
                patch.dict(os.environ, secret_vars),
                patch.object(serve.subprocess, "Popen", side_effect=fake_popen),
            ):
                result = serve.start_background(root, port=0)

            self.assertIn("env", captured)
            child_env = captured["env"]
            self.assertIsNotNone(child_env)
            for key in secret_vars:
                self.assertNotIn(key, child_env, key)
            self.assertIn("PATH", child_env)
            self.assertEqual(4242, result["pid"])


class PingTargetValidationTests(unittest.TestCase):
    """Item 5: o `urlopen` do ping só abre um alvo http local, nunca outro esquema/host."""

    def test_only_local_http_targets_are_accepted(self):
        self.assertTrue(serve._valid_ping_target("http://127.0.0.1:8767/__ping"))
        self.assertTrue(serve._valid_ping_target("http://localhost:8767/__ping"))
        self.assertFalse(serve._valid_ping_target("https://127.0.0.1:8767/__ping"))
        self.assertFalse(serve._valid_ping_target("http://evil.test:8767/__ping"))
        self.assertFalse(serve._valid_ping_target("file:///etc/passwd"))
        self.assertFalse(serve._valid_ping_target("http://127.0.0.1.evil.test:8767/__ping"))

    def test_ping_still_answers_normally_for_a_real_local_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            with _serving(root) as (server, port):
                self.assertTrue(serve._ping(port, server.session_id, timeout=2.0))


class SessionComparisonTests(unittest.TestCase):
    """Item 6: a checagem de sessão do ping é comparação de tempo constante."""

    def test_ping_treats_a_non_string_session_as_a_mismatch_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            with _serving(root) as (server, port):
                with patch.object(serve.json, "loads", return_value={"session": 12345, "port": port}):
                    self.assertFalse(serve._ping(port, server.session_id, timeout=2.0))


if __name__ == "__main__":
    unittest.main()
