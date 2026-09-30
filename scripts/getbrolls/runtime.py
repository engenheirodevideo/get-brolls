"""Operational diagnostics. Never log command arguments or tokens; tracebacks are stored redacted."""

# pylint: disable=import-error,missing-function-docstring,broad-exception-caught,use-sequence-for-iteration,missing-class-docstring,cyclic-import
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0). `import-error`
# é o `fcntl`/`msvcrt` condicional por plataforma em `_acquire_lock`/`_release_lock`.
# Os ciclos (getbrolls.runtime <-> getbrolls.logs, getbrolls.runtime <-> getbrolls.http)
# já existiam antes da 2.6.0: os imports de `logs`/`http` aqui são tardios (dentro de
# função) de propósito, exatamente para quebrar esses ciclos em tempo de execução
# (ver os comentários "avoids a runtime<->logs/http import cycle" abaixo).

import contextlib
import contextvars
import errno
import json
import logging
import os
import re
import shutil
import stat
import sys
import time
import traceback
from pathlib import Path

from . import _paths
from .errors import LockedError, PrerequisiteError, UsageError
from .models import now

ACTIVE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("getbrolls_operation", default=None)


def _acquire_lock(stream, platform=None, windows=None):
    """Acquire a non-blocking, one-byte lock on Windows or a flock elsewhere."""
    platform = platform or os.name
    if platform == "nt":
        if windows is None:
            import msvcrt as windows

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write("\0")
            stream.flush()
        stream.seek(0)
        try:
            windows.locking(stream.fileno(), windows.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]
        except OSError as exc:
            raise BlockingIOError from exc
        return
    import fcntl

    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_lock(stream, platform=None, windows=None):
    platform = platform or os.name
    if platform == "nt":
        if windows is None:
            import msvcrt as windows

        stream.seek(0)
        windows.locking(stream.fileno(), windows.LK_UNLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]
        return
    import fcntl

    fcntl.flock(stream, fcntl.LOCK_UN)


def _ensure_private_file(path):
    """Create `path` (empty) if missing, then force 0600 regardless of umask.

    `touch()`'s own `mode=` is still masked by umask, so an explicit `chmod` is the
    only way to guarantee 0600 both on first creation and on a file left over from
    before this fix (a plain `diagnostics.jsonl` created 0644 by an older run).
    """
    path.touch(exist_ok=True)
    path.chmod(0o600)


def record_warning(code, message):
    current = ACTIVE.get()
    if current is not None:
        current["warnings"].append({"code": code, "message": message})
    try:
        from . import logs  # local: avoids a runtime<->logs import cycle

        # Only the code: warning messages are prose written for the person and may
        # quote what they typed (an approver's name, a reason). The full message is
        # already in the command's JSON output; the log only needs to correlate.
        logs.event(logs.get("runtime"), logging.WARNING, "warning", code=code)
    except Exception:  # noqa: BLE001, S110 - logging must never break a command
        pass


def record_commit():
    current = ACTIVE.get()
    if current is not None:
        current["state_committed"] = True


SENSITIVE_HEADERS = ("Authorization", "Cookie", "Set-Cookie", "X-Api-Key")
# Accepts both `Name: value` and a quoted/JSON-rendered form (`"Name": "value"`,
# `{'Name': 'value'}`): an optional quote on each side of the separator, and `=` as
# well as `:`. The value stops at a quote or newline so the surrounding braces/quotes
# of a dict repr survive. No nested quantifiers — linear on adversarial input.
_HEADER_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(h) for h in SENSITIVE_HEADERS) + r")\s*[\"']?\s*[:=]\s*[\"']?[^\r\n\"']+"
)
# A bearer token with no header name in front of it (e.g. copied into an error
# message or a shell command). 8+ chars of the base64url/JWT-safe alphabet.
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
# Any identifier ENDING in one of these keywords (so `access_token`, `api_key`,
# `apikey`, `client_secret`, `X-Amz-Signature` and `X-Amz-Credential` all match, not
# just the bare word), plus a short list of known credential-shaped query names that
# don't end in a keyword (`Key-Pair-Id`), followed by `=` and a value. A full URL is
# already wiped out whole by the `https?://` pass below, before this pattern would
# see it. No nested quantifiers — linear on adversarial input.
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    # The name prefix is BOUNDED: an unbounded `[...]*` here rescans the rest of the
    # text from every position of a long run of `-`/`_`/`.`, which is quadratic.
    # The secret word must be a whole segment of the name (`api_key`, `X-Amz-Signature`),
    # or one of the glued spellings: `monkey=` and `turkey=` are not secrets.
    r"((?:[A-Za-z0-9_.-]{0,40}[_.-])?"
    r"(?:api_?key|access_?token|key|token|secret|signature|sig|policy|credential|password)|Key-Pair-Id)"
    r"\s*=\s*[\"']?[^&\s\"'<>]+"
)


# Nome de parâmetro/chave com cara de segredo, casado com o nome INTEIRO
# (`fullmatch`): a palavra secreta fecha o nome — `auth_token`, `client_secret`,
# `X-Amz-Signature` casam; `oauth2_token_id`, `monkey`, `profile_id` não. Os nomes
# soltos do fim cobrem assinaturas de CDN (Akamai `__token__`/`hdnts`/`hdnea`,
# CloudFront `Key-Pair-Id`) e os prefixos de assinatura S3/GCS.
SECRET_NAME_RE = re.compile(
    r"(?i)(?:[a-z0-9_.-]{0,40}[_.-])?"
    r"(?:api_?key|apikey|access_?token|key|token|secret|signature|sig|password|passwd|pwd|hmac|jwt"
    r"|credentials?|policy|authorization)"
    r"|__token__|hdnts|hdnea|key-pair-id|x-amz-[a-z0-9-]*|x-goog-[a-z0-9-]*"
)


def secret_name(name):
    """`True` quando `name` (chave de JSON ou de query string) parece guardar um segredo."""
    return isinstance(name, str) and SECRET_NAME_RE.fullmatch(name) is not None


# Quebra de linha ou caractere de controle: o que deixa um valor "fugir" da linha
# dele num Markdown gerado (ORIGEM.md, credits.md) e forjar outra linha.
_LINE_BREAKING = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")


def one_line(value):
    """`value` como texto de uma linha só: controle/quebra de linha viram espaço.

    Texto normal (sem controle) volta idêntico — a saída dos built-ins não muda."""
    return _LINE_BREAKING.sub(" ", str(value))


def _writable(target):
    """Liga a escrita do dono em `target` (e leitura/entrada, se for pasta); link não é tocado."""
    with contextlib.suppress(OSError):
        mode = target.lstat().st_mode
        if stat.S_ISLNK(mode):
            return
        extra = stat.S_IWUSR | (stat.S_IRUSR | stat.S_IXUSR if stat.S_ISDIR(mode) else 0)
        target.chmod(stat.S_IMODE(mode) | extra)


_REMOVE_FUNCS = (os.unlink, os.remove, os.rmdir)


def _removable(target):
    """Libera leitura+execução+escrita do dono numa pasta (0o700) ou escrita numa
    entrada comum (0o600); link não é tocado. Só precisa ser permissivo o
    suficiente para apagar — chamada apenas na retentativa do force_rmtree."""
    with contextlib.suppress(OSError):
        mode = target.lstat().st_mode
        if stat.S_ISLNK(mode):
            return
        target.chmod(0o700 if stat.S_ISDIR(mode) else 0o600)


def force_rmtree(path):
    """Apaga `path` inteiro mesmo com entrada somente-leitura; pasta ausente não é erro.

    O Git para Windows grava objetos (`.git/objects/**`) como somente-leitura, e o
    Windows não apaga arquivo somente-leitura — `rmtree(..., ignore_errors=True)`
    deixava um `.git` dentro do plugin instalado e `.install-*` acumulando. Na
    falha, libera a escrita da entrada (e da pasta de cima, se ela ainda é parte da
    árvore — nunca a pasta que CONTÉM `path`) e tenta de novo.

    Numa pasta sem leitura/execução (uma pasta comum plantada pelo dono do plugin,
    não um objeto git), o walk por fd do `rmtree` chama a retentativa com
    `func=os.open`/`os.scandir`/`os.lstat` (não `os.unlink`/`os.rmdir`/`os.remove`)
    — chamar `func(failed)` sem os argumentos certos levantaria `TypeError`. Nesse
    caso, depois de liberar a permissão, a subárvore é apagada por conta própria
    (`shutil.rmtree` recursivo) em vez de repetir a chamada original.

    Nunca levanta (guarda `Exception`, não só `OSError`): o que sobrar fica para a
    varredura seguinte (`install._sweep_stale_staging`)."""
    root = Path(path)
    if not os.path.lexists(root):
        return

    def retry(func, failed, _exc):
        failed_path = Path(failed)
        if failed_path != root:
            _writable(failed_path.parent)
        _removable(failed_path)
        if func in _REMOVE_FUNCS:
            with contextlib.suppress(OSError):
                func(failed)
        else:
            shutil.rmtree(failed_path, ignore_errors=True)

    with contextlib.suppress(Exception):
        if sys.version_info >= (3, 12):
            # O pylint infere a assinatura de `shutil.rmtree` pela stdlib do
            # interpretador que roda o lint, não pelo `sys.version_info` deste
            # ramo: `onexc` existe a partir do 3.12, mas o CI faz lint em
            # 3.11, que não conhece o kwarg. `**kwargs` monta fora da chamada
            # engana o pylint mas confunde o pyright (perde a distinção entre
            # os overloads de `onexc`/`onerror`), então a chamada fica direta
            # com a supressão na linha.
            shutil.rmtree(root, onexc=retry)  # pylint: disable=unexpected-keyword-arg
        else:  # pragma: no cover - Python 3.11
            shutil.rmtree(root, onerror=retry)  # pylint: disable=deprecated-argument


def scrub_home(text):
    """Replace the user's home directory prefix with `~`. Never raises.

    Only for text nobody acts on: the diagnostics file and the `repr`/`traceback`
    fields, which people paste into bug reports. It is deliberately NOT part of
    `redact()`: error messages name paths the person (or the agent driving the CLI)
    must open or pass back as an argument, and `~` inside quotes is not expanded by a
    shell nor understood by a file reader. Both the raw and the JSON-escaped form of
    the prefix are replaced, so it also works on an already serialized line on Windows.
    """
    value = str(text)
    try:
        home = str(Path.home())
    except RuntimeError:
        # No HOME/USERPROFILE to resolve: nothing to scrub.
        return value
    if home in ("", "/", "\\"):
        return value
    escaped = json.dumps(home)[1:-1]
    for prefix in {home, escaped}:
        value = value.replace(prefix, "~")
    return value


def redact(text):
    """Strip provider keys, secret-shaped headers/query values and URLs.

    Idempotent (running it twice yields the same string) and never raises — every
    step is a plain string replace/regex substitution over `str(text)`. Safe for
    user-facing messages: paths are left intact (see `scrub_home` for why).
    """
    value = str(text)
    for key in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "YOUTUBE_API_KEY"):
        secret = os.getenv(key)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = _HEADER_PATTERN.sub(lambda m: f"{m.group(1)}: [REDACTED]", value)
    value = re.sub(r'https?://[^\s"<>]+', "[URL omitida]", value)
    value = _QUERY_SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=[REDACTED]", value)
    value = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    return value[:1200]


STDERR_TAIL_MAX_CHARS = 600


def stderr_tail(stderr, limit=6):
    """Last `limit` non-empty stderr/detail lines, redacted, joined and capped.

    Shared truncation helper for CLI stderr/detail text (subprocess stderr, HTTP error
    bodies), so callers cannot drift apart on secrets, URLs or how much is surfaced.
    """
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    joined = " | ".join(redact(line) for line in lines[-limit:])
    return joined[:STDERR_TAIL_MAX_CHARS]


_LOCK_POLL_S = 0.05


_LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)


def _open_lock_file(path):
    refusal = ValueError(f"{path.name} em {scrub_home(str(path.parent))} é um link; apague o link e repita o comando.")
    if not getattr(os, "O_NOFOLLOW", 0) and path.is_symlink():
        raise refusal
    try:
        fd = os.open(path, _LOCK_FLAGS, 0o644)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise refusal from None
        raise
    return os.fdopen(fd, "a+", encoding="utf-8")


@contextlib.contextmanager
def exclusive_lock(path, busy_message, wait_s=0.0):
    """Trava exclusiva no arquivo `path` (criado se faltar); a pasta tem que existir.

    O arquivo é aberto sem seguir link (`O_NOFOLLOW`; onde não existe, um `lstat` antes):
    uma trava trocada por link nunca cria nem trava um arquivo fora do lugar.
    Tenta de novo por até `wait_s` segundos; depois, `LockedError(busy_message)` (código `LOCKED`).
    """
    with _open_lock_file(Path(path)) as lock:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                _acquire_lock(lock)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockedError(busy_message) from None
                time.sleep(_LOCK_POLL_S)
        try:
            yield
        finally:
            _release_lock(lock)


COMMAND_LOCK = ".command.lock"


@contextlib.contextmanager
def project_lock(project):
    if not project:
        yield
        return
    root = Path(project).resolve() / "brolls"
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(
        root / COMMAND_LOCK, "Outro comando está usando este projeto. Aguarde terminar antes de repetir."
    ):
        yield


# Comandos que só leem o projeto: sem trava exclusiva e sem criar a árvore.
# `brief` entra aqui porque só lê BRIEF.md, RULES.md e o manifesto já existente.
# `doctor` aceita `--project` por uniformidade com o resto da CLI, mas diagnostica a
# instalação: não pode criar `brolls/` numa pasta que talvez nem seja um projeto.
# `capabilities` só descreve o parser e o manifesto dos plugins, sem projeto.
# `setup` nunca recebe nem toca projeto; instala só em `$GB_HOME/runtime` ou `GB_RUNTIME_DIR`.
# `x` roda comando de plugin, que só lê o projeto por cópias (CommandContext).
# `profile` só grava `$GB_HOME/trusted-profiles.json` (trust/untrust), nunca um projeto.
READ_ONLY_COMMANDS = ("status", "serve", "brief", "doctor", "setup", "x", "capabilities", "profile")
# (comando, ação) somente leitura, além dos comandos inteiros acima: `queue --action status`
# só consulta queue.json (mesmo contrato de `status`), nunca deve tomar a trava exclusiva.
# `roteiro --action check|plan` e `assets` também só leem: plano de cena, sync simulado e
# inventário de componentes, sem trava nem árvore nova. `client --action list|show` só lê
# `$GB_HOME/clients.json` e o `client.json` de cada pasta; `template --action list|show` só lê
# as pastas de template dos clientes registrados. `migrate --action plan` só mostra o
# `project.json` que o `apply` gravaria. `analysis --action list|check` só lê `analysis/`,
# sem criar a pasta.
READ_ONLY_ACTIONS = {
    ("analysis", "check"),
    ("analysis", "list"),
    ("client", "list"),
    ("client", "show"),
    ("migrate", "plan"),
    ("template", "list"),
    ("template", "show"),
    ("queue", "status"),
    ("roteiro", "check"),
    ("roteiro", "plan"),
    ("assets", "list"),
    ("assets", "where"),
}

# (comando, ação) que grava só sob a própria trava, nunca sob a do projeto: `analysis
# --action register` calcula o sha256 de uma mídia grande sem segurar o projeto, e só a
# troca dos arquivos de `analysis/` fica dentro de `analysis/.lock`.
OWN_LOCK_ACTIONS = {("analysis", "register")}

# Comandos que criam o projeto (ou o `project.json`) e só tomam a trava dele depois de
# validar tudo: um `init` recusado (cliente desconhecido, template adulterado, flag errada)
# ou um `migrate` numa pasta que não existe não pode deixar para trás a pasta do projeto,
# `brolls/.command.lock` nem um log. O log de auditoria só é
# gravado quando `brolls/` já existe (ou seja, depois que o próprio comando a criou).
SELF_LOCKED_COMMANDS = ("init", "migrate")


# Erro que veio de um plugin: "Plugin <id>: …" (todo erro do core sobre código de
# plugin usa esse prefixo) ou "Fonte X é do plugin Y, …" (fonte de plugin fora do
# ar, que já traz a própria dica). RULES.md não tem nada a ver com nenhum dos dois.
_PLUGIN_ERROR_RE = re.compile(r"Plugin [A-Za-z0-9_-]+: |Fonte \S+ é do plugin ")
PLUGIN_ERROR_HINT = "Veja plugins --action list / doctor e docs/SDK.md."


# Comandos cujo erro sai sem a dica de recovery (nenhum erro mostra traceback nem
# repr à pessoa; esses ficam só em diagnostics.jsonl).
QUIET_ERROR_COMMANDS = ("plugins", "x", "export", "assets", "client", "analysis", "template")

# `error_code` → código de saída da CLI; qualquer outro código (INVALID_DATA,
# IO_ERROR, ...) é erro de operação ou de dados: 1. A tabela completa, com o 0, fica
# em `cli.EXIT_CODES`; este é o único lugar que decide a saída de um erro. `LOCKED`
# (outro processo segura a trava do projeto ou do runtime) sai 1, como erro de operação,
# mas com código próprio para quem automatiza saber que basta repetir depois.
ERROR_EXIT = {"USAGE_ERROR": 2, "INTERNAL_ERROR": 3, "PREREQUISITE_MISSING": 4, "LOCKED": 1, "INTERRUPTED": 130}
EXIT_FOR_OTHER_ERRORS = 1


def exit_code_for(error_code):
    """Código de saída de um `error_code` do JSON de erro."""
    return ERROR_EXIT.get(error_code, EXIT_FOR_OTHER_ERRORS)


def error_code_for(exc):
    """`error_code` de uma exceção que a pessoa pode corrigir (não um bug).

    `ValueError` genérico depois do parse continua erro de dados (1), mesmo quando é,
    na prática, uso errado: só `UsageError` explícito vira `USAGE_ERROR`.
    """
    if isinstance(exc, UsageError):
        return "USAGE_ERROR"
    if isinstance(exc, PrerequisiteError):
        return "PREREQUISITE_MISSING"
    if isinstance(exc, LockedError):
        return "LOCKED"
    return "IO_ERROR" if isinstance(exc, OSError) else "INVALID_DATA"


_RECOVERY_HINT = "Há uma gravação pendente (recovery_pending=true): o próximo comando a retoma antes de rodar."
_REVIEW_HINT = "O estado já foi gravado (state_committed=true): execute review para regenerar a página."


def _error_hint(command, event):
    """Dica do erro, só quando há o que fazer além de corrigir e repetir; senão `None`.

    `roteiro` nunca recebe a de `review`: ela fala do Storyboard do b-roll e se confunde
    com `roteiro --action review`.
    """
    if event.get("recovery_pending"):
        return _RECOVERY_HINT
    if event.get("state_committed") and command != "roteiro":
        return _REVIEW_HINT
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


def _classify_audited_error(event, exc, log):
    """Preenche o evento com a classificação da exceção capturada por `audited`."""
    event["status"] = "error"
    event["recovery_pending"] = bool(log and (log.parent / ".pending-transaction.json").exists())
    # Diagnostics survive regardless of classification, redacted like everything else here.
    event["type"] = type(exc).__name__
    event["repr"] = scrub_home(redact(repr(exc)))
    event["traceback"] = scrub_home(redact(traceback.format_exc()))
    if isinstance(exc, (KeyError, TypeError, AttributeError)):
        # These are bug signatures, not user-fixable input problems; the traceback is what
        # a maintainer needs, not a RULES.md pointer.
        event["error_code"] = "INTERNAL_ERROR"
        event["message"] = (
            f"Erro interno inesperado (bug) [type: {type(exc).__name__}]. Reporte incluindo diagnostics.jsonl"
            + (f" ({log})." if log else ".")
        )
        return
    from .http import ProviderError  # local: avoids a runtime<->http import cycle

    # `MissingToolError` (yt-dlp/ffmpeg ausente) é ao mesmo tempo `ProviderError` e
    # `PrerequisiteError`: sai 4, com a mensagem e o aviso de fonte de sempre.
    event["error_code"] = error_code_for(exc)
    if isinstance(exc, ProviderError):
        event["message"] = provider_error_message(redact(exc))
        current = ACTIVE.get()
        if current is not None:
            current["warnings"].append({"code": "PROVIDER_ERROR", "message": redact(exc)})
            event["warnings"] = current["warnings"]
    else:
        event["message"] = redact(exc)


def _audited_error_failure(args, event, log, app_log_path):
    """Monta o `OperationError` da exceção já classificada por `_classify_audited_error`."""
    # Traceback e repr nunca vão para a pessoa, em nenhum comando: ficam só no
    # evento que `audited` grava em diagnostics.jsonl.
    payload = {k: v for k, v in event.items() if k not in ("traceback", "repr")}
    hint = _error_hint(args.command, event)
    if hint:
        payload["hint"] = hint
    payload["log"] = str(log) if log else None
    payload["app_log"] = str(app_log_path) if app_log_path and app_log_path.is_file() else None
    if args.command in QUIET_ERROR_COMMANDS and event["error_code"] != "INTERNAL_ERROR":
        # `plugins`/`x`/`assets`/`client` não gravam no projeto (ou, no `assets`, só leem):
        # erro de uso ali (flag faltando, plugin inexistente) é só a mensagem — a
        # dica de recovery/review era ruído. `export` grava só numa pasta nova em
        # exports/ e nunca no manifesto nem no journal (recusa journal pendente
        # antes de começar): a dica de recovery também não vale lá. `analysis` só grava
        # em analysis/, sob a trava própria, nunca no manifesto.
        payload.pop("hint", None)
    return OperationError(payload)


def _audited_interrupt_failure(event, log, app_log_path):
    """Monta o `OperationError` de uma interrupção (Ctrl-C) durante `audited`."""
    event["status"] = "interrupted"
    event["error_code"] = "INTERRUPTED"
    return OperationError(
        {
            **event,
            "message": "Operação interrompida. O próximo comando recuperará uma gravação pendente, se houver.",
            "log": str(log) if log else None,
            "app_log": str(app_log_path) if app_log_path and app_log_path.is_file() else None,
        }
    )


def diagnostics_path(project):
    """`<projeto>/brolls/diagnostics.jsonl`; sem projeto, `$GB_HOME/diagnostics.jsonl`.

    `None` só quando nem a pasta pessoal se resolve (sem HOME no ambiente)."""
    if project:
        return Path(project).resolve() / "brolls/diagnostics.jsonl"
    try:
        return _paths.gb_home() / "diagnostics.jsonl"
    except RuntimeError:
        return None


def _audit_log_wanted(event, log, read_only, in_project):
    """Se o evento vai para `log`.

    Um comando somente leitura nunca cria a árvore do projeto só para logar. Sem
    projeto, só um erro vai para `$GB_HOME/diagnostics.jsonl` (o sucesso não deixa
    rastro fora de um projeto)."""
    if not log:
        return False
    if in_project:
        return not read_only or log.parent.is_dir()
    return event.get("status") != "success"


def _write_audit_log(event, log, failure, result):
    """Grava a linha JSONL do evento; se a escrita falhar, anexa o aviso onde houver espaço."""
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        _ensure_private_file(log)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(scrub_home(json.dumps(event, ensure_ascii=False)) + "\n")
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
    app_log_path = Path(project).resolve() / "brolls" / "getbrolls.log" if project else None
    result = None
    failure = None
    try:
        with project_lock(None if read_only or own_lock or self_locked else project):
            result = execute(args)
            event["status"] = "success"
            # Comando que já devolve a própria lista de avisos (o `export`) marca o evento;
            # os avisos continuam no evento, e portanto no diagnostics.jsonl.
            if event["warnings"] and isinstance(result, dict) and not event.get("warnings_in_result"):
                result = {**result, "warnings": event["warnings"]}
            return result
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
    ) as exc:
        _classify_audited_error(event, exc, log if project else None)
        failure = _audited_error_failure(args, event, log, app_log_path)
        raise failure from None
    except KeyboardInterrupt:
        failure = _audited_interrupt_failure(event, log, app_log_path)
        raise failure from None
    finally:
        event["duration_ms"] = round((time.monotonic() - started) * 1000)
        if _audit_log_wanted(event, log, read_only or self_locked, in_project=bool(project)):
            _write_audit_log(event, log, failure, result)
        ACTIVE.reset(token)


def write_diagnostics_log(project, event):
    """Append one diagnostics event to `diagnostics_path(project)`; best-effort.

    Used by cli.py's fallback handler for errors that happen outside audited() (e.g.
    before argument parsing finishes). Without a project the event goes to
    `$GB_HOME/diagnostics.jsonl`. Returns the log path, or None when it could not write.
    """
    log = diagnostics_path(project)
    if log is None:
        return None
    event = {"at": now(), **event}
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        _ensure_private_file(log)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(scrub_home(json.dumps(event, ensure_ascii=False)) + "\n")
        return log
    except OSError:
        return None


class OperationError(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload.get("message", "Falha na operação."))
