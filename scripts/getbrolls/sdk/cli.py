"""Ações do comando `plugins`."""

from pathlib import Path

from . import loader


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


def _install(args):
    from . import install

    if not args.source:
        raise ValueError("--source é obrigatório em plugins --action install (pasta local ou URL git).")
    return install.install(args.source, confirm=bool(args.yes), expect=args.expect)


def _update(args):
    from . import install

    return install.update(_require_id(args), confirm=bool(args.yes), expect=args.expect)


def _new(args):
    from . import scaffold

    if not args.kind:
        raise ValueError(f"plugins --action new precisa de --kind ({kind_list()}).")
    return scaffold.new(_require_id(args), args.kind, args.path)


ACTIONS = {
    "list": _list,
    "enable": _enable,
    "disable": _disable,
    "check": _check,
    "install": _install,
    "update": _update,
    "new": _new,
}


def run(args):
    """Roda a ação de `plugins --action ...` e devolve o resultado."""
    return ACTIONS[args.action](args)
