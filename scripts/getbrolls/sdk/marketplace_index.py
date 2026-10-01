"""Índice de um marketplace de plugins (`getbrolls-marketplace.json`): formato e validador.

O índice mora na raiz do repositório do marketplace e é lido no commit fixado
(nunca na ponta). Este módulo é a autoridade do formato: o schema publicado em
`schemas/marketplace_index.schema.json` descreve a mesma coisa para quem monta o
índice, e um teste mantém os dois de acordo. O validador vai além do schema:
ids únicos, `renames` sem ciclo, faixa de `requires_getbrolls` legível e as
mesmas regras de `permissions`, `requires`, `platforms` e `license` do manifesto.

Formato estrito: chave desconhecida, no topo ou numa entrada, é erro. A versão
mora em `"schema": "getbrolls.marketplace_index/<N>"`; `N` maior que o suportado
é recusado com a frase padrão de versão mais nova.

Só lê dado; nada aqui fala com a rede ou roda git.
"""

import json
import re
from typing import NoReturn

from .. import versioning
from ..errors import UsageError
from . import kinds
from .contracts import CORE, NAME_RE, RESERVED_IDS
from .git_source import COMMIT_RE, validate_ref, validate_repo_path, validate_url
from .manifest import (
    DESCRIPTION_MAX_CHARS,
    VERSION_RE,
    ManifestError,
    satisfies,
    validate_license,
    validate_permissions,
    validate_platforms,
    validate_requires,
)

FAMILY = "marketplace_index"
SCHEMA_VERSION = 1
INDEX_NAME = "getbrolls-marketplace.json"
INDEX_MAX_BYTES = 1024 * 1024
INDEX_MAX_DEPTH = 32
PLUGINS_MAX = 1000
MAINTAINERS_MAX = 20
MAINTAINER_MAX_CHARS = 120
REPO_MAX_CHARS = 2048
TIERS = ("official", "verified", "community")
# Tiers que podem entrar numa atualização em lote (a comunidade é sempre por id).
BATCH_TIERS = ("official", "verified")
MARKETPLACE_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
# `engenheirodevideo` é o nome do marketplace de plugins do Claude Code: um índice de
# plugins do get-brolls com o mesmo nome confundiria quem lê `install --id x@<nome>`.
RESERVED_MARKETPLACE_NAMES = ("engenheirodevideo",)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SELF_REPO = "."
DETAIL_MAX_CHARS = 300

TOP_LEVEL_REQUIRED = ("schema", "name", "plugins", "renames")
TOP_LEVEL_OPTIONAL = ("description", "metadata")
ENTRY_KEYS = (
    "id",
    "version",
    "description",
    "tier",
    "source",
    "content_sha256",
    "sdk_api",
    "requires_getbrolls",
    "permissions",
    "contributes",
    "requires",
    "platforms",
    "license",
    "maintainers",
    "attestation",
    "yanked",
    "deprecated",
)
SOURCE_KEYS = ("type", "repo", "ref", "commit", "subdir")
DEPRECATED_KEYS = ("reason", "replacement")
# Campos que a entrada e o manifesto materializado têm que ter iguais (senão, recusa).
MISMATCH_FIELDS = (
    "id",
    "version",
    "sdk_api",
    "requires_getbrolls",
    "permissions",
    "contributes",
    "requires",
    "platforms",
    "license",
)
# Campos em que a divergência só vira aviso.
WARNING_FIELDS = ("description",)


class MarketplaceIndexError(ValueError):
    """Índice de marketplace ilegível ou fora do formato; a mensagem diz onde."""


def _fail(where, message) -> NoReturn:
    raise MarketplaceIndexError(f"Índice {where}: {message}")


def _depth(value):
    """Profundidade de aninhamento de `value` (escalar = 0), sem recursão."""
    deepest, stack = 0, [(value, 0)]
    while stack:
        node, level = stack.pop()
        deepest = max(deepest, level)
        if isinstance(node, dict):
            stack.extend((child, level + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, level + 1) for child in node)
    return deepest


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise MarketplaceIndexError(f"Índice: chave repetida {key[:80]!r}.")
        seen[key] = value
    return seen


def _no_constant(name):
    raise MarketplaceIndexError(f"Índice: {name} não é JSON válido.")


def parse_index(data: bytes) -> dict:
    """`getbrolls-marketplace.json` em bytes → índice validado e normalizado.

    Recusa arquivo acima de `INDEX_MAX_BYTES`, texto fora de UTF-8, chave repetida,
    NaN/Infinity e aninhamento acima de `INDEX_MAX_DEPTH` antes de validar."""
    if not isinstance(data, bytes | bytearray):
        raise MarketplaceIndexError("Índice: esperado o conteúdo em bytes.")
    if len(data) > INDEX_MAX_BYTES:
        raise MarketplaceIndexError(f"Índice: {INDEX_NAME} passa de {INDEX_MAX_BYTES // 1024} KB.")
    try:
        text = bytes(data).decode("utf-8")
        raw = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except UnicodeDecodeError:
        raise MarketplaceIndexError(f"Índice: {INDEX_NAME} não está em UTF-8.") from None
    except (RecursionError, MemoryError):
        raise MarketplaceIndexError(f"Índice: {INDEX_NAME} tem aninhamento fundo demais.") from None
    except json.JSONDecodeError:
        raise MarketplaceIndexError(f"Índice: {INDEX_NAME} não é JSON válido.") from None
    if _depth(raw) > INDEX_MAX_DEPTH:
        raise MarketplaceIndexError(f"Índice: {INDEX_NAME} tem aninhamento fundo demais.")
    return validate_index(raw)


def validate_marketplace_name(name: object, where: str = "marketplace") -> str:
    """`name` como veio, se for um nome de marketplace aceito (e não reservado)."""
    if not isinstance(name, str) or not MARKETPLACE_NAME_RE.fullmatch(name):
        _fail(
            where,
            "name tem que ter 1–64 caracteres a-z, 0-9 e hífen, começando e terminando por letra ou dígito "
            f"(veio {str(name)[:80]!r}).",
        )
    if name in RESERVED_MARKETPLACE_NAMES:
        _fail(where, f"o nome {name!r} é reservado (é o marketplace de plugins do Claude Code); escolha outro.")
    return name


def _plugin_id(raw, where, field="id"):
    if not isinstance(raw, str) or not NAME_RE.fullmatch(raw) or raw == CORE or raw in RESERVED_IDS:
        _fail(where, f"{field} inválido ou reservado: {str(raw)[:80]!r}.")
    return raw


def _exact_keys(where, field, raw, keys):
    if not isinstance(raw, dict):
        _fail(where, f"{field} tem que ser um objeto.")
    missing = [key for key in keys if key not in raw]
    unknown = sorted(set(raw) - set(keys))
    if missing:
        _fail(where, f"{field}: falta {', '.join(missing)}.")
    if unknown:
        _fail(where, f"{field}: chave desconhecida {', '.join(unknown)}.")


def validate_repo(raw: object, where: str) -> str:
    """`source.repo` de uma entrada: `https://…` sem credencial, query nem fragmento,
    `git@host:caminho` ou `"."` (o próprio repositório do marketplace).

    Caminho absoluto, `file://`, `ext::`, `http://` e qualquer outro transporte são
    recusados em todo índice."""
    if raw == SELF_REPO:
        return SELF_REPO
    if isinstance(raw, str) and _repo_url_ok(raw):
        return raw
    _fail(
        where,
        'source.repo tem que ser https://… (sem usuário, senha, ? nem #), git@host:caminho ou "."; '
        f"caminho local e file:// não valem num índice (veio {str(raw)[:80]!r}).",
    )


def _repo_url_ok(raw: str) -> bool:
    """Mesma regra do `--source` da CLI (`git_source.validate_url`), mais o teto de tamanho."""
    if len(raw) > REPO_MAX_CHARS:
        return False
    try:
        validate_url(raw)
    except ValueError:
        return False
    return True


def _source(raw, where):
    _exact_keys(where, "source", raw, SOURCE_KEYS)
    if raw["type"] != "git":
        _fail(where, 'source.type tem que ser "git".')
    commit = raw["commit"]
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        _fail(where, "source.commit tem que ser o sha completo: 40 caracteres hexadecimais minúsculos.")
    try:
        ref = None if raw["ref"] is None else validate_ref(raw["ref"])
        subdir = None if raw["subdir"] is None else validate_repo_path(raw["subdir"], "source.subdir")
    except ValueError as exc:
        _fail(where, str(exc))
    return {"type": "git", "repo": validate_repo(raw["repo"], where), "ref": ref, "commit": commit, "subdir": subdir}


def _contributes(raw, where):
    if (
        not isinstance(raw, list)
        or any(not isinstance(kind, str) or kind not in kinds.SUPPORTED_SINGULAR for kind in raw)
        or raw != sorted(set(raw))
    ):
        _fail(
            where,
            f"contributes é a lista em ordem alfabética, sem repetição, dos tipos no singular "
            f"({', '.join(kinds.SUPPORTED_SINGULAR)}).",
        )
    return list(raw)


def _maintainers(raw, where):
    if (
        not isinstance(raw, list)
        or len(raw) > MAINTAINERS_MAX
        or any(
            not isinstance(item, str)
            or not 0 < len(item.strip()) <= MAINTAINER_MAX_CHARS
            or not item.isprintable()
            or item != item.strip()
            for item in raw
        )
        or len(set(raw)) != len(raw)
    ):
        _fail(
            where,
            f"maintainers é uma lista de até {MAINTAINERS_MAX} nomes, sem repetição, cada um com até "
            f"{MAINTAINER_MAX_CHARS} caracteres.",
        )
    return list(raw)


def _deprecated(raw, where):
    if raw is None:
        return None
    _exact_keys(where, "deprecated", raw, DEPRECATED_KEYS)
    reason = raw["reason"]
    if not isinstance(reason, str) or not 0 < len(reason.strip()) <= DESCRIPTION_MAX_CHARS:
        _fail(where, f"deprecated.reason é texto, com até {DESCRIPTION_MAX_CHARS} caracteres.")
    replacement = raw["replacement"]
    if replacement is not None:
        replacement = _plugin_id(replacement, where, "deprecated.replacement")
    return {"reason": reason, "replacement": replacement}


def _description(raw, where):
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw.strip()) > DESCRIPTION_MAX_CHARS:
        _fail(where, f"description é texto, com até {DESCRIPTION_MAX_CHARS} caracteres.")
    return raw


def _manifest_rules(raw, where):
    """Campos que seguem as regras do manifesto: `ManifestError` vira erro do índice."""
    try:
        if not isinstance(raw["requires_getbrolls"], str):
            raise ManifestError('requires_getbrolls tem que ser uma faixa como ">=2.6,<3".')
        satisfies("0.0.0", raw["requires_getbrolls"])
        rules = {
            "permissions": validate_permissions(where, raw["permissions"]),
            "requires": validate_requires(where, raw["requires"]),
            "platforms": None if raw["platforms"] is None else validate_platforms(where, raw["platforms"]),
            "license": validate_license(where, raw["license"]),
        }
    except ManifestError as exc:
        problem = str(exc).removeprefix(f"Plugin {where}: ")
    else:
        return rules
    _fail(where, problem)


def validate_entry(raw: object, where: str) -> dict:
    """Uma entrada de `plugins[]` validada e normalizada, com as chaves na ordem de `ENTRY_KEYS`.

    `where` nomeia a entrada nas mensagens (`Índice <where>: ...`)."""
    _exact_keys(where, "a entrada", raw, ENTRY_KEYS)
    assert isinstance(raw, dict)  # noqa: S101  # _exact_keys já recusou o que não é objeto
    ident = _plugin_id(raw["id"], where)
    if not isinstance(raw["version"], str) or not VERSION_RE.fullmatch(raw["version"]):
        _fail(where, "version tem que ser X.Y.Z.")
    if raw["tier"] not in TIERS:
        _fail(where, f"tier tem que ser {', '.join(TIERS)}.")
    if not isinstance(raw["content_sha256"], str) or not SHA256_RE.fullmatch(raw["content_sha256"]):
        _fail(where, "content_sha256 tem que ter 64 caracteres hexadecimais minúsculos.")
    if type(raw["sdk_api"]) is not int or raw["sdk_api"] < 1:
        _fail(where, "sdk_api tem que ser um inteiro a partir de 1.")
    if raw["attestation"] is not None:
        _fail(where, "attestation tem que ser null nesta versão (assinatura fica para uma versão futura).")
    if type(raw["yanked"]) is not bool:
        _fail(where, "yanked tem que ser true ou false.")
    rules = _manifest_rules(raw, where)
    return {
        "id": ident,
        "version": raw["version"],
        "description": _description(raw["description"], where),
        "tier": raw["tier"],
        "source": _source(raw["source"], where),
        "content_sha256": raw["content_sha256"],
        "sdk_api": raw["sdk_api"],
        "requires_getbrolls": raw["requires_getbrolls"],
        "permissions": rules["permissions"],
        "contributes": _contributes(raw["contributes"], where),
        "requires": rules["requires"],
        "platforms": rules["platforms"],
        "license": rules["license"],
        "maintainers": _maintainers(raw["maintainers"], where),
        "attestation": None,
        "yanked": raw["yanked"],
        "deprecated": _deprecated(raw["deprecated"], where),
    }


def _renames(raw, ids, where):
    if not isinstance(raw, dict):
        _fail(where, "renames tem que ser um objeto {id antigo: id novo}.")
    for old, new in raw.items():
        _plugin_id(old, where, "renames (id antigo)")
        _plugin_id(new, where, f"renames.{old}")
        if old in ids:
            _fail(where, f"renames.{old}: o id antigo ainda é uma entrada de plugins.")
    for old in raw:
        seen, current = {old}, raw[old]
        while current in raw:
            if current in seen:
                _fail(where, f"renames tem um ciclo passando por {old}.")
            seen.add(current)
            current = raw[current]
        if current not in ids:
            _fail(where, f"renames.{old} aponta para {current}, que não está em plugins.")
    return dict(raw)


def validate_index(raw: object) -> dict:
    """Índice validado e normalizado: topo estrito, ids únicos, `renames` sem ciclo e com alvo presente."""
    if not isinstance(raw, dict):
        raise MarketplaceIndexError("Índice: o arquivo tem que ser um objeto JSON.")
    missing = [key for key in TOP_LEVEL_REQUIRED if key not in raw]
    unknown = sorted(set(raw) - set(TOP_LEVEL_REQUIRED) - set(TOP_LEVEL_OPTIONAL))
    if missing:
        raise MarketplaceIndexError(f"Índice: falta {', '.join(missing)} no topo.")
    if unknown:
        raise MarketplaceIndexError(f"Índice: chave desconhecida no topo: {', '.join(unknown)}.")
    versioning.read_schema(
        raw,
        FAMILY,
        supported=SCHEMA_VERSION,
        label=INDEX_NAME,
        invalid=lambda: MarketplaceIndexError(
            f"Índice: schema tem que ser {versioning.schema_name(FAMILY, SCHEMA_VERSION)!r}."
        ),
    )
    name = validate_marketplace_name(raw["name"])
    description = _description(raw.get("description"), name)
    if "metadata" in raw and not isinstance(raw["metadata"], dict):
        _fail(name, "metadata tem que ser um objeto JSON (o core não lê o conteúdo).")
    plugins = raw["plugins"]
    if not isinstance(plugins, list) or len(plugins) > PLUGINS_MAX:
        _fail(name, f"plugins tem que ser uma lista com até {PLUGINS_MAX} entradas.")
    entries, ids = [], set()
    for position, item in enumerate(plugins):
        label = item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) else None
        valid = validate_entry(item, f"{name}/plugins[{position}]" + (f" ({label[:40]})" if label else ""))
        if valid["id"] in ids:
            _fail(name, f"o id {valid['id']} aparece mais de uma vez em plugins.")
        ids.add(valid["id"])
        entries.append(valid)
    result = {
        "schema": versioning.schema_name(FAMILY, SCHEMA_VERSION),
        "name": name,
        "description": description,
        "plugins": entries,
        "renames": _renames(raw["renames"], ids, name),
    }
    if "metadata" in raw:
        result["metadata"] = raw["metadata"]
    return result


def entry_from_manifest(
    manifest: dict, *, source: dict, content_sha256: str, tier: str, maintainers: list[str]
) -> dict:
    """A entrada do índice para um manifesto normalizado (`manifest.read_manifest`).

    `source` é `{"repo", "commit"}` e, opcionais, `"ref"` e `"subdir"`; `content_sha256`
    é o de `install.content_digest`. Sai validada, com as chaves na ordem de
    `ENTRY_KEYS`: o mesmo manifesto gera sempre os mesmos bytes."""
    unknown = set(source) - set(SOURCE_KEYS)
    if unknown:
        raise MarketplaceIndexError(f"source: chave desconhecida {', '.join(sorted(unknown))}.")
    raw = {
        "id": manifest["id"],
        "version": manifest["version"],
        "description": manifest.get("description"),
        "tier": tier,
        "source": {
            "type": source.get("type", "git"),
            "repo": source.get("repo"),
            "ref": source.get("ref"),
            "commit": source.get("commit"),
            "subdir": source.get("subdir"),
        },
        "content_sha256": content_sha256,
        "sdk_api": manifest["sdk_api"],
        "requires_getbrolls": manifest["requires_getbrolls"],
        "permissions": manifest["permissions"],
        "contributes": kinds.contributed(manifest["contributes"]),
        "requires": manifest["requires"],
        "platforms": manifest.get("platforms"),
        "license": manifest.get("license"),
        "maintainers": list(maintainers),
        "attestation": None,
        "yanked": False,
        "deprecated": None,
    }
    return validate_entry(raw, str(manifest["id"]))


def _manifest_view(manifest):
    """Os campos comparáveis do manifesto normalizado, na forma em que a entrada os guarda."""
    return {
        "id": manifest.get("id"),
        "version": manifest.get("version"),
        "description": manifest.get("description"),
        "sdk_api": manifest.get("sdk_api"),
        "requires_getbrolls": manifest.get("requires_getbrolls"),
        "permissions": manifest.get("permissions"),
        "contributes": kinds.contributed(manifest.get("contributes") or {}),
        "requires": manifest.get("requires"),
        "platforms": manifest.get("platforms"),
        "license": manifest.get("license"),
    }


def _differences(entry, manifest, fields):
    view = _manifest_view(manifest)
    found = []
    for field in fields:
        if entry.get(field) != view[field]:
            detail = f"{field}: índice {json.dumps(entry.get(field), ensure_ascii=False)}, manifesto " + json.dumps(
                view[field], ensure_ascii=False
            )
            found.append(detail if len(detail) <= DETAIL_MAX_CHARS else detail[: DETAIL_MAX_CHARS - 3] + "...")
    return found


def manifest_mismatches(entry: dict, manifest: dict) -> list[str]:
    """Campos de `MISMATCH_FIELDS` em que a entrada e o manifesto divergem (`[]` quando batem).

    Cada item começa pelo nome do campo (`"permissions: índice …, manifesto …"`).
    Qualquer item aqui recusa o install pelo marketplace."""
    return _differences(entry, manifest, MISMATCH_FIELDS)


def manifest_warnings(entry: dict, manifest: dict) -> list[str]:
    """Divergências que só viram aviso (`WARNING_FIELDS`: a `description`)."""
    return _differences(entry, manifest, WARNING_FIELDS)


def parse_plugin_ref(text: str) -> tuple[str, str]:
    """`"<id>@<marketplace>"` → `(id, marketplace)`; forma inválida é `UsageError`."""
    if not isinstance(text, str) or text.count("@") != 1:
        raise UsageError(f"Use <id>@<marketplace> (ex.: demo@exemplo); veio {str(text)[:80]!r}.")
    plugin_id, _, name = text.partition("@")
    if not NAME_RE.fullmatch(plugin_id) or not MARKETPLACE_NAME_RE.fullmatch(name):
        raise UsageError(f"Use <id>@<marketplace> (ex.: demo@exemplo); veio {text[:80]!r}.")
    if name in RESERVED_MARKETPLACE_NAMES:
        raise UsageError(f"{name} é o marketplace de plugins do Claude Code, não um índice do get-brolls.")
    return plugin_id, name


def resolve_rename(index: dict, plugin_id: str) -> str | None:
    """Id atual de um plugin renomeado (seguindo a cadeia de `renames`), ou `None` se não foi renomeado."""
    renames = index.get("renames") or {}
    if plugin_id not in renames:
        return None
    current, seen = plugin_id, set()
    while current in renames and current not in seen:
        seen.add(current)
        current = renames[current]
    return current
