"""Perfil `getbrolls.toml`, a confiança: o store `trusted-profiles.json`, `trust` e `untrust`."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import _paths, effects, json_read, logs
from .errors import DataRootError, LockedError, UsageError
from .profile_load import (
    _PRE_HOME,
    DIR_FIELDS,
    FIELDS,
    MAX_BYTES,
    PROFILE_NAME,
    TRUST_FILE,
    Located,
    Profile,
    _read_guarded,
    _refused,
    _schema_version,
    load,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_log = logs.get("profile")

LOCK_TIMEOUT_S = 10.0
_INSIDE_FOLDER = (
    "`{field}` fica dentro da pasta do getbrolls.toml ({value}): quem grava nessa pasta (um repositório "
    "compartilhado, por exemplo) pode deixar ali um `.env`, um `plugins.json` ou um runtime que o sha do "
    "perfil não cobre. Prefira uma pasta fora do repositório; com `home` do perfil, o `.env` e o "
    "`plugins.json` de lá só valem se forem seus e não graváveis por outros."
)
_UNKNOWN_PLUGINS = (
    "`plugins` cita ids que não estão instalados em {folder}: {ids}. Eles não têm efeito até serem "
    "instalados e habilitados."
)


def _inside(value: str, folder: Path) -> bool:
    target = os.path.normcase(os.path.realpath(value))
    base = os.path.normcase(os.path.realpath(folder))
    try:
        return os.path.commonpath([target, base]) == base
    except ValueError:  # unidades diferentes no Windows
        return False


def _installed_plugins(home: Path) -> set[str]:
    try:
        # lenient: inside try/except OSError, which 3.11 also reaches: no installed plugins
        return {child.name for child in (home / "plugins").iterdir() if child.is_dir()}
    except OSError:
        return set()


def trust_warnings(loaded: Profile) -> list[str]:
    """Avisos da prévia de confiança: pastas dentro da pasta do toml e plugins desconhecidos."""
    warnings = []
    folder = loaded.path.parent
    for field in DIR_FIELDS:
        value = loaded.values.get(FIELDS[field])
        if value is not None and _inside(value, folder):
            warnings.append(_INSIDE_FOLDER.format(field=field, value=value))
    if loaded.plugins:
        home = Path(loaded.values["GB_HOME"]) if "GB_HOME" in loaded.values else trust_home()
        unknown = [item for item in loaded.plugins if item not in _installed_plugins(home)]
        if unknown:
            warnings.append(_UNKNOWN_PLUGINS.format(folder=home / "plugins", ids=", ".join(unknown)))
    return warnings


# --- confiança -----------------------------------------------------------------------


def _key(path: Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def _home_from(environ: Mapping[str, str]) -> Path:
    """`GB_HOME` de `environ`, como `_paths.gb_home()` lê o do processo: o `~` cru vale a pasta pessoal."""
    if environ is os.environ:
        return _paths.gb_home()
    return Path(environ.get("GB_HOME") or Path.home() / ".getbrolls").expanduser()


def trust_home(environ: Mapping[str, str] | None = None) -> Path:
    """`GB_HOME` de antes do perfil: onde mora o `trusted-profiles.json`.

    Se o perfil ativo trocou o `GB_HOME`, vale o de antes dele — o `home` escolhido pelo
    próprio perfil nunca decide se ele é confiável.
    """
    environ = os.environ if environ is None else environ
    applied_home = _paths.profile_env().get("GB_HOME")
    if _PRE_HOME and applied_home is not None and environ.get("GB_HOME") == applied_home:
        return _PRE_HOME[0]
    return _home_from(environ)


def _store_schema() -> tuple[int, dict] | None:
    """(versão, schema) do trusted-profiles.json; `None` sem a pasta de dados do getbrolls."""
    from .sdk import schemas  # tardio: o sdk carrega o contrato inteiro

    try:
        return schemas.version("trusted_profiles"), schemas.load("trusted_profiles")
    except DataRootError:
        return None


def read_store(home: Path) -> dict:
    """`{"schema_version": N, "profiles": {chave: {"sha256", "trusted_at"}}}`; ausente = vazio.

    Estrito: versão ausente, diferente da do schema ou documento fora do schema é `store_corrupt`.
    Sem a pasta de dados (`DataRootError`) não há versão para comparar: a leitura confere só a forma
    e devolve o store sem nenhum perfil (`schema_version` `None`), o lado seguro — nenhum perfil vale por
    ele, e o `doctor` chega ao relatório. Gravar exige a versão (`_update_store`).
    """
    contract = _store_schema()
    supported = contract[0] if contract is not None else None
    raw = _read_guarded(Path(home) / TRUST_FILE, TRUST_FILE, missing_ok=True)
    if raw is None:
        return {"schema_version": supported, "profiles": {}}
    invalid = UsageError(f"{TRUST_FILE} está corrompido; apague o arquivo e confie de novo nos perfis.")
    try:
        data = json_read.loads(raw, max_bytes=MAX_BYTES)
    except json_read.JsonReadError:
        raise _refused("store", "store_corrupt", invalid) from None
    if not isinstance(data, dict) or (contract is not None and not _store_matches(data, *contract)):
        raise _refused("store", "store_corrupt", invalid)
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise _refused("store", "store_corrupt", invalid)
    for entry in profiles.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise _refused("store", "store_corrupt", invalid)
    return {"schema_version": supported, "profiles": profiles if contract is not None else {}}


def _store_matches(data: dict, supported: int, schema: dict) -> bool:
    """A versão gravada é a do schema (inteiro, sem `True`) e o documento passa no schema."""
    from .sdk import jsonschema  # tardio: o sdk carrega o contrato inteiro

    version = data.get("schema_version")
    return type(version) is int and version == supported and not jsonschema.errors(data, schema)


def trust_state(
    profile: Profile,
    located: Located,
    home: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """`env`, `inside_home`, `trusted`, `changed` ou `untrusted` (ver o topo do módulo)."""
    environ = os.environ if environ is None else environ
    if located.source == "GB_PROFILE":
        expected = environ.get("GB_PROFILE_SHA256")
        return "changed" if expected and expected != profile.sha256 else "env"
    if _key(profile.path) == _key(Path(home) / PROFILE_NAME):
        return "inside_home"
    entry = read_store(home)["profiles"].get(_key(profile.path))
    if entry is None:
        return "untrusted"
    return "trusted" if entry["sha256"] == profile.sha256 else "changed"


def _target(path: Path | None, located: Located, action: str) -> Path:
    """O getbrolls.toml de `action` (`trust` ou `untrust`): o indicado, ou o que a busca achou."""
    target = path if path is not None else located.path
    if target is None:
        raise _refused(
            action, "no_profile", UsageError("Nenhum getbrolls.toml encontrado; indique o arquivo com `--profile`.")
        )
    return Path(target)


def _write_store(home: Path, data: dict) -> None:
    """Grava atômico (temporário + `os.replace`), 0600; as pastas criadas ficam 0700.

    A versão gravada é sempre a do schema, nunca a que veio em `data`."""
    data = {"schema_version": _schema_version("trusted_profiles"), "profiles": data["profiles"]}
    effects.mkdir(home, parents=True, exist_ok=True, mode=0o700)
    with effects.op("fs.write", home / TRUST_FILE):
        fd, tmp = tempfile.mkstemp(prefix=f".{TRUST_FILE}.", dir=home)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                effects.make_private(tmp)  # inside: Ctrl-C during the wait closes the file and removes it
                json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
            Path(tmp).chmod(0o600)
            Path(tmp).replace(home / TRUST_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _update_store(home: Path, change) -> bool:
    """Lê, muda e grava o store sob a trava do SO (flock/msvcrt); devolve o que `change` diz.

    A trava é liberada pelo kernel se o processo morrer. Quem espera desiste depois de
    `LOCK_TIMEOUT_S`, com um erro que pede para repetir.
    """
    from . import runtime  # tardio: o runtime traz logs/http, que o perfil não precisa

    # Gravar exige a versão do schema: sem a pasta de dados, para aqui (`DataRootError`), antes de ler
    # um store que a leitura sem dados devolveria vazio e de regravá-lo por cima do que existe.
    _schema_version("trusted_profiles")
    effects.mkdir(home, parents=True, exist_ok=True, mode=0o700)
    lock_path = home / f"{TRUST_FILE}.lock"
    # effect-exempt: lock file of trusted-profiles.json; a lock that stays busy already logs lock_busy
    with lock_path.open("a+", encoding="utf-8", newline="\n") as lock:
        started = time.monotonic()
        deadline = started + LOCK_TIMEOUT_S
        while True:
            try:
                runtime._acquire_lock(lock)  # pylint: disable=protected-access
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    busy = LockedError(f"Outro processo está gravando {TRUST_FILE}; tente de novo.")
                    raise runtime.lock_busy(lock_path.name, started, busy) from None
                time.sleep(0.05)
        try:
            data = read_store(home)
            changed = change(data["profiles"])
            if changed:
                _write_store(home, data)
            return changed
        finally:
            runtime._release_lock(lock)  # pylint: disable=protected-access


def trust(path: Path | None, *, located: Located, yes: bool, expect: str | None) -> dict:
    """Confia no perfil em dois passos: prévia com o sha, depois `--yes --expect <sha>`."""
    loaded = load(_target(path, located, "trust"))
    result = {
        "path": str(loaded.path),
        "sha256": loaded.sha256,
        "requires": loaded.requires,
        "would_set": dict(loaded.values),
        "warnings": trust_warnings(loaded),
    }
    if not yes:
        logs.event(
            _log,
            logging.INFO,
            "profile_trust_preview",
            sha256=loaded.sha256[:12],
            requires=loaded.requires is not None,
            would_set=len(loaded.values),
            warnings=len(result["warnings"]),
        )
        command = " ".join(
            (
                _paths.cli_prefix_text(),
                "profile",
                "--action",
                "trust",
                "--path",
                _paths.quote_arg(str(loaded.path)),
                "--yes",
                "--expect",
                loaded.sha256,
            )
        )
        return {**result, "trusted": False, "confirm": {"command": command}}
    if not expect:
        raise _refused(
            "trust",
            "expect_missing",
            UsageError("`profile --action trust --yes` precisa de `--expect SHA256` da prévia."),
        )
    if expect != loaded.sha256:
        raise _refused(
            "trust",
            "expect_mismatch",
            UsageError("O getbrolls.toml mudou desde a prévia; rode `profile --action trust` de novo e confira."),
        )
    home = trust_home()
    entry = {"sha256": loaded.sha256, "trusted_at": datetime.now(UTC).isoformat()}

    def add(profiles: dict) -> bool:
        profiles[_key(loaded.path)] = entry
        return True

    _update_store(home, add)
    logs.event(_log, logging.INFO, "profile_trusted", sha256=loaded.sha256[:12], requires=loaded.requires is not None)
    return {**result, "trusted": True}


def untrust(path: Path | None, *, located: Located) -> dict:
    """Tira o perfil do store; não precisa que o arquivo ainda seja válido."""
    target = _target(path, located, "untrust")
    key = _key(target)
    removed = _update_store(trust_home(), lambda profiles: profiles.pop(key, None) is not None)
    logs.event(_log, logging.INFO, "profile_untrusted", removed=removed)
    return {"path": str(Path(target).resolve()), "trusted": False, "removed": removed}
