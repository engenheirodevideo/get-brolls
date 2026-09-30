"""`setup`: instala e confere o runtime compartilhado do getbrolls.

`check()` só lê: sonda os mesmos executáveis obrigatórios do `doctor`, pela mesma regra
(`commands.readiness_probe`), mais os arquivos de dados, e para o que faltar devolve os
comandos que resolvem. `ready` do `setup --check` e do `doctor` batem. Nunca cria pasta
nem arquivo.

`install()` monta a `.venv` do yt-dlp no lugar definitivo (`_paths.runtime_target`): o
console script do pip grava o caminho absoluto do Python, então a pasta não pode ser
montada noutro lugar e renomeada. Quem lê só aceita a parte com marcador `ready`, que é
gravado por último; uma trava do sistema (`runtime._acquire_lock`) serializa quem monta,
e o kernel a solta se o processo morrer. `run_step` é o único ponto que abre processo.
"""

# pylint: disable=cyclic-import
# `cyclic-import` vem de getbrolls.bootstrap <-> getbrolls.commands: o `commands` importa
# este módulo de forma tardia (dentro de `_execute_toolchain`), o que quebra o ciclo em
# tempo de execução; o pylint só permite suprimir R0401 no módulo.

import collections
import contextlib
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import _paths, commands, runtime
from .config import SECRET_ENV_SUFFIXES, TOOL_PATH_KEYS
from .errors import UsageError
from .models import now
from .social import LAYOUTS

# Executável do `doctor` → id do passo; os que não estão aqui usam o próprio nome.
STEP_IDS = {"yt-dlp": "ytdlp", "playwright-cli": "playwright"}
# Passos que o runtime da instalação resolve (vêm primeiro); os outros são do sistema.
RUNTIME_STEPS = ("ytdlp", "playwright")
# Pin → executável que ele fixa: um pin quebrado reprova o passo, como no `doctor`.
_PIN_TOOLS = {**{key: name for name, key in TOOL_PATH_KEYS.items()}, "GB_VENV_PATH": "yt-dlp"}
# Pins que tiram a `.venv` das mãos do `setup`.
_VENV_PINS = ("GB_YTDLP_PATH", "GB_VENV_PATH")

PART_STEP = {"venv": "ytdlp", "tools": "playwright"}
# Teto de cada passo, em segundos; estourou → a árvore do processo é encerrada (código 124).
STEP_TIMEOUT_S = {"venv": 300, "pip": 1200, "probe": 60}
TAIL_LINES = 40
TIMED_OUT = 124
NOT_FOUND = 127
LOCK_NAME = ".getbrolls-setup.lock"
LOW_DISK_BYTES = 1 << 30
_PIP_FLAGS = ("--disable-pip-version-check", "--no-input", "--progress-bar", "off")
_UPGRADE_SPECS = ("yt-dlp[default]", "yt-dlp-ejs")
_BATCH_SAFE = re.compile(r"[A-Za-z0-9_\-=.]+")
_ENV_DROPPED = ("PYTHONPATH", "PYTHONHOME")

PIP_VERSION_FAILED = (
    "Falha ao instalar as dependências Python: seu Python é {version}; o conjunto fixado foi validado "
    "em Python 3.11–3.13. Rode o getbrolls com um Python dessa faixa ou atualize requirements.txt "
    "como um conjunto revisado."
)
PIP_NETWORK_FAILED = (
    "Falha ao baixar as dependências Python: sem acesso à rede ou ao índice de pacotes (PyPI, proxy, "
    "certificado). Confira a conexão e rode o `setup` de novo."
)
PIP_FAILED = "Falha ao instalar as dependências Python (pip saiu com {code}); veja `output_tail`."
ENSUREPIP_MISSING = (
    "O Python que roda o getbrolls não consegue criar venv (falta o ensurepip). No Debian/Ubuntu, "
    "instale o pacote `python3-venv` (ou `python3.X-venv` da sua versão) e rode o `setup` de novo."
)
VENV_FAILED = "Falha ao criar a venv (python -m venv saiu com {code}); veja `output_tail`."
PROBE_FAILED = "A venv foi criada, mas o yt-dlp não respondeu; veja `output_tail`."
STEP_TIMED_OUT = "O passo `{label}` passou do tempo limite ({seconds} s) e foi encerrado."
LOCKED = "Outro `setup` está instalando nesta pasta agora; espere ele terminar e rode de novo."
FOREIGN_BROKEN = (
    "A pasta do runtime ({folder}) já existe, não foi criada pelo getbrolls e está incompleta; "
    "apague essa pasta ou aponte GB_RUNTIME_DIR para outra."
)
NOT_A_FOLDER = (
    "A pasta do runtime ({folder}) é um link ou não é uma pasta comum; o `setup` não apaga isso. "
    "Remova o link à mão ou aponte GB_RUNTIME_DIR para outra pasta."
)
PINNED = "yt-dlp fixado por {key}: o `setup` não mexe na venv."
UPGRADE_PINNED = (
    "yt-dlp está fixado por {key} (ambiente, .env ou perfil): `setup --upgrade ytdlp` só atualiza a venv "
    "gerenciada. Atualize o yt-dlp desse caminho à mão ou remova a variável."
)
UPGRADE_FOREIGN = "A venv em uso não foi criada pelo getbrolls: `setup --upgrade ytdlp` só atualiza a venv gerenciada."
UPGRADE_FAILED = "A atualização do yt-dlp falhou; a venv foi refeita na versão fixada. {reason}"
TOOLS_PENDING = "O Playwright CLI ainda não é instalado pelo `setup`: siga os `commands` do passo `playwright`."
LOW_DISK = "Pouco espaço livre na pasta do runtime ({free} MiB; recomendado 1 GiB ou mais)."

_NETWORK_MARKERS = (
    "newconnectionerror",
    "max retries exceeded",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname",
    "could not fetch url",
    "connecttimeout",
    "readtimeouterror",
    "proxyerror",
    "sslerror",
    "certificate_verify_failed",
    "network is unreachable",
    "connection refused",
)
_VERSION_MARKERS = ("requires-python", "requires a different python")


@dataclass(frozen=True)
class StepResult:
    """Código de saída e as últimas linhas (stdout e stderr juntos) de um passo."""

    returncode: int
    output: tuple[str, ...]


def _progress(message: str) -> None:
    print(f"getbrolls setup: {message}", file=sys.stderr, flush=True)


def _assert_batch_safe(argv: list[str]) -> None:
    """`.cmd`/`.bat` passam pelo cmd.exe: só argumentos constantes e inofensivos (bug se não)."""
    if argv and argv[0].lower().endswith((".cmd", ".bat")):
        unsafe = [arg for arg in argv[1:] if not _BATCH_SAFE.fullmatch(arg)]
        if unsafe:
            raise RuntimeError(f"argumento não constante para um script .cmd/.bat: {unsafe!r}")


def _install_env(**extra: str) -> dict[str, str]:
    """Ambiente do passo: sem segredos nem `PYTHONPATH`/`PYTHONHOME` herdados."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.endswith(SECRET_ENV_SUFFIXES) and key not in _ENV_DROPPED
    }
    env.update(extra)
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    """Encerra o processo e os filhos dele (grupo próprio no POSIX, `taskkill /T` no Windows)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False, timeout=30)
    else:
        killpg = getattr(os, "killpg", None)
        if killpg is not None:
            with contextlib.suppress(OSError):
                killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    if proc.poll() is None:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


def run_step(
    argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int, label: str
) -> StepResult:
    """Roda um passo da instalação: o único lugar deste módulo que abre processo.

    Nunca usa shell. A saída é lida numa thread (o tempo limite vale mesmo com o filho
    calado) e só vai para o stderr quando ele é um terminal; o resultado guarda as últimas
    linhas. Tempo esgotado encerra a árvore do processo e devolve 124; Ctrl+C encerra a
    árvore e segue adiante; executável ausente devolve 127.
    """
    _assert_batch_safe(argv)
    windows = os.name == "nt"
    try:
        proc = subprocess.Popen(  # pylint: disable=consider-using-with  # encerrado abaixo em todo caminho
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            # Grupo próprio: o tempo limite e o Ctrl+C encerram o passo e os filhos dele.
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if windows else 0,
            start_new_session=not windows,
        )
    except OSError as exc:
        return StepResult(NOT_FOUND, (f"{label}: {exc.strerror or exc}",))
    tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
    echo = sys.stderr.isatty()
    stream = proc.stdout
    if stream is None:  # stdout=PIPE sempre abre o pipe; só para o verificador de tipos
        raise RuntimeError("run_step sem pipe de saída")

    def read():
        for line in stream:
            tail.append(line.rstrip("\r\n"))
            if echo:
                sys.stderr.write(line)

    reader = threading.Thread(target=read, name=f"setup-{label}", daemon=True)
    reader.start()
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        returncode = TIMED_OUT
    except KeyboardInterrupt:
        _kill_tree(proc)
        raise
    finally:
        reader.join(timeout=10)
        stream.close()
    return StepResult(returncode, tuple(tail))


class _RootLock:
    """Trava exclusiva de uma raiz do runtime; outro `setup` na mesma raiz é recusado."""

    def __init__(self, root: Path):
        self.root = root
        self._stream = None

    def __enter__(self):
        if self.root.is_relative_to(_paths.gb_home()):
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            self.root.mkdir(parents=True, exist_ok=True)
        stream = (self.root / LOCK_NAME).open("a+", encoding="utf-8")  # pylint: disable=consider-using-with
        try:
            runtime._acquire_lock(stream)  # pylint: disable=protected-access
        except BlockingIOError as exc:
            stream.close()
            raise ValueError(LOCKED) from exc
        self._stream = stream
        return self

    def __exit__(self, *exc_info):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                runtime._release_lock(stream)  # pylint: disable=protected-access
            finally:
                stream.close()


def _venv_python(folder: Path) -> Path:
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_ytdlp(folder: Path) -> Path | None:
    return next((folder / relative for relative in LAYOUTS if (folder / relative).is_file()), None)


def _is_link(path: Path) -> bool:
    """Link simbólico ou, no Windows, qualquer ponto de reanálise (junção inclusive)."""
    try:
        info = path.lstat()
    except OSError:
        return False
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(reparse)


def _part_state(part: str, root: Path) -> str:
    """`ready` | `owned` (marcador nosso, qualquer outro estado) | `foreign_ok` | `foreign_broken` | `absent`."""
    folder = root / _paths.RUNTIME_PARTS[part]
    if _paths.read_marker(part, root) is not None:
        if _paths.part_ready(part, root) and not _is_link(folder) and _venv_ytdlp(folder) is not None:
            return "ready"
        return "owned"
    if not os.path.lexists(folder):
        return "absent"
    return "foreign_ok" if _venv_ytdlp(folder) is not None else "foreign_broken"


def _remove_part(folder: Path) -> None:
    """Apaga a parte só quando é uma pasta comum; link (ou arquivo) nunca é seguido nem apagado."""
    if not os.path.lexists(folder):
        return
    if _is_link(folder) or not folder.is_dir():
        raise UsageError(NOT_A_FOLDER.format(folder=folder.name))
    shutil.rmtree(folder)


def _clear_marker(part: str, root: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        _paths.marker_path(part, root).unlink()


def _free_space_warning(root: Path) -> str | None:
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return None
    return LOW_DISK.format(free=free // (1 << 20)) if free < LOW_DISK_BYTES else None


def _python_in_range() -> bool:
    return (3, 11) <= sys.version_info[:2] <= (3, 13)


def _classify_pip(result: StepResult) -> str:
    """Mensagem para a falha do pip: rede/índice, versão do Python ou genérica."""
    text = "\n".join(result.output).lower()
    if any(marker in text for marker in _VERSION_MARKERS):
        return PIP_VERSION_FAILED.format(version=_python_version())
    if any(marker in text for marker in _NETWORK_MARKERS):
        return PIP_NETWORK_FAILED
    if "no matching distribution" in text:
        return PIP_NETWORK_FAILED if _python_in_range() else PIP_VERSION_FAILED.format(version=_python_version())
    return PIP_FAILED.format(code=result.returncode)


def _python_version() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


class _StepFailedError(Exception):
    """Um passo da montagem falhou; `message` vai para a pessoa, `result` para o `output_tail`."""

    def __init__(self, message: str, result: StepResult):
        super().__init__(message)
        self.message = message
        self.result = result


def _step(argv: list[str], *, root: Path, label: str, kind: str) -> StepResult:
    _progress(f"{label}…")
    result = run_step(argv, cwd=root, env=_install_env(), timeout=STEP_TIMEOUT_S[kind], label=label)
    if result.returncode == TIMED_OUT:
        raise _StepFailedError(STEP_TIMED_OUT.format(label=label, seconds=STEP_TIMEOUT_S[kind]), result)
    return result


def _ytdlp_version(folder: Path, root: Path) -> str:
    ytdlp = _venv_ytdlp(folder)
    if ytdlp is None:
        raise _StepFailedError(PROBE_FAILED, StepResult(1, ()))
    result = _step([str(ytdlp), "--version"], root=root, label="version", kind="probe")
    lines = [line.strip() for line in result.output if line.strip()]
    if result.returncode != 0 or not lines:
        raise _StepFailedError(PROBE_FAILED, result)
    return lines[-1]


def _build_venv(root: Path) -> str:
    """Monta a `.venv` em `root` (marcador `building` antes, `ready` por último); devolve a versão do yt-dlp."""
    folder = root / _paths.RUNTIME_PARTS["venv"]
    _paths.write_marker("venv", root, "building")
    _remove_part(folder)
    result = _step([sys.executable, "-m", "venv", str(folder)], root=root, label="venv", kind="venv")
    if result.returncode != 0:
        text = "\n".join(result.output).lower()
        message = ENSUREPIP_MISSING if "ensurepip" in text else VENV_FAILED.format(code=result.returncode)
        raise _StepFailedError(message, result)
    recorded = root / "requirements.txt"
    shutil.copyfile(_paths.data_path("requirements.txt"), recorded)
    python = str(_venv_python(folder))
    result = _step(
        [python, "-m", "pip", "install", *_PIP_FLAGS, "-r", str(recorded)], root=root, label="pip", kind="pip"
    )
    if result.returncode != 0:
        raise _StepFailedError(_classify_pip(result), result)
    result = _step([python, "-c", "import yt_dlp, yt_dlp_ejs"], root=root, label="probe", kind="probe")
    if result.returncode != 0:
        raise _StepFailedError(PROBE_FAILED, result)
    version = _ytdlp_version(folder, root)
    _paths.write_marker("venv", root, "ready")
    return version


def _fail(entry: dict, root: Path, exc: _StepFailedError) -> dict:
    """Falha limpa: nada meio montado fica para trás e o marcador some."""
    try:
        _remove_part(root / _paths.RUNTIME_PARTS["venv"])
    finally:
        _clear_marker("venv", root)
    entry.update(status="failed", error=exc.message, output_tail=list(exc.result.output))
    _progress(f"falhou: {exc.message}")
    return entry


def _pinned_key() -> str | None:
    return next((key for key in _VENV_PINS if os.environ.get(key)), None)


def _entry(part: str) -> dict:
    target = _paths.runtime_target(part)
    return {
        "part": part,
        "step": PART_STEP[part],
        "status": None,
        "path": str(target.path),
        "source": target.source,
        "sha": _paths.part_sha(part),
        "seconds": 0.0,
        "error": None,
        "output_tail": [],
        "warnings": [],
    }


def _ensure_venv(entry: dict) -> dict:
    """Deixa a `.venv` pronta (ou diz por que não); chamada com a trava da raiz já tomada."""
    root = Path(entry["path"]).parent
    state = _part_state("venv", root)
    if state == "ready":
        entry["status"] = "already"
        return entry
    if state == "foreign_ok":
        entry["status"] = "adopted"
        return entry
    if state == "foreign_broken":
        raise UsageError(FOREIGN_BROKEN.format(folder=_paths.RUNTIME_PARTS["venv"]))
    warning = _free_space_warning(root)
    if warning:
        entry["warnings"].append(warning)
        _progress(warning)
    try:
        entry["version"] = _build_venv(root)
    except _StepFailedError as exc:
        return _fail(entry, root, exc)
    entry["status"] = "installed"
    return entry


def _upgrade(entry: dict) -> dict:
    """`--upgrade ytdlp` sob a trava: marcador `upgrading`; falhou → refaz a venv fixada."""
    root = Path(entry["path"]).parent
    folder = root / _paths.RUNTIME_PARTS["venv"]
    upgrade: dict = {"status": None, "version": None, "error": None, "output_tail": []}
    _paths.write_marker("venv", root, "upgrading")
    argv = [str(_venv_python(folder)), "-m", "pip", "install", *_PIP_FLAGS, "--upgrade", *_UPGRADE_SPECS]
    try:
        result = _step(argv, root=root, label="upgrade", kind="pip")
        if result.returncode != 0:
            raise _StepFailedError(_classify_pip(result), result)
        version = _ytdlp_version(folder, root)
    except _StepFailedError as exc:
        upgrade.update(status="failed", error=UPGRADE_FAILED.format(reason=exc.message))
        upgrade["output_tail"] = list(exc.result.output)
        _progress("a atualização falhou; refazendo a venv na versão fixada")
        try:
            entry["version"] = _build_venv(root)
            entry["status"] = "installed"
        except _StepFailedError as rebuild:
            _fail(entry, root, rebuild)
        return upgrade
    _paths.write_marker("venv", root, "ready", {"upgraded": {"yt-dlp": version, "at": now()}})
    entry["version"] = version
    upgrade.update(status="upgraded", version=version)
    return upgrade


def ensure_venv(upgrade: bool = False) -> tuple[dict, dict | None]:
    """Entrada da `.venv` para o resultado do `setup` (e o bloco `upgrade`, se pedido)."""
    entry = _entry("venv")
    started = time.monotonic()
    pinned = _pinned_key()
    if pinned:
        if upgrade:
            raise UsageError(UPGRADE_PINNED.format(key=pinned))
        entry.update(status="pinned", note=PINNED.format(key=pinned))
        return entry, None
    if entry["sha"] is None:
        entry.update(status="skipped", error=commands.data_fix())
        return entry, None
    conflict = _paths.runtime_conflict("venv")
    if conflict:
        raise UsageError(conflict)
    upgraded = None
    with _RootLock(Path(entry["path"]).parent):
        _ensure_venv(entry)
        if upgrade:
            if entry["status"] == "adopted":
                raise UsageError(UPGRADE_FOREIGN)
            if entry["status"] != "failed":
                upgraded = _upgrade(entry)
    entry["seconds"] = round(time.monotonic() - started, 1)
    return entry, upgraded


def _tools_pending() -> dict:
    entry = _entry("tools")
    entry.update(status="pending", note=TOOLS_PENDING)
    return entry


def install(upgrade: str | None = None) -> dict:
    """Instala o que o `setup` já sabe montar e devolve o `check()` com o que foi feito."""
    _progress("conferindo a venv do yt-dlp")
    venv, upgraded = ensure_venv(upgrade == "ytdlp")
    entries = [venv, _tools_pending()]
    result = check()
    notes = [
        f"{entry['step']}: {entry['status']}"
        for entry in entries
        if entry["status"] in ("failed", "skipped", "pending")
    ]
    if upgraded is not None and upgraded["status"] == "failed":
        notes.append("upgrade do yt-dlp: failed")
    if notes:
        result["summary"]["line"] += " Setup: " + "; ".join(notes) + "."
    _progress(result["summary"]["line"])
    ordered = {"ready": result["ready"], "summary": result["summary"], "installed": entries}
    if upgraded is not None:
        ordered["upgrade"] = upgraded
    return {**ordered, "runtime": result["runtime"], "steps": result["steps"]}


def _step_names():
    """(id, executável) na ordem: runtime primeiro, depois o sistema na ordem do `doctor`."""
    names = [(STEP_IDS.get(name, name), name) for name in commands.REQUIRED_EXECUTABLES]
    first = sorted((pair for pair in names if pair[0] in RUNTIME_STEPS), key=lambda p: RUNTIME_STEPS.index(p[0]))
    return first + [pair for pair in names if pair[0] not in RUNTIME_STEPS]


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
    tools = _paths.tools_dir().path
    sources = (data / "package.json", data / "package-lock.json")
    if os.name == "nt":
        copy = [_line("mkdir", tools), *(_line("copy", source, tools) for source in sources)]
    else:
        copy = [_line("mkdir", "-p", tools), _line("cp", *sources, tools)]
    npm = _line("npm", "ci", "--prefix", tools, "--ignore-scripts", "--no-audit", "--no-fund")
    return {"ytdlp": [_paths.cli_prefix_text() + " setup"], "playwright": [*copy, npm]}


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
    """`setup --check` só confere; `setup` instala a venv do yt-dlp; `--upgrade ytdlp` também a atualiza."""
    if getattr(args, "check", False):
        return check()
    return install(upgrade=getattr(args, "upgrade", None))
