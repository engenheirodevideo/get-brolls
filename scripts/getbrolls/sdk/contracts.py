"""Contratos públicos do SDK: o que uma extensão entrega ao registro."""

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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
        if self.project is None:
            return None
        from ..brief import brief_path, load_brief

        if not brief_path(self.project).is_file():
            return None
        return copy.deepcopy(load_brief(self.project))


@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str
    handler: Callable[[dict, CommandContext], dict]
