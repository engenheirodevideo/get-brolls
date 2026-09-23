"""Contratos públicos do SDK: o que uma extensão entrega ao registro."""

import re
from dataclasses import dataclass
from typing import Protocol

# Muda só em major do get-brolls. Plugin declara o mesmo número em `sdk_api`.
SDK_API = 1
# Dono dos built-ins no registro; nenhum plugin pode usar este id.
CORE = "core"
NAME_RE = re.compile(r"[a-z][a-z0-9_]{1,31}")
MATCH_KINDS = ("literal", "illustrative")
MEDIA_KINDS = ("video", "image")


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
