"""`override` para os testes: `typing.override` no 3.12+, decorador neutro no 3.11."""

import sys

if sys.version_info >= (3, 12):
    from typing import override
else:

    def override(method):
        """Devolve o método intacto; só marca que ele sobrescreve a base (nome fixo da stdlib)."""
        return method


__all__ = ["override"]
