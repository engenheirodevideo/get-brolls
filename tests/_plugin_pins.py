"""Habilita plugins de teste com o pin real do conteúdo em disco.

`GB_PLUGINS` só filtra plugins já habilitados com pin válido: um teste que
escreve um plugin e quer vê-lo carregado passa por aqui antes, exatamente como a
pessoa faria com `plugins --action enable --yes` (o mesmo `loader.pin`).
"""

import os
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
import _paths  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.sdk import loader
from getbrolls.sdk.manifest import read_manifest


def pin_plugins(*plugin_ids, home=None):
    """Pina (e habilita) cada id com o conteúdo atual de `<GB_HOME>/plugins/<id>`."""
    env = {"GB_HOME": str(home)} if home is not None else {}
    with patch.dict(os.environ, env):
        for plugin_id in plugin_ids:
            folder = loader.plugins_root() / plugin_id
            loader.pin(read_manifest(folder), folder)
