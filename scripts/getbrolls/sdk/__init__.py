"""SDK de extensões do Get B-rolls.

Só o que este pacote exporta é contrato público; o resto de `getbrolls` é
interno e pode mudar sem aviso. `SDK_API` muda só em major.
"""

from .contracts import (
    SDK_API,
    CommandContext,
    CommandSpec,
    PluginError,
    Preset,
    Provider,
    ProviderCapabilities,
    Route,
    RouteResult,
)
from .registry import get_registry

__all__ = [
    "SDK_API",
    "CommandContext",
    "CommandSpec",
    "PluginError",
    "Preset",
    "Provider",
    "ProviderCapabilities",
    "Route",
    "RouteResult",
    "get_registry",
]
