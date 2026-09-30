"""Leitura tolerante do `schema_version` dos JSON que o get-brolls grava.

Regra única para as famílias que já existiam: campo ausente vale 1 (arquivo gravado
antes de o campo existir); inteiro maior que o suportado é recusado com uma frase
padrão, para a pessoa atualizar em vez de perder dado; qualquer outro valor (texto,
`True`, `1.0`, zero ou negativo) é inválido. Toda gravação nova declara a versão.

As famílias de dado criadas a partir da 2.6 (projeto, análise, cliente, template)
declaram a versão num campo só, autodescritivo: `"schema": "getbrolls.<família>/<N>"`.
`read_schema` e `stamp_schema` fazem o mesmo papel para essa forma, com a mesma regra
(ausente = 1, maior que o suportado recusado com a frase padrão).
Módulo folha: não importa nada do get-brolls.
"""

import re
from collections.abc import Callable

FIELD = "schema_version"
SCHEMA_FIELD = "schema"
_SCHEMA = re.compile(r"getbrolls\.([a-z][a-z0-9_]*)/([1-9][0-9]*)")


def read_version(data: dict, label: str, supported: int = 1, invalid: str | None = None) -> int:
    """Versão declarada em `data` (ausente = 1); `ValueError` quando não dá para ler.

    `label` nomeia o arquivo na frase de versão mais nova. `invalid` substitui a
    mensagem padrão de valor inválido, para quem já tem a própria mensagem de arquivo
    incompatível; a frase de versão mais nova é sempre a mesma.
    """
    if FIELD not in data:
        return 1
    value = data[FIELD]
    # `type(...) is int`: `True == 1` e `1.0 == 1` não podem passar pelo inteiro 1.
    if type(value) is not int or value < 1:
        raise ValueError(
            invalid or f"{label} é incompatível: {FIELD} tem que ser um inteiro a partir de 1 (veio {value!r})."
        )
    if value > supported:
        raise ValueError(
            f"{label} foi gravado por uma versão mais nova do get-brolls ({FIELD} {value}); "
            "atualize antes de continuar."
        )
    return value


def stamp(data: dict, version: int = 1) -> dict:
    """Cópia rasa de `data` com `schema_version` = `version` como primeira chave."""
    return {FIELD: version, **{k: v for k, v in data.items() if k != FIELD}}


def schema_name(family: str, version: int = 1) -> str:
    """Valor do campo `schema` de uma família: `getbrolls.<família>/<versão>`."""
    return f"getbrolls.{family}/{version}"


def read_schema(
    data: dict,
    family: str,
    supported: int = 1,
    label: str | None = None,
    invalid: str | Callable[[], Exception] | None = None,
) -> int:
    """Versão de `data` pelo campo `schema` da família `family` (ausente = 1).

    Valor que não é `getbrolls.<family>/<N>` (texto de outra família, `N` com zero à
    esquerda, número solto) é inválido: `ValueError` com a frase padrão, com o texto
    `invalid` ou, quando `invalid` é uma função, a exceção que ela devolve (quem lê um
    registro inteiro troca pela própria mensagem sem comparar texto). `N` maior que
    `supported` é sempre `ValueError` com a frase de versão mais nova do `schema_version`.
    `label` nomeia o arquivo.
    """
    label = label or f"getbrolls.{family}"
    if SCHEMA_FIELD not in data:
        return 1
    value = data[SCHEMA_FIELD]
    found = _SCHEMA.fullmatch(value) if isinstance(value, str) else None
    if found is None or found.group(1) != family:
        if callable(invalid):
            raise invalid()
        raise ValueError(
            invalid
            or f"{label} é incompatível: {SCHEMA_FIELD} tem que ser {schema_name(family, 1)!r} "
            f"ou uma versão maior da mesma família (veio {value!r})."
        )
    version = int(found.group(2))
    if version > supported:
        raise ValueError(
            f"{label} foi gravado por uma versão mais nova do get-brolls ({SCHEMA_FIELD} {value}); "
            "atualize antes de continuar."
        )
    return version


def stamp_schema(data: dict, family: str, version: int = 1) -> dict:
    """Cópia rasa de `data` com `schema` = `getbrolls.<family>/<version>` como primeira chave."""
    return {SCHEMA_FIELD: schema_name(family, version), **{k: v for k, v in data.items() if k != SCHEMA_FIELD}}
