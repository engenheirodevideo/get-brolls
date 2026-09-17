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
        raise ValueError(
            "Diga quem revisou: acrescente --by \"seu nome\" ao comando."
        )
    path = Path(file)
    if path.stat().st_size > 2000000:
        raise ValueError(
            "Esse arquivo de escolhas passa de 2 MB — não parece ser o que a página "
            "salvou. Confira se apontou para o getbrolls-review.json certo."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("type") != "getbrolls-review"
        or data.get("templateVersion") != 2
        or data.get("project") != project_id(ledger)
    ):
        raise ValueError(
            "Esse arquivo de escolhas é de outra coleta. Abra a página desta pasta "
            "com `review`, decida ali e salve de novo."
        )
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(
            "O arquivo de escolhas está vazio. Volte à página, decida os trechos e "
            "clique em “Salvar decisões”."
        )
    changes = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError(
                "Tem um trecho sem identificação no arquivo de decisões. Volte à "
                "página, salve de novo e me passe o arquivo novo."
            )
        if item["id"] in seen:
            raise ValueError(
                "O trecho " + item["id"] + " aparece duas vezes no arquivo de decisões. "
                "Volte à página e salve de novo, sem juntar arquivos."
            )
        seen.add(item["id"])
        c = copy.deepcopy(ledger.get(item["id"]))
        if item.get("signature") != signature(c):
            raise ValueError(
                "O trecho " + c["id"] + " mudou depois que você decidiu (intervalo ou "
                "fonte). Rode `review` de novo e salve as decisões da página nova."
            )
        if item.get("reviewEpoch") not in (review_epoch(c), legacy_review_epoch(c)):
            raise ValueError(
                "Este arquivo de escolhas é de uma versão anterior da coleta "
                "(trecho " + c["id"] + "): alguma coisa mudou depois que você decidiu. "
                "Nada se perdeu — rode `review` de novo, confira as decisões que já "
                "estão marcadas e salve de novo."
            )
        state = item.get("state")
        comment = item.get("comment", "")
        suggestion = item.get("suggestion", "")
        if state not in ("pending", "approved", "changes", "alternative", "rejected"):
            raise ValueError(
                "Decisão desconhecida no arquivo. Use a página do Storyboard para "
                "decidir; não edite o arquivo de decisões à mão."
            )
        if (
            not isinstance(comment, str)
            or not isinstance(suggestion, str)
            or len(comment) > 10000
            or len(suggestion) > 2000
        ):
            raise ValueError(
                "O comentário ou o link do trecho " + c["id"] + " está grande demais "
                "(limite de 10 mil e 2 mil caracteres). Encurte e salve de novo."
            )
        if state in ("changes", "alternative") and not comment.strip():
            raise ValueError(
                "Faltou dizer o que mudar no trecho " + c["id"] + ". Abra a página, "
                "escreva uma linha no pedido de ajuste e salve de novo."
            )
        if suggestion:
            from .http import public_url

            if not public_url(suggestion):
                raise ValueError(
                    "O link que você colou no trecho " + c["id"] + " precisa ser um "
                    "endereço https público, sem senha nem código de acesso. "
                    "Corrija ou apague o link e salve de novo."
                )
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
