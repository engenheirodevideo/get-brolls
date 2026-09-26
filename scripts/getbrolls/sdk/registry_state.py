"""O registro já montado neste processo, ou nenhum: o estado que `registry` e `loader` dividem.

Módulo folha. O `registry` monta o registro chamando o `loader`; o `loader`, depois de
`enable`/`pin`/`disable`, descarta o registro montado daqui, sem importar o `registry`.
"""

from typing import Any

_STATE: dict[str, Any] = {"registry": None}


def current() -> Any:
    """O registro montado, ou `None` se nenhum foi montado desde o último descarte."""
    return _STATE["registry"]


def remember(registry: Any) -> None:
    """Guarda `registry` como o registro do processo."""
    _STATE["registry"] = registry


def forget() -> None:
    """Descarta o registro montado: a próxima consulta monta um novo."""
    _STATE["registry"] = None
