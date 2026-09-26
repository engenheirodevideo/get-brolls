"""Schema do plano de export em dois modos: o publicado (aberto) e o estrito dos testes.

O `schemas/export_plan.schema.json` publicado deixa os objetos que podem crescer
abertos para quem consome o plano. Os testes do core conferem a saída contra uma
variante fechada, derivada aqui: todo objeto com `properties` ganha
`additionalProperties: false`, então campo novo no core só passa com o schema atualizado.
"""

import copy
import json

from _paths import ROOT

EXPORT_PLAN_SCHEMA = ROOT / "schemas" / "export_plan.schema.json"
EXAMPLE_PLAN = ROOT / "examples" / "plans" / "reels.plan.json"


def published():
    return json.loads(EXPORT_PLAN_SCHEMA.read_text(encoding="utf-8"))


def _close(node):
    if not isinstance(node, dict):
        return
    if "properties" in node and "additionalProperties" not in node:
        node["additionalProperties"] = False
    for sub in (node.get("properties") or {}).values():
        _close(sub)
    _close(node.get("items"))
    _close(node.get("additionalProperties"))


def strict(schema=None):
    """Cópia fechada do schema: o que o core grava tem que casar com ela."""
    closed = copy.deepcopy(published() if schema is None else schema)
    _close(closed)
    return closed


def example_plan():
    return json.loads(EXAMPLE_PLAN.read_text(encoding="utf-8"))
