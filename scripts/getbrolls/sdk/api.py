"""Objeto entregue ao `register(api)` do plugin: o único caminho de entrada no registro."""

import json
import logging
import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from .. import http as core_http
from .. import logs
from ..http import ProviderError, get_json, public_url
from ..models import candidate as core_candidate
from ..rules import home_dir
from . import guard, safe_copy
from .contracts import CommandContext, CommandSpec, Exporter, ExporterSpec, Provider, Resolver, ResolverSpec, Route
from .errors import ApiError
from .files import bad_file_name, checked_roots

if TYPE_CHECKING:
    from .registry import Registry

_log = logs.get("sdk")

# Liga `api.download`/`api.local_file` de um plugin à pasta da rota em execução. Mora no
# `guard`, que o usa em `route_call`; continua acessível aqui como sempre foi.
route_scope = guard.route_scope

# Extensão simples (ponto + 1-8 letras/dígitos minúsculos) usada para nomear o arquivo
# copiado por `api.local_file` como `local<sufixo>`; qualquer outra coisa vira `.bin`.
SUFFIX_RE = re.compile(r"\.[a-z0-9]{1,8}")

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


def ignored_paths(manifest):
    """Entradas de `permissions.paths` que, neste sistema, apontam para a raiz de um
    disco, um ponto de montagem, a pasta pessoal ou uma pasta acima dela — ignoradas
    por `api.local_file`. Só lê o manifesto e o disco; não roda código do plugin."""
    try:
        return checked_roots(manifest["permissions"]["paths"])[1]
    except (OSError, RuntimeError, ValueError):
        return []


class PluginApi:
    """O que o `register(api)` do plugin recebe.

    Registra só o que o manifesto declarou, com o prefixo do id, e dá acesso a rede,
    variáveis e arquivos dentro das permissões do manifesto.

    Attributes:
        plugin_id: id do plugin, como no manifesto.
    """

    def __init__(self, manifest: dict, registry: "Registry") -> None:
        """Prepara a API do plugin `manifest["id"]` sobre `registry`.

        Args:
            manifest: manifesto já validado por `read_manifest`.
            registry: registro onde o plugin vai registrar o que declarou.
        """
        self.plugin_id = manifest["id"]
        self._manifest = manifest
        self._registry = registry
        self._registered = {
            "providers": set(),
            "presets": set(),
            "routes": set(),
            "commands": set(),
            "exporters": set(),
            "resolvers": set(),
        }
        guard.remember_env(self.plugin_id, manifest["permissions"]["env"])

    def _own(self, kind, name):
        if name not in self._manifest["contributes"][kind]:
            raise ApiError(f"Plugin {self.plugin_id}: {kind[:-1]} {name!r} não está declarado em contributes.{kind}.")
        if name != self.plugin_id and not str(name).startswith(self.plugin_id + "_"):
            raise ApiError(
                f"Plugin {self.plugin_id}: nomes têm que ser {self.plugin_id} ou começar por {self.plugin_id}_."
            )

    def provider(self, provider: Provider) -> None:
        """Registra uma fonte declarada em `contributes.providers`.

        Args:
            provider: objeto com `name`, `capabilities` e os métodos de `Provider`.

        Raises:
            ApiError: o nome não está em `contributes.providers` ou não é o id do plugin
                nem começa por `<id>_`.
            RegistryError: o registro recusou a fonte (nome ou host já tomado,
                capabilities fora do contrato).
        """
        name = getattr(provider, "name", None)
        self._own("providers", name)
        self._registry.add_provider(provider, owner=self.plugin_id)
        self._registered["providers"].add(name)

    def preset(self, name: str, url: str, text: str) -> None:
        """Registra um preset de condições declarado em `contributes.presets`.

        Args:
            name: nome do preset (o id do plugin ou `<id>_...`).
            url: página da fonte onde a pessoa confere as condições.
            text: condições; tem que terminar em `verifique a página da fonte: <url>`.

        Raises:
            ApiError: o nome não está declarado ou não tem o prefixo do plugin.
            RegistryError: o registro recusou o preset (nome tomado, texto fora do formato).
        """
        self._own("presets", name)
        self._registry.add_preset(name, url, text, owner=self.plugin_id)
        self._registered["presets"].add(name)

    def route(self, route: Route) -> None:
        """Registra uma rota declarada em `contributes.routes`.

        Args:
            route: objeto com `name`, `stage` (`"preview"` ou `"fetch"`) e `prepare(item, workdir)`.

        Raises:
            ApiError: o nome não está declarado ou não tem o prefixo do plugin.
            RegistryError: o registro recusou a rota (nome tomado, estágio inválido, sem `prepare`).
        """
        name = getattr(route, "name", None)
        self._own("routes", name)
        self._registry.add_route(route, owner=self.plugin_id)
        self._registered["routes"].add(name)

    # `help` é o nome do campo no contrato público (CommandSpec) e a keyword desta API.
    def command(
        self,
        name: str,
        handler: Callable[[dict, CommandContext], dict],
        help: str,  # noqa: A002  # pylint: disable=redefined-builtin  # keyword pública da API
    ) -> None:
        """Registra um comando declarado em `contributes.commands`.

        Args:
            name: nome do comando, chamado como `x <plugin> <comando>`.
            handler: `handler(args, ctx) -> dict`, com `args` vindos de `--arg chave=valor`.
            help: frase de 1 a 200 caracteres que `x --list` mostra.

        Raises:
            ApiError: o nome não está em `contributes.commands`.
            RegistryError: o registro recusou o comando (nome inválido ou tomado, help vazio).
        """
        # Comando não segue a regra de prefixo: `gb x <plugin> <comando>` já dá o espaço de nomes.
        if name not in self._manifest["contributes"]["commands"]:
            raise ApiError(f"Plugin {self.plugin_id}: comando {name!r} não está declarado em contributes.commands.")
        self._registry.add_command(CommandSpec(name, help, handler), owner=self.plugin_id)
        self._registered["commands"].add(name)

    def exporter(self, name: str, export: Exporter, description: str) -> None:
        """Registra um exportador (experimental).

        Args:
            name: nome do exportador (o id do plugin ou `<id>_...`).
            export: `export(plan, options) -> ExportResult`.
            description: frase de 1 a 200 caracteres.

        Raises:
            ApiError: o nome não está declarado ou não tem o prefixo do plugin.
            RegistryError: o registro recusou o exportador.
        """
        self._own("exporters", name)
        self._registry.add_exporter(ExporterSpec(name, description, export), owner=self.plugin_id)
        self._registered["exporters"].add(name)

    def resolver(self, name: str, resolve: Resolver, kinds: Sequence[str]) -> None:
        """Registra um resolvedor (experimental).

        As raízes são as de `permissions.paths` que valem neste sistema, conferidas
        como em `api.local_file` e guardadas no registro junto com o resolvedor.

        Args:
            name: nome do resolvedor (o id do plugin ou `<id>_...`).
            resolve: `resolve(kind, name) -> ResolverHit | None`.
            kinds: lista não vazia, sem repetição, de `RESOLVER_KINDS`.

        Raises:
            ApiError: o nome não está declarado, não tem o prefixo do plugin, ou
                `permissions.paths` não pôde ser resolvido.
            RegistryError: o registro recusou o resolvedor.
        """
        self._own("resolvers", name)
        try:
            roots = tuple(str(root) for root in self._roots())
        except (OSError, RuntimeError, ValueError) as exc:
            raise ApiError(
                f"Plugin {self.plugin_id}: permissions.paths não pôde ser resolvido ({type(exc).__name__})."
            ) from None
        spec = ResolverSpec(name, cast("tuple[str, ...]", kinds), resolve)
        self._registry.add_resolver(spec, owner=self.plugin_id, roots=roots)
        self._registered["resolvers"].add(name)

    def candidate(self, provider: str, source_id: object, title: str, source_url: str | None = None) -> dict:
        """Candidato vazio da fonte `provider` (do próprio plugin), no formato do core.

        Args:
            provider: nome de uma fonte em `contributes.providers`.
            source_id: id do item na fonte (vira texto).
            title: título do item.
            source_url: página pública do item; URL com credencial ou fora de https vira `None`.

        Returns:
            O candidato, com `id` `"<provider>:<source_id>"`, pronto para `search`/`resolve` devolverem.

        Raises:
            ApiError: `provider` não está em `contributes.providers`.
        """
        if provider not in self._manifest["contributes"]["providers"]:
            raise ApiError(f"Plugin {self.plugin_id}: candidato de fonte não declarada {provider!r}.")
        return core_candidate(provider, str(source_id), title, public_url(source_url, strict=True))

    def env(self, key: str) -> str | None:
        """Valor da variável `key`, que tem que estar em `permissions.env`.

        Mesma precedência das variáveis do core: o ambiente real do processo vence
        quando tem valor; senão, o que o `.env` guardou para ESTE plugin (nunca
        exportado ao ambiente).

        Args:
            key: nome da variável.

        Returns:
            O valor, ou `None` quando não está definido em lugar nenhum.

        Raises:
            ApiError: `key` não está em `permissions.env`.
        """
        if key not in self._manifest["permissions"]["env"]:
            raise ApiError(f"Plugin {self.plugin_id}: variável {key} não está em permissions.env.")
        from .. import config

        real = os.environ.get(key)
        return real or config.plugin_env_value(self.plugin_id, key)

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

    # `cache_ttl` fica na assinatura pública por compatibilidade; plugin nunca grava cache (ver abaixo).
    def get_json(
        self,
        url: str,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
        cache_ttl: int = 0,  # noqa: ARG002  # pylint: disable=unused-argument  # keyword pública, aceita e ignorada
        keep_signed: bool = False,
    ) -> Any:
        """GET de JSON num host de `permissions.network`; a resposta nunca vai para o cache em disco.

        Args:
            url: URL https cujo host está em `permissions.network`.
            params: parâmetros de query.
            headers: cabeçalhos de texto (ex.: `Authorization`); nunca vão para log nem mensagem.
            cache_ttl: aceito por compatibilidade e ignorado.
            keep_signed: mantém na resposta a URL assinada que a limpeza trocaria por `None`.

        Returns:
            O JSON da resposta, já com a limpeza rígida de URLs e segredos.

        Raises:
            ProviderError: host fora de `permissions.network`, URL ou cabeçalho inválido,
                ou falha da requisição (sem o corpo do erro na mensagem).
        """
        self._check_host(url)
        headers = self._validate_headers(headers)
        # Resposta de plugin NUNCA vai para o cache em disco: ela pode trazer
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
        active = guard.active_route()
        if active is None or active[0] != self.plugin_id:
            raise ProviderError(f"Plugin {self.plugin_id}: api.{operation} só funciona dentro de Route.prepare.")
        return active[1]

    def download(self, url: str, name: str, headers: dict[str, str] | None = None) -> Path:
        """Baixa `url` (https, host em permissions.network) para `workdir/name`.

        Só funciona dentro de `Route.prepare`. Aceita URL assinada e headers (ex.:
        Authorization); nenhum dos dois vai para log ou mensagem de erro. O teto é o
        mesmo do core (`http.DOWNLOAD_MAX_BYTES`).

        Args:
            url: URL https cujo host está em `permissions.network`.
            name: nome de arquivo simples (`[A-Za-z0-9._-]`, sem pasta nem nome reservado do Windows).
            headers: cabeçalhos de texto da requisição.

        Returns:
            O caminho do arquivo baixado dentro da pasta de trabalho da rota.

        Raises:
            ProviderError: fora de uma rota, nome ou cabeçalho inválido, host fora de
                `permissions.network`, arquivo acima do teto ou falha do download.
        """
        workdir = self._workdir("download")
        if bad_file_name(name):
            raise ProviderError(
                f"Plugin {self.plugin_id}: nome de arquivo inválido; use letras, números, '.', '_' ou '-', sem pasta, "
                "sem terminar em '.' e sem ser um nome reservado do Windows (CON, PRN, AUX, NUL, COM0-9, LPT0-9)."
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
        # O manifesto passou na checagem de texto, mas a pasta de verdade pode ser a raiz
        # do disco, um ponto de montagem, a pasta pessoal ou uma pasta acima dela (link,
        # `/Volumes/Macintosh HD`, `/Users`, outra caixa do mesmo nome): ignorada.
        roots, ignored = checked_roots(self._manifest["permissions"]["paths"])
        for _raw in ignored:
            logs.event(_log, logging.WARNING, "plugin_path_refused", plugin=self.plugin_id, reason="too_broad")
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
        return source, shown, roots

    def local_file(self, path: str | os.PathLike[str]) -> Path:
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
        raiz feitas antes (link simbólico lá exige privilégio de administrador), e um
        link ou junction no caminho resolvido é recusado antes de abrir; `O_BINARY`
        garante cópia byte a byte. A abertura e a cópia são as de `sdk.safe_copy`.

        Args:
            path: caminho do arquivo, dentro de uma raiz de `permissions.paths`.

        Returns:
            O caminho da cópia na pasta de trabalho da rota (`local<extensão>`).

        Raises:
            ProviderError: fora de uma rota, `permissions.paths` vazio ou que não resolve,
                arquivo fora das raízes, que não existe, não é arquivo, passa do teto ou
                mudou enquanto era aberto.
        """
        workdir = self._workdir("local_file")
        cap = core_http.DOWNLOAD_MAX_BYTES
        source, shown, roots = self._local_source(path)
        too_big = f"{shown} passa do teto de {cap // (1024 * 1024)} MB para arquivo de trabalho."
        refusals = {
            safe_copy.NOT_REGULAR: f"{shown} não é um arquivo.",
            safe_copy.TOO_BIG: too_big,
            safe_copy.TARGET_EXISTS: "api.local_file já trouxe um arquivo nesta rota.",
            safe_copy.OUTSIDE: f"{shown} está fora de permissions.paths.",
            safe_copy.CHANGED: f"{shown} mudou enquanto era aberto; tente de novo.",
        }
        try:
            # Sem exigir um nome só no disco (`single_link=False`): a rota sempre aceitou
            # hardlink dentro da raiz, e a cópia nunca mexe no original. `open_under`
            # confere de novo, com o arquivo aberto, que o caminho segue dentro da raiz
            # (uma pasta do meio trocada por link depois da conferência é recusada).
            source_fd, info = safe_copy.open_under(source, roots, single_link=False)
        except safe_copy.UnsafeFileError as exc:
            if exc.reason == safe_copy.OUTSIDE:
                logs.event(_log, logging.WARNING, "plugin_path_refused", plugin=self.plugin_id)
            text = refusals.get(exc.reason) or f"{shown} não pôde ser aberto ({exc.type_name})."
            raise self._refuse(text) from None
        try:
            if info.st_size > cap:
                raise self._refuse(too_big)
            suffix = source.suffix.lower()
            target = workdir / ("local" + (suffix if SUFFIX_RE.fullmatch(suffix) else ".bin"))
            try:
                safe_copy.copy_from_fd(source_fd, target, cap)
            except safe_copy.UnsafeFileError as exc:
                text = refusals.get(exc.reason) or f"não consegui criar o arquivo de trabalho ({exc.type_name})."
                raise self._refuse(text) from None
        finally:
            os.close(source_fd)
        return target

    @property
    def data_dir(self) -> Path:
        """`$GB_HOME/plugin-data/<id>/` (0700): estado e cache do plugin, fora da pasta do plugin.

        Escrever ali não muda o hash do enable. Recusa um link simbólico plantado em
        `plugin-data/<id>` (ou na própria pasta `plugin-data`, que o `<id>` fica dentro
        dela): seguir o link no `mkdir`/`chmod` aplicaria 0700 numa pasta de fora
        escolhida por quem plantou o link, não pela pessoa — mesma lógica de
        `serve.save_review`.

        Returns:
            A pasta, criada se ainda não existia.

        Raises:
            ApiError: `plugin-data` ou `plugin-data/<id>` é um link simbólico.
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

    def config(self) -> dict:
        """`settings.json` de `data_dir` como dict; sem arquivo, `{}`.

        Os textos lidos passam a ser trocados por `[REDACTED]` nas mensagens do plugin.

        Returns:
            O conteúdo do arquivo, ou `{}` quando ele não existe.

        Raises:
            ApiError: o arquivo não pôde ser lido, não é JSON em UTF-8 ou não é um objeto.
        """
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

    def finish(self) -> None:
        """Confere, depois do `register`, que o plugin registrou tudo o que declarou.

        Também confere que cada `capabilities.route` aponta uma rota do próprio plugin.

        Raises:
            ApiError: algo declarado em `contributes` não foi registrado, ou uma fonte
                aponta uma rota que não é deste plugin.
        """
        for kind, names in self._registered.items():
            missing = set(self._manifest["contributes"].get(kind, [])) - names
            if missing:
                raise ApiError(
                    f"Plugin {self.plugin_id}: declarado em contributes.{kind} e não registrado: "
                    f"{', '.join(sorted(missing))}."
                )
        for name in sorted(self._registered["providers"]):
            # `name` só entra em `_registered` depois que `add_provider` o aceitou.
            route = cast("Provider", self._registry.provider(name)).capabilities.route
            if route is not None and route not in self._registered["routes"]:
                raise ApiError(
                    f"Plugin {self.plugin_id}: {name} aponta capabilities.route={route!r}, "
                    "que não é uma rota registrada por este plugin."
                )
