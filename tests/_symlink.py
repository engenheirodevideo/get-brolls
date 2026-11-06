"""Link simbólico: a sonda de verdade, uma vez por rodada, no lugar de "o Windows não cria".

No Windows `os.symlink` pede o privilégio de criar link (ou o modo de desenvolvedor); o runner do CI o tem e a
máquina de quem desenvolve talvez não. Um teste que precisa de link roda onde o link nasce e pula, com o
motivo certo, onde não nasce. A sonda cria um link de pasta e um de arquivo numa pasta temporária e confere que
o de arquivo resolve; o resultado vale para o processo inteiro.
"""

import tempfile
import unittest
from pathlib import Path

SKIP_REASON = (
    "este sistema não criou um link simbólico na sonda (os.symlink recusou: no Windows falta o privilégio de "
    "criar link ou o modo de desenvolvedor)"
)


def probe_symlinks():
    """Cria um link de pasta e um de arquivo numa pasta temporária; `True` só se os dois nascem e resolvem."""
    try:
        with tempfile.TemporaryDirectory(prefix="gb-symlink-probe-") as raw:
            base = Path(raw)
            (base / "pasta").mkdir()
            (base / "arquivo.txt").write_text("alvo", encoding="utf-8")
            (base / "link-pasta").symlink_to(base / "pasta", target_is_directory=True)
            (base / "link-arquivo").symlink_to(base / "arquivo.txt")
            return (base / "link-arquivo").read_text(encoding="utf-8") == "alvo" and (base / "link-pasta").is_dir()
    except (OSError, NotImplementedError):
        return False


CAN_SYMLINK = probe_symlinks()
needs_symlink = unittest.skipUnless(CAN_SYMLINK, SKIP_REASON)
