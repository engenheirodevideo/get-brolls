"""`setup`: confere (e, numa versão futura, instala) o runtime compartilhado do getbrolls.

`check()` só lê: procura yt-dlp, Playwright CLI, FFmpeg, ffprobe e Node, e para o que
faltar devolve os comandos que resolvem. Nunca cria pasta nem arquivo.
"""

# pylint: disable=cyclic-import
# `cyclic-import` vem de getbrolls.bootstrap <-> getbrolls.commands: o `commands` importa
# este módulo de forma tardia (dentro de `_execute_toolchain`), o que quebra o ciclo em
# tempo de execução; o pylint só permite suprimir R0401 no módulo.

import os
from pathlib import Path

from . import _paths, commands
from .errors import DataRootError, UsageError

# (id do passo, executável que o doctor sonda): a mesma resolução do `doctor`.
STEPS = (
    ("ytdlp", "yt-dlp"),
    ("playwright", "playwright-cli"),
    ("ffmpeg", "ffmpeg"),
    ("ffprobe", "ffprobe"),
    ("node", "node"),
)
# Passos que o runtime da instalação resolve; os outros são do sistema.
RUNTIME_STEPS = ("ytdlp", "playwright")

_NO_INSTALL = (
    "`setup` sem `--check` ainda não instala nesta versão. Rode `{check}` para ver o que falta e siga os "
    "comandos que ele mostrar{installer}."
)


def _resolved():
    """Executável absoluto por id de passo (ou None), com os pins de caminho valendo."""
    overrides, _problems = commands.doctor_overrides()
    resolved = commands.doctor_resolved(overrides)
    return {step: resolved.get(name) for step, name in STEPS}


def _line(*args):
    return " ".join(_paths.quote_arg(str(arg)) for arg in args)


def _runtime_commands():
    """Comandos que montam o runtime compartilhado de um pacote instalado, por passo."""
    try:
        data = _paths.data_root()
    except DataRootError:
        return {step: [commands.REINSTALL] for step in RUNTIME_STEPS}
    venv, tools = _paths.venv_dir().path, _paths.tools_dir().path
    windows = os.name == "nt"
    python = venv / ("Scripts/python.exe" if windows else "bin/python")
    ytdlp = [
        _line("python" if windows else "python3", "-m", "venv", venv),
        _line(python, "-m", "pip", "install", "-r", data / "requirements.txt"),
    ]
    sources = (data / "package.json", data / "package-lock.json")
    if windows:
        copy = [_line("mkdir", tools), *(_line("copy", source, tools) for source in sources)]
    else:
        copy = [_line("mkdir", "-p", tools), _line("cp", *sources, tools)]
    npm = _line("npm", "ci", "--prefix", tools, "--ignore-scripts", "--no-audit", "--no-fund")
    return {"ytdlp": ytdlp, "playwright": [*copy, npm]}


def check() -> dict:
    """O que falta no runtime desta instalação e como resolver; não instala nada."""
    found = _resolved()
    if _paths.origin() == "checkout":
        fixes = {step: [_paths.installer_hint()] for step in RUNTIME_STEPS}
    else:
        fixes = _runtime_commands()
    steps = []
    for step, _name in STEPS:
        path = found.get(step)
        entry: dict = {"id": step, "ok": path is not None, "found": str(Path(path)) if path else None}
        if step in RUNTIME_STEPS:
            entry["commands"] = [] if path else fixes[step]
        else:
            entry["commands"] = []
            entry["note"] = commands.SYSTEM_TOOLS
        steps.append(entry)
    missing = [entry["id"] for entry in steps if not entry["ok"]]
    if missing:
        line = f"Faltam {len(missing)} de {len(steps)} itens do runtime: {', '.join(missing)}. Siga `commands`."
    else:
        line = f"Runtime pronto: {len(steps)} de {len(steps)} itens encontrados."
    return {
        "ready": not missing,
        "summary": {"line": line, "missing": missing},
        "runtime": _paths.runtime_info(),
        "steps": steps,
    }


def run(args) -> dict:
    """`setup --check` confere; sem `--check` é erro de uso até o `setup` instalar."""
    if getattr(args, "check", False):
        return check()
    installer = "; para instalar hoje, use " + _paths.installer_hint() if _paths.origin() == "checkout" else ""
    raise UsageError(_NO_INSTALL.format(check=_paths.cli_hint("setup", "--check"), installer=installer))
