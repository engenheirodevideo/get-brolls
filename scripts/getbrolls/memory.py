"""Explicit editorial reference history, separate from media permission."""

# pylint: disable=missing-function-docstring
# Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0).

import json

from . import effects, versioning
from ._paths import strict_exists
from .models import now, signature
from .sdk import jsonschema, schemas

REFERENCES_FILE = "references.json"


def load_references(root):
    """`references.json` de `root` (a pasta `brolls/`); ausente = `{"items": []}`. Só lê.

    Arquivo anterior ao `schema_version` carrega como versão 1; o de versão mais nova
    é recusado, e o arquivo fica como está.
    """
    path = root / REFERENCES_FILE
    if not strict_exists(path):
        return {"items": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
            f"{REFERENCES_FILE} é incompatível: esperado um objeto JSON."
        )
    versioning.read_version(data, REFERENCES_FILE, schemas.version("references"))
    if jsonschema.errors(data, schemas.load("references")):
        raise ValueError(f"{REFERENCES_FILE} é incompatível: o formato não segue o schema `references`.")
    return data


def remember(ledger, c, decision, reason, by):
    if not reason.strip() or not by.strip():
        raise ValueError("Referência exige motivo e responsável.")
    if decision == "approved" and (
        c["approval"]["status"] != "approved" or c["approval"].get("signature") != signature(c)
    ):
        raise ValueError("Aprove este insert antes de guardá-lo como referência positiva.")
    path = ledger.root / REFERENCES_FILE
    data = versioning.stamp(load_references(ledger.root), schemas.version("references"))
    entry = {
        "id": c["id"],
        "signature": signature(c),
        "source_url": c["source_url"],
        "title": c["title"],
        "asset_type": c.get("asset_type", "video"),
        "format": c.get("format", {}),
        "narration": c.get("narration"),
        "decision": decision,
        "reason": reason,
        "by": by,
        "at": now(),
    }
    data["items"].append(entry)
    temp = path.with_suffix(".tmp")
    with effects.op("fs.write", path):
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        temp.replace(path)
    ledger.save("remember", c)
    return entry
