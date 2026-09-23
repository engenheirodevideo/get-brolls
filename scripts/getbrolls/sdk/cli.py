"""Ações do comando `plugins`."""

from pathlib import Path

from . import loader


def run(args):
    action = args.action
    if action == "list":
        return {
            "plugins_dir": str(loader.plugins_root()),
            "selection": "GB_PLUGINS" if loader.env_selection() is not None else "plugins.json",
            "plugins": loader.inventory(),
            "note": (
                "Status pré-carga (manifesto + pin de hash), sem executar código de plugin; "
                "rode `doctor` para o resultado real do carregamento (register() executado)."
            ),
        }
    if action in ("enable", "disable") and not args.id:
        raise ValueError(f"--id é obrigatório em plugins --action {action}.")
    if action == "enable":
        return loader.enable(args.id, confirm=bool(args.yes))
    if action == "disable":
        return loader.disable(args.id)
    if not args.path:
        raise ValueError("--path é obrigatório em plugins --action check.")
    folder = Path(args.path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("--path tem que ser a pasta do plugin.")
    return loader.trial_load(folder)
