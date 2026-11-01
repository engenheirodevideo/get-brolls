"""SDK de extensões do getbrolls.

Só o que este pacote exporta é contrato público; o resto de `getbrolls` é
interno e pode mudar sem aviso. `SDK_API` muda só em major.

Os nomes públicos são resolvidos sob demanda (PEP 562): importar o pacote não
carrega nenhum submódulo. Em particular, `getbrolls.sdk.registry`, que instala o
construtor do registro, só é importado no primeiro acesso a `get_registry`.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name == "get_registry":
        from .registry import get_registry as value  # pylint: disable=import-outside-toplevel
    else:
        from . import contracts  # pylint: disable=import-outside-toplevel

        value = contracts.__dict__[name]
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
