"""Pastas de cliente: `<raiz>/<cliente>/client.json` e o registro `$GB_HOME/clients.json`.

A pessoa escolhe onde mora a pasta de cada cliente; o registro só guarda o caminho. A
pasta traz `client.json`, `assets/<tipo>/` (a prateleira que o cliente reusa entre
projetos), `recipes/` (as receitas seladas) e `projetos/` (onde o `init --client --slug` cria os
projetos dele).
Desregistrar nunca apaga arquivo.

Segurança da raiz: caminho absoluto, pasta existente, nunca link (nem a pasta do
cliente, nem as subpastas que o `add` cria), nunca ampla demais (raiz de disco, pasta
pessoal ou uma que a contém), nunca dentro da instalação do getbrolls nem das pastas
de plugin e runtime de `$GB_HOME`, e nunca uma pasta que contém o próprio `$GB_HOME`.

O registro é gravado de uma vez (temporário + troca) sob `$GB_HOME/.clients.lock`. Na
saída, a pasta pessoal aparece como `~`.
"""

import json
import logging
import os
import unicodedata
from pathlib import Path

from . import _paths, effects, labels, logs, runtime, versioning
from .errors import UsageError
from .layout import ASSET_FOLDERS
from .ledger import atomic_write
from .models import now
from .roteiro_frontmatter import SLUG_MAX, SLUG_RE
from .sdk import jsonschema, schemas
from .sdk.files import _too_broad, is_link

_log = logs.get("clients")

REGISTRY = "clients.json"
LOCK = ".clients.lock"
CLIENT_FILE = "client.json"
ASSETS = "assets"
PROJECTS = "projetos"
RECIPES = "recipes"
# As pastas de `assets/` de um projeto, na mesma grafia: `assets/<tipo>/` também na pasta do cliente.
COMPONENT_FOLDERS = ASSET_FOLDERS
# Pastas da versão de testes da 2.6 que o código não lê: o `migrate --client` converte.
OLD_FOLDERS = ("components", "templates")
# O que fazer à mão com cada pasta antiga: o `migrate` só adota o layout 1 e não move pastas.
_OLD_FOLDER_FIX = {
    "components": (
        "Mova cada pasta de `components/<tipo>/` para `assets/<tipo>/` dentro da pasta do cliente "
        "e remova a `components/` vazia."
    ),
    "templates": (
        "Ela não tem equivalente direto: as receitas ficam em `recipes/` e são seladas por um passo que esta "
        "versão da CLI ainda não tem (hoje só existem recipe --action list e recipe --action show). "
        "Deixe a `templates/` onde está ou guarde-a fora da pasta do cliente."
    ),
}
# Nunca são slug de cliente: `pessoal` é o dono das receitas pessoais nas refs (`pessoal.<slug>`),
# `personal` é o nível pessoal e `core` o dono dos embutidos.
RESERVED_SLUGS = ("pessoal", "personal", "core")
CLIENT_SCHEMA = "client"
REGISTRY_SCHEMA = "clients"
NAME_MAX = 120
LOCK_WAIT_S = 5.0
_CLIENT_FILE_MAX = 1024 * 1024
_REGISTRY_MAX = 16 * 1024 * 1024
# Pastas de `$GB_HOME` onde uma pasta de cliente nunca pode morar.
_HOME_RESERVED = ("plugins", "plugin-data", "runtime")
_BUSY = "Outro comando está mudando o registro de clientes. Aguarde terminar antes de repetir."


def _refused(action, reason, exc):
    """Registra a recusa (`client_refused`: ação, motivo e `error_code`) e devolve `exc` para levantar.

    Nunca o slug, o nome nem a pasta do cliente: o nome de um cliente é dado do cliente.
    """
    return logs.refusal(_log, "client_refused", exc, action=action, reason=reason)


def _schema_tag(name):
    """O valor do campo `schema` da família `name` (`client` ou `clients`), com a versão do schema."""
    return versioning.schema_name(name, schemas.version(name))


def _schema_problems(doc, name):
    """O que `doc` (com o padrão do campo ausente já aplicado) tem fora do schema publicado de `name`."""
    return jsonschema.errors(doc, schemas.load(name))


def registry_path() -> Path:
    """`$GB_HOME/clients.json`."""
    return _paths.gb_home() / REGISTRY


def _shown(path):
    """Caminho que o getbrolls tirou do registro ou montou, para a saída: a marca da pasta
    (`$GB_HOME/…`, `~/…`, `<pasta fora do projeto>/…`; `labels.shown`), nunca o absoluto.

    O `--root` que a pessoa digitou no `add` volta como ela digitou: esse não passa por aqui."""
    return labels.shown(path)


def _inside(child, parent):
    """`child` é `parent` ou fica dentro dele? Comparação textual de caminhos já resolvidos."""
    c, p = os.path.normcase(str(child)), os.path.normcase(str(parent))
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def check_slug(slug):
    """`slug` de cliente válido (a regra de `cliente` do roteiro); `ValueError` senão."""
    if not isinstance(slug, str) or len(slug) > SLUG_MAX or not SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"O slug do cliente tem que ter minúsculas, números e -, até {SLUG_MAX} caracteres (ex.: acme-corp)."
        )
    return slug


def _check_name(name):
    text = unicodedata.normalize("NFC", name).strip() if isinstance(name, str) else ""
    if not text or len(text) > NAME_MAX or any(unicodedata.category(char) == "Cc" for char in text):
        raise ValueError(f"--name tem que ser texto de uma linha, de 1 a {NAME_MAX} caracteres.")
    return text


def _invalid_registry(path, reason):
    return _refused(
        "registry",
        "registry_invalid",
        ValueError(
            f"clients.json inválido em {_shown(path)}: {reason}. "
            "Corrija o arquivo ou restaure uma cópia; nenhum cliente foi alterado."
        ),
    )


def _valid_entry(row):
    return (
        isinstance(row, dict)
        and isinstance(row.get("slug"), str)
        and len(row["slug"]) <= SLUG_MAX
        and SLUG_RE.fullmatch(row["slug"]) is not None
        and isinstance(row.get("root"), str)
        and Path(row["root"]).is_absolute()
        and isinstance(row.get("root_typed", ""), str)
        and isinstance(row.get("added"), str)
    )


def load_registry() -> dict:
    """O registro; sem o arquivo, `{"schema": "getbrolls.clients/1", "clients": []}`."""
    path = registry_path()
    if not _paths.strict_exists(path):
        return {"schema": _schema_tag(REGISTRY_SCHEMA), "clients": []}
    try:
        if path.stat().st_size > _REGISTRY_MAX:
            raise _invalid_registry(path, "arquivo grande demais")
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid_registry(path, "não é um JSON legível") from None
    if not isinstance(data, dict):
        raise _invalid_registry(path, "o topo tem que ser um objeto")
    versioning.read_schema(
        data,
        REGISTRY_SCHEMA,
        schemas.version(REGISTRY_SCHEMA),
        label="clients.json",
        invalid=lambda: _invalid_registry(path, f"schema tem que ser {_schema_tag(REGISTRY_SCHEMA)}"),
    )
    rows = data.get("clients")
    if not isinstance(rows, list) or not all(_valid_entry(row) for row in rows):
        raise _invalid_registry(path, "clients tem que ser uma lista de {slug, root, added}")
    slugs = [row["slug"] for row in rows]
    if len(set(slugs)) != len(slugs):
        raise _invalid_registry(path, "slug repetido")
    registry = {"schema": _schema_tag(REGISTRY_SCHEMA), "clients": rows}
    problems = _schema_problems(registry, REGISTRY_SCHEMA)
    if problems:
        raise _invalid_registry(path, "fora do schema (" + "; ".join(problems[:3]) + ")")
    return registry


def _write_registry(rows):
    data = {"schema": _schema_tag(REGISTRY_SCHEMA), "clients": sorted(rows, key=lambda row: row["slug"])}
    atomic_write(registry_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _locked():
    home = _paths.gb_home()
    effects.mkdir(home, parents=True, exist_ok=True)
    return runtime.exclusive_lock(home / LOCK, _BUSY, wait_s=LOCK_WAIT_S)


def _entry(slug):
    return next((row for row in load_registry()["clients"] if row["slug"] == slug), None)


def client_root(slug) -> Path | None:
    """Pasta registrada do cliente `slug`, como gravada; `None` quando ele não está registrado."""
    entry = _entry(check_slug(slug))
    return Path(entry["root"]) if entry else None


def _bad_client_file(exc):
    return _refused("load", "client_file_invalid", exc)


def _read_client_file(folder, slug, shown=None):
    """`client.json` de `folder`, conferido; `ValueError` com o motivo. `shown` é a pasta como a
    mensagem a nomeia (a digitada, no `add`); sem ele, a marca de `_shown`."""
    path = folder / CLIENT_FILE
    label = f"{CLIENT_FILE} do cliente {slug}"
    if is_link(path) or not _paths.strict_is_file(path):
        where = shown if shown is not None else _shown(folder)
        raise _bad_client_file(ValueError(f"{label} não é um arquivo comum em {where}."))
    try:
        if path.stat().st_size > _CLIENT_FILE_MAX:
            raise ValueError(f"{label} é grande demais.")
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise _bad_client_file(
            ValueError(f"{label} não é um JSON legível: corrija o arquivo ou restaure uma cópia.")
        ) from None
    if not isinstance(data, dict):
        raise _bad_client_file(ValueError(f"{label} tem que ser um objeto JSON."))
    version = versioning.read_schema(
        data,
        CLIENT_SCHEMA,
        schemas.version(CLIENT_SCHEMA),
        label=label,
        invalid=f"{label}: schema tem que ser {_schema_tag(CLIENT_SCHEMA)} (veio {data.get('schema')!r}).",
    )
    if data.get("slug") != slug:
        raise _bad_client_file(ValueError(f'{label} é do cliente "{data.get("slug")}", não de "{slug}".'))
    if not isinstance(data.get("name"), str):
        raise _bad_client_file(ValueError(f"{label} precisa de name em texto."))
    current = dict(data)  # em memória, o padrão do campo ausente; o arquivo não muda
    current.setdefault(versioning.SCHEMA_FIELD, versioning.schema_name(CLIENT_SCHEMA, version))
    problems = _schema_problems(current, CLIENT_SCHEMA)
    if problems:
        raise _bad_client_file(ValueError(f"{label} fora do schema: " + "; ".join(problems[:3]) + "."))
    return data


def _checked_folder(folder, slug):
    """A pasta registrada ainda serve: existe, é pasta e não é link."""
    if is_link(folder):
        raise _refused(
            "load",
            "folder_link",
            ValueError(f"A pasta do cliente {slug} ({_shown(folder)}) virou link; o getbrolls não segue links ali."),
        )
    if not _paths.strict_is_dir(folder):
        raise _refused(
            "load",
            "folder_missing",
            ValueError(
                f"A pasta do cliente {slug} ({_shown(folder)}) não existe mais. Restaure a pasta ou rode "
                f"client --action remove --slug {slug}."
            ),
        )
    return folder


def _registered(slug):
    entry = _entry(check_slug(slug))
    if entry is None:
        raise _refused(
            "load",
            "not_registered",
            ValueError(f'O cliente "{slug}" não está registrado. Rode client --action add antes.'),
        )
    return entry


def _load_entry(entry):
    slug = entry["slug"]
    return _read_client_file(_checked_folder(Path(entry["root"]), slug), slug)


def load_client(slug) -> dict:
    """`client.json` do cliente registrado `slug`; `ValueError` quando ele não serve."""
    return _load_entry(_registered(slug))


def _typed_form(value) -> bool:
    """`value` serve de `root_typed`: um caminho absoluto ou que começa com `~`."""
    return isinstance(value, str) and bool(value) and (value.startswith("~") or Path(value).is_absolute())


def _checked_typed(value) -> str:
    """`value` para gravar em `root_typed`; `ValueError` se não for absoluto nem começar com `~`."""
    if not _typed_form(value):
        raise ValueError("A pasta do cliente como digitada precisa ser absoluta ou começar com ~.")
    return value


def _typed_root(entry):
    """A pasta como a pessoa a registrou: o `--root` digitado no `add` (mais `/<cliente>`), guardado em
    `root_typed`; um registro de antes dele (ou com um `root_typed` que não serve) só tem o `root` resolvido,
    que é então o que ela registrou."""
    typed = entry.get("root_typed")
    return typed if _typed_form(typed) else entry["root"]


def _moved(entry):
    """O problema de um `root_typed` que hoje leva a outra pasta (um link no caminho trocou de alvo); `None`
    quando ele ainda leva à pasta registrada, ou quando não há `root_typed`."""
    typed = entry.get("root_typed")
    if not _typed_form(typed):
        return None
    try:
        current = Path(os.path.realpath(Path(typed).expanduser()))
        registered = Path(os.path.realpath(entry["root"]))
    except (OSError, RuntimeError, ValueError):
        return None
    if current == registered:
        return None
    return (
        f"A pasta digitada no add agora leva a outra pasta ({_shown(current)}); o cliente continua registrado em "
        f"{_shown(entry['root'])}. Confira o link no caminho ou registre o cliente de novo."
    )


def _view(entry, client=None, problem=None, root=None):
    """Um cliente na saída; `root` é a pasta como a pessoa a digitou (`_typed_root`), senão a marca dela."""
    return {
        "slug": entry["slug"],
        "name": client.get("name") if client else None,
        "root": root if root is not None else _shown(entry["root"]),
        "added": entry["added"],
        "problem": problem or None,
    }


def _describe(entry):
    try:
        client = _load_entry(entry)
    except ValueError as exc:
        return _view(entry, problem=str(exc), root=_typed_root(entry))
    except OSError as exc:
        problem = f"A pasta do cliente {entry['slug']} não pôde ser lida ({labels.reason(exc)})."
        return _view(entry, problem=problem, root=_typed_root(entry))
    return _view(entry, client, problem=_moved(entry), root=_typed_root(entry))


def _checked_base(root):
    """A raiz escolhida pela pessoa, resolvida; `ValueError` quando ela não pode receber um cliente."""
    if not isinstance(root, str) or not root or not Path(root).is_absolute():
        raise _refused(
            "add",
            "root_not_absolute",
            ValueError("--root tem que ser um caminho absoluto de uma pasta existente (sem ~)."),
        )
    base = Path(root)
    if is_link(base):
        raise _refused("add", "root_link", ValueError("--root é um link; indique a pasta de verdade."))
    if not _paths.strict_is_dir(base):
        raise _refused("add", "root_missing", ValueError("--root tem que ser uma pasta existente."))
    resolved = base.resolve()
    if _too_broad(resolved):
        raise _refused(
            "add",
            "root_too_broad",
            ValueError(
                "--root é ampla demais (raiz de disco, pasta pessoal ou uma que a contém); escolha uma subpasta."
            ),
        )
    return resolved


def _check_placement(folder):
    inst = _paths.install()
    home = _paths.gb_home()
    reserved = [inst.package_dir, inst.checkout_root, inst.data_root, *(home / name for name in _HOME_RESERVED)]
    for place in reserved:
        if place is not None and _inside(folder, Path(place).resolve()):
            raise _refused(
                "add",
                "inside_installation",
                ValueError("A pasta do cliente não pode ficar dentro da instalação do getbrolls nem dos plugins."),
            )
    if _inside(home.resolve(), folder):
        raise _refused(
            "add",
            "contains_home",
            ValueError("A pasta do cliente não pode conter a pasta do getbrolls ($GB_HOME)."),
        )


def _ensure_dir(path, shown):
    """Cria `path` se faltar; recusa link e arquivo no lugar. `shown` é `path` como a pessoa o digitou."""
    if is_link(path):
        raise _refused(
            "add", "folder_link", ValueError(f"{shown} é um link; o getbrolls não cria nada através de links.")
        )
    if _paths.strict_exists(path) and not _paths.strict_is_dir(path):
        raise _refused("add", "folder_not_dir", ValueError(f"{shown} existe e não é uma pasta."))
    effects.mkdir(path, exist_ok=True)


def _prepare_folder(folder, slug, name, typed):
    """Cria (ou confere) a pasta do cliente; devolve `(client.json, criado agora?)`.

    `typed` é `folder` na grafia do `--root` digitado: as mensagens nomeiam as pastas assim."""
    if is_link(folder):
        raise _refused(
            "add",
            "folder_link",
            ValueError(f"{_shown(typed)} é um link; o getbrolls não cria nada através de links."),
        )
    if _paths.strict_exists(folder) and not _paths.strict_is_dir(folder):
        raise _refused("add", "folder_not_dir", ValueError(f"{_shown(typed)} existe e não é uma pasta."))
    existing = _paths.strict_is_dir(folder) and _paths.strict_lexists(folder / CLIENT_FILE)
    client = _read_client_file(folder, slug, shown=str(typed)) if existing else None
    subfolders = [(ASSETS,), (PROJECTS,), (RECIPES,), *((ASSETS, sub) for sub in COMPONENT_FOLDERS)]
    for parts in subfolders:
        if is_link(folder.joinpath(*parts)):
            raise _refused(
                "add",
                "subfolder_link",
                ValueError(f"{_shown(typed.joinpath(*parts))} é um link; o getbrolls não cria nada através de links."),
            )
    _ensure_dir(folder, typed)
    for parts in ((ASSETS,), *((ASSETS, sub) for sub in COMPONENT_FOLDERS), (PROJECTS,), (RECIPES,)):
        _ensure_dir(folder.joinpath(*parts), typed.joinpath(*parts))
    if client is not None:
        return client, False
    client = {
        "schema": _schema_tag(CLIENT_SCHEMA),
        "slug": slug,
        "name": name or slug,
        "created": now(),
        "defaults": {},
    }
    atomic_write(folder / CLIENT_FILE, json.dumps(client, ensure_ascii=False, indent=2) + "\n")
    return client, True


def add(slug, root, name=None) -> dict:
    """Cria (ou reaproveita) `<root>/<slug>/` e registra o cliente em `$GB_HOME/clients.json`."""
    check_slug(slug)
    if slug in RESERVED_SLUGS:
        raise _refused(
            "add",
            "reserved_slug",
            ValueError(f'"{slug}" é reservado do getbrolls ({", ".join(RESERVED_SLUGS)}): escolha outro slug.'),
        )
    name = None if name is None else _check_name(name)
    folder = _checked_base(root) / slug
    typed = Path(root) / slug
    _check_placement(folder)
    load_registry()  # registro ilegível ou mais novo: recusa antes de criar qualquer pasta
    with _locked():
        rows = load_registry()["clients"]
        taken = next((row for row in rows if row["slug"] == slug), None)
        if taken:
            raise _refused(
                "add",
                "already_registered",
                ValueError(
                    f'O cliente "{slug}" já está registrado em {_shown(taken["root"])}. '
                    "Rode client --action remove antes."
                ),
            )
        client, created = _prepare_folder(folder, slug, name, typed)
        # `root` resolvido (o que a máquina abre); `root_typed`, como a pessoa o digitou (o que a saída devolve).
        entry = {"slug": slug, "root": str(folder), "root_typed": _checked_typed(str(typed)), "added": now()}
        _write_registry([*rows, entry])
    warn_old_shape(slug, folder)
    logs.event(_log, logging.INFO, "client_added", created=created, clients=len(rows) + 1)
    return {"client": _view(entry, client, root=str(typed)), "created": created, "registry": _shown(registry_path())}


def remove(slug) -> dict:
    """Tira `slug` do registro; a pasta do cliente e os arquivos dela ficam."""
    check_slug(slug)
    with _locked():
        rows = load_registry()["clients"]
        entry = next((row for row in rows if row["slug"] == slug), None)
        if entry is None:
            raise _refused("remove", "not_registered", ValueError(f'O cliente "{slug}" não está registrado.'))
        _write_registry([row for row in rows if row["slug"] != slug])
    logs.event(_log, logging.INFO, "client_removed", clients=len(rows) - 1)
    return {
        "removed": _view(entry),
        "files_kept": True,
        "message": f"Cliente {slug} desregistrado; a pasta dele não foi apagada.",
    }


def warn_old_shape(slug, root):
    """`OLD_SHAPE` (uma vez por processo) para cada pasta da versão de testes ainda na pasta do cliente."""
    from . import renamed  # tardio: renamed importa _paths e logs, como este módulo

    for name in OLD_FOLDERS:
        path = Path(root) / name
        try:
            old = is_link(path) or _paths.strict_is_dir(path)
        except OSError:
            continue  # a locked client folder: loading the client reports it; the hint must never fail a list
        if old:
            renamed.old_shape(f"{name}/ do cliente {slug}", _OLD_FOLDER_FIX[name], where=str(root))


def list_clients() -> dict:
    """Todos os clientes registrados, com o problema de cada pasta que não serve mais."""
    rows = load_registry()["clients"]
    for row in rows:
        warn_old_shape(row["slug"], Path(row["root"]))
    return {"registry": _shown(registry_path()), "clients": [_describe(row) for row in rows]}


def show(slug) -> dict:
    """Um cliente registrado, com o `client.json` conferido."""
    entry = _registered(slug)
    client = _load_entry(entry)
    warn_old_shape(entry["slug"], Path(entry["root"]))
    return {
        "client": {
            **_view(entry, client, problem=_moved(entry), root=_typed_root(entry)),
            "created": client.get("created"),
            "defaults": client.get("defaults", {}),
        }
    }


def _required(args, flag):
    # lenient: dynamic getattr over argparse flag names, not an existence check on a path
    value = getattr(args, flag)
    if not value:
        raise UsageError(f"--{flag} é obrigatório em client --action {args.action}.")
    return value


def run(args):
    """`client --action add|list|show|remove`."""
    if args.action == "list":
        return list_clients()
    if args.action == "add":
        return add(_required(args, "slug"), _required(args, "root"), name=args.name)
    if args.action == "show":
        return show(_required(args, "slug"))
    return remove(_required(args, "slug"))
