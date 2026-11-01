"""Operational diagnostics. Never log command arguments or tokens; tracebacks are stored redacted."""

# pylint: disable=missing-function-docstring,broad-exception-caught,use-sequence-for-iteration,missing-class-docstring,cyclic-import
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0).
# `cyclic-import`: `runtime` -> `config` e `runtime` -> `layout` têm a volta dentro de função (o `.env` do
# `config` e o `audited` que mostra o layout); ficam como pares aceitos em `tests/import_rules/`.
# O que saiu daqui mora em `redaction`, `private_files`, `file_locks`, `error_codes`, `command_policy` e
# `diagnostics`; os nomes seguem acessíveis em `runtime` (as ligações logo abaixo dos imports).

import contextlib
import json
import logging
import re
import sys
import time
from pathlib import Path

from . import (
    _paths,
    command_policy,
    config,
    diagnostics,
    effects,
    error_codes,
    file_locks,
    labels,
    logs,
    private_files,
    redaction,
)
from .errors import ProviderError, Refusal, UsageError
from .models import now

# Moved to `command_policy`; bound here so every importer of `runtime` keeps working (the same objects).
ACTIVE = command_policy.ACTIVE
READ_ONLY_COMMANDS = command_policy.READ_ONLY_COMMANDS
READ_ONLY_ACTIONS = command_policy.READ_ONLY_ACTIONS
OWN_LOCK_ACTIONS = command_policy.OWN_LOCK_ACTIONS
SELF_LOCKED_COMMANDS = command_policy.SELF_LOCKED_COMMANDS
STAGE_COMMANDS = command_policy.STAGE_COMMANDS
LAYOUT_TOLERANT = command_policy.LAYOUT_TOLERANT
INSTALLATION_WRITES = command_policy.INSTALLATION_WRITES
QUIET_ERROR_COMMANDS = command_policy.QUIET_ERROR_COMMANDS
writes_installation = command_policy.writes_installation

# Moved to `error_codes`.
ERROR_EXIT = error_codes.ERROR_EXIT
EXIT_FOR_OTHER_ERRORS = error_codes.EXIT_FOR_OTHER_ERRORS
exit_code_for = error_codes.exit_code_for
error_code_for = error_codes.error_code_for

# Moved to `diagnostics`.
STDERR_TAIL_MAX_CHARS = diagnostics.STDERR_TAIL_MAX_CHARS
redact_diagnostic = diagnostics.redact_diagnostic
diagnostic_line = diagnostics.diagnostic_line
redacted_traceback = diagnostics.redacted_traceback
stderr_tail = diagnostics.stderr_tail
diagnostics_path = diagnostics.diagnostics_path

# Moved to `file_locks`.
_acquire_lock = file_locks._acquire_lock  # pylint: disable=protected-access
_release_lock = file_locks._release_lock  # pylint: disable=protected-access
lock_busy = file_locks.lock_busy
exclusive_lock = file_locks.exclusive_lock
try_lock = file_locks.try_lock

# Moved to `private_files`.
make_private = private_files.make_private
force_rmtree = private_files.force_rmtree
_ensure_private_file = private_files._ensure_private_file  # pylint: disable=protected-access
_owner_only_acl = private_files._owner_only_acl  # pylint: disable=protected-access

# Moved to `redaction`.
SECRET_NAME_RE = redaction.SECRET_NAME_RE
REDACT_LIMIT = redaction.REDACT_LIMIT
secret_name = redaction.secret_name
one_line = redaction.one_line
scrub_home = redaction.scrub_home
redact = redaction.redact
_HEADER_PATTERN = redaction._HEADER_PATTERN  # pylint: disable=protected-access
_BEARER_PATTERN = redaction._BEARER_PATTERN  # pylint: disable=protected-access


def record_warning(code, message):
    current = ACTIVE.get()
    if current is not None:
        current["warnings"].append({"code": code, "message": message})
    # Only the code: warning messages are prose written for the person and may
    # quote what they typed (an approver's name, a reason). The full message is
    # already in the command's JSON output; the log only needs to correlate.
    with contextlib.suppress(Exception):  # logging must never break a command
        logs.event(logs.get("runtime"), logging.WARNING, "warning", code=code)


def record_commit():
    current = ACTIVE.get()
    if current is not None:
        current["state_committed"] = True


def record_delivery_conflicts():
    """O `deliver` já entregou o que pôde e vai falhar nomeando os arquivos em conflito.

    É o que escolhe a dica do conflito (`_error_hint`): um `deliver` que gravou o estado
    (a troca de formato, que vem antes) e recusou por outro motivo leva a de `review`.
    Marca interna, só do evento: não vai para o JSON de erro."""
    current = ACTIVE.get()
    if current is not None:
        current["delivery_conflicts"] = True


def record_recovered():
    """O comando concluiu, antes de rodar, a gravação interrompida de um comando anterior.

    Não é gravação deste comando: não liga `state_committed` (nem a dica de `review`) se ele
    falhar depois; liga `recovered` no evento e no JSON de erro."""
    current = ACTIVE.get()
    if current is not None:
        current["recovered"] = True


@contextlib.contextmanager
def usage_errors():
    """Todo `ValueError` levantado dentro sai como `UsageError` (exit 2): formato de flag errado
    é erro de uso, não de conteúdo. A exceção é o `Refusal`: uma recusa de dados com `reason_code`
    segue como é (`INVALID_DATA`, exit 1), para o motivo chegar a quem automatiza.

    A recusa registrada dentro (`logs.refusal`) já sai com `USAGE_ERROR`, o código que a CLI
    vai dar ao erro convertido (`INVALID_DATA` para o `Refusal`): a linha do log e o JSON de erro
    nunca discordam.
    """
    with logs.refused_as("USAGE_ERROR"):
        try:
            yield
        except (UsageError, Refusal):
            raise
        except ValueError as exc:
            raise UsageError(str(exc)) from None


COMMAND_LOCK = ".command.lock"


@contextlib.contextmanager
def project_lock(project, wait_s=None):
    """Trava exclusiva do projeto. `wait_s`: segundos de espera pelo projeto ocupado; `None` vale
    `GB_LOCK_WAIT_S` (padrão 0). Ao fim da espera, `LOCKED`, como sempre."""
    if not project:
        yield
        return
    if wait_s is None:
        wait_s = config.lock_wait_s()

    root = Path(project).resolve() / "brolls"
    effects.mkdir(root, parents=True, exist_ok=True)
    with exclusive_lock(
        root / COMMAND_LOCK,
        "Outro comando está usando este projeto. Aguarde terminar antes de repetir.",
        wait_s=wait_s,
    ):
        yield


def is_folder(path):
    """`path` é uma pasta? Nunca levanta.

    No Python 3.11 e 3.12, `Path.is_dir()` relança o EACCES de uma pasta sem permissão; aqui a
    pasta que não dá para ver conta como ausente, e quem for usá-la de fato recebe o `OSError`
    dentro da auditoria (`IO_ERROR`), nunca um traceback no caminho do erro."""
    try:
        # lenient: documented never-raise view: the audit that uses the folder raises IO_ERROR
        return Path(path).is_dir()
    except OSError:
        return False


def _visible(path):
    """`path` existe? Nunca levanta: o que não dá para ver (EACCES) conta como ausente."""
    try:
        # lenient: documented never-raise view: what it cannot see counts as absent
        return Path(path).exists()
    except OSError:
        return False


def _absent(path):
    """`path` comprovadamente não existe? Nunca levanta.

    Só ENOENT e ENOTDIR contam: a pasta que não dá para ver (EACCES) não é um typo, e quem
    for usá-la recebe o `OSError` dentro da auditoria (`IO_ERROR`)."""
    try:
        Path(path).stat()
    except (FileNotFoundError, NotADirectoryError):
        return True
    # lenient: documented never-raise: what it cannot see is not proven absent (False)
    except OSError:
        return False
    return False


def _surface_layout_shape(command, project, typed):
    """Dá ao comando em curso o aviso `OLD_SHAPE` de um `project.json` de forma antiga, qualquer que seja o comando.

    Ler o layout aqui, uma vez por comando, é o que impede um leitor que só olha o `project.json` de passagem
    (`assets`, `roteiro`, `verify`…) de degradar o projeto para o layout 0 calado. Um `--project` que existe e
    não é pasta, ou um `project.json` que o sistema não abre, recusa o comando (`status`, `init` e `migrate` têm
    a recusa deles).
    """
    if not project:
        return
    from . import layout  # local: layout importa renamed, que importa este módulo

    folder = Path(project).expanduser()
    # strict_*: a pasta que não dá para ver é erro de E/S do comando, nunca "não é pasta"
    if command not in LAYOUT_TOLERANT and _paths.strict_exists(folder) and not _paths.strict_is_dir(folder):
        raise ValueError(f"{typed or project} não é uma pasta; aponte --project para a pasta do projeto.")
    layout.surface(project, strict=command not in LAYOUT_TOLERANT)


def missing_project_tree(command, project):
    """O comando é de etapa e a pasta `project` não existe (um typo no `--project`)?"""
    return command in STAGE_COMMANDS and bool(project) and _absent(project)


def _refuse_missing_project(project):
    # A pasta como a pessoa a digitou: resolvida, um link no caminho mudaria a grafia.
    raise ValueError(
        f"Projeto não encontrado em {project} (a pasta não existe); nenhum arquivo foi criado. "
        "Confira o --project, ou comece o projeto com search ou resolve."
    )


# Erro que veio de um plugin: "Plugin <id>: …" (todo erro do core sobre código de
# plugin usa esse prefixo) ou "Fonte X é do plugin Y, …" (fonte de plugin fora do
# ar, que já traz a própria dica). RULES.md não tem nada a ver com nenhum dos dois.
_PLUGIN_ERROR_RE = re.compile(r"Plugin [A-Za-z0-9_-]+: |Fonte \S+ é do plugin ")
PLUGIN_ERROR_HINT = "Veja plugins --action list / doctor e docs/SDK.md."


_RECOVERY_HINT = "Há uma gravação pendente (recovery_pending=true): o próximo comando a retoma antes de rodar."
_REVIEW_HINT = "O estado já foi gravado (state_committed=true): execute review para regenerar a página."
# `deliver` que grava os itens bons e recusa os que estão em conflito (`delivery_conflicts`):
# regenerar o Storyboard não resolve nada; o que falta é resolver os arquivos que a mensagem nomeia.
_DELIVER_HINT = (
    "Os itens sem conflito já foram entregues (state_committed=true); resolva os arquivos em "
    "conflito que a mensagem nomeia e rode deliver de novo."
)
# Só sem `state_committed`: diz que o estado do projeto (o manifesto) não foi gravado, não que
# nada foi para o disco (prévias e GIFs são gravados antes do journal).
_RECOVERED_HINT = (
    "Uma gravação interrompida de um comando anterior foi concluída antes deste (recovered=true); "
    "este falhou sem gravar o estado do projeto (state_committed=false). Corrija e repita."
)


def _error_hint(command, event):
    """Dica do erro, só quando há o que fazer além de corrigir e repetir; senão `None`.

    `roteiro` nunca recebe a de `review`: ela fala do Storyboard do b-roll e se confunde
    com `roteiro --action review`. O `deliver` que falhou nomeando arquivos em conflito
    também não: o que ele já gravou são os itens sem conflito, e a dica dele fala do
    conflito. Um `deliver` recusado antes (BRIEF.md inválido) depois de gravar a troca de
    formato leva a de `review`, como os outros. O comando que gravou o próprio estado
    nunca recebe a de `recovered`, nem quando também concluiu a gravação de outro.
    """
    if event.get("recovery_pending"):
        return _RECOVERY_HINT
    if event.get("state_committed"):
        if command == "deliver" and event.get("delivery_conflicts"):
            return _DELIVER_HINT
        return None if command == "roteiro" else _REVIEW_HINT
    if event.get("recovered"):
        return _RECOVERED_HINT
    return None


def provider_error_message(text):
    """Mensagem de `ProviderError` para a pessoa: built-in segue com o " Confira
    docs/RULES.md." de sempre; erro de plugin ganha dica de plugin (ou nenhuma,
    quando já traz a dele), com a frase fechada antes."""
    if not _PLUGIN_ERROR_RE.match(text):
        return text + " Confira docs/RULES.md."
    if text.startswith("Fonte "):
        return text
    closed = text if text.rstrip().endswith((".", "!", "?")) else text.rstrip() + "."
    return f"{closed} {PLUGIN_ERROR_HINT}"


def _classify_audited_error(event, exc, log, project=None):
    """Preenche o evento com a classificação da exceção capturada por `audited`.

    `log` é o diagnostics.jsonl que vai guardar o evento (o que a mensagem do erro interno
    cita); `project`, o projeto cuja gravação pendente conta para `recovery_pending`."""
    event["status"] = "error"
    project_log = diagnostics_path(project) if project else None
    pending = project_log.parent / ".pending-transaction.json" if project_log else None
    # Num `brolls/` sem permissão, o EACCES não pode escapar do caminho do erro.
    event["recovery_pending"] = bool(pending and _visible(pending))
    # Diagnostics survive regardless of classification, redacted like everything else here.
    event["type"] = type(exc).__name__
    event["repr"] = labels.for_log(scrub_home(redact_diagnostic(repr(exc), limit=None)), REDACT_LIMIT)
    event["traceback"] = redacted_traceback()
    if not isinstance(exc, (ValueError, OSError, OverflowError)):
        # Anything but a data/IO error (KeyError, IndexError, TypeError, AttributeError,
        # RuntimeError…) is a bug signature, not a user-fixable input problem; the
        # traceback is what a maintainer needs, not a RULES.md pointer.
        event["error_code"] = "INTERNAL_ERROR"
        event["message"] = (
            f"Erro interno inesperado (bug) [type: {type(exc).__name__}]. Reporte incluindo diagnostics.jsonl"
            + (f" ({labels.shown(log)})." if log else ".")
        )
        return
    # `MissingToolError` (yt-dlp/ffmpeg ausente) é ao mesmo tempo `ProviderError` e
    # `PrerequisiteError`: sai 4, com a mensagem e o aviso de fonte de sempre.
    event["error_code"] = error_code_for(exc)
    if isinstance(exc, Refusal):
        event["reason_code"] = exc.reason_code
    message = labels.message_of(exc, REDACT_LIMIT, project=project)  # redact, label, the typed file, then cut
    if (
        isinstance(exc, OSError)
        and exc.errno is not None
        and not isinstance(exc, ProviderError)
        and not getattr(exc, "own_message", False)
    ):
        # O erro do próprio sistema (EACCES, ENAMETOOLONG…): o texto cru traz `[Errno N]` e o 3.11 e o 3.14 o
        # levantavam de lugares diferentes. Sai sempre a mesma mensagem em português, com o arquivo pela marca.
        # `own_message` (o `UnreadableError` do leitor) já traz a mensagem pronta: fica como está.
        message = redact(labels.os_message(exc, project=project), limit=REDACT_LIMIT)
    if isinstance(exc, ProviderError):
        event["message"] = provider_error_message(message)
        current = ACTIVE.get()
        # `audited` (the only caller) sets ACTIVE before the command and resets it only after this.
        assert current is not None  # noqa: S101 - type narrowing only; see the line above
        current["warnings"].append({"code": "PROVIDER_ERROR", "message": message})
        event["warnings"] = current["warnings"]
    else:
        event["message"] = message


def _audited_error_failure(args, event, log, app_log):
    """Monta o `OperationError` da exceção já classificada por `_classify_audited_error`."""
    # Traceback e repr nunca vão para a pessoa, em nenhum comando: ficam só no
    # evento que `audited` grava em diagnostics.jsonl.
    payload = {k: v for k, v in event.items() if k not in ("traceback", "repr", "delivery_conflicts")}
    hint = _error_hint(args.command, event)
    if hint:
        payload["hint"] = hint
    payload["log"] = str(log) if log else None
    payload["app_log"] = app_log
    if args.command in QUIET_ERROR_COMMANDS and event["error_code"] != "INTERNAL_ERROR":
        # `plugins`/`x`/`assets`/`client` não gravam no projeto (ou, no `assets`, só leem):
        # erro de uso ali (flag faltando, plugin inexistente) é só a mensagem — a
        # dica de recovery/review era ruído. `export` grava só numa pasta nova em
        # exports/ e nunca no manifesto nem no journal (recusa journal pendente
        # antes de começar): a dica de recovery também não vale lá. `analysis` só grava
        # em analysis/, sob a trava própria, nunca no manifesto.
        payload.pop("hint", None)
    return OperationError(payload)


def _audited_interrupt_failure(event, log, app_log):
    """Monta o `OperationError` de uma interrupção (Ctrl-C) durante `audited`."""
    event["status"] = "interrupted"
    event["error_code"] = "INTERRUPTED"
    return OperationError(
        {
            **event,
            "message": "Operação interrompida. O próximo comando recuperará uma gravação pendente, se houver.",
            "log": str(log) if log else None,
            "app_log": app_log,
        }
    )


def _app_log(project):
    """`app_log` do erro: o `getbrolls.log` que `logs.configure` escolheu para o comando, se existir.

    A mesma escolha do log: o do projeto, ou o da instalação para quem muda a instalação
    (com `--project` ou não). Nunca levanta: arquivo que não dá para conferir é `None`.
    """
    try:
        path = logs.log_path(project)
    except Exception:  # noqa: BLE001 - the error envelope must come out even if this check fails
        return None
    return str(path) if path else None


def _failure_destination(project, fresh_init):
    """`(projeto, log)` de uma falha: o diagnostics.jsonl que de fato vai guardar o evento.

    O do projeto só quando a árvore `brolls/` já existe; senão (`init` numa pasta nova que
    falhou, comando somente leitura numa pasta sem `brolls/`, projeto que não existe), o de
    `$GB_HOME`, sem projeto: nenhuma árvore nasce só para guardar um erro, e a mensagem
    nunca cita um arquivo que ninguém gravou."""
    if project and not fresh_init:
        log = diagnostics_path(project)
        if log is not None and is_folder(log.parent):
            return project, log
    return None, diagnostics_path(None)


def _audit_log_wanted(event, log, read_only, in_project):
    """Se o evento vai para `log`.

    Um comando somente leitura nunca cria a árvore do projeto só para logar. Sem
    projeto, só um erro vai para `$GB_HOME/diagnostics.jsonl` (o sucesso não deixa
    rastro fora de um projeto)."""
    if not log:
        return False
    if in_project:
        return not read_only or is_folder(log.parent)
    return event.get("status") != "success"


def _write_audit_log(event, log, failure, result):
    """Grava a linha JSONL do evento; se a escrita falhar, anexa o aviso onde houver espaço."""
    try:
        # effect-exempt: audit log writes diagnostics.jsonl itself; an op line per audit line would log the logger
        log.parent.mkdir(parents=True, exist_ok=True)
        private_files._ensure_private_file(log)  # pylint: disable=protected-access
        # effect-exempt: audit log writes diagnostics.jsonl itself; an op line per audit line would log the logger
        with log.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(diagnostic_line(event) + "\n")
    except OSError:
        warning = {
            "code": "LOG_UNAVAILABLE",
            "message": "Não foi possível gravar diagnostics.jsonl. Confira espaço e permissões.",
        }
        if failure is not None:
            failure.payload.setdefault("warnings", []).append(warning)
        elif isinstance(result, dict):
            result.setdefault("warnings", []).append(warning)
        else:
            # `result` isn't a dict (e.g. None, or a non-mapping success value), so
            # the warning has nowhere to live in the response; it must not vanish.
            print(json.dumps(warning, ensure_ascii=False), file=sys.stderr)


def audited(args, execute):
    """Envolve a execução de um comando com trava, log de auditoria e trilha de erro/interrupção."""
    started = time.monotonic()
    event = {
        "at": now(),
        "operation": args.command,
        "status": "running",
        "state_committed": False,
        "recovered": False,
        "warnings": [],
    }
    token = ACTIVE.set(event)
    project = getattr(args, "project", None)
    read_only = (
        args.command in READ_ONLY_COMMANDS
        or (
            args.command,
            getattr(args, "action", None),
        )
        in READ_ONLY_ACTIONS
    )
    own_lock = (args.command, getattr(args, "action", None)) in OWN_LOCK_ACTIONS
    self_locked = args.command in SELF_LOCKED_COMMANDS
    log = diagnostics_path(project)
    # `init` numa pasta que ainda não existe: se falhar, a pasta some e o diagnóstico vai para
    # `$GB_HOME/diagnostics.jsonl` (o de quando não há projeto), não para uma pasta que a pessoa
    # nunca criou.
    fresh_init = args.command == "init" and bool(project) and not _visible(Path(project).expanduser())
    result = None
    failure = None
    try:
        if missing_project_tree(args.command, project):
            _refuse_missing_project(getattr(args, "project_typed", None) or project)
        with project_lock(None if read_only or own_lock or self_locked else project):
            if not getattr(args, "project_not_operated", False):
                _surface_layout_shape(args.command, project, getattr(args, "project_typed", None))
            result = execute(args)
            event["status"] = "success"
            # Comando que já devolve a própria lista de avisos (o `export`) marca o evento;
            # os avisos continuam no evento, e portanto no diagnostics.jsonl.
            if event["warnings"] and isinstance(result, dict) and not event.get("warnings_in_result"):
                result = {**result, "warnings": event["warnings"]}
            return result
    except Exception as exc:  # noqa: BLE001 - every failure leaves one classified event, never a "running" one
        app_log = None if fresh_init else _app_log(project)
        # Fora da árvore do projeto (destino em $GB_HOME) não há `brolls/`, nem gravação pendente.
        project, log = _failure_destination(project, fresh_init)
        _classify_audited_error(event, exc, log, project)
        failure = _audited_error_failure(args, event, log, app_log)
        raise failure from None
    except KeyboardInterrupt:
        app_log = None if fresh_init else _app_log(project)
        project, log = _failure_destination(project, fresh_init)
        failure = _audited_interrupt_failure(event, log, app_log)
        raise failure from None
    finally:
        event["duration_ms"] = round((time.monotonic() - started) * 1000)
        if _audit_log_wanted(event, log, read_only or self_locked, in_project=bool(project)):
            _write_audit_log(event, log, failure, result)
        ACTIVE.reset(token)


def write_diagnostics_log(project, event):
    """Append one diagnostics event to `diagnostics_path(project)`; best-effort.

    Used by cli.py's fallback handler for errors that happen outside audited() (e.g.
    before argument parsing finishes). Without a project, or in a folder with no `brolls/`
    yet (no tree is created just to keep an error), the event goes to
    `$GB_HOME/diagnostics.jsonl`. Returns the log path, or None when it could not write.
    """
    _project, log = _failure_destination(project, fresh_init=False)
    if log is None:
        return None
    event = {"at": now(), **event}
    try:
        # effect-exempt: audit log writes diagnostics.jsonl itself; an op line per audit line would log the logger
        log.parent.mkdir(parents=True, exist_ok=True)
        private_files._ensure_private_file(log)  # pylint: disable=protected-access
        # effect-exempt: audit log writes diagnostics.jsonl itself; an op line per audit line would log the logger
        with log.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(diagnostic_line(event) + "\n")
        return log
    except OSError:
        return None


class OperationError(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload.get("message", "Falha na operação."))
