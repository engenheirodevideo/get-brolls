"""Servidor HTTP local, somente leitura, para abrir o Storyboard (brolls/review.html) no navegador.

Serve apenas `<project>/brolls/`, vinculado a 127.0.0.1 (nunca 0.0.0.0): nada aqui é
exposto fora desta máquina. Sem cache — o Storyboard e suas prévias podem mudar a
qualquer revisão local. Não cria a árvore do projeto nem toma a trava exclusiva
(comando somente leitura, como `status`); se `brolls/review.html` não existir, falha
cedo com uma mensagem clara em vez de servir um diretório vazio.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

DEFAULT_PORT = 8767

# Cabeçalho do token de sessão: sem ele (ou com o token errado) o POST é recusado.
TOKEN_HEADER = "X-GetBrolls-Token"
SAVE_PATH = "/__save"
# Identidade do servidor: quem pergunta descobre se o PID do arquivo ainda é nosso.
PING_PATH = "/__ping"
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
ALLOWED_GET_PREFIXES = ("previews/", "clips/")


class _ExclusiveServer(ThreadingHTTPServer):
    """Sem SO_REUSEADDR: no Windows ele deixaria dois servidores na mesma porta, e a
    queda para porta livre nunca aconteceria."""

    allow_reuse_address = False

    # Sorteados em `start()`; declarados aqui porque o handler também os lê.
    # `None` de propósito: um `""` faria `compare_digest("", "")` valer como token
    # correto, e um servidor ainda não inicializado aceitaria gravação sem segredo.
    save_token: str | None = None
    session_id: str = ""


class _NoCacheHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler servindo um diretório fixo, sem cache e sem log no console.

    Acrescenta um único endpoint de escrita, `POST /__save`, restrito ao token da
    sessão injetado no HTML servido: é como a página grava as decisões dentro do
    projeto em vez de mandar a pessoa caçar o arquivo na pasta de Downloads.
    """

    def _refuse(self, code, message):
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _head_error(self, code, message):
        """Mesmo corpo JSON de `_refuse`, mas devolvido como arquivo para quem chamou
        `send_head`: GET copia os bytes, HEAD só recebe os cabeçalhos."""
        import io

        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        return io.BytesIO(body)

    def _allowed_get_path(self, path_only):
        """Só `previews/…` e `clips/…` (mais `/` e `/review.html`, tratados à parte)."""
        rel = path_only.lstrip("/")
        if not rel or any(part.startswith(".") for part in rel.split("/")):
            return False
        return rel.startswith(ALLOWED_GET_PREFIXES)

    def _within_served_directory(self, path_only):
        """Recusa um symlink plantado dentro de previews/ ou clips/ que aponte para
        fora da pasta servida. `translate_path` (stdlib) já ignora componentes `..`
        antes deste ponto; o que falta é resolver o link e conferir o destino real."""
        local = Path(self.translate_path(path_only))
        base = Path(self.directory).resolve()
        try:
            resolved = local.resolve(strict=False)
        except OSError:
            return False
        return resolved == base or base in resolved.parents

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
        port = cast(_ExclusiveServer, self.server).server_address[1]
        host = (self.headers.get("Host") or "").strip()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{host}",):
            return False
        return True

    def do_POST(self):  # noqa: N802 - assinatura exigida pela stdlib
        if not self._local_request():
            self._refuse(403, "Pedido de outra origem; este servidor só atende esta máquina.")
            return
        if self.path.split("?")[0] != SAVE_PATH:
            self._refuse(404, "Endereço desconhecido.")
            return
        token = self.headers.get(TOKEN_HEADER) or ""
        expected = cast(_ExclusiveServer, self.server).save_token
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
        path = save_review(Path(self.directory), data)
        body = json.dumps({"path": str(path), "name": path.name}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_head(self):
        import io

        if not self._local_request():
            body = json.dumps(
                {"error": "Pedido de outra origem; este servidor só atende esta máquina."},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        if self.path.split("?")[0] == PING_PATH:
            # Identidade do servidor: é assim que `status`/`stop` sabem que o PID
            # gravado ainda é deste servidor, e não de um processo que reusou o número.
            body = json.dumps(
                {
                    "session": cast(_ExclusiveServer, self.server).session_id,
                    "port": cast(_ExclusiveServer, self.server).server_address[1],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        # O token vive só na memória do servidor e na página servida por ele: quem
        # abrir o review.html direto do disco continua com o caminho do download.
        if self.path.split("?")[0] in ("/review.html", "/"):
            page = Path(self.directory) / "review.html"
            if page.is_file():
                body = _inject_token(
                    page.read_text(encoding="utf-8"), cast(_ExclusiveServer, self.server).save_token or ""
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return io.BytesIO(body)
        path_only = self.path.split("?")[0]
        if not self._allowed_get_path(path_only) or not self._within_served_directory(path_only):
            return self._head_error(404, "Não encontrado.")
        return super().send_head()

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

    def log_message(self, format, *args):  # noqa: A002 - assinatura exigida pela stdlib
        # Silencioso: o processo comunica estado só via a linha JSON impressa em run().
        pass


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
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    handle = os.open(path, flags, 0o600)
    try:
        os.chmod(path, 0o600)
    except BaseException:
        os.close(handle)
        raise
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)


def save_review(directory, data):
    """Grava as decisões em `brolls/reviews/<timestamp>.json` e devolve o caminho.

    O nome sai do relógio (`%Y%m%d-%H%M%S`, com sufixo numérico em caso de empate):
    nada do corpo enviado entra no caminho, então não há como escapar da pasta.
    """
    reviews = Path(directory) / REVIEWS_DIR
    if reviews.is_symlink():
        raise ValueError(
            f"{reviews} é um link simbólico; a pasta de decisões precisa ser uma pasta de verdade dentro do projeto."
        )
    reviews.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    # O_EXCL nunca segue um arquivo existente e O_NOFOLLOW recusa um link plantado
    # no lugar do nome: a gravação cria o arquivo ou falha, jamais escreve através.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    extra = 0
    while True:
        path = reviews / (f"{stamp}.json" if not extra else f"{stamp}-{extra}.json")
        # `O_NOFOLLOW` não existe no Windows (vale 0 ali), então a recusa do link
        # plantado é feita antes do `open`, com `lstat`, igual em todo sistema.
        if path.is_symlink():
            raise ValueError(
                f"{path} é um link simbólico; apague esse link antes de salvar as "
                "decisões — o arquivo tem que ficar dentro do projeto."
            )
        try:
            handle = os.open(path, flags, 0o600)
        except FileExistsError:
            if path.is_symlink():
                # Alguém plantou um link no nome que íamos usar: nada de escrever
                # através dele, nem de escolher outro nome como se fosse normal.
                raise ValueError(
                    f"{path} é um link simbólico; apague esse link antes de salvar as "
                    "decisões — o arquivo tem que ficar dentro do projeto."
                ) from None
            extra += 1
            if extra > 50:
                raise
            continue
        except OSError as exc:
            raise ValueError(
                f"Não consegui gravar as decisões em {path}: {exc.strerror}. "
                "Confira se não há um link simbólico no lugar do arquivo."
            ) from exc
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
        return path


def start(project, port: int = DEFAULT_PORT):
    """Sobe o servidor em 127.0.0.1, tentando `port` e caindo para uma porta livre se ocupada.

    Retorna (server, port). O chamador decide quando chamar `serve_forever()`/fechar.
    """
    directory = Path(project).expanduser().resolve() / "brolls"
    review = directory / "review.html"
    if not review.is_file():
        raise ValueError(f"Storyboard não encontrado em {review}. Gere-o antes com o comando `review`.")
    handler = partial(_NoCacheHandler, directory=str(directory))
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
    """Sobe o servidor, imprime a URL em JSON e serve até Ctrl+C (saída limpa, exit 0)."""
    server, bound_port = start(project, port)
    directory = Path(project).expanduser().resolve() / "brolls"
    payload = {
        "urls": [
            f"http://localhost:{bound_port}/review.html",
            f"http://127.0.0.1:{bound_port}/review.html",
        ],
        "port": bound_port,
        "session": server.session_id,
        "directory": str(directory),
    }
    print(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _brolls(project):
    return Path(project).expanduser().resolve() / "brolls"


def _urls(port):
    return [
        f"http://localhost:{port}/review.html",
        f"http://127.0.0.1:{port}/review.html",
    ]


def _alive(pid):
    """O processo do PID file ainda existe? Somente leitura, e igual no Windows.

    `os.kill(pid, 0)` no Windows mataria o processo (a stdlib mapeia qualquer sinal
    para TerminateProcess), então lá a pergunta vai pela API do sistema.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Um PID de outro dono nunca é o nosso servidor; quem confirma é o ping.
        return False
    except OSError:
        return False
    return True


def _reap(pid):
    """Colhe o filho já encerrado: um zumbi ainda responde a `kill(pid, 0)`."""
    if os.name == "nt":
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


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


def _ping(port, session, timeout=1.0):
    """O servidor desta porta é o da nossa sessão? Só uma leitura, nada é gravado.

    Sem isso, um PID reaproveitado pelo sistema apareceria como "rodando" — e um
    `--stop` mataria um processo inocente.
    """
    if not port or not session or not isinstance(session, str):
        return False
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}{PING_PATH}"
    if not _valid_ping_target(url):
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
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
        if not log.is_file() or log.stat().st_size == 0:
            return
        with open(log, "rb") as stream:
            if log.stat().st_size > LOG_KEEP_BYTES:
                stream.seek(-LOG_KEEP_BYTES, os.SEEK_END)
            kept = stream.read()
        rotated = log.with_name(LOG_ROTATE_FILE)
        rotated.write_bytes(kept)
        try:
            os.chmod(rotated, 0o600)
        except OSError:
            pass
    except OSError:
        return


# Sufixos que marcam uma variável como segredo: nenhuma delas tem por que alcançar
# o filho detached, que nunca fala com um provedor.
_SECRET_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")


def _child_environment(source_env, scripts):
    """Ambiente do servidor de fundo: uma cópia de `source_env` sem nenhuma variável
    com jeito de segredo (`*_API_KEY`, `*_TOKEN`, `*_SECRET` — cobre PEXELS_API_KEY,
    PIXABAY_API_KEY, YOUTUBE_API_KEY e qualquer outra do mesmo formato).

    `serve` nunca chama um provedor; não há razão para essas chaves chegarem a um
    processo solto cujo stdout/stderr vão parar num log dentro do projeto.
    """
    environment = {key: value for key, value in source_env.items() if not key.endswith(_SECRET_ENV_SUFFIXES)}
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(scripts) + (os.pathsep + existing if existing else "")
    environment["PYTHONIOENCODING"] = "utf-8"
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


def _payload_in(output: str) -> bool:
    """A linha JSON com a porta já apareceu no log do servidor de fundo?"""
    for line in output.splitlines():
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and candidate.get("port"):
            return True
    return False


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
    if not review.is_file():
        raise ValueError(f"Storyboard não encontrado em {review}. Gere-o antes com o comando `review`.")
    # Antes de subir outro servidor, a pergunta tem que ser respondida com folga: um
    # servidor vivo e ocupado classificado como morto viraria uma segunda instância.
    current = state(project, timeout=PING_TIMEOUT_ACT_S)
    if current["running"]:
        return {"background": True, "already_running": True, **current}
    log = directory / LOG_FILE
    _rotate_log(log)
    _write_private_text(log, "")
    scripts = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        str(scripts.parent / "scripts" / "gb.py"),
        "serve",
        "--project",
        str(Path(project).expanduser().resolve()),
        "--port",
        str(port),
    ]
    # O filho precisa achar `getbrolls` e falar UTF-8 mesmo num console legado:
    # nada disso pode depender do diretório de trabalho ou do locale da máquina.
    environment = _child_environment(os.environ, scripts)
    extra = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: o servidor sobrevive ao console.
        extra["creationflags"] = 0x00000008 | 0x00000200
    else:
        extra["start_new_session"] = True
    with open(log, "ab") as stream:
        process = subprocess.Popen(
            command,
            stdout=stream,
            stderr=stream,
            stdin=subprocess.DEVNULL,
            cwd=str(directory),
            env=environment,
            **extra,
        )
    payload = None
    # Máquina de CI fria gasta segundos só para subir o interpretador; a espera é
    # longa porque quem morre é detectado na hora, não por esgotar o relógio.
    deadline = time.monotonic() + BACKGROUND_TIMEOUT
    while time.monotonic() < deadline:
        output = log.read_text(encoding="utf-8", errors="replace")
        if process.poll() is not None and not _payload_in(output):
            raise ValueError(
                "O servidor do Storyboard não subiu. Rode `serve` sem `--background` "
                f"para ver o erro; a saída ficou em {log}.{_tail(output)}"
            )
        for line in output.splitlines():
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            if isinstance(candidate, dict) and candidate.get("port"):
                payload = candidate
                break
        if payload:
            break
        time.sleep(0.05)
    if not payload:
        process.terminate()
        raise ValueError(
            "O servidor do Storyboard não respondeu a tempo. Rode `serve` sem "
            f"`--background` para ver o que aconteceu (saída em {log})."
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
    if not _ours(data, timeout=PING_TIMEOUT_ACT_S):
        # O processo pode ter morrido ou o número ter sido reaproveitado por outro
        # programa: só limpamos o arquivo. Nunca matamos um PID que não se identificou.
        path.unlink(missing_ok=True)
        return {"stopped": False, "reason": "stale_pid", "pid": pid}
    # `_ours` só responde sim para um PID vivo que se identificou: aqui é inteiro.
    assert isinstance(pid, int)
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        _reap(pid)
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        _reap(pid)
        time.sleep(0.05)
    if _alive(pid) and os.name != "nt":
        import signal

        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        _reap(pid)
        deadline = time.monotonic() + 2
        while _alive(pid) and time.monotonic() < deadline:
            _reap(pid)
            time.sleep(0.05)
    path.unlink(missing_ok=True)
    return {"stopped": not _alive(pid), "reason": None, "pid": pid}
