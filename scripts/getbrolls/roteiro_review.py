"""Revisão humana do roteiro, amarrada ao conteúdo e não à formatação.

`review` grava o sha256 de um JSON canônico do roteiro lido: marcador de versão,
meta sem `status` e, por cena, título, diretivas (com a posição na fala), fala
com espaços normalizados, notas e o papel do `[FULL: x]` (beat ou marca, que
depende de `assets/marca`). Linha em branco, espaço sobrando, comentário de id e a
linha `status` não contam; trocar uma palavra da fala, um alvo ou mudar uma
camada de lugar conta — e aí o `sync` recusa até a pessoa revisar de novo.
"""

import json
from pathlib import Path

from .models import now
from .roteiro import STATUSES, frontmatter_key, parse_frontmatter
from .roteiro_plan import HASH_VERSION, digest, scene_fingerprint
from .runtime import _ensure_private_file

REVIEWS_FILE = "roteiro-reviews.jsonl"


def review_hash(doc, project):
    """sha256 do documento lido: versão + meta sem `status` + impressão digital de cada cena."""
    meta = {key: value for key, value in doc.meta.items() if key != "status"}
    scenes = [scene_fingerprint(scene, project) for scene in doc.scenes]
    return digest({"v": HASH_VERSION, "meta": meta, "scenes": scenes})


def set_status(text, status):
    """Troca (ou insere) `status:` no frontmatter; os limites vêm do próprio `parse_frontmatter`."""
    if status not in STATUSES:
        raise ValueError(f'status "{status}" não existe; use: {", ".join(STATUSES)}.')
    lines = text.split("\n")
    _, body, errors = parse_frontmatter(lines)
    if errors:
        raise ValueError("ROTEIRO.md com frontmatter inválido: rode `roteiro --action check` e corrija antes.")
    closing = body - 1
    for index in range(1, closing):
        if frontmatter_key(lines[index]) == "status":
            lines[index] = f"status: {status}"
            break
    else:
        lines.insert(closing, f"status: {status}")
    return "\n".join(lines)


def reviews_path(project):
    """Caminho absoluto de `brolls/roteiro-reviews.jsonl` do projeto."""
    return Path(project).expanduser().resolve() / "brolls" / REVIEWS_FILE


def record_review(project, doc, by, channel, statement):
    """Acrescenta uma linha em `brolls/roteiro-reviews.jsonl` (append-only) e devolve a entrada."""
    if not isinstance(by, str) or not by.strip():
        raise ValueError("Informe em --by quem revisou o roteiro.")
    if channel != "chat":
        raise ValueError("Revisão de roteiro só pelo chat por enquanto: use --channel chat.")
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError("Revisão pelo chat exige --statement com a frase exata dita pela pessoa.")
    entry = {
        "at": now(),
        "by": by.strip(),
        "channel": channel,
        "statement": statement,
        "sha256": review_hash(doc, project),
    }
    path = reviews_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_private_file(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def review_state(project, doc):
    """Revisão que vale para o texto atual (a mais recente com o mesmo hash). Só lê."""
    current = review_hash(doc, project)
    path = reviews_path(project)
    if path.is_file():
        for raw in reversed(path.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("sha256") == current:
                return {"reviewed": True, "by": entry.get("by"), "at": entry.get("at"), "sha256": current}
    return {"reviewed": False, "by": None, "at": None, "sha256": current}
