"""Literais de versão e produtores sem mapa da família casa (perfil, clientes, receitas, SDK) a tirar do código."""

OWNER = "home"
# chave do literal -> schema alvo ("schema" ou "schema.campo"; tupla quando vale para vários)
LITERALS: dict[str, str | tuple[str, ...]] = {}
# produtores de JSON ainda sem mapa em WRITERS/EXCLUDED: chave -> por quê
UNMAPPED_WRITERS: dict[str, str] = {}
