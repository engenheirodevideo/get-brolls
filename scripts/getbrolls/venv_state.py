"""Estado da `.venv` e do yt-dlp em uso: as sondas de cada parte do runtime, a saúde da venv e a regra que o
`doctor` e o `setup --check` usam para dizer se o yt-dlp vale. Só lê; quem monta é `bootstrap`."""

import logging
import os
import time
from pathlib import Path

from . import _paths, bootstrap_steps, logs
from .bootstrap_messages import (
    _VENV_PINS,
    HEALTH_LABEL,
    STEP_TIMEOUT_S,
    VENV_BROKEN,
    VENV_EXPECTED,
    VENV_PIN_BROKEN,
)
from .bootstrap_steps import StepResult, _install_env, _is_link, _refused
from .errors import UsageError
from .social import LAYOUTS

_log = logs.get("bootstrap")


def _venv_ytdlp(folder: Path) -> Path | None:
    # lenient: tool discovery in the runtime: not found sends the user to setup
    return next((folder / relative for relative in LAYOUTS if (folder / relative).is_file()), None)


def _tools_cli(folder: Path) -> Path | None:
    """O Playwright CLI que o `npm ci` deixa em `node_modules/.bin` (o `.cmd` no Windows)."""
    names = ("playwright-cli.cmd", "playwright-cli") if os.name == "nt" else ("playwright-cli",)
    bin_dir = folder / "node_modules" / ".bin"
    # lenient: tool discovery in the runtime: not found sends the user to setup
    return next((bin_dir / name for name in names if (bin_dir / name).is_file()), None)


# Arquivo que prova que a parte funciona: o executável que o resto do getbrolls usa.
_PROBES = {"venv": _venv_ytdlp, "tools": _tools_cli}


def venv_health(folder: Path) -> StepResult:
    """Roda `<python da venv> -I -c "import yt_dlp"` (tempo curto, sem shell, sem segredos).

    `-I` isola o interpretador: nem `PYTHONPATH`, nem site do usuário, nem a pasta atual
    decidem o import. Python ausente ou que não executa (link quebrado, "bad interpreter")
    sai diferente de 0, como o import que falha.
    """
    python = _paths.venv_python(folder)
    started = time.monotonic()
    result = bootstrap_steps.run_step(
        [str(python), "-I", "-c", "import yt_dlp"],
        # lenient: working folder of the health probe only: None runs it from the current folder
        cwd=folder if folder.is_dir() else None,
        env=_install_env(),
        timeout=STEP_TIMEOUT_S["health"],
        label=HEALTH_LABEL,
    )
    logs.event(
        _log,
        logging.DEBUG,
        "setup_health",
        status="ok" if result.returncode == 0 else "error",
        exit=result.returncode,
        ms=round((time.monotonic() - started) * 1000),
    )
    return result


def ytdlp_problem(source: str | None, executable: str | None) -> str | None:
    """Por que o yt-dlp em uso não vale, ou `None`: a regra do `doctor` e do `setup --check`.

    Da venv (`source == "venv"`): o Python dela tem que importar o yt_dlp. Do PATH: vale,
    salvo quando a venv gerenciada ficou pela metade (marcador sem `ready`), que o PATH
    não substitui.
    """
    fix = _paths.cli_prefix_text() + " setup"
    if source == "venv" and executable:
        folder = Path(executable).parent.parent  # bin/yt-dlp → .venv
        healthy = venv_health(folder).returncode == 0
        root = folder.parent
        marker = _paths.read_marker("venv", root)
        if marker is not None and not _paths.marker_supported(marker):
            return _paths.newer_marker_message("venv", root)
        if healthy and (marker is None or _paths.part_ready("venv", root)):
            return None
        if healthy:  # venv gerenciada que o `setup` refaz: pela metade ou sem o Python base
            status = marker.get("status") if marker else None
            return VENV_EXPECTED.format(status=status, fix=fix) if status != "ready" else VENV_BROKEN.format(fix=fix)
        return VENV_PIN_BROKEN if os.environ.get("GB_VENV_PATH") else VENV_BROKEN.format(fix=fix)
    if source == "path" and not _pinned_key():
        root = _paths.runtime_target("venv").path.parent
        marker = _paths.read_marker("venv", root)
        # O marcador de uma versão mais nova não é venv pela metade desta: o yt-dlp do PATH vale.
        if marker is not None and _paths.marker_supported(marker) and not _paths.part_ready("venv", root):
            return VENV_EXPECTED.format(status=marker.get("status"), fix=fix)
    return None


def _part_state(part: str, root: Path) -> str:
    """`ready` | `owned` (marcador nosso, qualquer outro estado) | `foreign_ok` | `foreign_broken` | `absent`.

    A `.venv` só é `ready` se, além dos arquivos, o Python dela importa o yt_dlp.
    """
    folder = root / _paths.RUNTIME_PARTS[part]
    probe = _PROBES[part]
    marker = _paths.read_marker(part, root)
    if marker is not None and not _paths.marker_supported(marker):
        # Marcador de uma versão mais nova: não é desta, nem para adotar nem para montar por cima.
        raise _refused("runtime_conflict", UsageError(_paths.newer_marker_message(part, root)))
    if marker is not None:
        intact = _paths.part_ready(part, root) and not _is_link(folder) and probe(folder) is not None
        if intact and (part != "venv" or venv_health(folder).returncode == 0):
            return "ready"
        return "owned"
    # lenient: part absent: nothing to remove, and installing it there raises on a locked folder
    if not os.path.lexists(folder):
        return "absent"
    return "foreign_ok" if probe(folder) is not None else "foreign_broken"


def _pinned_key() -> str | None:
    return next((key for key in _VENV_PINS if os.environ.get(key)), None)
