"""Reviewable Playwright CLI capture plan; browser interactions stay with agent."""

# pylint: disable=missing-function-docstring
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0).

import json
from datetime import UTC

from . import _paths, effects
from .http import public_url
from .review import project_id
from .rules import domain_matches


def playwright_spec():
    """`@playwright/cli@<versão>`: a mesma versão que o `setup` instala, lida do `package.json` (fonte única)."""
    manifest = json.loads(_paths.data_path("package.json").read_text(encoding="utf-8"))
    return "@playwright/cli@" + manifest["dependencies"]["@playwright/cli"]


def plan(ledger, url, rules):
    if not public_url(url):
        raise ValueError("Use uma URL pública HTTPS sem credenciais.")
    if domain_matches(url, rules["blocked_domains"]):
        raise ValueError("Domínio bloqueado pelo usuário.")
    if not any(t in rules["asset_types"] for t in ("web_screenshot", "news_screenshot")):
        raise ValueError("Screenshots desabilitados em RULES.md.")
    asset_type = "news_screenshot" if "news_screenshot" in rules["asset_types"] else "web_screenshot"
    b = rules["browser"]
    mode = b["viewport"]
    w = b[mode + "_width"]
    h = b[mode + "_height"]
    session = "gb-" + project_id(ledger)[:8]
    project = ledger.root.parent
    effects.mkdir(project / "output/playwright", parents=True, exist_ok=True)
    from datetime import datetime

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    # Os comandos rodam na pasta do projeto (`cwd`): o arquivo vai relativo, sem o caminho da máquina.
    capture = "output/playwright/capture-" + stamp + ".png"
    prefix = [
        "npx",
        "--yes",
        "--package",
        playwright_spec(),
        "playwright-cli",
        "-s=" + session,
    ]
    commands = [
        [*prefix, "open", url, "--headed"],
        [*prefix, "resize", str(w), str(h)],
        [*prefix, "snapshot"],
        prefix + ["screenshot", "--filename=" + capture] + (["--full-page"] if b["full_page"] else []),
    ]
    return {
        "asset_type": asset_type,
        "url": url,
        "viewport": {"width": w, "height": h, "kind": mode, "emulates_device": False},
        "output": str(project / capture),
        "cwd": str(project),
        "commands": commands,
        "editorial_rules": rules["editorial_rules"],
        "preferred_domains": rules["preferred_domains"],
        "instructions": [
            "Rode os comandos na pasta do projeto (cwd).",
            "Execute open/resize e leia snapshot antes de interagir.",
            "Confirme manchete, data, autor, URL e conteúdo carregado.",
            "Se necessário, interaja por refs do snapshot e tire novo snapshot.",
            "Captura não passa por paywall/login nem autentica autoria automaticamente.",
            (
                f"Importe PNG com resolve --file --asset-type {asset_type} --source-url URL --title TITULO "
                "--captured-at ISO --shot ID."
            ),
            "Preview, revisão humana, permit e fetch continuam obrigatórios.",
        ],
    }
