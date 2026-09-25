"""Objeto entregue ao `register(api)` do plugin: o único caminho de entrada no registro."""

import contextlib
import contextvars
import json
import logging
import os
import re
import stat
from pathlib import Path, PurePath
from urllib.parse import urlsplit

from .. import http as core_http
from .. import logs
from ..http import ProviderError, get_json, public_url
from ..models import candidate as core_candidate
from ..rules import home_dir
from . import guard
from .contracts import CommandSpec

_log = logs.get("sdk")


class ApiError(ValueError):
    """Recusa escrita pelo próprio `PluginApi` (uso errado da API): o texto é do core,
    então chega à pessoa mesmo quando o plugin deixa a exceção subir."""


# Nome de arquivo que a rota pode pedir dentro da pasta de trabalho: sem barra,
# sem `..`, sem começar por ponto (nada de arquivo escondido nem caminho).
FILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

# Extensão simples (ponto + 1-8 letras/dígitos minúsculos) usada para nomear o arquivo
# copiado por `api.local_file` como `local<sufixo>`; qualquer outra coisa vira `.bin`.
SUFFIX_RE = re.compile(r"\.[a-z0-9]{1,8}")

# Nomes reservados do Windows (case-insensitive): mesmo num projeto rodando em
# Linux/macOS, o arquivo pode acabar sincronizado ou aberto numa máquina Windows,
# onde "CON.mp4"/"con"/"LPT1.txt" não são arquivos normais. `stem` = parte antes
# do primeiro ponto.
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{n}" for n in range(1, 10)} | {f"LPT{n}" for n in range(1, 10)}
)

# Nome de header HTTP (RFC 7230 token): letras, dígitos e os símbolos abaixo, sem
# espaço nem dois-pontos — o que `http.client.putheader` aceita como nome.
HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")

# Nomes de transporte que um plugin nunca escolhe: deixar passar Host abriria domain
# fronting por fora de permissions.network (a conexão TLS vai para o host validado,
# mas o servidor de origem roteia pelo Host que quiser); os outros mexem em como o
# corpo/keep-alive da conexão é interpretado, o que também não é do plugin decidir.
_FORBIDDEN_HEADER_NAMES = frozenset({"host", "content-length", "transfer-encoding", "connection"})

# Faixas de caractere proibidas num valor de header HTTP: control chars (inclui
# `\r`/`\n`/NUL) e DEL, mais tudo fora de latin-1.
_HEADER_CONTROL_MAX = 0x1F
_HEADER_DEL = 0x7F
_HEADER_LATIN1_MAX = 0xFF


def _bad_header_value_char(value):
    """`True` quando `value` tem um caractere que não pode ir num header HTTP.

    Control chars (`0x00`-`0x1f`, incluindo `\\r`/`\\n`/NUL, e `0x7f`) ou fora de
    latin-1 (`>0xff`) — checado caractere a caractere, nunca por
    `value.encode("latin-1")` dentro de um `try/except UnicodeEncodeError`: o
    `UnicodeEncodeError` do stdlib inclui o próprio valor no seu `repr`, e um
    `except` que o captura deixa isso em `exc.__context__` mesmo quando a exceção
    nova é levantada com `from None` (que só limpa `__cause__`/`__suppress_context__`).
    """
    return any(
        ord(char) <= _HEADER_CONTROL_MAX or ord(char) == _HEADER_DEL or ord(char) > _HEADER_LATIN1_MAX for char in value
    )


def _bad_file_name(name):
    """`True` quando `name` não serve como nome de arquivo dentro do workdir da rota."""
    if not isinstance(name, str) or not FILE_NAME_RE.fullmatch(name) or ".." in name or name.endswith("."):
        return True
    stem = name.split(".", 1)[0]
    return stem.upper() in _RESERVED_STEMS


# (id do plugin, pasta de trabalho) da rota em execução. Só o core liga isto, em
# volta de `Route.prepare`; fora dali `api.download`/`api.local_file` recusam.
_ACTIVE_ROUTE: contextvars.ContextVar[tuple[str, Path] | None] = contextvars.ContextVar(
    "getbrolls_active_route", default=None
)


@contextlib.contextmanager
def route_scope(plugin_id, workdir):
    """Liga `api.download`/`api.local_file` do plugin `plugin_id` à pasta `workdir`."""
    token = _ACTIVE_ROUTE.set((plugin_id, Path(workdir).resolve()))
    try:
        yield
    finally:
        _ACTIVE_ROUTE.reset(token)


class PluginApi:
    def __init__(self, manifest, registry):
        self.plugin_id = manifest["id"]
        self._manifest = manifest
        self._registry = registry
        self._registered = {"providers": set(), "presets": set(), "routes": set(), "commands": set()}
        guard.remember_env(self.plugin_id, manifest["permissions"]["env"])

    def _own(self, kind, name):
        if name not in self._manifest["contributes"][kind]:
            raise ApiError(f"Plugin {self.plugin_id}: {kind[:-1]} {name!r} não está declarado em contributes.{kind}.")
        if name != self.plugin_id and not str(name).startswith(self.plugin_id + "_"):
            raise ApiError(
                f"Plugin {self.plugin_id}: nomes têm que ser {self.plugin_id} ou começar por {self.plugin_id}_."
            )

    def provider(self, provider):
        name = getattr(provider, "name", None)
        self._own("providers", name)
        self._registry.add_provider(provider, owner=self.plugin_id)
        self._registered["providers"].add(name)

    def preset(self, name, url, text):
        self._own("presets", name)
        self._registry.add_preset(name, url, text, owner=self.plugin_id)
        self._registered["presets"].add(name)

    def route(self, route):
        name = getattr(route, "name", None)
        self._own("routes", name)
        self._registry.add_route(route, owner=self.plugin_id)
        self._registered["routes"].add(name)

    def command(self, name, handler, help):  # noqa: A002 - `help` é o nome do campo no contrato público (CommandSpec)
        # Comando não segue a regra de prefixo: `gb x <plugin> <comando>` já dá o espaço de nomes.
        if name not in self._manifest["contributes"]["commands"]:
            raise ApiError(f"Plugin {self.plugin_id}: comando {name!r} não está declarado em contributes.commands.")
        self._registry.add_command(CommandSpec(name, help, handler), owner=self.plugin_id)
        self._registered["commands"].add(name)

    def candidate(self, provider, source_id, title, source_url=None):
        if provider not in self._manifest["contributes"]["providers"]:
            raise ApiError(f"Plugin {self.plugin_id}: candidato de fonte não declarada {provider!r}.")
        return core_candidate(provider, str(source_id), title, public_url(source_url, strict=True))

    def env(self, key):
        if key not in self._manifest["permissions"]["env"]:
            raise ApiError(f"Plugin {self.plugin_id}: variável {key} não está em permissions.env.")
        return os.environ.get(key)

    def _check_host(self, url):
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError as exc:
            # `urlsplit` pode levantar em cima de uma URL malformada (ex.: IPv6 sem
            # fechar colchete) em vez de só devolver host vazio; mesmo assim nunca
            # ecoa a URL crua — pode carregar token/query sensível.
            logs.event(_log, logging.WARNING, "plugin_request_refused", plugin=self.plugin_id, host="-")
            raise ProviderError(f"Plugin {self.plugin_id}: URL inválida ({type(exc).__name__}).") from exc
        if host not in self._manifest["permissions"]["network"]:
            logs.event(_log, logging.WARNING, "plugin_request_refused", plugin=self.plugin_id, host=host or "-")
            # `host or "-"` nos dois lugares: nunca a URL crua (pode carregar
            # token/query sensível), só o host — ou "-" quando nem host tem.
            raise ProviderError(f"Plugin {self.plugin_id}: host {host or '-'} não está em permissions.network.")

    def _validate_headers(self, headers):
        """Nomes/valores de header de um plugin, antes de repassar ao transporte.

        Usado por `get_json` e `download`: a mensagem de erro nunca ecoa o valor do
        header (só o nome, quando o nome em si já foi validado como token — ver abaixo
        — e o tipo do problema).
        """
        if headers is None:
            return {}
        if not isinstance(headers, dict):
            raise ProviderError(f"Plugin {self.plugin_id}: headers tem que ser um dict de texto para texto.")
        cleaned = {}
        for position, (name, value) in enumerate(headers.items(), start=1):
            if not isinstance(name, str) or not isinstance(value, str):
                raise ProviderError(f"Plugin {self.plugin_id}: headers tem que ser um dict de texto para texto.")
            if not HEADER_NAME_RE.fullmatch(name):
                # `name` nunca aparece na mensagem: antes desta checagem ele pode
                # carregar o cabeçalho inteiro contrabandeado ali dentro — ex.:
                # {"Authorization: Bearer <segredo>": ""} tem nome inválido (tem ":" e
                # espaço), mas ecoar `name!r}` vazaria o segredo na mensagem/traceback.
                # Só a posição no dict é segura de dizer.
                raise ProviderError(f"Plugin {self.plugin_id}: nome de cabeçalho inválido (posição {position}).")
            if name.lower() in _FORBIDDEN_HEADER_NAMES:
                # `name` já passou no `fullmatch` acima (só charset de token, sem
                # espaço/":"/CR/LF), então ecoá-lo aqui é seguro.
                raise ProviderError(
                    f"Plugin {self.plugin_id}: header {name!r} é reservado ao transporte e não pode ser definido."
                )
            if _bad_header_value_char(value):
                # Mesma garantia: `name` é seguro (token já validado); `value` nunca
                # entra na mensagem. Checagem por caractere, sem try/except: um
                # `UnicodeEncodeError` capturado deixaria o valor cru no `repr` de
                # `exc.__context__` mesmo com `from None` (`from None` só limpa
                # `__cause__`/`__suppress_context__`, não `__context__`).
                raise ProviderError(f"Plugin {self.plugin_id}: valor do header {name!r} contém caractere inválido.")
            cleaned[name] = value
        return cleaned

    def get_json(self, url, params=None, headers=None, cache_ttl=0, keep_signed=False):  # noqa: ARG002 - `cache_ttl` fica na assinatura pública por compatibilidade; plugin nunca grava cache (ver abaixo)
        self._check_host(url)
        headers = self._validate_headers(headers)
        # Resposta de plugin NUNCA vai para o cache em disco (RT-09): ela pode trazer
        # URL assinada ou campo secreto que a limpeza não reconhece, e o cache é
        # compartilhado por todo o processo. `cache_ttl` é aceito e ignorado.
        # `quiet_errors=True` always: a plugin's error body is never assumed safe to
        # echo, unlike a built-in provider's (which never sets this and keeps its
        # exact previous message — "sem plugins, saída idêntica"). `strict_scrub`
        # casa esquema sem caixa, URL no meio do texto e chave com nome de segredo.
        return get_json(
            url, params, headers, cache_ttl=0, keep_signed=keep_signed, quiet_errors=True, strict_scrub=True
        )

    def _workdir(self, operation):
        active = _ACTIVE_ROUTE.get()
        if active is None or active[0] != self.plugin_id:
            raise ProviderError(f"Plugin {self.plugin_id}: api.{operation} só funciona dentro de Route.prepare.")
        return active[1]

    def download(self, url, name, headers=None):
        """Baixa `url` (https, host em permissions.network) para `workdir/name`.

        Aceita URL assinada e headers (ex.: Authorization); nenhum dos dois vai para
        log ou mensagem de erro. O teto é o mesmo do core (`http.DOWNLOAD_MAX_BYTES`).
        """
        workdir = self._workdir("download")
        if _bad_file_name(name):
            raise ProviderError(
                f"Plugin {self.plugin_id}: nome de arquivo inválido; use letras, números, '.', '_' ou '-', sem pasta, "
                "sem terminar em '.' e sem ser um nome reservado do Windows (CON, PRN, AUX, NUL, COM1-9, LPT1-9)."
            )
        headers = self._validate_headers(headers)
        self._check_host(url)
        target = workdir / name
        core_http.download(url, target, headers=headers, allow_signed=True)
        return target

    def _roots(self):
        """Raízes de `permissions.paths` que valem NESTE sistema, resolvidas.

        O manifesto aceita raiz POSIX (`/Volumes/...`) e Windows (`C:\\...`) em qualquer
        plataforma — ele viaja entre máquinas —; aqui só entra a que é absoluta no
        sistema atual (uma `C:\\acervo` lida no macOS seria um caminho relativo à pasta
        corrente, então é ignorada)."""
        roots = []
        for raw in self._manifest["permissions"]["paths"]:
            path = Path(raw).expanduser()
            if path.is_absolute():
                roots.append(path.resolve())
        return roots

    def _refuse(self, text):
        return ProviderError(f"Plugin {self.plugin_id}: {text}")

    def _local_source(self, path):
        """(caminho resolvido, nome mostrável) de `path` dentro de uma raiz; tudo que
        pode falhar aqui (NUL, tipo errado, raiz que não resolve) vira `ProviderError`."""
        try:
            roots = self._roots()
        except (OSError, RuntimeError, ValueError) as exc:
            raise self._refuse(f"permissions.paths não pôde ser resolvido ({type(exc).__name__}).") from None
        if not roots:
            raise self._refuse("permissions.paths está vazio; declare a pasta no manifesto para usar api.local_file.")
        try:
            raw = os.fspath(path)
            if type(raw) is not str:
                raise TypeError
            shown = PurePath(raw).name or "arquivo"
            source = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise self._refuse(f"arquivo local não encontrado ({type(exc).__name__}).") from None
        shown = guard.plain_line(shown, limit=120)
        if not any(source.is_relative_to(root) for root in roots):
            logs.event(_log, logging.WARNING, "plugin_path_refused", plugin=self.plugin_id)
            raise self._refuse(f"{shown} está fora de permissions.paths.")
        return source, shown

    def _copy_bounded(self, source_fd, target, too_big, cap):
        """Copia do descritor já conferido para `target` (criado exclusivo, sem seguir
        link), com teto nos bytes de fato lidos; apaga o destino parcial se falhar."""
        out_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            target_fd = os.open(target, out_flags, 0o600)
        except FileExistsError:
            raise self._refuse("api.local_file já trouxe um arquivo nesta rota.") from None
        except OSError as exc:
            raise self._refuse(f"não consegui criar o arquivo de trabalho ({type(exc).__name__}).") from None
        copied = 0
        try:
            with os.fdopen(target_fd, "wb") as out:
                while chunk := os.read(source_fd, 1024 * 1024):
                    copied += len(chunk)
                    if copied > cap:
                        raise too_big
                    out.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    def local_file(self, path):
        """Copia um arquivo de dentro de `permissions.paths` para a pasta de trabalho.

        O caminho é resolvido (links simbólicos seguidos) antes de conferir a raiz:
        um link dentro da pasta apontando para fora é recusado. Depois, o arquivo é
        aberto com `O_NOFOLLOW` (um link trocado ali entre a conferência e a abertura
        não é seguido) e `O_NONBLOCK` (uma FIFO não trava), conferido pelo próprio
        descritor (`fstat`: arquivo regular, dentro do teto) e copiado dele com teto
        nos bytes de fato lidos — o arquivo crescer durante a cópia não fura o teto.
        O destino é criado com `O_CREAT|O_EXCL|O_NOFOLLOW`: nunca segue um link
        plantado no workdir. Sempre cópia, nunca hardlink — o core ajusta permissão e
        move o arquivo de trabalho, e isso não pode respingar no original da pessoa.
        Mensagens de recusa nomeiam o arquivo que o plugin pediu, nunca o alvo resolvido.

        No Windows, onde `os.O_NOFOLLOW` não existe, vale a resolução + conferência de
        raiz feitas antes (link simbólico lá exige privilégio de administrador);
        `O_BINARY` garante cópia byte a byte.
        """
        workdir = self._workdir("local_file")
        cap = core_http.DOWNLOAD_MAX_BYTES
        source, shown = self._local_source(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        try:
            source_fd = os.open(source, flags)
        except OSError as exc:
            raise self._refuse(f"{shown} não pôde ser aberto ({type(exc).__name__}).") from None
        try:
            info = os.fstat(source_fd)
            if not stat.S_ISREG(info.st_mode):
                raise self._refuse(f"{shown} não é um arquivo.")
            too_big = self._refuse(f"{shown} passa do teto de {cap // (1024 * 1024)} MB para arquivo de trabalho.")
            if info.st_size > cap:
                raise too_big
            suffix = source.suffix.lower()
            target = workdir / ("local" + (suffix if SUFFIX_RE.fullmatch(suffix) else ".bin"))
            self._copy_bounded(source_fd, target, too_big, cap)
        finally:
            os.close(source_fd)
        return target

    @property
    def data_dir(self):
        """`$GB_HOME/plugin-data/<id>/` (0700): estado e cache do plugin, fora da pasta
        do plugin — escrever ali não muda o hash do enable.

        Recusa um link simbólico plantado em `plugin-data/<id>` (ou na própria pasta
        `plugin-data`, que o `<id>` fica dentro dela): seguir o link no `mkdir`/`chmod`
        aplicaria 0700 numa pasta de fora escolhida por quem plantou o link, não pela
        pessoa — mesma lógica de `serve.save_review`.
        """
        where = f"plugin-data/{self.plugin_id}"
        root = home_dir() / "plugin-data"
        folder = root / self.plugin_id
        if root.is_symlink() or folder.is_symlink():
            raise ApiError(
                f"Plugin {self.plugin_id}: {where} é um link simbólico; apague esse link antes de usar o plugin."
            )
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
        return folder

    def config(self):
        """`settings.json` de `data_dir` como dict; sem arquivo, `{}`."""
        path = self.data_dir / "settings.json"
        where = f"plugin-data/{self.plugin_id}/settings.json"
        if not path.exists():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ApiError(f"Plugin {self.plugin_id}: {where} não é JSON válido em UTF-8.") from exc
        except OSError as exc:
            # Ex.: `settings.json` é uma pasta, ou o arquivo não pode ser lido
            # (permissão) — nunca ecoa o caminho absoluto, só o tipo do erro.
            raise ApiError(f"Plugin {self.plugin_id}: {where} não pôde ser lido ({type(exc).__name__}).") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"Plugin {self.plugin_id}: {where} não é JSON válido em UTF-8.") from exc
        if not isinstance(data, dict):
            raise ApiError(f"Plugin {self.plugin_id}: {where} tem que ser um objeto JSON.")
        guard.remember_config(self.plugin_id, data)
        return data

    def finish(self):
        for kind, names in self._registered.items():
            missing = set(self._manifest["contributes"][kind]) - names
            if missing:
                raise ApiError(
                    f"Plugin {self.plugin_id}: declarado em contributes.{kind} e não registrado: {', '.join(sorted(missing))}."
                )
        for name in sorted(self._registered["providers"]):
            route = self._registry.provider(name).capabilities.route
            if route is not None and route not in self._registered["routes"]:
                raise ApiError(
                    f"Plugin {self.plugin_id}: {name} aponta capabilities.route={route!r}, "
                    "que não é uma rota registrada por este plugin."
                )
