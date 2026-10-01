"""`plugins --action install --id <id>@<marketplace>`: instalar pela entrada do índice fixado.

O índice em cache (`marketplace.load_index`, sha256 conferido) diz de onde vem o
plugin (`source`: repositório, commit, subpasta) e o que ele tem que ser
(`content_sha256` e os campos do manifesto). Nada aqui relaxa o fluxo de dois
passos do `install`:

- sem `--yes` a resposta é sempre a prévia, mesmo com `--expect`; o índice só
  pré-preenche o `expect` e o comando `next`, que a pessoa (ou o agente, depois de
  mostrar a prévia a ela) roda numa chamada separada;
- o sha256 do conteúdo materializado diferente do `content_sha256` da entrada é
  recusa dura (sem prévia nova), assim como um manifesto que diverge da entrada
  em `id`, `version`, `sdk_api`, `requires_getbrolls`, `permissions`,
  `contributes`, `requires`, `platforms` ou `license`; divergência só na
  `description` vira aviso;
- entrada retirada (`yanked`) é recusada; `deprecated` vira aviso; um id antigo
  de `renames` é recusado com o comando do id atual (nunca troca sozinho);
- `sdk_api`, `requires_getbrolls` e `platforms` da entrada são conferidos antes
  de buscar qualquer coisa; o teto de marketplaces do perfil vale antes de tudo.

O `tier` da entrada é o que o índice DECLARA: só o índice oficial (nome e origem
fixada, `marketplace.is_official`) sai com `tier_verified: true`; nos outros a saída
diz "declarado pelo marketplace, não verificado". Nunca afrouxa nenhuma regra.

Um plugin que pede qualquer permissão só é confirmado com um valor derivado (o sha256
do conteúdo amarrado às permissões), que o índice não traz e só a prévia mostra — o
mesmo do update que acrescenta permissão. Limite conhecido: esse valor é derivado,
não secreto; um agente pode calculá-lo. Ele atrapalha quem copia o sha do índice,
mas não garante leitura humana: a prévia em chamada separada e a skill orientam
mostrá-la à pessoa antes de confirmar. Plugin sem permissão confirma com o sha do índice.
"""

import hashlib
import json

from .. import _paths
from ..errors import UsageError
from . import git_source, loader, marketplace
from . import install as plugin_install
from .manifest import compatibility_problem
from .marketplace import not_allowed
from .marketplace_index import BATCH_TIERS, manifest_mismatches, manifest_warnings, parse_plugin_ref, resolve_rename

INSTALL_PERMISSIONS_NOTE = (
    "O plugin pede permissões (veja permissions_added): mostre-as à pessoa com a origem, o commit e os "
    "arquivos. O --expect desta prévia é um valor derivado que o índice não traz: atrapalha quem copia o sha "
    "do índice, mas não garante leitura humana. Com o ok dela, rode o comando de next. " + loader.NOT_SANDBOX
)
PREVIEW_NOTE = (
    "Mostre esta prévia à pessoa (permissões, origem, commit e arquivos) antes de confirmar: o --expect "
    "vem pré-preenchido do índice, e copiá-lo sem mostrar a prévia pula a revisão humana. Com o ok dela, "
    "rode o comando de next. " + loader.NOT_SANDBOX
)


def _search_hint(plugin_id):
    return _paths.cli_hint("plugins", "--action", "search", "--query", plugin_id)


def find_entry(index, plugin_id, name):
    """A entrada de `plugin_id` no índice; id renomeado ou ausente vira `ValueError` com a dica."""
    for entry in index["plugins"]:
        if entry["id"] == plugin_id:
            return entry
    current = resolve_rename(index, plugin_id)
    if current is not None:
        raise ValueError(
            f"O plugin {plugin_id} foi renomeado para {current} no marketplace {name}; use "
            + _paths.cli_hint("plugins", "--action", "install", "--id", f"{current}@{name}")
            + "."
        )
    raise ValueError(f"O marketplace {name} não tem o plugin {plugin_id}; procure com {_search_hint(plugin_id)}.")


def _yanked(plugin_id, name):
    return ValueError(
        f"O plugin {plugin_id} foi retirado (yanked) do marketplace {name}; não é instalado nem atualizado por ele."
    )


def entry_warnings(entry):
    """Avisos que a entrada carrega por si (hoje, `deprecated`)."""
    deprecated = entry.get("deprecated")
    if not deprecated:
        return []
    replacement = f" Substituto sugerido: {deprecated['replacement']}." if deprecated.get("replacement") else ""
    return [f"O plugin {entry['id']} está obsoleto (deprecated) no índice: {deprecated['reason']}.{replacement}"]


def check_compatible(entry):
    """`sdk_api`, `requires_getbrolls` e `platforms` da entrada, conferidos antes de qualquer busca."""
    problem = compatibility_problem(
        {
            "sdk_api": entry["sdk_api"],
            "requires_getbrolls": entry["requires_getbrolls"],
            "platforms": entry["platforms"],
        }
    )
    if problem:
        raise ValueError(f"Plugin {entry['id']}: {problem}")


def entry_spec(entry, pin):
    """A origem git da entrada; `"."` é o repositório do próprio marketplace."""
    source = entry["source"]
    spec = git_source.parse_source(
        marketplace.resolve_repo(source["repo"], pin),
        commit=source["commit"],
        ref=source["ref"],
        subdir=source["subdir"],
    )
    if not isinstance(spec, git_source.GitSource):
        raise ValueError(
            f"A origem do plugin {entry['id']} no marketplace {pin.name} não é mais um repositório git; "
            f"rode plugins --action marketplace-update --marketplace {pin.name}."
        )
    return spec


def origin_extra(entry, pin):
    """O que a origem gravada em `plugins.json` ganha de quem instala pelo marketplace."""
    return {"marketplace": pin.name, "tier": entry["tier"], "index_commit": pin.commit}


def verifier(entry, warnings, extra_check=None):
    """`verify(manifest, sha256)` do `install_from`/`update_from`: o conteúdo materializado
    tem que ser o da entrada. Os avisos (`description`) entram em `warnings`."""

    def verify(manifest, sha):
        if sha != entry["content_sha256"]:
            raise ValueError(
                f"O conteúdo do plugin {entry['id']} no commit {entry['source']['commit'][:12]} não bate com o "
                f"content_sha256 do índice (índice {entry['content_sha256'][:12]}…, conteúdo {sha[:12]}…); "
                "recusado. Avise quem mantém o marketplace."
            )
        mismatches = manifest_mismatches(entry, manifest)
        if mismatches:
            raise ValueError(
                f"O manifesto do plugin {entry['id']} não bate com a entrada do índice; recusado. "
                + "; ".join(mismatches)
            )
        warnings.extend(f"Divergência com o índice (só aviso): {item}" for item in manifest_warnings(entry, manifest))
        if extra_check is not None:
            extra_check(manifest, sha)

    return verify


def resolve(ref):
    """`(plugin_id, Pin, entrada)` de `<id>@<marketplace>`, com teto, índice e entrada conferidos."""
    plugin_id, name = parse_plugin_ref(ref)
    if not marketplace.allowed(name):
        raise not_allowed(name)
    pin, index = marketplace.load_index(name)
    entry = find_entry(index, plugin_id, name)
    if entry["yanked"]:
        raise _yanked(plugin_id, name)
    check_compatible(entry)
    return plugin_id, pin, entry


def market_block(entry, pin):
    """O bloco `marketplace` da resposta: nome, commit do índice e tier da entrada (declarado,
    com `tier_verified` só no índice oficial)."""
    return {"name": pin.name, "commit": pin.commit, **marketplace.tier_view(entry["tier"], _official(pin))}


def _official(pin):
    return marketplace.is_official(pin.name, pin.source)


def with_warnings(plugin, warnings):
    """`plugin` com `warnings` somados aos avisos que ele já traz."""
    if warnings:
        plugin = {**plugin, "warnings": [*plugin.get("warnings", []), *warnings]}
    return plugin


def install(ref: str, confirm: bool, expect: str | None) -> dict:
    """Prévia (sem `confirm`, mesmo com `expect`) ou instalação de `<id>@<marketplace>`.

    Plugin que pede permissão: o `expect` exigido é o derivado de `_confirmation(entry, None)`
    (o sha do índice sozinho é recusado); sem permissão, o sha do índice."""
    _plugin_id, pin, entry = resolve(ref)
    required, added, increased = _confirmation(entry, None)
    if confirm:
        _check_update_expect(expect, required, entry, increased, installing=True)
    warnings = entry_warnings(entry)
    result = plugin_install.install_from(
        entry_spec(entry, pin),
        confirm,
        entry["content_sha256"] if confirm else None,
        verify=verifier(entry, warnings),
        origin_extra=origin_extra(entry, pin),
    )
    result = {**result, "plugin": with_warnings(result["plugin"], warnings), "marketplace": market_block(entry, pin)}
    if not confirm:
        result["expect"] = required
        result["permissions_added"] = added
        result["next"] = _paths.cli_hint("plugins", "--action", "install", "--id", ref, "--yes", "--expect", required)
        result["note"] = INSTALL_PERMISSIONS_NOTE if increased else PREVIEW_NOTE
    return result


def reject_source_with_ref(args_source, pin_flags):
    """`--source`/`--commit`/`--ref`/`--subdir` junto com `--id <id>@<marketplace>` é erro de uso."""
    used = [f"--{name}" for name, value in pin_flags.items() if value is not None]
    if args_source:
        used.insert(0, "--source")
    if used:
        raise UsageError(
            f"{', '.join(used)} não vale(m) com --id <id>@<marketplace>: a origem e o commit vêm do índice fixado."
        )


# --- update -----------------------------------------------------------------

PERMISSIONS_NOTE = (
    "As permissões aumentam nesta atualização (veja diff.permissions_added): mostre o diff à pessoa. O "
    "--expect desta prévia é um valor derivado que o índice não traz: atrapalha quem copia o sha do índice, "
    "mas não garante leitura humana. Com o ok dela, rode o comando de next. " + loader.NOT_SANDBOX
)
UPDATE_ALL_NOTE = (
    "Só prévia: nada foi atualizado. Rode o comando de cada plugin (a prévia dele mostra o diff), mostre-a à "
    "pessoa e confirme um por vez com --yes --expect. Aplicar em lote fica para uma versão futura."
)
_PERMISSIONS_DOMAIN = "getbrolls.permissions_increase/1"


def marketplace_of(plugin_id: str) -> str | None:
    """O marketplace gravado na origem de `plugin_id`, ou `None` (instalado por `--source` ou à mão)."""
    origin = (loader.read_state().get("sources") or {}).get(plugin_id) or {}
    return origin.get("marketplace")


def _update_target(plugin_id, origin):
    """`(Pin, entrada, spec)` da atualização de `plugin_id` pelo marketplace da origem gravada."""
    name = origin["marketplace"]
    if not marketplace.allowed(name):
        raise not_allowed(name)
    if name not in marketplace.read_state()["marketplaces"]:
        raise ValueError(
            f"O plugin {plugin_id} veio do marketplace {name}, que foi removido (marketplace removido): adicione-o "
            f"de novo com plugins --action marketplace-add, reinstale de outra origem ou remova com "
            f"plugins --action remove --id {plugin_id}."
        )
    pin, index = marketplace.load_index(name)
    entry = next((item for item in index["plugins"] if item["id"] == plugin_id), None)
    if entry is None:
        current = resolve_rename(index, plugin_id)
        if current is not None:
            raise ValueError(
                f"O plugin {plugin_id} foi renomeado para {current} no marketplace {name}; o update nunca troca o "
                f"id. Para seguir o novo: plugins --action remove --id {plugin_id} e depois "
                + _paths.cli_hint("plugins", "--action", "install", "--id", f"{current}@{name}")
                + "."
            )
        raise ValueError(f"O marketplace {name} não traz mais o plugin {plugin_id}; o instalado continua como está.")
    if entry["yanked"]:
        raise ValueError(
            f"O plugin {plugin_id} foi retirado (yanked) do marketplace {name}: não há atualização por ele, e a "
            f"versão instalada continua; considere plugins --action remove --id {plugin_id}."
        )
    check_compatible(entry)
    return pin, entry, entry_spec(entry, pin)


def _same_content(origin, spec):
    return (origin.get("source"), origin.get("commit"), origin.get("subdir")) == (spec.repo, spec.commit, spec.subdir)


def _installed_manifest(plugin_id):
    _row, _folder, manifest = loader.find(plugin_id)
    return manifest


def _permissions_token(sha, added):
    text = f"{_PERMISSIONS_DOMAIN}\n{sha}\n{json.dumps(added, sort_keys=True, ensure_ascii=False)}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _confirmation(entry, current):
    """`(expect exigido, permissions_added, aumentou?)`: com permissão nova (no install, `current`
    é `None` e toda permissão declarada conta), o `expect` é um valor derivado que o índice não
    traz e a prévia mostra (amarra o sha256 do conteúdo às permissões acrescentadas). É
    derivado, não secreto: quem tem o índice consegue calculá-lo."""
    added = loader.permissions_added(current["permissions"] if current else None, entry["permissions"])
    increased = any(added.values())
    sha = entry["content_sha256"]
    return (_permissions_token(sha, added) if increased else sha), added, increased


def _check_update_expect(expect, required, entry, increased, installing=False):
    if expect and increased and expect == entry["content_sha256"]:
        what = "Este plugin pede permissões" if installing else "Esta atualização acrescenta permissões"
        raise ValueError(
            f"{what}: o sha256 do índice não basta para confirmar. Rode a prévia (sem --yes), mostre as "
            "permissões à pessoa e confirme com o --expect que ela mostrar."
        )
    loader.check_expect(expect, required)


def update(plugin_id: str, confirm: bool, expect: str | None) -> dict:
    """Prévia (com o diff) ou atualização de um plugin instalado pelo marketplace, pela entrada
    do índice fixado (rode `marketplace-update` antes para ver novidade; nada de rede até materializar)."""
    origin = (loader.read_state().get("sources") or {}).get(plugin_id) or {}
    if not origin.get("marketplace"):
        raise ValueError(f"O plugin {plugin_id} não foi instalado por um marketplace.")
    pin, entry, spec = _update_target(plugin_id, origin)
    current = _installed_manifest(plugin_id)
    block = market_block(entry, pin)
    if _same_content(origin, spec):
        return {
            "updated": False,
            "up_to_date": True,
            "plugin": {"id": plugin_id, "version": current["version"] if current else None},
            "marketplace": block,
        }
    required, _added, increased = _confirmation(entry, current)
    if confirm:
        _check_update_expect(expect, required, entry, increased)
    warnings = entry_warnings(entry)
    result = plugin_install.update_from(
        plugin_id,
        spec,
        confirm,
        entry["content_sha256"] if confirm else None,
        verify=verifier(entry, warnings),
        origin_extra=origin_extra(entry, pin),
    )
    result = {**result, "plugin": with_warnings(result["plugin"], warnings), "marketplace": block}
    if not confirm:
        result["expect"] = required
        result["next"] = _paths.cli_hint(
            "plugins", "--action", "update", "--id", plugin_id, "--yes", "--expect", required
        )
        result["note"] = PERMISSIONS_NOTE if increased else PREVIEW_NOTE
    return result


def _plan_row(plugin_id, origin):
    pin, entry, spec = _update_target(plugin_id, origin)
    if _same_content(origin, spec):
        return None
    current = _installed_manifest(plugin_id)
    _required, added, increased = _confirmation(entry, current)
    return {
        "id": plugin_id,
        "marketplace": pin.name,
        "from": current["version"] if current else None,
        "to": entry["version"],
        **marketplace.tier_view(entry["tier"], _official(pin)),
        "permissions_added": added,
        "permissions_increased": increased,
        "auto_update_eligible": entry["tier"] in BATCH_TIERS and _official(pin) and not increased,
        "warnings": entry_warnings(entry),
        "command": _paths.cli_hint("plugins", "--action", "update", "--id", plugin_id),
    }


def update_all(confirm: bool, expect: str | None) -> dict:
    """Prévia das atualizações de todos os plugins instalados por marketplace, sem rede nem mudança.

    Nesta versão só lista: cada linha traz versão de/para, tier, permissões
    acrescentadas e o comando da prévia por id. `auto_update_eligible` é a política
    exibida (tier `official`/`verified` verificado — só no índice oficial — e nenhuma
    permissão nova); a comunidade e o tier só declarado nunca são elegíveis. Confirmar
    em lote (`--yes`/`--expect`) é erro de uso."""
    if confirm or expect:
        raise UsageError(
            "plugins --action update --all só mostra a prévia nesta versão; confirme cada plugin com "
            "plugins --action update --id <id> (prévia) e depois --yes --expect."
        )
    sources = loader.read_state().get("sources") or {}
    plugins, up_to_date, skipped, outside = [], [], [], []
    for plugin_id in sorted(sources):
        origin = sources[plugin_id] or {}
        if not origin.get("marketplace"):
            outside.append(plugin_id)
            continue
        try:
            row = _plan_row(plugin_id, origin)
        except ValueError as exc:
            skipped.append({"id": plugin_id, "marketplace": origin["marketplace"], "reason": str(exc)})
            continue
        if row is None:
            up_to_date.append(plugin_id)
        else:
            plugins.append(row)
    return {
        "applied": False,
        "plugins": plugins,
        "up_to_date": up_to_date,
        "skipped": skipped,
        "outside_marketplace": outside,
        "note": UPDATE_ALL_NOTE,
    }


# --- origem gravada (list e doctor) ---------------------------------------------

ORIGIN_VIEW_KEYS = ("source", "commit", "ref", "subdir", "marketplace", "tier", "index_commit")


def _marketplace_notice(plugin_id, name):
    """O que o índice fixado diz hoje do plugin instalado: retirado, obsoleto, renomeado, sumido."""
    try:
        if name not in marketplace.read_state()["marketplaces"]:
            return f"O marketplace {name} foi removido; o plugin continua instalado, sem atualização por ele."
        _pin, index = marketplace.load_index(name)
    except ValueError as exc:
        return marketplace.portable_text(exc)
    entry = next((item for item in index["plugins"] if item["id"] == plugin_id), None)
    if entry is None:
        current = resolve_rename(index, plugin_id)
        if current is not None:
            return f"Renomeado para {current} no marketplace {name}; o update não troca o id."
        return f"O marketplace {name} não traz mais este plugin."
    if entry["yanked"]:
        return f"Retirado (yanked) do marketplace {name}; considere plugins --action remove --id {plugin_id}."
    warnings = entry_warnings(entry)
    return warnings[0] if warnings else None


def plugin_origins() -> dict:
    """`{id: origem}` dos plugins instalados por `install`: as chaves de `ORIGIN_VIEW_KEYS`
    (`None` quando não há) e `marketplace_notice` (o aviso do índice fixado, sem rede).
    Lê só `plugins.json` e o cache dos índices; nunca roda código de plugin."""
    sources = loader.read_state().get("sources") or {}
    views = {}
    for plugin_id, origin in sources.items():
        view = {key: origin.get(key) for key in ORIGIN_VIEW_KEYS}
        name = view["marketplace"]
        view.update(marketplace.tier_view(view["tier"], marketplace.official_marketplace(name)))
        view["marketplace_notice"] = _marketplace_notice(plugin_id, name) if name else None
        views[plugin_id] = view
    return views
