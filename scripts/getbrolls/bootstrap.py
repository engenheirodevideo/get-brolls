"""`setup`: instala e confere o runtime compartilhado do getbrolls.

`check()` só lê: sonda os mesmos executáveis obrigatórios do `doctor`, pela mesma regra
(`readiness.readiness_probe`), mais os arquivos de dados, e para o que faltar devolve os
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

import contextlib
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

from . import _paths, effects, logs, readiness, runtime_parts
from .bootstrap_messages import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    _HASHED_FLAGS,
    _NPM_ARGS,
    _NPM_SECRETS,
    _PIN_TOOLS,
    _PIP_FLAGS,
    _TOOLS_FILES,
    _UPGRADE_SPECS,
    _VENV_PINS,
    ENSUREPIP_MISSING,
    FOREIGN_BROKEN,
    HEALTH_LABEL,
    KEPT_ERROR_LINES,
    LOCK_NAME,
    LOW_DISK,
    LOW_DISK_BYTES,
    MIN_NODE_MAJOR,
    NODE_MISSING,
    NODE_TOO_OLD,
    NOT_A_FOLDER,
    NPM_CACHE,
    NPM_FAILED,
    NPM_MISSING,
    PART_STEP,
    PINNED,
    PIP_CONFLICT_FAILED,
    PIP_FAILED,
    PIP_HASH_FAILED,
    PIP_NETWORK_FAILED,
    PIP_VERSION_FAILED,
    PROBE_FAILED,
    RUNTIME_STEPS,
    STEP_IDS,
    STEP_TIMEOUT_S,
    SYSTEM_HINTS,
    TAIL_LINES,
    TIMED_OUT,
    TOOLS_PROBE_FAILED,
    UPGRADE_FAILED,
    UPGRADE_FOREIGN,
    UPGRADE_PINNED,
    UPGRADE_REBUILD_FAILED,
    UPGRADE_UNCHANGED,
    VENV_BROKEN,
    VENV_EXPECTED,
    VENV_FAILED,
    VENV_PIN_BROKEN,
    YTDLP_FROM_PATH,
)
from .bootstrap_steps import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    StepResult,
    _assert_batch_safe,
    _classify_pip,
    _classify_pip_code,
    _install_env,
    _is_link,
    _kill_tree,
    _log_step_end,
    _progress,
    _python_version,
    _refused,
    _RootLock,
    _step,
    _StepFailedError,
    _Tail,
    run_step,
)
from .errors import UsageError
from .models import now
from .venv_state import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    _PROBES,
    _part_state,
    _pinned_key,
    _tools_cli,
    _venv_ytdlp,
    venv_health,
    ytdlp_problem,
)

_log = logs.get("bootstrap")


def system_hint(name: str, platform: str | None = None) -> str | None:
    """Como instalar uma ferramenta do sistema aqui (`darwin`, `linux` ou `nt`); `None` se não é do sistema."""
    if platform is None:
        platform = "nt" if sys.platform.startswith(("win", "cygwin")) else sys.platform
    hints = SYSTEM_HINTS.get(name)
    if hints is None:
        return None
    return hints.get(platform, hints["linux"])


def _remove_part(folder: Path) -> None:
    """Apaga a parte só quando é uma pasta comum; link (ou arquivo) nunca é seguido nem apagado."""
    # lenient: part absent: nothing to remove, and installing it there raises on a locked folder
    if not os.path.lexists(folder):
        return
    # lenient: lexists above saw the name, so its folder is searchable; a link or file is refused
    if _is_link(folder) or not folder.is_dir():
        raise _refused("not_a_folder", UsageError(NOT_A_FOLDER.format(folder=folder.name)))
    effects.rmtree(folder)


def _clear_marker(part: str, root: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        effects.unlink(_paths.marker_path(part, root))


def _free_space_warning(root: Path) -> str | None:
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return None
    return LOW_DISK.format(free=free // (1 << 20)) if free < LOW_DISK_BYTES else None


def _ytdlp_version(folder: Path, root: Path) -> str:
    ytdlp = _venv_ytdlp(folder)
    if ytdlp is None:
        raise _StepFailedError(PROBE_FAILED, StepResult(1, ()), code="probe_failed")
    result = _step([str(ytdlp), "--version"], cwd=root, label="version", kind="probe")
    lines = [line.strip() for line in result.output if line.strip()]
    if result.returncode != 0 or not lines:
        raise _StepFailedError(PROBE_FAILED, result, code="probe_failed")
    return lines[-1]


def _pip_hashed(python: str, root: Path, name: str, label: str) -> None:
    """Instala a cópia gravada em `root` do arquivo de requisitos `name` dos dados; falha → `_StepFailedError`.

    `--require-hashes`: o pip só instala o arquivo cujo sha256 está no arquivo; um hash que não confere (ou
    falta) é a falha `pip_hash`. `--only-binary :all:`: só rodas, nunca sdist: o pip não confere hash do que
    instala para construir um sdist, e a opção de linha de comando vence `PIP_NO_BINARY`/pip.conf. Plataforma
    sem roda falha rápido, como falha do pip. O `--upgrade ytdlp` não usa nenhuma das duas.
    """
    recorded = root / name
    effects.copy_file(_paths.data_path(name), recorded)
    result = _step(
        [python, "-m", "pip", "install", *_PIP_FLAGS, *_HASHED_FLAGS, "-r", str(recorded)],
        cwd=root,
        label=label,
        kind="pip",
    )
    if result.returncode != 0:
        code, message = _classify_pip_code(result, name)
        raise _StepFailedError(message, result, code=code)


def _build_venv(root: Path) -> str:
    """Monta a `.venv` em `root` (marcador `building` antes, `ready` por último); devolve a versão do yt-dlp."""
    folder = root / _paths.RUNTIME_PARTS["venv"]
    runtime_parts.write_marker("venv", root, "building")
    _remove_part(folder)
    result = _step([sys.executable, "-m", "venv", str(folder)], cwd=root, label="venv", kind="venv")
    if result.returncode != 0:
        text = "\n".join(result.output).lower()
        message = ENSUREPIP_MISSING if "ensurepip" in text else VENV_FAILED.format(code=result.returncode)
        raise _StepFailedError(message, result, code="ensurepip_missing" if "ensurepip" in text else "venv_failed")
    python = str(_paths.venv_python(folder))
    # O pip que o `venv` traz carrega um certifi antigo: o primeiro download é o pip fixado em
    # `requirements-bootstrap.txt`, com hash, antes do conjunto do runtime.
    _pip_hashed(python, root, "requirements-bootstrap.txt", "pip-bootstrap")
    _pip_hashed(python, root, "requirements.txt", "pip")
    result = _step([python, "-c", "import yt_dlp, yt_dlp_ejs"], cwd=root, label="probe", kind="probe")
    if result.returncode != 0:
        raise _StepFailedError(PROBE_FAILED, result, code="probe_failed")
    version = _ytdlp_version(folder, root)
    runtime_parts.write_marker("venv", root, "ready")
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
    runtime_parts.write_marker("tools", root, "building")
    _remove_part(folder)
    effects.mkdir(folder)
    for name in _TOOLS_FILES:
        effects.copy_file(_paths.data_path(name), folder / name)
    env = _install_env(keep=_NPM_SECRETS, npm_config_cache=str(root / NPM_CACHE), npm_config_update_notifier="false")
    result = _step([npm, *_NPM_ARGS], cwd=folder, env=env, label="npm", kind="npm")
    if result.returncode != 0:
        raise _StepFailedError(NPM_FAILED.format(code=result.returncode), result, code="npm_failed")
    cli = _tools_cli(folder)
    if cli is None:
        raise _StepFailedError(TOOLS_PROBE_FAILED, result, code="tools_probe_failed")
    result = _step([str(cli), "--version"], cwd=root, label="playwright", kind="probe")
    version = _last_line(result)
    if result.returncode != 0 or version is None:
        raise _StepFailedError(TOOLS_PROBE_FAILED, result, code="tools_probe_failed")
    runtime_parts.write_marker("tools", root, "ready")
    return version


def _fail(entry: dict, root: Path, exc: _StepFailedError) -> dict:
    """Falha limpa: nada meio montado fica para trás e o marcador some."""
    part = entry["part"]
    try:
        _remove_part(root / _paths.RUNTIME_PARTS[part])
    finally:
        _clear_marker(part, root)
    entry.update(status="failed", error=exc.message, output_tail=list(exc.result.output))
    logs.event(_log, logging.WARNING, "setup_part_failed", part=part, reason=exc.code, exit=exc.result.returncode)
    _progress(f"falhou: {exc.message}")
    return entry


def _log_part(entry: dict, started: float) -> None:
    """`setup_part`: como a parte (`venv` ou `tools`) ficou e quanto levou; a falha sobe a WARNING."""
    logs.event(
        _log,
        logging.WARNING if entry["status"] == "failed" else logging.INFO,
        "setup_part",
        part=entry["part"],
        status=entry["status"],
        version=entry.get("version"),
        ms=round((time.monotonic() - started) * 1000),
    )


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
        raise _refused("foreign_broken", UsageError(FOREIGN_BROKEN.format(folder=_paths.RUNTIME_PARTS[part])))
    folder = root / _paths.RUNTIME_PARTS[part]
    # lenient(2): a name seen by lexists sits in a searchable folder; a link or file is refused
    if os.path.lexists(folder) and (_is_link(folder) or not folder.is_dir()):
        # Antes de rodar qualquer passo: a parte montada por cima de um link nunca é apagada.
        raise _refused("not_a_folder", UsageError(NOT_A_FOLDER.format(folder=folder.name)))
    warning = _free_space_warning(root)
    if warning:
        entry["warnings"].append(warning)
        _progress(warning)
    try:
        entry["version"] = build(root)
    except _SkipError as exc:
        entry.update(status="skipped", error=exc.message, hint=exc.hint)
        logs.event(_log, logging.INFO, "setup_part_skipped", part=part, reason=exc.code)
        _progress(exc.message)
        return entry
    except _StepFailedError as exc:
        return _fail(entry, root, exc)
    entry["status"] = "installed"
    return entry


class _SkipError(Exception):
    """A parte não pode ser montada aqui por falta de algo do sistema; nada foi gravado.

    `code` é o motivo estável para o log (`node_too_old`).
    """

    def __init__(self, message: str, hint: str | None, code: str = "system_missing"):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


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
    runtime_parts.write_marker("venv", root, "upgrading")
    argv = [str(_paths.venv_python(folder)), "-m", "pip", "install", *_PIP_FLAGS, "--upgrade", *_UPGRADE_SPECS]
    try:
        result = _step(argv, cwd=root, label="upgrade", kind="pip")
        if result.returncode != 0:
            code, message = _classify_pip_code(result)
            raise _StepFailedError(message, result, code=code)
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
        runtime_parts.write_marker("venv", root, "ready", {"upgraded": earlier} if earlier else None)
        upgrade["status"] = "unchanged"
        upgrade["warnings"].append(UPGRADE_UNCHANGED.format(version=version))
        _progress(upgrade["warnings"][-1])
        return upgrade
    runtime_parts.write_marker("venv", root, "ready", {"upgraded": {"yt-dlp": version, "at": now()}})
    upgrade["status"] = "upgraded"
    return upgrade


def ensure_venv(upgrade: bool = False) -> tuple[dict, dict | None]:
    """Entrada da `.venv` para o resultado do `setup` (e o bloco `upgrade`, se pedido)."""
    entry = _entry("venv")
    started = time.monotonic()
    pinned = _pinned_key()
    if pinned:
        if upgrade:
            raise _refused("upgrade_pinned", UsageError(UPGRADE_PINNED.format(key=pinned)))
        entry.update(status="pinned", note=PINNED.format(key=pinned))
        _log_part(entry, started)
        return entry, None
    if entry["sha"] is None:
        entry.update(status="skipped", error=readiness.data_fix())
        logs.event(_log, logging.INFO, "setup_part_skipped", part="venv", reason="data_missing")
        _log_part(entry, started)
        return entry, None
    conflict = _paths.runtime_conflict("venv")
    if conflict:
        raise _refused("runtime_conflict", UsageError(conflict))
    upgraded = None
    with _RootLock(Path(entry["path"]).parent):
        _ensure(entry, _build_venv)
        if upgrade:
            if entry["status"] == "adopted":
                raise _refused("upgrade_foreign", UsageError(UPGRADE_FOREIGN))
            if entry["status"] != "failed":
                upgraded = _upgrade(entry)
                logs.event(
                    _log,
                    logging.WARNING if upgraded["status"] == "failed" else logging.INFO,
                    "setup_upgrade",
                    status=upgraded["status"],
                    previous=upgraded["previous"],
                    version=upgraded["version"],
                )
    entry["seconds"] = round(time.monotonic() - started, 1)
    _log_part(entry, started)
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
            raise _SkipError(
                NODE_TOO_OLD.format(major=MIN_NODE_MAJOR, version=version), system_hint("node"), code="node_too_old"
            )
        return _build_tools(root, npm)

    return build


def ensure_tools() -> dict:
    """Entrada da `.tools` (Playwright CLI) para o resultado do `setup`."""
    entry = _entry("tools")
    started = time.monotonic()
    if entry["sha"] is None:
        entry.update(status="skipped", error=readiness.data_fix())
        logs.event(_log, logging.INFO, "setup_part_skipped", part="tools", reason="data_missing")
        _log_part(entry, started)
        return entry
    # `which` acha o `npm.cmd` no Windows (PATHEXT); o argv dele é constante (`_NPM_ARGS`).
    npm, node = shutil.which("npm"), shutil.which("node")
    if not npm or not node:
        message = (NPM_MISSING if node else NODE_MISSING).format(major=MIN_NODE_MAJOR)
        entry.update(status="skipped", error=message, hint=system_hint("node"))
        logs.event(
            _log, logging.INFO, "setup_part_skipped", part="tools", reason="npm_missing" if node else "node_missing"
        )
        _log_part(entry, started)
        _progress(message)
        return entry
    conflict = _paths.runtime_conflict("tools")
    if conflict:
        raise _refused("runtime_conflict", UsageError(conflict))
    with _RootLock(Path(entry["path"]).parent):
        _ensure(entry, _node_gate(node, npm))
    entry["seconds"] = round(time.monotonic() - started, 1)
    _log_part(entry, started)
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
    started = time.monotonic()
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
    logs.event(
        _log,
        logging.INFO if result["ready"] else logging.WARNING,
        "setup_done",
        ready=result["ready"],
        venv=venv["status"],
        tools=entries[1]["status"],
        upgrade=upgraded["status"] if upgraded is not None else None,
        missing=len(result["summary"]["missing"]),
        ms=round((time.monotonic() - started) * 1000),
    )
    _progress(result["summary"]["line"])
    ordered = {"ready": result["ready"], "summary": result["summary"], "installed": entries}
    if upgraded is not None:
        ordered["upgrade"] = upgraded
    return {**ordered, "runtime": result["runtime"], "steps": result["steps"]}


def _step_names():
    """(id, executável) na ordem: runtime primeiro, depois o sistema na ordem do `doctor`."""
    names = [(STEP_IDS.get(name, name), name) for name in readiness.REQUIRED_EXECUTABLES]
    first = sorted((pair for pair in names if pair[0] in RUNTIME_STEPS), key=lambda p: RUNTIME_STEPS.index(p[0]))
    return first + [pair for pair in names if pair[0] not in RUNTIME_STEPS]


def _resolved():
    """Por id de passo: `(executável absoluto ou None, nota do pin quebrado ou None)`.

    O do yt-dlp leva um terceiro item: `{"source", "found", "problem"}` do executável em
    uso (`social.ytdlp_in_use`), mesmo quando ele não vale.
    """
    overrides, pin_problems, social, present = readiness.readiness_probe()
    resolved = readiness.doctor_resolved(overrides)
    broken = {_PIN_TOOLS.get(problem["item"]): problem["note"] for problem in pin_problems}
    found: dict[str, tuple] = {
        step: (resolved.get(name) if present.get(name) and name not in broken else None, broken.get(name))
        for step, name in _step_names()
    }
    detail = {"source": social.get("source"), "found": resolved.get("yt-dlp"), "problem": social.get("problem")}
    found["ytdlp"] = (*found["ytdlp"], detail)
    return found


def _data_step():
    missing, unreadable = readiness.data_problems()
    if not missing and not unreadable:
        return {"id": "data", "ok": True, "found": str(_paths.data_root()), "commands": []}
    return {
        "id": "data",
        "ok": False,
        "found": None,
        "commands": [readiness.data_fix()] if missing else [],
        "note": ". ".join(readiness.data_problem_notes(missing, unreadable)),
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
            entry["note"] = readiness.SYSTEM_TOOLS
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


def _io_problem(exc: OSError) -> str:
    return "sem_permissao" if isinstance(exc, PermissionError) else "ilegivel"


def _folder_state(path: Path) -> tuple[bool | None, str | None]:
    """(`exists`, `problem`) de uma pasta para o diagnóstico: `None` e o motivo quando não dá
    para ver (`sem_permissao`, ou `ilegivel` para outro erro de E/S), nunca "não existe".

    Uma pasta que existe mas não abre (sem leitura ou sem busca) também tem `problem`: o que
    está dentro dela (o Python da `.venv`, o `playwright-cli`) não dá para conferir."""
    try:
        exists = _paths.strict_is_dir(path)
    except OSError as exc:
        return None, _io_problem(exc)
    # `os.access` usa o uid real e, no Windows, só o atributo somente-leitura: lá passa sempre.
    if exists and not os.access(path, os.R_OK | os.X_OK):
        return True, "sem_permissao"
    return exists, None


_IN_USE_UNKNOWN = {"path": None, "source": None, "executable": None}


def _in_use(part: str) -> dict:
    """A parte que o getbrolls usaria agora, com o executável dela."""
    found = _paths.venv_dir() if part == "venv" else _paths.tools_dir()
    executable = _PROBES[part](found.path)
    return {
        "path": str(found.path),
        "source": found.source,
        "executable": str(executable) if executable is not None else None,
    }


def _where_part(part: str) -> dict:
    target = _paths.runtime_target(part)
    root = target.path.parent
    marker = _paths.read_marker(part, root)
    exists, problem = _folder_state(target.path)
    # Com `problem`, `managed` e `in_use` não dão para saber: o 3.11–3.13 levantaria o EACCES
    # lá dentro, e o 3.14 diria "não pronta". Os dois respondem igual: `None`, e o motivo.
    managed, in_use = None, _IN_USE_UNKNOWN
    if problem is None:
        try:
            managed, in_use = _paths.part_ready(part, root), _in_use(part)
        except OSError as exc:
            managed, in_use, problem = None, _IN_USE_UNKNOWN, _io_problem(exc)
    return {
        "path": str(target.path),
        "root": str(root),
        "source": target.source,
        "sha": _paths.part_sha(part),
        "exists": exists,
        "problem": problem,
        "marker": marker.get("status") if marker is not None else None,
        "managed": managed,
        "conflict": _paths.runtime_conflict(part),
        "in_use": dict(in_use),
    }


def _where_safe(part: str) -> dict:
    try:
        return _where_part(part)
    except (OSError, ValueError) as exc:  # `DataRootError`/`UsageError` herdam de ValueError
        return {"error": str(exc)}


def where(part: str = "all") -> dict:
    """Onde fica cada parte do runtime (e qual está em uso), em JSON; só lê, nunca levanta.

    `path` é onde o `setup` instala; `in_use` é o que o getbrolls usa agora (pode ser a
    pasta do checkout enquanto a compartilhada não está pronta). `app_log` é onde o log do app
    da instalação grava (o arquivo só existe depois do primeiro comando que muda a instalação).
    Sem `ready`: sai 0, salvo erro de uso do `.env` (`--env-file`/`GB_ENV_FILE` que não existe,
    linha recusada), que a CLI lê antes de qualquer comando e sai 2.
    """
    if part != "all":
        return {"part": part, **_where_safe(part)}
    return {
        "gb_home": str(_paths.gb_home()),
        "app_log": str(logs.home_log_path()),
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
