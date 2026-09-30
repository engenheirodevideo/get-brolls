"""Leitura tolerante do `schema_version` dos JSON que o get-brolls grava.

Regra única para as famílias que já existiam: campo ausente vale 1 (arquivo gravado
antes de o campo existir); inteiro maior que o suportado é recusado com uma frase
padrão, para a pessoa atualizar em vez de perder dado; qualquer outro valor (texto,
`True`, `1.0`, zero ou negativo) é inválido. Toda gravação nova declara a versão.
Módulo folha: não importa nada do get-brolls.
"""

FIELD = "schema_version"


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
