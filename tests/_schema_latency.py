"""Custo de validar na leitura: o auxiliar que os testes de cada família reutilizam.

A leitura valida o documento contra o schema com o validador stdlib (Python puro). Um manifesto ou uma
fila grandes não podem ficar lentos por isso: `assert_validates_within` mede o melhor de poucas amostras
(uma regressão real é lenta em todas) e `large_document` monta o documento grande de um modelo de item.
"""

import copy
import unittest

import _paths  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
import _timing

# isort: split
from getbrolls.sdk import jsonschema

# Folga larga: o teste pergunta "é possível validar rápido", não "foi rápido uma vez".
DEFAULT_LIMIT_S = 3.0
DEFAULT_ITEMS = 5000


def large_document(template: dict, items_key: str, item: dict, count: int = DEFAULT_ITEMS) -> dict:
    """Cópia de `template` com `count` cópias de `item` em `items_key` (lista)."""
    doc = copy.deepcopy(template)
    doc[items_key] = [copy.deepcopy(item) for _ in range(count)]
    return doc


def assert_validates_within(
    case: unittest.TestCase, doc: object, schema: dict, limit_s: float = DEFAULT_LIMIT_S
) -> float:
    """Falha quando validar `doc` contra `schema` passa de `limit_s` em todas as amostras ou acha problema.

    Devolve o melhor tempo, para o teste do consumidor registrar ou comparar."""
    best, problems = _timing.best_of(lambda: jsonschema.errors(doc, schema), limit_s)
    case.assertEqual([], problems)
    case.assertLess(best, limit_s, f"validar {len(str(doc))} caracteres levou {best:.2f}s (limite {limit_s}s)")
    return best
