"""Leitura e validação do `getbrolls-plugin.json`.

O manifesto é a verdade que `plugins list`, `plugins check` e a CLI leem SEM
executar código do plugin: tudo o que ele contribui precisa estar declarado aqui.
"""

import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NoReturn

from .. import __version__
from .contracts import CORE, NAME_RE, RESERVED_IDS, SDK_API
from .kinds import SUPPORTED_PLURAL

MANIFEST_NAME = "getbrolls-plugin.json"
CONTRIBUTION_KINDS = (
    "providers",
    "presets",
    "routes",
    "commands",
    "exporters",
    "resolvers",
    "rules",
    "hooks",
    "themes",
    "brief_templates",
    "eval_rubrics",
    "capturers",
    "engines",
    "catalogs",
    "roteiro_templates",
)
# Tipos que esta versão do SDK sabe carregar; os outros ficam para versões futuras.
SUPPORTED_KINDS = SUPPORTED_PLURAL
TOP_LEVEL = frozenset(
    {
        "id",
        "name",
        "description",
        "version",
        "sdk_api",
        "requires_getbrolls",
        "entry",
        "contributes",
        "schema",
        "signed_fields",
        "engines",
        "permissions",
    }
)
# Campos de topo reservados para versões futuras: ausentes ou `{}`, nunca com conteúdo.
FUTURE_FIELDS = ("schema", "signed_fields", "engines")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
ENTRY_RE = re.compile(r"[A-Za-z0-9_]{1,64}\.py")
HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
ENV_RE = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
PATH_MAX_CHARS = 4096
NAME_MAX_CHARS = 80
DESCRIPTION_MAX_CHARS = 500
CLAUSE_RE = re.compile(r"\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+){0,2})\s*")


class ManifestError(ValueError):
    """Manifesto ausente, ilegível ou fora do formato; a mensagem diz o que corrigir."""


def _fail(folder_name, message) -> NoReturn:
    raise ManifestError(f"Plugin {folder_name}: {message}")


def _names(folder_name, field, value):
    if not isinstance(value, list) or len(set(map(str, value))) != len(value):
        _fail(folder_name, f"{field} tem que ser uma lista sem repetição.")
    for item in value:
        if not isinstance(item, str) or not NAME_RE.fullmatch(item):
            _fail(folder_name, f"{field}: nome inválido {item!r}.")
    return list(value)


def _contributes(folder_name, raw):
    if not isinstance(raw, dict) or set(raw) - set(CONTRIBUTION_KINDS):
        _fail(folder_name, f"contributes só aceita: {', '.join(CONTRIBUTION_KINDS)}.")
    result = {kind: _names(folder_name, f"contributes.{kind}", raw.get(kind, [])) for kind in CONTRIBUTION_KINDS}
    for kind in CONTRIBUTION_KINDS:
        if result[kind] and kind not in SUPPORTED_KINDS:
            _fail(folder_name, f"contributes.{kind} ainda não é suportado nesta versão do SDK.")
    return result


_SEPARATORS_RE = re.compile(r"[\\/]+")


def _is_home(raw):
    try:
        home = Path.home()
    except RuntimeError:
        return False
    return os.path.normcase(os.path.normpath(raw)) == os.path.normcase(os.path.normpath(str(home)))


def _root_ok(raw):
    """Raiz de `permissions.paths`: uma PASTA específica — absoluta (POSIX `/...` ou
    Windows `C:\\...`, conferida sem depender do sistema em que o manifesto é lido)
    ou `~/...`; nunca com `..`.

    Recusa raiz ampla demais, que tornaria a declaração sem sentido: a raiz do
    sistema (`/`, `\\`), uma raiz de unidade (`C:\\`, `C:/`, `C:`), `~` sozinho
    (`~`, `~/`, `~/.`) e a própria pasta pessoal escrita por extenso — `~` inteiro
    cobre `~/.ssh` e `~/.getbrolls/plugin-data/<outro plugin>`.
    """
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw or len(raw) > PATH_MAX_CHARS:
        return False
    if raw.startswith("~"):
        return _home_root_ok(raw)
    return _absolute_root_ok(raw)


def _home_root_ok(raw):
    """`~/pasta`: nunca `~` sozinho, `~/`, `~/.`, `~outro` nem com `..`."""
    if not raw.startswith(("~/", "~\\")):
        return False  # `~` sozinho ou `~outro`
    parts = [part for part in _SEPARATORS_RE.split(raw[2:]) if part not in ("", ".")]
    return bool(parts) and ".." not in parts


def _absolute_root_ok(raw):
    """Pasta absoluta (POSIX ou Windows) que não é a raiz do sistema ou da unidade,
    não tem `..` e não é a própria pasta pessoal."""
    posix, windows = PurePosixPath(raw), PureWindowsPath(raw)
    if windows.drive and not windows.root:
        return False  # `C:` / `C:pasta`: relativo à pasta corrente da unidade
    if not (posix.is_absolute() or windows.is_absolute()):
        return False
    if ".." in posix.parts or ".." in windows.parts:
        return False
    # Raiz do sistema ou da unidade: só a âncora, nenhuma pasta dentro dela.
    if not [part for part in _SEPARATORS_RE.split(raw[len(windows.anchor) :]) if part not in ("", ".")]:
        return False
    return not _is_home(raw)


def _permissions(folder_name, raw):
    if not isinstance(raw, dict) or set(raw) - {"network", "env", "paths"}:
        _fail(folder_name, "permissions só aceita network, env e paths.")
    network = raw.get("network", [])
    env = raw.get("env", [])
    paths = raw.get("paths", [])
    if not isinstance(network, list) or any(not isinstance(h, str) or not HOST_RE.fullmatch(h) for h in network):
        _fail(folder_name, "permissions.network aceita só nomes de host (api.exemplo.com), sem esquema nem caminho.")
    if not isinstance(env, list) or any(not isinstance(k, str) or not ENV_RE.fullmatch(k) for k in env):
        _fail(folder_name, "permissions.env aceita só nomes de variável em MAIÚSCULAS.")
    if not isinstance(paths, list) or not all(_root_ok(p) for p in paths):
        _fail(
            folder_name,
            "permissions.paths aceita só pastas específicas, absolutas ou começando por ~/, sem '..' "
            "(nunca /, a raiz de uma unidade como C:\\, ~ nem a pasta pessoal inteira).",
        )
    return {"network": list(network), "env": list(env), "paths": list(paths)}


def _identity(folder, raw):
    ident = raw.get("id")
    if not isinstance(ident, str) or not NAME_RE.fullmatch(ident) or ident == CORE:
        _fail(folder.name, "id inválido; use 2–32 caracteres a-z, 0-9 e _, começando por letra (e diferente de core).")
    if ident in RESERVED_IDS:
        _fail(folder.name, reserved_id_message(ident))
    return ident


def reserved_id_message(ident):
    """Frase que recusa um id reservado do get-brolls, listando os reservados."""
    return f'id "{ident}" é reservado do get-brolls ({", ".join(RESERVED_IDS)}): escolha outro.'


def _basic_fields(ident, raw):
    if not isinstance(raw.get("name"), str) or not 0 < len(raw["name"].strip()) <= NAME_MAX_CHARS:
        _fail(ident, "name é obrigatório, com até 80 caracteres.")
    if not isinstance(raw.get("version"), str) or not VERSION_RE.fullmatch(raw["version"]):
        _fail(ident, "version tem que ser X.Y.Z.")
    if type(raw.get("sdk_api")) is not int:
        _fail(ident, "sdk_api tem que ser um inteiro.")
    if not isinstance(raw.get("requires_getbrolls"), str):
        _fail(ident, 'requires_getbrolls é obrigatório (ex.: ">=2.6,<3").')
    satisfies(__version__, raw["requires_getbrolls"])


def _entry_field(folder, ident, raw):
    entry = raw.get("entry")
    if not isinstance(entry, str) or not ENTRY_RE.fullmatch(entry) or not (folder / entry).is_file():
        _fail(ident, "entry tem que ser um arquivo .py na raiz da pasta do plugin.")
    return entry


def _description(ident, raw):
    value = raw.get("description")
    if value is None:
        return None
    if not isinstance(value, str) or len(value.strip()) > DESCRIPTION_MAX_CHARS:
        _fail(ident, "description tem que ser texto, com até 500 caracteres.")
    return value


# Teto do manifesto: ele é relido a cada comando, para todo plugin da pasta.
# Um arquivo gigante ou com aninhamento absurdo vira `invalid` naquela linha — nunca
# um INTERNAL_ERROR de `plugins list`/`doctor`/`x --list`.
MANIFEST_MAX_BYTES = 64 * 1024


def _load_json(folder_name, path):
    if path.stat().st_size > MANIFEST_MAX_BYTES:
        _fail(folder_name, f"{MANIFEST_NAME} passa de {MANIFEST_MAX_BYTES // 1024} KB.")
    try:
        return json.loads(path.read_bytes()[: MANIFEST_MAX_BYTES + 1].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _fail(folder_name, f"{MANIFEST_NAME} não é JSON válido em UTF-8.")
    except (RecursionError, MemoryError):
        _fail(folder_name, f"{MANIFEST_NAME} tem aninhamento fundo demais.")


def read_manifest(folder: Path, require_folder_match: bool = True) -> dict:
    """Lê e valida `getbrolls-plugin.json` de `folder`; devolve o manifesto normalizado.

    Com `require_folder_match`, o `id` tem que ser o nome da pasta (plugin instalado);
    o `plugins check` passa `False` para conferir uma pasta qualquer."""
    path = folder / MANIFEST_NAME
    if not path.is_file():
        _fail(folder.name, f"{MANIFEST_NAME} não encontrado.")
    raw = _load_json(folder.name, path)
    if not isinstance(raw, dict):
        _fail(folder.name, "o manifesto tem que ser um objeto JSON.")
    unknown = set(raw) - TOP_LEVEL
    if unknown:
        _fail(folder.name, f"campo desconhecido no manifesto: {', '.join(sorted(unknown))}.")
    ident = _identity(folder, raw)
    if require_folder_match and folder.name != ident:
        _fail(folder.name, f"a pasta tem que se chamar {ident}, igual ao id.")
    _basic_fields(ident, raw)
    entry = _entry_field(folder, ident, raw)
    for field in FUTURE_FIELDS:
        if raw.get(field, {}) != {}:
            _fail(ident, f"{field} ainda não é suportado nesta versão do SDK.")
    return {
        "id": ident,
        "name": raw["name"].strip(),
        "description": _description(ident, raw),
        "version": raw["version"],
        "sdk_api": raw["sdk_api"],
        "requires_getbrolls": raw["requires_getbrolls"],
        "entry": entry,
        "contributes": _contributes(ident, raw.get("contributes", {})),
        "schema": {},
        "signed_fields": {},
        "permissions": _permissions(ident, raw.get("permissions", {})),
    }


def _as_tuple(text):
    parts = [int(p) for p in text.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def satisfies(version: str, spec: str) -> bool:
    """`version` atende a `spec` (cláusulas como `">=2.6,<3"`)? Spec inválida vira `ManifestError`."""
    clauses = spec.split(",")
    parsed = [CLAUSE_RE.fullmatch(clause) for clause in clauses]
    if not clauses or any(m is None for m in parsed):
        raise ManifestError(f'requires_getbrolls inválido: {spec!r}; use cláusulas como ">=2.6,<3".')
    current = _as_tuple(version.split("+", maxsplit=1)[0].split("-", maxsplit=1)[0])
    checks = {
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
    }
    return all(checks[m[1]](current, _as_tuple(m[2])) for m in parsed if m)


def compatibility_problem(manifest: dict, version: str = __version__) -> str | None:
    """Por que o plugin não roda nesta versão (`sdk_api` ou `requires_getbrolls`), ou `None`."""
    if manifest["sdk_api"] != SDK_API:
        return f"sdk_api {manifest['sdk_api']} não é suportado; esta versão do get-brolls fala sdk_api {SDK_API}."
    if not satisfies(version, manifest["requires_getbrolls"]):
        return f"exige get-brolls {manifest['requires_getbrolls']}; instalado: {version}."
    return None
