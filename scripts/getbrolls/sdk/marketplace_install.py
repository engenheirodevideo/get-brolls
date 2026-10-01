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

O `tier` da entrada é só informação gravada na origem e mostrada na prévia:
nunca afrouxa nenhuma dessas regras.

Limite conhecido: o `--expect` pré-preenchido vem do índice, então um agente que
copia esse sha256 pula a leitura humana. Por isso o `install` sem `--yes` sempre
para na prévia, e a skill orienta mostrá-la à pessoa antes de confirmar.
"""

from .. import _paths
from ..errors import UsageError
from . import git_source, loader, marketplace
from . import install as plugin_install
from .manifest import compatibility_problem
from .marketplace_index import manifest_mismatches, manifest_warnings, parse_plugin_ref, resolve_rename

PREVIEW_NOTE = (
    "Mostre esta prévia à pessoa (permissões, origem, commit e arquivos) antes de confirmar: o --expect "
    "vem pré-preenchido do índice, e copiá-lo sem mostrar a prévia pula a revisão humana. Com o ok dela, "
    "rode o comando de next. " + loader.NOT_SANDBOX
)


def _not_allowed(name):
    return ValueError(f"O perfil de workspace não permite o marketplace {name}; veja profile --action show.")


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
        raise _not_allowed(name)
    pin, index = marketplace.load_index(name)
    entry = find_entry(index, plugin_id, name)
    if entry["yanked"]:
        raise _yanked(plugin_id, name)
    check_compatible(entry)
    return plugin_id, pin, entry


def market_block(entry, pin):
    """O bloco `marketplace` da resposta: nome, commit do índice e tier da entrada."""
    return {"name": pin.name, "commit": pin.commit, "tier": entry["tier"]}


def with_warnings(plugin, warnings):
    """`plugin` com `warnings` somados aos avisos que ele já traz."""
    if warnings:
        plugin = {**plugin, "warnings": [*plugin.get("warnings", []), *warnings]}
    return plugin


def install(ref: str, confirm: bool, expect: str | None) -> dict:
    """Prévia (sem `confirm`, mesmo com `expect`) ou instalação de `<id>@<marketplace>`."""
    _plugin_id, pin, entry = resolve(ref)
    warnings = entry_warnings(entry)
    result = plugin_install.install_from(
        entry_spec(entry, pin),
        confirm,
        expect,
        verify=verifier(entry, warnings),
        origin_extra=origin_extra(entry, pin),
    )
    result = {**result, "plugin": with_warnings(result["plugin"], warnings), "marketplace": market_block(entry, pin)}
    if not confirm:
        sha = result["plugin"]["sha256"]
        result["expect"] = sha
        result["next"] = _paths.cli_hint("plugins", "--action", "install", "--id", ref, "--yes", "--expect", sha)
        result["note"] = PREVIEW_NOTE
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
