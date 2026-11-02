"""Entry point of the command line: `main`, error rendering and exit codes."""

# pylint: disable=missing-function-docstring,broad-exception-caught
# Legado: ocorrências pré-existentes em `entrypoint` (corpo idêntico ao código anterior à 2.6.0).
# O contrato (parser, resumos e códigos de saída) mora em `cli_parser`, `cli_project_args` e
# `cli_summaries`; este módulo os reexporta, para quem lê a superfície em `getbrolls.cli`.

import contextlib
import contextvars
import errno
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import _paths, labels, logs
from .cli_parser import _check_preset_name, build_parser, parse_args  # noqa: F401 -- build_parser is reexported: tests keep reading it here; pylint: disable=unused-import
from .cli_project_args import FORMAT_GATE_SUBCOMMANDS  # noqa: F401 -- reexported: tests keep reading it here; pylint: disable=unused-import
from .cli_summaries import (  # noqa: F401 -- reexported: importers and tests keep reading these here; pylint: disable=unused-import
    EXIT_CODES,
    EXIT_INTERNAL_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_OPERATION_ERROR,
    EXIT_PREREQUISITE,
    EXIT_USAGE_ERROR,
    SUMMARIES,
)
from .errors import EnvReadError, PrerequisiteError, Refusal, UsageError
from .runtime import (
    READ_ONLY_ACTIONS,
    READ_ONLY_COMMANDS,
    SELF_LOCKED_COMMANDS,
    OperationError,
    audited,
    error_code_for,
    exit_code_for,
    is_folder,
    missing_project_tree,
    writes_installation,
)

# Só estes comandos saem 4 com o resultado em stdout (`"ready": false`); nos outros, 4
# é sempre um erro de pré-requisito em JSON no stderr.
PREREQUISITE_COMMANDS = ("doctor", "setup")
_RESULT_EXIT: contextvars.ContextVar[int] = contextvars.ContextVar("getbrolls_result_exit", default=EXIT_OK)


def _given_option_names(argv, args):
    """Names of the options passed, never their values (values can be URLs or free text).

    A token only counts when the parsed namespace has that option: a free-text value
    that happens to start with `--` must not reach the log as if it were a flag name.
    """
    names = []
    for token in argv:
        if not token.startswith("--"):
            continue
        name = token[2:].split("=", 1)[0]
        if hasattr(args, name.replace("-", "_")) and name not in names:
            names.append(name)
    return ",".join(names) if names else None


def _log_early_refusal(args, error_code, reason):
    """Registra `command_refused` quando o `.env` ou o perfil param o comando antes do log.

    Esse erro sai antes do log, da trava e da auditoria: nenhum arquivo nasce, nem o log do app.
    Só o espelho do stderr (`GB_LOG_STDERR=1`) mostra a linha, antes do JSON de erro. `reason` é
    o motivo do perfil (`untrusted`, `changed`, `invalid` ou `requires`, ou o de um arquivo do
    `GB_HOME` do perfil, como `link`) ou o do `.env` (`env_missing`, `env_process_only_key`,
    `env_gb_home`, `env_unreadable`, `env_encoding`); um erro de uso sem motivo próprio sai como
    `usage`, e um erro de E/S que não é o `.env` (pasta sem permissão no caminho), como `io`.
    """
    logs.configure(None, read_only=True)
    logs.event(
        logs.get("cli"),
        logging.WARNING,
        "command_refused",
        command=args.command,
        error_code=error_code,
        reason=reason,
    )
    logs.shutdown()


def _load_env_early(args):
    """Ativa o perfil e carrega o `.env` antes de configurar o log (GB_LOG_LEVEL pode morar nele).

    Erro de uso (`UsageError`: arquivo que falta, `GB_ENV_FILE` ou `GB_HOME` onde não
    podem, `getbrolls.toml` inválido ou não confiável) vira `OperationError` aqui mesmo,
    sem tocar no projeto; `requires` do perfil que não bate vira `PREREQUISITE_MISSING`, e um
    `.env` que não abre (`EnvReadError`) ou outra falha de E/S (`OSError`: pasta sem
    permissão no caminho do perfil ou do `$GB_HOME`), `IO_ERROR`.
    Outro erro do `.env` (chave desconhecida) fica para `execute()`, que o levanta dentro
    da auditoria; o carregamento repetido lá é inofensivo (`setdefault`, e a ativação do
    perfil é reaproveitada).
    """
    from .env_loader import load_environment

    try:
        load_environment(args, warn=False)
    except UsageError as exc:
        _log_early_refusal(args, "USAGE_ERROR", exc.reason or "usage")
        raise OperationError(
            {"operation": args.command, "status": "error", "error_code": "USAGE_ERROR", "message": str(exc)}
        ) from None
    except PrerequisiteError as exc:
        _log_early_refusal(args, "PREREQUISITE_MISSING", "requires")
        raise OperationError(
            {"operation": args.command, "status": "error", "error_code": "PREREQUISITE_MISSING", "message": str(exc)}
        ) from None
    except ValueError:
        pass  # `execute()` levanta de novo, dentro da auditoria
    except OSError as exc:
        # Erro de E/S da pessoa (permissão), não bug: sai como os outros erros do `.env`, antes
        # do log e sem tocar no projeto. `env_unreadable` só quando é o próprio `.env`.
        _log_early_refusal(args, "IO_ERROR", "env_unreadable" if isinstance(exc, EnvReadError) else "io")
        raise OperationError(
            {"operation": args.command, "status": "error", "error_code": "IO_ERROR", "message": io_error_message(exc)}
        ) from None


def io_error_message(exc):
    """Mensagem em português de um `OSError` de antes do comando: o `.env` que não abre, ou o
    caminho (arquivo ou pasta) que não deu para acessar."""
    detail = exc.strerror or str(exc)
    if isinstance(exc, EnvReadError):
        shown = getattr(exc, "shown", None) or labels.shown(exc.filename)
        return f"Não consegui ler {shown} ({detail}). Confira as permissões do arquivo e repita."
    return labels.os_message(exc)


def _expand_project(args):
    """`--project '~/…'` com o `~` cru (entre aspas, `--project=~/…`, PowerShell) vale a pasta pessoal.

    Uma vez só, logo depois do parse: trava, manifesto, regras e log leem o mesmo caminho.
    Sem isso, `status` e `brief` expandiam por conta própria e o resto gravava em `./~/…`."""
    project = getattr(args, "project", None)
    labels.forget_typed_project()
    if project:
        # As digitado (`~/x` inclusive): as recusas devolvem isto, nunca a pasta pessoal expandida.
        args.project_typed = project
        try:
            expanded = str(Path(project).expanduser())
            if _unknown_tilde_user(project):
                raise RuntimeError(project)
            args.project = expanded
            labels.note_typed_project(project, expanded)
            _refuse_link_loop(project, expanded)
        except RuntimeError:
            # `~outro_usuario/…` de uma conta que não existe aqui (ou sem pasta pessoal): é o
            # caminho digitado que não serve, não um bug, e nenhum comando deve rodar com ele.
            raise UsageError(
                f"--project {project}: não consegui expandir o `~` (usuário desconhecido ou sem pasta "
                "pessoal). Use o caminho completo da pasta do projeto."
            ) from None


def _is_link_loop(exc):
    """Windows has no `ELOOP`: a loop is `ERROR_CANT_RESOLVE_FILENAME`, with `errno` 22."""
    return exc.errno == errno.ELOOP or getattr(exc, "winerror", None) == _paths.WINERROR_CANT_RESOLVE_FILENAME


def _refuse_link_loop(typed, path):
    """`--project` cujo caminho passa por um laço de links: erro de uso, igual no 3.11 (`RuntimeError` do
    `resolve`) e no 3.14 (`ELOOP`). Qualquer outro erro do `stat` fica para o comando.

    O Windows só diz "laço" quando o laço é o último nome; com o laço num componente do meio o
    erro do caminho inteiro é outro (caminho não encontrado). Aí os pais são perguntados do mais
    próximo ao mais longe, até o primeiro que responde sem erro: um laço em qualquer um deles
    é o laço do caminho."""
    path = Path(path)
    try:
        path.stat()
    except OSError as exc:
        if _is_link_loop(exc) or _parent_loops(path):
            raise UsageError(
                f"--project {typed}: o caminho tem um laço de links simbólicos ou um link que não resolve. "
                "Aponte para a pasta de verdade do projeto."
            ) from None


def _parent_loops(path):
    """Algum pai de `path`, antes do primeiro que responde ao `stat` sem erro, é um laço de links?"""
    for parent in path.parents:
        try:
            parent.stat()
        # Any other error on a parent (EACCES, ENOENT, ENOTDIR) is walked past: the command refuses the project.
        except OSError as exc:
            if _is_link_loop(exc):
                return True
        else:
            return False
    return False


def _unknown_tilde_user(project):
    """`~nome/…` cuja pasta pessoal não existe nesta máquina?

    No POSIX um usuário desconhecido já faz o `expanduser` levantar; no Windows o `ntpath`
    adivinha `C:\\Users\\nome` sem conferir, e o comando rodaria numa pasta que não é de ninguém.
    `~` sozinho e `~nome` com pasta pessoal de verdade seguem expandidos como sempre."""
    if not project.startswith("~"):
        return False
    separators = "".join(sep for sep in (os.sep, os.altsep) if sep)
    head = project[1:]
    for separator in separators:
        head = head.split(separator, 1)[0]
    if not head:
        return False
    profile = Path("~" + head).expanduser()  # RuntimeError (POSIX, unknown user): the caller refuses it
    # lenient: a profile folder that cannot be inspected counts as missing (the refusal names the path)
    return not profile.is_dir()


def _expanded(path):
    """`path` com o `~` do começo expandido; sem pasta pessoal para resolver, como veio."""
    try:
        return str(Path(path).expanduser())
    except RuntimeError:
        return path


def _serve_writes(args):
    """O `serve` vai gravar no projeto? Então as linhas `event=op` dele vão para o getbrolls.log.

    `serve` não toma a trava nem cria a árvore (é somente leitura para a trava), mas o
    servidor grava `.serve.pid`, `.serve.log` e as decisões em `reviews/`, e o `--stop`
    mata um processo: o log do projeto registra isso. Só com a árvore pronta (o Storyboard
    já gerado; para o `--stop`, um `.serve.pid`), para nunca criar `brolls/` nem um log num
    `--stop` que não tem o que parar. Nunca levanta.
    """
    project = getattr(args, "project", None)
    if args.command != "serve" or not project:
        return False
    name = ".serve.pid" if getattr(args, "stop", False) else "review.html"
    try:
        return _paths.strict_is_file(Path(project).expanduser() / "brolls" / name)
    except (OSError, RuntimeError, ValueError):
        return False


def _serve_error_payload(exc):
    """O JSON de erro do `serve` em primeiro plano: `error`, `error_code` e, numa recusa com motivo, `reason_code`."""
    # `OSError` (o `.env` que não abre, a porta que o sistema nega) é IO_ERROR, saída 1.
    message = io_error_message(exc) if isinstance(exc, OSError) else str(exc)
    payload = {"error": message, "error_code": error_code_for(exc)}
    if isinstance(exc, Refusal):
        payload["reason_code"] = exc.reason_code
    return payload


def main(argv=None):
    """Faz o parse, configura logging/trava e roda o comando com auditoria e log de início/fim."""
    from .commands import execute, with_summary

    args = parse_args(argv)
    _expand_project(args)
    logs.set_command(args.command)
    if args.command == "serve" and not (args.background or args.stop):
        # `serve` blocks in serve_forever() and owns its own stdout contract (one JSON
        # line with the URLs, printed by serve.run() itself, then nothing else): it does
        # not go through the JSON-wrapping in entrypoint(), so it exits directly here.
        try:
            from . import serve  # local: only this command needs the server

            serve.filter_output()  # the background child: its own log gets redacted lines only
            # O `.env` e o perfil antes do log: `GB_LOG_LEVEL=off` pode morar neles. Um erro aqui
            # sai pelo mesmo JSON de erro de `execute()`, logo abaixo.
            from .env_loader import load_environment

            load_environment(args, warn=False)
            if _serve_writes(args):
                logs.configure(args.project, read_only=False)
            raise SystemExit(execute(args))
        except (ValueError, OSError) as exc:
            # `OSError` (o `.env` que não abre, a porta que o sistema nega) é IO_ERROR, saída 1.
            payload = _serve_error_payload(exc)
            print(json.dumps(payload, ensure_ascii=False))
            raise SystemExit(exit_code_for(payload["error_code"])) from None
        finally:
            logs.shutdown()

    # `.env` que falta ou que tenta definir o que não pode é erro de uso: sai antes do
    # log, da trava e da auditoria, sem criar nada em `brolls/`. (`serve` em primeiro
    # plano, acima, tem contrato próprio de saída e recebe o erro de `execute()`.)
    _load_env_early(args)
    project = getattr(args, "project", None)
    read_only = args.command in READ_ONLY_COMMANDS or (args.command, getattr(args, "action", None)) in READ_ONLY_ACTIONS
    _check_preset_name(args)
    # `init`/`migrate` validam antes de criar: o log do app não pode criar `brolls/` antes disso.
    # Comando de etapa numa pasta que não existe é recusado: o log também não cria a árvore.
    tree_pending = missing_project_tree(args.command, project) or (
        args.command in SELF_LOCKED_COMMANDS and not (project and is_folder(Path(project, "brolls")))
    )
    # Sem projeto, quem muda a instalação grava `$GB_HOME/logs/getbrolls.log`; quem só lê não cria arquivo.
    logs.configure(
        project,
        read_only=(read_only or tree_pending) and not _serve_writes(args),
        installation=writes_installation(args),
    )

    try:
        log = logs.get("cli")
        logs.event(
            log,
            logging.INFO,
            "command_start",
            command=args.command,
            read_only=read_only,
            options=_given_option_names(sys.argv[1:] if argv is None else argv, args),
        )
        started = time.monotonic()
        try:
            result = audited(args, lambda parsed: with_summary(parsed.command, execute(parsed)))
        except OperationError as exc:
            # `reason` só quando a recusa trouxe um `reason_code`: o log sem ele fica como era.
            reason = {"reason": exc.payload["reason_code"]} if "reason_code" in exc.payload else {}
            logs.event(
                log,
                logging.INFO,
                "command_end",
                command=args.command,
                status="error",
                error_code=exc.payload.get("error_code"),
                **reason,
                ms=round((time.monotonic() - started) * 1000),
                outcome="error",
            )
            raise
        logs.event(
            log,
            logging.INFO,
            "command_end",
            command=args.command,
            status="ok",
            error_code=None,
            ms=round((time.monotonic() - started) * 1000),
            outcome="ok",
        )
        _RESULT_EXIT.set(result_exit(args.command, result, getattr(args, "action", None)))
        return result
    finally:
        logs.shutdown()


def result_exit(command, result, action=None):
    """Código de saída de um comando que deu certo: 4 para `doctor`/`setup` com
    `"ready": false`; 1 para `analysis --action check` com `"ok": false` e para
    `setup --upgrade` cuja atualização falhou com a venv refeita na versão fixada
    (`upgrade.status: "failed"`, `ready: true`) — o resultado sai em stdout do mesmo jeito,
    para portões por código de saída; 0 para o resto."""
    if command in PREREQUISITE_COMMANDS and isinstance(result, dict) and result.get("ready") is False:
        return EXIT_PREREQUISITE
    if (command, action) == ("analysis", "check") and isinstance(result, dict) and result.get("ok") is False:
        return EXIT_OPERATION_ERROR
    if command == "setup" and isinstance(result, dict) and (result.get("upgrade") or {}).get("status") == "failed":
        return EXIT_OPERATION_ERROR
    return EXIT_OK


def _print_error(payload):
    """Uma linha JSON de erro em stderr, sem traceback nem repr (esses ficam no diagnostics)."""
    clean = {k: v for k, v in payload.items() if k not in ("traceback", "repr")}
    print(json.dumps(clean, ensure_ascii=False), file=sys.stderr)


def _closed_pipe(exc):
    """O leitor do pipe fechou antes do fim da escrita em stdout (`| head`)?

    No POSIX isso é `BrokenPipeError` (EPIPE). No Windows a escrita num pipe sem leitor
    falha com ERROR_NO_DATA, que o CRT entrega como `OSError` EINVAL — só vale para a
    escrita do resultado, nunca para um EINVAL vindo do próprio comando.
    """
    return isinstance(exc, BrokenPipeError) or exc.errno in (errno.EPIPE, errno.EINVAL)


def _silence_stdout():
    """Depois de um `BrokenPipeError`, aponta stdout para o devnull: o flush da saída do
    interpretador não levanta de novo (nem imprime "Exception ignored ...")."""
    try:
        target = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return  # stdout sem descritor (redirecionado em memória): nada a flushar no fim
    if not isinstance(target, int):
        return
    # effect-exempt: opens os.devnull to silence a broken stdout pipe; nothing is written to any file
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, target)
    except OSError:
        pass
    finally:
        os.close(devnull)


def entrypoint():
    for stream in (sys.stdout, sys.stderr):
        # TextIO não declara `reconfigure`; quem não tiver cai no except.
        with contextlib.suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    _RESULT_EXIT.set(EXIT_OK)
    try:
        result = main()
        try:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            # Flush aqui, dentro do `try`: com `| head` o erro de pipe chega agora, e não no
            # encerramento do interpretador, onde viraria traceback em stderr.
            sys.stdout.flush()
        except OSError as exc:
            if not _closed_pipe(exc):
                raise
            _silence_stdout()
        return _RESULT_EXIT.get()
    except OperationError as exc:
        _print_error({"error": str(exc), **exc.payload})
        return exit_code_for(exc.payload.get("error_code"))
    except (UsageError, PrerequisiteError) as exc:
        # Levantado fora de `audited` (antes do parse terminar, por exemplo).
        code = error_code_for(exc)
        _print_error({"error": str(exc), "error_code": code})
        return exit_code_for(code)
    except KeyboardInterrupt:
        _print_error({"error": "Operação interrompida.", "error_code": "INTERRUPTED"})
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        # The consumer end of a pipe (e.g. `| head`) closed early; this is an ordinary,
        # expected shutdown, not a bug — do not report it as INTERNAL_ERROR. The command
        # already ran: its exit (4 for `doctor` with `ready: false`) still stands.
        _silence_stdout()
        return _RESULT_EXIT.get()
    except Exception as exc:  # noqa: BLE001 - last-resort CLI boundary, must exit as JSON not a raw traceback
        return _internal_error(exc)


def _internal_error(exc):
    """Último recurso: grava o traceback no diagnostics e mostra só o tipo do erro."""
    # Anything audited() didn't already turn into an OperationError (e.g. an argparse-time
    # bug) must still exit as JSON, not a raw traceback breaking the CLI's output contract.
    from .runtime import REDACT_LIMIT, redact_diagnostic, redacted_traceback, write_diagnostics_log

    project = _project_from_argv()
    event = {
        "operation": None,
        "status": "error",
        "error_code": "INTERNAL_ERROR",
        "type": type(exc).__name__,
        "repr": labels.for_log(redact_diagnostic(repr(exc), limit=None), REDACT_LIMIT),
        "traceback": redacted_traceback(),
    }
    log = write_diagnostics_log(project, event)
    app_log = logs.log_path(project)  # nunca levanta: arquivo que não dá para conferir é `None`
    message = f"Erro interno inesperado (bug) [type: {type(exc).__name__}]."
    if log:
        message += f" Detalhes em {labels.shown(log)} (diagnostics.jsonl)."
    else:
        message += " Não foi possível gravar diagnostics.jsonl."
    _print_error(
        {
            "error": message,
            "error_code": "INTERNAL_ERROR",
            "type": type(exc).__name__,
            "log": str(log) if log else None,
            "app_log": str(app_log) if app_log else None,
        }
    )
    return EXIT_INTERNAL_ERROR


def _project_from_argv():
    """Best-effort --project value from sys.argv, for diagnostics logging before/around parse_args.

    Expanded like `_expand_project` does after the parse, so both name the same folder."""
    argv = sys.argv[1:]
    value = None
    if "--project" in argv:
        index = argv.index("--project")
        if index + 1 < len(argv):
            value = argv[index + 1]
    if value is None:
        value = next((item.split("=", 1)[1] for item in argv if item.startswith("--project=")), None)
    return _expanded(value) if value else None
