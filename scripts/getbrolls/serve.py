"""Servidor HTTP local para abrir o Storyboard (brolls/review.html) no navegador.

Serve `<project>/brolls/` somente leitura, vinculado a 127.0.0.1 (nunca 0.0.0.0), e aceita uma
única escrita por HTTP, `POST /__save`, restrita ao token da sessão, que grava as decisões da
revisão dentro do projeto. Sem cache: o Storyboard e suas prévias podem mudar a qualquer revisão
local. Não cria a árvore do projeto nem toma a trava exclusiva; se `brolls/review.html` não
existir, falha cedo com uma mensagem clara em vez de servir um diretório vazio.
"""

# pylint: disable=missing-function-docstring,invalid-name,too-many-return-statements
# pylint: disable=redefined-builtin,too-many-locals,consider-using-with
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0); `invalid-name`
# e `redefined-builtin` vêm da API do `http.server` (`do_POST`, `log_message(...,
# format, ...)`), que não muda.

from __future__ import annotations

import atexit
import contextlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

from . import _paths, effects, labels, logs, runtime
from .config import SECRET_ENV_SUFFIXES as _SECRET_ENV_SUFFIXES
from .config import SERVE_CHILD_ENV, SERVE_CHILD_TOKEN
from .sdk.files import refuse_link

DEFAULT_PORT = 8767

# Cabeçalho do token de sessão: sem ele (ou com o token errado) o POST é recusado.
TOKEN_HEADER = "X-GetBrolls-Token"  # noqa: S105 - header name, not a secret value
SAVE_PATH = "/__save"
# Identidade do servidor: quem pergunta descobre se o PID do arquivo ainda é nosso.
PING_PATH = "/__ping"
# Maior porta TCP: o ping recusa qualquer outra coisa antes de montar a URL.
MAX_PORT = 65535
# Mesmo teto do `import-review`: um board legítimo não passa disso.
MAX_SAVE_BYTES = 2000000
PID_FILE = ".serve.pid"
LOG_FILE = ".serve.log"
# O log da rodada anterior fica guardado em `.serve.log.1`, cortado no último 1 MB:
# perder o motivo da queda anterior é pior que um arquivo a mais, e deixar o log
# crescer sem teto enche a pasta do projeto do usuário.
LOG_ROTATE_FILE = ".serve.log.1"
LOG_KEEP_BYTES = 1024 * 1024
# Teto do ping de identidade no `status`, que é leitura barata: quando o PID morreu
# nem perguntamos, e quando está vivo o servidor local responde em milissegundos —
# uma repetição cobre o aperto de um processo que acabou de subir. No pior caso o
# `status` responde "não está rodando", e ninguém perde nada com isso.
PING_TIMEOUT_S = 0.25
PING_RETRIES = 1
# Quem vai *agir* sobre o processo (parar, ou decidir que precisa subir outro) paga
# mais para ter certeza: um servidor vivo mas ocupado — máquina fria, um GIF grande
# sendo servido — demora mais que 0,25 s a responder, e classificá-lo como morto
# apagaria o PID file sem nunca mandar o SIGTERM, deixando o processo órfão.
PING_TIMEOUT_ACT_S = 1.0
# Espera pelo servidor de fundo: generosa de propósito, porque uma máquina de CI
# fria leva segundos para subir o interpretador e um filho morto falha na hora.
BACKGROUND_TIMEOUT = 60.0
REVIEWS_DIR = "reviews"
# O que `review.html` de fato referencia (ver `rendering.safe_preview_url` e
# `storyboard.render_page`): pôsteres, contact sheets, GIFs e o clipe final. Tudo o
# mais em `brolls/` — manifest.json, .serve.pid, .serve.log, diagnostics.jsonl,
# reviews/*.json, listagem de diretório, arquivos ocultos — responde 404.
ALLOWED_GET_FOLDERS = ("previews", "clips")
# NUL and the other control characters never belong in a served file name. They are
# refused by name, not by waiting for `Path.resolve()` or `open()` to raise: whether
# those raise for an embedded NUL depends on the platform and the Python version.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


_logger = logs.get("serve")


class _ExclusiveServer(ThreadingHTTPServer):
    """Sem SO_REUSEADDR: no Windows ele deixaria dois servidores na mesma porta, e a
    queda para porta livre nunca aconteceria."""

    allow_reuse_address = False

    # Sorteados em `start()`; declarados aqui porque o handler também os lê.
    # `None` de propósito: um `""` faria `compare_digest("", "")` valer como token
    # correto, e um servidor ainda não inicializado aceitaria gravação sem segredo.
    save_token: str | None = None
    session_id: str = ""

    def handle_error(self, request, client_address):
        """Cliente que sumiu no meio da resposta não é falha do servidor: só uma linha DEBUG, sem traceback."""
        # Aba fechada, `urlopen` que não leu o corpo. Só a classe do erro vai para o log; qualquer outro erro
        # segue o relatório padrão.
        error = sys.exception()
        if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            logs.event(_logger, logging.DEBUG, "client_gone", error=type(error).__name__)
            return
        super().handle_error(request, client_address)


def _resolve_allowed_target(folder, served_parent, candidate):
    """Resolve an allowed folder and the request's target file inside it for
    `_served_file_allowed`. Returns `(folder, resolved)`, or `None` when the folder
    is a symlink, resolution fails, or the resolved folder is not a direct child of
    the directory it is trusted under (`brolls/` for `previews`/`clips`, the project
    for `broll/` in layout 1; see `_served_file_allowed` for why each check exists).
    """
    # `previews`/`clips`/`broll` resolved THROUGH its own symlink would serve the whole
    # link target tree; only a real folder directly under its trusted directory is
    # trusted as an allowlisted folder.
    # lenient: an allowlisted folder that is a link is not trusted
    if folder.is_symlink():
        return None
    try:
        served_root = served_parent.resolve(strict=False)
        real_folder = folder.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
    except (OSError, ValueError):
        # `ValueError` covers an embedded NUL byte (e.g. a percent-encoded `%00` in
        # the request path), which `Path.resolve()` raises instead of `OSError`.
        return None
    if real_folder.parent != served_root:
        return None
    return real_folder, resolved


def _clip_roots(project, directory):
    """Pastas servidas em `/clips/` e o diretório em que cada uma é confiável.

    `None` no layout 0 (só `brolls/clips/`, como sempre). No layout 1, `broll/` (irmã
    de `brolls/`, confiável sob o projeto) e a antiga `brolls/clips/` quando existe;
    `broll/` como link não serve clipe nenhum, mas a página abre.
    """
    from . import layout

    try:
        with logs.handled():  # `broll/` como link: a página abre sem clipe nenhum
            roots = layout.clip_roots(project)
    except ValueError:
        return ()
    if roots == (directory / "clips",):
        return None
    return tuple((root, directory if root.parent == directory else project) for root in roots)


class _NoCacheHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler servindo um diretório fixo, sem cache e sem log no console.

    Acrescenta um único endpoint de escrita, `POST /__save`, restrito ao token da
    sessão injetado no HTML servido: é como a página grava as decisões dentro do
    projeto em vez de mandar a pessoa caçar o arquivo na pasta de Downloads.
    """

    def _send_headers(self, code, content_type, body):
        """Envia status, `Content-Type` e `Content-Length` da resposta, sem o corpo."""
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def _head_file(self, code, content_type, body):
        """Cabeçalhos já enviados; o corpo volta como arquivo para quem chamou `send_head`:
        GET copia os bytes, HEAD só recebe os cabeçalhos."""
        self._send_headers(code, content_type, body)
        return io.BytesIO(body)

    def _refuse(self, code, message):
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self._send_headers(code, "application/json; charset=utf-8", body)
        self.wfile.write(body)

    def _head_error(self, code, message):
        """Mesmo corpo JSON de `_refuse`, devolvido como arquivo."""
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        return self._head_file(code, "application/json; charset=utf-8", body)

    def __init__(self, *args, clip_roots=None, **kwargs):
        # Antes do `super().__init__`: a stdlib atende o pedido dentro do construtor.
        self.clip_roots = clip_roots
        super().__init__(*args, **kwargs)

    def _allowed_roots(self, name):
        """(pasta, diretório em que ela é confiável) para a pasta de URL `name`."""
        base = Path(self.directory)
        if name == "clips" and self.clip_roots is not None:
            return self.clip_roots
        return ((base / name, base),)

    def _pick(self, name, tail):
        """(pasta, diretório confiável, arquivo) de `/<name>/<tail>`, ou `None`.

        Com mais de uma pasta (layout 1), vale a que tem o arquivo; o mesmo nome em duas
        pastas nunca é escolhido calado: responde 404, e `verify` diz qual é qual.
        """
        roots = self._allowed_roots(name)
        # lenient: HTTP lookup; absent answers 404 to the browser
        found = [(folder, parent) for folder, parent in roots if os.path.lexists(folder.joinpath(*tail))]
        if len(found) > 1 or not roots:
            return None
        folder, parent = found[0] if found else roots[0]
        return folder, parent, folder.joinpath(*tail)

    def _logical_parts(self, path):
        """Partes do caminho pedido, relativas a `brolls/` e já decodificadas pela stdlib, ou `None`."""
        try:
            return Path(super().translate_path(path)).relative_to(Path(self.directory)).parts
        except ValueError:
            return None

    def translate_path(self, path):
        # Só `/clips/` do layout 1 muda de lugar: o arquivo sai da pasta de clipe que o
        # tem. O resto (e todo o layout 0) é a tradução da stdlib, sem mudança.
        translated = super().translate_path(path)
        if self.clip_roots is None:
            return translated
        parts = self._logical_parts(path)
        if not parts or parts[0] != "clips" or not parts[1:]:
            return translated
        picked = self._pick("clips", parts[1:])
        return translated if picked is None else str(picked[2])

    def _served_file_allowed(self, path_only):
        """Only files under `previews/` or `clips/` (`/` and `/review.html` are handled apart).

        The decision is made on the path the stdlib will actually open, never on the
        raw URL: `translate_path` percent-decodes and normalizes AFTER any check on the
        request text, so `/previews/%2e%2e/manifest.json` would pass a prefix test on
        the URL and still land on `manifest.json`. The real target must also stay inside
        the allowed folder once symlinks are resolved. In layout 1, `/clips/` maps to
        `broll/` (a sibling of `brolls/`), so each allowed folder is checked against the
        directory it is trusted under, never against `brolls/` alone.
        """
        parts = self._logical_parts(path_only)
        # A folder name alone (`/previews`) is not a file: something must follow it.
        if not parts or not parts[1:] or parts[0] not in ALLOWED_GET_FOLDERS:
            return False
        if any(part.startswith(".") or _CONTROL_CHARACTERS.search(part) for part in parts):
            return False
        picked = self._pick(parts[0], parts[1:])
        if picked is None:
            return False
        target = _resolve_allowed_target(*picked)
        if target is None:
            return False
        folder, resolved = target
        return folder in resolved.parents

    def list_directory(self, path):
        # Nenhum caminho servido é uma listagem: até dentro de previews/clips, só
        # arquivos individuais são alcançáveis. `path` vem da assinatura da stdlib.
        del path
        return self._head_error(404, "Não encontrado.")

    def _local_request(self):
        """Só aceita pedidos endereçados a esta máquina, nesta porta.

        Um nome de domínio que resolve para 127.0.0.1 (DNS rebinding) chega com
        outro `Host`; e um `Origin` de outra página não bate com o `Host` local.
        """
        port = cast("_ExclusiveServer", self.server).server_address[1]
        host = (self.headers.get("Host") or "").strip()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
            return False
        origin = self.headers.get("Origin")
        return not (origin and origin != f"http://{host}")

    def do_POST(self):  # noqa: PLR0911 - existing size; one early return per refusal/response
        if not self._local_request():
            self._refuse(403, "Pedido de outra origem; este servidor só atende esta máquina.")
            return
        if self.path.split("?")[0] != SAVE_PATH:
            self._refuse(404, "Endereço desconhecido.")
            return
        token = self.headers.get(TOKEN_HEADER) or ""
        expected = cast("_ExclusiveServer", self.server).save_token
        # `compare_digest` de texto explode com caracteres fora de ASCII: um
        # cabeçalho qualquer não pode virar 500, é só mais um token errado.
        if not expected or not token.isascii() or not hmac.compare_digest(token, expected):
            self._refuse(403, "Token da sessão ausente ou inválido.")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._refuse(400, "Tamanho do corpo inválido.")
            return
        if length <= 0 or length > MAX_SAVE_BYTES:
            # Drena o excesso (com teto) antes de responder: sem isso o cliente
            # levaria um cano quebrado no lugar do 413 que explica o problema.
            left = min(length, MAX_SAVE_BYTES * 4)
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
            self._refuse(413, "Corpo grande demais para um arquivo de decisões.")
            return
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._refuse(400, "O corpo precisa ser JSON.")
            return
        if not isinstance(data, dict):
            self._refuse(400, "O corpo precisa ser um objeto JSON.")
            return
        self._save_and_answer(data)

    def _save_and_answer(self, data):
        """Grava as decisões e responde 200 com o caminho, ou um erro JSON.

        Uma gravação recusada (link no lugar da pasta) ou que falhou (disco cheio) responde
        à página com o motivo; sem isso a conexão caía e a página não tinha o que mostrar."""
        try:
            path = save_review(Path(self.directory), data)
        except ValueError as exc:
            self._refuse(409, str(exc))
            return
        except OSError as exc:
            self._refuse(500, f"Não consegui gravar as decisões: {exc.strerror or type(exc).__name__}.")
            return
        body = json.dumps({"path": str(path), "name": path.name}, ensure_ascii=False).encode("utf-8")
        self._send_headers(200, "application/json; charset=utf-8", body)
        self.wfile.write(body)

    def send_head(self):
        if not self._local_request():
            return self._head_error(403, "Pedido de outra origem; este servidor só atende esta máquina.")
        if self.path.split("?")[0] == PING_PATH:
            # Identidade do servidor: é assim que `status`/`stop` sabem que o PID
            # gravado ainda é deste servidor, e não de um processo que reusou o número.
            server = cast("_ExclusiveServer", self.server)
            body = json.dumps(
                {"session": server.session_id, "port": server.server_address[1]}, ensure_ascii=False
            ).encode("utf-8")
            return self._head_file(200, "application/json; charset=utf-8", body)
        # O token vive só na memória do servidor e na página servida por ele: quem
        # abrir o review.html direto do disco continua com o caminho do download.
        if self.path.split("?")[0] in ("/review.html", "/"):
            page = Path(self.directory) / "review.html"
            # lenient: HTTP: absent page falls back to the default handler
            if page.is_file():
                body = _inject_token(
                    page.read_text(encoding="utf-8"), cast("_ExclusiveServer", self.server).save_token or ""
                ).encode("utf-8")
                return self._head_file(200, "text/html; charset=utf-8", body)
        path_only = self.path.split("?")[0]
        if not self._served_file_allowed(path_only):
            return self._head_error(404, "Não encontrado.")
        try:
            return super().send_head()
        except ValueError:
            # The stdlib opens the file before sending any header, so a name it cannot
            # open (embedded NUL on platforms where the check above did not see it)
            # still gets a clean 404 instead of a dropped connection.
            return self._head_error(404, "Não encontrado.")

    def end_headers(self):
        # Em toda resposta (página, prévia, JSON de erro ou de __save): o Storyboard
        # nunca pode ser enquadrado por outra página, nem ter seu Content-Type
        # reinterpretado, nem vazar de onde veio o clique que trouxe alguém até aqui.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 - signature required by the stdlib
        # Silencioso: o processo comunica estado só via a linha JSON impressa em run().
        pass


# Quem sobe o servidor de fundo (`start_background`) põe isto no ambiente do filho: o que ele escreve em
# `.serve.log` (stdout e stderr do Python: o traceback de uma falha, um aviso, a linha com as URLs) passa por
# `filter_output`. O processo em primeiro plano não leva isto: a saída dele é da pessoa, no terminal dela.
CHILD_ENV = SERVE_CHILD_ENV


def clean_line(line) -> str:
    """`line` do log do servidor como todo log do getbrolls: segredos, depois as pastas pela marca e o que sobrou
    de fora pelo hash (a mesma ordem de `logs.RedactingFilter`). Nunca levanta."""
    try:
        return labels.outside(runtime.scrub_home(labels.text(runtime.redact(line, limit=None))))
    # logging must never break the server
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return "<linha omitida>"


class RedactedStream:
    """O texto de `raw` só sai por linhas inteiras, cada uma por `clean_line`: um segredo não atravessa o corte
    entre duas escritas. `raw` fica à mão (`raw`) para a única linha que o pai precisa ler como está, a das URLs.
    O que fica sem quebra de linha sai no fim do processo; mais que o teto de `labels` sai omitido."""

    def __init__(self, raw):
        self.raw = raw
        self._pending = ""
        self.encoding = getattr(raw, "encoding", "utf-8")
        self.errors = getattr(raw, "errors", "strict")

    def write(self, text):
        self._pending += str(text)
        *lines, self._pending = self._pending.split("\n")
        if lines:
            self.raw.write("".join(clean_line(line) + "\n" for line in lines))
            self.raw.flush()
        if len(self._pending) > labels.INPUT_MAX:
            self.raw.write(labels.bounded(self._pending) + "\n")
            self.raw.flush()
            self._pending = ""
        return len(text)

    def flush(self):
        self.raw.flush()

    def finish(self):
        """Escreve o que sobrou sem quebra de linha (a saída do processo)."""
        if self._pending:
            pending, self._pending = self._pending, ""
            with contextlib.suppress(ValueError, OSError):  # the stream may be closed by now
                self.raw.write(clean_line(pending) + "\n")
                self.raw.flush()

    def isatty(self):
        return False


def filter_output(environ=None):
    """No servidor de fundo (`CHILD_ENV`), troca `sys.stdout` e `sys.stderr` por `RedactedStream`. Devolve `True`
    quando trocou. O processo em primeiro plano não é tocado."""
    if not (environ if environ is not None else os.environ).get(CHILD_ENV):
        return False
    if not isinstance(sys.stdout, RedactedStream):
        sys.stdout = RedactedStream(sys.stdout)
        atexit.register(sys.stdout.finish)
    if not isinstance(sys.stderr, RedactedStream):
        sys.stderr = RedactedStream(sys.stderr)
        atexit.register(sys.stderr.finish)
    return True


def _inject_token(page, token):
    """Entrega o endereço e o token para o review.js, antes de ele rodar."""
    script = (
        '<script>window.GETBROLLS_SAVE={"url":'
        + json.dumps(SAVE_PATH)
        + ',"header":'
        + json.dumps(TOKEN_HEADER)
        + ',"token":'
        + json.dumps(token)
        + "};</script>"
    )
    if "</head>" in page:
        return page.replace("</head>", script + "</head>", 1)
    if "<body" in page:
        return page.replace("<body", script + "<body", 1)
    return script + page


def _write_private_text(path, text):
    """Cria ou substitui `path` com `text`, sempre 0600 (dono lê/escreve, mais ninguém).

    Cobre os dois casos: um arquivo novo nasce com o modo já restrito (o `mode` do
    `os.open` só vale na criação), e um arquivo que sobrou de uma rodada anterior —
    talvez com um umask mais frouxo — é apertado de novo pelo `chmod` explícito.
    `.serve.pid`/`.serve.log` guardam o id de sessão do servidor; nenhum dos dois
    precisa ficar legível por outra conta na máquina.

    Um link no nome é recusado antes de abrir (`refuse_link`); onde não há `O_NOFOLLOW` (Windows) resta a
    janela entre essa conferência e a abertura, a mesma que `save_review` aceita.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with effects.op("fs.write", path):
        refuse_link(path)
        handle = os.open(path, flags, 0o600)
        try:
            effects.make_private(path)  # inside: Ctrl-C during the wait still closes the handle
            Path(path).chmod(0o600)
        except BaseException:
            os.close(handle)
            raise
        with effects.owned_stream(handle) as stream:
            stream.write(text.encode("utf-8"))


def save_review(directory, data):
    """Grava as decisões em `brolls/reviews/<timestamp>.json` e devolve o caminho.

    O nome sai do relógio (`%Y%m%d-%H%M%S`, com sufixo numérico em caso de empate):
    nada do corpo enviado entra no caminho, então não há como escapar da pasta.
    """
    reviews = Path(directory) / REVIEWS_DIR
    # lenient: link refusal; the open, copy or mkdir that follows raises the EACCES of a locked folder
    if reviews.is_symlink():
        raise ValueError(
            f"{reviews} é um link simbólico; a pasta de decisões precisa ser uma pasta de verdade dentro do projeto."
        )
    effects.mkdir(reviews, parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    with effects.op("server.save", reviews, bytes=len(body)) as saving:
        # O_EXCL nunca segue um arquivo existente e O_NOFOLLOW recusa um link plantado
        # no lugar do nome: a gravação cria o arquivo ou falha, jamais escreve através.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        extra = 0
        while True:
            path = reviews / (f"{stamp}.json" if not extra else f"{stamp}-{extra}.json")
            # `O_NOFOLLOW` não existe no Windows (vale 0 ali), então a recusa do link
            # plantado é feita antes do `open`, com `lstat`, igual em todo sistema.
            # lenient: link refusal; the open, copy or mkdir that follows raises the EACCES of a locked folder
            if path.is_symlink():
                raise ValueError(
                    f"{labels.shown(path)} é um link simbólico; apague esse link antes de salvar as "
                    "decisões — o arquivo tem que ficar dentro do projeto."
                )
            try:
                handle = os.open(path, flags, 0o600)
            except FileExistsError:
                # lenient: link refusal; the open, copy or mkdir that follows raises the EACCES of a locked folder
                if path.is_symlink():
                    # Alguém plantou um link no nome que íamos usar: nada de escrever
                    # através dele, nem de escolher outro nome como se fosse normal.
                    raise ValueError(
                        f"{labels.shown(path)} é um link simbólico; apague esse link antes de salvar as "
                        "decisões — o arquivo tem que ficar dentro do projeto."
                    ) from None
                extra += 1
                if extra > 50:  # noqa: PLR2004 - cap on alternative-name attempts before giving up
                    raise
                continue
            except OSError as exc:
                raise ValueError(
                    f"Não consegui gravar as decisões em {labels.shown(path)}: {exc.strerror}. "
                    "Confira se não há um link simbólico no lugar do arquivo."
                ) from exc
            try:
                with effects.owned_stream(handle) as stream:
                    effects.make_private(path)  # inside: Ctrl-C during the wait closes it and removes it
                    stream.write(body)
            except BaseException:
                # Disk full or interrupted mid-write: no truncated decisions file is left behind, and a
                # cleanup that fails (an antivirus holding the file on Windows) must not hide the error.
                with contextlib.suppress(OSError):
                    effects.unlink(path, missing_ok=True)
                raise
            saving.target = path
            return path


def start(project, port: int = DEFAULT_PORT):
    """Sobe o servidor em 127.0.0.1, tentando `port` e caindo para uma porta livre se ocupada.

    Retorna (server, port). O chamador decide quando chamar `serve_forever()`/fechar.
    """
    directory = _brolls(project)
    review = directory / "review.html"
    if not _paths.strict_is_file(review):
        where = labels.shown(review, project=project)
        raise ValueError(f"Storyboard não encontrado em {where}. Gere-o antes com o comando `review`.")
    handler = partial(
        _NoCacheHandler,
        directory=str(directory),
        clip_roots=_clip_roots(Path(project).expanduser().resolve(), directory),
    )
    try:
        server = _ExclusiveServer(("127.0.0.1", port), handler)
    except OSError:
        # Porta pedida ocupada: cai para uma porta efêmera livre.
        server = _ExclusiveServer(("127.0.0.1", 0), handler)
    # Um token por sessão: some quando o servidor cai, e só a página servida o conhece.
    server.save_token = secrets.token_urlsafe(32)
    # Identidade sorteada: é o que separa "o nosso servidor" de um PID reaproveitado.
    server.session_id = secrets.token_urlsafe(16)
    return server, server.server_address[1]


def run(project, port: int = DEFAULT_PORT) -> int:
    """Sobe o servidor, imprime a URL em JSON e serve até Ctrl+C (saída limpa, exit 0).

    No getbrolls.log: `server.start` quando a porta está tomada (antes da linha com a URL) e
    `server.stop` quando ele para (`effects.serve`)."""
    with effects.op("server.start", "127.0.0.1") as starting:
        server, bound_port = start(project, port)
        starting.target = f"127.0.0.1:{bound_port}"
        starting.set(port=bound_port)
    directory = _brolls(project)
    payload = {
        "urls": _urls(bound_port),
        "port": bound_port,
        "session": server.session_id,
        "directory": str(directory),
    }
    stream = sys.stdout
    if isinstance(stream, RedactedStream):
        # The one line the parent reads as it is (the URLs); the folder goes by its label.
        payload["directory"] = labels.shown(directory, project=project)
        stream = stream.raw
    print(json.dumps(payload, ensure_ascii=False), file=stream)
    stream.flush()
    try:
        effects.serve(server, address=f"127.0.0.1:{bound_port}")
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _brolls(project):
    """Pasta `brolls/` do projeto, já resolvida."""
    return Path(project).expanduser().resolve() / "brolls"


def _urls(port):
    """Endereços locais em que o `review.html` fica disponível nesta porta."""
    return [
        f"http://localhost:{port}/review.html",
        f"http://127.0.0.1:{port}/review.html",
    ]


def _windows():
    """Estamos no Windows? Passa por `_paths._os_name()`, o ponto onde os testes simulam o SO."""
    return _paths._os_name() == "nt"  # pylint: disable=protected-access


def _alive(pid):
    """O processo do PID file ainda existe? Somente leitura, e igual no Windows.

    `os.kill(pid, 0)` no Windows mataria o processo (a stdlib mapeia qualquer sinal
    para TerminateProcess), então lá a pergunta vai pela API do sistema.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if _windows():
        import ctypes

        kernel32 = ctypes.windll.kernel32  # pyright: ignore[reportAttributeAccessIssue] - Windows-only attribute
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        # Falso positivo do pylint em 3.11: c_ulong() sem args é válido (value=0 por padrão).
        # pylint: disable-next=no-value-for-parameter
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # noqa: PLR2004 - STILL_ACTIVE, a Windows API constant
    try:
        os.kill(pid, 0)
    except OSError:
        # Sem processo, ou um PID de outro dono (nunca o nosso servidor; quem confirma é o ping).
        return False
    return True


def _reap(pid):
    """Colhe o filho já encerrado: um zumbi ainda responde a `kill(pid, 0)`."""
    if _windows():
        return
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)


def read_pid(project):
    """Conteúdo do PID file, ou None quando não há (ou está ilegível)."""
    path = _brolls(project) / PID_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _valid_ping_target(url):
    """O alvo do ping é mesmo `http://` para 127.0.0.1/localhost?

    `_ping` monta a URL de um template fixo com uma porta inteira local — nunca de
    entrada externa —, mas a checagem de esquema/host fica explícita mesmo assim,
    para não depender de quem chamou ter montado a URL com cuidado.
    """
    import urllib.parse

    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")


def _open_ping(url, *, timeout):
    """Abre o ping sem seguir redirecionamento nem proxy: um 3xx vira `HTTPError` e o ping dá "não é nosso".

    `ProxyHandler({})` ignora `HTTP_PROXY`/`http_proxy` do ambiente: o ping é de loopback, e um proxy que
    respondesse no lugar do servidor faria `status` e `--stop` julgarem errado quem está na porta."""
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        # pylint: disable-next=too-many-arguments,too-many-positional-arguments
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002, PLR0913, PLR0917
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with effects.op("net.get", url):  # `_ping` already pinned scheme+host via `_valid_ping_target`
        return opener.open(url, timeout=timeout)


def _ping(port, session, timeout=1.0):
    """O servidor desta porta é o da nossa sessão? Só uma leitura, nada é gravado.

    Sem isso, um PID reaproveitado pelo sistema apareceria como "rodando" — e um
    `--stop` mataria um processo inocente.
    """
    # Porta é um `int` de verdade (nunca `bool`, texto ou float) dentro da faixa TCP: o PID file é lido do disco.
    port_ok = not isinstance(port, bool) and isinstance(port, int) and 0 < port <= MAX_PORT
    if not port_ok or not session or not isinstance(session, str):
        return False
    import urllib.error

    url = f"http://127.0.0.1:{port}{PING_PATH}"
    if not _valid_ping_target(url):
        return False
    try:
        with _open_ping(url, timeout=timeout) as response:
            answer = json.loads(response.read(4096).decode("utf-8"))  # o corpo do ping é minúsculo
    except urllib.error.HTTPError as err:
        # 3xx (não seguido) e 4xx seguram o socket: sem fechar, o 3.14 avisa `ResourceWarning`.
        # Vem antes do `URLError` porque `HTTPError` é subclasse dele.
        err.close()
        return False
    except (OSError, ValueError, urllib.error.URLError):
        return False
    if not isinstance(answer, dict):
        return False
    candidate = answer.get("session")
    # `compare_digest` de tempo constante, igual ao token: o id de sessão não é
    # segredo (a resposta do ping é pública), mas a checagem segue o mesmo padrão
    # de comparação do resto do arquivo em vez de um `==` avulso.
    return isinstance(candidate, str) and hmac.compare_digest(candidate, session)


def _rotate_log(log):
    """Guarda o log da rodada anterior em `.serve.log.1`, com no máximo `LOG_KEEP_BYTES`.

    Roda no `start_background`, antes de zerar o log novo. Só o último 1 MB é
    guardado: o fim do arquivo é onde está o erro, e o começo de um log de horas de
    servidor não ajuda ninguém. Falha de disco aqui nunca impede o servidor de subir.
    """
    try:
        # lenient: log rotation inside try is optional: a log it cannot read is not rotated
        if not log.is_file() or log.stat().st_size == 0:
            return
        with log.open("rb") as stream:
            if log.stat().st_size > LOG_KEEP_BYTES:
                stream.seek(-LOG_KEEP_BYTES, os.SEEK_END)
            kept = stream.read()
        rotated = log.with_name(LOG_ROTATE_FILE)
        # Same discipline as `_write_private_text`: private from the first byte, and
        # `O_NOFOLLOW` refuses a symlink planted at the rotated name instead of
        # writing through it.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        with effects.op("fs.write", rotated):
            refuse_link(rotated)
            handle = os.open(rotated, flags, 0o600)
            with effects.owned_stream(handle) as stream:
                effects.make_private(rotated)
                stream.write(kept)
    # lenient: log rotation is optional: a failure keeps the current log as it is
    except OSError:
        return


def _child_environment(source_env, scripts):
    """Ambiente do servidor de fundo: uma cópia de `source_env` sem nenhuma variável
    com jeito de segredo (`*_API_KEY`, `*_TOKEN`, `*_SECRET` — cobre PEXELS_API_KEY,
    PIXABAY_API_KEY, YOUTUBE_API_KEY e qualquer outra do mesmo formato).

    `serve` nunca chama um provedor; não há razão para essas chaves chegarem a um
    processo solto cujo stdout/stderr vão parar num log dentro do projeto.
    """
    environment = {key: value for key, value in source_env.items() if not key.endswith(_SECRET_ENV_SUFFIXES)}
    if scripts is not None:
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(scripts) + (os.pathsep + existing if existing else "")
    else:
        # Pacote instalado já está no `sys.path` do interpretador; um `PYTHONPATH` herdado
        # só poderia pôr outro `getbrolls` (ou um módulo com o mesmo nome) na frente dele.
        environment.pop("PYTHONPATH", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment[CHILD_ENV] = SERVE_CHILD_TOKEN
    return environment


def _ours(data, timeout=PING_TIMEOUT_S):
    """O PID gravado ainda é o nosso servidor? Existir não basta: tem que responder.

    A ordem importa para o custo: `_reap` derruba o zumbi do nosso próprio filho já
    encerrado (que ainda passaria no `kill(pid, 0)`), `_alive` responde na hora, e só
    um PID vivo paga o ping. Com o PID morto, `state()` não abre socket nenhum.

    `timeout` é por tentativa. Quem só lê usa o teto curto; quem vai agir sobre o
    processo passa `PING_TIMEOUT_ACT_S`, porque aqui um falso "não é nosso" custa um
    processo órfão, não uma linha de status desatualizada.
    """
    if not data:
        return False
    pid = data.get("pid")
    if isinstance(pid, int) and pid > 0:
        _reap(pid)
    if not _alive(pid):
        return False
    for attempt in range(PING_RETRIES + 1):
        if _ping(data.get("port"), data.get("session"), timeout=timeout):
            return True
        if attempt < PING_RETRIES and not _alive(pid):
            return False
    return False


def state(project, timeout=PING_TIMEOUT_S):
    """`serve.running` para o `status`: lê o PID file e confirma a identidade pelo ping."""
    data = read_pid(project) or {}
    pid = data.get("pid")
    running = _ours(data, timeout=timeout)
    return {
        "running": running,
        "pid": pid if running else None,
        "port": data.get("port") if running else None,
        "urls": data.get("urls") or [] if running else [],
        "pid_file": str(_brolls(project) / PID_FILE) if data else None,
    }


def _payload_in(output: str) -> dict | None:
    """A linha JSON com a porta, se já apareceu no log do servidor de fundo."""
    for line in output.splitlines():
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and candidate.get("port"):
            return candidate
    return None


def _tail(output: str, lines: int = 20) -> str:
    """O fim do log junto do erro: sem isso, a falha do filho fica invisível."""
    text = "\n".join(output.splitlines()[-lines:]).strip()
    return f"\n\nÚltimas linhas do log:\n{text}" if text else ""


def start_background(project, port: int = DEFAULT_PORT):
    """Sobe o servidor num processo solto e devolve as URLs quando ele já responde.

    Um subprocesso (nunca `fork`) mantém o mesmo comportamento no Windows: o filho
    escreve a linha JSON de `run()` no log e o pai lê dali a porta realmente usada.
    """
    directory = _brolls(project)
    review = directory / "review.html"
    if not _paths.strict_is_file(review):
        where = labels.shown(review, project=project)
        raise ValueError(f"Storyboard não encontrado em {where}. Gere-o antes com o comando `review`.")
    # Antes de subir outro servidor, a pergunta tem que ser respondida com folga: um
    # servidor vivo e ocupado classificado como morto viraria uma segunda instância.
    current = state(project, timeout=PING_TIMEOUT_ACT_S)
    if current["running"]:
        return {"background": True, "already_running": True, **current}
    log = directory / LOG_FILE
    _rotate_log(log)
    _write_private_text(log, "")
    command = [*_paths.cli_argv(), "serve", "--project", str(Path(project).expanduser().resolve())]
    if port:
        # Só uma porta de verdade vai ao filho: `--port` aceita 1 a 65535, e sem ela o filho
        # usa a padrão (ou uma livre, se ocupada).
        command += ["--port", str(port)]
    # O filho precisa achar `getbrolls` e falar UTF-8 mesmo num console legado:
    # nada disso pode depender do diretório de trabalho ou do locale da máquina.
    scripts = _paths.install().package_dir.parent if _paths.origin() == "checkout" else None
    environment = _child_environment(os.environ, scripts)
    extra = {}
    if _windows():
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: o servidor sobrevive ao console.
        extra["creationflags"] = 0x00000008 | 0x00000200
    else:
        extra["start_new_session"] = True
    with effects.op("proc.spawn", command) as spawning, log.open("ab") as stream:
        process = subprocess.Popen(
            command,
            stdout=stream,
            stderr=stream,
            stdin=subprocess.DEVNULL,
            cwd=str(directory),
            env=environment,
            **extra,
        )
        spawning.set(pid=process.pid)
    payload = None
    # Máquina de CI fria gasta segundos só para subir o interpretador; a espera é
    # longa porque quem morre é detectado na hora, não por esgotar o relógio.
    deadline = time.monotonic() + BACKGROUND_TIMEOUT
    while time.monotonic() < deadline:
        output = log.read_text(encoding="utf-8", errors="replace")
        payload = _payload_in(output)
        if process.poll() is not None and not payload:
            raise ValueError(
                "O servidor do Storyboard não subiu. Rode `serve` sem `--background` "
                f"para ver o erro; a saída ficou em {labels.shown(log)}.{_tail(output)}"
            )
        if payload:
            break
        time.sleep(0.05)
    if not payload:
        with effects.op("proc.kill", command, pid=process.pid):
            process.terminate()
        raise ValueError(
            "O servidor do Storyboard não respondeu a tempo. Rode `serve` sem "
            f"`--background` para ver o que aconteceu (saída em {labels.shown(log)})."
            f"{_tail(log.read_text(encoding='utf-8', errors='replace'))}"
        )
    record = {
        "pid": process.pid,
        "port": payload["port"],
        # Identidade do processo: PID sozinho é reaproveitável pelo sistema operacional.
        "session": payload.get("session"),
        "executable": sys.executable,
        "urls": payload.get("urls") or _urls(payload["port"]),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_private_text(directory / PID_FILE, json.dumps(record, ensure_ascii=False))
    return {"background": True, "already_running": False, "log": str(log), **record}


def stop(project):
    """Encerra o servidor de fundo pelo PID file; PID morto só limpa o arquivo."""
    directory = _brolls(project)
    path = directory / PID_FILE
    data = read_pid(project)
    if not data:
        return {"stopped": False, "reason": "not_running", "pid": None}
    pid = data.get("pid")
    address = f"127.0.0.1:{data.get('port')}"
    with effects.op("server.stop", address) as stopping:
        return _stop(path, data, pid, stopping)


def _stop(path, data, pid, stopping):
    """O corpo de `stop`, dentro da linha `server.stop` (`skipped` quando o PID não é mais nosso)."""
    if not _ours(data, timeout=PING_TIMEOUT_ACT_S):
        # O processo pode ter morrido ou o número ter sido reaproveitado por outro
        # programa: só limpamos o arquivo. Nunca matamos um PID que não se identificou.
        stopping.skip()
        effects.unlink(path, missing_ok=True)
        return {"stopped": False, "reason": "stale_pid", "pid": pid}
    # `_ours` só responde sim para um PID vivo que se identificou: aqui é inteiro.
    assert isinstance(pid, int)  # noqa: S101 - type narrowing only; `_ours` already guarantees this at runtime
    if _windows():
        effects.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            operation="proc.kill",
            capture_output=True,
            check=False,
        )
    else:
        import signal

        with contextlib.suppress(OSError):
            effects.kill(pid, signal.SIGTERM, target="serve")
        _reap(pid)
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        _reap(pid)
        time.sleep(0.05)
    if _alive(pid) and not _windows():
        import signal

        with contextlib.suppress(OSError):
            effects.kill(pid, signal.SIGKILL, target="serve")
        _reap(pid)
        deadline = time.monotonic() + 2
        while _alive(pid) and time.monotonic() < deadline:
            _reap(pid)
            time.sleep(0.05)
    effects.unlink(path, missing_ok=True)
    return {"stopped": not _alive(pid), "reason": None, "pid": pid}
