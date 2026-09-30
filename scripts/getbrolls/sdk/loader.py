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
from pathlib import Path
from typing import NamedTuple

from .. import logs
from ..ledger import atomic_write
from ..rules import home_dir
from . import guard, registry_state
from .api import PluginApi
from .files import TOP_LEVEL_VCS, counted_files, resolved_roots
from .files import is_link as _is_link  # `export_folder`/`export_plan` leem `loader._is_link`
from .guard import without_prefix
from .manifest import PERMISSION_KEYS, ManifestError, compatibility_problem, read_manifest

_log = logs.get("sdk")

NOT_SANDBOX = "O plugin roda código Python com as permissões dela: não é sandbox."
# Prévia de `enable` de plugin nunca pinado: `--yes` basta (o conteúdo não tem pin
# anterior com que comparar).
SANDBOX_NOTE = "Mostre o manifesto e as permissões à pessoa; com o ok dela, rode de novo com --yes. " + NOT_SANDBOX
# Prévia de `install`/`update` e de `enable` de um plugin cujo conteúdo mudou desde o
# pin: `--yes` sozinho é recusado; o sha256 desta prévia tem que voltar em `--expect`.
EXPECT_NOTE = (
    "Mostre o manifesto, as permissões e o que mudou à pessoa; com o ok dela, rode de novo com "
    "--yes --expect <sha256> (o sha256 desta prévia). " + NOT_SANDBOX
)
# Respostas de sucesso: nada para rodar de novo.
DONE_NOTE = "Pronto. " + NOT_SANDBOX
GB_PLUGINS_REASON = "desligado por GB_PLUGINS (a variável escolhe os plugins desta sessão, sem mexer no plugins.json)"
# `GB_PLUGINS` só FILTRA: escolhe, entre os plugins habilitados com pin válido,
# os desta sessão. Nunca carrega um plugin sem pin, nunca habilitado ou adulterado.
GB_PLUGINS_UNPINNED_REASON = (
    "GB_PLUGINS só escolhe entre plugins já habilitados; habilite com plugins --action enable --id {id}."
)


def plugins_root():
    """Pasta dos plugins instalados: `$GB_HOME/plugins`."""
    return home_dir() / "plugins"


def state_path():
    """`$GB_HOME/plugins.json`: pins, últimos pins e origens."""
    return home_dir() / "plugins.json"


# Bytecode ao lado da fonte: o `import` de um módulo irmão lê um `.pyc` de
# `__pycache__` (um `.pyc` com hash não conferido nem olha a fonte), então o código
# que roda deixaria de ser o que a pessoa revisou. Pasta com bytecode, ou com
# link simbólico (conteúdo fora do hash), fica `invalid` e nunca carrega.
BYTECODE_DIRNAME = "__pycache__"
BYTECODE_SUFFIXES = (".pyc", ".pyo")
# Pastas de VCS. Só o `.git` DE TOPO (`files.TOP_LEVEL_VCS`) fica fora do hash.
# Qualquer pasta de VCS em outro lugar (`vendor/.hg`, `.svn` aninhado, `.git` dentro
# de subpasta) torna o plugin inválido (`nested_vcs`), e `.hg`/`.svn` de topo contam
# no hash como qualquer arquivo.
VCS_DIRNAMES = frozenset({".git", ".hg", ".svn"})


def nested_vcs(folder):
    """Caminho relativo (texto) da primeira pasta de VCS fora do `.git` de topo, ou `None`."""
    for current, dirs, _files in os.walk(folder):
        rel = Path(current).relative_to(folder)
        for name in sorted(dirs):
            if name.casefold() in VCS_DIRNAMES and (rel.parts or name != TOP_LEVEL_VCS):
                return (rel / name).as_posix()
        if not rel.parts and TOP_LEVEL_VCS in dirs:
            dirs.remove(TOP_LEVEL_VCS)
    return None


def is_bytecode_name(name, is_dir):
    """`__pycache__` (pasta) ou `.pyc`/`.pyo` (arquivo), sem diferenciar maiúsculas."""
    folded = name.casefold()
    return folded == BYTECODE_DIRNAME if is_dir else folded.endswith(BYTECODE_SUFFIXES)


def content_problem(folder):
    """`("link" | "bytecode", caminho relativo)` do primeiro item que torna a pasta
    inválida, ou `None`. O `.git` de topo não é percorrido (é metadado de VCS, fora
    do hash); um link com esse nome ainda conta como link."""
    for current, dirs, files in os.walk(folder):
        here = Path(current)
        rel = here.relative_to(folder)
        for name in sorted([*dirs, *files]):
            path = here / name
            if _is_link(path):
                return "link", (rel / name).as_posix()
            if is_bytecode_name(name, name in dirs):
                return "bytecode", (rel / name).as_posix()
        if not rel.parts and TOP_LEVEL_VCS in dirs:
            dirs.remove(TOP_LEVEL_VCS)
    return None


def content_reason(problem, fix):
    """Motivo legível de `content_problem`; `fix` diz o que fazer depois de limpar a pasta."""
    kind, rel = problem
    if kind == "link":
        return (
            f"O plugin tem link simbólico ({rel}); o conteúdo apontado fica fora do hash do pin. "
            f"Copie os arquivos reais para a pasta e {fix}."
        )
    return (
        f"O plugin tem bytecode Python ({rel}); o Python pode rodá-lo no lugar da fonte revisada. "
        f"Apague a pasta __pycache__ (e qualquer .pyc/.pyo solto) da pasta do plugin — isso basta — e {fix}."
    )


# Tetos do hash da pasta: o `folder_digest` roda a cada comando, para cada
# plugin habilitado. Um arquivo enorme largado na pasta (um plugin que faz cache ao
# lado do `__file__`) não pode custar a memória/tempo dele a cada comando: passou do
# teto, o plugin fica suspenso com o motivo — nunca um MemoryError derrubando a CLI.
# O `install` já recusa mais de 200 MB; o total aqui dá folga para o que cresce depois.
DIGEST_MAX_FILE_BYTES = 200 * 1024 * 1024
DIGEST_MAX_TOTAL_BYTES = 400 * 1024 * 1024
_DIGEST_CHUNK = 1024 * 1024


class DigestLimitError(ValueError):
    """A pasta do plugin passou de um teto do hash; a mensagem diz qual."""


def _size_label(limit):
    return f"{limit // (1024 * 1024)} MB" if limit >= 1024 * 1024 else f"{limit} bytes"


def _hash_file(path, budget, *into):
    """Soma `path` em cada hash de `into` (e devolve os bytes lidos), em pedaços de
    1 MB, sem passar de `DIGEST_MAX_FILE_BYTES` nem do `budget` que resta do total."""
    limit = DIGEST_MAX_FILE_BYTES
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_DIGEST_CHUNK):
            total += len(chunk)
            if total > limit:
                raise DigestLimitError(
                    f"O plugin tem um arquivo acima do teto de {_size_label(limit)} para conferir o conteúdo "
                    f"({path.name}); tire-o da pasta do plugin (use api.data_dir) e habilite de novo."
                )
            if total > budget:
                raise DigestLimitError(
                    f"A pasta do plugin passa do teto de {_size_label(DIGEST_MAX_TOTAL_BYTES)} para conferir o "
                    "conteúdo; tire os arquivos grandes dela (use api.data_dir) e habilite de novo."
                )
            for hasher in into:
                hasher.update(chunk)
    return total


def folder_digest(folder):
    """Hash de todo arquivo da pasta, exceto lixo de SO (`files.JUNK_FILENAMES`) e o
    `.git` de topo — o resto conta sem exceção. Link simbólico e bytecode nem
    chegam aqui: tornam o plugin `invalid` antes (`content_problem`).

    Lê em pedaços, com teto por arquivo e total (`DigestLimitError`, um
    `ValueError`, quando passa): nunca carrega um arquivo inteiro na memória."""
    digest = hashlib.sha256()
    budget = DIGEST_MAX_TOTAL_BYTES
    for rel, path in counted_files(folder):
        digest.update(rel.as_posix().encode() + b"\0")
        budget -= _hash_file(path, budget, digest)
        digest.update(b"\0")
    return digest.hexdigest()


def file_digests(folder):
    """sha256 por arquivo, com o mesmo recorte e os mesmos tetos de `folder_digest` — base do diff do `update`."""
    result = {}
    budget = DIGEST_MAX_TOTAL_BYTES
    for rel, path in counted_files(folder):
        one = hashlib.sha256()
        budget -= _hash_file(path, budget, one)
        result[rel.as_posix()] = one.hexdigest()
    return result


def pin_digests(folder):
    """`(folder_digest, file_digests)` numa leitura só — o que o pin grava.

    O mapa por arquivo (caminho relativo POSIX → sha256) fica ao lado do pin em
    plugins.json: é o que deixa o `enable` de um plugin suspenso mostrar o que
    mudou. Mesmo recorte e mesmos tetos das duas funções acima."""
    whole = hashlib.sha256()
    files = {}
    budget = DIGEST_MAX_TOTAL_BYTES
    for rel, path in counted_files(folder):
        one = hashlib.sha256()
        whole.update(rel.as_posix().encode() + b"\0")
        budget -= _hash_file(path, budget, whole, one)
        whole.update(b"\0")
        files[rel.as_posix()] = one.hexdigest()
    return whole.hexdigest(), files


# Teto do mapa por arquivo do pin: o mesmo do `install` (`install.MAX_FILES`). Uma
# pasta copiada à mão não passa por esse teto; acima dele o pin guarda só o sha256
# total (`files_omitted`), senão o plugins.json — relido a cada comando — incharia.
PIN_MAP_MAX_FILES = 2000
# Quantos nomes de arquivo a prévia do `enable` lista (o total vem sempre), como no install.
PREVIEW_FILES_MAX = 50
MAP_OMITTED_NOTE = (
    "Plugin com muitos arquivos, diff omitido (mais de {limit}); confira a pasta do plugin antes de confirmar."
)


def permissions_diff(before, after):
    """Bloco `{from, to}` das permissões (network, env, paths) — o mesmo do diff do
    `update` e do `enable` de conteúdo mudado. `before` é `None` quando não se sabe."""
    return {"from": before, "to": after}


def permissions_added(before, after):
    """`{chave de permissions: itens de `after` que não estavam em `before`}`, para
    TODAS as chaves (`network`, `env`, `paths`, `project_write`). Uma pasta nova em
    `paths` conta mesmo quando só alarga uma que já existia (`~/Midia` no lugar de
    `~/Midia/sfx`): o que o plugin passa a alcançar mudou. `before` `None`
    (desconhecido) conta tudo de `after` como novo."""
    before = before or {}
    return {key: [item for item in after.get(key, []) if item not in before.get(key, [])] for key in PERMISSION_KEYS}


ROOTS_FIELD = "roots_resolved"


class PinContent(NamedTuple):
    """O que o pin grava do conteúdo confirmado: sha256 da pasta, mapa por arquivo e
    as raízes de `permissions.paths` resolvidas (`pinned_roots`)."""

    sha: str
    files: dict
    roots: dict


def pinned_roots(manifest):
    """`{entrada de permissions.paths como escrita: caminho resolvido}` das raízes que valem agora.

    Vai para o pin (`roots_resolved`), fora do sha256 do conteúdo: na carga, uma raiz
    que passou a resolver para outro lugar fica ignorada. Uma entrada que não resolve,
    ou cujo caminho não cabe em UTF-8, fica de fora (e fica ignorada na carga). Quem
    instala ou atualiza calcula isto antes de mexer em qualquer pasta."""
    try:
        pairs, _ignored = resolved_roots(manifest["permissions"].get("paths", []))
    except (OSError, RuntimeError, ValueError):
        return {}
    return {raw: str(resolved) for raw, resolved in pairs if _utf8_text(raw) and _utf8_text(str(resolved))}


def _utf8_text(text):
    """`text` cabe em UTF-8? Um caminho com bytes fora de UTF-8 (Linux) vira `str` com
    substitutos que o `plugins.json` não grava: fica fora do pin (e ignorado na carga)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def pin_entry(manifest, sha, files, roots=None):
    """Entrada de `enabled`/`last_pins`: versão, sha256, permissões aprovadas, as raízes
    resolvidas de `permissions.paths` (`roots`, ou calculadas agora) e o mapa por
    arquivo (se cabe no teto). As permissões guardadas deixam o `enable` de um conteúdo
    mudado mostrar o que elas eram antes."""
    entry = {
        "version": manifest["version"],
        "sha256": sha,
        "permissions": manifest["permissions"],
        ROOTS_FIELD: pinned_roots(manifest) if roots is None else roots,
    }
    if len(files) > PIN_MAP_MAX_FILES:
        entry["files_omitted"] = True
    else:
        entry["files"] = files
    return entry


def _pinned_files(entry):
    """Mapa por arquivo guardado no pin, ou `None` num pin antigo (ou malformado)."""
    files = entry.get("files")
    if isinstance(files, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return files
    return None


def files_diff(before, after):
    """`{added, removed, changed}` entre dois mapas caminho → sha256."""
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(name for name in set(before) & set(after) if before[name] != after[name]),
    }


def check_expect(expect, sha):
    """`--yes` sozinho não basta — o sha256 mostrado na prévia (do conteúdo
    já materializado, não de um manifesto solto) tem que ser reapresentado, ou
    a pessoa pode estar confirmando um conteúdo diferente do que viu."""
    if not expect:
        raise ValueError("--yes precisa de --expect <sha256>; rode a prévia (sem --yes) de novo e confira o valor.")
    if expect != sha:
        raise ValueError(
            "O sha256 de --expect não bate com o conteúdo agora: o valor foi copiado errado, ou a origem "
            "mudou desde a prévia. Rode a prévia de novo (sem --yes) e confirme com o sha256 dela."
        )


def _valid_pin(entry):
    return isinstance(entry, dict) and isinstance(entry.get("sha256"), str) and isinstance(entry.get("version"), str)


# Chaves opcionais da origem gravada em `sources.<id>`, todas `str` ou `null`: a
# ref e a subpasta de um repositório git e, para quem instala de um marketplace, o
# nome dele, o tier da entrada e o commit do índice usado.
ORIGIN_OPTIONAL_KEYS = ("ref", "subdir", "marketplace", "tier", "index_commit")


def _valid_origin(entry):
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("source"), str)
        and all(entry.get(key) is None or isinstance(entry.get(key), str) for key in ("commit", *ORIGIN_OPTIONAL_KEYS))
    )


def _valid_map(value, valid_entry):
    """`value` é um dict cujas entradas passam todas em `valid_entry`?"""
    return isinstance(value, dict) and all(valid_entry(entry) for entry in value.values())


def _valid_state(data):
    """`enabled` obrigatório; `last_pins` (o pin guardado pelo `disable`) e `sources`
    (origem/commit gravados pelo `plugins install`) são opcionais: um plugins.json de
    antes dessas versões continua válido sem eles."""
    return (
        _valid_map(data.get("enabled"), _valid_pin)
        and _valid_map(data.get("last_pins", {}), _valid_pin)
        and _valid_map(data.get("sources", {}), _valid_origin)
    )


def read_state():
    """`plugins.json` como dict (`{"enabled": {}}` sem arquivo); inválido vira `ValueError`."""
    path = state_path()
    if not path.exists():
        return {"enabled": {}}
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        data = None
    if isinstance(data, dict) and _valid_state(data):
        return data
    raise ValueError(f"plugins.json inválido em {path}. Corrija ou apague o arquivo para recomeçar sem plugins.")


def _write_state(data):
    home_dir().mkdir(parents=True, exist_ok=True)
    atomic_write(state_path(), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


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
    if pinned.get("sha256") != folder_digest(folder):
        return "suspended", "O conteúdo do plugin mudou desde o enable; revise e habilite de novo."
    if selection is not None and manifest["id"] not in selection:
        return "disabled", GB_PLUGINS_REASON
    return "enabled", None


def _invalid_row(ident, reason):
    return {"id": ident, "folder": ident, "version": None, "status": "invalid", "reason": reason, "contributes": {}}


def entries():
    """`(linha de inventário, pasta, manifesto ou None)` de cada pasta em `plugins/`,
    com status e motivo, sem rodar código de plugin."""
    root = plugins_root()
    if not root.is_dir():
        return []
    selection = env_selection()
    state = read_state()["enabled"]
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
            nested = nested_vcs(folder)
            problem = None if nested is not None else content_problem(folder)
        except OSError as exc:
            nested, problem = f"({type(exc).__name__})", None
        if nested is not None:
            reason = (
                f"O plugin tem uma pasta de controle de versão aninhada ({nested}), fora do hash do pin; "
                "tire-a da pasta do plugin e habilite de novo."
            )
            result.append((_invalid_row(manifest["id"], reason), folder, None))
            continue
        if problem is not None:
            result.append((_invalid_row(manifest["id"], content_reason(problem, "habilite de novo")), folder, None))
            continue
        try:
            status, reason = _status(manifest, folder, selection, state)
        except DigestLimitError as exc:
            status, reason = "suspended", str(exc)
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
        # (opt-in explícito) e hash conferido na hora.
        exec(code, module.__dict__)  # noqa: S102  # pylint: disable=exec-used  # rodar o plugin é o propósito do loader
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def register_plugin(folder, manifest, registry, pinned=None):
    """Roda a fonte do plugin e o `register(api)` dele contra `registry`, e confere o que ele registrou.

    `pinned` é a entrada do plugin em `plugins.json` (a carga normal passa; o `check`, não):
    dela sai o caminho resolvido de cada raiz de `permissions.paths` no enable."""
    module = _import(folder, manifest)
    try:
        register = module.register
    except AttributeError:
        register = None
    if not callable(register):
        raise ManifestError(f"Plugin {manifest['id']}: {manifest['entry']} não define register(api).")
    api = PluginApi(manifest, registry, pin=pinned)
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
    if entry.get("sha256") != current:
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
    variável — `enable` não resolve; nos outros casos, `default`."""
    if row.get("status") == "disabled" and row.get("reason") == GB_PLUGINS_REASON:
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
                f"permissions.env pede {key}, uma variável do core do Get B-rolls: o plugin leria a "
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
    versão, permissões e arquivos, com uma nota quando o pin antigo não guardou algo."""
    before = _pinned_files(pinned)
    too_many = pinned.get("files_omitted") is True or len(files) > PIN_MAP_MAX_FILES
    previous = pinned.get("permissions")
    diff = {
        "version": {"from": pinned.get("version"), "to": manifest["version"]},
        "permissions": permissions_diff(previous if isinstance(previous, dict) else None, manifest["permissions"]),
        "files": files_diff(before, files) if before is not None and not too_many else None,
    }
    if too_many:
        diff["note"] = MAP_OMITTED_NOTE.format(limit=PIN_MAP_MAX_FILES)
    elif before is None:
        diff["note"] = (
            "O pin anterior não guardou a lista de arquivos (versão antiga); confira a pasta do plugin "
            "antes de confirmar."
        )
    elif not isinstance(previous, dict):
        diff["note"] = (
            "O pin anterior não guardou as permissões (versão antiga); confira permissions nesta prévia "
            "antes de confirmar."
        )
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
    confirmar exige `--expect <sha256>` desta prévia — paridade com install/update,
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
    changed = pinned is not None and pinned.get("sha256") != sha
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
