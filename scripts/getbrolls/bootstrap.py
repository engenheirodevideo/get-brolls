"""`setup`: confere (e, numa versão futura, instala) o runtime compartilhado do getbrolls.

`check()` só lê: sonda os mesmos executáveis obrigatórios do `doctor`, pela mesma regra
(`commands.readiness_probe`), mais os arquivos de dados, e para o que faltar devolve os
comandos que resolvem. `ready` do `setup --check` e do `doctor` batem. Nunca cria pasta
nem arquivo.
"""

# pylint: disable=cyclic-import
# `cyclic-import` vem de getbrolls.bootstrap <-> getbrolls.commands: o `commands` importa
# este módulo de forma tardia (dentro de `_execute_toolchain`), o que quebra o ciclo em
# tempo de execução; o pylint só permite suprimir R0401 no módulo.

import os
from pathlib import Path

from . import _paths, commands
from .config import TOOL_PATH_KEYS
from .errors import UsageError

# Executável do `doctor` → id do passo; os que não estão aqui usam o próprio nome.
STEP_IDS = {"yt-dlp": "ytdlp", "playwright-cli": "playwright"}
# Passos que o runtime da instalação resolve (vêm primeiro); os outros são do sistema.
RUNTIME_STEPS = ("ytdlp", "playwright")
# Pin → executável que ele fixa: um pin quebrado reprova o passo, como no `doctor`.
_PIN_TOOLS = {**{key: name for name, key in TOOL_PATH_KEYS.items()}, "GB_VENV_PATH": "yt-dlp"}

_NO_INSTALL = (
    "`setup` sem `--check` ainda não instala nesta versão. Rode `{check}` para ver o que falta e siga os "
    "comandos que ele mostrar{installer}."
)


def _step_names():
    """(id, executável) na ordem: runtime primeiro, depois o sistema na ordem do `doctor`."""
    names = [(STEP_IDS.get(name, name), name) for name in commands.REQUIRED_EXECUTABLES]
    runtime = sorted((pair for pair in names if pair[0] in RUNTIME_STEPS), key=lambda p: RUNTIME_STEPS.index(p[0]))
    return runtime + [pair for pair in names if pair[0] not in RUNTIME_STEPS]


def _resolved():
    """Por id de passo: `(executável absoluto ou None, nota do pin quebrado ou None)`."""
    overrides, pin_problems, _social, present = commands.readiness_probe()
    resolved = commands.doctor_resolved(overrides)
    broken = {_PIN_TOOLS.get(problem["item"]): problem["note"] for problem in pin_problems}
    return {
        step: (resolved.get(name) if present.get(name) and name not in broken else None, broken.get(name))
        for step, name in _step_names()
    }


def _line(*args):
    return " ".join(_paths.quote_arg(str(arg)) for arg in args)


def _runtime_commands():
    """Comandos que montam o runtime compartilhado de um pacote instalado, por passo."""
    if _paths.part_sha("venv") is None or _paths.part_sha("tools") is None:
        # Sem requirements.txt/package-lock.json não há o que instalar: só reinstalar repõe.
        return {step: [_paths.REINSTALL_COMMAND] for step in RUNTIME_STEPS}
    data = _paths.data_root()
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


def _data_step():
    missing = commands.missing_data_files()
    if not missing:
        return {"id": "data", "ok": True, "found": str(_paths.data_root()), "commands": []}
    return {
        "id": "data",
        "ok": False,
        "found": None,
        "commands": [commands.data_fix()],
        "note": "Faltam: " + ", ".join(missing),
    }


def check() -> dict:
    """O que falta no runtime desta instalação e como resolver; não instala nada."""
    found = _resolved()
    if _paths.origin() == "checkout":
        fixes = {step: [_paths.installer_hint()] for step in RUNTIME_STEPS}
    else:
        fixes = _runtime_commands()
    steps = []
    for step, _name in _step_names():
        path, pin_note = found.get(step, (None, None))
        entry: dict = {"id": step, "ok": path is not None, "found": str(Path(path)) if path else None}
        if pin_note:
            entry["commands"] = []
            entry["note"] = pin_note
        elif step in RUNTIME_STEPS:
            entry["commands"] = [] if path else fixes[step]
        else:
            entry["commands"] = []
            entry["note"] = commands.SYSTEM_TOOLS
        steps.append(entry)
    steps.append(_data_step())
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
