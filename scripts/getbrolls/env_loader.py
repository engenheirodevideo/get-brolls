"""Loading the .env and the profile, in the order of the contract.

The part of the configuration that needs the profile, the plugin loader and the runtime."""

# pylint: disable=missing-function-docstring
# Legado: o corpo de `load_env_choice` e `load_environment` é o mesmo de `config` antes da 2.6.0.
# De `config` só resta a função-ponte `config.installed_env` (import tardio), mantida porque `sdk/loader.py` a lê lá;
# os demais nomes (`load_env`, `plugin_env_keys`, `load_environment`) vivem só aqui.

import os
from pathlib import Path

from . import _paths, profile, runtime
from ._paths import strict_is_file
from .config import (
    _ENV_APPLIED,
    _PLUGIN_ENV,
    KEYS,
    SECRET_ENV_SUFFIXES,
    _env_shown,
    _parse_env,
    _refuse_env_entries,
    _typed_env,
    cache_dir_alias_in_use,
    core_env_key,
    env_namespace_owner,
    is_serve_child,
    toolchain_env_key,
)
from .sdk import loader
from .sdk.manifest import read_manifest


def installed_env():
    """`{id: [nomes de permissions.env]}` dos plugins em `plugins/`, só pelo manifesto."""
    try:
        # lenient: inside try/except OSError, which 3.11 also reaches: no plugin env keys
        folders = [p for p in loader.plugins_root().iterdir() if p.is_dir() and not p.name.startswith((".", "_"))]
    except OSError:
        return {}
    installed = {}
    for folder in folders:
        try:
            manifest = read_manifest(folder)
        except (ValueError, OSError):
            continue
        installed[manifest["id"]] = list(manifest["permissions"]["env"])
    return installed


def plugin_env_owners():
    """`{variável: id do plugin}` que o `.env` aceita para plugins instalados: só as do
    espaço de nomes do próprio plugin, nunca uma variável do core."""
    installed = installed_env()
    return {
        key: plugin_id
        for plugin_id, keys in installed.items()
        for key in keys
        if not core_env_key(key) and env_namespace_owner(key, installed) == plugin_id
    }


def plugin_env_keys():
    """Nomes de `permissions.env` dos plugins instalados que o `.env` aceita."""
    return frozenset(plugin_env_owners())


def _refused_plugin_key(number, key, installed):
    """Mensagem para uma chave do `.env` que nenhum plugin pode receber, ou `None`."""
    declared_by = sorted(plugin_id for plugin_id, keys in installed.items() if key in keys)
    if declared_by:
        prefix = declared_by[0].upper() + "_"
        return (
            f".env: a variável {key} (linha {number}) é pedida pelo plugin {declared_by[0]}, mas o .env só "
            f"entrega a um plugin variáveis do espaço de nomes dele ({prefix}...), nunca uma do core ou de "
            "outro plugin. Tire a linha do .env; se o plugin precisa dela, defina-a no ambiente do processo."
        )
    try:
        state = loader.read_state()
    except (ValueError, OSError):
        return None
    known = set(state.get("enabled", {})) | set(state.get("last_pins", {})) | set(state.get("sources", {}))
    owner = env_namespace_owner(key, known - set(installed))
    if owner is None:
        return None
    return (
        f".env: a variável {key} (linha {number}) é do plugin {owner}, que não está mais em plugins/. "
        f"Tire a linha do .env, ou reinstale o plugin {owner}."
    )


_CACHE_DIR_DEPRECATED = (
    "GETBROLLS_CACHE_DIR ainda vale, mas deixa de ser lido na 2.7. Defina GB_CACHE_DIR no lugar dela."
)


_ENV_SHADOWED = (
    "Há dois .env: usei o da pasta da instalação e ignorei o de $GB_HOME (os dois nunca se misturam). "
    "Deixe só um; veja install.env_file no doctor."
)


_GB_HOME_IN_ENV_DEPRECATED = (
    "GB_HOME definido num .env ainda vale, mas deixa de ser lido na 2.7. "
    "Defina GB_HOME no ambiente do processo e tire a linha do .env."
)


# `.env` cuja linha GB_HOME perdeu para o `home` do perfil (aviso `ENV_GB_HOME_IGNORED`).
_HOME_IGNORED: list[str] = []


_GB_HOME_IGNORED = (
    "GB_HOME do .env ({path}) foi ignorado: o `home` do perfil getbrolls.toml confiável vale para a pasta "
    "pessoal, porque é ele que decide qual .env é lido. Tire a linha do .env ou o `home` do perfil."
)


def _home_from_profile(value):
    """O `GB_HOME` em vigor veio do `home` do perfil e o `.env` pede outro?"""
    return _paths.from_profile("GB_HOME") and os.environ.get("GB_HOME") != value


def _note_home_ignored(entries, path, typed=None):
    """Guarda o `.env` cuja linha `GB_HOME` vai perder para o `home` do perfil (antes de exportar nada)."""
    _HOME_IGNORED.clear()
    if any(key == "GB_HOME" and _home_from_profile(value) for _number, key, value in entries):
        _HOME_IGNORED.append(_env_shown(path, typed))


def load_env(path, *, source=None, withhold_secrets=False):
    """Lê o `.env`: chaves do core (`KEYS`) vão para o ambiente do processo, como
    sempre; as de `permissions.env` de plugins instalados ficam só em `_PLUGIN_ENV`,
    lidas por `api.env` do plugin dono, nunca exportadas. As do core entram primeiro —
    `GB_HOME` no próprio `.env` decide em qual `plugins/` procurar os manifestos
    (menos no `.env` de `$GB_HOME`, onde ele é recusado: `source == "gb_home"`).
    Chave que ninguém declara é erro. Devolve as chaves do core que valeram deste arquivo
    (as que o ambiente do processo já tinha ficam de fora: o processo vence). Com `withhold_secrets` (o servidor de
    fundo), as chaves com sufixo de segredo (`SECRET_ENV_SUFFIXES`) não entram no ambiente."""
    _PLUGIN_ENV.clear()
    path = Path(path)
    if not strict_is_file(path):
        return frozenset()
    typed = _typed_env(source, path)
    entries = list(_parse_env(path, typed))
    _refuse_env_entries(entries, source, path)
    applied = set()
    _note_home_ignored(entries, path, typed)
    for _number, key, value in entries:
        if key not in KEYS or (withhold_secrets and key.endswith(SECRET_ENV_SUFFIXES)):
            continue
        if key not in os.environ:
            os.environ[key] = value
            _ENV_APPLIED[key] = value
        # Uma leitura anterior deste `.env` (a antecipada do `main`) já pode ter posto o valor.
        if _ENV_APPLIED.get(key) == os.environ.get(key) == value:
            applied.add(key)
    core_keys = frozenset(applied)
    unknown = [entry for entry in entries if entry[1] not in KEYS]
    if not unknown:
        return core_keys
    owners = plugin_env_owners()
    for number, key, value in unknown:
        if key not in owners:
            raise ValueError(
                _refused_plugin_key(number, key, installed_env())
                or f".env: variável desconhecida na linha {number}: {key}. Aceitas: "
                + ", ".join(sorted(KEYS))
                + "."
                + _old_name_hint(key)
            )
        _PLUGIN_ENV.setdefault(key, (owners[key], value))
        if toolchain_env_key(key):
            runtime.record_warning(
                "PLUGIN_ENV_TOOLCHAIN",
                f".env: {key} (linha {number}) é do plugin {owners[key]} e tem nome de variável que ferramentas "
                "do sistema leem; ela chega só ao plugin, por api.env, nunca ao ambiente dos subprocessos.",
            )
    return core_keys


def _old_name_hint(key):
    """ " Use `GB_X`." quando a chave é o `GETBROLLS_X` da versão de testes da 2.6 e `GB_X` existe."""
    prefix = "GETBROLLS_"
    if key.startswith(prefix) and "GB_" + key[len(prefix) :] in KEYS:
        return f" Use `GB_{key[len(prefix) :]}`."
    return ""


_OUTPUT_IGNORED = "GB_OUTPUT só aceita `json`; o valor dado é ignorado e a saída continua JSON."


def load_env_choice(choice, *, warn, withhold_secrets=False):
    """Carrega o `.env` escolhido por `_paths.env_file()`; com `warn`, registra os avisos.

    A origem do `.env` não vira aviso em todo comando: ela fica em `install.env_file`
    do `doctor`. Só dois `.env` ao mesmo tempo (um deles ignorado) e `GB_HOME` num
    `.env` fora de `$GB_HOME` (compatível até a 2.7) avisam."""
    if choice.path is None:
        _PLUGIN_ENV.clear()
        return
    core_keys = load_env(choice.path, source=choice.source, withhold_secrets=withhold_secrets)
    if not warn:
        return
    if choice.ignored:
        runtime.record_warning("ENV_FILE_SHADOWED", _ENV_SHADOWED)
    if "GB_HOME" in core_keys:
        runtime.record_warning("DEPRECATED", _GB_HOME_IN_ENV_DEPRECATED)
    for ignored in _HOME_IGNORED:
        runtime.record_warning("ENV_GB_HOME_IGNORED", _GB_HOME_IGNORED.format(path=ignored))


def load_environment(args, *, warn):
    """Perfil e `.env` na ordem do contrato: `home` do perfil → escolha do `.env` → `.env` → resto do perfil.

    O `home` do perfil decide qual `$GB_HOME/.env` é lido; o `.env` vence o resto do
    perfil. Devolve a escolha do `.env` (`_paths.EnvChoice`). Perfil não confiável ou
    inválido num comando estrito é `UsageError`; `requires` que não bate,
    `PrerequisiteError` (ver `profile.activate`).
    """
    active = profile.activate(args)
    profile.check_home_files()
    choice = _paths.env_file(getattr(args, "env_file", None))
    load_env_choice(choice, warn=warn, withhold_secrets=is_serve_child(args))
    profile.finish(active)
    if warn and cache_dir_alias_in_use():
        runtime.record_warning("DEPRECATED", _CACHE_DIR_DEPRECATED)
    if warn and os.environ.get("GB_OUTPUT", "").strip() not in ("", "json"):
        runtime.record_warning("ENV_VALUE_IGNORED", _OUTPUT_IGNORED)
    return choice
