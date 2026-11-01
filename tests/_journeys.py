"""Base dos testes de jornada: o produto inteiro pela CLI real, sem rede e sem tocar a pasta pessoal.

Cada teste de jornada ganha uma pasta pessoal (`GB_HOME`), um projeto e um vídeo sintético só dele.
A fonte de busca é um plugin `demo` local (provider + rota de prévia que copia o vídeo sintético):
o caminho `brief → search → preview → approve → permit → fetch → deliver` roda de verdade, com FFmpeg
real, e a única coisa que falta é a internet. Tudo passa por subprocesso (`_cli.run_cli`), como a pessoa
ou o agente usariam, então código de saída, JSON de sucesso (stdout) e JSON de erro (stderr) são os reais.
"""

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _media import synth_video
from _paths import CLI_ARGV

from getbrolls.ledger import Ledger

APPROVAL = "Aprovo este trecho para o vídeo."
EVIDENCE = "Material próprio de teste sintético."

BRIEF = {
    "version": 1,
    "video": {
        "title": "Reels sobre o mar",
        "objective": "Mostrar o mar calmo antes da tempestade",
        "audience": "Quem edita vídeo",
        "delivery": {"format": "native", "duration_s": 30, "platform": "instagram"},
    },
    "rights": {"posture": "per_item_evidence", "stock_allowed": False, "notes": None},
    "defaults": {"allowed_sources": ["demo"], "intent": "literal", "duration_hint_s": 4, "stock": False},
    "beats": [
        {
            "id": "abertura",
            "narration": "O mar estava calmo naquela manhã.",
            "target": "Mar calmo ao amanhecer",
            "queries": ["mar calmo"],
        },
        {
            "id": "tempestade",
            "narration": "Então o céu fechou de repente.",
            "target": "Nuvens escuras sobre o mar",
            "queries": ["nuvens escuras"],
        },
    ],
}

# Fonte de mentira: duas respostas por busca (ids 1 e 2), nenhuma para a palavra "nada". A rota de prévia
# copia o vídeo sintético que o teste aponta em DEMO_SOURCE e anota cada chamada em DEMO_CALLS.
PLUGIN_CODE = """
import os
import shutil
from pathlib import Path

from getbrolls.sdk import ProviderCapabilities, RouteResult


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True, route="demo")

    def __init__(self, api):
        self.api = api

    def one(self, number, query):
        item = self.api.candidate("demo", number, "Demo " + query + " " + number, "https://demo.example/v/" + number)
        item["preview"]["embed_url"] = "https://demo.example/embed/" + number
        item["media"]["duration_s"] = 6
        item["media"]["kind"] = "video"
        return item

    def search(self, query, limit, media):
        if "nada" in query:
            return []
        return [self.one("1", query), self.one("2", query)][:limit]

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


class Copia:
    name = "demo"
    stage = "preview"

    def prepare(self, item, workdir):
        with Path(os.environ["DEMO_CALLS"]).open("a", encoding="utf-8") as calls:
            calls.write(item["id"] + "\\n")
        target = workdir / "v.mp4"
        shutil.copyfile(os.environ["DEMO_SOURCE"], target)
        return RouteResult(target, None)


def register(api):
    api.provider(Fonte(api))
    api.route(Copia())
"""
PLUGIN_MANIFEST = {
    "id": "demo",
    "name": "Demo",
    "version": "0.1.0",
    "sdk_api": 1,
    "requires_getbrolls": ">=2.5,<3",
    "entry": "plugin.py",
    "contributes": {"providers": ["demo"], "routes": ["demo"]},
    "permissions": {"network": ["demo.example"], "env": []},
}


def write_brief(project, data=None):
    """BRIEF.md com um único bloco JSON (o formato que o `brief` lê)."""
    body = json.dumps(BRIEF if data is None else data, ensure_ascii=False, indent=2)
    path = Path(project) / "BRIEF.md"
    path.write_text("---\ntype: brief\n---\n\n# Brief\n\n```json\n" + body + "\n```\n", encoding="utf-8")
    return path


def brief_with(**changes):
    """Cópia do BRIEF de jornada com `changes` na raiz."""
    data = copy.deepcopy(BRIEF)
    data.update(changes)
    return data


def install_plugin(home, manifest, code):
    """Escreve um plugin em `<home>/plugins/<id>` (ainda desligado)."""
    folder = Path(home) / "plugins" / manifest["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "getbrolls-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "plugin.py").write_text(code, encoding="utf-8")
    return folder


class JourneyCase(unittest.TestCase):
    """Pasta pessoal, projeto e vídeo sintético por teste; `gb(...)` roda a CLI real contra eles."""

    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="gb-journey-")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.home = base / "home"
        self.project = base / "project"
        self.project.mkdir()
        self.calls = base / "calls.txt"
        self.source = base / "fonte.mp4"
        synth_video(self.source, size="160x90", duration=6, rate=10)
        self.env = {
            "GB_HOME": str(self.home),
            "GB_CACHE_DIR": str(base / "cache"),
            "DEMO_SOURCE": str(self.source),
            "DEMO_CALLS": str(self.calls),
            "GB_PACE_MIN_S": "0",
            "GB_PACE_MAX_S": "0",
        }

    def gb(self, *args, expect=0, project=True):
        """Roda `gb.py <args> --project <projeto>`; devolve o JSON de stdout (expect=0) ou o de stderr."""
        return run_cli(*args, project=self.project if project else None, expect=expect, env=self.env)

    def raw(self, *args):
        """O subprocesso cru (para o que não sai em JSON, como o uso do argparse)."""
        return subprocess.run(
            [*CLI_ARGV, *map(str, args), "--project", str(self.project)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, **self.env},
            check=False,
        )

    def enable_demo(self):
        """Instala e liga o plugin `demo` pela CLI, como a pessoa faria (`plugins --action enable --yes`)."""
        install_plugin(self.home, PLUGIN_MANIFEST, PLUGIN_CODE)
        done = self.gb("plugins", "--action", "enable", "--id", "demo", "--yes", project=False)
        self.assertTrue(done["enabled"], done)

    def search(self, query, shot, expect=0):
        return self.gb("search", "--provider", "demo", "--query", query, "--shot", shot, expect=expect)

    def manifest(self):
        return json.loads((self.project / "brolls" / "manifest.json").read_text(encoding="utf-8"))

    def item(self, ident):
        return next(row for row in self.manifest()["items"] if row["id"] == ident)

    def ledger_bytes(self):
        """O manifest como está no disco: prova de que uma recusa não gravou nada."""
        return (self.project / "brolls" / "manifest.json").read_bytes()

    def approve(self, ident, expect=0):
        return self.gb("approve", "--candidate", ident, "--by", "Ana", "--statement", APPROVAL, expect=expect)

    def permit(self, ident, expect=0):
        return self.gb("permit", "--candidate", ident, "--evidence", EVIDENCE, expect=expect)

    def collect(self, ident, start=0, end=2):
        """preview → approve → permit → fetch de um candidato; devolve o JSON do fetch."""
        self.gb("preview", "--candidate", ident, "--start", start, "--end", end)
        self.approve(ident)
        self.permit(ident)
        return self.gb("fetch", "--candidate", ident)

    def calls_made(self):
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def ledger(self):
        return Ledger(self.project, recover=False)
