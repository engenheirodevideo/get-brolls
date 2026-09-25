"""Checagens de contrato para quem escreve plugin — as mesmas que `plugins --action check` roda.

Cada `check_*` levanta `AssertionError` com uma frase que diz o que corrigir; use
direto no `unittest` do seu plugin, ou `check_plugin(pasta)` para conferir tudo o
que o `register(api)` registra, sem instalar nada.
"""

import inspect
from pathlib import Path

from .contracts import NAME_RE, ROUTE_STAGES, CommandSpec, ProviderCapabilities


def _fail(message):
    raise AssertionError(message)


def _accepts(fn, count):
    try:
        inspect.signature(fn).bind(*range(count))
    except TypeError:
        return False
    except ValueError:
        return True  # built-in sem assinatura legível: o registro já conferiu que é chamável
    return True


def _named(kind, obj):
    name = getattr(obj, "name", None)
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        _fail(f"{kind}: name tem que ter 2–32 caracteres a-z, 0-9 e _, começando por letra; veio {name!r}.")
    return name


def check_provider(provider):
    name = _named("Provider", provider)
    if not isinstance(getattr(provider, "capabilities", None), ProviderCapabilities):
        _fail(f"Provider {name}: capabilities tem que ser ProviderCapabilities.")
    from .registry import PluginProvider

    for method, count in (("search", 3), ("resolve", 1), ("refresh", 1)):
        # No registro, o provider de plugin vira um `PluginProvider` (snapshot): o que
        # se confere é o método do plugin capturado ali, não o repasse do snapshot.
        fn = provider.bound(method) if isinstance(provider, PluginProvider) else getattr(provider, method, None)
        if not callable(fn) or not _accepts(fn, count):
            _fail(f"Provider {name}: falta {method}() com {count} argumento(s) além de self.")


def check_route(route):
    name = _named("Rota", route)
    if getattr(route, "stage", None) not in ROUTE_STAGES:
        _fail(f'Rota {name}: stage tem que ser "preview" ou "fetch".')
    prepare = getattr(route, "prepare", None)
    if not callable(prepare) or not _accepts(prepare, 2):
        _fail(f"Rota {name}: falta prepare(item, workdir).")


def check_command(spec):
    if not isinstance(spec, CommandSpec):
        _fail("Comando: registre com api.command(nome, handler, help).")
    _named("Comando", spec)
    if not isinstance(spec.help, str) or not spec.help.strip():
        _fail(f"Comando {spec.name}: help não pode ser vazio.")
    if not callable(spec.handler) or not _accepts(spec.handler, 2):
        _fail(f"Comando {spec.name}: o handler tem que aceitar (args, ctx).")


def check_registry(registry, owner):
    """Roda as checagens em tudo o que `owner` registrou; devolve os nomes conferidos."""
    owned = registry.owned_by(owner)
    for name in owned["provider"]:
        check_provider(registry.provider(name))
    for name in owned["route"]:
        check_route(registry.route(name))
    for key in owned["command"]:
        check_command(registry.command(*key.split(":", 1)))
    return {
        "providers": owned["provider"],
        "presets": owned["preset"],
        "routes": owned["route"],
        "commands": [key.split(":", 1)[1] for key in owned["command"]],
        # Exportadores e resolvedores já passaram pelas checagens do registro.
        "exporters": owned["exporter"],
        "resolvers": owned["resolver"],
    }


def check_plugin(folder):
    """Manifesto + `register(api)` num registro descartável + as checagens acima."""
    from . import loader

    return loader.trial_load(Path(folder).resolve())
