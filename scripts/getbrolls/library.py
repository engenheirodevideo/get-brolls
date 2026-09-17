"""Biblioteca global de aprendizados: ponteiro editorial, nunca licença nem aprovação.

O que mora aqui atravessa projetos porque é memória de trabalho — que busca
acertou, que fonte falhou, que trecho já serviu e por quê. Direito de uso e
aprovação humana **não** atravessam: continuam por projeto, por revisão e por
intervalo, e toda resposta daqui repete isso em `rights_not_transferable`.
"""

import hashlib
import json
import os
import unicodedata
from pathlib import Path

from .models import now
from .rules import home_dir

SCHEMA_VERSION = 1
EMPTY = {
    "schema_version": SCHEMA_VERSION,
    "assets": [],
    "queries": [],
    "providers": {},
    "preferences": [],
}
OFF_NOTE = (
    "Biblioteca desligada por GB_LIBRARY=off: nada foi lido nem gravado em "
    "~/.getbrolls/library. Apague a variável para voltar a usá-la."
)


def enabled():
    return (os.environ.get("GB_LIBRARY") or "").strip().lower() != "off"


def library_dir():
    return home_dir() / "library"


def index_path():
    return library_dir() / "index.json"


def answer(**extra):
    """Toda resposta da biblioteca diz, na cara, que direito não viaja com ela."""
    return {"rights_not_transferable": True, "enabled": enabled(), **extra}


def _off(**extra):
    return {**answer(**extra), "note": OFF_NOTE}


def load_index():
    path = index_path()
    if not enabled() or not path.exists():
        return json.loads(json.dumps(EMPTY))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ValueError(
            f"{path} está com JSON inválido. Preserve o arquivo e conserte-o, ou "
            "apague-o para começar uma biblioteca nova; nenhum projeto depende dele."
        ) from None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path} não é uma biblioteca da versão {SCHEMA_VERSION}. Preserve o "
            "arquivo e conserte-o, ou apague-o para começar uma biblioteca nova."
        )
    for key, value in EMPTY.items():
        data.setdefault(key, json.loads(json.dumps(value)))
    return data


def _write_private(path, text):
    """Escrita atômica com 0600: a biblioteca é pessoal e nunca fica meio gravada."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(
            os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_index(data):
    _write_private(index_path(), json.dumps(data, ensure_ascii=False, indent=2))


def _write_note(text):
    """Nota longa vira arquivo; o índice guarda só o caminho relativo."""
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]
    relative = f"notes/{digest}.md"
    _write_private(library_dir() / relative, text.strip() + "\n")
    return relative


def _normalise(text):
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else " " for c in text)


def _tokens(text):
    return {t for t in _normalise(text).split() if t}


def learn_query(query, provider, outcome, note=None, auto=False):
    """Registra que uma busca acertou ou não numa fonte."""
    query = (query or "").strip()
    if not query:
        raise ValueError("Informe a busca real em --query.")
    if outcome not in ("hit", "miss"):
        raise ValueError("--outcome aceita hit ou miss.")
    provider = (provider or "").strip()
    if not provider:
        raise ValueError("Informe a fonte em --provider.")
    if not enabled():
        return _off(entry=None)
    data = load_index()
    entry = {
        "query": query,
        "provider": provider,
        "outcome": outcome,
        "at": now(),
        "note": _write_note(note) if (note or "").strip() else None,
    }
    if auto:
        # Marcado porque ninguém digitou: veio de uma falha real de provedor.
        entry["auto"] = True
    data["queries"].append(entry)
    counts = data["providers"].setdefault(
        provider, {"outcomes": {"hit": 0, "miss": 0}, "last_at": None}
    )
    counts["outcomes"][outcome] = counts["outcomes"].get(outcome, 0) + 1
    counts["last_at"] = entry["at"]
    save_index(data)
    return answer(entry=entry)


def learn_preference(text, by=None):
    """Guarda uma preferência editorial dita por uma pessoa."""
    text = (text or "").strip()
    if len(text) < 5:
        raise ValueError("Escreva a preferência como ela foi dita, com pelo menos 5 caracteres.")
    if not enabled():
        return _off(entry=None)
    data = load_index()
    entry = {"text": text, "by": (by or "").strip() or None, "at": now()}
    data["preferences"].append(entry)
    save_index(data)
    return answer(entry=entry)


def asset_id(source_url, clip_signature):
    return hashlib.sha256(
        json.dumps([source_url, clip_signature], sort_keys=True).encode()
    ).hexdigest()[:16]


def learn_from_candidate(project, ident, shot=None):
    """Copia o ponteiro editorial de um candidato já memorizado no projeto.

    A fonte é `references.json` (a decisão humana já registrada por `remember`)
    mais o candidato do ledger. Nada de `rights.evidence`, nada de `approval`:
    o que vai para a biblioteca não permite coletar nada em lugar nenhum.
    """
    from .ledger import Ledger
    from .review import project_id

    ledger = Ledger(project)
    c = ledger.get(ident)
    path = ledger.root / "references.json"
    items = (
        json.loads(path.read_text(encoding="utf-8")).get("items", [])
        if path.exists()
        else []
    )
    references = [r for r in items if r.get("id") == ident]
    if not references:
        raise ValueError(
            "Este candidato ainda não tem decisão registrada na memória do projeto. "
            'Rode `remember --candidate ' + ident + ' --decision approved|rejected '
            '--reason "..." --by NOME --project ...` antes de guardá-lo na biblioteca.'
        )
    reference = references[-1]
    if not enabled():
        return _off(entry=None)
    from .models import signature

    entry = {
        "asset_id": asset_id(c.get("source_url"), signature(c)),
        "source_url": c.get("source_url"),
        "provider": c.get("provider"),
        "title": c.get("title"),
        "creator": c.get("creator", {}).get("name"),
        "license_name": c.get("rights", {}).get("license_name"),
        "license_url": c.get("rights", {}).get("license_url"),
        # Só o nome da base declarada no projeto: é pista de onde procurar a
        # condição de uso, nunca a prova dela.
        "rights_basis": c.get("rights", {}).get("basis"),
        "clip": {
            "start_s": c["segment"]["start_s"],
            "end_s": c["segment"]["end_s"],
            "signature": signature(c),
        },
        "tags": sorted(_tokens(c.get("title")) | _tokens(c.get("narration")))[:12],
        "decision": reference["decision"],
        "reason": reference["reason"],
        "by": reference["by"],
        "at": now(),
        "used_by": [],
    }
    use = {"project_id": project_id(ledger), "shot": shot or c.get("shot"), "at": now()}
    data = load_index()
    for existing in data["assets"]:
        if existing["asset_id"] == entry["asset_id"]:
            existing["used_by"].append(use)
            existing["decision"] = entry["decision"]
            existing["reason"] = entry["reason"]
            existing["by"] = entry["by"]
            existing["at"] = entry["at"]
            save_index(data)
            return answer(entry=existing)
    entry["used_by"].append(use)
    data["assets"].append(entry)
    save_index(data)
    return answer(entry=entry)


def _score(term_tokens, text):
    found = term_tokens & _tokens(text)
    return len(found) / len(term_tokens) if term_tokens else 0.0


def search(term, limit=5):
    """Procura assets, buscas e preferências que já falaram desse assunto."""
    term = (term or "").strip()
    if not term:
        raise ValueError("Informe o que procurar em --search.")
    if not enabled():
        return _off(assets=[], queries=[], providers={}, preferences=[])
    wanted = _tokens(term)
    data = load_index()
    assets = sorted(
        (
            (a, _score(wanted, " ".join(filter(None, [a.get("title"), a.get("reason"), a.get("source_url"), *(a.get("tags") or [])]))))
            for a in data["assets"]
        ),
        key=lambda pair: -pair[1],
    )
    queries = sorted(
        ((q, _score(wanted, q.get("query"))) for q in data["queries"]),
        key=lambda pair: -pair[1],
    )
    preferences = sorted(
        ((p, _score(wanted, p.get("text"))) for p in data["preferences"]),
        key=lambda pair: -pair[1],
    )
    def keep(pairs):
        return [dict(item, score=round(score, 3)) for item, score in pairs if score][:limit]

    found_assets, found_queries = keep(assets), keep(queries)
    named = {item.get("provider") for item in found_assets + found_queries}
    return answer(
        term=term,
        assets=found_assets,
        queries=found_queries,
        preferences=keep(preferences),
        providers={
            name: counts
            for name, counts in data["providers"].items()
            if name in named
        },
    )


def hints(query, limit=5):
    """Pistas curtas para anexar a `search`; silenciosas quando não há biblioteca."""
    if not enabled():
        return []
    try:
        found = search(query, limit=limit)
    except ValueError:
        return []
    out = []
    for a in found["assets"]:
        out.append(
            {
                "kind": "asset",
                "source_url": a.get("source_url"),
                "provider": a.get("provider"),
                "title": a.get("title"),
                "decision": a.get("decision"),
                "reason": a.get("reason"),
                "rights_not_transferable": True,
            }
        )
    for q in found["queries"]:
        out.append(
            {
                "kind": "query",
                "query": q.get("query"),
                "provider": q.get("provider"),
                "outcome": q.get("outcome"),
                "rights_not_transferable": True,
            }
        )
    return out[:limit]
