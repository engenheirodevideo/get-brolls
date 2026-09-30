"""Schemas públicos do core, lidos uma vez dos dados do pacote."""

import functools
import json

from .. import _paths

NAMES = (
    "brief",
    "candidate",
    "analysis_index",
    "markers",
    "media",
    "transcript",
    "scenes",
    "silence",
    "speakers",
    "visual",
    "client",
    "clients",
    "project",
    "template",
)


@functools.cache
def load(name):
    """O schema `name` (um dos `NAMES`) como dict, lido uma vez por processo."""
    if name not in NAMES:
        raise ValueError(f"Schema desconhecido: {name}")
    return json.loads(_paths.data_path("schemas", f"{name}.schema.json").read_text(encoding="utf-8"))
