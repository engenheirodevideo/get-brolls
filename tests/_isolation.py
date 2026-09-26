"""Nenhum teste lê ou escreve a pasta pessoal real.

`GB_HOME` aponta para um temporário por rodada, criado no primeiro import e
apagado na saída; o cache local (`GB_CACHE_DIR`) fica dentro dele. Quem já
exportou `GB_HOME` no ambiente continua mandando — mas aí a pasta é dele, não
a `~/.getbrolls` de verdade.

`discover -s tests` não importa `tests/__init__.py`, então cada módulo que
toca a biblioteca (direta ou indiretamente, por `next_action`/`search`)
importa este aqui antes de importar `getbrolls`.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

if not os.environ.get("GB_HOME"):
    _home = tempfile.mkdtemp(prefix="gb-home-")
    os.environ["GB_HOME"] = _home
    atexit.register(shutil.rmtree, _home, ignore_errors=True)

# Um GB_PLUGINS vazado do ambiente de quem roda os testes (ou deixado por um teste
# anterior que esqueceu de limpar) selecionaria plugins por fora do plugins.json do
# GB_HOME de mentira acima — mudando quais plugins um teste vê sem ele pedir isso.
os.environ.pop("GB_PLUGINS", None)

GB_HOME = Path(os.environ["GB_HOME"])

# O cache HTTP/drawtext cairia em `~/.cache/getbrolls` de verdade. Sem um
# `GB_CACHE_DIR` de quem roda, ele fica dentro do GB_HOME de mentira; o nome antigo
# (`GETBROLLS_CACHE_DIR`) sai do ambiente para não competir com ele.
if not os.environ.get("GB_CACHE_DIR"):
    os.environ["GB_CACHE_DIR"] = str(GB_HOME / "cache")
    os.environ.pop("GETBROLLS_CACHE_DIR", None)
