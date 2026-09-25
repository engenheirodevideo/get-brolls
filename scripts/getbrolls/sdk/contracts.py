"""Contratos públicos do SDK: o que uma extensão entrega ao registro."""

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..http import ProviderError

# Muda só em major do get-brolls. Plugin declara o mesmo número em `sdk_api`.
SDK_API = 1
# Dono dos built-ins no registro; nenhum plugin pode usar este id.
CORE = "core"
NAME_RE = re.compile(r"[a-z][a-z0-9_]{1,31}")
MATCH_KINDS = ("literal", "illustrative")
MEDIA_KINDS = ("video", "image")
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
    """


@dataclass(frozen=True)
class ProviderCapabilities:
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
    name: str
    url: str
    text: str


class Provider(Protocol):
    name: str
    capabilities: ProviderCapabilities

    def search(self, query: str, limit: int, media: str) -> list[dict]: ...

    def resolve(self, url: str) -> dict | None: ...

    def refresh(self, item: dict) -> dict: ...


@dataclass(frozen=True)
class RouteResult:
    """O arquivo que a rota trouxe para a pasta de trabalho, e a licença que ela registrou."""

    path: Path
    license: str | None = None


class Route(Protocol):
    name: str
    stage: str

    def prepare(self, item: dict, workdir: Path) -> RouteResult: ...


class CommandContext:
    """O que um comando de plugin enxerga do projeto: sempre cópias, nunca o ledger."""

    def __init__(self, plugin_id: str, project: Path | None):
        self.plugin_id = plugin_id
        self.project = project

    def candidates(self) -> list[dict]:
        if self.project is None or not (self.project / "brolls").is_dir():
            return []
        from ..ledger import Ledger

        return copy.deepcopy(Ledger(self.project, recover=False).data["items"])

    def brief(self) -> dict | None:
        """O JSON do BRIEF.md sem os beats aposentados pelo roteiro: os mesmos beats do `status`.

        Sai exatamente o que `retired_beat_ids()` lista (um predicado só); beat marcado
        `retired` com id fora do formato continua visível, nunca some calado.
        """
        if self.project is None:
            return None
        from ..brief import brief_path, load_brief

        if not brief_path(self.project).is_file():
            return None
        data = copy.deepcopy(load_brief(self.project))
        beats = data.get("beats") if isinstance(data, dict) else None
        retired = set(self.retired_beat_ids())
        if isinstance(beats, list) and retired:
            data["beats"] = [b for b in beats if not (isinstance(b, dict) and b.get("id") in retired)]
        return data

    def retired_beat_ids(self) -> list[str]:
        """Ids dos beats aposentados (`"retired": true`), na ordem do BRIEF.md, para o plugin que precisa do histórico."""
        if self.project is None:
            return []
        from ..brief import load_brief, retired_beat_ids

        retired = retired_beat_ids(self.project)
        if not retired:
            return []
        beats = load_brief(self.project).get("beats") or []
        ordered = [b["id"] for b in beats if isinstance(b, dict) and b.get("id") in retired]
        return list(dict.fromkeys(ordered))


@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str
    handler: Callable[[dict, CommandContext], dict]


@dataclass(frozen=True)
class MediaRequest:
    """Mídia que o export precisa: o core resolve `media_id` e grava em `dest`."""

    media_id: str  # id lógico da tabela de mídia do plano, nunca um caminho
    dest: str  # caminho POSIX relativo, dentro de "assets/"


@dataclass(frozen=True)
class ExportResult:
    """O que um exportador devolve: texto e pedidos de mídia; quem escreve é o core."""

    files: dict[str, str]  # caminho relativo -> texto UTF-8
    media: list[MediaRequest] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResolverHit:
    """Arquivo que um resolvedor achou dentro de `permissions.paths`."""

    path: str | Path  # absoluto, dentro de permissions.paths
    license: str | None = None  # só informativa: nunca vale como permit


class Exporter(Protocol):
    def __call__(self, plan: dict, options: dict) -> ExportResult: ...


class Resolver(Protocol):
    def __call__(self, kind: str, name: str) -> ResolverHit | None: ...


@dataclass(frozen=True)
class ExporterSpec:
    name: str
    description: str
    export: Callable[[dict, dict], ExportResult]


@dataclass(frozen=True)
class ResolverSpec:
    name: str
    kinds: tuple[str, ...]
    resolve: Callable[[str, str], ResolverHit | None]
