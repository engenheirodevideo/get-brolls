"""Schemas públicos do core, lidos uma vez dos dados do pacote."""

import functools
import json

from .. import _paths

NAMES = ("brief", "candidate")


@functools.cache
def load(name):
    """O schema `name` (`brief` ou `candidate`) como dict, lido uma vez por processo."""
    if name not in NAMES:
        raise ValueError(f"Schema desconhecido: {name}")
    return json.loads(_paths.data_path("schemas", f"{name}.schema.json").read_text(encoding="utf-8"))
