"""Leitura e validação do `getbrolls-plugin.json`.

O manifesto é a verdade que `plugins list`, `plugins check` e a CLI leem SEM
executar código do plugin: tudo o que ele contribui precisa estar declarado aqui.
"""

import json
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple, NoReturn
from urllib.parse import urlsplit

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
# Campos de topo do `sdk_api` 1, congelados: campo novo só dentro de `metadata` ou
# com um `sdk_api` novo. Um campo fora desta lista continua sendo erro.
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
        "permissions",
        "schema",
        "signed_fields",
        "engines",
        "homepage",
        "license",
        "author",
        "keywords",
        "platforms",
        "requires",
        "metadata",
    }
)
# Campos de topo reservados para versões futuras: ausentes ou `{}`, nunca com conteúdo.
FUTURE_FIELDS = ("schema", "signed_fields")
# Chaves de `permissions`, também congeladas no `sdk_api` 1.
PERMISSION_KEYS = ("network", "env", "paths", "project_write")
# Áreas do projeto em que um plugin pode pedir para gravar (`permissions.project_write`).
PROJECT_WRITE_AREAS = ("analysis",)
PLATFORMS = ("darwin", "linux", "windows")
HOMEPAGE_MAX_CHARS = 2048
AUTHOR_MAX_CHARS = 120
KEYWORDS_MAX = 10
REQUIRES_LIST_MAX = 20
SERVICES_MAX = 10
REQUIREMENT_MAX_CHARS = 200
URL_RE = re.compile(r"https://[^\s\x00-\x1f\x7f]+")
LICENSE_RE = re.compile(r"[A-Za-z0-9.+\-() ]{1,128}")
KEYWORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
BINARY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
SERVICE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
RUNTIME_RE = SERVICE_RE
_DIST_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_VERSION_CLAUSE = r"(?:>=|<=|==|>|<)\d+(?:\.\d+){0,2}"
# Requisito Python: nome da distribuição, extras opcionais e cláusulas na mesma
# gramática de `requires_getbrolls` (sem marcadores, URLs nem caminhos).
REQUIREMENT_RE = re.compile(
    rf"(?P<name>{_DIST_NAME})(?:\[{_DIST_NAME}(?:,{_DIST_NAME})*\])?(?:{_VERSION_CLAUSE}(?:,{_VERSION_CLAUSE})*)?"
)
REQUIRES_KEYS = ("python", "binaries", "runtimes", "services")
ENGINES_RETIRED = "engines foi substituído por requires (requires.runtimes); veja docs/SDK.md#manifesto."
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


def validate_permissions(where: str, raw: object) -> dict:
    """`permissions` validado e normalizado, com as quatro chaves sempre presentes.

    `where` entra no começo da mensagem (`Plugin <where>: ...`); o índice de um
    marketplace usa a mesma regra para a entrada de cada plugin."""
    if not isinstance(raw, dict) or set(raw) - set(PERMISSION_KEYS):
        _fail(where, "permissions só aceita network, env, paths e project_write.")
    network = raw.get("network", [])
    env = raw.get("env", [])
    paths = raw.get("paths", [])
    project_write = raw.get("project_write", [])
    if not isinstance(network, list) or any(not isinstance(h, str) or not HOST_RE.fullmatch(h) for h in network):
        _fail(where, "permissions.network aceita só nomes de host (api.exemplo.com), sem esquema nem caminho.")
    if not isinstance(env, list) or any(not isinstance(k, str) or not ENV_RE.fullmatch(k) for k in env):
        _fail(where, "permissions.env aceita só nomes de variável em MAIÚSCULAS.")
    if not isinstance(paths, list) or not all(_root_ok(p) for p in paths):
        _fail(
            where,
            "permissions.paths aceita só pastas específicas, absolutas ou começando por ~/, sem '..' "
            "(nunca /, a raiz de uma unidade como C:\\, ~ nem a pasta pessoal inteira).",
        )
    if (
        not isinstance(project_write, list)
        or any(not isinstance(area, str) or area not in PROJECT_WRITE_AREAS for area in project_write)
        or len(set(project_write)) != len(project_write)
    ):
        _fail(
            where,
            f"permissions.project_write aceita só {', '.join(PROJECT_WRITE_AREAS)}, sem repetição "
            "(a área do projeto em que o plugin pode gravar).",
        )
    return {"network": list(network), "env": list(env), "paths": list(paths), "project_write": list(project_write)}


class _ListRule(NamedTuple):
    """Regra de uma lista de textos do manifesto: formato de cada item, teto e a dica do erro."""

    pattern: re.Pattern[str]
    limit: int
    hint: str


def _unique_strings(where, field, raw, rule):
    """Lista de textos únicos que casam com `rule.pattern`, com no máximo `rule.limit` itens."""
    if not isinstance(raw, list) or len(raw) > rule.limit:
        _fail(where, f"{field} tem que ser uma lista com até {rule.limit} itens.")
    if any(not isinstance(item, str) or not rule.pattern.fullmatch(item) for item in raw):
        _fail(where, f"{field}: {rule.hint}")
    if len(set(raw)) != len(raw):
        _fail(where, f"{field} não pode ter itens repetidos.")
    return list(raw)


def _version_spec_ok(spec):
    return (
        isinstance(spec, str)
        and 0 < len(spec) <= REQUIREMENT_MAX_CHARS
        and all(CLAUSE_RE.fullmatch(clause) for clause in spec.split(","))
    )


def validate_requires(where: str, raw: object) -> dict:
    """`requires` validado: `{"python": [], "binaries": [], "runtimes": {}, "services": []}`.

    Chave desconhecida é erro. `python` são requisitos de distribuição (`nome[extras]>=X.Y`),
    `binaries` nomes de executável (nunca caminho), `runtimes` `{nome: faixa de versão}` e
    `services` nomes informativos (`postgres`)."""
    if not isinstance(raw, dict) or set(raw) - set(REQUIRES_KEYS):
        _fail(where, f"requires só aceita {', '.join(REQUIRES_KEYS)}.")
    python = raw.get("python", [])
    if isinstance(python, list) and any(isinstance(req, str) and len(req) > REQUIREMENT_MAX_CHARS for req in python):
        _fail(where, f"requires.python: cada requisito tem até {REQUIREMENT_MAX_CHARS} caracteres.")
    python = _unique_strings(
        where,
        "requires.python",
        python,
        _ListRule(
            REQUIREMENT_RE,
            REQUIRES_LIST_MAX,
            'use o nome da distribuição com extras e versão opcionais (ex.: "psycopg[binary]>=3.1"); '
            "sem URL, caminho nem marcador de ambiente.",
        ),
    )
    if len({requirement_name(req).lower() for req in python}) != len(python):
        _fail(where, "requires.python não pode ter itens repetidos.")
    binaries = _unique_strings(
        where,
        "requires.binaries",
        raw.get("binaries", []),
        _ListRule(BINARY_RE, REQUIRES_LIST_MAX, 'use só o nome do executável (ex.: "node"), nunca um caminho.'),
    )
    runtimes = raw.get("runtimes", {})
    if (
        not isinstance(runtimes, dict)
        or len(runtimes) > REQUIRES_LIST_MAX
        or any(not isinstance(name, str) or not RUNTIME_RE.fullmatch(name) for name in runtimes)
        or not all(_version_spec_ok(spec) for spec in runtimes.values())
    ):
        _fail(
            where,
            f"requires.runtimes é um objeto {{nome: faixa}} com até {REQUIRES_LIST_MAX} itens "
            '(ex.: {"node": ">=18"}).',
        )
    services = _unique_strings(
        where,
        "requires.services",
        raw.get("services", []),
        _ListRule(SERVICE_RE, SERVICES_MAX, 'use nomes curtos em minúsculas (ex.: "postgres").'),
    )
    return {"python": python, "binaries": binaries, "runtimes": dict(runtimes), "services": services}


def requirement_name(req: str) -> str:
    """Nome da distribuição de um requisito já validado: `"psycopg[binary]>=3.1"` → `"psycopg"`."""
    match = REQUIREMENT_RE.fullmatch(req)
    if match is None:
        raise ManifestError(f"requisito inválido: {req!r}.")
    return match["name"]


def validate_platforms(where: str, raw: object) -> list[str]:
    """`platforms` validado, na ordem canônica (`darwin`, `linux`, `windows`)."""
    if (
        not isinstance(raw, list)
        or not raw
        or any(not isinstance(item, str) or item not in PLATFORMS for item in raw)
        or len(set(raw)) != len(raw)
    ):
        _fail(where, f"platforms tem que ser uma lista não vazia, sem repetição, de: {', '.join(PLATFORMS)}.")
    return [name for name in PLATFORMS if name in raw]


def current_platform() -> str:
    """Nome da plataforma no vocabulário de `platforms`: `darwin`, `linux`, `windows`
    (ou o `sys.platform` cru, num sistema fora dos três)."""
    if sys.platform.startswith(("win", "cygwin", "msys")):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _homepage(where, raw):
    if raw is None:
        return None
    ok = isinstance(raw, str) and len(raw) <= HOMEPAGE_MAX_CHARS and URL_RE.fullmatch(raw) is not None
    if ok:
        parts = urlsplit(raw)
        ok = bool(parts.hostname) and "@" not in parts.netloc
    if not ok:
        _fail(
            where,
            f"homepage tem que ser uma URL https:// sem usuário nem senha, com até {HOMEPAGE_MAX_CHARS} caracteres.",
        )
    return raw


def validate_license(where: str, raw: object) -> str | None:
    """`license` (identificador ou expressão SPDX curta, como `MIT OR Apache-2.0`) ou `None`."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not LICENSE_RE.fullmatch(raw) or not raw.strip():
        _fail(where, "license tem que ser um identificador SPDX (ex.: MIT), com até 128 caracteres.")
    return raw


def _author(where, raw):
    if raw is None:
        return None
    if not isinstance(raw, str) or not 0 < len(raw.strip()) <= AUTHOR_MAX_CHARS or not raw.isprintable():
        _fail(where, f"author tem que ser texto, com até {AUTHOR_MAX_CHARS} caracteres.")
    return raw.strip()


def _optional_fields(where, raw):
    """Campos opcionais do topo, normalizados; `metadata` é conferido e descartado."""
    if "metadata" in raw and not isinstance(raw["metadata"], dict):
        _fail(where, "metadata tem que ser um objeto JSON (o core não lê o conteúdo).")
    if raw.get("engines", {}) != {}:
        _fail(where, ENGINES_RETIRED)
    keywords = _unique_strings(
        where,
        "keywords",
        raw.get("keywords", []),
        _ListRule(KEYWORD_RE, KEYWORDS_MAX, "use minúsculas, dígitos e hífen."),
    )
    platforms = raw.get("platforms")
    return {
        "homepage": _homepage(where, raw.get("homepage")),
        "license": validate_license(where, raw.get("license")),
        "author": _author(where, raw.get("author")),
        "keywords": keywords,
        "platforms": None if platforms is None and "platforms" not in raw else validate_platforms(where, platforms),
        "requires": validate_requires(where, raw.get("requires", {})),
    }


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
    optional = _optional_fields(ident, raw)
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
        "permissions": validate_permissions(ident, raw.get("permissions", {})),
        **optional,
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
    """Por que o plugin não roda aqui (`sdk_api`, `requires_getbrolls` ou `platforms`), ou `None`."""
    if manifest["sdk_api"] != SDK_API:
        return f"sdk_api {manifest['sdk_api']} não é suportado; esta versão do get-brolls fala sdk_api {SDK_API}."
    if not satisfies(version, manifest["requires_getbrolls"]):
        return f"exige get-brolls {manifest['requires_getbrolls']}; instalado: {version}."
    platforms = manifest.get("platforms")
    if platforms is not None and current_platform() not in platforms:
        return f"platforms {', '.join(platforms)}: não roda neste sistema ({current_platform()})."
    return None
