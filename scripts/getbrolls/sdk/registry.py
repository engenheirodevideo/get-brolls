"""Registro tipado das extensões: o core se registra primeiro e sempre vence."""

from typing import cast

from .contracts import CORE, MATCH_KINDS, MEDIA_KINDS, NAME_RE, Preset, Provider, ProviderCapabilities

KINDS = ("provider", "preset")


class RegistryError(ValueError):
    pass


def _check_name(kind: str, name: object) -> None:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise RegistryError(
            f"Nome de {kind} inválido: {name!r}; use 2–32 caracteres a-z, 0-9 e _, começando por letra."
        )


class Registry:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, object]] = {kind: {} for kind in KINDS}
        self._owners: dict[str, dict[str, str]] = {kind: {} for kind in KINDS}
        self._hosts: dict[str, str] = {}
        # id do plugin → linha de inventário (status, motivo); preenchido pelo loader.
        self.plugins: dict[str, dict] = {}

    def _claim(self, kind: str, name: str, owner: str) -> None:
        current = self._owners[kind].get(name)
        if current is not None:
            raise RegistryError(f"{kind} {name!r} já registrado por {current}; registro de {owner} recusado.")

    def add_provider(self, provider: Provider, owner: str = CORE) -> None:
        name = getattr(provider, "name", None)
        _check_name("provider", name)
        name = cast("str", name)
        caps = getattr(provider, "capabilities", None)
        if not isinstance(caps, ProviderCapabilities):
            raise RegistryError(f"Provider {name!r}: capabilities tem que ser ProviderCapabilities.")
        if caps.match_kind not in MATCH_KINDS or not set(caps.media_kinds) <= set(MEDIA_KINDS):
            raise RegistryError(f"Provider {name!r}: match_kind ou media_kinds fora do contrato.")
        self._claim("provider", name, owner)
        taken = [host for host in caps.url_hosts if host in self._hosts]
        if taken:
            raise RegistryError(f"Host {taken[0]} já pertence à fonte {self._hosts[taken[0]]}; {name!r} recusado.")
        self._items["provider"][name] = provider
        self._owners["provider"][name] = owner
        for host in caps.url_hosts:
            self._hosts[host] = name

    def provider(self, name: str) -> Provider | None:
        return self._items["provider"].get(name)  # type: ignore[return-value]

    def provider_names(self) -> tuple[str, ...]:
        return tuple(self._items["provider"])

    def provider_for_host(self, host: str) -> Provider | None:
        name = self._hosts.get(host)
        return self.provider(name) if name else None

    def add_preset(self, name: str, url: str, text: str, owner: str = CORE) -> None:
        _check_name("preset", name)
        if not isinstance(text, str) or not text.rstrip().endswith(f"verifique a página da fonte: {url}"):
            raise RegistryError(f'Preset {name!r}: o texto tem que terminar em "verifique a página da fonte: {url}".')
        self._claim("preset", name, owner)
        self._items["preset"][name] = Preset(name, url, text)
        self._owners["preset"][name] = owner

    def preset(self, name: str) -> Preset | None:
        return self._items["preset"].get(name)  # type: ignore[return-value]

    def preset_names(self) -> tuple[str, ...]:
        return tuple(self._items["preset"])

    def owner(self, kind: str, name: str) -> str | None:
        return self._owners[kind].get(name)

    def remove_owner(self, owner: str) -> None:
        for kind in KINDS:
            for name in [n for n, o in self._owners[kind].items() if o == owner]:
                del self._owners[kind][name]
                del self._items[kind][name]
        self._hosts = {host: name for host, name in self._hosts.items() if name in self._items["provider"]}


_STATE: dict[str, Registry | None] = {"registry": None}


def get_registry() -> Registry:
    """Registro do processo; montado na primeira consulta (start-up da CLI continua rápido)."""
    if _STATE["registry"] is None:
        from .. import providers

        registry = Registry()
        providers.register_builtins(registry)
        _STATE["registry"] = registry
    return _STATE["registry"]


def reset_registry() -> None:
    """Descarta o registro montado: `plugins enable/disable` e testes recomeçam do zero."""
    _STATE["registry"] = None
