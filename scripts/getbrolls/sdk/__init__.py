"""SDK de extensões do Get B-rolls.

Só o que este pacote exporta é contrato público; o resto de `getbrolls` é
interno e pode mudar sem aviso. `SDK_API` muda só em major.
"""

from .contracts import (
    RESOLVER_KINDS,
    SDK_API,
    CommandContext,
    CommandSpec,
    Exporter,
    ExporterSpec,
    ExportResult,
    MediaRequest,
    PluginError,
    Preset,
    Provider,
    ProviderCapabilities,
    Resolver,
    ResolverHit,
    ResolverSpec,
    Route,
    RouteResult,
)
from .registry import get_registry

__all__ = [
    "RESOLVER_KINDS",
    "SDK_API",
    "CommandContext",
    "CommandSpec",
    "ExportResult",
    "Exporter",
    "ExporterSpec",
    "MediaRequest",
    "PluginError",
    "Preset",
    "Provider",
    "ProviderCapabilities",
    "Resolver",
    "ResolverHit",
    "ResolverSpec",
    "Route",
    "RouteResult",
    "get_registry",
]
