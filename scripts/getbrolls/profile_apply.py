"""Perfil `getbrolls.toml`, a aplicação: o que o perfil confiável põe no ambiente, os tetos e `activate`."""

from __future__ import annotations

import enum
import os
from typing import TYPE_CHECKING

from . import _paths
from .errors import PrerequisiteError, UsageError
from .profile_load import (
    _PRE_HOME,
    APPLIED_TRUST,
    MESSAGES,
    TOLERANT_COMMANDS,
    TOLERANT_SETUP_FLAGS,
    Active,
    Located,
    Profile,
    ProfileRefusedError,
    load,
    locate,
    requires_problem,
)
from .profile_trust import _home_from, trust_state

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Callable, Collection, Mapping, MutableMapping
    from pathlib import Path

# O que os `apply_*` puseram no ambiente mora num registro só, em `_paths`
# (`note_profile_env`/`profile_env`), que o `runtime_info` também lê. Aqui fica o
# GB_HOME de antes de o perfil trocar o dele, o que esta ativação escreveu no ambiente
# (para desfazer na próxima) e a própria ativação.
_WRITTEN: dict[str, str | None] = {}  # chave → valor que escrevemos (None = removemos)
_ORIGINAL: dict[str, str | None] = {}  # chave → valor de antes da primeira escrita (None = ausente)
# A ativação em vigor e o `args` que a pediu (comparado por identidade); vazio antes de `activate`.
_ACTIVATION: list[tuple[Active, object]] = []


def reset_state() -> None:
    """Esquece o que um perfil anterior aplicou (nova ativação, ou testes); não mexe no ambiente."""
    _paths.forget_profile_env()
    _PRE_HOME.clear()
    _WRITTEN.clear()
    _ORIGINAL.clear()
    _ACTIVATION.clear()


def from_profile() -> dict[str, str]:
    """Chaves `GB_*` que o perfil pôs no ambiente, com o valor que pôs."""
    return _paths.profile_env()


# --- aplicação -----------------------------------------------------------------------


def _write(environ: MutableMapping[str, str], key: str, value: str | None) -> None:
    """Escreve (ou remove, com `None`) guardando o valor de antes, para `_undo`."""
    if key not in _ORIGINAL:
        _ORIGINAL[key] = environ.get(key)
    _WRITTEN[key] = value
    if value is None:
        environ.pop(key, None)
    elif environ.get(key) != value:
        environ[key] = value


def _set(environ: MutableMapping[str, str], key: str, value: str, applied: dict[str, str]) -> None:
    _write(environ, key, value)
    applied[key] = value
    _paths.note_profile_env({key: value})


def apply_home(profile: Profile, environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Só `GB_HOME`, e só quando o ambiente não tem um (vazio conta como não definido).

    Guarda o `GB_HOME` de antes, que `trust_home` usa para achar o store.
    """
    environ = os.environ if environ is None else environ
    applied: dict[str, str] = {}
    value = profile.values.get("GB_HOME")
    if value is None or (environ.get("GB_HOME") and environ.get("GB_HOME") != _paths.profile_env().get("GB_HOME")):
        return applied
    if not _PRE_HOME:
        _PRE_HOME.append(_home_from(environ))
    if environ.get("GB_HOME") != value:
        _set(environ, "GB_HOME", value, applied)
    return applied


def _plugins_ceiling(profile: Profile, selected: str | None) -> str | None:
    """Novo `GB_PLUGINS` com o teto do perfil, ou `None` se nada muda."""
    ceiling = profile.values["GB_PLUGINS"]
    if not selected or not selected.strip():
        return ceiling
    if selected.strip().lower() == "off":
        return None
    allowed = set(profile.plugins or ())
    chosen = [part.strip() for part in selected.split(",") if part.strip()]
    narrowed = ",".join(item for item in dict.fromkeys(chosen) if item in allowed) or "off"
    return None if narrowed == selected else narrowed


def apply_rest(profile: Profile, environ: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Todo campo menos `home`, quando o ambiente não o define; `GB_PLUGINS` vira teto.

    Com `GB_PLUGINS` no ambiente, fica a interseção com a lista do perfil (na ordem do
    ambiente); interseção vazia vira `off`, e `off` continua `off`.
    """
    environ = os.environ if environ is None else environ
    applied: dict[str, str] = {}
    for key, value in profile.values.items():
        if key == "GB_HOME":
            continue
        if key == "GB_PLUGINS":
            narrowed = _plugins_ceiling(profile, environ.get(key))
            if narrowed is not None:
                _set(environ, key, narrowed, applied)
            continue
        if not environ.get(key):
            _set(environ, key, value, applied)
    return applied


class _Current(enum.Enum):
    """Marca "o perfil em vigor nesta execução" nos tetos."""

    ACTIVE = "active"


CURRENT = _Current.ACTIVE


def applied_profile() -> Profile | None:
    """O perfil que vale nesta execução (confiável e aplicado), ou `None`."""
    active = current()
    if active is None or active.trust not in APPLIED_TRUST:
        return None
    return active.profile


def _ceiling(
    profile: Profile | _Current | None, pick: Callable[[Profile], Collection[str] | None]
) -> frozenset[str] | None:
    """O conjunto que `pick` tira do perfil (em vigor quando `CURRENT`); `None` quando ele não restringe."""
    if profile is CURRENT:
        profile = applied_profile()
    if not isinstance(profile, Profile):
        return None
    names = pick(profile)
    return None if names is None else frozenset(names)


def plugin_ceiling(profile: Profile | _Current | None = CURRENT) -> frozenset[str] | None:
    """Ids de plugin que o perfil permite; `None` quando ele não restringe plugins.

    Sem argumento, vale o perfil em vigor (`applied_profile`).
    """
    return _ceiling(profile, lambda found: found.plugins)


def marketplace_ceiling(profile: Profile | _Current | None = CURRENT) -> frozenset[str] | None:
    """Marketplaces que o perfil permite; `None` quando ele não restringe marketplaces.

    Contrato com `getbrolls.sdk.marketplace`: por padrão, ele chama
    `marketplace_ceiling()` sem argumento a cada checagem — isto é, com o perfil já
    ativado por `env_loader.load_environment` — e trata o resultado como teto: `None` não
    restringe; um conjunto (mesmo vazio) limita os marketplaces aceitos a ele. O perfil
    só estreita, nunca acrescenta um marketplace que a pessoa não configurou.
    """
    return _ceiling(profile, lambda found: found.marketplaces)


# --- ativação (CLI) ------------------------------------------------------------------


def tolerant(args: Namespace) -> bool:
    """O comando relata o problema do perfil em vez de parar?"""
    command = getattr(args, "command", None)
    if command == "setup":
        # lenient: dynamic getattr over argparse flag names, not an existence check on a path
        return any(getattr(args, flag, None) for flag in TOLERANT_SETUP_FLAGS)
    return command in TOLERANT_COMMANDS


def _flag(args: Namespace) -> str | None:
    """`--profile`, ou o `--path` de `profile --action show|trust|untrust`."""
    flag = getattr(args, "profile", None)
    if flag:
        return flag
    if getattr(args, "command", None) == "profile":
        return getattr(args, "path", None)
    return None


def _exports_intact(environ: Mapping[str, str]) -> bool:
    return all(environ.get(key) == _WRITTEN.get(key) for key in ("GB_PROFILE", "GB_PROFILE_SHA256"))


def _undo(environ: MutableMapping[str, str]) -> None:
    """Desfaz o que uma ativação anterior deste processo escreveu e ninguém trocou depois.

    Assim o `GB_PROFILE` que o próprio processo exportou nunca volta como "veio do
    ambiente" (e portanto confiável) numa nova ativação.
    """
    for key, written in _WRITTEN.items():
        if environ.get(key) != written:
            continue
        original = _ORIGINAL.get(key)
        if original is None:
            environ.pop(key, None)
        else:
            environ[key] = original
    reset_state()


def _evaluate(located: Located, pre_home: Path, environ: Mapping[str, str]) -> Active:
    """Carrega e classifica o perfil localizado, sem aplicar nada."""
    if located.disabled:
        return Active(located, None, "disabled", None, None, pre_home, {})
    if located.path is None:
        return Active(located, None, "none", None, None, pre_home, {})
    loaded = load(located.path)
    state = trust_state(loaded, located, pre_home, environ=environ)
    return Active(located, loaded, state, None, requires_problem(loaded), pre_home, {})


def _export(active: Active, environ: MutableMapping[str, str]) -> None:
    """Passa aos filhos o perfil já conferido (com o sha) ou `off`, nunca a busca de novo."""
    if active.profile is not None and active.trust in APPLIED_TRUST:
        _write(environ, "GB_PROFILE", str(active.profile.path))
        _write(environ, "GB_PROFILE_SHA256", active.profile.sha256)
    else:
        _write(environ, "GB_PROFILE", "off")
        _write(environ, "GB_PROFILE_SHA256", None)


def activate(args: Namespace, environ: MutableMapping[str, str] | None = None) -> Active:
    """Acha, confere e aplica o `home` do perfil; o resto vem em `finish`, depois do `.env`.

    A segunda chamada com o mesmo `args` (a de `execute()`, depois da de `main()`)
    reaproveita a ativação enquanto o `GB_PROFILE` exportado estiver intacto. Qualquer
    outra começa do zero, desfazendo a anterior.

    Comando estrito: perfil inválido, não confiável ou mudado é `UsageError`; `requires`
    que não bate é `PrerequisiteError`. Comando tolerante (`tolerant`): o problema fica
    registrado na ativação e só o perfil confiável é aplicado.
    """
    environ = os.environ if environ is None else environ
    if _ACTIVATION and _ACTIVATION[0][1] is args and _exports_intact(environ):
        return _ACTIVATION[0][0]
    _undo(environ)
    pre_home = _home_from(environ)
    strict = not tolerant(args)
    located = Located(None, None, False, ())
    try:
        located = locate(getattr(args, "project", None), _flag(args), environ=environ)
        active = _evaluate(located, pre_home, environ)
    except UsageError as exc:
        if strict:
            raise ProfileRefusedError(str(exc), "invalid") from exc
        active = Active(located, None, "invalid", str(exc), None, pre_home, {})
    if active.trust in MESSAGES:
        if strict:
            raise ProfileRefusedError(MESSAGES[active.trust], active.trust)
    elif active.profile is not None and active.trust in APPLIED_TRUST:
        if strict and active.requires_problem:
            raise PrerequisiteError(active.requires_problem)
        active.applied.update(apply_home(active.profile, environ))
    _export(active, environ)
    _ACTIVATION[:] = [(active, args)]
    return active


def finish(active: Active, environ: MutableMapping[str, str] | None = None) -> None:
    """Aplica o resto do perfil confiável, depois do `.env` (que vence o perfil)."""
    if active.profile is None or active.trust not in APPLIED_TRUST:
        return
    active.applied.update(apply_rest(active.profile, environ))


def current() -> Active | None:
    """A ativação desta execução, ou `None` antes de `activate`."""
    return _ACTIVATION[0][0] if _ACTIVATION else None
