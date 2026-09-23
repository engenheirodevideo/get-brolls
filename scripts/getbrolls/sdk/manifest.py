"""Leitura e validação do `getbrolls-plugin.json`.

O manifesto é a verdade que `plugins list`, `plugins check` e a CLI leem SEM
executar código do plugin: tudo o que ele contribui precisa estar declarado aqui.
"""

import json
import re
from pathlib import Path
from typing import NoReturn

from .. import __version__
from .contracts import CORE, NAME_RE, SDK_API

MANIFEST_NAME = "getbrolls-plugin.json"
CONTRIBUTION_KINDS = (
    "providers",
    "presets",
    "exporters",
    "rules",
    "hooks",
    "themes",
    "brief_templates",
    "eval_rubrics",
)
# Tipos que esta versão do SDK sabe carregar; os outros chegam nas próximas ondas.
SUPPORTED_KINDS = ("providers", "presets")
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
        "permissions",
    }
)
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
ENTRY_RE = re.compile(r"[A-Za-z0-9_]{1,64}\.py")
HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")
ENV_RE = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
CLAUSE_RE = re.compile(r"\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+){0,2})\s*")


class ManifestError(ValueError):
    pass


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


def _permissions(folder_name, raw):
    if not isinstance(raw, dict) or set(raw) - {"network", "env"}:
        _fail(folder_name, "permissions só aceita network e env.")
    network = raw.get("network", [])
    env = raw.get("env", [])
    if not isinstance(network, list) or any(not isinstance(h, str) or not HOST_RE.fullmatch(h) for h in network):
        _fail(folder_name, "permissions.network aceita só nomes de host (api.exemplo.com), sem esquema nem caminho.")
    if not isinstance(env, list) or any(not isinstance(k, str) or not ENV_RE.fullmatch(k) for k in env):
        _fail(folder_name, "permissions.env aceita só nomes de variável em MAIÚSCULAS.")
    return {"network": list(network), "env": list(env)}


def _identity(folder, raw):
    ident = raw.get("id")
    if not isinstance(ident, str) or not NAME_RE.fullmatch(ident) or ident == CORE:
        _fail(folder.name, "id inválido; use 2–32 caracteres a-z, 0-9 e _, começando por letra (e diferente de core).")
    return ident


def _basic_fields(ident, raw):
    if not isinstance(raw.get("name"), str) or not 0 < len(raw["name"].strip()) <= 80:  # noqa: PLR2004 - "até 80 caracteres" na mensagem
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


def read_manifest(folder: Path, require_folder_match: bool = True) -> dict:
    path = folder / MANIFEST_NAME
    if not path.is_file():
        _fail(folder.name, f"{MANIFEST_NAME} não encontrado.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        _fail(folder.name, f"{MANIFEST_NAME} não é JSON válido em UTF-8.")
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
    for field in ("schema", "signed_fields"):
        if raw.get(field, {}) != {}:
            _fail(ident, f"{field} ainda não é suportado nesta versão do SDK.")
    return {
        "id": ident,
        "name": raw["name"].strip(),
        "description": raw.get("description") if isinstance(raw.get("description"), str) else None,
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
    if manifest["sdk_api"] != SDK_API:
        return f"sdk_api {manifest['sdk_api']} não é suportado; esta versão do get-brolls fala sdk_api {SDK_API}."
    if not satisfies(version, manifest["requires_getbrolls"]):
        return f"exige get-brolls {manifest['requires_getbrolls']}; instalado: {version}."
    return None
