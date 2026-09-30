"""Grafia única dos tipos de extensão que esta versão do SDK carrega.

O manifesto usa o plural (`contributes.providers`); a CLI, o registro e o scaffold
usam o singular (`provider`). Todo lugar que precisa de um dos dois deriva daqui.
Só biblioteca padrão: módulo folha, importável por qualquer outro do SDK.
"""

from collections.abc import Mapping

SINGULAR_TO_PLURAL: dict[str, str] = {
    "provider": "providers",
    "preset": "presets",
    "route": "routes",
    "command": "commands",
    "exporter": "exporters",
    "resolver": "resolvers",
}
PLURAL_TO_SINGULAR: dict[str, str] = {plural_kind: kind for kind, plural_kind in SINGULAR_TO_PLURAL.items()}
SUPPORTED_SINGULAR: tuple[str, ...] = tuple(SINGULAR_TO_PLURAL)
SUPPORTED_PLURAL: tuple[str, ...] = tuple(SINGULAR_TO_PLURAL.values())


def plural(kind: str) -> str:
    """`provider` → `providers`; `ValueError` para um tipo que o SDK não carrega."""
    try:
        return SINGULAR_TO_PLURAL[kind]
    except (KeyError, TypeError):
        raise ValueError(f"tipo de extensão desconhecido: {kind!r}; use {', '.join(SUPPORTED_SINGULAR)}.") from None


def singular(kind: str) -> str:
    """`providers` → `provider`; `ValueError` para um tipo que o SDK não carrega."""
    try:
        return PLURAL_TO_SINGULAR[kind]
    except (KeyError, TypeError):
        raise ValueError(f"tipo de extensão desconhecido: {kind!r}; use {', '.join(SUPPORTED_PLURAL)}.") from None


def contributed(contributes: Mapping[str, list[str]]) -> list[str]:
    """Tipos (no singular, em ordem alfabética) com ao menos um nome em `contributes`.

    Tipos reservados para versões futuras (`engines`, `catalogs`…) ficam de fora:
    esta versão do SDK não os carrega."""
    return sorted(
        PLURAL_TO_SINGULAR[kind] for kind, names in contributes.items() if kind in PLURAL_TO_SINGULAR and names
    )
