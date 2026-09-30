"""`plugins --action remove`: tirar um plugin instalado, em dois passos.

Sem `--yes` só mostra o que sai: a pasta em `plugins/<id>` e o que o
`plugins.json` guarda do plugin (pin em `enabled`, último pin em `last_pins`,
origem em `sources`). Com `--yes`, apaga a pasta e essas três entradas. Não há
`--expect`: remover não aprova conteúdo nenhum. `plugin-data/<id>` (estado e
cache do plugin) fica, e a resposta diz onde está.

Uma pasta que é link simbólico (ou junction) perde só o link; o alvo não é
tocado. Uma pasta de verdade é trocada de lugar (`.removed-<epoch>-<uuid>`)
antes de ser apagada, para `plugins/<id>` sumir de uma vez; um resto desses é
apagado pela varredura do install, nunca devolvido ao lugar.
"""

import logging
import os

from .. import logs
from ..rules import home_dir
from ..runtime import force_rmtree
from . import loader, registry_state
from .contracts import NAME_RE
from .files import is_link
from .install import new_removed_staging_name

_log = logs.get("sdk")

REMOVE_NOTE = "Mostre à pessoa o que sai; com o ok dela, rode de novo com --yes."


def _plugin_row(plugin_id, folder):
    """`{id, folder, version, status}` da pasta instalada (sem rodar código), ou `None` sem pasta."""
    if not os.path.lexists(folder):
        return None
    for row, entry_folder, _manifest in loader.entries():
        if entry_folder.name == plugin_id:
            return {key: row[key] for key in ("id", "folder", "version", "status")}
    return {"id": plugin_id, "folder": plugin_id, "version": None, "status": "invalid"}


def _unlink(folder):
    """Tira só o link `folder` (no Windows, uma junction sai com `rmdir`)."""
    try:
        folder.unlink()
    except (IsADirectoryError, PermissionError):
        folder.rmdir()


def _delete_folder(folder):
    if is_link(folder):
        _unlink(folder)
        return
    retired = folder.with_name(new_removed_staging_name())
    folder.replace(retired)
    force_rmtree(retired)


def remove(plugin_id, confirm):
    """Prévia (sem `confirm`) ou remoção do plugin `plugin_id`: pasta e estado em `plugins.json`."""
    if not isinstance(plugin_id, str) or not NAME_RE.fullmatch(plugin_id):
        raise ValueError(f"Id de plugin inválido: {str(plugin_id)[:80]!r}.")
    state = loader.read_state()  # plugins.json corrompido recusa antes de qualquer mutação.
    folder = loader.plugins_root() / plugin_id
    plugin = _plugin_row(plugin_id, folder)
    in_state = {
        "enabled": plugin_id in state.get("enabled", {}),
        "last_pin": plugin_id in state.get("last_pins", {}),
        "source": state.get("sources", {}).get(plugin_id),
    }
    if plugin is None and not (in_state["enabled"] or in_state["last_pin"] or in_state["source"] is not None):
        raise ValueError(f"Plugin {plugin_id} não encontrado em {loader.plugins_root()} nem em plugins.json.")
    data_dir = home_dir() / "plugin-data" / plugin_id
    kept = {"plugin_data": str(data_dir) if os.path.lexists(data_dir) else None}
    if not confirm:
        return {"removed": False, "plugin": plugin, "state": in_state, "kept": kept, "note": REMOVE_NOTE}
    if plugin is not None:
        try:
            _delete_folder(folder)
        except OSError as exc:
            raise ValueError(
                f"Não consegui tirar {folder} ({type(exc).__name__}); nada foi mudado no plugins.json."
            ) from exc
    for key in ("enabled", "last_pins", "sources"):
        state.get(key, {}).pop(plugin_id, None)
    loader.write_state(state)
    registry_state.forget()
    logs.event(_log, logging.INFO, "plugin_removed", plugin=plugin_id)
    return {"removed": True, "plugin": plugin, "state": in_state, "kept": kept, "note": "Pronto."}
