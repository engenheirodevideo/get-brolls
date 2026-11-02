"""`getbrolls.toml`: perfil de workspace que fixa pastas, executáveis e plugins.

O perfil é um arquivo TOML pequeno (stdlib `tomllib`) que a CLI descobre a partir do
`--project` e depois da pasta atual. Ele pode apontar executáveis e pastas, então só
vale quando é confiável:

- `env`: veio de `GB_PROFILE` no ambiente do processo (a mesma fronteira de confiança
  de `GB_VENV_PATH`); com `GB_PROFILE_SHA256` junto, o sha tem que bater;
- `inside_home`: é exatamente `<GB_HOME>/getbrolls.toml`, com o `GB_HOME` de antes do
  perfil;
- `trusted`: `profile --action trust` gravou o sha destes bytes em `trusted-profiles.json`, que
  mora no `GB_HOME` de antes do perfil — nunca no `home` que o próprio perfil escolhe,
  senão ele se autoconfiaria.

O arquivo em si nunca é um link simbólico e, no POSIX, é do usuário atual e não é
gravável por grupo nem por outros. Com o `GB_HOME` vindo do perfil, o `.env` e o
`plugins.json` dessa pasta passam pelas mesmas recusas (`check_home_files`): eles não
entram no sha do perfil, e um `home` relativo pode cair dentro de um repositório
compartilhado. A prévia de `profile --action trust` avisa desse caso (`trust_warnings`).

Precedência: ambiente (inclusive `.env`) vence o perfil, que vence o padrão. `home` é
aplicado antes da escolha do `.env` (`apply_home`), o resto depois (`apply_rest`).
`plugins` é a exceção: é um teto — o perfil só estreita o `GB_PLUGINS` do ambiente.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__, _paths, install_report
from .profile_apply import (  # noqa: F401 -- reexported: callers and tests keep reading these here; pylint: disable=unused-import
    activate,
    apply_home,
    apply_rest,
    current,
    finish,
    from_profile,
    marketplace_ceiling,
    plugin_ceiling,
    reset_state,
    tolerant,
)
from .profile_load import (  # noqa: F401 -- reexported: callers and tests keep reading these here; pylint: disable=unused-import
    APPLIED_TRUST,
    FIELDS,
    MAX_BYTES,
    MESSAGES,
    PROFILE_NAME,
    TRUST_FILE,
    Active,
    Located,
    ProfileRefusedError,
    check_home_files,
    check_requires,
    load,
    locate,
    requires_problem,
)
from .profile_trust import (  # noqa: F401 -- reexported: callers and tests keep reading these here; pylint: disable=unused-import
    _key,
    _update_store,
    _write_store,
    read_store,
    trust,
    trust_home,
    trust_state,
    trust_warnings,
    untrust,
)

if TYPE_CHECKING:
    from argparse import Namespace
    from collections.abc import Mapping


def value_sources(active: Active | None, environ: Mapping[str, str] | None = None) -> dict[str, dict]:
    """De onde vem cada campo do perfil: `profile`, `env_file`, `env` ou `default`."""
    from . import config  # tardio: o config importa o runtime

    environ = os.environ if environ is None else environ
    applied = active.applied if active is not None else {}
    env_applied = config._ENV_APPLIED  # pylint: disable=protected-access
    sources: dict[str, dict] = {}
    for field, key in FIELDS.items():
        value = environ.get(key) or None
        if value is None:
            source = "default"
        elif applied.get(key) == value:
            source = "profile"
        elif env_applied.get(key) == value:
            source = "env_file"
        else:
            source = "env"
        sources[field] = {"env": key, "value": value, "source": source}
    return sources


def report(active: Active | None = None) -> dict:
    """Retrato do perfil para `doctor` e `profile --action show`; nunca levanta."""
    active = current() if active is None else active
    if active is None:
        return {"path": None, "source": None, "trust": "none", "applied": [], "values": value_sources(None)}
    loaded = active.profile
    path = loaded.path if loaded is not None else active.located.path
    requires = None
    if loaded is not None and loaded.requires is not None:
        requires = {"spec": loaded.requires, "ok": active.requires_problem is None, "version": __version__}
    return {
        "path": str(path) if path is not None else None,
        "source": active.located.source,
        "trust": active.trust,
        "sha256": loaded.sha256 if loaded is not None else None,
        "searched": [str(item) for item in active.located.searched],
        "requires": requires,
        "error": active.error,
        "message": MESSAGES.get(active.trust) or active.requires_problem,
        "applied": sorted(active.applied),
        "values": value_sources(active),
        "trust_file": str(active.pre_home / TRUST_FILE),
    }


def _command(*args: str) -> str:
    return " ".join((_paths.cli_prefix_text(), *(_paths.quote_arg(arg) for arg in args)))


def doctor_problems(active: Active | None) -> list[dict]:
    """Linhas de `summary.missing` do `doctor`: perfil inválido, não confiável ou `requires`."""
    if active is None:
        return []
    item = "perfil getbrolls.toml"
    path = active.profile.path if active.profile is not None else active.located.path
    if active.trust == "invalid":
        show = (
            _command("profile", "--action", "show", "--path", str(path))
            if path is not None
            else _command("profile", "--action", "show")
        )
        note = f"{active.error} Corrija o arquivo ou ignore o perfil com `--profile off`."
        return [{"item": item, "fix": show, "note": note}]
    if active.trust in MESSAGES and path is not None:
        return [
            {
                "item": item,
                "fix": _command("profile", "--action", "trust", "--path", str(path)),
                "note": MESSAGES[active.trust],
            }
        ]
    if active.requires_problem and active.trust in APPLIED_TRUST and path is not None:
        return [
            {
                "item": item,
                "fix": _command("profile", "--action", "show", "--path", str(path)),
                "note": active.requires_problem,
            }
        ]
    return []


def run(args: Namespace) -> dict:
    """`profile --action show|trust|untrust`: o comando `profile` da CLI."""
    if getattr(args, "legacy", None):
        from . import renamed  # tardio: o perfil é lido antes do resto da CLI

        raise renamed.usage(
            f"profile --action {args.action} <arquivo>",
            f"profile --action {args.action} --path ARQUIVO",
            reason="profile_positional",
        )
    active = current() or activate(args)
    action = args.action
    target = Path(args.path) if getattr(args, "path", None) else None
    if action == "trust":
        return trust(target, located=active.located, yes=bool(args.yes), expect=getattr(args, "expect", None))
    if action == "untrust":
        return untrust(target, located=active.located)
    return {"profile": report(active), "env_file": install_report.env_report(getattr(args, "env_file", None))}
