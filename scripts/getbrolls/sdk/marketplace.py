"""Marketplaces de plugins: índices fixados por commit, lidos só do cache local.

Um marketplace é um repositório git com `getbrolls-marketplace.json` na raiz
(`marketplace_index`). `plugins --action marketplace-add --source <repo>` busca o
índice num commit (o de `--commit` ou o da `--ref`, padrão `HEAD`), valida e
grava:

- `$GB_HOME/marketplaces.json`: `{"schema": "getbrolls.marketplaces/1",
  "marketplaces": {<nome>: {source, ref, commit, index_sha256, added_at, updated_at}}}`;
- `$GB_HOME/marketplaces/<nome>/getbrolls-marketplace.json`: os bytes do índice
  naquele commit. Toda leitura confere o sha256 com o gravado; divergência é erro.

`marketplace-list` e `search` só leem esse cache (nenhuma rede). `marketplace-update`
busca de novo a ref gravada (ou o `--commit` pedido) e mostra o que mudou.

Política (teto de marketplaces): por padrão vale `profile.marketplace_ceiling()`,
lido na hora — `None` não restringe; um conjunto (mesmo vazio) é o teto.
`set_policy(allowed)` troca esse teto pelo conjunto dado (ou `None`, sem restrição)
até `set_policy(PROFILE_POLICY)` devolver a decisão ao perfil. Marketplace fora do
teto não pode ser adicionado nem atualizado por nome, some de `pinned_indexes` e do
`search`, e aparece com `allowed: false` no `marketplace-list`.
"""

import hashlib
import json
import os
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from .. import _paths, versioning
from ..errors import UsageError
from ..rules import home_dir
from ..runtime import force_rmtree, scrub_home
from . import git_source, install, loader
from .files import is_link
from .marketplace_index import (
    INDEX_MAX_BYTES,
    INDEX_NAME,
    SELF_REPO,
    SHA256_RE,
    MarketplaceIndexError,
    parse_index,
    resolve_rename,
    validate_marketplace_name,
)

STATE_FAMILY = "marketplaces"
STATE_VERSION = 1
STATE_NAME = "marketplaces.json"
CACHE_DIRNAME = "marketplaces"
RECORD_KEYS = ("source", "ref", "commit", "index_sha256", "added_at", "updated_at")
REMOVE_NOTE = (
    "Os plugins já instalados deste marketplace continuam instalados e habilitados; "
    "para tirar um, use plugins --action remove --id <id>."
)


class Pin(NamedTuple):
    """Um marketplace gravado: origem, ref pedida, commit fixado e sha256 do índice."""

    name: str
    source: str
    ref: str | None
    commit: str
    index_sha256: str


# `PROFILE_POLICY`: o teto vem de `profile.marketplace_ceiling()` na hora de cada checagem.
PROFILE_POLICY = object()
_policy: object = PROFILE_POLICY


def set_policy(names: object) -> None:  # Collection[str], None ou PROFILE_POLICY
    """Fixa o teto de marketplaces: um conjunto de nomes, `None` (sem restrição) ou
    `PROFILE_POLICY` (volta a ler `profile.marketplace_ceiling()`, o padrão)."""
    global _policy  # noqa: PLW0603  # pylint: disable=global-statement  # teto único do processo
    if names is None or names is PROFILE_POLICY:
        _policy = names
        return
    if isinstance(names, str) or not isinstance(names, Collection):
        raise TypeError("set_policy aceita um conjunto de nomes, None ou PROFILE_POLICY.")
    _policy = frozenset(names)


def _ceiling():
    if _policy is PROFILE_POLICY:
        from .. import profile

        return profile.marketplace_ceiling()
    return _policy


def allowed(name: str) -> bool:
    """O teto atual permite o marketplace `name`?"""
    ceiling = _ceiling()
    return ceiling is None or name in ceiling  # type: ignore[operator]  # conjunto de nomes


def _profile_hint():
    return f"veja `{_paths.cli_hint('profile', 'show')}`"


def not_allowed(name: str) -> ValueError:
    """Recusa de um marketplace fora do teto do perfil, com o comando que mostra o perfil."""
    return ValueError(f"O perfil de workspace não permite o marketplace {name}; {_profile_hint()}.")


def portable_text(text: object) -> str:
    """`text` sem caminho da máquina, para `capabilities` e `doctor`: `$GB_HOME` no lugar
    da pasta da instalação e `~` no lugar da pasta pessoal."""
    value = str(text)
    home = home_dir()
    for prefix in {str(home), str(home.resolve())}:
        value = value.replace(prefix, "$GB_HOME")
    return scrub_home(value)


def state_path() -> Path:
    """`$GB_HOME/marketplaces.json`."""
    return home_dir() / STATE_NAME


def cache_path(name: str) -> Path:
    """`$GB_HOME/marketplaces/<nome>/getbrolls-marketplace.json` (o nome é validado antes)."""
    try:
        validate_marketplace_name(name)
    except MarketplaceIndexError as exc:
        raise UsageError(str(exc)) from None
    return home_dir() / CACHE_DIRNAME / name / INDEX_NAME


def _empty_state():
    return {"schema": versioning.schema_name(STATE_FAMILY, STATE_VERSION), "marketplaces": {}}


def _valid_record(record):
    return (
        isinstance(record, dict)
        and set(record) == set(RECORD_KEYS)
        and isinstance(record["source"], str)
        and (record["ref"] is None or isinstance(record["ref"], str))
        and isinstance(record["commit"], str)
        and git_source.COMMIT_RE.fullmatch(record["commit"]) is not None
        and isinstance(record["index_sha256"], str)
        and SHA256_RE.fullmatch(record["index_sha256"]) is not None
        and isinstance(record["added_at"], str)
        and isinstance(record["updated_at"], str)
    )


def _valid_name(name):
    try:
        validate_marketplace_name(name)
    except MarketplaceIndexError:
        return False
    return True


def read_state() -> dict:
    """`marketplaces.json` como dict (vazio sem arquivo); inválido vira `ValueError`."""
    path = state_path()
    if not path.exists():
        return _empty_state()
    invalid = f"marketplaces.json inválido em {path}. Corrija ou apague o arquivo para recomeçar sem marketplaces."
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError, RecursionError):
        raise ValueError(invalid) from None
    if not isinstance(data, dict) or set(data) != {"schema", "marketplaces"}:
        raise ValueError(invalid)
    versioning.read_schema(data, STATE_FAMILY, STATE_VERSION, label=STATE_NAME, invalid=invalid)
    markets = data["marketplaces"]
    if not isinstance(markets, dict) or not all(
        _valid_name(name) and _valid_record(record) for name, record in markets.items()
    ):
        raise ValueError(invalid)
    return data


def _write_state(data):
    home_dir().mkdir(parents=True, exist_ok=True)
    ordered = {
        "schema": versioning.schema_name(STATE_FAMILY, STATE_VERSION),
        "marketplaces": dict(sorted(data["marketplaces"].items())),
    }
    _atomic_write_bytes(state_path(), (json.dumps(ordered, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _atomic_write_bytes(path, data):
    """Grava `data` como está (temporário + troca), sem nenhuma tradução de fim de linha."""
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _pin(name, record):
    return Pin(name, record["source"], record["ref"], record["commit"], record["index_sha256"])


def spec_for_add(source, ref, commit):
    spec = git_source.parse_source(source, commit=commit, ref=ref)
    if not isinstance(spec, git_source.GitSource):
        raise ValueError(
            "--source de um marketplace tem que ser um repositório git (URL https://, git@host:caminho ou "
            "pasta local com .git); pasta comum não fixa commit."
        )
    return spec


def _recorded_spec(pin, commit):
    """A origem gravada de novo como `GitSource`, conferida como na hora do add."""
    if git_source.is_remote(pin.source):
        return git_source.GitSource(git_source.validate_url(pin.source), commit, pin.ref)
    folder = Path(pin.source)
    if not folder.is_dir() or not git_source.is_repository(folder):
        raise ValueError(
            f"A origem do marketplace {pin.name} não é mais um repositório git acessível; "
            f"remova com plugins --action marketplace-remove --marketplace {pin.name} e adicione de novo."
        )
    return git_source.GitSource(str(folder), commit, pin.ref)


def _fetch_index(spec):
    """`(commit, bytes, índice)` do `getbrolls-marketplace.json` no commit de `spec`."""
    commit, data = install.fetch_file(spec, INDEX_NAME, INDEX_MAX_BYTES)
    return commit, data, parse_index(data)


def _write_cache(name, data):
    folder = cache_path(name).parent
    if is_link(folder):
        raise ValueError(f"{folder} é um link; recusado. Apague o link e rode de novo.")
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(cache_path(name), data)


def _summary(name, record, plugins=None):
    return {"name": name, **record, "allowed": allowed(name), "plugins": plugins}


def add(source: str, ref: str | None = None, commit: str | None = None) -> dict:
    """Adiciona o marketplace de `source` fixado no commit (o de `commit` ou o da `ref`)."""
    state = read_state()  # marketplaces.json corrompido recusa antes de qualquer rede ou mutação.
    spec = spec_for_add(source, ref, commit)
    if _ceiling() == frozenset():
        raise ValueError(f"O perfil de workspace não permite nenhum marketplace; {_profile_hint()}.")
    pinned, data, index = _fetch_index(spec)
    name = index["name"]
    if name in state["marketplaces"]:
        raise ValueError(
            f"O marketplace {name} já existe; use plugins --action marketplace-update --marketplace {name} "
            "(ou remova o antigo antes)."
        )
    if not allowed(name):
        raise not_allowed(name)
    now = _now()
    record = {
        "source": spec.repo,
        "ref": spec.ref,
        "commit": pinned,
        "index_sha256": hashlib.sha256(data).hexdigest(),
        "added_at": now,
        "updated_at": now,
    }
    _write_cache(name, data)
    state["marketplaces"][name] = record
    _write_state(state)
    count = len(index["plugins"])
    return {"added": True, "marketplace": _summary(name, record, count), "plugins": count}


def _record(state, name):
    try:
        validate_marketplace_name(name)
    except MarketplaceIndexError as exc:
        raise ValueError(str(exc)) from None
    record = state["marketplaces"].get(name)
    if record is None:
        raise ValueError(f"Marketplace {name} não encontrado; veja plugins --action marketplace-list.")
    return record


def _read_cache(pin):
    path = cache_path(pin.name)
    stale = f"rode plugins --action marketplace-update --marketplace {pin.name}."
    if is_link(path) or is_link(path.parent) or not path.is_file():
        raise ValueError(f"O índice em cache do marketplace {pin.name} sumiu ou não é um arquivo; {stale}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != pin.index_sha256:
        raise ValueError(
            f"O índice em cache do marketplace {pin.name} mudou desde o commit {pin.commit[:12]} "
            f"(sha256 não bate); {stale}"
        )
    return parse_index(data)


def load_index(name: str) -> tuple[Pin, dict]:
    """`(Pin, índice)` do marketplace `name`, lido do cache com o sha256 conferido."""
    pin = _pin(name, _record(read_state(), name))
    return pin, _read_cache(pin)


def pinned_indexes() -> list[tuple[Pin, dict]]:
    """`(Pin, índice)` de cada marketplace permitido pelo teto, em ordem de nome."""
    state = read_state()
    return [
        (pin, _read_cache(pin))
        for pin in (_pin(name, record) for name, record in sorted(state["marketplaces"].items()))
        if allowed(pin.name)
    ]


def listing() -> dict:
    """Marketplaces gravados, em ordem de nome, sem rede: cada um com `allowed`,
    `plugins` (quantos no índice) e `problem` (cache que não confere, ou `None`)."""
    rows = []
    for name, record in sorted(read_state()["marketplaces"].items()):
        try:
            plugins, problem = len(_read_cache(_pin(name, record))["plugins"]), None
        except ValueError as exc:
            plugins, problem = None, str(exc)
        rows.append({**_summary(name, record, plugins), "problem": problem})
    return {"state": str(state_path()), "marketplaces": rows}


def summary() -> list[dict]:
    """`[{name, commit, plugins, allowed, problem}]` em ordem de nome, sem rede e sem caminho
    da máquina (para `capabilities` e `doctor`). Só levanta `ValueError` (estado ilegível)."""
    rows = [
        {key: row[key] for key in ("name", "commit", "plugins", "allowed", "problem")}
        for row in listing()["marketplaces"]
    ]
    return [{**row, "problem": None if row["problem"] is None else portable_text(row["problem"])} for row in rows]


def _installed_from(name):
    """Ids instalados que vieram do marketplace `name` (pela origem gravada no plugins.json)."""
    sources = loader.read_state().get("sources", {})
    return sorted(plugin_id for plugin_id, origin in sources.items() if origin.get("marketplace") == name)


def remove(name: str) -> dict:
    """Tira o marketplace `name` do estado e apaga o cache; plugins instalados ficam."""
    state = read_state()
    record = _record(state, name)
    folder = cache_path(name).parent
    if is_link(folder):
        folder.unlink()
    elif folder.exists():
        force_rmtree(folder)
    del state["marketplaces"][name]
    _write_state(state)
    return {
        "removed": True,
        "marketplace": {"name": name, **record},
        "installed_plugins": _installed_from(name),
        "note": REMOVE_NOTE,
    }


def _diff(old, new):
    before = {entry["id"]: entry for entry in (old or {}).get("plugins", [])}
    after = {entry["id"]: entry for entry in new["plugins"]}
    updated = [
        {"id": plugin_id, "from": before[plugin_id]["version"], "to": after[plugin_id]["version"]}
        for plugin_id in sorted(set(before) & set(after))
        if (before[plugin_id]["version"], before[plugin_id]["content_sha256"])
        != (after[plugin_id]["version"], after[plugin_id]["content_sha256"])
    ]
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "updated": updated,
        "yanked": sorted(
            plugin_id
            for plugin_id, entry in after.items()
            if entry["yanked"] and not before.get(plugin_id, {}).get("yanked", False)
        ),
    }


def _installed_updates(name, index):
    """Ids instalados deste marketplace cuja entrada atual tem outro conteúdo (e não foi retirada)."""
    state = loader.read_state()
    entries = {entry["id"]: entry for entry in index["plugins"]}
    found = []
    for plugin_id in _installed_from(name):
        pinned = state.get("enabled", {}).get(plugin_id) or state.get("last_pins", {}).get(plugin_id)
        entry = entries.get(plugin_id)
        if entry is None or entry["yanked"] or pinned is None:
            continue
        if (pinned["version"], pinned["sha256"]) != (entry["version"], entry["content_sha256"]):
            found.append(plugin_id)
    return found


def _refresh_one(state, name, commit):
    record = _record(state, name)
    pin = _pin(name, record)
    try:
        old = _read_cache(pin)
    except ValueError:
        old = None  # cache adulterado ou ausente: o update o repõe; o diff parte do vazio.
    pinned, data, index = _fetch_index(_recorded_spec(pin, commit))
    if index["name"] != name:
        raise ValueError(
            f"O índice do marketplace {name} passou a se chamar {index['name']} no commit {pinned[:12]}; "
            "recusado. Remova e adicione de novo se a troca de nome for esperada."
        )
    sha = hashlib.sha256(data).hexdigest()
    changed = (pinned, sha) != (record["commit"], record["index_sha256"]) or old is None
    if changed:
        _write_cache(name, data)
        state["marketplaces"][name] = {**record, "commit": pinned, "index_sha256": sha, "updated_at": _now()}
        _write_state(state)  # um por vez: o cache gravado e o estado andam juntos
    return {
        "name": name,
        "allowed": True,
        "from": record["commit"],
        "to": pinned,
        "changed": changed,
        "diff": _diff(old, index),
        "installed_updates": _installed_updates(name, index),
    }


def refresh(name: str | None = None, commit: str | None = None) -> dict:
    """Busca de novo a ref gravada (ou `commit`) de um marketplace ou de todos e move o pin.

    Devolve, por marketplace, `from`/`to` (commits), `changed`, o `diff` do índice
    (`added`, `removed`, `updated` [{id, from, to}], `yanked`) e `installed_updates`
    (plugins instalados dele que têm conteúdo novo). Sem `name`, os que o teto não
    permite ficam de fora (`skipped: true`)."""
    if commit is not None and name is None:
        raise UsageError("--commit em marketplace-update precisa de --marketplace <nome>.")
    if commit is not None:
        git_source.validate_commit(commit)
    state = read_state()
    names = [name] if name is not None else sorted(state["marketplaces"])
    results = []
    for current in names:
        _record(state, current)
        if not allowed(current):
            if name is not None:
                raise not_allowed(current)
            results.append({"name": current, "allowed": False, "skipped": True})
            continue
        results.append(_refresh_one(state, current, commit))
    return {"marketplaces": results}


def resolve_repo(entry_repo: str, pin: Pin) -> str:
    """`source.repo` de uma entrada como origem git: `"."` é o próprio repositório do marketplace."""
    return pin.source if entry_repo == SELF_REPO else entry_repo


def _installed():
    """`{id: {"version", "marketplace"}}` dos plugins com pasta em `plugins/`, pelo plugins.json (sem rodar nada)."""
    state = loader.read_state()
    root = loader.plugins_root()
    found = {}
    for plugin_id in {*state.get("enabled", {}), *state.get("last_pins", {}), *state.get("sources", {})}:
        if not (root / plugin_id).is_dir():
            continue
        pinned = state.get("enabled", {}).get(plugin_id) or state.get("last_pins", {}).get(plugin_id) or {}
        origin = state.get("sources", {}).get(plugin_id) or {}
        found[plugin_id] = {"version": pinned.get("version"), "marketplace": origin.get("marketplace")}
    return found


def _install_hint(plugin_id, name):
    return _paths.cli_hint("plugins", "--action", "install", "--id", f"{plugin_id}@{name}")


def _matches(needle, entry):
    haystack = [entry["id"], entry["description"] or "", *entry["contributes"]]
    return any(needle in text.casefold() for text in haystack)


def _row(pin, entry, installed):
    return {
        "id": entry["id"],
        "marketplace": pin.name,
        "version": entry["version"],
        "description": entry["description"],
        "tier": entry["tier"],
        "contributes": entry["contributes"],
        "yanked": entry["yanked"],
        "deprecated": entry["deprecated"],
        "installed": installed.get(entry["id"]),
        "renamed_to": None,
        "install": None if entry["yanked"] else _install_hint(entry["id"], pin.name),
    }


def _renamed_row(pin, old, new, installed):
    return {
        "id": old,
        "marketplace": pin.name,
        "version": None,
        "description": None,
        "tier": None,
        "contributes": [],
        "yanked": False,
        "deprecated": None,
        "installed": installed.get(old),
        "renamed_to": new,
        "install": _install_hint(new, pin.name),
    }


def search(query: str, marketplace: str | None = None) -> dict:
    """Procura `query` (sem diferenciar maiúsculas) em `id`, `description` e `contributes`
    das entradas dos índices em cache — nunca na rede.

    Só marketplaces permitidos pelo teto; `marketplace` restringe a um. Resultados em
    ordem de `(id, marketplace)`, com `installed`, `tier`, `yanked`, `deprecated`, o
    comando de `install` (nenhum para entrada retirada) e, para um id antigo de
    `renames`, `renamed_to` com o id atual."""
    needle = query.strip().casefold() if isinstance(query, str) else ""
    if not needle:
        raise UsageError("--query é obrigatório em plugins --action search (um trecho do id ou da descrição).")
    if marketplace is not None:
        pin, index = load_index(marketplace)
        if not allowed(pin.name):
            raise not_allowed(pin.name)
        indexes = [(pin, index)]
    else:
        indexes = pinned_indexes()
    installed = _installed()
    results = []
    for pin, index in indexes:
        results += [_row(pin, entry, installed) for entry in index["plugins"] if _matches(needle, entry)]
        results += [
            _renamed_row(pin, old, resolve_rename(index, old) or new, installed)
            for old, new in index["renames"].items()
            if needle in old.casefold()
        ]
    results.sort(key=lambda row: (row["id"], row["marketplace"]))
    return {"query": query, "marketplaces": [pin.name for pin, _ in indexes], "results": results}
