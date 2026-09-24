"""Descoberta e carga dos plugins em $GB_HOME/plugins, sempre com opt-in.

Plugin roda código Python com as permissões de quem usa a skill: não é sandbox.
Por isso só carrega o que foi habilitado por id, e o `enable` grava o hash da
pasta — conteúdo mudou, o plugin fica suspenso até novo `enable`, do mesmo jeito
que uma aprovação cai quando o trecho muda.
"""

import hashlib
import json
import logging
import os
import sys
import types

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


# Arquivo de lixo de SO que aparece sozinho (Finder/Explorer abriram a pasta) e nunca
# é lido para rodar o plugin: contá-lo no hash suspende o plugin por um arquivo que
# ninguém escreveu de propósito. `__pycache__`/`.pyc`, ao contrário, continuam
# contando — são o vetor do Finding 1 (round 1): um bytecode plantado tem que mudar
# o hash, mesmo nunca sendo lido, porque `_import` sempre compila a fonte na hora.
JUNK_FILENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})
# Pasta de VCS que sobra de um `git pull`/clone dentro da pasta do plugin: metadado
# do controle de versão, não conteúdo que `_import` executa.
VCS_DIRNAMES = frozenset({".git", ".hg", ".svn"})


def _counted_files(folder):
    """(caminho relativo, caminho) de cada arquivo que entra no hash, em ordem estável."""
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder)
        if rel.name in JUNK_FILENAMES or VCS_DIRNAMES & set(rel.parts[:-1]):
            continue
        yield rel, path


def folder_digest(folder):
    """Hash de todo arquivo da pasta, exceto lixo de SO (`JUNK_FILENAMES`) e o
    metadado de dentro de uma pasta de VCS (`VCS_DIRNAMES`) — o resto, incluindo
    `__pycache__`/`.pyc`, conta sem exceção (ver comentário de `JUNK_FILENAMES`)."""
    digest = hashlib.sha256()
    for rel, path in _counted_files(folder):
        digest.update(rel.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def file_digests(folder):
    """sha256 por arquivo, com o mesmo recorte de `folder_digest` — base do diff do `update`."""
    return {rel.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for rel, path in _counted_files(folder)}


def _valid_pin(entry):
    return isinstance(entry, dict) and isinstance(entry.get("sha256"), str) and isinstance(entry.get("version"), str)


def _valid_origin(entry):
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("source"), str)
        and (entry.get("commit") is None or isinstance(entry.get("commit"), str))
    )


def read_state():
    path = state_path()
    if not path.exists():
        return {"enabled": {}}
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        data = None
    if isinstance(data, dict):
        enabled = data.get("enabled")
        # `sources` (origem/commit gravados pelo `plugins install`) é opcional: um
        # plugins.json de antes desta versão continua válido sem ela.
        sources = data.get("sources", {})
        if (
            isinstance(enabled, dict)
            and all(_valid_pin(entry) for entry in enabled.values())
            and isinstance(sources, dict)
            and all(_valid_origin(entry) for entry in sources.values())
        ):
            return data
    raise ValueError(f"plugins.json inválido em {path}. Corrija ou apague o arquivo para recomeçar sem plugins.")


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


def _invalid_row(ident, reason):
    return {"id": ident, "folder": ident, "version": None, "status": "invalid", "reason": reason, "contributes": {}}


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
            result.append((_invalid_row(folder.name, str(exc)), folder, None))
            continue
        except OSError as exc:
            reason = f"Não consegui ler o plugin {folder.name}: {type(exc).__name__}."
            result.append((_invalid_row(folder.name, reason), folder, None))
            continue
        try:
            status, reason = _status(manifest, folder, selection, state)
        except OSError as exc:
            reason = f"Não consegui conferir o conteúdo do plugin {manifest['id']}: {type(exc).__name__}."
            result.append((_invalid_row(manifest["id"], reason), folder, None))
            continue
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
    """Compila e roda a fonte do plugin na hora — nunca via `importlib`/`exec_module`.

    O loader de `import` normal lê (e pode escrever) um `.pyc` em `__pycache__`
    ao lado da fonte; um `.pyc` plantado ali rodaria sem que o `sha256` do pin
    tivesse motivo pra mudar (bytecode não é a fonte). Compilar `source_bytes`
    direto e rodar com `exec()` nunca lê nem escreve bytecode: só a fonte —
    já coberta pelo `folder_digest` — decide o que roda.
    """
    entry_path = folder / manifest["entry"]
    source = entry_path.read_bytes()
    name = f"getbrolls_plugins.{manifest['id']}"
    module = types.ModuleType(name)
    module.__file__ = str(entry_path)
    sys.modules[name] = module
    try:
        code = compile(source, str(entry_path), "exec", dont_inherit=True)
        exec(code, module.__dict__)  # noqa: S102 - rodar o plugin é o propósito do loader; só chega aqui com status "enabled" (opt-in explícito) e hash conferido na hora
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


def _pin_mismatch_reason(row, folder, pinned):
    """Reconfere o hash da pasta agora, na borda do `exec` — não reaproveita o
    hash que `entries()` calculou mais cedo — para fechar a janela entre listar
    e carregar (TOCTOU): conteúdo trocado nesse meio-tempo vira suspenso, não roda."""
    try:
        current = folder_digest(folder)
    except OSError as exc:
        return f"Não consegui reconferir o conteúdo do plugin antes de carregar: {type(exc).__name__}."
    pin = pinned.get(row["id"]) or {}
    if pin.get("sha256") != current:
        return "O conteúdo do plugin mudou desde o enable; revise e habilite de novo."
    return None


def _load_one(row, folder, manifest, pinned, registry):
    if manifest is None or row["status"] != "enabled":
        logs.event(_log, logging.DEBUG, "plugin_skipped", plugin=row["id"], status=row["status"])
        return row

    if pinned is not None:
        reason = _pin_mismatch_reason(row, folder, pinned)
        if reason is not None:
            logs.event(_log, logging.DEBUG, "plugin_skipped", plugin=row["id"], status="suspended")
            return {**row, "status": "suspended", "reason": reason}

    try:
        _register(folder, manifest, registry)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - código de plugin é de terceiro: qualquer falha (incl. SystemExit de um sys.exit() no import) desliga só aquele plugin, nunca o processo; KeyboardInterrupt continua propagando
        registry.remove_owner(manifest["id"])
        logs.event(_log, logging.WARNING, "plugin_failed", plugin=row["id"], error=type(exc).__name__)
        return {**row, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}

    logs.event(
        _log,
        logging.INFO,
        "plugin_loaded",
        plugin=row["id"],
        version=row["version"],
        providers=",".join(manifest["contributes"]["providers"]) or "-",
        presets=",".join(manifest["contributes"]["presets"]) or "-",
    )
    return row


def load_enabled(registry):
    """Monta o registro de plugins habilitados; nunca deixa um `plugins.json`
    corrompido ou uma pasta ilegível derrubar os built-ins — o pior caso é
    carregar nenhum plugin, registrado como `plugin_failed` com `plugin="-"`."""
    try:
        rows = entries()
    except (ValueError, OSError) as exc:
        logs.event(_log, logging.WARNING, "plugin_failed", plugin="-", error=type(exc).__name__)
        return

    selection = env_selection()
    pinned = None
    if selection is None:
        try:
            pinned = read_state()["enabled"]
        except (ValueError, OSError) as exc:
            logs.event(_log, logging.WARNING, "plugin_failed", plugin="-", error=type(exc).__name__)
            return

    for row, folder, manifest in rows:
        stored_row = _load_one(row, folder, manifest, pinned, registry)
        registry.plugins[stored_row["id"]] = stored_row


def find(plugin_id):
    for entry in entries():
        if entry[0]["id"] == plugin_id:
            return entry
    raise ValueError(f"Plugin {plugin_id} não encontrado em {plugins_root()}. Confira o nome da pasta.")


def declared_by(name, kind="providers"):
    """Linha de status do plugin instalado que declara `name` em
    `contributes.<kind>` (providers ou presets), ou `None` se nenhum declarar.

    Prefere `registry.built_registry().plugins`, quando o registro já foi
    montado por quem chamou isto: reflete o carregamento de verdade (um plugin
    cujo pin bate mas cujo `register()` estourou aparece como "failed", não
    "enabled"). Só olha esse registro se ele já existe — nunca monta um do
    zero aqui, o que executaria código de plugin como efeito colateral de uma
    simples pergunta "quem declara esse nome?". Sem registro montado (ou sem
    a linha nele), cai para o inventário pré-carga (`entries()`, só
    manifesto/pin); um `plugins.json` corrompido nesse fallback vira "não sei
    dizer o motivo" (`None`), não uma queda de quem chamou."""
    from .registry import built_registry

    registry = built_registry()
    if registry is not None:
        for row in registry.plugins.values():
            if name in (row.get("contributes") or {}).get(kind, []):
                return row
    try:
        rows = entries()
    except (ValueError, OSError):
        return None
    return next((row for row, _, manifest in rows if manifest and name in manifest["contributes"][kind]), None)


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


def pin(manifest, folder, origin=None, enable=True):
    """Grava o pin de hash de `folder` como o plugin `manifest["id"]` e, vindo do
    `install`/`update`, a origem (`{"source", "commit"}`) em `plugins.json`.

    `enable=False` (usado pelo `update` de um plugin que já estava desabilitado)
    só atualiza `sources`, sem criar/mudar a entrada em `enabled` — atualizar o
    conteúdo não liga de volta um plugin que a pessoa desligou de propósito."""
    from .registry import reset_registry

    plugin_id = manifest["id"]
    sha = folder_digest(folder)
    state = read_state()
    if enable:
        state["enabled"][plugin_id] = {"version": manifest["version"], "sha256": sha}
    if origin is not None:
        state.setdefault("sources", {})[plugin_id] = origin
    _write_state(state)
    reset_registry()
    return sha


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
    try:
        _register(folder, manifest, registry)
    except (
        Exception,
        SystemExit,
    ) as exc:  # código de plugin é de terceiro: sem isto, um bug do plugin (RuntimeError, KeyError...) chegava cru em `runtime.audited()` e virava INTERNAL_ERROR/exit 3 — "erro interno" nosso, escondendo o motivo real. Reembrulhado em ValueError, vira exit 2 com o tipo e a mensagem visíveis; KeyboardInterrupt continua propagando.
        raise ValueError(f"Plugin {manifest['id']}: {type(exc).__name__}: {exc}") from exc
    return {"ok": True, **_preview(manifest, folder), "manifest_file": MANIFEST_NAME}
