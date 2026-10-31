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

import errno
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from .. import _paths, effects, json_read, labels, logs, versioning
from ..errors import UsageError
from ..rules import home_dir
from ..runtime import force_rmtree, record_warning, scrub_home, usage_errors
from . import git_source, install, jsonschema, loader, schemas
from .files import is_link
from .guard import plain_line
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

_log = logs.get("sdk")

STATE_FAMILY = "marketplaces"
STATE_NAME = "marketplaces.json"
CACHE_DIRNAME = "marketplaces"
# Teto de leitura do marketplaces.json: cada marketplace ocupa umas centenas de bytes, então milhares
# cabem com folga em 1 MiB, bem abaixo do teto padrão do `json_read` (o pior caso de memória cai junto).
STATE_MAX_BYTES = 1024 * 1024
RECORD_KEYS = ("source", "ref", "commit", "index_sha256", "added_at", "updated_at")
REMOVE_NOTE = (
    "Os plugins já instalados deste marketplace continuam instalados e habilitados; "
    "para tirar um, use plugins --action remove --id ID."
)


# O índice oficial: o nome sozinho não basta (qualquer repositório pode se chamar assim);
# a origem fixada tem que ser um destes repositórios. Só ele tem `tier_verified: true`.
OFFICIAL_INDEX_NAME = "getbrolls-plugins"
OFFICIAL_INDEX_REPOS = tuple(
    f"https://github.com/engenheirodevideo/{repo}{suffix}"
    for repo in ("getbrolls-plugins", "get-brolls-plugins")
    for suffix in ("", ".git")
)
TIER_NOT_VERIFIED = "declarado pelo marketplace, não verificado"
TIER_LABEL_SUFFIX = "declarado, não verificado"
NOT_OFFICIAL_WARNING = (
    "Este marketplace não é o índice oficial ({name} de {repo}): o tier de cada plugin é declarado por "
    "ele, não verificado, e nunca muda as regras de instalação."
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
    return f"veja `{_paths.cli_hint('profile', '--action', 'show')}`"


def _refused(action, reason, exc, **fields):
    """Registra a recusa (`marketplace_refused`: ação, motivo e o `error_code` do erro) e devolve `exc`."""
    return logs.refusal(_log, "marketplace_refused", exc, action=action, reason=reason, **fields)


def not_allowed(name: str, action: str | None = None) -> ValueError:
    """Recusa de um marketplace fora do teto do perfil, com o comando que mostra o perfil."""
    error = ValueError(f"O perfil de workspace não permite o marketplace {name}; {_profile_hint()}.")
    return _refused(action, "not_allowed", error, marketplace=name)


def portable_text(text: object) -> str:
    """`text` sem caminho da máquina, para `capabilities`, o `problem` do marketplace e `doctor`: as
    mesmas marcas de toda mensagem (`labels.text`: `<projeto>/…`, `$GB_HOME/…`, `$TMPDIR/…`, `~/…`) e,
    fora de todas, `<pasta fora do projeto>/#<hash>`.

    As duas grafias (como escrita e resolvida: no macOS `/var/…` é `/private/var/…`) valem,
    só como caminho inteiro: `/x/gb2` não vira `$GB_HOME2`."""
    return scrub_home(labels.text(text))


def state_path() -> Path:
    """`$GB_HOME/marketplaces.json`."""
    return home_dir() / STATE_NAME


def cache_path(name: str) -> Path:
    """`$GB_HOME/marketplaces/<nome>/getbrolls-marketplace.json` (o nome é validado antes)."""
    with usage_errors():
        validate_marketplace_name(name)
    return home_dir() / CACHE_DIRNAME / name / INDEX_NAME


def _state_schema():
    return versioning.schema_name(STATE_FAMILY, schemas.version(STATE_FAMILY))


def _empty_state():
    return {"schema": _state_schema(), "marketplaces": {}}


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
    """`marketplaces.json` como dict (vazio sem arquivo); conteúdo inválido vira `ValueError`.

    Arquivo que não dá para ler (sem permissão, disco) e pasta, FIFO ou dispositivo no lugar dele são
    `OSError` (IO_ERROR), sem travar num FIFO: o arquivo pode estar certo, e a recusa não manda apagá-lo."""
    path = state_path()
    if not _paths.strict_exists(path):
        return _empty_state()
    invalid = (
        f"marketplaces.json inválido em {labels.shown(path)}. Corrija ou apague o arquivo para recomeçar sem "
        "marketplaces."
    )
    try:
        data = json_read.read(path, max_bytes=STATE_MAX_BYTES)
    except json_read.JsonReadError as exc:
        if exc.reason == "not_regular":
            raise _refused(
                "state", "state_unreadable", OSError(errno.EINVAL, "não é um arquivo comum", str(path))
            ) from None
        raise _refused("state", "state_invalid", ValueError(invalid)) from None
    except OSError as exc:
        raise _refused("state", "state_unreadable", exc) from None
    if not isinstance(data, dict) or set(data) != {"schema", "marketplaces"}:
        raise _refused("state", "state_invalid", ValueError(invalid))
    versioning.read_schema(data, STATE_FAMILY, schemas.version(STATE_FAMILY), label=STATE_NAME, invalid=invalid)
    markets = data["marketplaces"]
    if (
        not isinstance(markets, dict)
        or not all(_valid_name(name) and _valid_record(record) for name, record in markets.items())
        or jsonschema.errors(data, schemas.load(STATE_FAMILY))
    ):
        raise _refused("state", "state_invalid", ValueError(invalid))
    return data


def _write_state(data):
    effects.mkdir(home_dir(), parents=True, exist_ok=True)
    ordered = {
        "schema": _state_schema(),
        "marketplaces": dict(sorted(data["marketplaces"].items())),
    }
    _atomic_write_bytes(state_path(), (json.dumps(ordered, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _atomic_write_bytes(path, data):
    """Grava `data` como está (temporário + troca), sem nenhuma tradução de fim de linha.
    O temporário tem nome único (`<nome>.<uuid>.tmp`): dois gravadores nunca dividem um."""
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    with effects.op("fs.write", path):
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
    """`--source`/`--ref`/`--commit` do `marketplace-add` como `GitSource`; pasta comum é recusada."""
    spec = git_source.parse_source(source, commit=commit, ref=ref)
    if not isinstance(spec, git_source.GitSource):
        raise _refused(
            "add",
            "source_not_git",
            ValueError(
                "--source de um marketplace tem que ser um repositório git (URL https://, git@host:caminho ou "
                "pasta local com .git); pasta comum não fixa commit."
            ),
        )
    return spec


def _recorded_spec(pin, commit):
    """A origem gravada de novo como `GitSource`, conferida como na hora do add."""
    if git_source.is_remote(pin.source):
        return git_source.GitSource(git_source.validate_url(pin.source), commit, pin.ref)
    folder = Path(pin.source)
    if not _paths.strict_is_dir(folder) or not git_source.is_repository(folder):
        raise _refused(
            "update",
            "origin_unavailable",
            ValueError(
                f"A origem do marketplace {pin.name} não é mais um repositório git acessível; "
                f"remova com plugins --action marketplace-remove --marketplace {pin.name} e adicione de novo."
            ),
            marketplace=pin.name,
        )
    return git_source.GitSource(str(folder), commit, pin.ref)


# (marketplace, posição, id, motivo) das entradas incompatíveis já avisadas neste processo.
_NOTED: set[tuple[str, int, str | None, str]] = set()


def incompatible_entry(index, plugin_id):
    """A linha de `incompatible` do índice para `plugin_id`, ou `None`."""
    return next((row for row in index.get("incompatible", []) if row["id"] == plugin_id), None)


def incompatible_message(name, plugin_id, row):
    """A frase de uma entrada que esta versão do getbrolls não consegue ler."""
    return (
        f"A entrada {plugin_id} do marketplace {name} não serve nesta versão do getbrolls "
        f"({row['reason']}); atualize o getbrolls ou fale com quem mantém o marketplace."
    )


def _note_incompatible(index):
    """`MARKETPLACE_ENTRY_INCOMPATIBLE` (uma vez por processo) para cada entrada que esta versão não lê."""
    for row in index.get("incompatible", []):
        key = (index["name"], row["position"], row["id"], row["reason"])
        if key in _NOTED:
            continue
        _NOTED.add(key)
        label = plain_line(row["id"] or f"#{row['position']}", limit=40)
        record_warning(
            "MARKETPLACE_ENTRY_INCOMPATIBLE",
            f"Marketplace {index['name']}: a entrada {label} ficou de fora, porque esta versão do getbrolls "
            f"não a entende ({row['reason']}).",
        )
    return index


def _fetch_index(spec):
    """`(commit, bytes, índice)` do `getbrolls-marketplace.json` no commit de `spec`."""
    commit, data = install.fetch_file(spec, INDEX_NAME, INDEX_MAX_BYTES)
    return commit, data, _note_incompatible(parse_index(data))


def _write_cache(name, data):
    folder = cache_path(name).parent
    if is_link(folder):
        raise _refused(
            "cache",
            "cache_link",
            ValueError(f"{labels.shown(folder)} é um link; recusado. Apague o link e rode de novo."),
            marketplace=name,
        )
    effects.mkdir(folder, parents=True, exist_ok=True)
    _atomic_write_bytes(cache_path(name), data)


def is_official(name: str, source: str | None) -> bool:
    """O marketplace `name`, fixado na origem `source`, é o índice oficial?"""
    return name == OFFICIAL_INDEX_NAME and source in OFFICIAL_INDEX_REPOS


def official_marketplace(name: str | None) -> bool:
    """`name` é um marketplace gravado aqui que é o índice oficial? Estado inválido ou ilegível vale `False`."""
    if not name:
        return False
    try:
        record = read_state()["marketplaces"].get(name)
    except (ValueError, OSError):
        return False
    return record is not None and is_official(name, record["source"])


def tier_view(tier: str | None, official: bool) -> dict:
    """`tier` como o índice declara, `tier_verified` (só no índice oficial) e, fora dele, o rótulo.

    `tier_label` é o que um resumo humano mostra: o tier sozinho só quando verificado;
    senão, com a ressalva ("official (declarado, não verificado)"). `tier` não muda.
    """
    verified = bool(official and tier is not None)
    return {
        "tier": tier,
        "tier_verified": verified,
        "tier_note": None if verified or tier is None else TIER_NOT_VERIFIED,
        "tier_label": None if tier is None else tier if verified else f"{tier} ({TIER_LABEL_SUFFIX})",
    }


def _summary(name, record, plugins=None):
    return {
        "name": name,
        **record,
        "allowed": allowed(name),
        "official": is_official(name, record["source"]),
        "plugins": plugins,
    }


def add(source: str, ref: str | None = None, commit: str | None = None) -> dict:
    """Adiciona o marketplace de `source` fixado no commit (o de `commit` ou o da `ref`)."""
    state = read_state()  # marketplaces.json corrompido recusa antes de qualquer rede ou mutação.
    spec = spec_for_add(source, ref, commit)
    if _ceiling() == frozenset():
        raise _refused(
            "add",
            "ceiling_empty",
            ValueError(f"O perfil de workspace não permite nenhum marketplace; {_profile_hint()}."),
        )
    pinned, data, index = _fetch_index(spec)
    name = index["name"]
    if name in state["marketplaces"]:
        raise _refused(
            "add",
            "already_exists",
            ValueError(
                f"O marketplace {name} já existe; use plugins --action marketplace-update --marketplace {name} "
                "(ou remova o antigo antes)."
            ),
            marketplace=name,
        )
    if not allowed(name):
        raise not_allowed(name, "add")
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
    official = is_official(name, record["source"])
    logs.event(
        _log,
        logging.INFO,
        "marketplace_added",
        marketplace=name,
        plugins=count,
        official=official,
        host=git_source.repo_host(spec.repo),
        commit=pinned[:12],
    )
    warnings = [] if official else [NOT_OFFICIAL_WARNING.format(name=OFFICIAL_INDEX_NAME, repo=OFFICIAL_INDEX_REPOS[0])]
    return {"added": True, "marketplace": _summary(name, record, count), "plugins": count, "warnings": warnings}


def _record(state, name):
    # Nome fora do formato é erro de uso (exit 2), como no `cache_path` do remove: o
    # `--marketplace` que a pessoa digitou está errado, não o conteúdo de nada.
    with usage_errors():
        try:
            validate_marketplace_name(name)
        except MarketplaceIndexError as exc:
            raise _refused("lookup", "invalid_name", ValueError(str(exc))) from None
    record = state["marketplaces"].get(name)
    if record is None:
        raise _refused(
            "lookup",
            "not_found",
            ValueError(f"Marketplace {name} não encontrado; veja plugins --action marketplace-list."),
            marketplace=name,
        )
    return record


def _read_cache(pin):
    path = cache_path(pin.name)
    stale = f"rode plugins --action marketplace-update --marketplace {pin.name}."
    if is_link(path) or is_link(path.parent) or not _paths.strict_is_file(path):
        raise _refused(
            "read",
            "cache_missing",
            ValueError(f"O índice em cache do marketplace {pin.name} sumiu ou não é um arquivo; {stale}"),
            marketplace=pin.name,
        )
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != pin.index_sha256:
        raise _refused(
            "read",
            "cache_changed",
            ValueError(
                f"O índice em cache do marketplace {pin.name} mudou desde o commit {pin.commit[:12]} "
                f"(sha256 não bate); {stale}"
            ),
            marketplace=pin.name,
        )
    return _note_incompatible(parse_index(data))


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


def readable_indexes() -> tuple[list[tuple[Pin, dict]], list[dict]]:
    """Como `pinned_indexes`, mas um cache que não confere não derruba os outros: vira
    `{marketplace, problem}` na segunda lista (como `problem` no `marketplace-list`)."""
    state = read_state()
    indexes, problems = [], []
    for name, record in sorted(state["marketplaces"].items()):
        pin = _pin(name, record)
        if not allowed(name):
            continue
        try:
            indexes.append((pin, _read_cache(pin)))
        except ValueError as exc:
            problems.append({"marketplace": name, "problem": str(exc)})
    return indexes, problems


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
    return {"state": labels.shown(state_path()), "marketplaces": rows}


def summary() -> list[dict]:
    """`[{name, commit, plugins, allowed, problem}]` em ordem de nome, sem rede e sem caminho
    da máquina (para `capabilities` e `doctor`). Levanta `ValueError` (estado inválido) ou `OSError`
    (estado que não dá para ler)."""
    rows = [
        {key: row[key] for key in ("name", "commit", "plugins", "allowed", "official", "problem")}
        for row in listing()["marketplaces"]
    ]
    return [{**row, "problem": None if row["problem"] is None else portable_text(row["problem"])} for row in rows]


def _installed_from(name):
    """Ids instalados que vieram do marketplace `name` (pela origem gravada no plugins.json)."""
    sources = loader.read_state().get("sources", {})
    return sorted(plugin_id for plugin_id, origin in sources.items() if origin.get("marketplace") == name)


def _delete_cache(folder):
    """Apaga a pasta de cache `folder` (ou o link no lugar dela). `None` quando ela saiu
    inteira; senão, a classe do erro (ou `"incomplete"`, quando sobrou algo sem erro).

    Uma pasta acima que não dá para ver (EACCES) não conta como "saiu": nem a remoção nem a
    conferência enxergam o cache, e o erro volta para quem chama relatar a sobra."""
    error = None
    try:
        if is_link(folder):
            effects.unlink(folder)
        elif _paths.strict_exists(folder):
            force_rmtree(folder)
    except OSError as exc:
        error = type(exc).__name__
    try:
        if not _paths.strict_lexists(folder):
            return None
    except OSError as exc:
        return error or type(exc).__name__
    return error or "incomplete"


def remove(name: str) -> dict:
    """Tira o marketplace `name` do estado e apaga o cache; plugins instalados ficam.

    Um cache que não sai inteiro não desfaz a remoção (o estado já foi gravado): a resposta
    traz `leftover` e a `note` diz o que apagar, e o log ganha `marketplace_cache_leftover`."""
    folder = cache_path(name).parent
    if is_link(folder.parent):
        # Apagar através de um link sairia de $GB_HOME: recusa antes de mexer em qualquer coisa.
        raise _refused(
            "remove",
            "cache_parent_link",
            ValueError(
                f"$GB_HOME/{CACHE_DIRNAME} é um link; o getbrolls não apaga nada através de links. "
                "Troque o link pela pasta de verdade e repita."
            ),
        )
    state = read_state()
    record = _record(state, name)
    del state["marketplaces"][name]
    # Estado primeiro: se ele não grava, o registro e o cache continuam juntos. Depois dele,
    # uma sobra de cache é lixo que nada mais lê, mas um `marketplace-add` com o mesmo nome
    # recusa um link no lugar dela: a resposta e o log dizem que sobrou.
    _write_state(state)
    error = _delete_cache(folder)
    note = REMOVE_NOTE
    if error is not None:
        logs.event(_log, logging.WARNING, "marketplace_cache_leftover", marketplace=name, error=error)
        note = (
            f"{REMOVE_NOTE} Sobrou {labels.shown(folder)} (o índice em cache), que não consegui apagar; apague à mão."
        )
    installed = _installed_from(name)
    logs.event(_log, logging.INFO, "marketplace_removed", marketplace=name, installed_plugins=len(installed))
    return {
        "removed": True,
        "marketplace": {"name": name, **record},
        "installed_plugins": installed,
        "leftover": None if error is None else f"{CACHE_DIRNAME}/{name}",
        "note": note,
    }


def _source_view(entry):
    """Commit e sha256 de uma entrada: o que muda quando o conteúdo muda sem mudar a versão."""
    return {"commit": entry["source"]["commit"], "content_sha256": entry["content_sha256"]}


def _diff(old, new):
    before = {entry["id"]: entry for entry in (old or {}).get("plugins", [])}
    after = {entry["id"]: entry for entry in new["plugins"]}
    updated = [
        {"id": plugin_id, "from": before[plugin_id]["version"], "to": after[plugin_id]["version"]}
        for plugin_id in sorted(set(before) & set(after))
        if (before[plugin_id]["version"], before[plugin_id]["content_sha256"])
        != (after[plugin_id]["version"], after[plugin_id]["content_sha256"])
    ]
    changed = [
        {
            "id": plugin_id,
            "from": _source_view(before[plugin_id]),
            "to": _source_view(after[plugin_id]),
        }
        for plugin_id in sorted(set(before) & set(after))
        if _source_view(before[plugin_id]) != _source_view(after[plugin_id])
    ]
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after) - {row["id"] for row in new.get("incompatible", [])}),
        "updated": updated,
        "changed": changed,
        "yanked": sorted(
            plugin_id
            for plugin_id, entry in after.items()
            if entry["yanked"] and not before.get(plugin_id, {}).get("yanked", False)
        ),
    }


def installed_pin(plugin_id: str, state: dict | None = None) -> dict | None:
    """O pin de `plugin_id` no plugins.json: o habilitado ou, depois de um `disable`, o último."""
    state = loader.read_state() if state is None else state
    return state.get("enabled", {}).get(plugin_id) or state.get("last_pins", {}).get(plugin_id)


def installed_content(plugin_id: str, state: dict | None = None) -> dict | None:
    """`{version, sha256}` do que está instalado, para "tem update?": o pin (`installed_pin`)
    ou, sem pin nenhum (plugins.json antigo ou editado à mão), a versão do manifesto e o sha256
    da pasta em `plugins/<id>`. `None` quando nem a pasta se lê."""
    pinned = installed_pin(plugin_id, state)
    if pinned is not None:
        return pinned
    folder = loader.plugins_root() / plugin_id
    try:
        version = loader.read_manifest(folder)["version"]
        return {"version": version, "sha256": loader.folder_digest(folder)}
    except (ValueError, OSError):
        return None


def has_update(installed: dict | None, entry: dict) -> bool:
    """A entrada do índice traz outro conteúdo que o instalado (`installed_content`)?

    A única resposta a "tem update?" (`marketplace-update`, `update --id` e `update --all`):
    decide o conteúdo, versão e `content_sha256` da entrada contra o instalado. Commit
    sozinho não é update (o mesmo conteúdo noutro commit não muda nada). Sem nada legível
    instalado não há como confirmar que é o mesmo conteúdo: conta como update."""
    if installed is None:
        return True
    return (installed["version"], installed["sha256"]) != (entry["version"], entry["content_sha256"])


def _installed_updates(name, index):
    """Ids instalados deste marketplace cuja entrada atual tem outro conteúdo (e não foi retirada)."""
    state = loader.read_state()
    entries = {entry["id"]: entry for entry in index["plugins"]}
    found = []
    for plugin_id in _installed_from(name):
        entry = entries.get(plugin_id)
        if entry is None or entry["yanked"]:
            continue
        if has_update(installed_content(plugin_id, state), entry):
            found.append(plugin_id)
    return found


def _refresh_one(state, name, commit, allow_rollback=False):
    record = _record(state, name)
    pin = _pin(name, record)
    try:
        with logs.handled():
            old = _read_cache(pin)
    except ValueError:
        old = None  # cache adulterado ou ausente: o update o repõe; o diff parte do vazio.
    spec = _recorded_spec(pin, commit)
    pinned, data, index = _fetch_index(spec)
    if index["name"] != name:
        raise _refused(
            "update",
            "name_changed",
            ValueError(
                f"O índice do marketplace {name} passou a se chamar {index['name']} no commit {pinned[:12]}; "
                "recusado. Remova e adicione de novo se a troca de nome for esperada."
            ),
            marketplace=name,
        )
    rollback = not git_source.descends_from(spec, record["commit"], pinned)
    if rollback and not allow_rollback:
        raise _refused(
            "update",
            "rollback",
            UsageError(
                f"O commit {pinned[:12]} do índice do marketplace {name} não descende do fixado hoje "
                f"({record['commit'][:12]}): é uma volta atrás ou a história foi reescrita. Se for isso mesmo, "
                "rode de novo com --allow-rollback."
            ),
            marketplace=name,
        )
    sha = hashlib.sha256(data).hexdigest()
    changed = (pinned, sha) != (record["commit"], record["index_sha256"]) or old is None
    if changed:
        _write_cache(name, data)
        state["marketplaces"][name] = {**record, "commit": pinned, "index_sha256": sha, "updated_at": _now()}
        _write_state(state)  # um por vez: o cache gravado e o estado andam juntos
    result = {
        "name": name,
        "allowed": True,
        "from": record["commit"],
        "to": pinned,
        "changed": changed,
        "rollback": rollback,
        "diff": _diff(old, index),
        "installed_updates": _installed_updates(name, index),
    }
    logs.event(
        _log,
        logging.INFO,
        "marketplace_updated",
        marketplace=name,
        from_commit=record["commit"][:12],
        to_commit=pinned[:12],
        changed=changed,
        rollback=rollback,
        added=len(result["diff"]["added"]),
        removed=len(result["diff"]["removed"]),
        updated=len(result["diff"]["updated"]),
        yanked=len(result["diff"]["yanked"]),
        installed_updates=len(result["installed_updates"]),
    )
    return result


def refresh(name: str | None = None, commit: str | None = None, allow_rollback: bool = False) -> dict:
    """Busca de novo a ref gravada (ou `commit`) de um marketplace ou de todos e move o pin.

    Devolve, por marketplace, `from`/`to` (commits), `changed`, `rollback`, o `diff` do
    índice (`added`, `removed`, `updated` [{id, from, to}], `changed` [{id, from, to} com
    commit e sha256 de cada entrada cuja origem mudou], `yanked`) e `installed_updates`
    (plugins instalados dele que têm conteúdo novo). Um commit que não descende do fixado
    (volta atrás, história reescrita) só passa com `allow_rollback`. Sem `name`, os que o
    teto não permite ficam de fora (`skipped: true`) e o erro de um marketplace não para os
    outros: ele sai em `error` na linha dele e o nome em `failed`. Se todos os permitidos
    falharem, é `ValueError` com o erro de cada um (o comando sai com 1)."""
    if commit is not None and name is None:
        raise _refused(
            "update",
            "commit_needs_marketplace",
            UsageError("--commit em marketplace-update precisa de --marketplace NOME."),
        )
    if commit is not None:
        with usage_errors():  # o `git_source_refused` sai com o USAGE_ERROR que a CLI devolve
            git_source.validate_commit(commit)
    state = read_state()
    if name is not None:
        _record(state, name)
        if not allowed(name):
            raise not_allowed(name, "update")
        return {"marketplaces": [_refresh_one(state, name, commit, allow_rollback)], "failed": []}
    results, failed = [], []
    for current in sorted(state["marketplaces"]):
        if not allowed(current):
            results.append({"name": current, "allowed": False, "skipped": True})
            continue
        try:
            with logs.handled():
                results.append(_refresh_one(state, current, None, allow_rollback))
        except (ValueError, OSError) as exc:
            # Um disco que não grava o cache de um marketplace também não para os outros.
            failed.append(current)
            logs.event(
                _log, logging.WARNING, "marketplace_update_failed", marketplace=current, error=type(exc).__name__
            )
            error = labels.os_message(exc) if isinstance(exc, OSError) else str(exc)
            results.append({"name": current, "allowed": True, "changed": False, "error": error})
    if failed and len(failed) == sum(1 for row in results if row["allowed"]):
        # Nenhum dos permitidos atualizou: o comando falhou, não é sucesso parcial.
        errors = "; ".join(f"{row['name']}: {row['error']}" for row in results if "error" in row)
        raise _refused(
            "update",
            "all_failed",
            ValueError(f"Nenhum marketplace foi atualizado. {errors}"),
            failed=len(failed),
        )
    return {"marketplaces": results, "failed": failed}


def resolve_repo(entry_repo: str, pin: Pin) -> str:
    """`source.repo` de uma entrada como origem git: `"."` é o próprio repositório do marketplace."""
    return pin.source if entry_repo == SELF_REPO else entry_repo


def _installed():
    """`{id: {"version", "marketplace"}}` dos plugins com pasta em `plugins/`, pelo plugins.json (sem rodar nada)."""
    state = loader.read_state()
    root = loader.plugins_root()
    found = {}
    for plugin_id in {*state.get("enabled", {}), *state.get("last_pins", {}), *state.get("sources", {})}:
        if not _paths.strict_is_dir(root / plugin_id):
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
        **tier_view(entry["tier"], is_official(pin.name, pin.source)),
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
        **tier_view(None, False),
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
    `renames`, `renamed_to` com o id atual. Sem `marketplace`, um cache que não confere
    fica de fora com o motivo em `problems`; os outros continuam."""
    needle = query.strip().casefold() if isinstance(query, str) else ""
    if not needle:
        raise _refused(
            "search",
            "empty_query",
            UsageError("--query é obrigatório em plugins --action search (um trecho do id ou da descrição)."),
        )
    problems = []
    if marketplace is not None:
        pin, index = load_index(marketplace)
        if not allowed(pin.name):
            raise not_allowed(pin.name, "search")
        indexes = [(pin, index)]
    else:
        indexes, problems = readable_indexes()
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
    return {
        "query": query,
        "marketplaces": [pin.name for pin, _ in indexes],
        "results": results,
        "problems": problems,
    }
