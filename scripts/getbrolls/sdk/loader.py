"""Descoberta e carga dos plugins em $GB_HOME/plugins, sempre com opt-in.

Plugin roda código Python com as permissões de quem usa a skill: não é sandbox.
Por isso só carrega o que foi habilitado por id, e o `enable` grava o hash da
pasta — conteúdo mudou, o plugin fica suspenso até novo `enable`, do mesmo jeito
que uma aprovação cai quando o trecho muda.
"""

import logging
import os
import sys
import types

from .. import _paths, effects, labels, logs
from .._paths import gb_home as home_dir
from . import guard, plugin_digest, plugin_state, registry_state
from .api import PluginApi
from .contracts import official_claim
from .guard import without_prefix
from .manifest import ManifestError, compatibility_problem, read_manifest

# Os nomes do hash e do pin moveram para `plugin_digest`; seguem lendo-se por aqui (mesmos objetos).
from .plugin_digest import (  # noqa: F401  # pylint: disable=unused-import
    BYTECODE_DIRNAME,
    DIGEST_FORMAT,
    MAP_OMITTED_NOTE,
    OFFICIAL_DIR,
    PREVIEW_FILES_MAX,
    ROOTS_FIELD,
    VCS_DIRNAMES,
    DigestLimitError,
    PinContent,
    _is_link,
    _pinned_files,
    check_expect,
    content_problem,
    content_reason,
    current_format,
    file_digests,
    files_diff,
    folder_digest,
    installer_bytecode,
    is_bytecode_name,
    nested_vcs,
    official_digest,
    permissions_added,
    permissions_diff,
    pin_digests,
    pin_entry,
    pinned_roots,
    shipped_bytecode,
)

# O `plugins.json` vive em `plugin_state`; segue lendo-se por aqui (mesmos objetos).
from .plugin_state import (  # noqa: F401  # pylint: disable=unused-import
    STATE_MAX_BYTES,
    read_state,
    state_path,
    write_state,
)

_log = logs.get("sdk")

NOT_SANDBOX = "O plugin roda código Python com as permissões dela: não é sandbox."
# Prévia de `enable` de plugin nunca pinado: `--yes` basta (o conteúdo não tem pin
# anterior com que comparar).
SANDBOX_NOTE = "Mostre o manifesto e as permissões à pessoa; com o ok dela, rode de novo com --yes. " + NOT_SANDBOX
# Prévia de `install`/`update` e de `enable` de um plugin cujo conteúdo mudou desde o
# pin: `--yes` sozinho é recusado; o sha256 desta prévia tem que voltar em `--expect`.
EXPECT_NOTE = (
    "Mostre o manifesto, as permissões e o que mudou à pessoa; com o ok dela, rode de novo com "
    "--yes --expect SHA256 (o sha256 desta prévia). " + NOT_SANDBOX
)
# Respostas de sucesso: nada para rodar de novo.
DONE_NOTE = "Pronto. " + NOT_SANDBOX
GB_PLUGINS_REASON = "desligado por GB_PLUGINS (a variável escolhe os plugins desta sessão, sem mexer no plugins.json)"
# O mesmo filtro, quando quem o pôs foi o teto `plugins` de um getbrolls.toml confiável.
PROFILE_PLUGINS_REASON = (
    "desligado pelo perfil do workspace (getbrolls.toml `plugins`), que escolhe os plugins desta sessão "
    "sem mexer no plugins.json"
)
SELECTION_REASONS = (GB_PLUGINS_REASON, PROFILE_PLUGINS_REASON)
# `GB_PLUGINS` só FILTRA: escolhe, entre os plugins habilitados com pin válido,
# os desta sessão. Nunca carrega um plugin sem pin, nunca habilitado ou adulterado.
GB_PLUGINS_UNPINNED_REASON = (
    "GB_PLUGINS só escolhe entre plugins já habilitados; habilite com plugins --action enable --id {id}."
)


def plugins_root():
    """Pasta dos plugins instalados: `$GB_HOME/plugins`."""
    return home_dir() / "plugins"


def _write_state(data):
    plugin_state.write_state(data)


# Pin de antes da 2.6.0 (sem `digest`): o sha256 antigo não amarra o conteúdo, então nem é conferido.
OLD_FORMAT_REASON = (
    "O pin deste plugin está no formato antigo do hash da pasta (de antes da 2.6.0), que não amarra o conteúdo; "
    "revise e habilite de novo com plugins --action enable --id {id} (o diff de arquivos da prévia pode vir "
    "vazio: o que mudou foi o formato do pin)."
)
OLD_FORMAT_NOTE = (
    "O pin anterior está no formato antigo do hash da pasta (de antes da 2.6.0): o sha256 mudou mesmo que "
    "nenhum arquivo tenha mudado, e o diff de arquivos pode vir vazio por isso. Confira a pasta do plugin "
    "antes de confirmar."
)


def env_selection():
    """Ids escolhidos por `GB_PLUGINS` (`off` = nenhum), ou `None` sem a variável."""
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
    pinned = state.get(manifest["id"])
    if not pinned:
        chosen = selection is not None and manifest["id"] in selection
        return "disabled", GB_PLUGINS_UNPINNED_REASON.format(id=manifest["id"]) if chosen else None
    if not current_format(pinned):
        return "suspended", OLD_FORMAT_REASON.format(id=manifest["id"])
    if pinned.get("sha256") != folder_digest(folder):
        return "suspended", "O conteúdo do plugin mudou desde o enable; revise e habilite de novo."
    if selection is not None and manifest["id"] not in selection:
        return "disabled", selection_reason()
    return "enabled", None


def selection_source():
    """Quem escolhe os plugins da sessão: `profile` (teto do getbrolls.toml), `GB_PLUGINS` ou `plugins.json`."""
    if env_selection() is None:
        return "plugins.json"
    return "profile" if _paths.from_profile("GB_PLUGINS") else "GB_PLUGINS"


def selection_reason():
    """Motivo de `disabled` para quem ficou fora da seleção da sessão, com a origem certa."""
    return PROFILE_PLUGINS_REASON if selection_source() == "profile" else GB_PLUGINS_REASON


def selection_blocks(plugin_id):
    """Aviso quando a seleção da sessão (perfil ou GB_PLUGINS) vai deixar `plugin_id` desligado; senão None."""
    selection = env_selection()
    if selection is None or plugin_id in selection:
        return None
    if selection_source() == "profile":
        return (
            f"O plugin {plugin_id} foi instalado e habilitado, mas o perfil do workspace (getbrolls.toml `plugins`) "
            "não o inclui: ele fica desligado nesta sessão até você acrescentá-lo lá (e confiar de novo com "
            "profile --action trust)."
        )
    return (
        f"O plugin {plugin_id} foi instalado e habilitado, mas GB_PLUGINS não o inclui: ele fica desligado "
        "nesta sessão até você acrescentá-lo à variável (ou tirá-la do ambiente)."
    )


def _invalid_row(ident, reason):
    return {"id": ident, "folder": ident, "version": None, "status": "invalid", "reason": reason, "contributes": {}}


# Uma pasta em `$GB_HOME/plugins/` com o id de um plugin oficial (sobra da versão de testes, cópia à mão):
# nunca carrega nem toma o lugar do oficial, e a linha diz como tirá-la.
IMPOSTOR_REASON = (
    "A pasta plugins/{name} usa um id reservado para plugin oficial do getbrolls ({official}): ela nunca carrega e "
    "não toma o lugar do oficial. Tire-a com plugins --action remove --id {name}."
)


def _user_manifest(folder):
    """`(manifesto, None)` ou `(None, motivo de invalid)` de uma pasta de `$GB_HOME/plugins`."""
    official = official_claim(folder.name)
    if official is not None:
        return None, IMPOSTOR_REASON.format(name=folder.name, official=official)
    try:
        return read_manifest(folder), None
    except ManifestError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, f"Não consegui ler o plugin {folder.name}: {type(exc).__name__}."


def _user_entry(folder, selection, state):
    """`(linha, pasta, manifesto ou None)` de uma pasta de `$GB_HOME/plugins`, sem rodar código."""
    manifest, invalid = _user_manifest(folder)
    if manifest is None:
        return _invalid_row(folder.name, invalid), folder, None
    try:
        nested = nested_vcs(folder)
        problem = None if nested is not None else content_problem(folder)
    except OSError as exc:
        nested, problem = f"({type(exc).__name__})", None
    if nested is not None:
        reason = (
            f"O plugin tem uma pasta de controle de versão aninhada ({nested}), fora do hash do pin; "
            "tire-a da pasta do plugin e habilite de novo."
        )
        return _invalid_row(manifest["id"], reason), folder, None
    if problem is not None:
        return _invalid_row(manifest["id"], content_reason(problem, "habilite de novo")), folder, None
    try:
        status, reason = _status(manifest, folder, selection, state)
    except DigestLimitError as exc:
        status, reason = "suspended", str(exc)
    except OSError as exc:
        reason = f"Não consegui conferir o conteúdo do plugin {manifest['id']}: {type(exc).__name__}."
        return _invalid_row(manifest["id"], reason), folder, None
    row = {
        "id": manifest["id"],
        "folder": folder.name,
        "version": manifest["version"],
        "status": status,
        "reason": reason,
        "contributes": {k: v for k, v in manifest["contributes"].items() if v},
    }
    return row, folder, manifest


def entries():
    """`(linha de inventário, pasta, manifesto ou None)` de cada pasta em `plugins/`,
    com status e motivo, sem rodar código de plugin. Os plugins oficiais ficam em `official`."""
    root = plugins_root()
    if not _paths.strict_is_dir(root):
        return []
    selection = env_selection()
    state = read_state()["enabled"]
    return [
        _user_entry(folder, selection, state)
        for folder in sorted(p for p in root.iterdir() if _paths.strict_is_dir(p) and not p.name.startswith((".", "_")))
    ]


def inventory():
    """As linhas de inventário de `entries()`."""
    return [row for row, _, _ in entries()]


def declared(kind):
    """Nomes de `contributes.<kind>` dos plugins habilitados."""
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
        # Rodar o plugin é o propósito do loader; só chega aqui com status "enabled"
        # (opt-in explícito) e hash conferido na hora. Uma linha `plugin.load` com o id.
        with effects.load_plugin(manifest["id"]):
            exec(code, module.__dict__)  # noqa: S102  # pylint: disable=exec-used  # rodar o plugin é o propósito do loader
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def register_plugin(folder, manifest, registry, pinned=None, *, official=False):
    """Roda a fonte do plugin e o `register(api)` dele contra `registry`, e confere o que ele registrou.

    `pinned` é a entrada do plugin em `plugins.json` (a carga normal passa; o `check`, não):
    dela sai o caminho resolvido de cada raiz de `permissions.paths` no enable. `official`: a
    ponte oficial, com a pasta de dados própria (`plugin-data/_official/<id>`)."""
    module = _import(folder, manifest)
    try:
        register = module.register
    except AttributeError:
        register = None
    if not callable(register):
        raise ManifestError(f"Plugin {manifest['id']}: {manifest['entry']} não define register(api).")
    api = PluginApi(manifest, registry, pin=pinned, official=official)
    register(api)
    api.finish()


def _pin_mismatch_reason(row, folder, pinned):
    """Reconfere o hash da pasta agora, na borda do `exec` — não reaproveita o
    hash que `entries()` calculou mais cedo — para fechar a janela entre listar
    e carregar (TOCTOU): conteúdo trocado nesse meio-tempo vira suspenso, não roda."""
    try:
        current = folder_digest(folder)
    except DigestLimitError as exc:
        return str(exc)
    except OSError as exc:
        return f"Não consegui reconferir o conteúdo do plugin antes de carregar: {type(exc).__name__}."
    entry = pinned.get(row["id"]) or {}
    if not current_format(entry) or entry.get("sha256") != current:
        return "O conteúdo do plugin mudou desde o enable; revise e habilite de novo."
    return None


def _load_one(row, folder, manifest, pinned, registry):
    if manifest is None or row["status"] != "enabled":
        logs.event(_log, logging.DEBUG, "plugin_skipped", plugin=row["id"], status=row["status"])
        return row

    def load():
        reason = _pin_mismatch_reason(row, folder, pinned)
        if reason is not None:
            return reason
        register_plugin(folder, manifest, registry, pinned.get(row["id"]) or {})
        return None

    # Código de plugin é de terceiro: qualquer falha (incl. SystemExit de um sys.exit()
    # no import, uma BaseException custom, um `__str__` hostil) desliga só aquele
    # plugin, nunca o processo. A razão guarda só o tipo — ou o texto de uma recusa do
    # core/`PluginError`, já saneado —, nunca `str(exc)` do plugin.
    outcome = guard.attempt(manifest["id"], load)
    failure = outcome.failure
    if failure is not None:
        registry.remove_owner(manifest["id"])
        logs.event(_log, logging.WARNING, "plugin_failed", plugin=row["id"], error=failure.type_name)
        reason = failure.text or f"O plugin falhou ao carregar, no import ou no register() ({failure.type_name})."
        return {**row, "status": "failed", "reason": reason}
    if outcome.value is not None:
        logs.event(_log, logging.DEBUG, "plugin_skipped", plugin=row["id"], status="suspended")
        return {**row, "status": "suspended", "reason": outcome.value}

    owned = registry.owned_by(manifest["id"])
    logs.event(
        _log,
        logging.INFO,
        "plugin_loaded",
        plugin=row["id"],
        version=row["version"],
        providers=",".join(manifest["contributes"]["providers"]) or "-",
        presets=",".join(manifest["contributes"]["presets"]) or "-",
        routes=len(owned["route"]),
        commands=len(owned["command"]),
        exporters=len(owned["exporter"]),
        resolvers=len(owned["resolver"]),
    )
    return row


def disable_bytecode():
    """Plugin que importa um módulo irmão (`sys.path` + `import`) faria o `import`
    normal gravar `__pycache__` na pasta dele — o que muda o hash e suspende o
    próprio plugin depois do primeiro uso. Desligado para o processo inteiro antes
    de rodar qualquer código de plugin (imports tardios, dentro de `search`, também)."""
    sys.dont_write_bytecode = True


def load_enabled(registry):
    """Monta o registro de plugins habilitados; nunca deixa um `plugins.json`
    corrompido ou uma pasta ilegível derrubar os built-ins — o pior caso é
    carregar nenhum plugin, registrado como `plugin_failed` com `plugin="-"`.

    `disable_bytecode()` só roda quando existe de fato uma linha `enabled` para
    carregar (na borda do primeiro `_load_one` que vai importar) — não no topo
    daqui incondicionalmente: numa instalação sem plugin nenhum,
    `providers`/`search`/`doctor` (que montam o registro assim mesmo) não
    tinham motivo pra desligar o cache de `.pyc` dos módulos do próprio core,
    importados de leve (lazy) depois."""
    try:
        rows = entries()
    except (ValueError, OSError) as exc:
        logs.event(_log, logging.WARNING, "plugin_failed", plugin="-", error=type(exc).__name__)
        return

    try:
        pinned = read_state()["enabled"]
    except (ValueError, OSError) as exc:
        logs.event(_log, logging.WARNING, "plugin_failed", plugin="-", error=type(exc).__name__)
        return

    for row, folder, manifest in rows:
        if row["status"] == "enabled":
            disable_bytecode()
        stored_row = _load_one(row, folder, manifest, pinned, registry)
        registry.plugins[stored_row["id"]] = stored_row


def find(plugin_id):
    """A entrada de `entries()` do plugin `plugin_id`; ausente vira `ValueError`."""
    for entry in entries():
        if entry[0]["id"] == plugin_id:
            return entry
    raise ValueError(f"Plugin {plugin_id} não encontrado em {labels.shown(plugins_root())}. Confira o nome da pasta.")


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
    registry = registry_state.current()
    if registry is not None:
        for row in registry.plugins.values():
            if name in (row.get("contributes") or {}).get(kind, []):
                return row
    try:
        rows = entries()
    except (ValueError, OSError):
        return None
    return next((row for row, _, manifest in rows if manifest and name in manifest["contributes"][kind]), None)


def status_phrase(row):
    """`que está <status>[: <motivo>]` para frases do tipo "a fonte X é do plugin Y, …".

    Sem ponto final (quem chama fecha a frase) e sem repetir o `Plugin <id>:` que o
    motivo às vezes já traz — um `reason` termina em "." e colá-lo antes de ". Rode…"
    ou "; removida…" dava "..", ".;" e "Plugin X: Plugin X:"."""
    reason = (row.get("reason") or "").strip()
    reason = without_prefix(row["id"], reason).rstrip(" .")
    return f"que está {row['status']}" + (f": {reason}" if reason else "")


def status_hint(row, default):
    """O que fazer com um plugin indisponível: fora de `GB_PLUGINS`, a saída é a
    variável — `enable` não resolve; quando o teto veio do `plugins` do getbrolls.toml,
    a saída é o perfil; nos outros casos, `default`."""
    if row.get("status") == "disabled" and row.get("reason") in SELECTION_REASONS:
        if row.get("reason") == PROFILE_PLUGINS_REASON or _paths.from_profile("GB_PLUGINS"):
            return (
                f"O perfil getbrolls.toml deste workspace não inclui {row['id']} em `plugins`; acrescente-o lá "
                "(e confie de novo com profile --action trust) para usá-lo nesta sessão; habilitar de novo não muda "
                "essa seleção."
            )
        return (
            f"Inclua {row['id']} em GB_PLUGINS (ou tire GB_PLUGINS do ambiente) para usá-lo nesta sessão; "
            "habilitar de novo não muda essa seleção."
        )
    return default


def permission_warnings(manifest):
    """Avisos (não recusas) para a prévia de enable/install e o `plugins check`:
    `permissions.env` com variável do core, de ferramenta do sistema, do espaço de nomes
    de outro plugin instalado ou fora do espaço de nomes do próprio plugin (o `.env` não
    a entrega); outro plugin instalado que pede uma variável do espaço de nomes deste;
    e `permissions.paths` que, neste sistema, é ampla demais e fica ignorada."""
    from .. import config
    from .api import ignored_paths

    plugin_id = manifest["id"]
    installed = config.installed_env()
    others = [other for other in installed if other != plugin_id]
    own = plugin_id.upper() + "_"
    warnings = []
    for key in manifest["permissions"]["env"]:
        owner = config.env_namespace_owner(key, [*others, plugin_id])
        if config.core_env_key(key):
            warnings.append(
                f"permissions.env pede {key}, uma variável do core do getbrolls: o plugin leria a "
                "configuração do core. Confira se isso faz sentido antes de confirmar."
            )
        elif config.toolchain_env_key(key):
            warnings.append(
                f"permissions.env pede {key}, uma variável que ferramentas do sistema leem (loader, git, ssh, "
                "proxy, certificados, shell): o .env nunca a entrega a um plugin. Confira antes de confirmar."
            )
        elif owner is not None and owner != plugin_id:
            warnings.append(
                f"permissions.env pede {key}, do espaço de nomes do plugin {owner} ({owner.upper()}_...): "
                "o plugin leria uma variável de outro plugin. Confira antes de confirmar."
            )
        elif not key.startswith(own):
            warnings.append(
                f"permissions.env pede {key}, fora do espaço de nomes {own}...: o .env não entrega essa "
                "variável ao plugin, só o ambiente do processo."
            )
    # O outro lado: um plugin já instalado que pede uma variável deste espaço de nomes
    # (ex.: `banco` pedindo `BANCO_HTTP_TOKEN` quando chega `banco_http`).
    warnings.extend(
        f"O plugin instalado {other} pede {key}, do espaço de nomes deste plugin ({own}...): "
        f"ele leria uma variável que é de {plugin_id}. Confira o {other} antes de confirmar."
        for other in sorted(others)
        for key in installed[other]
        if config.env_namespace_owner(key, [*others, plugin_id]) == plugin_id
    )
    warnings.extend(
        f"permissions.paths {raw} é, neste sistema, a raiz de um disco, um ponto de montagem, a pasta "
        "pessoal ou uma pasta acima dela: fica ignorada por api.local_file. Use uma pasta específica."
        for raw in ignored_paths(manifest)
    )
    return warnings


def manifest_summary(manifest):
    """Id, nome, versão, contribuições não vazias e permissões: o começo de toda prévia."""
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "contributes": {k: v for k, v in manifest["contributes"].items() if v},
        "permissions": manifest["permissions"],
    }


def plugin_preview(manifest, folder, sha=None):
    """O que a prévia de `enable`/`check` mostra: manifesto, permissões, sha256 e avisos."""
    preview = {**manifest_summary(manifest), "sha256": sha if sha is not None else folder_digest(folder)}
    warnings = permission_warnings(manifest)
    if warnings:
        preview["warnings"] = warnings
    return preview


def _pin_diff(pinned, manifest, files):
    """O `diff` da prévia de `enable` de um conteúdo que mudou desde o pin `pinned`:
    versão, permissões e arquivos, com uma nota quando o pin é do formato antigo do hash ou não guardou algo."""
    before = _pinned_files(pinned)
    too_many = pinned.get("files_omitted") is True or len(files) > plugin_digest.PIN_MAP_MAX_FILES
    previous = pinned.get("permissions")
    diff = {
        "version": {"from": pinned.get("version"), "to": manifest["version"]},
        "permissions": permissions_diff(previous if isinstance(previous, dict) else None, manifest["permissions"]),
        "files": files_diff(before, files) if before is not None and not too_many else None,
    }
    notes = [OLD_FORMAT_NOTE] if not current_format(pinned) else []
    if too_many:
        notes.append(MAP_OMITTED_NOTE.format(limit=plugin_digest.PIN_MAP_MAX_FILES))
    elif before is None:
        notes.append(
            "O pin anterior não guardou a lista de arquivos (versão antiga); confira a pasta do plugin "
            "antes de confirmar."
        )
    elif not isinstance(previous, dict):
        notes.append(
            "O pin anterior não guardou as permissões (versão antiga); confira permissions nesta prévia "
            "antes de confirmar."
        )
    if notes:
        diff["note"] = " ".join(notes)
    return diff


def _enable_preview(manifest, folder, content):
    """Prévia do `enable`: a de `plugin_preview`, os arquivos que o pin cobre e, quando o
    plugin declara `permissions.paths`, as raízes resolvidas que o pin vai gravar, tal e qual."""
    preview = plugin_preview(manifest, folder, content.sha)
    names = sorted(content.files)
    preview["files"] = {
        "count": len(names),
        "names": names[:PREVIEW_FILES_MAX],
        "truncated": len(names) > PREVIEW_FILES_MAX,
    }
    if manifest["permissions"]["paths"]:
        preview[ROOTS_FIELD] = content.roots
    return preview


def enable(plugin_id, confirm, expect=None):
    """Prévia (sem `confirm`) ou pin do conteúdo atual como habilitado.

    Plugin nunca pinado (ou pinado com o mesmo conteúdo): `--yes` basta, como
    sempre. Plugin com pin cujo conteúdo mudou (o `suspended` de "mudou desde o
    enable"): a prévia traz o `diff` dos arquivos contra o mapa guardado no pin, e
    confirmar exige `--expect SHA256` desta prévia — paridade com install/update,
    sem re-pinar às cegas o que estiver no disco."""
    row, folder, manifest = find(plugin_id)
    if manifest is None:
        raise ValueError(row["reason"])
    problem = compatibility_problem(manifest)
    if problem:
        raise ValueError(f"Plugin {plugin_id}: {problem}")
    sha, files = pin_digests(folder)
    roots = pinned_roots(manifest)
    preview = _enable_preview(manifest, folder, PinContent(sha, files, roots))
    state = read_state()
    # Pin atual ou, depois de um `disable`, o último pin guardado: desligar e mudar
    # a pasta não pode virar atalho para re-pinar às cegas só com `--yes`.
    pinned = state["enabled"].get(plugin_id) or state.get("last_pins", {}).get(plugin_id)
    changed = pinned is not None and (not current_format(pinned) or pinned.get("sha256") != sha)
    extra = {"diff": _pin_diff(pinned, manifest, files)} if pinned is not None and changed else {}
    if not confirm:
        return {"enabled": False, "plugin": preview, **extra, "note": EXPECT_NOTE if changed else SANDBOX_NOTE}
    if changed or expect:
        check_expect(expect, sha)
    state["enabled"][plugin_id] = pin_entry(manifest, sha, files, roots)
    state.get("last_pins", {}).pop(plugin_id, None)
    _write_state(state)
    registry_state.forget()
    logs.event(_log, logging.INFO, "plugin_enabled", plugin=plugin_id, version=manifest["version"])
    return {"enabled": True, "plugin": preview, **extra, "note": DONE_NOTE}


def pin(manifest, folder, origin=None, enabled=True, content=None):
    """Grava o pin de hash de `folder` como o plugin `manifest["id"]` e, vindo do
    `install`/`update`, a origem (`{"source", "commit"}`) em `plugins.json`.

    `enabled=False` (usado pelo `update` de um plugin que já estava desabilitado)
    só atualiza `sources`, sem criar/mudar a entrada em `enabled` — atualizar o
    conteúdo não liga de volta um plugin que a pessoa desligou de propósito. O
    `last_pins` dele, se houver, passa a ser este conteúdo: a pessoa já o aprovou
    no update (`--expect`), então religar depois é só `--yes`.

    `content` (`PinContent` já calculado) vem do `install`/`update`: o pin grava
    exatamente o conteúdo (e as raízes resolvidas) que a pessoa confirmou no staging,
    não um novo hash da pasta depois da troca — se alguém mexer nela no meio, o plugin
    fica suspenso."""
    plugin_id = manifest["id"]
    if content is None:
        content = PinContent(*pin_digests(folder), pinned_roots(manifest))
    state = read_state()
    entry = pin_entry(manifest, content.sha, content.files, content.roots)
    last_pins = state.get("last_pins", {})
    if enabled:
        state["enabled"][plugin_id] = entry
        last_pins.pop(plugin_id, None)
    elif plugin_id in last_pins:
        last_pins[plugin_id] = entry
    if origin is not None:
        state.setdefault("sources", {})[plugin_id] = origin
    _write_state(state)
    registry_state.forget()
    return content.sha


def disable(plugin_id):
    """Tira o pin do plugin, guardando-o em `last_pins`, e descarta o registro montado."""
    state = read_state()
    last = state["enabled"].pop(plugin_id, None)
    was = last is not None
    if was:
        # Guarda o último pin: um `enable` depois de a pasta mudar mostra o diff e
        # exige `--expect`; conteúdo igual religa só com `--yes`.
        state.setdefault("last_pins", {})[plugin_id] = last
        _write_state(state)
    registry_state.forget()
    logs.event(_log, logging.INFO, "plugin_disabled", plugin=plugin_id, was_enabled=was)
    return {"disabled": True, "id": plugin_id, "was_enabled": was}
