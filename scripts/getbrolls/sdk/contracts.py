"""Contratos públicos do SDK: o que uma extensão entrega ao registro."""

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .. import vocab
from ..http import ProviderError
from . import names
from .errors import ApiError

# Muda só em major do get-brolls. Plugin declara o mesmo número em `sdk_api`.
SDK_API = 1
# Dono dos built-ins no registro; nenhum plugin pode usar este id.
CORE = "core"
# Ids que nenhum plugin pode usar: o do core e os nomes que o get-brolls guarda para
# conceitos próprios (cliente, catálogo, direção, template e projeto), para que
# `[cliente:x]` ou `[direcao:x]` num roteiro nunca vire diretiva de um plugin.
RESERVED_IDS = (CORE, "cliente", "catalogo", "direcao", "template", "projeto")
# O padrão mora no módulo folha `names` (o roteiro o lê sem passar por aqui); o
# mesmo objeto continua acessível como `contracts.NAME_RE`.
NAME_RE = names.NAME_RE
MATCH_KINDS = vocab.MATCH_KINDS
MEDIA_KINDS = vocab.MEDIA_KINDS
# Áreas do projeto em que um comando de plugin pode gravar pela API do core, quando o pin
# aprovado as traz em `permissions.project_write`.
ANALYSIS_AREA = "analysis"
# "preview": a rota pode trazer mídia de trabalho para revisão. "fetch": trazer o
# arquivo consome licença ou cota, então só roda no `fetch`, depois de aprovação e permit.
ROUTE_STAGES = ("preview", "fetch")
# Tipos de mídia que um resolvedor de plugin pode achar. Nunca "aroll" (a gravação
# é da pessoa) nem "marca" (marca e beat são decididos só pelas raízes do core).
RESOLVER_KINDS = ("sfx", "musica")


class PluginError(ProviderError):
    """A única exceção de plugin cujo texto chega à pessoa: `raise PluginError("Configure X...")`.

    O core mostra `Plugin <id>: <mensagem>` — em uma linha, sem caractere de controle,
    com `redact()` aplicado, o valor de cada variável de `permissions.env` trocado por
    `[REDACTED]` e no máximo 300 caracteres. Use a própria classe (subclasse não
    conta) com um único argumento de texto. Qualquer outra exceção aparece só pelo tipo.

    Args:
        message: frase para a pessoa, dizendo o que fazer (ex.: qual variável configurar).
    """


@dataclass(frozen=True)
class ProviderCapabilities:  # pylint: disable=too-many-instance-attributes  # contrato público: um campo por capacidade
    """O que uma fonte sabe fazer: busca, URL, tipos de mídia, transporte, hosts e rota.

    Attributes:
        search: a fonte responde a `search`.
        resolve_url: a fonte transforma uma URL dela em candidato (`resolve`).
        media_kinds: tipos de mídia que ela devolve (`"video"`, `"image"`).
        match_kind: `"literal"` (o que a busca pediu) ou `"illustrative"` (ilustra o tema).
        env_key: variável de ambiente que a fonte precisa, ou `None`.
        transport: rótulo do transporte (`https`, `local`...), só para exibição.
        url_hosts: hosts cujas URLs `resolve` aceita; cada host tem um dono só.
        seek: rótulo do tipo de busca por tempo que a fonte oferece, só para exibição.
        download: o core pode baixar `media_url` direto; `False` numa fonte só de metadados.
        route: nome da rota (do mesmo plugin) que entrega o arquivo, ou `None`.
    """

    search: bool = False
    resolve_url: bool = False
    media_kinds: tuple[str, ...] = ("video",)
    match_kind: str = "literal"
    env_key: str | None = None
    transport: str = "https"
    url_hosts: tuple[str, ...] = ()
    seek: str = "unsupported"
    download: bool = True
    # Nome da rota (do mesmo plugin) que entrega o arquivo dos candidatos desta fonte.
    route: str | None = None


@dataclass(frozen=True)
class Preset:
    """Condições de uso conhecidas de uma fonte, com a página onde conferi-las.

    Attributes:
        name: nome do preset, usado em `permit --preset`.
        url: página da fonte com as condições.
        text: as condições; termina em `verifique a página da fonte: <url>`.
    """

    name: str
    url: str
    text: str


class Provider(Protocol):
    """Fonte de candidatos: busca, resolve uma URL e atualiza o arquivo de um candidato.

    Attributes:
        name: nome da fonte (o id do plugin ou `<id>_...`).
        capabilities: o que a fonte sabe fazer.
    """

    name: str
    capabilities: ProviderCapabilities

    def search(self, query: str, limit: int, media: str) -> list[dict]:
        """Busca candidatos para `query`.

        Args:
            query: o texto da busca.
            limit: quantos candidatos, no máximo.
            media: `"video"` ou `"image"`.

        Returns:
            Candidatos montados com `api.candidate` (o core reescreve o que não é da fonte).

        Raises:
            PluginError: com a frase do que a pessoa tem que fazer (ex.: configurar a chave).
        """
        ...

    def resolve(self, url: str) -> dict | None:
        """Transforma uma URL desta fonte em candidato.

        Args:
            url: URL de um host de `capabilities.url_hosts`.

        Returns:
            O candidato, ou `None` quando a URL não é de um item conhecido.

        Raises:
            PluginError: com a frase do que a pessoa tem que fazer.
        """
        ...

    def refresh(self, item: dict) -> dict:
        """Atualiza o endereço do arquivo de um candidato.

        Args:
            item: cópia do candidato guardado no projeto.

        Returns:
            O candidato com `media_url` atual (o core só aproveita esse campo).

        Raises:
            PluginError: com a frase do que a pessoa tem que fazer.
        """
        ...


@dataclass(frozen=True)
class RouteResult:
    """O arquivo que a rota trouxe para a pasta de trabalho, e a licença que ela registrou.

    Attributes:
        path: arquivo dentro da pasta de trabalho da rota.
        license: texto da licença (até 500 caracteres), ou `None`.
    """

    path: Path
    license: str | None = None


class Route(Protocol):  # pylint: disable=too-few-public-methods  # Protocol do contrato público
    """Traz o arquivo de um candidato para a pasta de trabalho, no estágio `stage`.

    Attributes:
        name: nome da rota (o id do plugin ou `<id>_...`).
        stage: `"preview"` (mídia de trabalho para revisão) ou `"fetch"` (só depois de
            aprovação e permit, quando trazer o arquivo consome licença ou cota).
    """

    name: str
    stage: str

    def prepare(self, item: dict, workdir: Path) -> RouteResult:
        """Grava o arquivo de `item` dentro de `workdir` e diz onde ficou.

        Args:
            item: cópia do candidato.
            workdir: pasta de trabalho desta rota; use `api.download`/`api.local_file`.

        Returns:
            O arquivo trazido e a licença registrada.

        Raises:
            PluginError: com a frase do que a pessoa tem que fazer.
        """
        ...


def _has_text_id(beat: object) -> bool:
    """Beat é objeto com `id` de texto: só esse pode estar num conjunto de ids aposentados."""
    return isinstance(beat, dict) and isinstance(beat.get("id"), str)


class AnalysisAccess:
    """`ctx.analysis`: os arquivos de `analysis/` do projeto, pela API do core.

    Ler é sempre permitido. Gravar exige `"analysis"` em `permissions.project_write` do
    pin aprovado no enable (não do manifesto em disco) e `--project`. O core valida cada
    documento, força `schema`, `media_id`, `created`, `time_unit` e `producer.tool` (o id
    do plugin), grava sem seguir link e só sob a trava `analysis/.lock`. `media.json` é
    do core e nunca passa por aqui. A permissão é uma declaração auditável, não um
    sandbox: o plugin roda no mesmo processo do get-brolls.

    Attributes:
        plugin_id: id do plugin dono do comando.
        writable: se o pin aprovado deu `project_write: ["analysis"]` ao plugin.
    """

    def __init__(self, plugin_id: str, project: Path | None, writable: bool) -> None:
        """Acesso de `plugin_id` a `analysis/` de `project`.

        Args:
            plugin_id: id do plugin dono do comando.
            project: pasta do projeto já resolvida, ou `None` sem `--project`.
            writable: se o pin aprovado permite gravar em `analysis/`.
        """
        self.plugin_id = plugin_id
        self.writable = writable
        self._root = project

    def _refuse(self, text: str) -> ApiError:
        return ApiError(f"Plugin {self.plugin_id}: {text}")

    def _project(self) -> Path:
        if self._root is None:
            raise self._refuse("ctx.analysis precisa de --project.")
        return self._root

    def _can_write(self) -> Path:
        project = self._project()
        if not self.writable:
            raise self._refuse(
                'gravar em analysis/ pede permissions.project_write com "analysis" no manifesto, aprovado '
                f"no enable (plugins --action enable --id {self.plugin_id})."
            )
        return project

    def media_id(self, rel: str) -> str:
        """O `media_id` da mídia `rel` (caminho relativo ao projeto, como `aroll/c01.mp4`).

        Com permissão de gravação, registra a mídia quando preciso (o sha256 inteiro só
        roda quando o arquivo mudou); sem ela, só acha uma mídia já registrada.

        Args:
            rel: caminho relativo ao projeto, com `/`.

        Returns:
            Os 16 hex do `media_id`.

        Raises:
            ApiError: sem `--project`, caminho inseguro, ou mídia não registrada (sem permissão).
        """
        from .. import analysis

        project = self._project()
        try:
            if self.writable:
                return analysis.ensure_media(project, rel)["media_id"]
            entry = analysis.lookup(project, rel)
        except ValueError as exc:
            raise self._refuse(str(exc)) from None
        if entry is None:
            raise self._refuse(f"{rel} não está registrada em analysis/; rode analysis --action register --path {rel}.")
        return entry["media_id"]

    def read(self, media_id: str, name: str) -> dict | None:
        """Cópia validada do componente `name` (`transcript`, `scenes`...) da mídia.

        Returns:
            O documento, ou `None` quando ainda não existe.

        Raises:
            ApiError: sem `--project`, ou arquivo fora do contrato.
        """
        from .. import analysis

        project = self._project()
        try:
            return analysis.read_component(project, media_id, name)
        except ValueError as exc:
            raise self._refuse(str(exc)) from None

    def write(
        self, media_id: str, name: str, doc: dict, *, model: str | None = None, version: str | None = None
    ) -> None:
        """Grava o componente `name` da mídia, com o plugin como `producer.tool`.

        Args:
            media_id: `media_id` de uma mídia já no índice (`media_id(rel)`).
            name: `transcript`, `scenes`, `silence`, `speakers` ou `visual`; nunca `media`.
            doc: o documento; `schema`, `media_id`, `created`, `time_unit` e `producer` são do core.
            model: modelo usado (vai para `producer.model`).
            version: versão do modelo ou do plugin (vai para `producer.version`).

        Raises:
            ApiError: sem permissão ou sem `--project`, mídia fora do índice, `name` inválido
                ou documento fora do contrato (com o código, como `MISSING_REASON`).
        """
        from .. import analysis

        project = self._can_write()
        if name == "media":
            raise self._refuse("media.json é do core; grave os outros componentes (transcript, scenes...).")
        producer = {"tool": self.plugin_id, "model": model, "version": version}
        try:
            analysis.write_component(project, media_id, name, doc, producer=producer)
        except ValueError as exc:
            raise self._refuse(str(exc)) from None

    def write_markers(self, markers: list[dict], *, model: str | None = None, version: str | None = None) -> None:
        """Troca os marcadores deste plugin em `analysis/markers.json`; os dos outros ficam.

        Args:
            markers: marcadores de `schemas/markers.schema.json`; `producer` é do core.
            model: modelo usado (vai para `producer.model`).
            version: versão do modelo ou do plugin (vai para `producer.version`).

        Raises:
            ApiError: sem permissão ou sem `--project`, mídia fora do índice ou marcador inválido.
        """
        from .. import analysis

        project = self._can_write()
        producer = {"tool": self.plugin_id, "model": model, "version": version}
        try:
            analysis.write_markers(project, producer, markers)
        except ValueError as exc:
            raise self._refuse(str(exc)) from None


def _approved_areas(permissions: dict | None) -> tuple[str, ...]:
    areas = permissions.get("project_write") if isinstance(permissions, dict) else None
    return tuple(area for area in areas if isinstance(area, str)) if isinstance(areas, list) else ()


class CommandContext:
    """O que um comando de plugin enxerga do projeto: cópias, e `analysis/` pela API do core.

    Attributes:
        plugin_id: id do plugin dono do comando.
        project: pasta do projeto (`--project`), ou `None`.
    """

    def __init__(self, plugin_id: str, project: Path | None, permissions: dict | None = None) -> None:
        """Contexto do comando de `plugin_id` sobre `project`.

        Args:
            plugin_id: id do plugin dono do comando.
            project: pasta do projeto já resolvida, ou `None` sem `--project`.
            permissions: as permissões aprovadas no pin do plugin (não as do manifesto em disco).
        """
        self.plugin_id = plugin_id
        self.project = project
        self._analysis = AnalysisAccess(plugin_id, project, ANALYSIS_AREA in _approved_areas(permissions))

    @property
    def analysis(self) -> AnalysisAccess:
        """Leitura (sempre) e gravação (com `project_write: ["analysis"]` aprovado) em `analysis/`."""
        return self._analysis

    def candidates(self) -> list[dict]:
        """Cópia dos candidatos do projeto.

        Returns:
            Os candidatos, ou lista vazia sem projeto ou sem `brolls/`.
        """
        if self.project is None or not (self.project / "brolls").is_dir():
            return []
        from ..ledger import Ledger

        return copy.deepcopy(Ledger(self.project, recover=False).data["items"])

    def brief(self) -> dict | None:
        """O JSON do BRIEF.md sem os beats aposentados pelo roteiro: os mesmos beats do `status`.

        Sai exatamente o que `retired_beat_ids()` lista (um predicado só); beat marcado
        `retired` com id fora do formato continua visível, nunca some calado.

        Returns:
            Cópia do brief, ou `None` sem projeto ou sem BRIEF.md.
        """
        if self.project is None:
            return None
        from ..brief_source import brief_path, load_brief

        if not brief_path(self.project).is_file():
            return None
        data = copy.deepcopy(load_brief(self.project))
        beats = data.get("beats") if isinstance(data, dict) else None
        retired = set(self.retired_beat_ids())
        if isinstance(beats, list) and retired:
            # Id que não é texto (lista, objeto, número) nunca é aposentado e nunca derruba a leitura.
            data["beats"] = [b for b in beats if not (_has_text_id(b) and b["id"] in retired)]
        return data

    def retired_beat_ids(self) -> list[str]:
        """Ids dos beats aposentados (`"retired": true`), para o plugin que precisa do histórico.

        Returns:
            Os ids, na ordem do BRIEF.md e sem repetição; vazio sem projeto.
        """
        if self.project is None:
            return []
        from ..brief_source import load_brief, retired_beat_ids

        retired = retired_beat_ids(self.project)
        if not retired:
            return []
        beats = load_brief(self.project).get("beats") or []
        ordered = [b["id"] for b in beats if _has_text_id(b) and b["id"] in retired]
        return list(dict.fromkeys(ordered))


@dataclass(frozen=True)
class CommandSpec:
    """Comando de plugin: nome, ajuda de uma linha e `handler(args, ctx) -> dict`.

    Attributes:
        name: nome do comando (`x <plugin> <comando>`).
        help: frase de 1 a 200 caracteres que `x --list` mostra.
        handler: recebe os `--arg chave=valor` e o `CommandContext`; devolve um objeto JSON.
    """

    name: str
    help: str
    handler: Callable[[dict, CommandContext], dict]


@dataclass(frozen=True)
class MediaRequest:
    """Mídia que o export precisa: o core resolve `media_id` e grava em `dest`.

    Attributes:
        media_id: id lógico da tabela de mídia do plano, nunca um caminho.
        dest: caminho POSIX relativo, dentro de `assets/`.
    """

    media_id: str  # id lógico da tabela de mídia do plano, nunca um caminho
    dest: str  # caminho POSIX relativo, dentro de "assets/"


@dataclass(frozen=True)
class ExportResult:
    """O que um exportador devolve: texto e pedidos de mídia; quem escreve é o core.

    Attributes:
        files: caminho relativo → texto UTF-8 de cada arquivo do export.
        media: pedidos de mídia que o core copia para `assets/`.
        notes: avisos de uma linha para a pessoa.
    """

    files: dict[str, str]  # caminho relativo -> texto UTF-8
    media: list[MediaRequest] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResolverHit:
    """Arquivo que um resolvedor achou dentro de `permissions.paths`.

    Attributes:
        path: caminho absoluto, dentro de `permissions.paths`.
        license: licença informada, só como texto: nunca vale como permit.
    """

    path: str | Path  # absoluto, dentro de permissions.paths
    license: str | None = None  # só informativa: nunca vale como permit


class Exporter(Protocol):  # pylint: disable=too-few-public-methods  # Protocol do contrato público
    """Função que transforma um plano em arquivos de texto e pedidos de mídia."""

    def __call__(self, plan: dict, options: dict) -> ExportResult:
        """Exporta uma cópia do plano.

        Args:
            plan: cópia do plano de export (`schemas/export_plan.schema.json`).
            options: cópia das opções do comando (`{"args": {...}}`).

        Returns:
            Os arquivos de texto e os pedidos de mídia do export.

        Raises:
            PluginError: com a frase do que a pessoa tem que fazer.
        """
        ...


class Resolver(Protocol):  # pylint: disable=too-few-public-methods  # Protocol do contrato público
    """Função que acha um arquivo de som ou música por nome dentro de `permissions.paths`."""

    def __call__(self, kind: str, name: str) -> ResolverHit | None:
        """Procura o arquivo `name` do tipo `kind`.

        Args:
            kind: um de `RESOLVER_KINDS` (`"sfx"`, `"musica"`).
            name: o nome pedido pelo plano.

        Returns:
            O arquivo achado, ou `None`.

        Raises:
            PluginError: com a frase do que a pessoa tem que fazer.
        """
        ...


@dataclass(frozen=True)
class ExporterSpec:
    """Exportador de plugin: nome, descrição de uma linha e `export(plan, options)`.

    Attributes:
        name: nome do exportador.
        description: frase de 1 a 200 caracteres.
        export: a função `Exporter`.
    """

    name: str
    description: str
    export: Callable[[dict, dict], ExportResult]


@dataclass(frozen=True)
class ResolverSpec:
    """Resolvedor de plugin: nome, tipos de mídia aceitos e `resolve(kind, name)`.

    Attributes:
        name: nome do resolvedor.
        kinds: tipos de `RESOLVER_KINDS` que ele atende.
        resolve: a função `Resolver`.
    """

    name: str
    kinds: tuple[str, ...]
    resolve: Callable[[str, str], ResolverHit | None]
