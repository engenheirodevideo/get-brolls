"""Schemas públicos do core, lidos uma vez de `schemas/` na raiz do repositório."""

import functools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NAMES = ("brief", "candidate")


@functools.cache
def load(name):
    """O schema `name` (`brief` ou `candidate`) como dict, lido uma vez por processo."""
    if name not in NAMES:
        raise ValueError(f"Schema desconhecido: {name}")
    return json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))
