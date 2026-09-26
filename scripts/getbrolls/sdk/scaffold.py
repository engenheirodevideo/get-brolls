"""`plugins --action new`: gera um plugin mínimo que já passa no próprio teste e no `check`."""

import json
from pathlib import Path

from .. import __version__
from .contracts import CORE, NAME_RE, RESERVED_IDS, SDK_API
from .manifest import MANIFEST_NAME, reserved_id_message

KINDS = ("provider", "route", "command")

PROVIDER = '''"""Plugin __ID__ para o Get B-rolls (gerado por `plugins --action new --kind provider`)."""

from getbrolls.sdk import ProviderCapabilities


class Fonte:
    name = "__ID__"
    capabilities = ProviderCapabilities(search=True, match_kind="illustrative")

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        # Troque pela busca na sua fonte (api.get_json com host em permissions.network).
        return [self.api.candidate(self.name, "exemplo-1", f"Exemplo: {query}")][:limit]

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


def register(api):
    api.provider(Fonte(api))
'''

ROUTE = '''"""Plugin __ID__ para o Get B-rolls (gerado por `plugins --action new --kind route`).

A busca devolve metadados; a rota `__ID__` só baixa o arquivo no `fetch`, depois
da aprovação e do permit, com o token de `__ENV__` no header.
"""

from getbrolls.sdk import PluginError, ProviderCapabilities, RouteResult

API = "https://api.example.com/v1"


class Fonte:
    name = "__ID__"
    capabilities = ProviderCapabilities(search=True, match_kind="illustrative", env_key="__ENV__", route="__ID__")

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        item = self.api.candidate(self.name, "exemplo-1", f"Exemplo: {query}")
        # Preencha item["preview"]["poster_url"] (miniatura) ou ["embed_url"] (player):
        # sem nada para a pessoa ver, o core recusa aprovar um candidato de plugin.
        return [item][:limit]

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


class Download:
    name = "__ID__"
    stage = "fetch"

    def __init__(self, api):
        self.api = api

    def prepare(self, item, workdir):
        token = self.api.env("__ENV__")
        if not token:
            # PluginError: a única exceção cujo texto chega a quem usa (as outras, só o tipo).
            raise PluginError("Configure __ENV__ com o token da sua conta.")
        url = f"{API}/files/{item['source_id']}"
        path = self.api.download(url, "original.mp4", headers={"Authorization": f"Bearer {token}"})
        return RouteResult(path, license=None)


def register(api):
    api.provider(Fonte(api))
    api.route(Download(api))
'''

COMMAND = '''"""Plugin __ID__ para o Get B-rolls (gerado por `plugins --action new --kind command`)."""


def resumo(args, ctx):
    items = ctx.candidates()
    return {"plugin": ctx.plugin_id, "candidatos": len(items), "args": args}


def register(api):
    api.command("resumo", resumo, "Conta os candidatos do projeto (somente leitura)")
'''

TEST = '''"""Contrato do plugin __ID__: roda com PYTHONPATH=<pasta da skill>/scripts."""

import unittest
from pathlib import Path

from getbrolls.sdk import testing

FOLDER = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_plugin_passes_the_sdk_contract(self):
        report = testing.check_plugin(FOLDER)
        self.assertTrue(report["ok"])
        self.assertEqual("__ID__", report["id"])


if __name__ == "__main__":
    unittest.main()
'''

README = """# __ID__

Plugin gerado por `plugins --action new --kind __KIND__`. Antes de usar:

1. Edite `getbrolls-plugin.json` (nome, descrição, `permissions`) e `plugin.py`.
2. Teste, **com esta pasta como diretório atual** (`cd <esta pasta>`; o `-s tests` é relativo a ela):
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<pasta da skill>/scripts python3 -m unittest discover -s tests`
   (de outra pasta, use `-s <esta pasta>/tests`; sem o `PYTHONDONTWRITEBYTECODE`, o `__pycache__`
   criado pelo teste muda o hash do plugin). No PowerShell:
   `$env:PYTHONDONTWRITEBYTECODE = "1"; $env:PYTHONPATH = "<pasta da skill>\\scripts"; python -m unittest discover -s tests`
3. Confira: `python3 scripts/gb.py plugins --action check --path <esta pasta>`.
4. Instale em dois passos: `python3 scripts/gb.py plugins --action install --source <esta pasta>` mostra a
   prévia com o `sha256`; com o ok, repita com `--yes --expect <sha256 da prévia>`.
"""


def _manifest(plugin_id, kind):
    major, minor = (int(part) for part in __version__.split(".")[:2])
    contributes = {
        "provider": {"providers": [plugin_id]},
        "route": {"providers": [plugin_id], "routes": [plugin_id]},
        "command": {"commands": ["resumo"]},
    }[kind]
    env = [f"{plugin_id.upper()}_TOKEN"] if kind == "route" else []
    network = ["api.example.com"] if kind == "route" else []
    return {
        "id": plugin_id,
        "name": f"Plugin {plugin_id}",
        "description": "Descreva em uma frase o que este plugin traz.",
        "version": "0.1.0",
        "sdk_api": SDK_API,
        "requires_getbrolls": f">={major}.{minor},<{major + 1}",
        "entry": "plugin.py",
        "contributes": contributes,
        "permissions": {"network": network, "env": env, "paths": []},
    }


def new(plugin_id, kind, parent=None):
    if not isinstance(plugin_id, str) or not NAME_RE.fullmatch(plugin_id) or plugin_id == CORE:
        raise ValueError("--id inválido; use 2–32 caracteres a-z, 0-9 e _, começando por letra.")
    if plugin_id in RESERVED_IDS:
        raise ValueError(f"--id: {reserved_id_message(plugin_id)}")
    if kind not in KINDS:
        raise ValueError(f"--kind aceita {', '.join(KINDS)}.")
    parent_dir = Path(parent or ".").expanduser().resolve()
    folder = parent_dir / plugin_id
    # `is_symlink()` primeiro (nunca segue o link, mesmo quebrado) — só depois
    # `exists()` (que segue link): nunca escrevemos através de um link plantado
    # em `--path`/<id>, nem sobrescrevemos uma pasta (vazia ou não) já ali.
    if folder.is_symlink() or folder.exists():
        raise ValueError(f"{folder} já existe; escolha outro --id ou outra --path.")
    code = {"provider": PROVIDER, "route": ROUTE, "command": COMMAND}[kind]
    env = f"{plugin_id.upper()}_TOKEN"
    (folder / "tests").mkdir(parents=True)
    (folder / MANIFEST_NAME).write_text(
        json.dumps(_manifest(plugin_id, kind), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (folder / "plugin.py").write_text(code.replace("__ID__", plugin_id).replace("__ENV__", env), encoding="utf-8")
    (folder / "tests" / "test_plugin.py").write_text(TEST.replace("__ID__", plugin_id), encoding="utf-8")
    (folder / "README.md").write_text(README.replace("__ID__", plugin_id).replace("__KIND__", kind), encoding="utf-8")
    return {
        "created": str(folder),
        "id": plugin_id,
        "kind": kind,
        "files": [MANIFEST_NAME, "plugin.py", "tests/test_plugin.py", "README.md"],
        "next": f"plugins --action check --path {folder}",
    }
