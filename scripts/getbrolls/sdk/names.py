"""Padrão de nome das extensões, num módulo folha.

Id de plugin, fonte, preset, rota, comando, exportador e resolvedor seguem este padrão.
Mora aqui, sem importar nada do get-brolls, para que o core (o roteiro, que o `brief`
importa) o use sem voltar a `sdk.contracts`, que lê o `brief`. `sdk.contracts.NAME_RE`
é o mesmo objeto.
"""

import re

NAME_RE = re.compile(r"[a-z][a-z0-9_]{1,31}")
