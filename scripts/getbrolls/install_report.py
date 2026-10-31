"""Retrato da instalação para o `doctor` (versão, dados, CLI, runtime e `.env`)."""

import os
import platform
from collections.abc import Mapping

from . import _paths
from .errors import DataRootError, UsageError

_ENV_CHECKOUT_NOTE = (
    "Este .env está na pasta da instalação e só vale para ela; prefira $GB_HOME/.env "
    "(padrão ~/.getbrolls/.env), que vale para qualquer instalação."
)


def old_env_names(environ: Mapping[str, str] | None = None) -> list[str]:
    """Os `GETBROLLS_X` do ambiente que a versão de testes da 2.6 lia como `GB_X` e que não valem mais.

    Só os que têm um `GB_X` do core; `GETBROLLS_CACHE_DIR` (da 2.5) continua valendo e não entra."""
    from . import config  # tardio: o config importa o runtime, que não precisa entrar aqui

    known = config.KEYS | config.PROCESS_ONLY_KEYS
    prefix = "GETBROLLS_"
    environ = os.environ if environ is None else environ
    return sorted(
        name
        for name in environ
        if name.startswith(prefix) and name != "GETBROLLS_CACHE_DIR" and "GB_" + name[len(prefix) :] in known
    )


def build_info() -> dict:
    """Versão, Python e origem desta instalação."""
    from . import __version__  # tardio: evita ciclo com o pacote

    inst = _paths.install()
    return {
        "version": __version__,
        "python": platform.python_version(),
        "origin": inst.origin,
        "package_dir": str(inst.package_dir),
        "data_root": str(inst.data_root) if inst.data_root is not None else None,
    }


def env_report(env_flag: str | None) -> dict:
    """Qual `.env` vale (e o que foi procurado); erro de uso vira `{"error"}`, nunca levanta."""
    try:
        choice = _paths.env_file(env_flag)
    except UsageError as exc:
        return {"error": str(exc)}
    return {
        "path": str(choice.path) if choice.path is not None else None,
        "source": choice.source,
        "ignored": [str(path) for path in choice.ignored],
        "searched": [str(path) for path in choice.searched],
        "note": _ENV_CHECKOUT_NOTE if choice.source == "checkout" else None,
    }


def install_report(env_flag: str | None = None) -> dict:
    """Retrato da instalação para o `doctor`; nunca levanta."""
    try:
        missing, unreadable = _paths.data_inventory()
    except DataRootError:
        missing, unreadable = list(_paths.REQUIRED_DATA), []
    return {
        **build_info(),
        "data_missing": missing,
        "data_unreadable": unreadable,
        "cli": {"argv": _paths.cli_argv(), "command": _paths.cli_command()},
        "runtime": _paths.runtime_info(),
        "env_file": env_report(env_flag),
        "gb_home": str(_paths.gb_home()),
    }
