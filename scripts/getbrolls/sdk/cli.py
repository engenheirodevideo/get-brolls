"""Ações do comando `plugins`."""

from pathlib import Path

from ..errors import UsageError
from . import loader

# Flags de pin (`--commit`, `--ref`, `--subdir`) e as ações em que cada uma vale. O
# `update` aceita as três aqui e recusa `--ref`/`--subdir` com a própria mensagem.
_PIN_FLAGS = ("commit", "ref", "subdir")
_PIN_FLAG_ACTIONS = {
    "install": _PIN_FLAGS,
    "update": _PIN_FLAGS,
    "marketplace-add": ("commit", "ref"),
    "marketplace-update": ("commit",),
}
# Ações em que `--marketplace <nome>` vale.
_MARKETPLACE_FLAG_ACTIONS = ("marketplace-remove", "marketplace-update", "search")


def kind_list():
    """`provider, route ou command`: os tipos do `new`, na ordem do scaffold."""
    from .scaffold import KINDS

    return f"{', '.join(KINDS[:-1])} ou {KINDS[-1]}"


def _list(_args):  # mesma assinatura das outras ações
    return {
        "plugins_dir": str(loader.plugins_root()),
        "selection": "GB_PLUGINS" if loader.env_selection() is not None else "plugins.json",
        "plugins": loader.inventory(),
        "note": (
            "Status pré-carga (manifesto + pin de hash), sem executar código de plugin; "
            "rode `doctor` para o resultado real do carregamento (register() executado)."
        ),
    }


def _require_id(args):
    if not args.id:
        raise ValueError(f"--id é obrigatório em plugins --action {args.action}.")
    return args.id


def _enable(args):
    return loader.enable(_require_id(args), confirm=bool(args.yes), expect=args.expect)


def _disable(args):
    return loader.disable(_require_id(args))


def _check(args):
    if not args.path:
        raise ValueError("--path é obrigatório em plugins --action check.")
    folder = Path(args.path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("--path tem que ser a pasta do plugin.")
    from . import testing

    return testing.check_plugin(folder)


def _pin_flags(args):
    return {name: getattr(args, name, None) for name in _PIN_FLAGS}


def _install(args):
    from . import install

    flags = _pin_flags(args)
    if args.id and "@" in args.id:
        from . import marketplace_install

        marketplace_install.reject_source_with_ref(args.source, flags)
        return marketplace_install.install(args.id, confirm=bool(args.yes), expect=args.expect)
    if not args.source:
        if any(value is not None for value in flags.values()):
            raise UsageError("--commit, --ref e --subdir precisam de --source em plugins --action install.")
        raise ValueError("--source é obrigatório em plugins --action install (pasta local ou URL git).")
    return install.install(args.source, confirm=bool(args.yes), expect=args.expect, **flags)


def _update(args):
    from . import install, marketplace_install

    flags = _pin_flags(args)
    if flags["ref"] is not None or flags["subdir"] is not None:
        raise UsageError(
            "--ref e --subdir só valem em plugins --action install (com --source); o update usa os gravados "
            "na instalação. Para fixar outro commit, use --commit."
        )
    if getattr(args, "all", False):
        if args.id or flags["commit"] is not None:
            raise UsageError("--all não vale junto com --id nem --commit em plugins --action update.")
        return marketplace_install.update_all(confirm=bool(args.yes), expect=args.expect)
    plugin_id = _require_id(args)
    if marketplace_install.marketplace_of(plugin_id) is not None:
        if flags["commit"] is not None:
            raise UsageError(
                "--commit não vale no update de um plugin instalado por marketplace: o commit vem do índice "
                "fixado. Para voltar o índice, use plugins --action marketplace-update --marketplace <nome> --commit."
            )
        return marketplace_install.update(plugin_id, confirm=bool(args.yes), expect=args.expect)
    return install.update(plugin_id, confirm=bool(args.yes), expect=args.expect, commit=flags["commit"])


def _remove(args):
    from . import remove

    if args.expect:
        raise UsageError("plugins --action remove não usa --expect: remover não aprova conteúdo; use só --yes.")
    return remove.remove(_require_id(args), confirm=bool(args.yes))


def _new(args):
    from . import scaffold

    if not args.kind:
        raise ValueError(f"plugins --action new precisa de --kind ({kind_list()}).")
    return scaffold.new(_require_id(args), args.kind, args.path)


def _require_marketplace(args):
    if not getattr(args, "marketplace", None):
        raise UsageError(f"--marketplace <nome> é obrigatório em plugins --action {args.action}.")
    return args.marketplace


def _marketplace_add(args):
    from . import marketplace

    if not args.source:
        raise UsageError(
            "--source é obrigatório em plugins --action marketplace-add (URL git ou pasta local que é repositório)."
        )
    return marketplace.add(args.source, ref=args.ref, commit=args.commit)


def _marketplace_list(_args):  # mesma assinatura das outras ações
    from . import marketplace

    return marketplace.listing()


def _marketplace_remove(args):
    from . import marketplace

    return marketplace.remove(_require_marketplace(args))


def _marketplace_update(args):
    from . import marketplace

    return marketplace.refresh(getattr(args, "marketplace", None), commit=args.commit)


def _search(args):
    from . import marketplace

    return marketplace.search(args.query or "", getattr(args, "marketplace", None))


ACTIONS = {
    "list": _list,
    "enable": _enable,
    "disable": _disable,
    "check": _check,
    "install": _install,
    "update": _update,
    "remove": _remove,
    "new": _new,
    "marketplace-add": _marketplace_add,
    "marketplace-list": _marketplace_list,
    "marketplace-remove": _marketplace_remove,
    "marketplace-update": _marketplace_update,
    "search": _search,
}


def run(args):
    """Roda a ação de `plugins --action ...` e devolve o resultado."""
    allowed_flags = _PIN_FLAG_ACTIONS.get(args.action, ())
    misused = [
        f"--{name}" for name, value in _pin_flags(args).items() if value is not None and name not in allowed_flags
    ]
    if misused:
        raise UsageError(f"{', '.join(misused)} não vale(m) em plugins --action {args.action}.")
    if getattr(args, "marketplace", None) is not None and args.action not in _MARKETPLACE_FLAG_ACTIONS:
        raise UsageError(f"--marketplace não vale em plugins --action {args.action}.")
    if getattr(args, "all", False) and args.action != "update":
        raise UsageError(f"--all só vale em plugins --action update, não em {args.action}.")
    if getattr(args, "query", None) is not None and args.action != "search":
        raise UsageError(f"--query só vale em plugins --action search, não em {args.action}.")
    return ACTIONS[args.action](args)
