"""Enums abertos: valor que esta versão não conhece vira o aviso `ENUM_UNKNOWN`, nunca erro.

Abertos (contrato de formatos, §5): tipo de componente, nível, ambiente, `origin.class`, papel
de cena, transição, análise, `stages`, `needs_human.kind` e `reason_code`. Fechados: `exec`,
canal de revisão, `licence`, `rights_basis` e `media.role`. Cada campo aberto entra
em `OPEN_FIELDS` quando o código passa a lê-lo; o aviso sai uma vez por processo, campo e valor,
e quem chama pula o item.
"""

import logging

from . import logs

_log = logs.get("open_enums")

# Campo → como a mensagem o chama.
OPEN_FIELDS = {"component_kind": "tipo de componente", "tier": "nível"}
_SEEN: set[tuple[str, str]] = set()


def known(field: str, value: str, allowed) -> bool:
    """`value` está em `allowed`? Senão, `ENUM_UNKNOWN` (uma vez) e `False`: quem chama pula o item."""
    if value in allowed:
        return True
    if (field, value) not in _SEEN:
        _SEEN.add((field, value))
        from . import runtime  # tardio: o runtime traz logs e http

        label = OPEN_FIELDS.get(field, field)
        runtime.record_warning(
            "ENUM_UNKNOWN",
            f'{label} "{value}" é desconhecido nesta versão do getbrolls: esse item não é usado. Atualize o getbrolls.',
        )
        logs.event(_log, logging.INFO, "enum_unknown", field=field)
    return False


def reset() -> None:
    """Esquece os avisos já dados (testes no mesmo processo)."""
    _SEEN.clear()
