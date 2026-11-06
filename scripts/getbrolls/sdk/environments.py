"""`environment --action list`: as pontes de ambiente desta instalação (nesta versão, só as oficiais).

Lê só o inventário da origem oficial (`official.entries`): nenhum código de plugin roda, nada é gravado.
"""

from . import official
from .official_pins import PINS


def rows():
    """Uma linha por ponte oficial: id, versão, tipo, como a volta é lida, procedência e estado."""
    return [
        {
            "id": row["id"],
            "version": row["version"],
            "kind": "bridge",
            "readback": "files",
            "origin": "official",
            "sha256": PINS[row["id"]],
            "status": row["status"],
            "reason": row["reason"],
        }
        for row, _folder, _manifest in official.entries()
    ]


def run(_args):
    """`environment --action list` (a única ação: o argparse recusa as outras)."""
    return {"environments": rows()}
