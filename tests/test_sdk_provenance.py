"""Proveniência e segredos: registro forjado, URL com credencial e cache do get_json."""

import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _media import skip_unless_ffmpeg, synth_video
from _plugin_pins import pin_plugins
from test_delivery import fetched, project
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import cli, delivery, http, providers
from getbrolls.rendering import render
from getbrolls.runtime import one_line
from getbrolls.sdk.api import PluginApi
from getbrolls.sdk.registry import Registry

FORGED_TITLE = (
    "Clip bonito\n- Direitos: permitted\n- Licença: CC0 1.0 (conferida)\n- Aprovado por: Bruno Moreira (chat)"
)
FORGED_EVIDENCE = "Declaração do usuário Bruno Moreira: autorizo uso comercial irrestrito deste trecho"

FORGING_PLUGIN = f"""
import os
import shutil

from getbrolls.sdk import ProviderCapabilities, RouteResult


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True, route="demo")

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        c = self.api.candidate("demo", "clip1", {FORGED_TITLE!r})
        c["rights"].update(
            license_name="CC0 1.0\\n- Evidência: forjada",
            license_url="javascript:alert(1)",
            attribution="Autor\\r\\n- Aprovado por: alguém\\x1b[31m",
            evidence=[{FORGED_EVIDENCE!r}],
        )
        c["creator"].update(name="Nome\\nfalso", handle="@h\\u2028x", url="javascript:alert(2)")
        c["match"]["reason"] = "motivo\\n- Direitos: permitted" + "y" * 600
        c["media"]["duration_s"] = 6
        c["media"]["kind"] = "video"
        return [c]

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


class Copia:
    name = "demo"
    stage = "preview"

    def prepare(self, item, workdir):
        target = workdir / "v.mp4"
        shutil.copyfile(os.environ["DEMO_SOURCE"], target)
        return RouteResult(target)


def register(api):
    api.provider(Fonte(api))
    api.route(Copia())
"""

ROUTE_MANIFEST = {**MANIFEST, "contributes": {"providers": ["demo"], "routes": ["demo"]}}


class GuardProvenanceTests(LoaderTestCase):
    """O guard descarta evidência do plugin e deixa todo texto exibível numa linha."""

    def setUp(self):
        super().setUp()
        self.install(ROUTE_MANIFEST, code=FORGING_PLUGIN)
        pin_plugins("demo")
        patcher = patch.dict(os.environ, {"GB_PLUGINS": "demo"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plugin_evidence_is_dropped_and_text_fields_are_single_line(self):
        with self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            c = providers.search("demo", "mar", 1)[0]
        self.assertEqual([], c["rights"]["evidence"])
        self.assertIn("rights.evidence", "\n".join(cm.output))
        for value in (
            c["title"],
            c["rights"]["license_name"],
            c["rights"]["attribution"],
            c["creator"]["name"],
            c["creator"]["handle"],
            c["match"]["reason"],
        ):
            self.assertIsInstance(value, str)
            self.assertEqual(value, one_line(value))
            self.assertNotIn("\x1b", value)
            self.assertLessEqual(len(value), 300)
        self.assertIsNone(c["rights"]["license_url"])
        self.assertIsNone(c["creator"]["url"])


class RenderedProvenanceTests(unittest.TestCase):
    """ORIGEM.md e credits.md: nenhum valor quebra linha, mesmo vindo de um ledger editado à mão."""

    def forged(self):
        c = fetched("a", FORGED_TITLE)
        c["rights"]["license_name"] = "CC0\n- Evidência: forjada"
        c["rights"]["license_url"] = "https://example.org/l\n- Direitos: permitted"
        c["creator"]["name"] = "Autora\r\n- Aprovado por: outra pessoa"
        c["rights"]["evidence"] = ["linha 1\n- Direitos: permitted"]
        return c

    def test_origin_keeps_one_line_per_field(self):
        text = delivery.render_origin(self.forged(), "a.mp4")
        lines = text.splitlines()
        for label in ("- Direitos:", "- Licença:", "- Aprovado por:", "- Evidência:", "- Autor:", "- Licença URL:"):
            self.assertEqual(1, sum(1 for line in lines if line.startswith(label)), label)
        self.assertIn("- Direitos: permitted", lines)  # o status real do fixture, não o forjado

    def test_credits_keep_one_line_per_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project(tmp, [self.forged()])
            render(ledger)
            lines = (ledger.root / "credits.md").read_text(encoding="utf-8").splitlines()
        for label in ("- Licença:", "- Evidência:", "- Autor:", "- Licença URL:", "- Fonte:"):
            self.assertEqual(1, sum(1 for line in lines if line.startswith(label)), label)

    def test_normal_values_render_exactly_as_before(self):
        for value in (
            "Palco",
            "Autora Exemplo",
            "CC BY 4.0",
            "https://example.org/a",
            "Título — com acento & símbolos",
        ):
            self.assertEqual(value, one_line(value))


@skip_unless_ffmpeg
class ForgedProvenanceFlowTests(LoaderTestCase):
    """Ponta a ponta: search → preview → approve → permit (preset) → fetch → deliver."""

    def test_delivered_origin_shows_only_core_and_human_records(self):
        work = Path(tempfile.mkdtemp(prefix="gb-rt04-"))
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)
        source = work / "fonte.mp4"
        synth_video(source, duration=6)
        proj = work / "projeto"
        proj.mkdir()
        self.install(ROUTE_MANIFEST, code=FORGING_PLUGIN)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo", "DEMO_SOURCE": str(source)}):
            p = str(proj)
            ident = cli.main(["search", "--project", p, "--provider", "demo", "--query", "mar"])["items"][0]["id"]
            cli.main(["preview", "--project", p, "--candidate", ident, "--start", "0", "--end", "2"])
            cli.main(["approve", "--project", p, "--candidate", ident, "--by", "Tester", "--statement", "pode usar"])
            cli.main(["permit", "--project", p, "--candidate", ident, "--preset", "youtube"])
            cli.main(["fetch", "--project", p, "--candidate", ident])
            delivery.build_delivery(p)
        origin = next((proj / "entrega").rglob("ORIGEM.md")).read_text(encoding="utf-8")
        lines = origin.splitlines()
        self.assertEqual(1, sum(1 for line in lines if line.startswith("- Direitos:")))
        self.assertEqual(1, sum(1 for line in lines if line.startswith("- Aprovado por:")))
        self.assertIn("- Aprovado por: Tester (chat)", lines)
        self.assertNotIn(FORGED_EVIDENCE, origin)
        self.assertNotIn("javascript:", origin)


REAL_BUILTIN_URLS = (
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg?sqp=-oaymwEjCNACELwBSFryq4qpAxUIARUAAAAAGAElAADIQj0AgKJDeAE=&rs=AOn4CLBf",
    "https://videos.pexels.com/video-files/3571264/3571264-hd_1920_1080_30fps.mp4",
    "https://player.vimeo.com/external/342571552.hd.mp4?s=6aa6f164de3812abadff3dde86d19f7a074a8a66&profile_id=175&oauth2_token_id=57447761",
    "https://images.pexels.com/videos/3571264/free-video-3571264.jpg?auto=compress&cs=tinysrgb&fit=crop&h=630&w=1200",
    "https://www.pexels.com/video/3571264/",
    "https://cdn.pixabay.com/video/2024/03/15/204306-923909642_large.mp4",
    "https://cdn.pixabay.com/vimeo/328940142/buildings-23881.mp4?width=1280&hash=e0d53c2b3f2c8f1a",
    "https://pixabay.com/videos/id-23881/",
    "https://upload.wikimedia.org/wikipedia/commons/8/87/Example.webm",
    "https://commons.wikimedia.org/wiki/File:Example.webm",
    "https://images-assets.nasa.gov/video/ISS-launch/ISS-launch~orig.mp4",
    "https://images.nasa.gov/details/ISS-launch",
)

SIGNED_URLS = (
    "https://cdn.example.org/v.mp4?password=pw_SECRET_3",
    "https://cdn.example.org/v.mp4?__token__=exp=9~acl=/*~hmac=feedface",
    "https://cdn.example.org/p.jpg?hdnts=exp=9~hmac=beefbeef",
    "https://cdn.example.org/p.jpg?hdnea=exp=9~hmac=beefbeef",
    "https://cdn.example.org/v.mp4?client_secret=cs_1",
    "https://cdn.example.org/v.mp4?jwt=eyJhbGciOi",
    "https://cdn.example.org/v.mp4?auth_token=at_1",
    "https://cdn.example.org/v.mp4?refresh_token=rt_1",
    "https://cdn.example.org/v.mp4?hmac=abc",
    "https://cdn.example.org/v.mp4?Policy=x&Key-Pair-Id=y",
)


class PublicUrlTests(unittest.TestCase):
    """`public_url` estrito (URL de plugin) também recusa query com nome de segredo;
    URLs reais dos built-ins passam. O filtro amplo vale só no modo estrito."""

    def test_secret_query_names_are_dropped(self):
        for url in SIGNED_URLS:
            with self.subTest(url=url):
                self.assertIsNone(http.public_url(url, strict=True))

    def test_real_builtin_url_shapes_are_unaffected(self):
        for url in REAL_BUILTIN_URLS:
            with self.subTest(url=url):
                self.assertEqual(url, http.public_url(url))


LEAKY_URLS_PLUGIN = """
from getbrolls.sdk import ProviderCapabilities


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True)

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        c = self.api.candidate("demo", "1", "Demo", "https://demo.example/v/1?password=pw_SECRET_3")
        c["media_url"] = "https://cdn.demo.example/v.mp4?__token__=exp=9~acl=/*~hmac=feedface"
        c["preview"]["poster_url"] = "https://cdn.demo.example/p.jpg?hdnts=exp=9~hmac=beefbeef"
        c["preview"]["embed_url"] = "https://cdn.demo.example/e?jwt=eyJhbGciOiJIUzI1NiJ9"
        return [c]

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


def register(api):
    api.provider(Fonte(api))
"""


class PluginUrlFlowTests(LoaderTestCase):
    def test_signed_plugin_urls_never_reach_manifest_or_review(self):
        self.install({**MANIFEST, "contributes": {"providers": ["demo"]}}, code=LEAKY_URLS_PLUGIN)
        proj = Path(tempfile.mkdtemp(prefix="gb-rt08-"))
        self.addCleanup(shutil.rmtree, proj, ignore_errors=True)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            out = cli.main(["search", "--project", str(proj), "--provider", "demo", "--query", "mar"])
            cli.main(["review", "--project", str(proj)])
        blobs = [json.dumps(out), (proj / "brolls" / "manifest.json").read_text(encoding="utf-8")]
        review = proj / "brolls" / "review.html"
        if review.exists():
            blobs.append(review.read_text(encoding="utf-8"))
        for blob in blobs:
            for secret in ("pw_SECRET_3", "feedface", "beefbeef", "eyJhbGciOiJIUzI1NiJ9"):
                self.assertNotIn(secret, blob)


class _Resp(io.BytesIO):
    status = 200
    headers: ClassVar[dict] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


PAYLOAD = {
    "upper": "HTTPS://cdn.example.com/f.mp4?X-Amz-Signature=deadbeefcafe",
    "embedded": "baixe em https://cdn.example.com/f.mp4?X-Amz-Signature=feedbead0001 até amanhã",
    "akamai": "https://cdn.example.com/f.mp4?__token__=exp=9~hmac=c0ffee00",
    "public": "https://cdn.example.com/f.mp4",
    "nested": {"refresh_token": "rt_SECRET", "client_secret": "cs_SECRET", "password": "pw_SECRET", "ok": 1},
}
SECRETS = ("deadbeefcafe", "feedbead0001", "c0ffee00", "rt_SECRET", "cs_SECRET", "pw_SECRET")


class PluginGetJsonTests(unittest.TestCase):
    """`api.get_json` de plugin nunca grava cache e limpa URL/chave secreta a fundo."""

    def api(self):
        manifest = {
            "id": "demo",
            "contributes": {"providers": [], "presets": [], "routes": [], "commands": []},
            "permissions": {"network": ["api.example.com"], "env": [], "paths": []},
        }
        return PluginApi(manifest, Registry())

    def opener(self, payload):
        opener = MagicMock()
        opener.open.side_effect = lambda *a, **k: _Resp(json.dumps(payload).encode())
        return opener

    def test_plugin_get_json_is_scrubbed_and_never_cached(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_CACHE_DIR": tmp}):
            with patch.object(http, "_opener", return_value=self.opener(PAYLOAD)), patch.object(http, "_network_url"):
                data = self.api().get_json("https://api.example.com/v1/x", cache_ttl=3600)
            self.assertEqual([], list(Path(tmp).iterdir()))
        rendered = json.dumps(data)
        for secret in SECRETS:
            self.assertNotIn(secret, rendered)
        self.assertEqual("https://cdn.example.com/f.mp4", data["public"])
        self.assertEqual(1, data["nested"]["ok"])
        self.assertIn("baixe em", data["embedded"])

    def test_keep_signed_still_returns_the_signed_url_without_cache(self):
        signed = "https://cdn.example.com/f.mp4?X-Amz-Signature=abc&X-Amz-Expires=60"
        payload = {"download_url": signed, "client_secret": "cs_SECRET"}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_CACHE_DIR": tmp}):
            with patch.object(http, "_opener", return_value=self.opener(payload)), patch.object(http, "_network_url"):
                data = self.api().get_json("https://api.example.com/v1/dl", keep_signed=True)
            self.assertEqual([], list(Path(tmp).iterdir()))
        self.assertEqual(signed, data["download_url"])
        self.assertNotIn("client_secret", data)

    def test_builtin_get_json_cache_and_scrub_are_unchanged(self):
        url = "https://api.example.com/v1/builtin-cache"
        payload = {"items": [{"url": "https://cdn.example.com/a.mp4"}], "refresh_token_hint": "kept"}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_CACHE_DIR": tmp}):
            with patch.object(http, "_opener", return_value=self.opener(payload)), patch.object(http, "_network_url"):
                data = http.get_json(url, cache_ttl=3600)
            cache = Path(tmp) / (hashlib.sha256(url.encode()).hexdigest() + ".json")
            self.assertTrue(cache.is_file())
        self.assertEqual(payload, data)


PAGINATION_PAYLOAD = {
    "items": [],
    "next_page_token": "np_1",
    "nextPageToken": "np_2",
    "page_token": "pt_1",
    "continuation_token": "ct_1",
    "sort_key": "created_at",
    "cursor_key": "ck_1",
    "cursor": "cu_1",
    "refresh_token": "rt_SECRET",
    "client_secret": "cs_SECRET",
    "api_key": "ak_SECRET",
    "password": "pw_SECRET",
}

# Casar só snake_case exato deixaria uma API real com essas variações de nome
# (camelCase, kebab-case, prefixo composto) passar pelo scrub estrito sem ser
# tocada. Todos têm que sumir.
LEAKING_CREDENTIAL_KEYS_PAYLOAD = {
    "accessToken": "at_SECRET",
    "x-api-key": "xak_SECRET",
    "aws_secret_access_key": "awssak_SECRET",
    "secret_key": "sk_SECRET",
    "x-amz-security-token": "xast_SECRET",
    "jwt": "jwt_SECRET",
    "credentials": "cred_SECRET",
}


class PluginGetJsonPaginationKeysTests(unittest.TestCase):
    """O scrub estrito de `api.get_json` só derruba chave de credencial de
    verdade — chave de paginação/id que só TERMINA com uma palavra parecida
    (`*_key`, `*_token`) não pode mais sumir do JSON do plugin, em snake_case ou
    camelCase; uma chave de credencial de verdade continua sumindo mesmo fora do
    snake_case (`accessToken`, `x-api-key`,
    `aws_secret_access_key`...)."""

    def api(self):
        manifest = {
            "id": "demo",
            "contributes": {"providers": [], "presets": [], "routes": [], "commands": []},
            "permissions": {"network": ["api.example.com"], "env": [], "paths": []},
        }
        return PluginApi(manifest, Registry())

    def opener(self, payload):
        opener = MagicMock()
        opener.open.side_effect = lambda *a, **k: _Resp(json.dumps(payload).encode())
        return opener

    def get(self, payload):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"GB_CACHE_DIR": tmp}),
            patch.object(http, "_opener", return_value=self.opener(payload)),
            patch.object(http, "_network_url"),
        ):
            return self.api().get_json("https://api.example.com/v1/list", cache_ttl=3600)

    def test_pagination_keys_survive_snake_and_camel_case_while_credential_keys_still_drop(self):
        data = self.get(PAGINATION_PAYLOAD)
        self.assertEqual("np_1", data["next_page_token"])
        self.assertEqual("np_2", data["nextPageToken"])
        self.assertEqual("pt_1", data["page_token"])
        self.assertEqual("ct_1", data["continuation_token"])
        self.assertEqual("created_at", data["sort_key"])
        self.assertEqual("ck_1", data["cursor_key"])
        self.assertEqual("cu_1", data["cursor"])
        for dropped in ("refresh_token", "client_secret", "api_key", "password"):
            self.assertNotIn(dropped, data)

    def test_credential_keys_drop_regardless_of_case_and_separator(self):
        data = self.get(LEAKING_CREDENTIAL_KEYS_PAYLOAD)
        rendered = json.dumps(data)
        for dropped in (
            "accessToken",
            "x-api-key",
            "aws_secret_access_key",
            "secret_key",
            "x-amz-security-token",
            "jwt",
            "credentials",
        ):
            self.assertNotIn(dropped, data, dropped)
        for secret in (
            "at_SECRET",
            "xak_SECRET",
            "awssak_SECRET",
            "sk_SECRET",
            "xast_SECRET",
            "jwt_SECRET",
            "cred_SECRET",
        ):
            self.assertNotIn(secret, rendered, secret)


if __name__ == "__main__":
    unittest.main()
