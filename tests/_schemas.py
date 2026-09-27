"""Schema do plano de export em dois modos: o publicado (aberto) e o estrito dos testes.

O `schemas/export_plan.schema.json` publicado deixa os objetos que podem crescer
abertos para quem consome o plano. Os testes do core conferem a saída contra uma
variante fechada, derivada aqui: todo objeto com `properties` ganha
`additionalProperties: false` e exige todos os campos, então campo novo no core só passa
com o schema atualizado, e campo que some da saída é pego mesmo fora de `required`.
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
    if "properties" in node:
        node.setdefault("additionalProperties", False)
        node["required"] = list(node["properties"])
    for sub in (node.get("properties") or {}).values():
        _close(sub)
    _close(node.get("items"))
    _close(node.get("additionalProperties"))


def strict(schema=None):
    """Cópia fechada do schema: o que o core grava tem que casar com ela.

    `plan_version` fica travado em `2` aqui (o schema publicado só exige `>= 2`,
    informativo para o exportador): o core sempre grava `2` hoje, e a variante
    estrita continua pegando um valor diferente por engano.
    """
    closed = copy.deepcopy(published() if schema is None else schema)
    _close(closed)
    closed["properties"]["plan_version"] = {"const": 2}
    return closed


def example_plan():
    return json.loads(EXAMPLE_PLAN.read_text(encoding="utf-8"))
