"""Biblioteca global de aprendizados: ponteiro editorial, nunca licença nem aprovação.

O que mora aqui atravessa projetos porque é memória de trabalho — que busca
acertou, que fonte falhou, que trecho já serviu e por quê. Direito de uso e
aprovação humana **não** atravessam: continuam por projeto, por revisão e por
intervalo, e toda resposta daqui repete isso em `rights_not_transferable`.
"""

import contextlib
import hashlib
import json
import logging
import os
import time
import unicodedata
import uuid

from . import logs
from .models import now
from .rules import home_dir

_log = logs.get(__name__.rsplit(".", 1)[-1])

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
    """Escrita atômica com 0600: a biblioteca é pessoal e nunca fica meio gravada.

    O temporário leva pid e um sufixo aleatório: duas escritas simultâneas nunca
    disputam o mesmo arquivo nem apagam o `.tmp` uma da outra.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(  # noqa: PTH123 - wraps an os.open() fd (explicit flags/mode), no Path equivalent
            os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def save_index(data):
    _write_private(index_path(), json.dumps(data, ensure_ascii=False, indent=2))


# Trava de diretório: um arquivo criado com O_EXCL, que funciona igual no Windows.
LOCK_TIMEOUT_S = 10.0
STALE_LOCK_S = 60.0
MAX_QUERIES = 500


@contextlib.contextmanager
def _locked():
    """Serializa ler → mudar → gravar; sem isso duas escritas perdem uma entrada."""
    path = library_dir() / "index.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + LOCK_TIMEOUT_S
    while True:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            break
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > STALE_LOCK_S
            except OSError:
                stale = False
            if stale:
                # Sobrou de um processo que morreu no meio: ninguém a renova.
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            if time.monotonic() > deadline:
                logs.event(
                    _log,
                    logging.WARNING,
                    "library_lock",
                    waited_ms=round((time.monotonic() - started) * 1000),
                    timeout=True,
                )
                raise ValueError(
                    f"{path} está travado há tempo demais por outro get-brolls. "
                    "Espere a outra execução terminar ou apague esse arquivo."
                ) from None
            time.sleep(0.02)
    logs.event(
        _log,
        logging.DEBUG,
        "library_lock",
        waited_ms=round((time.monotonic() - started) * 1000),
        timeout=False,
    )
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def _update(mutate):
    """Aplica uma mudança no índice inteiro sob trava, e devolve o que ela retornar."""
    with _locked():
        data = load_index()
        result = mutate(data)
        save_index(data)
    return result


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
        result = _off(entry=None)
        logs.event(_log, logging.INFO, "library", action="learn", entries=0, disabled=True)
        return result
    note_path = _write_note(note) if (note or "").strip() else None

    def mutate(data):
        at = now()
        same = next(
            (
                q
                for q in data["queries"]
                if (q["query"], q["provider"], q["outcome"], bool(q.get("auto")))
                == (query, provider, outcome, bool(auto))
            ),
            None,
        )
        if same is not None:
            # A mesma busca repetida é um contador, não uma linha nova: senão a
            # biblioteca cresce sem parar e cada `search` reescreve o índice inteiro.
            same["count"] = same.get("count", 1) + 1
            same["at"] = at
            same["note"] = note_path or same.get("note")
            entry = same
        else:
            entry = {
                "query": query,
                "provider": provider,
                "outcome": outcome,
                "count": 1,
                "at": at,
                "note": note_path,
            }
            if auto:
                # Marcado porque ninguém digitou: veio de uma falha real de provedor.
                entry["auto"] = True
            data["queries"].append(entry)
        if len(data["queries"]) > MAX_QUERIES:
            # Teto: o que ninguém digitou sai primeiro, do mais antigo para o mais novo.
            order = sorted(
                range(len(data["queries"])),
                key=lambda i: (
                    not data["queries"][i].get("auto"),
                    data["queries"][i].get("at") or "",
                ),
            )
            drop = set(order[: len(data["queries"]) - MAX_QUERIES])
            data["queries"] = [q for i, q in enumerate(data["queries"]) if i not in drop]
        counts = data["providers"].setdefault(provider, {"outcomes": {"hit": 0, "miss": 0}, "last_at": None})
        counts["outcomes"][outcome] = counts["outcomes"].get(outcome, 0) + 1
        counts["last_at"] = at
        return entry

    result = answer(entry=_update(mutate))
    logs.event(_log, logging.INFO, "library", action="learn", entries=1, disabled=False)
    return result


def learn_preference(text, by=None):
    """Guarda uma preferência editorial dita por uma pessoa."""
    text = (text or "").strip()
    if len(text) < 5:
        raise ValueError("Escreva a preferência como ela foi dita, com pelo menos 5 caracteres.")
    if not enabled():
        result = _off(entry=None)
        logs.event(_log, logging.INFO, "library", action="learn", entries=0, disabled=True)
        return result
    entry = {"text": text, "by": (by or "").strip() or None, "at": now()}

    def mutate(data):
        data["preferences"].append(entry)
        return entry

    result = answer(entry=_update(mutate))
    logs.event(_log, logging.INFO, "library", action="learn", entries=1, disabled=False)
    return result


def asset_id(source_url, clip_signature):
    return hashlib.sha256(json.dumps([source_url, clip_signature], sort_keys=True).encode()).hexdigest()[:16]


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
    items = json.loads(path.read_text(encoding="utf-8")).get("items", []) if path.exists() else []
    references = [r for r in items if r.get("id") == ident]
    if not references:
        raise ValueError(
            "Este candidato ainda não tem decisão registrada na memória do projeto. "
            "Rode `remember --candidate " + ident + " --decision approved|rejected "
            '--reason "..." --by NOME --project ...` antes de guardá-lo na biblioteca.'
        )
    reference = references[-1]
    if not enabled():
        result = _off(entry=None)
        logs.event(_log, logging.INFO, "library", action="learn", entries=0, disabled=True)
        return result
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

    def mutate(data):
        for existing in data["assets"]:
            if existing["asset_id"] == entry["asset_id"]:
                existing["used_by"].append(use)
                existing["decision"] = entry["decision"]
                existing["reason"] = entry["reason"]
                existing["by"] = entry["by"]
                existing["at"] = entry["at"]
                return existing
        entry["used_by"].append(use)
        data["assets"].append(entry)
        return entry

    result = answer(entry=_update(mutate))
    logs.event(_log, logging.INFO, "library", action="learn", entries=1, disabled=False)
    return result


def _score(term_tokens, text):
    found = term_tokens & _tokens(text)
    return len(found) / len(term_tokens) if term_tokens else 0.0


def search(term, limit=5):
    """Procura assets, buscas e preferências que já falaram desse assunto."""
    term = (term or "").strip()
    if not term:
        raise ValueError("Informe o que procurar em --search.")
    if not enabled():
        result = _off(assets=[], queries=[], providers={}, preferences=[])
        logs.event(_log, logging.INFO, "library", action="search", entries=0, disabled=True)
        return result
    wanted = _tokens(term)
    data = load_index()
    assets = sorted(
        (
            (
                a,
                _score(
                    wanted,
                    " ".join(
                        filter(None, [a.get("title"), a.get("reason"), a.get("source_url"), *(a.get("tags") or [])])
                    ),
                ),
            )
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
    found_preferences = keep(preferences)
    named = {item.get("provider") for item in found_assets + found_queries}
    result = answer(
        term=term,
        assets=found_assets,
        queries=found_queries,
        preferences=found_preferences,
        providers={name: counts for name, counts in data["providers"].items() if name in named},
    )
    logs.event(
        _log,
        logging.INFO,
        "library",
        action="search",
        entries=len(found_assets) + len(found_queries) + len(found_preferences),
        disabled=False,
    )
    return result


def hints(query, limit=5):
    """Pistas curtas para anexar a `search`; silenciosas quando não há biblioteca.

    Sem `reason` e sem `by`: esses textos foram escritos noutro projeto, às vezes
    sobre outro cliente. Quem quiser lê-los pede de propósito, em `library --search`.
    """
    if not enabled():
        logs.event(_log, logging.INFO, "library", action="hints", entries=0, disabled=True)
        return []
    try:
        found = search(query, limit=limit)
    except (ValueError, OSError) as error:
        # Non-fatal (hints are advisory), but a corrupted index must not look
        # identical to "nothing found" — surface it on the same warnings channel
        # every other command uses, naming the file so the person can act on it.
        from .runtime import record_warning

        record_warning(
            "LIBRARY_INDEX_UNREADABLE",
            f"Biblioteca de aprendizados ({index_path()}) não pôde ser lida; pistas ignoradas nesta busca: {error}",
        )
        return []
    out = [
        {
            "kind": "asset",
            "source_url": a.get("source_url"),
            "provider": a.get("provider"),
            "title": a.get("title"),
            "decision": a.get("decision"),
            "rights_not_transferable": True,
        }
        for a in found["assets"]
    ]
    out.extend(
        {
            "kind": "query",
            "query": q.get("query"),
            "provider": q.get("provider"),
            "outcome": q.get("outcome"),
            "rights_not_transferable": True,
        }
        for q in found["queries"]
    )
    result = out[:limit]
    logs.event(_log, logging.INFO, "library", action="hints", entries=len(result), disabled=False)
    return result
