"""Onde esta instalação mora: dados, CLI, runtime e `.env`.

Escopo: só instalação. O layout de projeto (0 = `brolls/clips`, 1 = `project.json`/
`analysis/`/`broll/`) mora em módulo próprio, nunca aqui.

A origem sai só de sentinelas: `_data/MANIFEST` ao lado do pacote (wheel) ou os
arquivos do repositório acima de `scripts/getbrolls` (checkout). Sem nenhuma das duas,
a origem é `unknown` e nada é adivinhado por `exists()` em pasta vizinha.
"""

import functools
import hashlib
import json
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

# Reinstalar é o único jeito de repor os arquivos de dados de um pacote instalado.
REINSTALL_COMMAND = "uv tool install --reinstall getbrolls"
_DATA_REINSTALL = f"Reinstale (`{REINSTALL_COMMAND}`) ou rode pelo repositório (`python3 scripts/gb.py`)."
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
    source: str  # "GB_RUNTIME_DIR", "profile", "gb_home" ou "checkout"


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
    """Pasta pessoal: `GB_HOME`, depois `~/.getbrolls` (sem resolve).

    O alias `GETBROLLS_HOME` chega aqui como `GB_HOME` por `apply_env_aliases`, que a CLI
    aplica antes de qualquer outra coisa (`cli.main`): um só lugar resolve aliases.
    """
    return Path(os.environ.get("GB_HOME") or Path.home() / ".getbrolls")


# Partes do runtime e a pasta de cada uma dentro da raiz.
RUNTIME_PARTS: dict[str, str] = {"venv": ".venv", "tools": ".tools"}
# Arquivos que definem a versão de cada parte: o `npm ci` exige os dois do npm em acordo.
_PART_FILES: dict[str, tuple[str, ...]] = {
    "venv": ("requirements.txt",),
    "tools": ("package.json", "package-lock.json"),
}
# Marcador de cada parte, na raiz dela (ao lado de `.venv`/`.tools`). Quem instala grava
# `building` antes e `ready` por último: quem lê só aceita uma parte compartilhada `ready`.
RUNTIME_MARKER = ".getbrolls-runtime-{part}.json"
MARKER_SCHEMA_VERSION = 1
MARKER_STATUSES = ("building", "upgrading", "ready")
_RUNTIME_CONFLICT = (
    "{source} aponta para uma pasta de runtime montada por outra versão do getbrolls ({part}). "
    "Pastas explícitas não são trocadas sozinhas: use uma pasta por versão ou apague essa à mão."
)

# Chaves que o perfil aplicou ao ambiente, com o valor aplicado (ver `note_profile_env`).
_PROFILE_ENV: dict[str, str] = {}
# Por parte: (assinatura dos arquivos por mtime/tamanho, sha calculado).
_SHA_CACHE: dict[str, tuple[tuple, str]] = {}


def note_profile_env(applied: Mapping[str, str]) -> None:
    """Registra as chaves que o perfil pôs no ambiente, para citar `profile` como origem."""
    _PROFILE_ENV.update(applied)


def from_profile(key: str) -> bool:
    """A chave veio do perfil e ninguém a trocou depois?"""
    return key in _PROFILE_ENV and os.environ.get(key) == _PROFILE_ENV[key]


def part_sha(part: str) -> str | None:
    """Versão de uma parte do runtime pelos arquivos que a definem; `None` sem eles.

    Guardada por mtime e tamanho dos arquivos: só relê quando um deles muda.
    """
    try:
        files = [data_root() / name for name in _PART_FILES[part]]
        signature = tuple((str(path), stat.st_mtime_ns, stat.st_size) for path, stat in ((p, p.stat()) for p in files))
        cached = _SHA_CACHE.get(part)
        if cached is not None and cached[0] == signature:
            return cached[1]
        blob = b"\0".join(path.read_bytes() for path in files)
    except (DataRootError, OSError):
        return None
    sha = hashlib.sha256(blob).hexdigest()[:16]
    _SHA_CACHE[part] = (signature, sha)
    return sha


def _explicit_root() -> tuple[Path, str] | None:
    explicit = os.environ.get("GB_RUNTIME_DIR")
    if not explicit:
        return None
    return Path(explicit), "profile" if from_profile("GB_RUNTIME_DIR") else "GB_RUNTIME_DIR"


def runtime_root(part: str) -> tuple[Path, str]:
    """Raiz de uma parte e sua origem: a pasta explícita ou `$GB_HOME/runtime/<sha da parte>`.

    Numa pasta explícita (`GB_RUNTIME_DIR` ou o `runtime_dir` do perfil) as duas partes
    dividem a raiz e não há garantia de troca atômica: quem lê usa o que estiver lá.
    """
    explicit = _explicit_root()
    if explicit is not None:
        return explicit
    # Sem os dados não há versão da parte; a pasta compartilhada ganha um nome fixo.
    return gb_home() / "runtime" / (part_sha(part) or "unknown"), "gb_home"


def runtime_target(part: str) -> RuntimePart:
    """Onde a parte seria instalada; nunca a pasta do checkout."""
    root, source = runtime_root(part)
    return RuntimePart(root / RUNTIME_PARTS[part], source)


def marker_path(part: str, root: Path) -> Path:
    """Arquivo do marcador de uma parte, na raiz dela."""
    return root / RUNTIME_MARKER.format(part=part)


def read_marker(part: str, root: Path) -> dict | None:
    """Conteúdo do marcador, ou `None` (ausente, ilegível, não é objeto); nunca levanta."""
    try:
        marker = json.loads(marker_path(part, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


def write_marker(part: str, root: Path, status: str, extra: Mapping[str, object] | None = None) -> Path:
    """Grava o marcador da parte (troca atômica do arquivo); devolve o caminho.

    Registra a versão da parte e, na `.venv`, o Python base que a criou; `extra` acrescenta
    campos informativos (o `upgraded` do `setup --upgrade`, por exemplo).
    """
    if status not in MARKER_STATUSES:
        raise ValueError(f"status de marcador desconhecido: {status}")
    from . import __version__  # tardio: este módulo só depende da stdlib e de `errors`

    python = getattr(sys, "_base_executable", None) or sys.executable
    marker = {
        "schema": MARKER_SCHEMA_VERSION,
        "part": part,
        "sha": part_sha(part),
        "status": status,
        "python": python if part == "venv" else None,
        "getbrolls": __version__,
        **(extra or {}),
    }
    path = marker_path(part, root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def _venv_base_exists(venv: Path) -> bool:
    """O Python base da `.venv` (`home` do `pyvenv.cfg`) ainda existe?"""
    try:
        lines = (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "home":
            return bool(value.strip()) and Path(value.strip()).is_dir()
    return False


def part_ready(part: str, root: Path) -> bool:
    """A parte em `root` está pronta: marcador `ready` da versão atual e pasta íntegra."""
    marker = read_marker(part, root)
    sha = part_sha(part)
    if marker is None or sha is None or marker.get("status") != "ready" or marker.get("sha") != sha:
        return False
    folder = root / RUNTIME_PARTS[part]
    if part == "venv":
        return _venv_base_exists(folder)
    return folder.is_dir()


def runtime_conflict(part: str) -> str | None:
    """Mensagem quando a pasta explícita já tem esta parte pronta de outra versão.

    Duas versões dividindo uma pasta explícita se reinstalariam sem fim; quem instala
    recusa em vez de apagar. Um marcador que não chegou a `ready` é build interrompido.
    """
    explicit = _explicit_root()
    if explicit is None:
        return None
    root, source = explicit
    marker = read_marker(part, root)
    if marker is None or marker.get("status") != "ready" or marker.get("sha") == part_sha(part):
        return None
    return _RUNTIME_CONFLICT.format(
        source=source if source == "GB_RUNTIME_DIR" else "O runtime_dir do perfil", part=part
    )


def _runtime_part(part: str) -> RuntimePart:
    root, source = runtime_root(part)
    shared = RuntimePart(root / RUNTIME_PARTS[part], source)
    if source != "gb_home" or part_ready(part, root):
        return shared
    inst = install()
    local = inst.checkout_root / RUNTIME_PARTS[part] if inst.origin == "checkout" and inst.checkout_root else None
    if local is not None and local.is_dir():
        return RuntimePart(local, "checkout")
    return shared


def venv_dir() -> RuntimePart:
    """`.venv` do runtime (yt-dlp)."""
    return _runtime_part("venv")


def tools_dir() -> RuntimePart:
    """`.tools` do runtime (Playwright)."""
    return _runtime_part("tools")


def _part_info(part: str, found: RuntimePart) -> dict:
    marker = read_marker(part, found.path.parent)
    return {
        "path": str(found.path),
        "source": found.source,
        "managed": found.source != "checkout" and part_ready(part, found.path.parent),
        "marker": marker.get("status") if marker is not None else None,
        "conflict": runtime_conflict(part),
    }


def runtime_info() -> dict:
    """Resumo do runtime para diagnóstico."""
    return {
        "shas": {part: part_sha(part) for part in RUNTIME_PARTS},
        "venv": _part_info("venv", venv_dir()),
        "tools": _part_info("tools", tools_dir()),
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


def _started_from(found: Path) -> bool:
    """No Windows: o `getbrolls(.exe)` achado é o arquivo que iniciou este processo?"""
    if not sys.argv or not sys.argv[0]:
        return False
    started = Path(sys.argv[0]).resolve()
    names = {started, started.with_suffix("") if started.suffix.lower() == ".exe" else started.with_suffix(".exe")}
    return str(found).lower() in {str(name).lower() for name in names}


def _console_script_is_ours(os_name: str | None = None) -> bool:
    """`getbrolls` do PATH só vale se for o script deste mesmo interpretador.

    No Windows vale também o `getbrolls.exe` que iniciou este processo: o `uv tool` põe o
    executável numa pasta própria do PATH, longe do python do ambiente da ferramenta.
    """
    found = shutil.which("getbrolls")
    if not found:
        return False
    found_path = Path(found).resolve()
    if found_path.parent == Path(sys.executable).parent.resolve():
        return True
    return _os_name(os_name) == "nt" and _started_from(found_path)


def cli_command(os_name: str | None = None) -> list[str]:
    """argv para exibir à pessoa (o prefixo dos comandos sugeridos)."""
    script = _gb_script(install())
    if script is not None:
        return [_interpreter(os_name), str(script)]
    if _console_script_is_ours(os_name):
        return ["getbrolls"]
    return [sys.executable, "-P", "-m", "getbrolls"]


def module_invocation(os_name: str | None = None) -> list[str]:
    """`python -P -m getbrolls` sem caminho da máquina (o `-P` isola o pacote da pasta atual)."""
    return [_interpreter(os_name), "-P", "-m", "getbrolls"]


def portable_cli_argv(os_name: str | None = None) -> list[str]:
    """argv da CLI para um manifesto ou documento: roda, mas não cita caminho desta máquina.

    No checkout, relativo à raiz do repositório; no pacote, o `getbrolls` do PATH só quando
    ele é o deste interpretador, senão o módulo isolado.
    """
    if origin() == "checkout":
        return [_interpreter(os_name), "scripts/gb.py"]
    if _console_script_is_ours(os_name):
        return ["getbrolls"]
    return module_invocation(os_name)


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
    return "rode `getbrolls setup` (instala yt-dlp e Playwright em $GB_HOME/runtime)"


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
    """Copia `GETBROLLS_X` para `GB_X` quando `GB_X` não está definido (vazio conta como não
    definido); devolve os aliases usados."""
    environ = environ if environ is not None else os.environ
    used = []
    for name, twin in list(_alias_twins(environ)):
        if not environ.get(twin) and environ[name]:
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
