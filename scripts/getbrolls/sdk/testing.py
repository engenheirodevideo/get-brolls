"""Checagens de contrato para quem escreve plugin — as mesmas que `plugins --action check` roda.

Cada `check_*` levanta `AssertionError` com uma frase que diz o que corrigir; use
direto no `unittest` do seu plugin, ou `check_plugin(pasta)` para conferir tudo o
que o `register(api)` registra, sem instalar nada.
"""

import inspect
from pathlib import Path

from .contracts import (
    NAME_RE,
    RESOLVER_KINDS,
    ROUTE_STAGES,
    CommandSpec,
    ExporterSpec,
    ProviderCapabilities,
    ResolverSpec,
)


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


def check_exporter(spec):
    """Forma do exportador e, depois, uma exportação de verdade com o plano de exemplo
    (`exporters.sample_plan()`), conferida pelo MESMO validador do core."""
    from .exporters import ExportValidationError, sample_plan, validate_export_result

    if not isinstance(spec, ExporterSpec):
        _fail("Exportador: registre com api.exporter(nome, export, description).")
    _named("Exportador", spec)
    if not isinstance(spec.description, str) or not spec.description.strip():
        _fail(f"Exportador {spec.name}: description não pode ser vazia.")
    if not callable(spec.export) or not _accepts(spec.export, 2):
        _fail(f"Exportador {spec.name}: export tem que aceitar (plan, options).")
    plan = sample_plan()
    result = spec.export(plan, {"args": {}})
    try:
        validated = validate_export_result(result)
    except ExportValidationError as exc:
        _fail(f"Exportador {spec.name}: com o plano de exemplo, {exc}")
    from ..export_folder import RESERVED_NAMES

    reserved = sorted(path for path in validated.files if path.casefold() in RESERVED_NAMES)
    if reserved:
        _fail(f"Exportador {spec.name}: {', '.join(reserved)} é nome reservado do get-brolls na pasta do export.")


def check_resolver(spec):
    if not isinstance(spec, ResolverSpec):
        _fail("Resolvedor: registre com api.resolver(nome, resolve, kinds).")
    _named("Resolvedor", spec)
    kinds = spec.kinds
    if (
        not kinds
        or not isinstance(kinds, tuple)
        or not set(kinds) <= set(RESOLVER_KINDS)
        or len(set(kinds)) != len(kinds)
    ):
        _fail(
            f"Resolvedor {spec.name}: kinds tem que ser uma lista não vazia, sem repetição, de {', '.join(RESOLVER_KINDS)}."
        )
    if not callable(spec.resolve) or not _accepts(spec.resolve, 2):
        _fail(f"Resolvedor {spec.name}: resolve tem que aceitar (kind, name).")


def check_registry(registry, owner):
    """Roda as checagens em tudo o que `owner` registrou; devolve os nomes conferidos."""
    owned = registry.owned_by(owner)
    for name in owned["provider"]:
        check_provider(registry.provider(name))
    for name in owned["route"]:
        check_route(registry.route(name))
    for key in owned["command"]:
        check_command(registry.command(*key.split(":", 1)))
    for name in owned["exporter"]:
        check_exporter(registry.exporter(name))
    for name in owned["resolver"]:
        check_resolver(registry.resolver(name))
    return {
        "providers": owned["provider"],
        "presets": owned["preset"],
        "routes": owned["route"],
        "commands": [key.split(":", 1)[1] for key in owned["command"]],
        "exporters": owned["exporter"],
        "resolvers": owned["resolver"],
    }


def check_plugin(folder):
    """Manifesto + `register(api)` num registro descartável + as checagens acima.

    É o `plugins --action check`: valida o manifesto e executa o `register()` contra
    um registro descartável com os built-ins, para pegar colisão de nome sem habilitar
    nada."""
    from .. import presets, providers
    from . import guard, loader
    from .manifest import MANIFEST_NAME, compatibility_problem, read_manifest
    from .registry import Registry

    folder = Path(folder).resolve()
    manifest = read_manifest(folder, require_folder_match=False)
    problem = compatibility_problem(manifest)
    if problem:
        raise ValueError(f"Plugin {manifest['id']}: {problem}")
    # O mesmo que o `install` recusaria: VCS aninhado, link simbólico e bytecode.
    nested = loader.nested_vcs(folder)
    if nested is not None:
        raise ValueError(f"Plugin {manifest['id']}: pasta de controle de versão aninhada ({nested}); tire-a da pasta.")
    content = loader.content_problem(folder)
    if content is not None:
        raise ValueError(f"Plugin {manifest['id']}: {loader.content_reason(content, 'rode o check de novo')}")
    registry = Registry()
    providers.register_builtins(registry)
    presets.register_builtins(registry)
    plugin_id = manifest["id"]
    loader.disable_bytecode()
    # Mesmo isolamento do carregamento (BaseException, tipo seguro, sem cadeia). Aqui,
    # e só aqui, o texto de um tipo embutido exato (RuntimeError('boom')) também aparece:
    # `check` é a ferramenta de quem escreve o plugin, rodando a pasta que ele apontou.
    failure = guard.attempt(plugin_id, loader.register_plugin, folder, manifest, registry, builtin_text=True).failure
    if failure is not None:
        detail = f": {guard.without_prefix(plugin_id, failure.text)}" if failure.text else "."
        raise ValueError(f"Plugin {plugin_id}: {failure.type_name}{detail}") from None
    checked = guard.attempt(plugin_id, check_registry, registry, plugin_id, builtin_text=True)
    if checked.failure is not None:
        detail = checked.failure.text or f"a checagem falhou ({checked.failure.type_name})."
        raise ValueError(f"Plugin {plugin_id}: contrato: {guard.without_prefix(plugin_id, detail)}") from None
    return {
        "ok": True,
        **loader.plugin_preview(manifest, folder),
        "manifest_file": MANIFEST_NAME,
        "contracts": checked.value,
    }
