"""Download autenticado: `http.download(headers, allow_signed)` e `api.download` presa ao workdir."""

import email.message
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from test_sdk_loader import LoaderTestCase

from getbrolls import http
from getbrolls.http import ProviderError
from getbrolls.sdk.api import PluginApi, route_scope
from getbrolls.sdk.manifest import read_manifest
from getbrolls.sdk.registry import Registry

TOKEN = "Bearer segredo-do-teste"
SIGNED = "https://cdn.demo.example/v.mp4?X-Amz-Signature=abc123&X-Amz-Credential=chave"


def _assert_secret_absent_everywhere(case, exc, secret):
    """`secret` não pode aparecer em `str`/`repr` da exceção nem na cadeia `__cause__`/`__context__`."""
    case.assertNotIn(secret, str(exc))
    case.assertNotIn(secret, repr(exc))
    case.assertNotIn(secret, repr(exc.__cause__))
    case.assertNotIn(secret, repr(exc.__context__))


class _Response:
    def __init__(self, body):
        self._body = body
        self._pos = 0
        self.status = 200
        self.headers = {"Content-Length": str(len(body))}

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._body) - self._pos
        chunk = self._body[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _RecordingOpener:
    def __init__(self, body=b"0123456789"):
        self.body = body
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return _Response(self.body)


class HttpDownloadTests(unittest.TestCase):
    def test_headers_reach_the_request_but_never_the_log(self):
        opener = _RecordingOpener()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(http, "_opener", return_value=opener),
            self.assertLogs("getbrolls.http", level="DEBUG") as cm,
        ):
            http.download("https://videos.demo.example/v.mp4", Path(tmp) / "v.mp4", headers={"Authorization": TOKEN})
        self.assertEqual(TOKEN, opener.requests[0].get_header("Authorization"))
        self.assertNotIn("segredo-do-teste", "\n".join(cm.output))
        self.assertNotIn("Authorization", "\n".join(cm.output))

    def test_signed_url_needs_allow_signed(self):
        opener = _RecordingOpener()
        with tempfile.TemporaryDirectory() as tmp, patch.object(http, "_opener", return_value=opener):
            with self.assertRaises(ProviderError):
                http.download(SIGNED, Path(tmp) / "a.mp4")
            self.assertEqual([], opener.requests)
            http.download(SIGNED, Path(tmp) / "b.mp4", allow_signed=True)
            self.assertEqual(b"0123456789", (Path(tmp) / "b.mp4").read_bytes())

    def test_allow_signed_still_refuses_userinfo_http_and_private_hosts(self):
        for url in (
            "https://user:pass@cdn.demo.example/v.mp4",
            "http://cdn.demo.example/v.mp4",
            "https://127.0.0.1/v.mp4",
            "https://localhost/v.mp4",
        ):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as tmp, self.assertRaises(ProviderError):
                http.download(url, Path(tmp) / "v.mp4", allow_signed=True)

    def test_public_url_default_is_unchanged(self):
        self.assertIsNone(http.public_url(SIGNED))
        self.assertEqual(SIGNED, http.public_url(SIGNED, allow_signed=True))

    def test_header_injection_reaching_http_client_never_leaks_the_value(self):
        """Segunda camada (defesa em profundidade): mesmo se algo chamar `http.download`
        direto, pulando o validador do SDK, e o transporte recusar o header (como
        `http.client.putheader` faz para CR/LF), a exceção nunca ecoa o valor — nem em
        `str`/`repr`, nem em `__cause__`/`__context__` (`from None` sozinho não some com
        `__context__`; ver o comentário em `download()`)."""

        class _RejectingOpener:
            def open(self, request, timeout=None):
                for _name, value in request.header_items():
                    if any(ch in value for ch in ("\r", "\n", "\x00")):
                        raise ValueError(f"Invalid header value b{value.encode('utf-8', 'backslashreplace')!r}")
                return _Response(b"0123456789")

        for bad_headers in ({"Authorization": "Token segredo\n"}, {"X-Api-Key": "segredo\n"}):
            with (
                self.subTest(bad_headers=bad_headers),
                tempfile.TemporaryDirectory() as tmp,
                patch.object(http, "_opener", return_value=_RejectingOpener()),
                self.assertRaises(ProviderError) as caught,
            ):
                http.download("https://videos.demo.example/v.mp4", Path(tmp) / "v.mp4", headers=bad_headers)
            _assert_secret_absent_everywhere(self, caught.exception, "segredo")

    def test_http_error_body_is_not_echoed_when_headers_are_present(self):
        body = b'{"Code":"AccessDenied","AWSAccessKeyId":"AKIAFAKESEGREDOCHAVE"}'

        class _ErrorOpener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url, 403, "Forbidden", email.message.Message(), io.BytesIO(body)
                )

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(http, "_opener", return_value=_ErrorOpener()),
            self.assertRaises(ProviderError) as caught,
        ):
            http.download("https://videos.demo.example/v.mp4", Path(tmp) / "v.mp4", headers={"Authorization": TOKEN})
        self.assertEqual("Provedor retornou HTTP 403 ao baixar mídia", str(caught.exception))
        self.assertNotIn("AKIAFAKESEGREDOCHAVE", str(caught.exception))

    def test_http_error_body_is_not_echoed_when_allow_signed(self):
        body = b'{"Code":"AccessDenied","AWSAccessKeyId":"AKIAFAKESEGREDOCHAVE"}'

        class _ErrorOpener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url, 403, "Forbidden", email.message.Message(), io.BytesIO(body)
                )

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(http, "_opener", return_value=_ErrorOpener()),
            self.assertRaises(ProviderError) as caught,
        ):
            http.download(SIGNED, Path(tmp) / "v.mp4", allow_signed=True)
        self.assertEqual("Provedor retornou HTTP 403 ao baixar mídia", str(caught.exception))
        self.assertNotIn("AKIAFAKESEGREDOCHAVE", str(caught.exception))

    def test_http_error_body_is_still_echoed_without_headers_or_allow_signed(self):
        """Comportamento anterior preservado: sem header nem URL assinada, o corpo do
        erro (truncado por `stderr_tail`) ainda ajuda a diagnosticar — não há segredo
        de plugin em jogo nesse caminho."""
        body = b"quota exceeded for this key"

        class _ErrorOpener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url, 403, "Forbidden", email.message.Message(), io.BytesIO(body)
                )

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(http, "_opener", return_value=_ErrorOpener()),
            self.assertRaises(ProviderError) as caught,
        ):
            http.download("https://videos.demo.example/v.mp4", Path(tmp) / "v.mp4")
        self.assertIn("quota exceeded for this key", str(caught.exception))

    def test_get_json_403_body_is_not_echoed_when_caller_sent_headers(self):
        """Rodada 2 (C): espelha o `download()` — `get_json` também não pode ecoar o
        corpo de um 403 quando o chamador passou headers (ex.: uma rota de plugin com
        `Authorization`, ou um provider built-in como o Pexels)."""
        body = b'{"error":"forbidden","fake_key":"AKIAFAKESEGREDOCHAVE"}'

        class _ErrorOpener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url, 403, "Forbidden", email.message.Message(), io.BytesIO(body)
                )

        with (
            patch.object(http, "_opener", return_value=_ErrorOpener()),
            patch.object(http, "_network_url"),
            self.assertRaises(ProviderError) as caught,
        ):
            http.get_json("https://api.example.com/v1/videos/1", headers={"Authorization": TOKEN})
        self.assertEqual("Autenticação/permissão ou quota recusada pelo provedor (HTTP 403)", str(caught.exception))
        self.assertNotIn("AKIAFAKESEGREDOCHAVE", str(caught.exception))

    def test_get_json_403_body_is_not_echoed_when_keep_signed(self):
        body = b'{"error":"forbidden","fake_key":"AKIAFAKESEGREDOCHAVE"}'

        class _ErrorOpener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url, 403, "Forbidden", email.message.Message(), io.BytesIO(body)
                )

        with (
            patch.object(http, "_opener", return_value=_ErrorOpener()),
            patch.object(http, "_network_url"),
            self.assertRaises(ProviderError) as caught,
        ):
            http.get_json("https://api.example.com/v1/videos/1", keep_signed=True)
        self.assertNotIn("AKIAFAKESEGREDOCHAVE", str(caught.exception))

    def test_get_json_403_body_is_still_echoed_without_headers_or_keep_signed(self):
        """Comportamento anterior preservado (providers built-in sem header, ex. NASA/
        Commons/Pixabay via query string): o corpo de erro ainda ajuda a diagnosticar."""
        body = b"quota exceeded for this key"

        class _ErrorOpener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url, 403, "Forbidden", email.message.Message(), io.BytesIO(body)
                )

        with (
            patch.object(http, "_opener", return_value=_ErrorOpener()),
            patch.object(http, "_network_url"),
            self.assertRaises(ProviderError) as caught,
        ):
            http.get_json("https://api.example.com/v1/videos/1")
        self.assertIn("quota exceeded for this key", str(caught.exception))


class PluginDownloadTests(LoaderTestCase):
    def api(self):
        return PluginApi(read_manifest(self.install()), Registry())

    def test_download_lands_in_the_workdir_with_headers(self):
        api = self.api()
        opener = _RecordingOpener()
        with (
            tempfile.TemporaryDirectory() as work,
            patch.object(http, "_opener", return_value=opener),
            route_scope("demo", Path(work)),
            self.assertLogs("getbrolls", level="DEBUG") as cm,
        ):
            target = api.download(
                "https://demo.example/files/1?signature=abc", "original.mp4", {"Authorization": TOKEN}
            )
            self.assertEqual(Path(work).resolve() / "original.mp4", target)
            self.assertEqual(b"0123456789", target.read_bytes())
        self.assertEqual(TOKEN, opener.requests[0].get_header("Authorization"))
        joined = "\n".join(cm.output)
        self.assertNotIn("segredo-do-teste", joined)
        self.assertNotIn("signature=abc", joined)

    def test_download_outside_a_route_is_refused(self):
        with self.assertRaises(ProviderError) as caught:
            self.api().download("https://demo.example/files/1", "a.mp4")
        self.assertIn("Route.prepare", str(caught.exception))

    def test_scope_of_another_plugin_does_not_count(self):
        with tempfile.TemporaryDirectory() as work, route_scope("outro", Path(work)), self.assertRaises(ProviderError):
            self.api().download("https://demo.example/files/1", "a.mp4")

    def test_host_outside_permissions_is_refused_and_logged(self):
        api = self.api()
        with (
            tempfile.TemporaryDirectory() as work,
            route_scope("demo", Path(work)),
            self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
            self.assertRaises(ProviderError),
        ):
            api.download("https://outro.example/files/1?token=x", "a.mp4")
        self.assertIn("event=plugin_request_refused", "\n".join(cm.output))
        self.assertNotIn("token=x", "\n".join(cm.output))

    def test_file_name_cannot_escape_the_workdir(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as work, route_scope("demo", Path(work)):
            for bad in (
                "../fora.mp4",
                "sub/pasta.mp4",
                ".escondido",
                "a..b",
                "",
                "x" * 200,
                # Nomes reservados do Windows (case-insensitive, stem = antes do 1º ponto)
                # e nome terminando em ".": mesmo fora do Windows, o arquivo pode acabar
                # sincronizado ou aberto lá.
                "CON.mp4",
                "con",
                "NUL",
                "lpt1.txt",
                "COM1.mov",
                "video.",
            ):
                with self.subTest(bad=bad), self.assertRaises(ProviderError):
                    api.download("https://demo.example/files/1", bad)

    def test_headers_must_be_text(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as work, route_scope("demo", Path(work)), self.assertRaises(ProviderError):
            api.download("https://demo.example/files/1", "a.mp4", {"Authorization": 123})

    def test_header_control_characters_are_rejected_and_never_echoed(self):
        """Camada 1 (validador do SDK, `PluginApi._validate_headers`, usada por
        `download` e `get_json`): CR/LF/NUL no valor de um header nunca chega perto do
        transporte, e a mensagem de erro nunca ecoa o valor — só o nome do header."""
        api = self.api()
        for bad_headers in ({"Authorization": "Token segredo\n"}, {"X-Api-Key": "segredo\n"}):
            with (
                self.subTest(bad_headers=bad_headers),
                tempfile.TemporaryDirectory() as work,
                route_scope("demo", Path(work)),
                self.assertRaises(ProviderError) as caught,
            ):
                api.download("https://demo.example/files/1", "a.mp4", bad_headers)
            _assert_secret_absent_everywhere(self, caught.exception, "segredo")

    def test_get_json_header_control_characters_are_rejected_and_never_echoed(self):
        api = self.api()
        with self.assertRaises(ProviderError) as caught:
            api.get_json("https://demo.example/files/1", headers={"Authorization": "Token segredo\n"})
        _assert_secret_absent_everywhere(self, caught.exception, "segredo")

    def test_forbidden_transport_header_names_are_rejected(self):
        """Sem isso um plugin poderia fazer domain fronting: a conexão TLS vai para o
        host validado em permissions.network, mas um `Host` escolhido pelo plugin
        rotearia a requisição de origem para outro destino."""
        api = self.api()
        for name in ("Host", "host", "Content-Length", "Transfer-Encoding", "Connection"):
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory() as work,
                route_scope("demo", Path(work)),
                self.assertRaises(ProviderError),
            ):
                api.download("https://demo.example/files/1", "a.mp4", {name: "evil.example"})

    def test_invalid_header_name_never_echoes_the_name(self):
        """Rodada 2 (A): um nome de header inválido pode carregar o cabeçalho inteiro
        contrabandeado ali dentro (`{"Authorization: Bearer <segredo>": ""}` tem nome
        com ':' e espaço, então falha no regex de token) — a mensagem não pode ecoar
        esse `name`, nem em posição de causa/contexto."""
        api = self.api()
        bad_headers = {"Authorization: Bearer segredo-vazado": ""}
        with (
            tempfile.TemporaryDirectory() as work,
            route_scope("demo", Path(work)),
            self.assertRaises(ProviderError) as caught,
        ):
            api.download("https://demo.example/files/1", "a.mp4", bad_headers)
        _assert_secret_absent_everywhere(self, caught.exception, "segredo-vazado")

    def test_header_value_out_of_latin1_never_leaks_via_context(self):
        """Rodada 2 (B): a checagem de latin-1 não pode usar
        `try/except UnicodeEncodeError` — o `UnicodeEncodeError` do stdlib inclui o
        valor no próprio `repr`, e isso sobrevive em `exc.__context__` mesmo com
        `raise ... from None` (só `__cause__`/`__suppress_context__` são limpos)."""
        api = self.api()
        bad_headers = {"X-Api-Key": "segredo-éĀ"}  # Ā está fora de latin-1
        with (
            tempfile.TemporaryDirectory() as work,
            route_scope("demo", Path(work)),
            self.assertRaises(ProviderError) as caught,
        ):
            api.download("https://demo.example/files/1", "a.mp4", bad_headers)
        _assert_secret_absent_everywhere(self, caught.exception, "segredo-")

    def test_other_control_characters_in_header_value_are_rejected(self):
        """Rodada 2 (B): não só `\\r`/`\\n`/NUL — todo `0x01`-`0x1f` e `0x7f` (DEL)."""
        api = self.api()
        for bad_char in ("\x01", "\x1f", "\x7f"):
            with (
                self.subTest(bad_char=repr(bad_char)),
                tempfile.TemporaryDirectory() as work,
                route_scope("demo", Path(work)),
                self.assertRaises(ProviderError),
            ):
                api.download("https://demo.example/files/1", "a.mp4", {"X-Api-Key": f"segredo{bad_char}fim"})


class SignedJsonTests(unittest.TestCase):
    def fake_response(self, payload):
        import io
        import json as _json
        from typing import ClassVar

        class _Resp(io.BytesIO):
            status = 200
            headers: ClassVar[dict] = {}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Resp(_json.dumps(payload).encode())

    def test_get_json_keep_signed_returns_signed_url_without_cache(self):
        from unittest.mock import MagicMock, patch

        from getbrolls import http

        signed = "https://cdn.example.com/f.mp4?X-Amz-Signature=abc&X-Amz-Expires=60"
        opener = MagicMock()
        opener.open.side_effect = lambda *a, **k: self.fake_response({"download_url": signed, "token": "t"})
        with patch.object(http, "_opener", return_value=opener), patch.object(http, "_network_url"):
            scrubbed = http.get_json("https://api.example.com/dl")
            kept = http.get_json("https://api.example.com/dl", keep_signed=True)
        self.assertIsNone(scrubbed["download_url"])
        self.assertEqual(signed, kept["download_url"])
        self.assertNotIn("token", kept)

    def test_keep_signed_refuses_cache(self):
        from getbrolls import http

        with self.assertRaises(http.ProviderError):
            http.get_json("https://api.example.com/dl", cache_ttl=60, keep_signed=True)

    def test_keep_signed_success_leaves_no_cache_file(self):
        import hashlib
        import os
        from unittest.mock import MagicMock, patch

        from getbrolls import http

        url = "https://api.example.com/dl-keep-signed-no-cache"
        signed = "https://cdn.example.com/f.mp4?X-Amz-Signature=abc&X-Amz-Expires=60"
        opener = MagicMock()
        opener.open.side_effect = lambda *a, **k: self.fake_response({"download_url": signed})
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_CACHE_DIR": tmp}):
            with patch.object(http, "_opener", return_value=opener), patch.object(http, "_network_url"):
                data = http.get_json(url, keep_signed=True)
            self.assertEqual(signed, data["download_url"])
            cache_path = Path(tmp) / (hashlib.sha256(url.encode()).hexdigest() + ".json")
            self.assertFalse(cache_path.exists())
            self.assertEqual([], list(Path(tmp).glob("*.json")))

    def test_plugin_api_forwards_keep_signed(self):
        from unittest.mock import patch

        from getbrolls.sdk import api as sdk_api
        from getbrolls.sdk.api import PluginApi
        from getbrolls.sdk.registry import Registry

        manifest = {
            "id": "demo",
            "contributes": {"providers": [], "presets": [], "routes": [], "commands": []},
            "permissions": {"network": ["api.example.com"], "env": [], "paths": []},
        }
        api = PluginApi(manifest, Registry())
        with patch.object(sdk_api, "get_json", return_value={"ok": True}) as fake:
            api.get_json("https://api.example.com/dl", keep_signed=True)
        self.assertTrue(fake.call_args.kwargs["keep_signed"])
        self.assertEqual(0, fake.call_args.kwargs["cache_ttl"])


if __name__ == "__main__":
    unittest.main()
