"""Hardening regressions for `scripts/getbrolls/serve.py`: framing/sniff headers on
every response, an allowlisted static-file surface (previews/ and clips/ only —
everything else, including directory listings and dotfiles, answers 404), private
`.serve.pid`/`.serve.log` files, a secret-free environment for the detached
background server, and a validated ping target."""

import errno
import http.client
import io
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _posix import without_nofollow
from _symlink import needs_symlink

from getbrolls import config, env_loader, serve


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
                with (
                    self.assertRaises(urllib.error.HTTPError) as ctx,
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/manifest.json", timeout=5),
                ):
                    pass
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


def _post_save(server, port):
    """POST de decisões com o token da sessão; devolve (status, corpo JSON)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        body = json.dumps({"items": []}).encode()
        conn.request(
            "POST",
            serve.SAVE_PATH,
            body=body,
            headers={"Content-Type": "application/json", serve.TOKEN_HEADER: server.save_token or ""},
        )
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        conn.close()


class SaveFailureTests(unittest.TestCase):
    """Quando a gravação das decisões falha, a página recebe um erro JSON, não uma conexão caída."""

    @needs_symlink
    def test_save_failure_answers_a_json_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp) / "proj")
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            (root / "brolls" / serve.REVIEWS_DIR).symlink_to(elsewhere, target_is_directory=True)
            with _serving(root) as (server, port):
                status, payload = _post_save(server, port)
            self.assertEqual(409, status)
            self.assertIn("link simbólico", payload["error"])
            self.assertEqual([], list(elsewhere.iterdir()))

    def test_a_disk_error_while_saving_answers_500(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            full = OSError(errno.ENOSPC, "No space left on device")
            with _serving(root) as (server, port), patch.object(serve, "save_review", side_effect=full):
                status, payload = _post_save(server, port)
            self.assertEqual(500, status)
            self.assertIn("Não consegui gravar as decisões", payload["error"])

    def test_a_failed_write_leaves_no_partial_decisions_file(self):
        real_fdopen = os.fdopen

        class FullDisk:
            """O arquivo de verdade, com a escrita falhando como num disco cheio.

            O `with` fecha o descritor, como o arquivo real faria: no Windows, o `unlink`
            de um arquivo ainda aberto falha (WinError 32) e o arquivo ficaria na pasta."""

            def __init__(self, fd, *args, **kwargs):
                self.stream = real_fdopen(fd, *args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                self.stream.close()

            def write(self, _data):
                raise OSError(errno.ENOSPC, "No space left on device")

        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            with patch.object(serve.os, "fdopen", side_effect=FullDisk), self.assertRaises(OSError):
                serve.save_review(root / "brolls", {"items": []})
            self.assertEqual([], list((root / "brolls" / serve.REVIEWS_DIR).iterdir()))


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
                    with (
                        self.assertRaises(urllib.error.HTTPError) as ctx,
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}", timeout=5),
                    ):
                        pass
                    self.assertEqual(404, ctx.exception.code, path)

                # Listagem de diretório.
                with (
                    self.assertRaises(urllib.error.HTTPError) as ctx,
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/previews/", timeout=5),
                ):
                    pass
                self.assertEqual(404, ctx.exception.code)
                with (
                    self.assertRaises(urllib.error.HTTPError) as ctx,
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/reviews/", timeout=5),
                ):
                    pass
                self.assertEqual(404, ctx.exception.code)


class EncodedPathAllowlistTests(unittest.TestCase):
    """The allowlist is decided on the file the server would open, not on the raw URL.

    The stdlib percent-decodes and normalizes the request path after any check made on
    its text, so an encoded `..` must not walk out of `previews/` or `clips/`.
    """

    def _status(self, port, raw_path):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            # http.client sends the path untouched, unlike a browser's URL bar.
            connection.request("GET", raw_path, headers={"Host": f"127.0.0.1:{port}"})
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def test_an_encoded_parent_segment_cannot_reach_internal_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            brolls = root / "brolls"
            (brolls / "previews").mkdir()
            (brolls / "clips").mkdir()
            (brolls / "reviews").mkdir()
            (brolls / "previews" / "poster.jpg").write_bytes(b"poster")
            (brolls / "clips" / "clip.mp4").write_bytes(b"clip")
            (brolls / "manifest.json").write_text("{}", encoding="utf-8")
            (brolls / "diagnostics.jsonl").write_text("{}\n", encoding="utf-8")
            (brolls / "reviews" / "decision.json").write_text("{}", encoding="utf-8")
            with _serving(root) as (_server, port):
                for allowed in ("/previews/poster.jpg", "/clips/clip.mp4", "/previews/poster.jpg?v=1", "/review.html"):
                    with self.subTest(allowed=allowed):
                        self.assertEqual(200, self._status(port, allowed))
                refused = (
                    "/previews/%2e%2e/manifest.json",
                    "/previews/%2E%2E/diagnostics.jsonl",
                    "/previews/%2e%2e/reviews/decision.json",
                    "/clips/%2e%2e/%2eserve.pid",
                    "/clips/%2e%2e/manifest.json",
                    "/previews/..%2fmanifest.json",
                    "/previews/%2e%2e%2fmanifest.json",
                    "/previews/%252e%252e/manifest.json",
                    "/previews/../manifest.json",
                    "/previews/",
                    "/previews",
                    "/previews/.hidden",
                )
                for raw in refused:
                    with self.subTest(refused=raw):
                        self.assertEqual(404, self._status(port, raw))

    def test_a_percent_encoded_nul_byte_is_refused_without_killing_the_server(self):
        """A `%00` decodes to an embedded NUL, which makes `Path.resolve()` raise
        `ValueError` instead of `OSError`. Both must be caught: the request answers 404,
        and the worker thread must not die (the server keeps answering afterwards)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            brolls = root / "brolls"
            (brolls / "previews").mkdir()
            (brolls / "previews" / "poster.jpg").write_bytes(b"poster")
            with _serving(root) as (_server, port):
                for raw in ("/previews/%00x.jpg", "/previews/%00../manifest.json"):
                    with self.subTest(refused=raw):
                        self.assertEqual(404, self._status(port, raw))
                # The server must still be alive and answering the next request.
                self.assertEqual(200, self._status(port, "/previews/poster.jpg"))

    def test_a_nul_byte_is_refused_by_name_even_where_resolve_does_not_raise(self):
        """Windows with Python 3.13 resolves a path with an embedded NUL without raising;
        the refusal cannot depend on that exception."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            previews = root / "brolls" / "previews"
            previews.mkdir()
            (previews / "poster.jpg").write_bytes(b"poster")

            def permissive(folder, served_parent, candidate):
                del served_parent, candidate
                real = folder.resolve()
                return real, real / "anything"

            with patch.object(serve, "_resolve_allowed_target", side_effect=permissive), _serving(root) as (_s, port):
                self.assertEqual(404, self._status(port, "/previews/%00x.jpg"))
                self.assertEqual(404, self._status(port, "/previews/a%1bb.jpg"))
                self.assertEqual(200, self._status(port, "/previews/poster.jpg"))

    def test_a_value_error_while_opening_the_file_is_a_clean_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            previews = root / "brolls" / "previews"
            previews.mkdir()
            (previews / "poster.jpg").write_bytes(b"poster")
            with (
                patch.object(serve.SimpleHTTPRequestHandler, "send_head", side_effect=ValueError("embedded null")),
                _serving(root) as (_s, port),
            ):
                self.assertEqual(404, self._status(port, "/previews/poster.jpg"))


class SymlinkEscapeTests(unittest.TestCase):
    """Um symlink plantado dentro de previews/ não pode servir arquivo de fora."""

    @needs_symlink
    def test_a_symlink_inside_previews_pointing_outside_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            outside = Path(tmp).parent / f"gb-outside-{os.getpid()}.txt"
            outside.write_text("segredo", encoding="utf-8")
            try:
                previews = root / "brolls" / "previews"
                previews.mkdir(parents=True)
                (previews / "escape.jpg").symlink_to(outside)
                with _serving(root) as (_server, port):
                    with (
                        self.assertRaises(urllib.error.HTTPError) as ctx,
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/previews/escape.jpg", timeout=5),
                    ):
                        pass
                    self.assertEqual(404, ctx.exception.code)
            finally:
                outside.unlink(missing_ok=True)

    @needs_symlink
    def test_previews_itself_as_a_symlink_to_an_outside_folder_is_refused(self):
        """`brolls/previews` resolved THROUGH its own symlink used to serve the whole
        target tree: the allowlist check must refuse when the top-level folder is a
        link, not just when a file inside it is."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            outside = Path(tmp).parent / f"gb-outside-dir-{os.getpid()}"
            outside.mkdir()
            (outside / "secret.txt").write_text("segredo", encoding="utf-8")
            try:
                (root / "brolls" / "previews").symlink_to(outside, target_is_directory=True)
                with _serving(root) as (_server, port):
                    with (
                        self.assertRaises(urllib.error.HTTPError) as ctx,
                        urllib.request.urlopen(f"http://127.0.0.1:{port}/previews/secret.txt", timeout=5),
                    ):
                        pass
                    self.assertEqual(404, ctx.exception.code)
            finally:
                (root / "brolls" / "previews").unlink()

                shutil.rmtree(outside)


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
            pid_file.chmod(0o644)
            log_file.chmod(0o644)
            _start_background(self, root)
            try:
                self.assertEqual(0, stat.S_IMODE(pid_file.stat().st_mode) & 0o077)
                self.assertEqual(0, stat.S_IMODE(log_file.stat().st_mode) & 0o077)
            finally:
                serve.stop(root)


class LogRotationTests(unittest.TestCase):
    """Item 6: `.serve.log.1` is created private (never world-readable, never a
    symlink target) instead of written-then-chmod'd."""

    @unittest.skipIf(os.name == "nt", "bits de permissão são um conceito POSIX")
    def test_rotated_log_is_0600_right_after_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            log = root / "brolls" / serve.LOG_FILE
            log.write_text("log da rodada anterior\n", encoding="utf-8")
            log.chmod(0o644)
            serve._rotate_log(log)
            rotated = root / "brolls" / serve.LOG_ROTATE_FILE
            self.assertTrue(rotated.is_file())
            self.assertEqual(0, stat.S_IMODE(rotated.stat().st_mode) & 0o077)
            self.assertEqual("log da rodada anterior\n", rotated.read_text(encoding="utf-8"))

    @needs_symlink
    def test_a_symlink_planted_at_the_rotated_name_is_not_written_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            brolls = root / "brolls"
            log = brolls / serve.LOG_FILE
            log.write_text("conteudo novo\n", encoding="utf-8")
            victim = brolls / "victim.txt"
            victim.write_text("nao mexer", encoding="utf-8")
            rotated = brolls / serve.LOG_ROTATE_FILE
            rotated.symlink_to(victim)
            serve._rotate_log(log)
            self.assertEqual("nao mexer", victim.read_text(encoding="utf-8"))

    @needs_symlink
    def test_a_link_at_the_rotated_name_is_refused_where_there_is_no_nofollow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp))
            brolls = root / "brolls"
            log = brolls / serve.LOG_FILE
            log.write_text("conteudo novo\n", encoding="utf-8")
            victim = brolls / "victim.txt"
            victim.write_text("nao mexer", encoding="utf-8")
            rotated = brolls / serve.LOG_ROTATE_FILE
            rotated.symlink_to(victim)
            with without_nofollow():
                serve._rotate_log(log)
            self.assertEqual("nao mexer", victim.read_text(encoding="utf-8"))
            self.assertTrue(rotated.is_symlink())


class PrivateTextLinkTests(unittest.TestCase):
    """`.serve.pid` and `.serve.log` are never written through a link planted at their name."""

    @needs_symlink
    def test_a_link_at_the_target_is_refused_where_there_is_no_nofollow(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            victim = directory / "victim.txt"
            victim.write_text("nao mexer", encoding="utf-8")
            target = directory / serve.PID_FILE
            target.symlink_to(victim)
            with without_nofollow(), self.assertRaises(OSError) as caught:
                serve._write_private_text(target, "texto")
            self.assertEqual(errno.ELOOP, caught.exception.errno)
            self.assertEqual("nao mexer", victim.read_text(encoding="utf-8"))
            self.assertTrue(target.is_symlink())

    @needs_symlink
    def test_a_dangling_link_is_refused_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            planted = directory / "plantado.txt"
            target = directory / serve.LOG_FILE
            target.symlink_to(planted)
            with without_nofollow(), self.assertRaises(OSError):
                serve._write_private_text(target, "")
            self.assertFalse(planted.exists())

    def test_a_plain_file_is_still_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / serve.PID_FILE
            target.write_text("antigo", encoding="utf-8")
            with without_nofollow():
                serve._write_private_text(target, "novo")
            self.assertEqual("novo", target.read_text(encoding="utf-8"))


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


class BackgroundDoesNotReloadSecretsTests(unittest.TestCase):
    """O pai tira as chaves do ambiente do filho; o filho não pode trazê-las de volta lendo o `.env`."""

    SECRETS = ("PEXELS_API_KEY", "PIXABAY_API_KEY", "YOUTUBE_API_KEY")

    def write_env(self, folder):
        path = Path(folder) / "chaves.env"
        path.write_text(
            "PEXELS_API_KEY=segredo-pexels\nPIXABAY_API_KEY=segredo-pixabay\nYOUTUBE_API_KEY=segredo-youtube\n"
            "GB_GIF_WIDTH=400\nGB_LOG_LEVEL=DEBUG\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    @contextmanager
    def clean_environment(self, *, marker=None):
        """Ambiente sem as chaves e com `marker` (ou nenhum) no marcador; o registro de `load_env` limpo."""
        names = (*self.SECRETS, "GB_GIF_WIDTH", "GB_LOG_LEVEL", serve.CHILD_ENV)
        with patch.dict(os.environ), patch.dict(config._ENV_APPLIED):  # pylint: disable=protected-access
            for name in names:
                os.environ.pop(name, None)
            if marker is not None:
                os.environ[serve.CHILD_ENV] = marker
            yield

    def load(self, command, folder):
        """O que o despacho do comando faz com o `.env` (`load_environment`); devolve o que o ambiente ficou tendo."""
        args = Namespace(command=command, project=None, env_file=str(self.write_env(folder)), profile=None)
        env_loader.load_environment(args, warn=False)
        return {key: os.environ.get(key) for key in (*self.SECRETS, "GB_GIF_WIDTH")}

    def test_the_marker_is_the_one_the_parent_puts_in_the_child_environment(self):
        self.assertEqual(config.SERVE_CHILD_ENV, serve.CHILD_ENV)
        self.assertEqual(config.SERVE_CHILD_TOKEN, serve._child_environment({}, None)[config.SERVE_CHILD_ENV])
        self.assertNotIn(config.SERVE_CHILD_TOKEN, ("", "0", "1"))

    def test_the_serve_child_skips_secret_keys_and_still_loads_the_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp, self.clean_environment(marker=config.SERVE_CHILD_TOKEN):
            loaded = self.load("serve", tmp)
            for key in self.SECRETS:
                self.assertIsNone(loaded[key], key)
            self.assertEqual("400", loaded["GB_GIF_WIDTH"])
            self.assertEqual(400, config.settings()["width"])

    def test_load_env_withholds_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp, self.clean_environment():
            applied = env_loader.load_env(self.write_env(tmp), withhold_secrets=True)
            self.assertEqual({"GB_GIF_WIDTH", "GB_LOG_LEVEL"}, set(applied))
        with tempfile.TemporaryDirectory() as tmp, self.clean_environment():
            applied = env_loader.load_env(self.write_env(tmp))
            self.assertTrue(set(self.SECRETS) <= set(applied))

    def test_a_foreground_command_with_the_variable_exported_still_reads_the_keys(self):
        for command in ("search", "doctor", "serve"):
            for value in (config.SERVE_CHILD_TOKEN, "1", "0", "true"):
                if command == "serve" and value == config.SERVE_CHILD_TOKEN:
                    continue  # esse é o filho de fundo
                with (
                    self.subTest(command=command, value=value),
                    tempfile.TemporaryDirectory() as tmp,
                    self.clean_environment(marker=value),
                ):
                    loaded = self.load(command, tmp)
                    self.assertEqual("segredo-pexels", loaded["PEXELS_API_KEY"])
                    self.assertEqual("segredo-youtube", loaded["YOUTUBE_API_KEY"])

    def test_every_process_other_than_the_serve_child_still_reads_the_keys(self):
        with tempfile.TemporaryDirectory() as tmp, self.clean_environment():
            loaded = self.load("search", tmp)
            self.assertEqual("segredo-pexels", loaded["PEXELS_API_KEY"])

    def test_a_real_background_child_never_sees_the_keys_of_the_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp) / "projeto")
            env_file = self.write_env(tmp)
            with self.clean_environment():
                os.environ["GB_ENV_FILE"] = str(env_file)
                started = _start_background(self, root)
                try:
                    self.assertTrue(started["background"])
                    log = (root / "brolls" / "getbrolls.log").read_text(encoding="utf-8")
                finally:
                    serve.stop(root)
            lines = [line for line in log.splitlines() if "event=config" in line]
            # O `.env` foi lido (o nível DEBUG vem dele, e a linha só existe nesse nível) e as chaves ficaram de fora.
            self.assertEqual(1, len(lines), log)
            self.assertRegex(lines[0], r"provider_keys=(?![a-z])")
            for secret in ("segredo-pexels", "segredo-pixabay", "segredo-youtube"):
                self.assertNotIn(secret, log)


class MissingStoryboardMessageTests(unittest.TestCase):
    def test_the_storyboard_of_the_project_is_named_under_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "brolls").mkdir()
            for call in (serve.start, serve.start_background):
                with self.subTest(call=call.__name__), self.assertRaises(ValueError) as caught:
                    call(root)
                self.assertIn("<projeto>/brolls/review.html", str(caught.exception))
                self.assertNotIn(str(root), str(caught.exception))


class ServeLogRedactionTests(unittest.TestCase):
    """O que o servidor de fundo escreve em `.serve.log` passa pela mesma redação do `getbrolls.log`."""

    def test_the_child_environment_asks_for_the_filter(self):
        self.assertEqual(config.SERVE_CHILD_TOKEN, serve._child_environment({}, None)[serve.CHILD_ENV])

    def test_a_foreground_process_is_left_alone(self):
        before = (serve.sys.stdout, serve.sys.stderr)
        self.assertFalse(serve.filter_output({}))
        self.assertEqual(before, (serve.sys.stdout, serve.sys.stderr))

    def test_every_line_of_the_child_is_redacted_before_it_is_written(self):
        raw = io.StringIO()
        stream = serve.RedactedStream(raw)
        stream.write("login password=hunter2 falhou em /Volumes/Externo/pessoa/a.mp4: sem acesso\n")
        stream.write("par")
        stream.write("cial token=abc")
        stream.write("defghijk\nFim /Volumes/pessoa/b c.mp4")
        self.assertEqual(2, raw.getvalue().count("\n"))  # the last line has no break yet
        stream.finish()
        out = raw.getvalue()
        for word in ("hunter2", "Externo", "pessoa", "abcdefghijk", "b c"):
            self.assertNotIn(word, out)
        self.assertIn("password=[REDACTED]", out)
        self.assertIn(": sem acesso", out)
        self.assertEqual(3, out.count("\n"))
        self.assertFalse(stream.isatty())

    def test_an_endless_line_is_omitted_not_kept(self):
        raw = io.StringIO()
        stream = serve.RedactedStream(raw)
        stream.write("x" * (serve.labels.INPUT_MAX + 1))
        self.assertIn("<texto omitido:", raw.getvalue())
        self.assertNotIn("xxxx", raw.getvalue())

    def test_the_urls_line_stays_readable_for_the_parent_and_names_the_folder_by_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp).resolve())
            raw = io.StringIO()
            stream = serve.RedactedStream(raw)
            closed = []
            fake = MagicMock()
            fake.server_close.side_effect = lambda: closed.append(True)
            fake.session_id = "sessao"
            with (
                patch.object(serve, "start", return_value=(fake, 4321)),
                patch.object(serve.effects, "serve"),
                patch.object(serve.sys, "stdout", stream),
            ):
                serve.run(root, port=0)
            payload = json.loads(raw.getvalue())
            self.assertEqual(
                ["http://localhost:4321/review.html", "http://127.0.0.1:4321/review.html"], payload["urls"]
            )
            self.assertNotIn(str(root), raw.getvalue())
            self.assertTrue(payload["directory"])

    def test_the_real_child_writes_no_machine_path_in_its_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_with_review(Path(tmp).resolve())
            started = serve.start_background(root, port=0)
            try:
                text = (root / "brolls" / serve.LOG_FILE).read_text(encoding="utf-8")
            finally:
                serve.stop(root)
            self.assertIn(str(started["port"]), text)
            self.assertNotIn(str(root), text)


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
            # Aninhado de propósito: combinar os dois `with` faz o pylint reportar
            # "used-before-assignment" de `port` (falso positivo).
            with _serving(root) as (server, port):  # noqa: SIM117
                with patch.object(serve.json, "loads", return_value={"session": 12345, "port": port}):
                    self.assertFalse(serve._ping(port, server.session_id, timeout=2.0))


if __name__ == "__main__":
    unittest.main()
