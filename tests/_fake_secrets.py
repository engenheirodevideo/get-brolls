"""Segredos de mentira montados na hora, para nenhum scanner achar um deles parado no repositório.

Os testes de redação precisam de textos com a forma real de cada provedor (`xoxb-…`, `sk_live_…`, uma chave
privada PEM). Escritos inteiros num literal, eles são indistinguíveis de um vazamento para o GitHub Push
Protection e para o gitleaks. Aqui cada um é escrito em pedaços — o corte cai dentro do que a regex do
scanner reconhece — e `joined` os cola de volta: o redator recebe exatamente o mesmo texto de antes.
"""


def joined(*parts: str) -> str:
    return "".join(parts)
