"""Storyboard review exchange. Human decisions never grant media usage rights."""

import copy
import hashlib
import json
import logging
import re
from pathlib import Path

from . import logs
from .models import approve, empty_output, now, signature

_log = logs.get(__name__.rsplit(".", 1)[-1])

ASSETS = Path(__file__).resolve().parents[2] / "assets"

# Versão do esquema do JSON de decisões que o Storyboard exporta e que `import_review` aceita.
REVIEW_TEMPLATE_VERSION = 2


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
        json.dumps([candidate["approval"], candidate.get("review")], sort_keys=True).encode()
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
                "templateVersion": REVIEW_TEMPLATE_VERSION,
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
    return page.replace("</style>", css + "</style>").replace(
        "</body>",
        "<script>window.GETBROLLS_REVIEW=" + payload + ";</script><script>" + js + "</script></body>",
    )


# Nome que `serve.py::save_review` grava: carimbo do relógio, e um `-N` numérico
# quando duas decisões caem no mesmo segundo. Sem sufixo == a primeira daquele
# segundo (equivalente a `-0`), não a mais recente.
_REVIEW_FILENAME = re.compile(r"(?P<stamp>\d{8}-\d{6})(?:-(?P<suffix>\d+))?\.json")


def _review_sort_key(path):
    match = _REVIEW_FILENAME.fullmatch(path.name)
    if not match:
        return (path.stat().st_mtime, path.name, -1)
    return (path.stat().st_mtime, match.group("stamp"), int(match.group("suffix") or 0))


def latest_review_file(root):
    """Decisão mais recente salva pela própria página em `brolls/reviews/`.

    O servidor local grava um arquivo por vez que a pessoa clica em “Salvar
    decisões”; o mais novo é o que ela acabou de decidir. Em sistemas de arquivos
    com mtime grosseiro, duas decisões do mesmo segundo empatam no horário — o
    desempate usa o sufixo `-N` numérico do nome, não a ordem alfabética bruta
    (onde `-1.json` viria antes de `.json`, escolhendo a decisão errada).
    """
    folder = Path(root) / "reviews"
    if not folder.is_dir():
        return None
    files = [p for p in folder.glob("*.json") if p.is_file()]
    if not files:
        return None
    return max(files, key=_review_sort_key)


def _import_review_result(review_state):
    """Collapse the five review states into the three audit-trail outcomes."""
    if review_state in ("approved", "rejected"):
        return review_state
    return "pending"


def import_review(ledger, file, by, rules=None):  # noqa: C901, PLR0912, PLR0915 - existing size; validates the saved decision file then applies it item by item
    if not by.strip():
        raise ValueError('Diga quem revisou: acrescente --by "seu nome" ao comando.')
    file_source = "explicit" if file is not None else "latest"
    if file is None:
        found = latest_review_file(ledger.root)
        if found is None:
            raise ValueError(
                "Não achei nenhuma decisão salva em "
                + str(Path(ledger.root) / "reviews")
                + ". Abra o Storyboard (`serve --background`), clique em “Salvar "
                "decisões” e rode este comando de novo — ou aponte o arquivo com --file."
            )
        file = found
    path = Path(file)
    if path.stat().st_size > 2000000:  # noqa: PLR2004 - 2 MB, matches the message below
        raise ValueError(
            "Esse arquivo de escolhas passa de 2 MB — não parece ser o que a página "
            "salvou. Confira se apontou para o getbrolls-review.json certo."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("type") != "getbrolls-review"
        or data.get("templateVersion") != REVIEW_TEMPLATE_VERSION
        or data.get("project") != project_id(ledger)
    ):
        raise ValueError(
            "Esse arquivo de escolhas é de outra coleta. Abra a página desta pasta "
            "com `review`, decida ali e salve de novo."
        )
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(
            "O arquivo de escolhas está vazio. Volte à página, decida os trechos e clique em “Salvar decisões”."
        )
    logs.event(_log, logging.INFO, "review_file_selected", name=path.name, candidates=len(items))
    changes = []
    skipped = []
    seen = set()
    item_log = []

    def skip(item_id, reason, detail):
        skipped.append({"id": item_id, "reason": reason, "detail": detail})

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
        if "signature" not in item or "reviewEpoch" not in item:
            # Falta a assinatura em si: o arquivo não veio do board desta coleta.
            raise ValueError(
                "O trecho " + item["id"] + " veio sem assinatura no arquivo de "
                "decisões: ou alguma coisa mudou depois que você decidiu, ou o arquivo "
                "não saiu desta página. Rode `review` de novo e salve as decisões da "
                "página nova."
            )
        c = copy.deepcopy(ledger.get(item["id"]))
        if item.get("signature") != signature(c):
            skip(
                c["id"],
                "signature_mismatch",
                "O trecho " + c["id"] + " mudou depois que você decidiu (intervalo ou "
                "fonte). Rode `review` de novo e decida este de novo.",
            )
            continue
        if item.get("reviewEpoch") not in (review_epoch(c), legacy_review_epoch(c)):
            skip(
                c["id"],
                "stale_epoch",
                "A decisão do trecho " + c["id"] + " é de uma versão anterior da "
                "coleta: alguma coisa mudou depois que você decidiu. Nada se perdeu — "
                "rode `review` de novo e confirme este trecho.",
            )
            continue
        state = item.get("state")
        comment = item.get("comment", "")
        suggestion = item.get("suggestion", "")
        if state not in ("pending", "approved", "changes", "alternative", "rejected"):
            skip(
                c["id"],
                "invalid_item",
                "Decisão desconhecida no trecho " + c["id"] + ". Use a página do "
                "Storyboard para decidir; não edite o arquivo de decisões à mão.",
            )
            continue
        if (
            not isinstance(comment, str)
            or not isinstance(suggestion, str)
            or len(comment) > 10000  # noqa: PLR2004 - teto de caracteres do campo "comentário"
            or len(suggestion) > 2000  # noqa: PLR2004 - teto de caracteres do campo "sugestão"
        ):
            skip(
                c["id"],
                "invalid_item",
                "O comentário ou o link do trecho " + c["id"] + " está grande demais "
                "(limite de 10 mil e 2 mil caracteres). Encurte e salve de novo.",
            )
            continue
        if state in ("changes", "alternative") and not comment.strip():
            skip(
                c["id"],
                "invalid_item",
                "Faltou dizer o que mudar no trecho " + c["id"] + ". Abra a página, "
                "escreva uma linha no pedido de ajuste e salve de novo.",
            )
            continue
        if suggestion:
            from .http import public_url

            if not public_url(suggestion):
                skip(
                    c["id"],
                    "invalid_item",
                    "O link que você colou no trecho " + c["id"] + " precisa ser um "
                    "endereço https público, sem senha nem código de acesso. "
                    "Corrija ou apague o link e salve de novo.",
                )
                continue
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
                skip(
                    c["id"],
                    "invalid_item",
                    "Asset bloqueado pelas regras atuais do usuário: " + c["id"] + ".",
                )
                continue
            approve(c, by, "storyboard")
        elif state == "rejected":
            # Same transition as the CLI `reject` command, recorded with the reviewer.
            c["approval"] = {"status": "rejected", "by": by, "at": now(), "revision": None}
            c["state"] = "rejected"
            # Same reset as `commands.mark_rejected`: an already-fetched candidate must
            # stop counting as delivered/verified once rejected here too.
            c["output"] = empty_output()
        else:
            c["approval"] = {
                "status": "pending",
                "by": None,
                "at": None,
                "revision": None,
            }
            c["state"] = "awaiting_approval"
        item_log.append((c["id"], state))
        changes.append(c)
    if not changes:
        # Nada aplicado: o comando falha e diz, item a item, o que impediu cada um.
        raise ValueError("Nenhuma decisão pôde ser importada. " + " ".join(entry["detail"] for entry in skipped))
    # Só o que passou em toda a validação chega ao disco, num único registro.
    updates = {c["id"]: c for c in changes}
    ledger.data["items"] = [updates.get(c["id"], c) for c in ledger.data["items"]]
    ledger.save_many("import-review", changes)
    try:
        for cid, review_state in item_log:
            logs.event(
                _log,
                logging.INFO,
                "import_review_item",
                candidate=cid,
                result=_import_review_result(review_state),
                skip_code=None,
            )
        for entry in skipped:
            logs.event(
                _log,
                logging.WARNING,
                "import_review_item",
                candidate=entry["id"],
                result="skipped",
                skip_code=entry["reason"],
            )
        logs.event(
            _log,
            logging.INFO,
            "import_review",
            file_source=file_source,
            applied=len(changes),
            skipped=len(skipped),
            rejected_file=any(review_state == "rejected" for _, review_state in item_log),
        )
    except Exception:  # noqa: BLE001, S110 - logging must never break a command
        pass
    return {
        "imported": len(changes),
        "skipped": skipped,
        "by": by,
        "file": str(path),
        "review": str(ledger.root / "review.html"),
    }
