"""`setup`: instala e confere o runtime compartilhado do getbrolls.

`check()` só lê: sonda os mesmos executáveis obrigatórios do `doctor`, pela mesma regra
(`commands.readiness_probe`), mais os arquivos de dados, e para o que faltar devolve os
comandos que resolvem. `ready` do `setup --check` e do `doctor` batem. Nunca cria pasta
nem arquivo.

`install()` monta as duas partes no lugar definitivo (`_paths.runtime_target`): a `.venv`
do yt-dlp (o console script do pip grava o caminho absoluto do Python, então a pasta não
pode ser montada noutro lugar e renomeada) e a `.tools` do Playwright CLI (`npm ci`).
Quem lê só aceita a parte com marcador `ready`, que é gravado por último; uma trava do
sistema (`runtime._acquire_lock`) serializa quem monta, e o kernel a solta se o processo
morrer. `run_step` é o único ponto que abre processo. FFmpeg, ffprobe, curl e Node são do
sistema: só conferidos, com a dica de instalação do sistema operacional.

`where()` só lê: diz onde cada parte fica e qual está em uso, sem criar nada.

A `.venv` só vale com o Python dela funcionando: além dos arquivos (`_paths.part_ready`),
`venv_health` roda `<python da venv> -I -c "import yt_dlp"` com tempo curto. `setup`
refaz a venv que não passa; `setup --check` e `doctor` a dão como ausente, mesmo com um
yt-dlp global no PATH (o que vale é o executável em uso, `social.ytdlp_in_use`).
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
from .errors import LockedError, UsageError
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
STEP_TIMEOUT_S = {"venv": 300, "pip": 1200, "npm": 1200, "probe": 60, "health": 20}
# Sonda barata da venv em uso: o Python dela importa o yt_dlp? (`venv_health`)
HEALTH_LABEL = "health"
MIN_NODE_MAJOR = 22
# argv do npm: constante (sem caminho), então passa pela regra do `.cmd` no Windows; a
# pasta vai no `cwd` (o npm usa o `package.json` dali) e o cache, pelo ambiente.
_NPM_ARGS = ("ci", "--ignore-scripts", "--no-audit", "--no-fund")
_TOOLS_FILES = ("package.json", "package-lock.json")
NPM_CACHE = ".npm-cache"
TAIL_LINES = 40
TIMED_OUT = 124
NOT_FOUND = 127
LOCK_NAME = ".getbrolls-setup.lock"
LOW_DISK_BYTES = 1 << 30
_PIP_FLAGS = ("--disable-pip-version-check", "--no-input", "--progress-bar", "off")
_UPGRADE_SPECS = ("yt-dlp[default]", "yt-dlp-ejs")
_BATCH_SAFE = re.compile(r"[A-Za-z0-9_\-=.]+")
_ENV_DROPPED = ("PYTHONPATH", "PYTHONHOME")
# Segredos que o passo do npm precisa: o `.npmrc` de um registro privado costuma ler
# `${NPM_TOKEN}`. Os outros `*_TOKEN`/`*_KEY` continuam de fora de todo passo.
_NPM_SECRETS = ("NPM_TOKEN",)

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
ROOT_IS_LINK = (
    "A pasta do runtime ({folder}) é um link (ou junção); o `setup` não instala através de link. "
    "Remova o link à mão ou aponte GB_RUNTIME_DIR para uma pasta de verdade."
)
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
UPGRADE_REBUILD_FAILED = (
    "A atualização do yt-dlp falhou e a venv não pôde ser refeita na versão fixada: ela foi removida e o "
    "yt-dlp do getbrolls não está pronto. Rode `{fix}` quando a causa for resolvida. Atualização: {reason} "
    "Refazer: {rebuild}"
)
UPGRADE_UNCHANGED = (
    "O yt-dlp continua na versão {version}: ou ela já é a mais nova, ou o índice de pacotes não respondeu "
    "(rede, proxy). Nada mudou na venv."
)
VENV_BROKEN = (
    "A venv do yt-dlp em uso não funciona (o Python dela não importa o yt_dlp; ele pode ter sido removido "
    "ou atualizado). Rode `{fix}` para refazê-la."
)
VENV_PIN_BROKEN = (
    "A venv de GB_VENV_PATH não funciona (o Python dela não importa o yt_dlp). Conserte essa venv ou remova a variável."
)
VENV_EXPECTED = (
    "A venv gerenciada do yt-dlp está pela metade (marcador `{status}`): o yt-dlp do PATH não a substitui. "
    "Rode `{fix}`."
)
YTDLP_FROM_PATH = (
    "yt-dlp do PATH, fora do runtime do getbrolls: vale, mas a versão é a desse executável; `setup` instala a "
    "versão fixada na venv gerenciada."
)
NPM_MISSING = (
    "npm não encontrado no PATH: o Playwright CLI (Instagram) ficou de fora. Instale o Node {major}+ "
    "(ele traz o npm) e rode o `setup` de novo."
)
NODE_MISSING = (
    "Node não encontrado no PATH: o Playwright CLI (Instagram) precisa de Node {major}+. Instale e rode o "
    "`setup` de novo."
)
NODE_TOO_OLD = (
    "O Playwright CLI (Instagram) precisa de Node {major}+; o Node do PATH é {version}. Atualize e rode o "
    "`setup` de novo."
)
NPM_FAILED = (
    "Falha ao instalar o Playwright CLI (npm ci saiu com {code}); veja `output_tail`. Sem acesso ao "
    "registro do npm (rede, proxy, certificado), confira a conexão e rode o `setup` de novo."
)
TOOLS_PROBE_FAILED = "O npm terminou, mas o Playwright CLI não respondeu; veja `output_tail`."
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

# Ferramenta do sistema → sistema operacional → como instalar. Só dica: o `setup` não instala.
_NODE_HINTS = {
    "darwin": "brew install node",
    "linux": "instale o Node 22+ (nodejs.org ou o gerenciador da distribuição)",
    "nt": "winget install --id OpenJS.NodeJS.LTS -e",
}
_FFMPEG_HINTS = {
    "darwin": "brew install ffmpeg",
    "linux": "sudo apt install ffmpeg",
    "nt": "winget install Gyan.FFmpeg",
}
SYSTEM_HINTS: dict[str, dict[str, str]] = {
    "ffmpeg": _FFMPEG_HINTS,
    "ffprobe": _FFMPEG_HINTS,
    "curl": {"darwin": "brew install curl", "linux": "sudo apt install curl", "nt": "winget install --id cURL.cURL -e"},
    "node": _NODE_HINTS,
    "npx": _NODE_HINTS,
    "npm": _NODE_HINTS,
}


def system_hint(name: str, platform: str | None = None) -> str | None:
    """Como instalar uma ferramenta do sistema aqui (`darwin`, `linux` ou `nt`); `None` se não é do sistema."""
    if platform is None:
        platform = "nt" if sys.platform.startswith(("win", "cygwin")) else sys.platform
    hints = SYSTEM_HINTS.get(name)
    if hints is None:
        return None
    return hints.get(platform, hints["linux"])


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


def _install_env(*, keep: tuple[str, ...] = (), **extra: str) -> dict[str, str]:
    """Ambiente do passo: sem segredos nem `PYTHONPATH`/`PYTHONHOME` herdados.

    `keep` devolve segredos nomeados que o passo precisa (o `NPM_TOKEN` do `npm ci`).
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if (key in keep or not key.endswith(SECRET_ENV_SUFFIXES)) and key not in _ENV_DROPPED
    }
    env.update(extra)
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    """Encerra o processo e os filhos dele (grupo próprio no POSIX, `taskkill /T` no Windows)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        # taskkill travado ou ausente não pode esconder o erro do passo: o `kill` abaixo encerra o filho.
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False, timeout=30
            )
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
        if _is_link(self.root):
            # `runtime/<sha>` como link instalaria no alvo dele, fora do GB_HOME.
            raise UsageError(ROOT_IS_LINK.format(folder=self.root.name))
        if self.root.is_relative_to(_paths.gb_home()):
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        else:
            self.root.mkdir(parents=True, exist_ok=True)
        stream = (self.root / LOCK_NAME).open("a+", encoding="utf-8")  # pylint: disable=consider-using-with
        try:
            runtime._acquire_lock(stream)  # pylint: disable=protected-access
        except BlockingIOError as exc:
            stream.close()
            raise LockedError(LOCKED) from exc
        self._stream = stream
        return self

    def __exit__(self, *exc_info):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                runtime._release_lock(stream)  # pylint: disable=protected-access
            finally:
                stream.close()


def _venv_ytdlp(folder: Path) -> Path | None:
    return next((folder / relative for relative in LAYOUTS if (folder / relative).is_file()), None)


def _tools_cli(folder: Path) -> Path | None:
    """O Playwright CLI que o `npm ci` deixa em `node_modules/.bin` (o `.cmd` no Windows)."""
    names = ("playwright-cli.cmd", "playwright-cli") if os.name == "nt" else ("playwright-cli",)
    bin_dir = folder / "node_modules" / ".bin"
    return next((bin_dir / name for name in names if (bin_dir / name).is_file()), None)


# Arquivo que prova que a parte funciona: o executável que o resto do getbrolls usa.
_PROBES = {"venv": _venv_ytdlp, "tools": _tools_cli}


def _is_link(path: Path) -> bool:
    """Link simbólico ou, no Windows, qualquer ponto de reanálise (junção inclusive)."""
    try:
        info = path.lstat()
    except OSError:
        return False
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(reparse)


def venv_health(folder: Path) -> StepResult:
    """Roda `<python da venv> -I -c "import yt_dlp"` (tempo curto, sem shell, sem segredos).

    `-I` isola o interpretador: nem `PYTHONPATH`, nem site do usuário, nem a pasta atual
    decidem o import. Python ausente ou que não executa (link quebrado, "bad interpreter")
    sai diferente de 0, como o import que falha.
    """
    python = _paths.venv_python(folder)
    return run_step(
        [str(python), "-I", "-c", "import yt_dlp"],
        cwd=folder if folder.is_dir() else None,
        env=_install_env(),
        timeout=STEP_TIMEOUT_S["health"],
        label=HEALTH_LABEL,
    )


def ytdlp_problem(source: str | None, executable: str | None) -> str | None:
    """Por que o yt-dlp em uso não vale, ou `None`: a regra do `doctor` e do `setup --check`.

    Da venv (`source == "venv"`): o Python dela tem que importar o yt_dlp. Do PATH: vale,
    salvo quando a venv gerenciada ficou pela metade (marcador sem `ready`), que o PATH
    não substitui.
    """
    fix = _paths.cli_prefix_text() + " setup"
    if source == "venv" and executable:
        if venv_health(Path(executable).parent.parent).returncode == 0:  # bin/yt-dlp → .venv
            return None
        return VENV_PIN_BROKEN if os.environ.get("GB_VENV_PATH") else VENV_BROKEN.format(fix=fix)
    if source == "path" and not _pinned_key():
        root = _paths.runtime_target("venv").path.parent
        marker = _paths.read_marker("venv", root)
        if marker is not None and not _paths.part_ready("venv", root):
            return VENV_EXPECTED.format(status=marker.get("status"), fix=fix)
    return None


def _part_state(part: str, root: Path) -> str:
    """`ready` | `owned` (marcador nosso, qualquer outro estado) | `foreign_ok` | `foreign_broken` | `absent`.

    A `.venv` só é `ready` se, além dos arquivos, o Python dela importa o yt_dlp.
    """
    folder = root / _paths.RUNTIME_PARTS[part]
    probe = _PROBES[part]
    if _paths.read_marker(part, root) is not None:
        intact = _paths.part_ready(part, root) and not _is_link(folder) and probe(folder) is not None
        if intact and (part != "venv" or venv_health(folder).returncode == 0):
            return "ready"
        return "owned"
    if not os.path.lexists(folder):
        return "absent"
    return "foreign_ok" if probe(folder) is not None else "foreign_broken"


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


def _step(argv: list[str], *, cwd: Path, label: str, kind: str, env: dict[str, str] | None = None) -> StepResult:
    _progress(f"{label}…")
    result = run_step(
        argv, cwd=cwd, env=env if env is not None else _install_env(), timeout=STEP_TIMEOUT_S[kind], label=label
    )
    if result.returncode == TIMED_OUT:
        raise _StepFailedError(STEP_TIMED_OUT.format(label=label, seconds=STEP_TIMEOUT_S[kind]), result)
    return result


def _ytdlp_version(folder: Path, root: Path) -> str:
    ytdlp = _venv_ytdlp(folder)
    if ytdlp is None:
        raise _StepFailedError(PROBE_FAILED, StepResult(1, ()))
    result = _step([str(ytdlp), "--version"], cwd=root, label="version", kind="probe")
    lines = [line.strip() for line in result.output if line.strip()]
    if result.returncode != 0 or not lines:
        raise _StepFailedError(PROBE_FAILED, result)
    return lines[-1]


def _build_venv(root: Path) -> str:
    """Monta a `.venv` em `root` (marcador `building` antes, `ready` por último); devolve a versão do yt-dlp."""
    folder = root / _paths.RUNTIME_PARTS["venv"]
    _paths.write_marker("venv", root, "building")
    _remove_part(folder)
    result = _step([sys.executable, "-m", "venv", str(folder)], cwd=root, label="venv", kind="venv")
    if result.returncode != 0:
        text = "\n".join(result.output).lower()
        message = ENSUREPIP_MISSING if "ensurepip" in text else VENV_FAILED.format(code=result.returncode)
        raise _StepFailedError(message, result)
    recorded = root / "requirements.txt"
    shutil.copyfile(_paths.data_path("requirements.txt"), recorded)
    python = str(_paths.venv_python(folder))
    result = _step(
        [python, "-m", "pip", "install", *_PIP_FLAGS, "-r", str(recorded)], cwd=root, label="pip", kind="pip"
    )
    if result.returncode != 0:
        raise _StepFailedError(_classify_pip(result), result)
    result = _step([python, "-c", "import yt_dlp, yt_dlp_ejs"], cwd=root, label="probe", kind="probe")
    if result.returncode != 0:
        raise _StepFailedError(PROBE_FAILED, result)
    version = _ytdlp_version(folder, root)
    _paths.write_marker("venv", root, "ready")
    return version


def _last_line(result: StepResult) -> str | None:
    lines = [line.strip() for line in result.output if line.strip()]
    return lines[-1] if lines else None


def _build_tools(root: Path, npm: str) -> str:
    """Monta a `.tools` em `root` com `npm ci` (marcador `building` antes, `ready` por último).

    Devolve a versão do Playwright CLI. Os dois arquivos do npm são copiados para a pasta:
    o `npm ci` instala exatamente o que o lock fixa, sem rodar scripts de pacote.
    """
    folder = root / _paths.RUNTIME_PARTS["tools"]
    _paths.write_marker("tools", root, "building")
    _remove_part(folder)
    folder.mkdir()
    for name in _TOOLS_FILES:
        shutil.copyfile(_paths.data_path(name), folder / name)
    env = _install_env(keep=_NPM_SECRETS, npm_config_cache=str(root / NPM_CACHE), npm_config_update_notifier="false")
    result = _step([npm, *_NPM_ARGS], cwd=folder, env=env, label="npm", kind="npm")
    if result.returncode != 0:
        raise _StepFailedError(NPM_FAILED.format(code=result.returncode), result)
    cli = _tools_cli(folder)
    if cli is None:
        raise _StepFailedError(TOOLS_PROBE_FAILED, result)
    result = _step([str(cli), "--version"], cwd=root, label="playwright", kind="probe")
    version = _last_line(result)
    if result.returncode != 0 or version is None:
        raise _StepFailedError(TOOLS_PROBE_FAILED, result)
    _paths.write_marker("tools", root, "ready")
    return version


def _fail(entry: dict, root: Path, exc: _StepFailedError) -> dict:
    """Falha limpa: nada meio montado fica para trás e o marcador some."""
    part = entry["part"]
    try:
        _remove_part(root / _paths.RUNTIME_PARTS[part])
    finally:
        _clear_marker(part, root)
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


def _ensure(entry: dict, build) -> dict:
    """Deixa a parte pronta (ou diz por que não); chamada com a trava da raiz já tomada.

    `build(root)` monta a parte e devolve a versão; pode levantar `_SkipError` antes de
    gravar qualquer coisa (falta um pré-requisito do sistema).
    """
    part = entry["part"]
    root = Path(entry["path"]).parent
    state = _part_state(part, root)
    if state == "ready":
        entry["status"] = "already"
        return entry
    if state == "foreign_ok":
        entry["status"] = "adopted"
        return entry
    if state == "foreign_broken":
        raise UsageError(FOREIGN_BROKEN.format(folder=_paths.RUNTIME_PARTS[part]))
    folder = root / _paths.RUNTIME_PARTS[part]
    if os.path.lexists(folder) and (_is_link(folder) or not folder.is_dir()):
        # Antes de rodar qualquer passo: a parte montada por cima de um link nunca é apagada.
        raise UsageError(NOT_A_FOLDER.format(folder=folder.name))
    warning = _free_space_warning(root)
    if warning:
        entry["warnings"].append(warning)
        _progress(warning)
    try:
        entry["version"] = build(root)
    except _SkipError as exc:
        entry.update(status="skipped", error=exc.message, hint=exc.hint)
        _progress(exc.message)
        return entry
    except _StepFailedError as exc:
        return _fail(entry, root, exc)
    entry["status"] = "installed"
    return entry


class _SkipError(Exception):
    """A parte não pode ser montada aqui por falta de algo do sistema; nada foi gravado."""

    def __init__(self, message: str, hint: str | None):
        super().__init__(message)
        self.message = message
        self.hint = hint


def _upgrade(entry: dict) -> dict:
    """`--upgrade ytdlp` sob a trava: marcador `upgrading`; falhou → refaz a venv fixada.

    `status`: `upgraded` (a versão mudou), `unchanged` (pip terminou e a versão é a mesma:
    já era a mais nova ou o índice não respondeu; vem com aviso) ou `failed` (com a venv
    refeita na versão fixada, ou removida quando nem isso deu: o `error` diz qual).
    """
    root = Path(entry["path"]).parent
    folder = root / _paths.RUNTIME_PARTS["venv"]
    upgrade: dict = {"status": None, "version": None, "previous": None, "error": None, "warnings": []}
    upgrade["output_tail"] = []
    try:
        upgrade["previous"] = entry.get("version") or _ytdlp_version(folder, root)
    except _StepFailedError:
        upgrade["previous"] = None
    earlier = (_paths.read_marker("venv", root) or {}).get("upgraded")
    _paths.write_marker("venv", root, "upgrading")
    argv = [str(_paths.venv_python(folder)), "-m", "pip", "install", *_PIP_FLAGS, "--upgrade", *_UPGRADE_SPECS]
    try:
        result = _step(argv, cwd=root, label="upgrade", kind="pip")
        if result.returncode != 0:
            raise _StepFailedError(_classify_pip(result), result)
        version = _ytdlp_version(folder, root)
    except _StepFailedError as exc:
        upgrade["status"] = "failed"
        upgrade["output_tail"] = list(exc.result.output)
        _progress("a atualização falhou; refazendo a venv na versão fixada")
        try:
            entry["version"] = _build_venv(root)
            entry["status"] = "installed"
            upgrade["error"] = UPGRADE_FAILED.format(reason=exc.message)
        except _StepFailedError as rebuild:
            _fail(entry, root, rebuild)
            fix = _paths.cli_prefix_text() + " setup"
            upgrade["error"] = UPGRADE_REBUILD_FAILED.format(fix=fix, reason=exc.message, rebuild=rebuild.message)
        _progress(upgrade["error"])
        return upgrade
    entry["version"] = version
    upgrade["version"] = version
    if version == upgrade["previous"]:
        _paths.write_marker("venv", root, "ready", {"upgraded": earlier} if earlier else None)
        upgrade["status"] = "unchanged"
        upgrade["warnings"].append(UPGRADE_UNCHANGED.format(version=version))
        _progress(upgrade["warnings"][-1])
        return upgrade
    _paths.write_marker("venv", root, "ready", {"upgraded": {"yt-dlp": version, "at": now()}})
    upgrade["status"] = "upgraded"
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
        _ensure(entry, _build_venv)
        if upgrade:
            if entry["status"] == "adopted":
                raise UsageError(UPGRADE_FOREIGN)
            if entry["status"] != "failed":
                upgraded = _upgrade(entry)
    entry["seconds"] = round(time.monotonic() - started, 1)
    return entry, upgraded


def _node_major(version: str) -> int | None:
    match = re.match(r"v?(\d+)\.", version)
    return int(match.group(1)) if match else None


def _node_gate(node: str, npm: str):
    """`build` que antes confere o Node do PATH: menor que o mínimo pula a parte sem gravar nada."""

    def build(root: Path) -> str:
        result = _step([node, "--version"], cwd=root, label="node", kind="probe")
        version = _last_line(result) or "desconhecido"
        major = _node_major(version) if result.returncode == 0 else None
        if major is None or major < MIN_NODE_MAJOR:
            raise _SkipError(NODE_TOO_OLD.format(major=MIN_NODE_MAJOR, version=version), system_hint("node"))
        return _build_tools(root, npm)

    return build


def ensure_tools() -> dict:
    """Entrada da `.tools` (Playwright CLI) para o resultado do `setup`."""
    entry = _entry("tools")
    started = time.monotonic()
    if entry["sha"] is None:
        entry.update(status="skipped", error=commands.data_fix())
        return entry
    # `which` acha o `npm.cmd` no Windows (PATHEXT); o argv dele é constante (`_NPM_ARGS`).
    npm, node = shutil.which("npm"), shutil.which("node")
    if not npm or not node:
        message = (NPM_MISSING if node else NODE_MISSING).format(major=MIN_NODE_MAJOR)
        entry.update(status="skipped", error=message, hint=system_hint("node"))
        _progress(message)
        return entry
    conflict = _paths.runtime_conflict("tools")
    if conflict:
        raise UsageError(conflict)
    with _RootLock(Path(entry["path"]).parent):
        _ensure(entry, _node_gate(node, npm))
    entry["seconds"] = round(time.monotonic() - started, 1)
    return entry


def _summary(steps: list[dict], notes: list[str] | tuple[str, ...] = ()) -> dict:
    """Linha do veredito: nunca "Runtime pronto" com passo faltando ou com o setup incompleto."""
    missing = [entry["id"] for entry in steps if not entry["ok"]]
    total = len(steps)
    if missing:
        line = f"Faltam {len(missing)} de {total} itens do runtime: {', '.join(missing)}. Siga `commands`."
    elif notes:
        line = f"Itens do runtime encontrados ({total} de {total}), mas o setup não concluiu tudo."
    else:
        line = f"Runtime pronto: {total} de {total} itens encontrados."
    if notes:
        line += " Setup: " + "; ".join(notes) + "."
    return {"line": line, "missing": missing}


def _hold_failed(steps: list[dict], entries: list[dict]) -> None:
    """Parte que o `setup` tentou montar e falhou reprova o passo, mesmo com o executável no PATH."""
    fix = _paths.cli_prefix_text() + " setup"
    for entry in entries:
        if entry["status"] != "failed":
            continue
        step = next(item for item in steps if item["id"] == entry["step"])
        step.update(ok=False, note=entry["error"], commands=[fix])


def install(upgrade: str | None = None) -> dict:
    """Instala as duas partes do runtime e devolve o `check()` com o que foi feito."""
    _progress("conferindo a venv do yt-dlp")
    venv, upgraded = ensure_venv(upgrade == "ytdlp")
    _progress("conferindo o Playwright CLI")
    entries = [venv, ensure_tools()]
    result = check()
    _hold_failed(result["steps"], entries)
    notes = [f"{entry['step']}: {entry['status']}" for entry in entries if entry["status"] in ("failed", "skipped")]
    if upgraded is not None and upgraded["status"] == "failed":
        notes.append("upgrade do yt-dlp: failed")
    result["summary"] = _summary(result["steps"], notes)
    result["ready"] = not result["summary"]["missing"]
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
    """Por id de passo: `(executável absoluto ou None, nota do pin quebrado ou None)`.

    O do yt-dlp leva um terceiro item: `{"source", "found", "problem"}` do executável em
    uso (`social.ytdlp_in_use`), mesmo quando ele não vale.
    """
    overrides, pin_problems, social, present = commands.readiness_probe()
    resolved = commands.doctor_resolved(overrides)
    broken = {_PIN_TOOLS.get(problem["item"]): problem["note"] for problem in pin_problems}
    found: dict[str, tuple] = {
        step: (resolved.get(name) if present.get(name) and name not in broken else None, broken.get(name))
        for step, name in _step_names()
    }
    detail = {"source": social.get("source"), "found": resolved.get("yt-dlp"), "problem": social.get("problem")}
    found["ytdlp"] = (*found["ytdlp"], detail)
    return found


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
    if _paths.origin() != "checkout" and (_paths.part_sha("venv") is None or _paths.part_sha("tools") is None):
        # Sem requirements.txt/package-lock.json não há o que instalar: só reinstalar repõe.
        fix = _paths.REINSTALL_COMMAND
    else:
        fix = _paths.cli_prefix_text() + " setup"
    steps = []
    for step, name in _step_names():
        path, pin_note, *extra = found.get(step, (None, None))
        detail = extra[0] if extra else {}
        shown = path or detail.get("found")
        entry: dict = {"id": step, "ok": path is not None, "found": str(Path(shown)) if shown else None}
        if detail:
            entry["source"] = detail.get("source")
        if pin_note:
            entry["commands"] = []
            entry["note"] = pin_note
        elif step in RUNTIME_STEPS:
            entry["commands"] = [] if path else [fix]
            if detail.get("problem"):
                entry["note"] = detail["problem"]
                if os.environ.get("GB_VENV_PATH"):
                    entry["commands"] = []
            elif path and detail.get("source") == "path":
                entry["note"] = YTDLP_FROM_PATH
        else:
            entry["commands"] = []
            entry["note"] = commands.SYSTEM_TOOLS
            entry["hint"] = system_hint(name)
        steps.append(entry)
    steps.append(_data_step())
    summary = _summary(steps)
    return {
        "ready": not summary["missing"],
        "summary": summary,
        "runtime": _paths.runtime_info(),
        "steps": steps,
    }


def _where_part(part: str) -> dict:
    target = _paths.runtime_target(part)
    root = target.path.parent
    marker = _paths.read_marker(part, root)
    in_use = _paths.venv_dir() if part == "venv" else _paths.tools_dir()
    executable = _PROBES[part](in_use.path)
    return {
        "path": str(target.path),
        "root": str(root),
        "source": target.source,
        "sha": _paths.part_sha(part),
        "exists": target.path.is_dir(),
        "marker": marker.get("status") if marker is not None else None,
        "managed": _paths.part_ready(part, root),
        "conflict": _paths.runtime_conflict(part),
        "in_use": {
            "path": str(in_use.path),
            "source": in_use.source,
            "executable": str(executable) if executable is not None else None,
        },
    }


def _where_safe(part: str) -> dict:
    try:
        return _where_part(part)
    except (OSError, ValueError) as exc:  # `DataRootError`/`UsageError` herdam de ValueError
        return {"error": str(exc)}


def where(part: str = "all") -> dict:
    """Onde fica cada parte do runtime (e qual está em uso), em JSON; só lê, nunca levanta.

    `path` é onde o `setup` instala; `in_use` é o que o getbrolls usa agora (pode ser a
    pasta do checkout enquanto a compartilhada não está pronta). Sem `ready`: sai 0, salvo
    erro de uso do `.env` (`--env-file`/`GB_ENV_FILE` que não existe, linha recusada), que
    a CLI lê antes de qualquer comando e sai 2.
    """
    if part != "all":
        return {"part": part, **_where_safe(part)}
    return {
        "gb_home": str(_paths.gb_home()),
        "explicit": bool(os.environ.get("GB_RUNTIME_DIR")),
        **{name: _where_safe(name) for name in _paths.RUNTIME_PARTS},
    }


def run(args) -> dict:
    """`setup --check` só confere; `--where` só mostra as pastas; `setup` instala; `--upgrade ytdlp` também atualiza."""
    if getattr(args, "check", False):
        return check()
    if getattr(args, "where", None):
        return where(args.where)
    return install(upgrade=getattr(args, "upgrade", None))
