"""Bordas do servidor do Storyboard: recusas do `POST /__save` (corpo JSON), gravação das
decisões (colisão de nome, falha no meio, erro do disco), allowlist de arquivos, ping de
identidade, PID file e o log do servidor de fundo."""

import contextlib
import errno
import http.client
import io
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock, patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _posix
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

from getbrolls import layout, serve

STAMP = "20260101-000000"


def _project(root):
    brolls = Path(root) / "brolls"
    brolls.mkdir(parents=True)
    (brolls / "review.html").write_text("<html><head></head><body>storyboard</body></html>", encoding="utf-8")
    return Path(root)


@contextmanager
def _serving(root):
    server, port = serve.start(root, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _request(port, method, path, *, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _post_save(port, token, body, extra_headers=None):
    headers = {"Content-Type": "application/json", serve.TOKEN_HEADER: token}
    headers.update(extra_headers or {})
    return _request(port, "POST", serve.SAVE_PATH, body=body, headers=headers)


class PostRefusalTests(unittest.TestCase):
    """Toda recusa do POST responde um JSON `{"error": ...}` e não grava nada."""

    def _assert_refusal(self, answer, status, fragment):
        code, raw = answer
        self.assertEqual(status, code)
        payload = json.loads(raw.decode("utf-8"))
        self.assertEqual(["error"], list(payload))
        self.assertIn(fragment, payload["error"])

    def test_foreign_host_origin_unknown_path_and_bad_token_are_refused_in_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with _serving(root) as (server, port):
                token = server.save_token or ""
                self._assert_refusal(_post_save(port, token, b"{}", {"Host": "evil.example"}), 403, "outra origem")
                self._assert_refusal(
                    _post_save(port, token, b"{}", {"Origin": "http://evil.example"}), 403, "outra origem"
                )
                self._assert_refusal(
                    _request(
                        port, "POST", "/outro", body=b"{}", headers={serve.TOKEN_HEADER: token, "Content-Length": "2"}
                    ),
                    404,
                    "desconhecido",
                )
                self._assert_refusal(_post_save(port, "errado", b"{}"), 403, "Token")
                self._assert_refusal(_post_save(port, "", b"{}"), 403, "Token")
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_a_non_numeric_content_length_is_a_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with _serving(root) as (server, port):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.putrequest("POST", serve.SAVE_PATH)
                    connection.putheader(serve.TOKEN_HEADER, server.save_token or "")
                    connection.putheader("Content-Length", "abc")
                    connection.endheaders()
                    response = connection.getresponse()
                    self._assert_refusal((response.status, response.read()), 400, "Tamanho do corpo")
                finally:
                    connection.close()
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_an_empty_or_negative_length_is_a_413(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with _serving(root) as (server, port):
                for length in ("0", "-5"):
                    with self.subTest(length=length):
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                        try:
                            connection.putrequest("POST", serve.SAVE_PATH)
                            connection.putheader(serve.TOKEN_HEADER, server.save_token or "")
                            connection.putheader("Content-Length", length)
                            connection.endheaders()
                            response = connection.getresponse()
                            self._assert_refusal((response.status, response.read()), 413, "grande demais")
                        finally:
                            connection.close()
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_an_oversized_body_that_the_client_cuts_short_still_gets_the_413(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with _serving(root) as (server, port):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.putrequest("POST", serve.SAVE_PATH)
                    connection.putheader(serve.TOKEN_HEADER, server.save_token or "")
                    connection.putheader("Content-Length", str(serve.MAX_SAVE_BYTES + 10))
                    connection.endheaders()
                    connection.send(b"x" * 10)
                    assert connection.sock is not None
                    # EOF no meio do corpo: o servidor para de drenar e ainda responde.
                    connection.sock.shutdown(socket.SHUT_WR)
                    response = connection.getresponse()
                    self._assert_refusal((response.status, response.read()), 413, "grande demais")
                finally:
                    connection.close()
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_invalid_utf8_non_json_and_non_object_bodies_are_400(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with _serving(root) as (server, port):
                token = server.save_token or ""
                self._assert_refusal(_post_save(port, token, b"\xff\xfe"), 400, "JSON")
                self._assert_refusal(_post_save(port, token, b"{nope"), 400, "JSON")
                for body in (b"[1, 2]", b'"texto"', b"7", b"null"):
                    with self.subTest(body=body):
                        self._assert_refusal(_post_save(port, token, body), 400, "objeto JSON")
            self.assertFalse((root / "brolls" / "reviews").exists())

    def test_a_query_string_on_the_save_path_still_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with _serving(root) as (server, port):
                answer = _request(
                    port,
                    "POST",
                    serve.SAVE_PATH + "?x=1",
                    body=b'{"items": []}',
                    headers={serve.TOKEN_HEADER: server.save_token or ""},
                )
            self.assertEqual(200, answer[0])
            saved = Path(json.loads(answer[1])["path"])
            self.assertEqual({"items": []}, json.loads(saved.read_text(encoding="utf-8")))

    @needs_symlink
    def test_save_answers_409_for_a_planted_link_and_500_for_a_disk_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            (root / "brolls" / "reviews").symlink_to(elsewhere, target_is_directory=True)
            with _serving(root) as (server, port):
                token = server.save_token or ""
                self._assert_refusal(_post_save(port, token, b"{}"), 409, "link simbólico")
                self.assertEqual([], list(elsewhere.iterdir()))
                (root / "brolls" / "reviews").unlink()
                with patch.object(serve.os, "fdopen", side_effect=OSError(errno.ENOSPC, "No space left on device")):
                    # o `handle` aberto fica pendurado pelo erro simulado: o `save_review` apaga o arquivo
                    self._assert_refusal(_post_save(port, token, b"{}"), 500, "No space left on device")
            self.assertEqual([], list((root / "brolls" / "reviews").iterdir()))


class AllowlistEdgeTests(unittest.TestCase):
    def test_a_directory_inside_previews_is_never_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "brolls" / "previews" / "sub").mkdir(parents=True)
            (root / "brolls" / "previews" / "sub" / "a.jpg").write_bytes(b"jpg")
            with _serving(root) as (_server, port):
                status, raw = _request(port, "GET", "/previews/sub/")
                self.assertEqual(404, status)
                self.assertEqual({"error": "Não encontrado."}, json.loads(raw))
                self.assertNotIn(b"a.jpg", raw)
                self.assertEqual((200, b"jpg"), _request(port, "GET", "/previews/sub/a.jpg"))

    def test_a_vanished_review_page_is_a_404_not_a_directory_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with _serving(root) as (_server, port):
                self.assertEqual(200, _request(port, "GET", "/review.html")[0])
                (root / "brolls" / "review.html").unlink()
                for path in ("/review.html", "/"):
                    with self.subTest(path=path):
                        self.assertEqual(404, _request(port, "GET", path)[0])

    def test_layout_one_serves_previews_through_the_stock_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(Path(tmp).resolve())
            layout.write_project(root, layout.new_project_doc())
            (root / "broll").mkdir()
            (root / "brolls" / "previews").mkdir()
            (root / "brolls" / "previews" / "p.jpg").write_bytes(b"poster")
            with _serving(root) as (_server, port):
                self.assertEqual((200, b"poster"), _request(port, "GET", "/previews/p.jpg"))
                self.assertEqual(404, _request(port, "GET", "/previews/nope.jpg")[0])


class ClientGoneTests(unittest.TestCase):
    """Cliente que some no meio da resposta (aba fechada, `urlopen` sem ler o corpo) não é falha do servidor."""

    LOGGER = "getbrolls.serve"
    CLIENTS = 3

    @staticmethod
    def wait_for(condition, seconds=10.0):
        """Espera `condition()` valer por até `seconds` segundos; devolve o último valor."""
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.02)
        return condition()

    def test_a_client_that_resets_the_connection_mid_download_leaves_no_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "brolls" / "previews").mkdir()
            (root / "brolls" / "previews" / "big.gif").write_bytes(b"x" * (16 << 20))
            (root / "brolls" / "previews" / "small.gif").write_bytes(b"small")
            err = io.StringIO()
            with (
                _serving(root) as (_server, port),
                contextlib.redirect_stderr(err),
                self.assertLogs(self.LOGGER, "DEBUG") as seen,
            ):
                for _ in range(self.CLIENTS):
                    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                        request = f"GET /previews/big.gif HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n"
                        sock.sendall(request.encode())
                        sock.recv(64)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # RST ao fechar
                # Cada cliente que sumiu deixa uma linha DEBUG: é a condição de espera (e a prova de que o
                # servidor passou por `handle_error`), sem um tempo fixo.
                self.wait_for(lambda: len(seen.records) >= self.CLIENTS)
                self.assertEqual((200, b"small"), _request(port, "GET", "/previews/small.gif"))
            self.assertEqual("", err.getvalue())
            self.assertEqual(self.CLIENTS, len(seen.records))
            for record in seen.records:
                self.assertRegex(record.getMessage(), r"^event=client_gone error=(BrokenPipe|ConnectionReset)Error$")

    def test_only_a_client_that_went_away_is_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp, _serving(_project(tmp)) as (server, _port):
            for gone in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                err = io.StringIO()
                with (
                    self.subTest(error=gone.__name__),
                    contextlib.redirect_stderr(err),
                    self.assertLogs(self.LOGGER, "DEBUG") as seen,
                ):
                    try:
                        raise gone("mensagem que não pode ir para o log")
                    except gone:
                        server.handle_error(MagicMock(), ("127.0.0.1", 1))
                self.assertEqual("", err.getvalue())
                # Uma linha, só com a classe do erro (nunca o texto dele).
                self.assertEqual([f"event=client_gone error={gone.__name__}"], [r.getMessage() for r in seen.records])
                self.assertEqual(["DEBUG"], [r.levelname for r in seen.records])
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertNoLogs(self.LOGGER, "DEBUG"):
                try:
                    raise ValueError("a real failure")
                except ValueError:
                    server.handle_error(MagicMock(), ("127.0.0.1", 1))
            self.assertIn("ValueError: a real failure", err.getvalue())

    def test_the_line_is_debug_only(self):
        # Sem DEBUG (o padrão do log é INFO) a linha não existe: nada em INFO ou acima, e nenhum traceback.
        with tempfile.TemporaryDirectory() as tmp, _serving(_project(tmp)) as (server, _port):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertNoLogs(self.LOGGER, "INFO"):
                try:
                    raise ConnectionResetError("client closed")
                except ConnectionResetError:
                    server.handle_error(MagicMock(), ("127.0.0.1", 1))
            self.assertEqual("", err.getvalue())


class TargetResolutionTests(unittest.TestCase):
    class _Unresolvable:
        def __init__(self, error):
            self.error = error

        def resolve(self, strict=False):
            del strict
            raise self.error

    def test_a_candidate_that_cannot_be_resolved_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            folder = parent / "previews"
            folder.mkdir()
            for error in (ValueError("embedded null byte"), OSError(errno.ELOOP, "loop")):
                with self.subTest(error=type(error).__name__):
                    stub = self._Unresolvable(error)
                    self.assertIsNone(serve._resolve_allowed_target(folder, parent, stub))  # type: ignore[arg-type]

    def test_a_folder_that_is_not_a_direct_child_of_its_trusted_parent_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            nested = parent / "a" / "previews"
            nested.mkdir(parents=True)
            (nested / "x.jpg").write_bytes(b"x")
            self.assertIsNone(serve._resolve_allowed_target(nested, parent, nested / "x.jpg"))
            direct = parent / "previews"
            direct.mkdir()
            self.assertEqual(
                (direct, direct / "x.jpg"), serve._resolve_allowed_target(direct, parent, direct / "x.jpg")
            )

    def test_a_translation_outside_the_served_directory_has_no_logical_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = object.__new__(serve._NoCacheHandler)
            handler.directory = tmp
            handler.clip_roots = ()
            with patch.object(SimpleHTTPRequestHandler, "translate_path", return_value="/elsewhere/x.mp4"):
                self.assertIsNone(handler._logical_parts("/clips/x.mp4"))
                # camada de layout 1: sem partes lógicas, vale a tradução da stdlib
                self.assertEqual("/elsewhere/x.mp4", handler.translate_path("/clips/x.mp4"))

    def test_a_non_clips_path_keeps_the_stock_translation_in_layout_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = object.__new__(serve._NoCacheHandler)
            handler.directory = tmp
            handler.clip_roots = ()
            self.assertEqual(str(Path(tmp) / "previews" / "p.jpg"), handler.translate_path("/previews/p.jpg"))
            self.assertEqual(str(Path(tmp) / "clips"), handler.translate_path("/clips"))


class SaveReviewEdgeTests(unittest.TestCase):
    def _brolls(self, tmp):
        brolls = Path(tmp) / "brolls"
        brolls.mkdir()
        return brolls

    def test_a_name_taken_in_the_same_second_gets_the_next_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = self._brolls(tmp)
            with patch.object(serve.time, "strftime", return_value=STAMP):
                first = serve.save_review(brolls, {"n": 1})
                second = serve.save_review(brolls, {"n": 2})
                third = serve.save_review(brolls, {"n": 3})
            self.assertEqual(
                [f"{STAMP}.json", f"{STAMP}-1.json", f"{STAMP}-2.json"], [first.name, second.name, third.name]
            )
            self.assertEqual(
                [1, 2, 3], [json.loads(p.read_text(encoding="utf-8"))["n"] for p in (first, second, third)]
            )

    def test_giving_up_after_fifty_collisions_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = self._brolls(tmp)
            with (
                patch.object(serve.time, "strftime", return_value=STAMP),
                patch.object(serve.os, "open", side_effect=FileExistsError(errno.EEXIST, "exists")) as opened,
                self.assertRaises(FileExistsError),
            ):
                serve.save_review(brolls, {"n": 1})
            self.assertEqual(51, opened.call_count)
            self.assertEqual([], list((brolls / "reviews").iterdir()))

    @needs_symlink
    def test_a_link_planted_between_the_check_and_the_open_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = self._brolls(tmp)
            victim = Path(tmp) / "victim.txt"
            victim.write_text("nao mexer", encoding="utf-8")
            real_open = os.open

            def racing_open(path, flags, mode=0o777):
                if str(path).endswith(f"{STAMP}.json"):
                    Path(path).symlink_to(victim)
                return real_open(path, flags, mode)

            with (
                patch.object(serve.time, "strftime", return_value=STAMP),
                patch.object(serve.os, "open", side_effect=racing_open),
                self.assertRaisesRegex(ValueError, "link simbólico"),
            ):
                serve.save_review(brolls, {"n": 1})
            self.assertEqual("nao mexer", victim.read_text(encoding="utf-8"))

    def test_an_os_error_on_open_becomes_a_value_error_with_the_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = self._brolls(tmp)
            with (
                patch.object(serve.os, "open", side_effect=PermissionError(errno.EACCES, "Permission denied")),
                self.assertRaises(ValueError) as ctx,
            ):
                serve.save_review(brolls, {"n": 1})
            self.assertIn("Permission denied", str(ctx.exception))
            # The folder by its label (`labels.shown`), never the machine path.
            self.assertIn("/brolls/reviews/", str(ctx.exception))
            self.assertNotIn(str(brolls), str(ctx.exception))
            self.assertEqual([], list((brolls / "reviews").iterdir()))

    def test_a_write_that_fails_midway_leaves_no_truncated_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = self._brolls(tmp)
            real_fdopen = os.fdopen

            class Failing:
                def __init__(self, handle):
                    self.stream = real_fdopen(handle, "wb")

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    self.stream.close()
                    return False

                def write(self, _body):
                    raise OSError(errno.ENOSPC, "No space left on device")

            with (
                patch.object(serve.os, "fdopen", side_effect=lambda handle, _mode: Failing(handle)),
                self.assertRaises(OSError) as ctx,
            ):
                serve.save_review(brolls, {"n": 1})
            self.assertEqual(errno.ENOSPC, ctx.exception.errno)
            self.assertEqual([], list((brolls / "reviews").iterdir()))

    def test_a_failed_fdopen_closes_the_descriptor_once_and_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = self._brolls(tmp)
            with (
                patch.object(serve.os, "fdopen", side_effect=OSError(errno.ENOSPC, "No space left on device")),
                patch.object(serve.os, "close", wraps=os.close) as closed,
                self.assertRaises(OSError) as ctx,
            ):
                serve.save_review(brolls, {"n": 1})
            self.assertEqual(errno.ENOSPC, ctx.exception.errno)
            self.assertEqual(1, closed.call_count)
            with self.assertRaises(OSError):  # o descritor devolvido ao SO não é mais válido
                os.fstat(closed.call_args.args[0])
            self.assertEqual([], list((brolls / "reviews").iterdir()))

    def test_a_cleanup_that_fails_does_not_replace_the_disk_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = self._brolls(tmp)
            with (
                patch.object(serve.os, "fdopen", side_effect=OSError(errno.ENOSPC, "No space left on device")),
                patch.object(serve.effects, "unlink", side_effect=PermissionError(errno.EACCES, "em uso")),
                self.assertRaises(OSError) as ctx,
            ):
                serve.save_review(brolls, {"n": 1})
            self.assertEqual(errno.ENOSPC, ctx.exception.errno)

    def test_an_already_closed_descriptor_after_a_failed_fdopen_keeps_the_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            brolls = self._brolls(tmp)

            def closes_then_fails(handle, _mode):
                os.close(handle)  # `io.open` fails after the raw file exists and may have closed it already
                raise OSError(errno.ENOSPC, "No space left on device")

            with (
                patch.object(serve.os, "fdopen", side_effect=closes_then_fails),
                self.assertRaises(OSError) as ctx,
            ):
                serve.save_review(brolls, {"n": 1})
            self.assertEqual(errno.ENOSPC, ctx.exception.errno)

    def test_a_failed_fdopen_closes_the_descriptor_of_a_private_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".serve.pid"
            with (
                patch.object(serve.os, "fdopen", side_effect=OSError(errno.ENOSPC, "No space left on device")),
                patch.object(serve.os, "close", wraps=os.close) as closed,
                self.assertRaises(OSError) as ctx,
            ):
                serve._write_private_text(target, "texto")
            self.assertEqual(errno.ENOSPC, ctx.exception.errno)
            self.assertEqual(1, closed.call_count)
            with self.assertRaises(OSError):
                os.fstat(closed.call_args.args[0])

    def test_a_chmod_failure_closes_the_descriptor_and_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / ".serve.pid"
            with (
                patch.object(Path, "chmod", side_effect=PermissionError(errno.EPERM, "nope")),
                patch.object(serve.os, "close", wraps=os.close) as closed,
                self.assertRaises(PermissionError),
            ):
                serve._write_private_text(target, "texto")
            self.assertEqual(1, closed.call_count)
            with self.assertRaises(OSError):  # o descritor devolvido ao SO não é mais válido
                os.fstat(closed.call_args.args[0])


def _ping_answer(payload):
    opener = MagicMock()
    opener.return_value.__enter__.return_value.read.return_value = payload
    return opener


class PingTests(unittest.TestCase):
    def test_missing_port_or_session_never_opens_a_socket(self):
        with patch.object(serve, "_open_ping") as opened:
            self.assertFalse(serve._ping(None, "sessao"))
            self.assertFalse(serve._ping(0, "sessao"))
            self.assertFalse(serve._ping(8767, ""))
            self.assertFalse(serve._ping(8767, None))
            self.assertFalse(serve._ping(8767, 12345))
        opened.assert_not_called()

    def test_a_target_that_is_not_local_http_is_never_contacted(self):
        with (
            patch.object(serve, "_valid_ping_target", return_value=False),
            patch.object(serve, "_open_ping") as opened,
        ):
            self.assertFalse(serve._ping(8767, "sessao"))
        opened.assert_not_called()

    def test_valid_ping_target_pins_scheme_and_host(self):
        self.assertTrue(serve._valid_ping_target("http://127.0.0.1:9/__ping"))
        self.assertTrue(serve._valid_ping_target("http://localhost:9/__ping"))
        self.assertFalse(serve._valid_ping_target("https://127.0.0.1:9/__ping"))
        self.assertFalse(serve._valid_ping_target("http://example.com:9/__ping"))
        self.assertFalse(serve._valid_ping_target("file:///etc/passwd"))

    def test_only_a_matching_string_session_counts(self):
        cases = [
            (json.dumps({"session": "sessao"}).encode(), True),
            (json.dumps({"session": "outra"}).encode(), False),
            (json.dumps({"session": 7}).encode(), False),
            (json.dumps({"port": 1}).encode(), False),
            (json.dumps(["sessao"]).encode(), False),
            (b"isto nao e json", False),
            (b"\xff\xfe", False),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload), patch.object(serve, "_open_ping", _ping_answer(payload)):
                self.assertIs(expected, serve._ping(8767, "sessao"))

    def test_network_errors_are_not_the_server(self):
        for error in (urllib.error.URLError("recusado"), ConnectionRefusedError(), TimeoutError()):
            with self.subTest(error=type(error).__name__), patch.object(serve, "_open_ping", side_effect=error):
                self.assertFalse(serve._ping(8767, "sessao"))

    def test_the_url_and_timeout_come_from_the_arguments(self):
        opener = _ping_answer(json.dumps({"session": "s"}).encode())
        with patch.object(serve, "_open_ping", opener):
            self.assertTrue(serve._ping(4321, "s", timeout=0.5))
        self.assertEqual(("http://127.0.0.1:4321/__ping",), opener.call_args.args)
        self.assertEqual(0.5, opener.call_args.kwargs["timeout"])


class OursTests(unittest.TestCase):
    def test_empty_data_or_a_dead_pid_is_not_ours_and_costs_no_ping(self):
        with patch.object(serve, "_ping") as pinged:
            self.assertFalse(serve._ours({}))
            self.assertFalse(serve._ours(None))
            with patch.object(serve, "_alive", return_value=False):
                self.assertFalse(serve._ours({"pid": 123, "port": 1, "session": "s"}))
        pinged.assert_not_called()

    def test_only_a_positive_int_pid_is_reaped(self):
        with patch.object(serve, "_reap") as reaped, patch.object(serve, "_alive", return_value=False):
            for pid in ("12", -3, 0, None, 1.5):
                serve._ours({"pid": pid})
            reaped.assert_not_called()
            serve._ours({"pid": 99})
            reaped.assert_called_once_with(99)

    def test_a_ping_that_succeeds_on_the_retry_is_ours(self):
        data = {"pid": 99, "port": 5, "session": "s"}
        with (
            patch.object(serve, "_reap"),
            patch.object(serve, "_alive", return_value=True),
            patch.object(serve, "_ping", side_effect=[False, True]) as pinged,
        ):
            self.assertTrue(serve._ours(data, timeout=0.7))
        self.assertEqual(2, pinged.call_count)
        self.assertEqual(0.7, pinged.call_args.kwargs["timeout"])

    def test_a_process_that_dies_between_the_pings_is_not_ours(self):
        data = {"pid": 99, "port": 5, "session": "s"}
        with (
            patch.object(serve, "_reap"),
            patch.object(serve, "_alive", side_effect=[True, False]),
            patch.object(serve, "_ping", return_value=False) as pinged,
        ):
            self.assertFalse(serve._ours(data))
        self.assertEqual(1, pinged.call_count)

    def test_a_live_pid_that_never_answers_is_not_ours(self):
        data = {"pid": 99, "port": 5, "session": "s"}
        with (
            patch.object(serve, "_reap"),
            patch.object(serve, "_alive", return_value=True),
            patch.object(serve, "_ping", return_value=False) as pinged,
        ):
            self.assertFalse(serve._ours(data))
        self.assertEqual(serve.PING_RETRIES + 1, pinged.call_count)


class PosixAliveTests(unittest.TestCase):
    def test_kill_errors_mean_not_alive_and_success_means_alive(self):
        with patch.object(serve, "_windows", return_value=False):
            with patch.object(serve.os, "kill", return_value=None) as killed:
                self.assertTrue(serve._alive(321))
            killed.assert_called_once_with(321, 0)
            for error in (ProcessLookupError(), PermissionError(), OSError(errno.EINVAL, "invalid")):
                with self.subTest(error=type(error).__name__), patch.object(serve.os, "kill", side_effect=error):
                    self.assertFalse(serve._alive(321))

    def test_a_pid_that_is_not_a_positive_int_is_never_probed(self):
        with patch.object(serve.os, "kill") as killed:
            for pid in (None, 0, -1, "12", 1.5):
                with self.subTest(pid=pid):
                    self.assertFalse(serve._alive(pid))
        killed.assert_not_called()

    def test_reap_collects_a_finished_child_without_blocking(self):
        with (
            _posix.posix_names(),
            patch.object(serve, "_windows", return_value=False),
            patch.object(serve.os, "waitpid") as waited,
        ):
            serve._reap(55)
        waited.assert_called_once_with(55, _posix.WNOHANG)

    def test_reap_ignores_a_pid_that_is_not_our_child(self):
        for error in (ChildProcessError(), OSError(errno.ECHILD, "no child")):
            with (
                self.subTest(error=type(error).__name__),
                _posix.posix_names(),
                patch.object(serve, "_windows", return_value=False),
                patch.object(serve.os, "waitpid", side_effect=error),
            ):
                self.assertIsNone(serve._reap(55))


class PidFileTests(unittest.TestCase):
    def test_an_unreadable_or_non_object_pid_file_reads_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            pid_file = root / "brolls" / serve.PID_FILE
            self.assertIsNone(serve.read_pid(root))
            for text in ("não é json", "[1, 2]", '"texto"', "7", ""):
                with self.subTest(text=text):
                    pid_file.write_text(text, encoding="utf-8")
                    self.assertIsNone(serve.read_pid(root))
            pid_file.write_text('{"pid": 4}', encoding="utf-8")
            self.assertEqual({"pid": 4}, serve.read_pid(root))

    def test_state_of_a_stale_pid_file_reports_the_file_but_not_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            pid_file = root / "brolls" / serve.PID_FILE
            pid_file.write_text(json.dumps({"pid": 4, "port": 9, "urls": ["u"]}), encoding="utf-8")
            # `serve` resolves the project once: on macOS `/var/…` comes back as `/private/var/…`.
            shown = str(pid_file.resolve())
            with patch.object(serve, "_ours", return_value=False):
                result = serve.state(root)
            self.assertEqual({"running": False, "pid": None, "port": None, "urls": [], "pid_file": shown}, result)
            with patch.object(serve, "_ours", return_value=True):
                result = serve.state(root)
            self.assertEqual({"running": True, "pid": 4, "port": 9, "urls": ["u"], "pid_file": shown}, result)

    def test_a_project_reached_through_a_link_reads_the_same_pid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = _project(Path(tmp) / "real")
            link = Path(tmp) / "link"
            try:
                link.symlink_to(real, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("sem permissão para criar link simbólico")
            (real / "brolls" / serve.PID_FILE).write_text(json.dumps({"pid": 4, "port": 9}), encoding="utf-8")
            with patch.object(serve, "_ours", return_value=True):
                through_link, direct = serve.state(link), serve.state(real)
            self.assertEqual(direct, through_link)
            self.assertEqual({"pid": 4, "port": 9}, serve.read_pid(link))


class LogHelpersTests(unittest.TestCase):
    def test_payload_in_needs_a_json_object_with_a_port(self):
        self.assertFalse(serve._payload_in(""))
        self.assertFalse(serve._payload_in("Traceback (most recent call last):\n  boom\n"))
        self.assertFalse(serve._payload_in('[1, 2]\n{"x": 1}\n{"port": 0}\n7\n'))
        self.assertTrue(serve._payload_in('ruído\n{"x": 1}\n{"port": 8767, "session": "s"}\n'))

    def test_tail_keeps_only_the_last_lines_and_nothing_when_empty(self):
        self.assertEqual("", serve._tail(""))
        self.assertEqual("", serve._tail("  \n \n"))
        text = "\n".join(f"linha {i}" for i in range(30))
        tail = serve._tail(text)
        self.assertTrue(tail.startswith("\n\nÚltimas linhas do log:\n"))
        self.assertIn("linha 29", tail)
        self.assertIn("linha 10", tail)
        self.assertNotIn("linha 9\n", tail)
        self.assertEqual("\n\nÚltimas linhas do log:\nlinha 2", serve._tail("linha 1\nlinha 2", lines=1))

    def test_rotation_keeps_exactly_the_last_megabyte(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / serve.LOG_FILE
            head = b"A" * 700
            tail = bytes(range(256)) * (serve.LOG_KEEP_BYTES // 256)
            log.write_bytes(head + tail)
            serve._rotate_log(log)
            self.assertEqual(tail, (Path(tmp) / serve.LOG_ROTATE_FILE).read_bytes())

    def test_rotation_failure_never_raises_and_keeps_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / serve.LOG_FILE
            log.write_text("saída anterior\n", encoding="utf-8")
            with patch.object(serve.os, "open", side_effect=OSError(errno.ENOSPC, "cheio")):
                self.assertIsNone(serve._rotate_log(log))
            self.assertEqual("saída anterior\n", log.read_text(encoding="utf-8"))
            self.assertFalse((Path(tmp) / serve.LOG_ROTATE_FILE).exists())


class StartBackgroundFailureTests(unittest.TestCase):
    def setUp(self):
        self.seen = {}

    def _popen(self, brolls, log_text, poll):

        def fake_popen(_command, **kwargs):
            self.seen["kwargs"] = kwargs
            (brolls / serve.LOG_FILE).write_text(log_text, encoding="utf-8")
            process = MagicMock()
            process.pid = 4242
            process.poll.return_value = poll
            self.seen["process"] = process
            return process

        return patch.object(serve.subprocess, "Popen", side_effect=fake_popen)

    def test_without_a_storyboard_nothing_is_spawned(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(serve.subprocess, "Popen") as popen, self.assertRaisesRegex(ValueError, "não encontrado"):
                serve.start_background(Path(tmp))
            popen.assert_not_called()
            self.assertFalse((Path(tmp) / "brolls").exists())

    def test_a_child_that_died_reports_the_log_tail_and_writes_no_pid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            log = "Traceback (most recent call last):\nOSError: porta tomada\n"
            with self._popen(root / "brolls", log, poll=1), self.assertRaises(ValueError) as ctx:
                serve.start_background(root, port=0)
            message = str(ctx.exception)
            self.assertIn("não subiu", message)
            self.assertIn("OSError: porta tomada", message)
            self.assertIn(f"/brolls/{serve.LOG_FILE}", message)
            self.assertNotIn(str(root / "brolls"), message)
            self.assertFalse((root / "brolls" / serve.PID_FILE).exists())
            self.seen["process"].terminate.assert_not_called()

    def test_a_timeout_terminates_the_child_and_explains_with_the_log_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with (
                patch.object(serve, "BACKGROUND_TIMEOUT", 0.0),
                self._popen(root / "brolls", "iniciando...\n", poll=None),
                self.assertRaises(ValueError) as ctx,
            ):
                serve.start_background(root, port=0)
            self.seen["process"].terminate.assert_called_once_with()
            self.assertIn("não respondeu a tempo", str(ctx.exception))
            self.assertIn("iniciando...", str(ctx.exception))
            self.assertFalse((root / "brolls" / serve.PID_FILE).exists())

    def test_log_noise_before_the_payload_is_skipped_and_urls_default_from_the_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            log = 'aviso qualquer\n{"sem": "porta"}\n[1]\n{"port": 4321, "session": "sess"}\n'
            with self._popen(root / "brolls", log, poll=None):
                result = serve.start_background(root, port=0)
            self.assertEqual(4321, result["port"])
            self.assertEqual("sess", result["session"])
            self.assertEqual(serve._urls(4321), result["urls"])
            self.assertEqual(4242, result["pid"])
            self.assertFalse(result["already_running"])
            self.assertEqual(result["pid"], json.loads((root / "brolls" / serve.PID_FILE).read_text())["pid"])
            if os.name == "nt":  # o Windows destaca o filho com `creationflags` e não conhece `start_new_session`
                self.assertEqual(0x00000008 | 0x00000200, self.seen["kwargs"]["creationflags"])
                self.assertNotIn("start_new_session", self.seen["kwargs"])
            else:
                self.assertNotIn("creationflags", self.seen["kwargs"])
                self.assertIs(True, self.seen["kwargs"]["start_new_session"])
            self.assertEqual(str((root / "brolls").resolve()), self.seen["kwargs"]["cwd"])

    def test_a_child_that_already_exited_but_printed_its_payload_still_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            with self._popen(root / "brolls", '{"port": 99, "session": "s", "urls": ["u1"]}\n', poll=0):
                result = serve.start_background(root, port=0)
            self.assertEqual(["u1"], result["urls"])

    def test_the_port_argument_reaches_the_child_only_when_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            commands = []

            def fake_popen(command, **_kwargs):
                commands.append(command)
                (root / "brolls" / serve.LOG_FILE).write_text('{"port": 5, "session": "s"}\n', encoding="utf-8")
                process = MagicMock()
                process.pid = 1
                process.poll.return_value = None
                return process

            with patch.object(serve.subprocess, "Popen", side_effect=fake_popen):
                serve.start_background(root, port=0)
                serve.start_background(root, port=8123)
            self.assertNotIn("--port", commands[0])
            self.assertEqual(["--port", "8123"], commands[1][-2:])
            self.assertEqual("serve", commands[0][-3])


class ChildEnvironmentTests(unittest.TestCase):
    def test_secret_shaped_variables_are_dropped_and_utf8_is_forced(self):
        source = {"PATH": "/bin", "PEXELS_API_KEY": "k", "X_TOKEN": "t", "MY_SECRET": "s", "HOME": "/h"}
        environment = serve._child_environment(source, None)
        self.assertEqual(
            {"PATH": "/bin", "HOME": "/h", "PYTHONIOENCODING": "utf-8", serve.CHILD_ENV: serve.SERVE_CHILD_TOKEN},
            environment,
        )
        self.assertIn("PEXELS_API_KEY", source)  # a cópia não toca o original

    def test_a_checkout_prepends_its_scripts_and_an_installed_package_drops_pythonpath(self):
        scripts = PurePosixPath("/repo/scripts")  # o texto é o que importa: `Path` viraria `\\repo\\scripts` no Windows
        with_scripts = serve._child_environment({"PYTHONPATH": "/outro"}, scripts)
        self.assertEqual("/repo/scripts" + os.pathsep + "/outro", with_scripts["PYTHONPATH"])
        self.assertEqual("/repo/scripts", serve._child_environment({}, scripts)["PYTHONPATH"])
        self.assertNotIn("PYTHONPATH", serve._child_environment({"PYTHONPATH": "/outro"}, None))


class RunTests(unittest.TestCase):
    def _run(self, root, serve_side_effect):
        out = io.StringIO()
        closed = []
        real_start = serve.start

        def tracking_start(project, port=serve.DEFAULT_PORT):
            server, bound = real_start(project, port)
            real_close = server.server_close
            server.server_close = lambda: (closed.append(True), real_close())[1]
            return server, bound

        with (
            patch.object(serve, "start", side_effect=tracking_start),
            patch.object(serve.effects, "serve", side_effect=serve_side_effect) as serving,
            contextlib.redirect_stdout(out),
        ):
            code = serve.run(root, port=0)
        return code, json.loads(out.getvalue()), closed, serving

    def test_run_prints_the_urls_serves_until_interrupted_and_closes_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(Path(tmp).resolve())
            for side_effect in (None, KeyboardInterrupt()):
                with self.subTest(side_effect=side_effect):
                    code, payload, closed, serving = self._run(root, side_effect)
                    self.assertEqual(0, code)
                    self.assertEqual([True], closed)
                    port = payload["port"]
                    self.assertEqual(
                        [f"http://localhost:{port}/review.html", f"http://127.0.0.1:{port}/review.html"],
                        payload["urls"],
                    )
                    self.assertEqual(str(root / "brolls"), payload["directory"])
                    self.assertTrue(payload["session"])
                    self.assertEqual(f"127.0.0.1:{port}", serving.call_args.kwargs["address"])

    def test_run_without_a_storyboard_raises_before_printing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaisesRegex(ValueError, "não encontrado"):
                serve.run(Path(tmp), port=0)
            self.assertEqual("", out.getvalue())


if __name__ == "__main__":
    unittest.main()
