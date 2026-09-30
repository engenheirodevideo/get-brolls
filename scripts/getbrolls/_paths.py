"""Onde esta instalação mora: dados, CLI, runtime e `.env`.

Escopo: só instalação. O layout de projeto (0 = `brolls/clips`, 1 = `project.json`/
`analysis/`/`broll/`) mora em módulo próprio, nunca aqui.

A origem sai só de sentinelas: `_data/MANIFEST` ao lado do pacote (wheel) ou os
arquivos do repositório acima de `scripts/getbrolls` (checkout). Sem nenhuma das duas,
a origem é `unknown` e nada é adivinhado por `exists()` em pasta vizinha.
"""

import functools
import hashlib
import os
import platform
import shlex
import shutil
import sys
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from .errors import DataRootError, UsageError

# Ordenada; precisa ser igual ao manifesto (`packaging/data_manifest.txt`).
REQUIRED_DATA: tuple[str, ...] = (
    "assets/brand-logo.png",
    "assets/review.css",
    "assets/review.js",
    "assets/storyboard.css",
    "assets/storyboard.js",
    "docs/BRIEF.md",
    "docs/RULES.md",
    "examples/plans/reels.plan.json",
    "package-lock.json",
    "package.json",
    "requirements.txt",
    "schemas/brief.schema.json",
    "schemas/candidate.schema.json",
    "schemas/export_plan.schema.json",
)
WHEEL_MANIFEST = "_data/MANIFEST"  # relativo à pasta do pacote
CHECKOUT_MANIFEST = "packaging/data_manifest.txt"  # relativo à raiz do repositório
_REPO_SENTINELS = ("scripts/gb.py", "docs/RULES.md", "assets/brand-logo.png")

_DATA_REINSTALL = (
    "Reinstale (`uv tool install --reinstall getbrolls`) ou rode pelo repositório (`python3 scripts/gb.py`)."
)
_DATA_ROOT_MISSING = f"Os arquivos de dados do getbrolls não estão junto do pacote. {_DATA_REINSTALL}"
_ENV_FLAG_MISSING = "--env-file não existe. Confira o caminho."
_ENV_VAR_MISSING = "GB_ENV_FILE aponta para um arquivo que não existe. Confira o caminho ou remova a variável."
_ENV_CHECKOUT_NOTE = (
    "Este .env está na pasta da instalação e só vale para ela; prefira $GB_HOME/.env "
    "(padrão ~/.getbrolls/.env), que vale para qualquer instalação."
)
_ALIAS_PREFIX = "GETBROLLS_"
# Sem aspas no Windows só o que nenhum shell (Git Bash, PowerShell, cmd) reinterpreta.
_NT_SAFE = frozenset("_-.,:/=@+")


@dataclass(frozen=True)
class Install:
    """Origem da instalação e as pastas que ela determina."""

    origin: str  # "wheel", "checkout" ou "unknown"
    package_dir: Path
    data_root: Path | None
    checkout_root: Path | None


@dataclass(frozen=True)
class RuntimePart:
    """Pasta do runtime (`.venv` ou `.tools`) e de onde ela veio."""

    path: Path
    source: str  # "GB_RUNTIME_DIR", "gb_home" ou "checkout"


@dataclass(frozen=True)
class EnvChoice:
    """`.env` escolhido, os que ficaram de fora e tudo o que foi procurado."""

    path: Path | None
    source: str | None
    ignored: tuple[Path, ...]
    searched: tuple[Path, ...]


def detect(package_dir: Path | None = None) -> Install:
    """Classifica a instalação pelas sentinelas; nunca chuta."""
    pkg = package_dir if package_dir is not None else Path(__file__).resolve().parent
    if (pkg / WHEEL_MANIFEST).is_file():
        return Install("wheel", pkg, pkg / "_data", None)
    root = pkg.parent.parent
    if pkg.parent.name == "scripts" and all((root / sentinel).is_file() for sentinel in _REPO_SENTINELS):
        return Install("checkout", pkg, root, root)
    return Install("unknown", pkg, None, None)


@functools.cache
def install() -> Install:
    """Instalação deste processo, detectada uma vez."""
    return detect()


def origin() -> str:
    """`wheel`, `checkout` ou `unknown`; nunca levanta."""
    return install().origin


def data_root() -> Path:
    """Pasta dos dados do pacote; `DataRootError` quando ela não existe."""
    root = install().data_root
    if root is None:
        raise DataRootError(_DATA_ROOT_MISSING)
    return root


def data_path(*parts: str) -> Path:
    """Caminho de um arquivo de dados do pacote; `DataRootError` quando ele falta.

    Sem o arquivo, o erro nomeia só a entrada relativa (nunca o caminho desta máquina):
    é pré-requisito da instalação (exit 4), não `FileNotFoundError` de operação.
    """
    path = data_root().joinpath(*parts)
    if not path.is_file():
        raise DataRootError(f"Falta o arquivo de dados {'/'.join(parts)} do getbrolls. {_DATA_REINSTALL}")
    return path


def manifest_entries(inst: Install | None = None) -> tuple[str, ...]:
    """Entradas do manifesto de dados (wheel ou checkout), sem comentários."""
    inst = inst if inst is not None else install()
    if inst.origin == "wheel":
        manifest = inst.package_dir / WHEEL_MANIFEST
    elif inst.checkout_root is not None:
        manifest = inst.checkout_root / CHECKOUT_MANIFEST
    else:
        raise DataRootError(_DATA_ROOT_MISSING)
    lines = (line.strip() for line in manifest.read_text(encoding="utf-8").splitlines())
    return tuple(line for line in lines if line and not line.startswith("#"))


def verify_data() -> list[str]:
    """Entradas de `REQUIRED_DATA` que faltam; `[]` quando está tudo lá."""
    root = data_root()
    return [entry for entry in REQUIRED_DATA if not (root / entry).is_file()]


def gb_home() -> Path:
    """Pasta pessoal: `GB_HOME`, depois `GETBROLLS_HOME`, depois `~/.getbrolls` (sem resolve)."""
    return Path(os.environ.get("GB_HOME") or os.environ.get("GETBROLLS_HOME") or Path.home() / ".getbrolls")


def requirements_sha() -> str | None:
    """Versão das dependências (requirements.txt + package-lock.json); `None` sem os dados."""
    try:
        root = data_root()
        blob = (root / "requirements.txt").read_bytes() + b"\0" + (root / "package-lock.json").read_bytes()
    except (DataRootError, OSError):
        return None
    return hashlib.sha256(blob).hexdigest()[:16]


def _runtime_part(name: str) -> RuntimePart:
    explicit = os.environ.get("GB_RUNTIME_DIR")
    if explicit:
        return RuntimePart(Path(explicit) / name, "GB_RUNTIME_DIR")
    # Sem os dados não há versão das dependências; a pasta compartilhada ganha um nome fixo.
    shared = gb_home() / "runtime" / (requirements_sha() or "unknown") / name
    if shared.is_dir():
        return RuntimePart(shared, "gb_home")
    inst = install()
    if inst.origin == "checkout" and inst.checkout_root is not None and (inst.checkout_root / name).is_dir():
        return RuntimePart(inst.checkout_root / name, "checkout")
    return RuntimePart(shared, "gb_home")


def venv_dir() -> RuntimePart:
    """`.venv` do runtime (yt-dlp)."""
    return _runtime_part(".venv")


def tools_dir() -> RuntimePart:
    """`.tools` do runtime (Playwright)."""
    return _runtime_part(".tools")


def runtime_info() -> dict:
    """Resumo do runtime para diagnóstico."""
    venv, tools = venv_dir(), tools_dir()
    return {
        "req_sha": requirements_sha(),
        "venv": {"path": str(venv.path), "source": venv.source},
        "tools": {"path": str(tools.path), "source": tools.source},
        "explicit": bool(os.environ.get("GB_RUNTIME_DIR")),
    }


def env_file(flag: str | None = None) -> EnvChoice:
    """`.env` que vale: flag, `GB_ENV_FILE`, o do checkout e, por fim, o de `gb_home()`."""
    if flag:
        path = Path(flag)
        if not path.is_file():
            raise UsageError(_ENV_FLAG_MISSING)
        return EnvChoice(path, "flag", (), (path,))
    variable = os.environ.get("GB_ENV_FILE")
    if variable:
        path = Path(variable)
        if not path.is_file():
            raise UsageError(_ENV_VAR_MISSING)
        return EnvChoice(path, "GB_ENV_FILE", (), (path,))
    home_env = gb_home() / ".env"
    inst = install()
    searched: list[Path] = []
    if inst.origin == "checkout" and inst.checkout_root is not None:
        checkout_env = inst.checkout_root / ".env"
        searched.append(checkout_env)
        if checkout_env.is_file():
            ignored = (home_env,) if home_env.is_file() else ()
            return EnvChoice(checkout_env, "checkout", ignored, (checkout_env, home_env))
    searched.append(home_env)
    if home_env.is_file():
        return EnvChoice(home_env, "gb_home", (), tuple(searched))
    return EnvChoice(None, None, (), tuple(searched))


def _os_name(os_name: str | None = None) -> str:
    return os_name if os_name is not None else os.name


def _interpreter(os_name: str | None = None) -> str:
    """Nome do Python para exibir num comando do checkout."""
    return "python" if _os_name(os_name) == "nt" else "python3"


def _gb_script(inst: Install) -> Path | None:
    return inst.checkout_root / "scripts" / "gb.py" if inst.origin == "checkout" and inst.checkout_root else None


def cli_argv() -> list[str]:
    """argv novo para rodar a CLI como subprocesso."""
    script = _gb_script(install())
    if script is not None:
        return [sys.executable, str(script)]
    return [sys.executable, "-P", "-m", "getbrolls"]


def _console_script_is_ours() -> bool:
    """`getbrolls` do PATH só vale se for o script deste mesmo interpretador."""
    found = shutil.which("getbrolls")
    if not found:
        return False
    return Path(found).resolve().parent == Path(sys.executable).parent.resolve()


def cli_command(os_name: str | None = None) -> list[str]:
    """argv para exibir à pessoa (o prefixo dos comandos sugeridos)."""
    script = _gb_script(install())
    if script is not None:
        return [_interpreter(os_name), str(script)]
    if _console_script_is_ours():
        return ["getbrolls"]
    return [sys.executable, "-P", "-m", "getbrolls"]


def cli_prefix_text(os_name: str | None = None) -> str:
    """Prefixo dos comandos sugeridos; no checkout POSIX, idêntico ao da 2.5."""
    script = _gb_script(install())
    if script is not None:
        return f'{_interpreter(os_name)} "{script}"'
    return " ".join(quote_arg(arg, os_name) for arg in cli_command(os_name))


def _quote_nt(value: str) -> str:
    if value and all(char.isalnum() or char in _NT_SAFE for char in value):
        return value
    out = ['"']
    backslashes = 0
    for char in value:
        if char == "\\":
            backslashes += 1
            continue
        if char == '"':
            out.append("\\" * (2 * backslashes + 1) + '"')
        else:
            out.append("\\" * backslashes + char)
        backslashes = 0
    out.append("\\" * (2 * backslashes) + '"')
    return "".join(out)


def quote_arg(value: str, os_name: str | None = None) -> str:
    """Um argumento pronto para colar no terminal do sistema."""
    return _quote_nt(value) if _os_name(os_name) == "nt" else shlex.quote(value)


def command_text(*args: str, os_name: str | None = None) -> str:
    """Comando completo da CLI, como texto para a pessoa copiar."""
    return " ".join([cli_prefix_text(os_name), *(quote_arg(arg, os_name) for arg in args)])


def _nt_backslashes(text: str, start: int) -> tuple[str, int]:
    """Barras a partir de `start`: (texto literal, próximo índice)."""
    end = start
    while end < len(text) and text[end] == "\\":
        end += 1
    count = end - start
    if end < len(text) and text[end] == '"':
        if count % 2:
            return "\\" * (count // 2) + '"', end + 1
        return "\\" * (count // 2), end
    return "\\" * count, end


def _nt_token(text: str, start: int) -> tuple[str, int]:
    """Um argumento pelas regras do CommandLineToArgvW, a partir de `start`."""
    out: list[str] = []
    quoted = False
    index = start
    while index < len(text):
        char = text[index]
        if char in " \t" and not quoted:
            break
        if char == "\\":
            literal, index = _nt_backslashes(text, index)
            out.append(literal)
        elif char == '"':
            if quoted and text[index + 1 : index + 2] == '"':
                out.append('"')
                index += 2
            else:
                quoted = not quoted
                index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out), index


def _split_nt(text: str) -> Iterator[str]:
    index = 0
    while True:
        while index < len(text) and text[index] in " \t":
            index += 1
        if index >= len(text):
            return
        token, index = _nt_token(text, index)
        yield token


def split_command(text: str, os_name: str | None = None) -> list[str]:
    """Inverso de `command_text`: o argv que o terminal do sistema veria."""
    return list(_split_nt(text)) if _os_name(os_name) == "nt" else shlex.split(text)


def module_command(module: str, os_name: str | None = None) -> list[str]:
    """argv para rodar um módulo do pacote (`getbrolls.x`)."""
    inst = install()
    if inst.origin == "checkout" and inst.checkout_root is not None:
        script = inst.checkout_root / "scripts" / Path(*module.split(".")).with_suffix(".py")
        return [_interpreter(os_name), str(script)]
    return [sys.executable, "-m", module]


def cli_hint(*args: str) -> str:
    """Comando para citar em prosa, sem caminho da máquina."""
    base = "python3 scripts/gb.py" if origin() == "checkout" else "getbrolls"
    return " ".join((base, *args))


def installer_hint() -> str:
    """Como instalar as dependências do runtime nesta instalação."""
    inst = install()
    if inst.origin == "checkout" and inst.checkout_root is not None:
        scripts = inst.checkout_root / "scripts"
        return f'bash "{scripts / "install.sh"}" (ou "{scripts / "install.ps1"}" no Windows)'
    return "rode `getbrolls setup --check` e siga os comandos que ele mostrar"


def _alias_twins(environ: Mapping[str, str]) -> Iterator[tuple[str, str]]:
    """Pares (`GETBROLLS_X`, `GB_X`) cujo `GB_X` é uma chave conhecida do core."""
    from . import config  # tardio: o config importa o runtime, que não precisa entrar aqui

    known = config.KEYS | config.PROCESS_ONLY_KEYS
    for name in sorted(environ):
        if name.startswith(_ALIAS_PREFIX):
            twin = "GB_" + name[len(_ALIAS_PREFIX) :]
            if twin in known:
                yield name, twin


def apply_env_aliases(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Copia `GETBROLLS_X` para `GB_X` quando `GB_X` não está definido; devolve os aliases usados."""
    environ = environ if environ is not None else os.environ
    used = []
    for name, twin in list(_alias_twins(environ)):
        if twin not in environ and environ[name]:
            environ[twin] = environ[name]
            used.append(name)
    return used


def aliased_env_names(environ: Mapping[str, str] | None = None) -> list[str]:
    """Nomes `GETBROLLS_X` cujo `GB_X` tem o mesmo valor."""
    environ = environ if environ is not None else os.environ
    return [name for name, twin in _alias_twins(environ) if environ.get(twin) == environ[name]]


def build_info() -> dict:
    """Versão, Python e origem desta instalação."""
    from . import __version__  # tardio: este módulo só depende da stdlib e de `errors`

    inst = install()
    return {
        "version": __version__,
        "python": platform.python_version(),
        "origin": inst.origin,
        "package_dir": str(inst.package_dir),
        "data_root": str(inst.data_root) if inst.data_root is not None else None,
    }


def _env_report(env_flag: str | None) -> dict:
    try:
        choice = env_file(env_flag)
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
        missing = verify_data()
    except DataRootError:
        missing = list(REQUIRED_DATA)
    return {
        **build_info(),
        "data_missing": missing,
        "cli": {"argv": cli_argv(), "command": cli_command()},
        "runtime": runtime_info(),
        "env_file": _env_report(env_flag),
        "gb_home": str(gb_home()),
        "env_aliases": aliased_env_names(),
    }
