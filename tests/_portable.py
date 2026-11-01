"""Mecânica de teste que muda de sistema para sistema, num lugar só."""

import os
import stat
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import


def unlink_frozen(path):
    """Apaga um arquivo que a entrega congelou (somente leitura), como a pessoa o apagaria.

    No POSIX o bit de escrita do arquivo não impede apagar o nome; no Windows o atributo somente-leitura
    impede, e o hardlink da entrega o compartilha com o clipe de `brolls/`. Lá a escrita volta antes."""
    path = Path(path)
    if os.name == "nt":
        path.chmod(stat.S_IWRITE | stat.S_IREAD)
    path.unlink()
