"""Exemplos com rota: `banco_http` (fetch autenticado) e `pasta_local` (prévia da pasta + comando)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT
from _plugin_pins import pin_plugins
from test_sdk_loader import LoaderTestCase

from getbrolls import cli, http
from getbrolls.runtime import OperationError

EXAMPLES = ROOT / "examples" / "plugins"
TOKEN = "segredo-do-teste"


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


class _BancoApi:
    """Dublê da API do banco: responde busca, licença e arquivo, e anota cada pedido."""

    def __init__(self, media):
        self.media = media
        self.requests = []

    def open(self, request, timeout=None):
        url = request.full_url
        self.requests.append((url, request.get_header("Authorization")))
        if url.startswith("https://api.banco.example/v1/search"):
            payload = {
                "items": [
                    {"id": "1", "title": "Praia ao entardecer", "page_url": "https://banco.example/v/1", "duration": 6}
                ]
            }
            return _Response(json.dumps(payload).encode())
        if url == "https://api.banco.example/v1/videos/1/license":
            return _Response(json.dumps({"text": "Licença padrão Banco Exemplo, pedido L-1"}).encode())
        if url == "https://api.banco.example/v1/videos/1/file":
            return _Response(self.media)
        raise AssertionError(f"pedido inesperado: {url}")

    def paths(self):
        return [url.split("/v1/", 1)[1].split("?", 1)[0] for url, _ in self.requests]


@skip_unless_ffmpeg
class BancoHttpExampleTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        work = Path(tempfile.mkdtemp(prefix="gb-banco-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        synth_video(work / "original.mp4", duration=6)
        self.api = _BancoApi((work / "original.mp4").read_bytes())
        self.project = work / "projeto"
        self.project.mkdir()
        shutil.copytree(EXAMPLES / "banco_http", self.home / "plugins" / "banco_http")
        pin_plugins("banco_http")
        env = patch.dict(os.environ, {"GB_PLUGINS": "banco_http", "BANCO_HTTP_TOKEN": TOKEN})
        env.start()
        self.addCleanup(env.stop)

    def gb(self, *args):
        with patch.object(http, "_opener", return_value=self.api):
            return cli.main([args[0], "--project", str(self.project), *args[1:]])

    def test_search_reference_approve_permit_fetch(self):
        found = self.gb("search", "--provider", "banco_http", "--query", "praia", "--limit", "1")
        item = found["items"][0]
        self.assertEqual("banco_http:1", item["id"])
        self.assertEqual({"status": "available", "method": "plugin:banco_http", "evidence": []}, item["acquisition"])

        with self.assertRaises(OperationError) as caught:
            self.gb("preview", "--candidate", item["id"], "--start", "0", "--end", "2")
        self.assertIn("--reference-only", str(caught.exception))
        self.gb("preview", "--candidate", item["id"], "--start", "0", "--end", "2", "--reference-only")
        self.gb("approve", "--candidate", item["id"], "--by", "Bruno", "--statement", "pode usar esse trecho")
        self.gb("permit", "--candidate", item["id"], "--evidence", "Plano anual do Banco Exemplo, conferido na conta")
        self.assertEqual(["search"], self.api.paths())

        done = self.gb("fetch", "--candidate", item["id"])
        self.assertTrue(done["output"]["verified"])
        self.assertTrue((self.project / "brolls" / done["output"]["path"]).is_file())
        self.assertIn(
            "Licença registrada pelo plugin banco_http: Licença padrão Banco Exemplo, pedido L-1",
            done["rights"]["evidence"],
        )
        self.assertEqual(["search", "videos/1/license", "videos/1/file"], self.api.paths())
        self.assertTrue(all(header == f"Bearer {TOKEN}" for _, header in self.api.requests))
        log = (self.project / "brolls" / "getbrolls.log").read_text(encoding="utf-8")
        self.assertIn("event=plugin_route", log)
        self.assertNotIn(TOKEN, log)
        self.assertNotIn(TOKEN, (self.project / "brolls" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([], sorted((self.project / ".getbrolls-sources").glob("plugin-*")))


class PastaLocalExampleTests(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.media = Path(tempfile.mkdtemp(prefix="gb-pasta-"))
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.project = Path(tempfile.mkdtemp(prefix="gb-projeto-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def install_example(self, roots):
        target = self.home / "plugins" / "pasta_local"
        shutil.copytree(EXAMPLES / "pasta_local", target)
        manifest_path = target / "getbrolls-plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["permissions"]["paths"] = [str(root) for root in roots]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        pin_plugins("pasta_local")
        env = patch.dict(os.environ, {"GB_PLUGINS": "pasta_local", "PASTA_LOCAL_DIR": str(self.media)})
        env.start()
        self.addCleanup(env.stop)

    def gb(self, *args):
        return cli.main([args[0], "--project", str(self.project), *args[1:]])

    @skip_unless_ffmpeg
    def test_preview_uses_the_folder_file_without_resolve_file(self):
        synth_video(self.media / "por do sol na praia.mp4", duration=6)
        self.install_example([self.media])
        item = self.gb("search", "--provider", "pasta_local", "--query", "praia", "--limit", "1")["items"][0]
        self.assertEqual("plugin:pasta_local", item["acquisition"]["method"])
        shown = self.gb("preview", "--candidate", item["id"], "--start", "0", "--end", "2")
        self.assertEqual("awaiting_approval", shown["state"])
        self.assertTrue(Path(shown["local_path"]).is_file())
        self.assertTrue(shown["local_path"].startswith(str(self.project.resolve() / ".getbrolls-sources")))
        self.assertTrue((self.media / "por do sol na praia.mp4").is_file())

    def test_file_outside_permissions_paths_is_refused(self):
        (self.media / "praia.mp4").write_bytes(b"\x00")
        elsewhere = Path(tempfile.mkdtemp(prefix="gb-outra-raiz-"))
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        self.install_example([elsewhere])
        item = self.gb("search", "--provider", "pasta_local", "--query", "praia", "--limit", "1")["items"][0]
        with self.assertRaises(OperationError) as caught:
            self.gb("preview", "--candidate", item["id"], "--start", "0", "--end", "2")
        self.assertIn("fora de permissions.paths", str(caught.exception))

    def test_recentes_command_returns_json(self):
        for name in ("a.mp4", "b.mov", "nota.txt"):
            (self.media / name).write_bytes(b"\x00")
        self.install_example([self.media])
        pin_plugins("pasta_local")
        env = {"GB_HOME": str(self.home), "GB_PLUGINS": "pasta_local", "PASTA_LOCAL_DIR": str(self.media)}
        listed = run_cli("x", "--list", env=env)
        self.assertIn("x pasta_local recentes", [row["run"] for row in listed["commands"]])
        out = run_cli("x", "pasta_local", "recentes", "--arg", "limite=5", project=self.project, env=env)
        self.assertEqual({"a.mp4", "b.mov"}, set(out["result"]["arquivos"]))
        self.assertEqual(0, out["result"]["candidatos_no_projeto"])


class ExampleHygieneTests(unittest.TestCase):
    def test_examples_pass_check_and_ship_no_local_paths(self):
        for name in ("banco_http", "pasta_local"):
            with self.subTest(name=name):
                out = run_cli("plugins", "--action", "check", "--path", EXAMPLES / name)
                self.assertTrue(out["ok"])
                self.assertTrue(out["contracts"]["routes"])
                for path in (EXAMPLES / name).rglob("*"):
                    if path.is_file():
                        text = path.read_text(encoding="utf-8")
                        self.assertNotIn("/Users/", text)
                        self.assertNotIn("/private/tmp", text)


if __name__ == "__main__":
    unittest.main()
