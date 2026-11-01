"""Origem oficial: os plugins que vêm no pacote (`plugins_official/`), confiados pelo pin do core.

Um plugin oficial não passa por `enable`, não grava nada no `plugins.json` e não lê o estado de lá: a
confiança é o `folder_digest` da pasta em `official_pins.PINS`, uma constante gerada do core. A pasta é
conferida inteira (manifesto pela porta oficial, link, bytecode além do que o instalador compila, pasta de
VCS, hash) e de novo na borda do `exec`. A ponte só roda sob demanda (`load_official`), pelos comandos que
usam um ambiente; os outros comandos nunca carregam código oficial. A seleção da sessão (`GB_PLUGINS`,
perfil) pode desligá-la.
"""

import logging

from .. import _paths, logs
from ..errors import DataRootError
from . import guard, loader
from .manifest import ManifestError, compatibility_problem, read_manifest
from .official_pins import PINS

_log = logs.get("sdk")

OFFICIAL_BROKEN = (
    "A ponte oficial {ident} não confere com o pin do getbrolls ({why}) e não roda. " + _paths.DATA_REINSTALL
)


def official_root():
    """Onde moram os plugins oficiais: `plugins_official/` do checkout ou `_data/plugins_official/` do wheel."""
    return _paths.data_root() / loader.OFFICIAL_DIR


def _content_why(folder):
    if loader.nested_vcs(folder) is not None:
        return "há uma pasta de controle de versão dentro dela"
    problem = loader.content_problem(folder, loader.installer_bytecode(folder) or frozenset())
    if problem is not None:
        return "tem link" if problem[0] == "link" else "tem bytecode que o instalador não gera"
    return None


def check_folder(ident, folder):
    """`(motivo, manifesto)`: por que a pasta oficial não vale (motivo `None` quando vale) e o manifesto."""
    if not _paths.strict_is_dir(folder):
        return "a pasta não está no pacote", None
    try:
        manifest = read_manifest(folder, official=True)
    except ManifestError:
        return "o manifesto não passa", None
    if manifest["id"] != ident or manifest["contributes"]["environments"] != [ident]:
        return "o manifesto não é o da ponte", None
    why = _content_why(folder) or (None if loader.official_digest(folder) == PINS[ident] else "um arquivo mudou")
    return (why, None) if why else (None, manifest)


def _row(ident, selection):
    """`(linha, pasta, manifesto ou None)` do plugin oficial `ident`, sem rodar código nem ler o `plugins.json`."""
    try:
        folder = official_root() / ident
    except DataRootError:
        folder = None
    try:
        why, manifest = ("faltam os dados do pacote", None) if folder is None else check_folder(ident, folder)
    except (OSError, loader.DigestLimitError) as exc:
        why, manifest = f"não consegui ler: {type(exc).__name__}", None
    if manifest is None:
        reason = OFFICIAL_BROKEN.format(ident=ident, why=why)
        row = {"id": ident, "folder": ident, "version": None, "status": "invalid", "reason": reason, "contributes": {}}
        return {**row, "official": True}, folder, None
    problem = compatibility_problem(manifest)
    if problem:
        status, reason = "incompatible", problem
    elif selection is not None and ident not in selection:
        status, reason = "disabled", loader.selection_reason()
    else:
        status, reason = "enabled", None
    row = {
        "id": ident,
        "folder": ident,
        "version": manifest["version"],
        "status": status,
        "reason": reason,
        "contributes": {k: v for k, v in manifest["contributes"].items() if v},
        "official": True,
    }
    return row, folder, manifest


def entries():
    """`(linha, pasta, manifesto ou None)` de cada plugin oficial (`official_pins.PINS`), sem rodar código."""
    selection = loader.env_selection()
    return [_row(ident, selection) for ident in sorted(PINS)]


def load(registry, ident):
    """Carrega a ponte oficial `ident` em `registry` e devolve a linha dela; nada roda se ela não está `enabled`.

    O pin é conferido de novo na borda do `exec` (TOCTOU), como `loader._pin_mismatch_reason` faz com os
    de terceiros; código oficial que falha desliga só a ponte, nunca o processo."""
    row, folder, _manifest = _row(ident, loader.env_selection())
    if row["status"] == "enabled" and folder is not None and registry.environment(ident) is None:

        def run():
            why, manifest = check_folder(ident, folder)
            if manifest is None:
                return why
            loader.register_plugin(folder, manifest, registry, official=True)
            return None

        outcome = guard.attempt(ident, run)
        if outcome.failure is not None:
            registry.remove_owner(ident)
            logs.event(_log, logging.WARNING, "plugin_failed", plugin=ident, error=outcome.failure.type_name)
            row = {**row, "status": "failed", "reason": outcome.failure.text or f"A ponte {ident} falhou ao carregar."}
        elif outcome.value is not None:
            row = {**row, "status": "invalid", "reason": OFFICIAL_BROKEN.format(ident=ident, why=outcome.value)}
        else:
            logs.event(_log, logging.INFO, "plugin_loaded", plugin=ident, version=row["version"], official=True)
    registry.plugins[ident] = row
    return row
