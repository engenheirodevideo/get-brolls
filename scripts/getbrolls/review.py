"""Storyboard review exchange. Human decisions never grant media usage rights."""

import hashlib, json, copy
from pathlib import Path
from .models import signature, approve, now

ASSETS = Path(__file__).resolve().parents[2] / "assets"


# Campos da decisão que definem a época: chaves acrescentadas depois (canal, frase)
# não podem invalidar um board já exportado.
EPOCH_FIELDS = ("status", "by", "at", "revision")


def review_epoch(candidate):
    """Bind an exported decision to the approval/review it was based on."""
    approval = candidate["approval"]
    return hashlib.sha256(
        json.dumps(
            [
                {key: approval.get(key) for key in EPOCH_FIELDS},
                candidate.get("review"),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def legacy_review_epoch(candidate):
    """Época como a 2.3.x a calculava: o dicionário `approval` inteiro.

    Um board exportado antes da 2.4 traz esse valor; aceitá-lo na importação evita
    descartar decisões humanas já tomadas só porque a fórmula mudou.
    """
    return hashlib.sha256(
        json.dumps(
            [candidate["approval"], candidate.get("review")], sort_keys=True
        ).encode()
    ).hexdigest()


def project_id(ledger):
    # Stable when project folder is copied to another reviewer/computer.
    if "project_id" not in ledger.data:
        import uuid

        ledger.data["project_id"] = str(uuid.uuid4())
        ledger.save("project_identity")
    return ledger.data["project_id"]


def enhance(page, ledger, records):
    payload = (
        json.dumps(
            {
                "type": "getbrolls-review",
                "templateVersion": 2,
                "project": project_id(ledger),
                "items": records,
            },
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    css = (ASSETS / "review.css").read_text(encoding="utf-8")
    js = (ASSETS / "review.js").read_text(encoding="utf-8")
    return (
        page.replace("</style>", css + "</style>")
        .replace(
            "</body>",
            "<script>window.GETBROLLS_REVIEW="
            + payload
            + ";</script><script>"
            + js
            + "</script></body>",
        )
    )


def import_review(ledger, file, by, rules=None):
    if not by.strip():
        raise ValueError("Informe quem revisou com --by.")
    path = Path(file)
    if path.stat().st_size > 2000000:
        raise ValueError("Revisão excede 2 MB.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("type") != "getbrolls-review"
        or data.get("templateVersion") != 2
        or data.get("project") != project_id(ledger)
    ):
        raise ValueError("Revisão não pertence a este projeto/template.")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Revisão sem itens.")
    changes = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Item inválido.")
        if item["id"] in seen:
            raise ValueError("Item duplicado na revisão.")
        seen.add(item["id"])
        c = copy.deepcopy(ledger.get(item["id"]))
        if item.get("signature") != signature(c):
            raise ValueError("Revisão desatualizada para " + c["id"])
        if item.get("reviewEpoch") not in (review_epoch(c), legacy_review_epoch(c)):
            raise ValueError(
                "Revisão desatualizada para " + c["id"]
                + ": a decisão mudou ou o arquivo não contém sua versão. "
                "Execute review, confira as decisões e exporte um novo JSON."
            )
        state = item.get("state")
        comment = item.get("comment", "")
        suggestion = item.get("suggestion", "")
        if state not in ("pending", "approved", "changes", "alternative", "rejected"):
            raise ValueError("Decisão desconhecida.")
        if (
            not isinstance(comment, str)
            or not isinstance(suggestion, str)
            or len(comment) > 10000
            or len(suggestion) > 2000
        ):
            raise ValueError("Comentário/sugestão inválido.")
        if state in ("changes", "alternative") and not comment.strip():
            raise ValueError("Ajuste/outra fonte precisa de comentário.")
        if suggestion:
            from .http import public_url

            if not public_url(suggestion):
                raise ValueError("Sugestão deve ser URL HTTPS pública sem credenciais.")
        c["review"] = {
            "state": state,
            "comment": comment,
            "suggestion": suggestion,
            "by": by,
            "at": now(),
            "signature": signature(c),
        }
        if state == "approved":
            from .rules import allowed

            if rules is not None and not allowed(c, rules):
                raise ValueError("Asset bloqueado pelas regras atuais do usuário.")
            approve(c, by, "storyboard")
        elif state == "rejected":
            # Same transition as the CLI `reject` command, recorded with the reviewer.
            c["approval"] = {"status": "rejected", "by": by, "at": now(), "revision": None}
            c["state"] = "rejected"
        else:
            c["approval"] = {
                "status": "pending",
                "by": None,
                "at": None,
                "revision": None,
            }
            c["state"] = "awaiting_approval"
        changes.append(c)
    # Validate the entire payload before persisting any decision.
    updates = {c["id"]: c for c in changes}
    ledger.data["items"] = [updates.get(c["id"], c) for c in ledger.data["items"]]
    ledger.save_many("import-review", changes)
    return {
        "imported": len(changes),
        "by": by,
        "review": str(ledger.root / "review.html"),
    }
