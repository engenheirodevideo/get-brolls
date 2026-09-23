"""Objeto entregue ao `register(api)` do plugin: o único caminho de entrada no registro."""

import logging
import os
from urllib.parse import urlsplit

from .. import logs
from ..http import ProviderError, get_json, public_url
from ..models import candidate as core_candidate

_log = logs.get("sdk")


class PluginApi:
    def __init__(self, manifest, registry):
        self.plugin_id = manifest["id"]
        self._manifest = manifest
        self._registry = registry
        self._registered = {"providers": set(), "presets": set()}

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

    def candidate(self, provider, source_id, title, source_url=None):
        if provider not in self._manifest["contributes"]["providers"]:
            raise ValueError(f"Plugin {self.plugin_id}: candidato de fonte não declarada {provider!r}.")
        return core_candidate(provider, str(source_id), title, public_url(source_url))

    def env(self, key):
        if key not in self._manifest["permissions"]["env"]:
            raise ValueError(f"Plugin {self.plugin_id}: variável {key} não está em permissions.env.")
        return os.environ.get(key)

    def get_json(self, url, params=None, headers=None, cache_ttl=0):
        host = (urlsplit(url).hostname or "").lower()
        if host not in self._manifest["permissions"]["network"]:
            logs.event(_log, logging.WARNING, "plugin_request_refused", plugin=self.plugin_id, host=host or "-")
            raise ProviderError(f"Plugin {self.plugin_id}: host {host or url!r} não está em permissions.network.")
        return get_json(url, params, headers, cache_ttl=cache_ttl)

    def finish(self):
        for kind, names in self._registered.items():
            missing = set(self._manifest["contributes"][kind]) - names
            if missing:
                raise ValueError(
                    f"Plugin {self.plugin_id}: declarado em contributes.{kind} e não registrado: {', '.join(sorted(missing))}."
                )
