"""Pastas de cliente: `<raiz>/<cliente>/client.json` e o registro `$GB_HOME/clients.json`.

A pessoa escolhe onde mora a pasta de cada cliente; o registro só guarda o caminho. A
pasta traz `client.json`, `components/<tipo>/` (os componentes que o cliente reusa entre
projetos) e `templates/`. Desregistrar nunca apaga arquivo.

Segurança da raiz: caminho absoluto, pasta existente, nunca link (nem a pasta do
cliente, nem as subpastas que o `add` cria), nunca ampla demais (raiz de disco, pasta
pessoal ou uma que a contém), nunca dentro da instalação do get-brolls nem das pastas
de plugin e runtime de `$GB_HOME`, e nunca uma pasta que contém o próprio `$GB_HOME`.

O registro é gravado de uma vez (temporário + troca) sob `$GB_HOME/.clients.lock`. Na
saída, a pasta pessoal aparece como `~`.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

from . import _paths, runtime, versioning
from .errors import UsageError
from .layout import ASSET_FOLDERS
from .ledger import atomic_write
from .models import now
from .roteiro_frontmatter import SLUG_MAX, SLUG_RE
from .sdk.files import _too_broad, is_link

REGISTRY = "clients.json"
LOCK = ".clients.lock"
CLIENT_FILE = "client.json"
COMPONENTS = "components"
TEMPLATES = "templates"
# As pastas de `assets/` de um projeto, na mesma grafia: `components/<tipo>/`.
COMPONENT_FOLDERS = ASSET_FOLDERS
CLIENT_SCHEMA = "client"
REGISTRY_SCHEMA = "clients"
SUPPORTED = 1
NAME_MAX = 120
LOCK_WAIT_S = 5.0
_CLIENT_FILE_MAX = 1024 * 1024
_REGISTRY_MAX = 16 * 1024 * 1024
# Pastas de `$GB_HOME` onde uma pasta de cliente nunca pode morar.
_HOME_RESERVED = ("plugins", "plugin-data", "runtime")
_BUSY = "Outro comando está mudando o registro de clientes. Aguarde terminar antes de repetir."
_SCHEMA_RE = re.compile(r"getbrolls\.([a-z][a-z0-9_]*)/([1-9][0-9]{0,8})")


def _schema_tag(name, version=SUPPORTED):
    return versioning.schema_name(name, version)


def _schema_version(data, name, label):
    """Versão de `"schema": "getbrolls.<name>/<n>"` (ausente = 1); `ValueError` se não der para ler."""
    if "schema" not in data:
        return 1
    value = data["schema"]
    match = _SCHEMA_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None or match.group(1) != name:
        raise ValueError(f"{label}: schema tem que ser {_schema_tag(name)} (veio {value!r}).")
    version = int(match.group(2))
    if version > SUPPORTED:
        raise ValueError(
            f"{label} foi gravado por uma versão mais nova do get-brolls (schema {value}); atualize antes de continuar."
        )
    return version


def registry_path() -> Path:
    """`$GB_HOME/clients.json`."""
    return _paths.gb_home() / REGISTRY


def _shown(path):
    """Caminho para a saída, com a pasta pessoal (também a resolvida) trocada por `~`."""
    text = str(path)
    try:
        home = str(Path.home().resolve())
    except (RuntimeError, OSError):
        home = ""
    if home not in ("", "/", "\\") and (text == home or text.startswith(home.rstrip(os.sep) + os.sep)):
        return "~" + text[len(home) :]
    return runtime.scrub_home(text)


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
    return ValueError(
        f"clients.json inválido em {path}: {reason}. "
        "Corrija o arquivo ou restaure uma cópia; nenhum cliente foi alterado."
    )


def _valid_entry(row):
    return (
        isinstance(row, dict)
        and isinstance(row.get("slug"), str)
        and len(row["slug"]) <= SLUG_MAX
        and SLUG_RE.fullmatch(row["slug"]) is not None
        and isinstance(row.get("root"), str)
        and Path(row["root"]).is_absolute()
        and isinstance(row.get("added"), str)
    )


def load_registry() -> dict:
    """O registro; sem o arquivo, `{"schema": "getbrolls.clients/1", "clients": []}`."""
    path = registry_path()
    if not path.exists():
        return {"schema": _schema_tag(REGISTRY_SCHEMA), "clients": []}
    try:
        if path.stat().st_size > _REGISTRY_MAX:
            raise _invalid_registry(path, "arquivo grande demais")
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid_registry(path, "não é um JSON legível") from None
    if not isinstance(data, dict):
        raise _invalid_registry(path, "o topo tem que ser um objeto")
    try:
        _schema_version(data, REGISTRY_SCHEMA, "clients.json")
    except ValueError as exc:
        if "versão mais nova" in str(exc):
            raise
        raise _invalid_registry(path, f"schema tem que ser {_schema_tag(REGISTRY_SCHEMA)}") from None
    rows = data.get("clients")
    if not isinstance(rows, list) or not all(_valid_entry(row) for row in rows):
        raise _invalid_registry(path, "clients tem que ser uma lista de {slug, root, added}")
    slugs = [row["slug"] for row in rows]
    if len(set(slugs)) != len(slugs):
        raise _invalid_registry(path, "slug repetido")
    return {"schema": _schema_tag(REGISTRY_SCHEMA), "clients": rows}


def _write_registry(rows):
    data = {"schema": _schema_tag(REGISTRY_SCHEMA), "clients": sorted(rows, key=lambda row: row["slug"])}
    atomic_write(registry_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _locked():
    home = _paths.gb_home()
    home.mkdir(parents=True, exist_ok=True)
    return runtime.exclusive_lock(home / LOCK, _BUSY, wait_s=LOCK_WAIT_S)


def _entry(slug):
    return next((row for row in load_registry()["clients"] if row["slug"] == slug), None)


def client_root(slug) -> Path | None:
    """Pasta registrada do cliente `slug`, como gravada; `None` quando ele não está registrado."""
    entry = _entry(check_slug(slug))
    return Path(entry["root"]) if entry else None


def _read_client_file(folder, slug):
    """`client.json` de `folder`, conferido; `ValueError` com o motivo."""
    path = folder / CLIENT_FILE
    label = f"{CLIENT_FILE} do cliente {slug}"
    if is_link(path) or not path.is_file():
        raise ValueError(f"{label} não é um arquivo comum em {_shown(folder)}.")
    try:
        if path.stat().st_size > _CLIENT_FILE_MAX:
            raise ValueError(f"{label} é grande demais.")
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"{label} não é um JSON legível: corrija o arquivo ou restaure uma cópia.") from None
    if not isinstance(data, dict):
        raise ValueError(f"{label} tem que ser um objeto JSON.")
    _schema_version(data, CLIENT_SCHEMA, label)
    if data.get("slug") != slug:
        raise ValueError(f'{label} é do cliente "{data.get("slug")}", não de "{slug}".')
    if not isinstance(data.get("name"), str):
        raise ValueError(f"{label} precisa de name em texto.")
    return data


def _checked_folder(folder, slug):
    """A pasta registrada ainda serve: existe, é pasta e não é link."""
    if is_link(folder):
        raise ValueError(f"A pasta do cliente {slug} ({_shown(folder)}) virou link; o get-brolls não segue links ali.")
    if not folder.is_dir():
        raise ValueError(
            f"A pasta do cliente {slug} ({_shown(folder)}) não existe mais. Restaure a pasta ou rode "
            f"client --action remove --slug {slug}."
        )
    return folder


def _registered(slug):
    entry = _entry(check_slug(slug))
    if entry is None:
        raise ValueError(f'O cliente "{slug}" não está registrado. Rode client --action add antes.')
    return entry


def _load_entry(entry):
    slug = entry["slug"]
    return _read_client_file(_checked_folder(Path(entry["root"]), slug), slug)


def load_client(slug) -> dict:
    """`client.json` do cliente registrado `slug`; `ValueError` quando ele não serve."""
    return _load_entry(_registered(slug))


def _view(entry, client=None, problem=None):
    return {
        "slug": entry["slug"],
        "name": client.get("name") if client else None,
        "root": _shown(entry["root"]),
        "added": entry["added"],
        "problem": runtime.scrub_home(problem) if problem else None,
    }


def _describe(entry):
    try:
        client = _load_entry(entry)
    except ValueError as exc:
        return _view(entry, problem=str(exc))
    return _view(entry, client)


def _checked_base(root):
    """A raiz escolhida pela pessoa, resolvida; `ValueError` quando ela não pode receber um cliente."""
    if not isinstance(root, str) or not root or not Path(root).is_absolute():
        raise ValueError("--root tem que ser um caminho absoluto de uma pasta existente (sem ~).")
    base = Path(root)
    if is_link(base):
        raise ValueError("--root é um link; indique a pasta de verdade.")
    if not base.is_dir():
        raise ValueError("--root tem que ser uma pasta existente.")
    resolved = base.resolve()
    if _too_broad(resolved):
        raise ValueError(
            "--root é ampla demais (raiz de disco, pasta pessoal ou uma que a contém); escolha uma subpasta."
        )
    return resolved


def _check_placement(folder):
    inst = _paths.install()
    home = _paths.gb_home()
    reserved = [inst.package_dir, inst.checkout_root, inst.data_root, *(home / name for name in _HOME_RESERVED)]
    for place in reserved:
        if place is not None and _inside(folder, Path(place).resolve()):
            raise ValueError("A pasta do cliente não pode ficar dentro da instalação do get-brolls nem dos plugins.")
    if _inside(home.resolve(), folder):
        raise ValueError("A pasta do cliente não pode conter a pasta do get-brolls ($GB_HOME).")


def _ensure_dir(path):
    """Cria `path` se faltar; recusa link e arquivo no lugar."""
    if is_link(path):
        raise ValueError(f"{_shown(path)} é um link; o get-brolls não cria nada através de links.")
    if path.exists() and not path.is_dir():
        raise ValueError(f"{_shown(path)} existe e não é uma pasta.")
    path.mkdir(exist_ok=True)


def _prepare_folder(folder, slug, name):
    """Cria (ou confere) a pasta do cliente; devolve `(client.json, criado agora?)`."""
    if is_link(folder):
        raise ValueError(f"{_shown(folder)} é um link; o get-brolls não cria nada através de links.")
    if folder.exists() and not folder.is_dir():
        raise ValueError(f"{_shown(folder)} existe e não é uma pasta.")
    existing = folder.is_dir() and os.path.lexists(folder / CLIENT_FILE)
    client = _read_client_file(folder, slug) if existing else None
    for sub in (COMPONENTS, TEMPLATES):
        if is_link(folder / sub):
            raise ValueError(f"{_shown(folder / sub)} é um link; o get-brolls não cria nada através de links.")
    for sub in COMPONENT_FOLDERS:
        if is_link(folder / COMPONENTS / sub):
            raise ValueError(
                f"{_shown(folder / COMPONENTS / sub)} é um link; o get-brolls não cria nada através de links."
            )
    _ensure_dir(folder)
    _ensure_dir(folder / COMPONENTS)
    for sub in COMPONENT_FOLDERS:
        _ensure_dir(folder / COMPONENTS / sub)
    _ensure_dir(folder / TEMPLATES)
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
    name = None if name is None else _check_name(name)
    folder = _checked_base(root) / slug
    _check_placement(folder)
    load_registry()  # registro ilegível ou mais novo: recusa antes de criar qualquer pasta
    with _locked():
        rows = load_registry()["clients"]
        taken = next((row for row in rows if row["slug"] == slug), None)
        if taken:
            raise ValueError(
                f'O cliente "{slug}" já está registrado em {_shown(taken["root"])}. Rode client --action remove antes.'
            )
        client, created = _prepare_folder(folder, slug, name)
        entry = {"slug": slug, "root": str(folder), "added": now()}
        _write_registry([*rows, entry])
    return {"client": _view(entry, client), "created": created, "registry": _shown(registry_path())}


def remove(slug) -> dict:
    """Tira `slug` do registro; a pasta do cliente e os arquivos dela ficam."""
    check_slug(slug)
    with _locked():
        rows = load_registry()["clients"]
        entry = next((row for row in rows if row["slug"] == slug), None)
        if entry is None:
            raise ValueError(f'O cliente "{slug}" não está registrado.')
        _write_registry([row for row in rows if row["slug"] != slug])
    return {
        "removed": _view(entry),
        "files_kept": True,
        "message": f"Cliente {slug} desregistrado; a pasta dele não foi apagada.",
    }


def list_clients() -> dict:
    """Todos os clientes registrados, com o problema de cada pasta que não serve mais."""
    return {"registry": _shown(registry_path()), "clients": [_describe(row) for row in load_registry()["clients"]]}


def show(slug) -> dict:
    """Um cliente registrado, com o `client.json` conferido."""
    entry = _registered(slug)
    client = _load_entry(entry)
    return {
        "client": {**_view(entry, client), "created": client.get("created"), "defaults": client.get("defaults", {})}
    }


def _required(args, flag):
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
