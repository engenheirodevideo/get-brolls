"""Os passos do `setup`: o único ponto que abre processo (`run_step`), a trava da raiz do runtime e a leitura
da saída do pip. `bootstrap` monta as partes em cima disto e reexporta os nomes."""

import collections
import contextlib
import logging
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import _paths, effects, logs, runtime
from .bootstrap_messages import (
    _BATCH_SAFE,
    _CONFLICT_MARKERS,
    _ENV_DROPPED,
    _HASH_MARKERS,
    _NETWORK_MARKERS,
    _PYTHON_TAG_HINTS,
    _VERSION_MARKERS,
    KEPT_ERROR_LINES,
    LOCK_NAME,
    LOCKED,
    NOT_FOUND,
    PIP_CONFLICT_FAILED,
    PIP_FAILED,
    PIP_HASH_FAILED,
    PIP_NETWORK_FAILED,
    PIP_VERSION_FAILED,
    ROOT_IS_LINK,
    STEP_TIMED_OUT,
    STEP_TIMEOUT_S,
    TAIL_LINES,
    TIMED_OUT,
)
from .config import SECRET_ENV_SUFFIXES
from .errors import LockedError, UsageError

_log = logs.get("bootstrap")


def _refused(reason: str, exc: Exception) -> Exception:
    """Registra a recusa (`setup_refused`: motivo e `error_code`) e devolve `exc` para levantar."""
    return logs.refusal(_log, "setup_refused", exc, reason=reason)


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
    with effects.op("proc.kill", proc.args, pid=proc.pid):
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


class _Tail:
    """As últimas `TAIL_LINES` linhas de um passo, mais as linhas `ERROR:` do começo que o corte levaria."""

    def __init__(self) -> None:
        self._last: collections.deque[tuple[int, str]] = collections.deque(maxlen=TAIL_LINES)
        self._errors: list[tuple[int, str]] = []
        self._count = 0
        self._after_error = False

    def add(self, text: str) -> None:
        """Registra a próxima linha; guarda também as `ERROR:` (e a linha seguinte) do começo."""
        self._count += 1
        self._last.append((self._count, text))
        if (self._after_error or text.startswith("ERROR:")) and len(self._errors) < KEPT_ERROR_LINES:
            self._errors.append((self._count, text))
        self._after_error = text.startswith("ERROR:")

    def lines(self) -> tuple[str, ...]:
        """Em ordem de leitura; uma linha `ERROR:` que ainda está na cauda não se repete."""
        first = self._last[0][0] if self._last else 0
        kept = [text for number, text in self._errors if number < first]
        return tuple(kept + [text for _number, text in self._last])


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
    with effects.op("proc.run", argv) as running:
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
            running.set(exit=NOT_FOUND)
            return StepResult(NOT_FOUND, (f"{label}: {exc.strerror or exc}",))
        tail = _Tail()
        echo = sys.stderr.isatty()
        stream = proc.stdout
        if stream is None:  # stdout=PIPE sempre abre o pipe; só para o verificador de tipos
            raise RuntimeError("run_step sem pipe de saída")

        def read():
            for line in stream:
                tail.add(line.rstrip("\r\n"))
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
        running.set(exit=returncode)
        return StepResult(returncode, tail.lines())


class _RootLock:
    """Trava exclusiva de uma raiz do runtime; outro `setup` na mesma raiz é recusado."""

    def __init__(self, root: Path):
        self.root = root
        self._stream = None

    def __enter__(self):
        if _is_link(self.root):
            # `runtime/<sha>` como link instalaria no alvo dele, fora do GB_HOME.
            raise _refused("runtime_link", UsageError(ROOT_IS_LINK.format(folder=self.root.name)))
        if self.root.is_relative_to(_paths.gb_home()):
            effects.mkdir(self.root, mode=0o700, parents=True, exist_ok=True)
        else:
            effects.mkdir(self.root, parents=True, exist_ok=True)
        # effect-exempt: lock file of the runtime root; a busy lock already logs lock_busy, as in runtime
        stream = (self.root / LOCK_NAME).open("a+", encoding="utf-8", newline="\n")  # pylint: disable=consider-using-with
        try:
            runtime._acquire_lock(stream)  # pylint: disable=protected-access
        except BlockingIOError as exc:
            stream.close()
            raise runtime.lock_busy(LOCK_NAME, time.monotonic(), LockedError(LOCKED)) from exc
        self._stream = stream
        return self

    def __exit__(self, *exc_info):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                runtime._release_lock(stream)  # pylint: disable=protected-access
            finally:
                stream.close()


def _is_link(path: Path) -> bool:
    """Link simbólico ou, no Windows, qualquer ponto de reanálise (junção inclusive)."""
    try:
        info = path.lstat()
    # lenient: link probe only: a path it cannot lstat is no link; the rmtree or install after it raises
    except OSError:
        return False
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(reparse)


def _classify_pip_code(result: StepResult, file: str = "requirements.txt") -> tuple[str, str]:
    """`(código, mensagem)` da falha do pip: hash, versão do Python (só se o pip disser), conflito, rede ou genérica.

    Hash primeiro: é a falha final, e um retry de conexão antes dela, na mesma saída, não pode escondê-la.
    `file` é o arquivo de requisitos do passo, nomeado na mensagem de hash.
    """
    text = "\n".join(result.output).lower()
    if any(marker in text for marker in _HASH_MARKERS):
        return "pip_hash", PIP_HASH_FAILED.format(file=file)
    version_failed = PIP_VERSION_FAILED.format(version=_python_version())
    if any(marker in text for marker in _VERSION_MARKERS):
        return "pip_python_version", version_failed
    if any(marker in text for marker in _CONFLICT_MARKERS):
        return "pip_conflict", PIP_CONFLICT_FAILED
    if any(marker in text for marker in _NETWORK_MARKERS):
        return "pip_network", PIP_NETWORK_FAILED
    if "no matching distribution" in text and any(hint in text for hint in _PYTHON_TAG_HINTS):
        return "pip_python_version", version_failed
    return "pip_failed", PIP_FAILED.format(code=result.returncode)


def _classify_pip(result: StepResult) -> str:
    """Mensagem para a falha do pip (o código do log vem de `_classify_pip_code`)."""
    return _classify_pip_code(result)[1]


def _python_version() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


class _StepFailedError(Exception):
    """Um passo da montagem falhou; `message` vai para a pessoa, `result` para o `output_tail`.

    `code` é o motivo estável para o log (`timeout`, `venv_failed`, `pip_network`, `npm_failed`…).
    """

    # No `event=op` (`logs.error_code`, `logs.outcome_for`): erro de operação, não bug, e uma falha,
    # não uma recusa.
    error_code = "INVALID_DATA"
    outcome = "error"

    def __init__(self, message: str, result: StepResult, code: str = "step_failed"):
        super().__init__(message)
        self.message = message
        self.result = result
        self.code = code


def _log_step_end(label: str, kind: str, started: float, status: str, exit_code: int | None) -> None:
    """`setup_step_end`: como o passo acabou e quanto levou; só o rótulo, nunca a linha de comando."""
    logs.event(
        _log,
        logging.INFO if status == "ok" else logging.WARNING,
        "setup_step_end",
        step=label,
        kind=kind,
        status=status,
        exit=exit_code,
        ms=round((time.monotonic() - started) * 1000),
    )


def _step(argv: list[str], *, cwd: Path, label: str, kind: str, env: dict[str, str] | None = None) -> StepResult:
    _progress(f"{label}…")
    logs.event(_log, logging.INFO, "setup_step_start", step=label, kind=kind)
    started = time.monotonic()
    try:
        result = run_step(
            argv, cwd=cwd, env=env if env is not None else _install_env(), timeout=STEP_TIMEOUT_S[kind], label=label
        )
    except KeyboardInterrupt:
        _log_step_end(label, kind, started, "interrupted", None)
        raise
    if result.returncode == TIMED_OUT:
        _log_step_end(label, kind, started, "timeout", result.returncode)
        raise _StepFailedError(STEP_TIMED_OUT.format(label=label, seconds=STEP_TIMEOUT_S[kind]), result, code="timeout")
    _log_step_end(label, kind, started, "ok" if result.returncode == 0 else "error", result.returncode)
    return result
