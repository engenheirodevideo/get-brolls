"""Download autenticado: `http.download(headers, allow_signed)` e `api.download` presa ao workdir."""

import tempfile
import unittest
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
            for bad in ("../fora.mp4", "sub/pasta.mp4", ".escondido", "a..b", "", "x" * 200):
                with self.subTest(bad=bad), self.assertRaises(ProviderError):
                    api.download("https://demo.example/files/1", bad)

    def test_headers_must_be_text(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as work, route_scope("demo", Path(work)), self.assertRaises(ProviderError):
            api.download("https://demo.example/files/1", "a.mp4", {"Authorization": 123})


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
