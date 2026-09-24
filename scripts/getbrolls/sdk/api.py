"""Objeto entregue ao `register(api)` do plugin: o único caminho de entrada no registro."""

import contextlib
import contextvars
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .. import http as core_http
from .. import logs
from ..http import ProviderError, get_json, public_url
from ..models import candidate as core_candidate
from .contracts import CommandSpec

_log = logs.get("sdk")

# Nome de arquivo que a rota pode pedir dentro da pasta de trabalho: sem barra,
# sem `..`, sem começar por ponto (nada de arquivo escondido nem caminho).
FILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

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

    def _own(self, kind, name):
        if name not in self._manifest["contributes"][kind]:
            raise ValueError(f"Plugin {self.plugin_id}: {kind[:-1]} {name!r} não está declarado em contributes.{kind}.")
        if name != self.plugin_id and not str(name).startswith(self.plugin_id + "_"):
            raise ValueError(
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
            raise ValueError(f"Plugin {self.plugin_id}: comando {name!r} não está declarado em contributes.commands.")
        self._registry.add_command(CommandSpec(name, help, handler), owner=self.plugin_id)
        self._registered["commands"].add(name)

    def candidate(self, provider, source_id, title, source_url=None):
        if provider not in self._manifest["contributes"]["providers"]:
            raise ValueError(f"Plugin {self.plugin_id}: candidato de fonte não declarada {provider!r}.")
        return core_candidate(provider, str(source_id), title, public_url(source_url))

    def env(self, key):
        if key not in self._manifest["permissions"]["env"]:
            raise ValueError(f"Plugin {self.plugin_id}: variável {key} não está em permissions.env.")
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

    def get_json(self, url, params=None, headers=None, cache_ttl=0, keep_signed=False):
        self._check_host(url)
        if keep_signed:
            cache_ttl = 0
        return get_json(url, params, headers, cache_ttl=cache_ttl, keep_signed=keep_signed)

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
        if not isinstance(name, str) or not FILE_NAME_RE.fullmatch(name) or ".." in name:
            raise ProviderError(
                f"Plugin {self.plugin_id}: nome de arquivo inválido; use letras, números, '.', '_' ou '-', sem pasta."
            )
        if headers is not None and (
            not isinstance(headers, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())
        ):
            raise ProviderError(f"Plugin {self.plugin_id}: headers tem que ser um dict de texto para texto.")
        self._check_host(url)
        target = workdir / name
        core_http.download(url, target, headers=dict(headers or {}), allow_signed=True)
        return target

    def finish(self):
        for kind, names in self._registered.items():
            missing = set(self._manifest["contributes"][kind]) - names
            if missing:
                raise ValueError(
                    f"Plugin {self.plugin_id}: declarado em contributes.{kind} e não registrado: {', '.join(sorted(missing))}."
                )
        for name in sorted(self._registered["providers"]):
            route = self._registry.provider(name).capabilities.route
            if route is not None and route not in self._registered["routes"]:
                raise ValueError(
                    f"Plugin {self.plugin_id}: {name} aponta capabilities.route={route!r}, "
                    "que não é uma rota registrada por este plugin."
                )
