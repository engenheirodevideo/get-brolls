"""O registro já montado neste processo, ou nenhum: o estado que `registry` e `loader` dividem.

Módulo folha. O `registry` monta o registro chamando o `loader`; o `loader`, depois de
`enable`/`pin`/`disable`, descarta o registro montado daqui, sem importar o `registry`.
Módulos do core que o `registry` importa para registrar os built-ins (`presets`) pedem o
registro por `get()`, também sem importar o `registry`.
"""

from collections.abc import Callable
from typing import Any


def _not_installed() -> Any:
    """Construtor enquanto `registry` não instalou o seu (nunca depois de importar `getbrolls.sdk`)."""
    raise RuntimeError("getbrolls.sdk.registry ainda não foi importado.")


_STATE: dict[str, Any] = {"registry": None, "build": _not_installed}


def current() -> Any:
    """O registro montado, ou `None` se nenhum foi montado desde o último descarte."""
    return _STATE["registry"]


def remember(registry: Any) -> None:
    """Guarda `registry` como o registro do processo."""
    _STATE["registry"] = registry


def forget() -> None:
    """Descarta o registro montado: a próxima consulta monta um novo."""
    _STATE["registry"] = None


def set_builder(build: Callable[[], Any]) -> None:
    """Guarda a função que devolve o registro do processo (o `registry` a instala ao ser importado)."""
    _STATE["build"] = build


def get() -> Any:
    """O registro do processo, montado na primeira consulta (`registry.get_registry`).

    Raises:
        RuntimeError: nenhuma função instalada; não acontece depois de importar `getbrolls.sdk`.
    """
    return _STATE["build"]()
