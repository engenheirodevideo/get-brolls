"""Registro dos schemas do core: o nome, o campo de versão e a versão, tudo lido dos arquivos.

A versão de cada família mora no próprio schema (`schemas/<nome>.schema.json`), nunca num literal do
código: quem grava ou lê passa `version(nome)` para `versioning`. Os nomes vêm dos arquivos da pasta
`schemas/` (sem lista à mão), e `version` é construído sobre `load`, então herda o cache dele.
"""

import functools
import json
import re

from .. import _paths

_SUFFIX = ".schema.json"
_FAMILY = re.compile(r"getbrolls\.([a-z][a-z0-9_]*)/([1-9][0-9]*)")

# Campo que carrega a versão de cada schema. Duas convenções gravadas em disco convivem na 2.6:
# `schema_version` (inteiro) e `schema` (`getbrolls.<família>/<N>`); o resto são campos que já existiam.
VERSION_FIELD: dict[str, str] = {
    "analysis_index": "schema",
    "brief": "version",
    "candidate": "schema_version",
    "capabilities": "schema_version",
    "client": "schema",
    "clients": "schema",
    "components": "schema",
    "draft": "schema",
    "export_marker": "schema_version",
    "export_plan": "export_version",
    "handoff": "schema",
    "handoff_return": "schema",
    "instagram_summary": "schema_version",
    "library_index": "schema_version",
    "manifest": "schema_version",
    "markers": "schema",
    "marketplace_index": "schema",
    "marketplaces": "schema",
    "media": "schema",
    "pending_transaction": "schema_version",
    "plugin_manifest": "sdk_api",
    "plugins_state": "schema",
    "profile_toml": "schema_version",
    "project": "schema",
    "queue": "schema_version",
    "recipe": "schema",
    "recipe_lock": "schema",
    "references": "schema_version",
    "review_decisions": "templateVersion",
    "roteiro_state": "schema_version",
    "rules_block": "version",
    "runtime_marker": "schema",
    "scenes": "schema",
    "silence": "schema",
    "source_index": "schema_version",
    "speakers": "schema",
    "transcript": "schema",
    "trusted_profiles": "schema_version",
    "visual": "schema",
}

# Segundo campo fixado de um schema: o formato do layout do projeto e o formato do plano de cena do roteiro.
# Mesma leitura de `version`; `plan_version` não tem teto (o exporter não recusa por ele), a versão dele é o piso.
SECONDARY_FIELDS: dict[str, tuple[str, ...]] = {
    "project": ("layout",),
    "export_plan": ("plan_version",),
}


def names() -> tuple[str, ...]:
    """Os nomes dos schemas publicados, em ordem alfabética, lidos dos arquivos de `schemas/`."""
    try:
        entries = [entry.name for entry in (_paths.data_root() / "schemas").iterdir()]
    except OSError:
        raise _paths.DataRootError(f"Falta a pasta de dados schemas do getbrolls. {_paths.DATA_REINSTALL}") from None
    return tuple(sorted(entry.removesuffix(_SUFFIX) for entry in entries if entry.endswith(_SUFFIX)))


def __getattr__(attr: str):
    # `NAMES` continua existindo para quem o importava, mas é lido dos arquivos no acesso: importar o
    # módulo nunca toca a pasta de dados (uma instalação quebrada ainda chega ao `doctor`).
    if attr == "NAMES":
        return names()
    raise AttributeError(f"module {__name__!r} has no attribute {attr!r}")


@functools.cache
def load(name):
    """O schema `name` (um de `names()`) como dict, lido uma vez por processo."""
    if name not in names():
        raise ValueError(f"Schema desconhecido: {name}")
    return json.loads(_paths.data_path("schemas", f"{name}{_SUFFIX}").read_text(encoding="utf-8"))


def version_field(name: str) -> str:
    """O campo que declara a versão do schema `name` (`schema_version`, `schema`, `version`…)."""
    try:
        return VERSION_FIELD[name]
    except KeyError:
        raise ValueError(f"Schema desconhecido: {name}") from None


def _whole(value) -> bool:
    return type(value) is int and value >= 1


def _declared(prop: dict) -> int | None:
    """A versão que a propriedade declara: `maximum`, senão `const`, senão o `N` de `getbrolls.<família>/N`."""
    if "maximum" in prop:
        return prop["maximum"] if _whole(prop["maximum"]) else None
    const = prop.get("const")
    if _whole(const):
        return const
    if isinstance(const, str):
        found = _FAMILY.fullmatch(const)
        return int(found.group(2)) if found else None
    options = prop.get("enum")
    if isinstance(options, list) and options and all(_whole(option) for option in options):
        return max(options)
    return None


def version(name: str, field: str | None = None) -> int:
    """A versão corrente de `name`, lida do schema; `field` escolhe um segundo campo fixado (`SECONDARY_FIELDS`)."""
    primary = version_field(name)
    secondary = SECONDARY_FIELDS.get(name, ())
    if field is None:
        field = primary
    elif field != primary and field not in secondary:
        raise ValueError(f"{name} não tem {field} como campo de versão")
    prop = load(name).get("properties", {}).get(field)
    found = _declared(prop) if isinstance(prop, dict) else None
    if found is None and field in secondary and isinstance(prop, dict) and "maximum" not in prop:
        # Sem teto nem valor único, a versão corrente é o `default` e, na falta dele, o piso
        # (o plano de cena aceita `plan_version` do piso em diante).
        floor = prop.get("default") if _whole(prop.get("default")) else prop.get("minimum")
        found = floor if _whole(floor) else None
    if found is None:
        raise ValueError(f"O schema {name} não declara uma versão legível em {field}")
    return found
