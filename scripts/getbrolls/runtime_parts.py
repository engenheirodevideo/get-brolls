"""Escrita do marcador de uma parte do runtime (`venv`, `tools`).

A leitura e o formato do marcador moram em `_paths`; gravar usa `effects`, que importa `_paths`,
por isso este módulo fica uma camada acima.
"""

import json
import sys
from collections.abc import Mapping
from pathlib import Path

from . import _paths, effects


def write_marker(part: str, root: Path, status: str, extra: Mapping[str, object] | None = None) -> Path:
    """Grava o marcador da parte (troca atômica do arquivo); devolve o caminho.

    Registra a versão da parte e, na `.venv`, o Python base que a criou; `extra` acrescenta
    campos informativos (o `upgraded` do `setup --upgrade`, por exemplo).
    """
    if status not in _paths.MARKER_STATUSES:
        raise ValueError(f"status de marcador desconhecido: {status}")
    from . import __version__  # tardio: evita ciclo com o pacote

    python = getattr(sys, "_base_executable", None) or sys.executable
    marker = {
        "schema": _paths.MARKER_SCHEMA_VERSION,
        "part": part,
        "sha": _paths.part_sha(part),
        "status": status,
        "python": python if part == "venv" else None,
        "getbrolls": __version__,
        **(extra or {}),
    }
    path = _paths.marker_path(part, root)
    effects.mkdir(root, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with effects.op("fs.write", path):
        temporary.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(path)
    return path
