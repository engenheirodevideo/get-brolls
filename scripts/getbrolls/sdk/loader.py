"""Descoberta e carga dos plugins em $GB_HOME/plugins, sempre com opt-in.

Plugin roda código Python com as permissões de quem usa a skill: não é sandbox.
Por isso só carrega o que foi habilitado por id, e o `enable` grava o hash da
pasta — conteúdo mudou, o plugin fica suspenso até novo `enable`, do mesmo jeito
que uma aprovação cai quando o trecho muda.
"""

import hashlib
import importlib.util
import json
import logging
import os
import sys

from .. import logs
from ..ledger import atomic_write
from ..rules import home_dir
from .api import PluginApi
from .manifest import MANIFEST_NAME, ManifestError, compatibility_problem, read_manifest

_log = logs.get("sdk")

SANDBOX_NOTE = (
    "Mostre o manifesto e as permissões à pessoa; com o ok dela, rode de novo com --yes. "
    "O plugin roda código Python com as permissões dela: não é sandbox."
)


def plugins_root():
    return home_dir() / "plugins"


def state_path():
    return home_dir() / "plugins.json"


def folder_digest(folder):
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder)
        if "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue
        digest.update(rel.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def read_state():
    path = state_path()
    if not path.exists():
        return {"enabled": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), dict):
        raise ValueError(f"plugins.json inválido em {path}. Corrija ou apague o arquivo para recomeçar sem plugins.")
    return data


def _write_state(data):
    home_dir().mkdir(parents=True, exist_ok=True)
    atomic_write(state_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def env_selection():
    raw = os.environ.get("GB_PLUGINS")
    if raw is None:
        return None
    if raw.strip().lower() == "off":
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _status(manifest, folder, selection, state):
    problem = compatibility_problem(manifest)
    if problem:
        return "incompatible", problem
    if selection is not None:
        return ("enabled", None) if manifest["id"] in selection else ("disabled", None)
    pinned = state.get(manifest["id"])
    if not pinned:
        return "disabled", None
    if pinned.get("sha256") != folder_digest(folder):
        return "suspended", "O conteúdo do plugin mudou desde o enable; revise e habilite de novo."
    return "enabled", None


def entries():
    root = plugins_root()
    if not root.is_dir():
        return []
    selection = env_selection()
    state = read_state()["enabled"] if selection is None else {}
    result = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        try:
            manifest = read_manifest(folder)
        except ManifestError as exc:
            row = {
                "id": folder.name,
                "folder": folder.name,
                "version": None,
                "status": "invalid",
                "reason": str(exc),
                "contributes": {},
            }
            result.append((row, folder, None))
            continue
        status, reason = _status(manifest, folder, selection, state)
        row = {
            "id": manifest["id"],
            "folder": folder.name,
            "version": manifest["version"],
            "status": status,
            "reason": reason,
            "contributes": {k: v for k, v in manifest["contributes"].items() if v},
        }
        result.append((row, folder, manifest))
    return result


def inventory():
    return [row for row, _, _ in entries()]


def declared(kind):
    return [
        name
        for row, _, manifest in entries()
        if manifest and row["status"] == "enabled"
        for name in manifest["contributes"][kind]
    ]


def _import(folder, manifest):
    name = f"getbrolls_plugins.{manifest['id']}"
    spec = importlib.util.spec_from_file_location(name, folder / manifest["entry"])
    if spec is None or spec.loader is None:
        raise ManifestError(f"Plugin {manifest['id']}: não consegui carregar {manifest['entry']}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _register(folder, manifest, registry):
    module = _import(folder, manifest)
    register = getattr(module, "register", None)
    if not callable(register):
        raise ManifestError(f"Plugin {manifest['id']}: {manifest['entry']} não define register(api).")
    api = PluginApi(manifest, registry)
    register(api)
    api.finish()


def load_enabled(registry):
    for row, folder, manifest in entries():
        stored_row = row
        if manifest is not None and row["status"] == "enabled":
            try:
                _register(folder, manifest, registry)
            except Exception as exc:  # noqa: BLE001 - código de plugin é de terceiro: qualquer falha desliga só aquele plugin
                registry.remove_owner(manifest["id"])
                stored_row = {**row, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
                logs.event(_log, logging.WARNING, "plugin_failed", plugin=row["id"], error=type(exc).__name__)
            else:
                logs.event(
                    _log,
                    logging.INFO,
                    "plugin_loaded",
                    plugin=row["id"],
                    version=row["version"],
                    providers=",".join(manifest["contributes"]["providers"]) or "-",
                    presets=",".join(manifest["contributes"]["presets"]) or "-",
                )
        else:
            logs.event(_log, logging.DEBUG, "plugin_skipped", plugin=row["id"], status=row["status"])
        registry.plugins[stored_row["id"]] = stored_row


def find(plugin_id):
    for entry in entries():
        if entry[0]["id"] == plugin_id:
            return entry
    raise ValueError(f"Plugin {plugin_id} não encontrado em {plugins_root()}. Confira o nome da pasta.")


def _preview(manifest, folder):
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "contributes": {k: v for k, v in manifest["contributes"].items() if v},
        "permissions": manifest["permissions"],
        "sha256": folder_digest(folder),
    }


def enable(plugin_id, confirm):
    from .registry import reset_registry

    row, folder, manifest = find(plugin_id)
    if manifest is None:
        raise ValueError(row["reason"])
    problem = compatibility_problem(manifest)
    if problem:
        raise ValueError(f"Plugin {plugin_id}: {problem}")
    preview = _preview(manifest, folder)
    if not confirm:
        return {"enabled": False, "plugin": preview, "note": SANDBOX_NOTE}
    state = read_state()
    state["enabled"][plugin_id] = {"version": manifest["version"], "sha256": preview["sha256"]}
    _write_state(state)
    reset_registry()
    logs.event(_log, logging.INFO, "plugin_enabled", plugin=plugin_id, version=manifest["version"])
    return {"enabled": True, "plugin": preview, "note": SANDBOX_NOTE}


def disable(plugin_id):
    from .registry import reset_registry

    state = read_state()
    was = state["enabled"].pop(plugin_id, None) is not None
    if was:
        _write_state(state)
    reset_registry()
    logs.event(_log, logging.INFO, "plugin_disabled", plugin=plugin_id, was_enabled=was)
    return {"disabled": True, "id": plugin_id, "was_enabled": was}


def trial_load(folder):
    """`plugins check`: valida manifesto e executa o register() contra um registro
    descartável com os built-ins, para pegar colisão de nome sem habilitar nada."""
    from .. import presets, providers
    from .registry import Registry

    manifest = read_manifest(folder, require_folder_match=False)
    problem = compatibility_problem(manifest)
    if problem:
        raise ValueError(f"Plugin {manifest['id']}: {problem}")
    registry = Registry()
    providers.register_builtins(registry)
    presets.register_builtins(registry)
    _register(folder, manifest, registry)
    return {"ok": True, **_preview(manifest, folder), "manifest_file": MANIFEST_NAME}
