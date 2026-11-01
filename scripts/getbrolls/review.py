"""Storyboard review exchange. Human decisions never grant media usage rights."""

# pylint: disable=missing-function-docstring,too-many-locals,broad-exception-caught
# pylint: disable=too-many-branches,too-many-statements
# Legado: ocorrências pré-existentes (corpo idêntico ao código anterior à 2.6.0).

import copy
import errno
import hashlib
import json
import logging
import re
from pathlib import Path

from . import _paths, json_read, logs
from .models import approve, empty_output, now, pending_approval, signature
from .sdk import jsonschema, schemas

_log = logs.get(__name__.rsplit(".", 1)[-1])


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
    """Id estável do projeto (sobrevive à cópia da pasta para outra pessoa ou máquina).

    Com `project.json` (layout 1), o id dele manda e é copiado para o manifesto que
    ainda não tem `project_id`: uma identidade só. Sem ele, o manifesto guarda um uuid
    criado na primeira vez. `project.json` inválido levanta `ValueError`.
    """
    from . import layout

    declared = layout.project_id(ledger.root.parent)
    if declared is not None:
        if "project_id" not in ledger.data:
            ledger.data["project_id"] = declared
            ledger.save("project_identity")
        return declared
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
                "templateVersion": schemas.version("review_decisions"),
                "project": project_id(ledger),
                "items": records,
            },
            ensure_ascii=False,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    css = _paths.data_path("assets", "review.css").read_text(encoding="utf-8")
    js = _paths.data_path("assets", "review.js").read_text(encoding="utf-8")
    return page.replace("</style>", css + "</style>").replace(
        "</body>",
        "<script>window.GETBROLLS_REVIEW=" + payload + ";</script><script>" + js + "</script></body>",
    )


# Nome que `serve.py::save_review` grava: carimbo do relógio, e um `-N` numérico
# quando duas decisões caem no mesmo segundo. Sem sufixo == a primeira daquele
# segundo (equivalente a `-0`), não a mais recente.
_REVIEW_FILENAME = re.compile(r"(?P<stamp>\d{8}-\d{6})(?:-(?P<suffix>\d+))?\.json")


def _review_sort_key(path):
    # The name closes every tie (`x.json` and `x-0.json` are the same second and position):
    # the latest file never depends on the order the file system lists the folder.
    match = _REVIEW_FILENAME.fullmatch(path.name)
    if not match:
        return (path.stat().st_mtime, path.name, -1, path.name)
    return (path.stat().st_mtime, match.group("stamp"), int(match.group("suffix") or 0), path.name)


def latest_review_file(root):
    """Decisão mais recente salva pela própria página em `brolls/reviews/`.

    O servidor local grava um arquivo por vez que a pessoa clica em “Salvar
    decisões”; o mais novo é o que ela acabou de decidir. Em sistemas de arquivos
    com mtime grosseiro, duas decisões do mesmo segundo empatam no horário — o
    desempate usa o sufixo `-N` numérico do nome, não a ordem alfabética bruta
    (onde `-1.json` viria antes de `.json`, escolhendo a decisão errada).
    """
    folder = Path(root) / "reviews"
    if not _paths.strict_is_dir(folder):
        return None
    files = [p for p in folder.glob("*.json") if _paths.strict_is_file(p)]
    if not files:
        return None
    return max(files, key=_review_sort_key)


def _import_review_result(review_state):
    """Collapse the five review states into the three audit-trail outcomes."""
    if review_state in ("approved", "rejected"):
        return review_state
    return "pending"


# Teto do arquivo de decisões: a página salva bem menos que isso.
_REVIEW_MAX_BYTES = 2000000
_TOO_LARGE = (
    "Esse arquivo de escolhas passa de 2 MB — não parece ser o que a página "
    "salvou. Confira se apontou para o getbrolls-review.json certo."
)


def _resolve_review_file(ledger, file):
    """Arquivo de decisões a importar: o explícito ou o mais recente da pasta `reviews/`."""
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
    if path.stat().st_size > _REVIEW_MAX_BYTES:
        raise ValueError(_TOO_LARGE)
    return path


def _read_review_file(path):
    """O JSON do arquivo de decisões pela leitura única (teto de 2 MB, profundidade, NaN, surrogates).

    JSON recusado é dado inválido (`ValueError`, INVALID_DATA); pasta, FIFO ou dispositivo no lugar do
    arquivo é problema de E/S (`OSError`, IO_ERROR), como a leitura direta já respondia para uma pasta."""
    try:
        return json_read.read(path, max_bytes=_REVIEW_MAX_BYTES)
    except json_read.JsonReadError as exc:
        if exc.reason == "not_regular":
            raise OSError(errno.EINVAL, "não é um arquivo comum", str(path)) from None
        if exc.reason == "too_large":
            raise ValueError(_TOO_LARGE) from None
        raise ValueError(
            "Esse arquivo de escolhas não é um JSON válido. Volte à página do Storyboard, "
            "clique em “Salvar decisões” e importe o arquivo novo."
        ) from None


def _load_review_items(ledger, path):
    """Lê e valida o arquivo de decisões inteiro (versão, coleta, schema); devolve os itens."""
    data = _read_review_file(path)
    supported = schemas.version("review_decisions")
    declared = data.get("templateVersion") if isinstance(data, dict) else None
    if (
        isinstance(data, dict)
        and data.get("type") == "getbrolls-review"
        and type(declared) is int
        and declared > supported
    ):
        raise ValueError(
            "Esse arquivo de escolhas foi gravado por uma versão mais nova do getbrolls "
            f"(templateVersion {declared}); atualize antes de continuar."
        )
    # `type(...) is not int` keeps `True` (`== 1`) and a float like `2.0` (`== 2`)
    # from passing as the real int, mirroring `ledger.validate_manifest`'s strictness.
    if (
        not isinstance(data, dict)
        or data.get("type") != "getbrolls-review"
        or type(data.get("templateVersion")) is not int
        or data.get("templateVersion") != supported
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
    problems = jsonschema.errors(data, schemas.load("review_decisions"))
    if problems:
        raise ValueError("Esse arquivo de escolhas está fora do formato: " + "; ".join(problems[:5]))
    return items


def _check_item_identity(item, seen):
    """Falha o arquivo todo se o trecho vem sem id, repetido ou sem assinatura."""
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
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


def _stale_item_skip(item, c):
    """`(reason, detail)` se a decisão não vale mais para o trecho atual; senão `None`."""
    if item.get("signature") != signature(c):
        return (
            "signature_mismatch",
            "O trecho " + c["id"] + " mudou depois que você decidiu (intervalo ou "
            "fonte). Rode `review` de novo e decida este de novo.",
        )
    if item.get("reviewEpoch") not in (review_epoch(c), legacy_review_epoch(c)):
        return (
            "stale_epoch",
            "A decisão do trecho " + c["id"] + " é de uma versão anterior da "
            "coleta: alguma coisa mudou depois que você decidiu. Nada se perdeu — "
            "rode `review` de novo e confirme este trecho.",
        )
    return None


def _decision_fields_skip(c, state, comment, suggestion):
    """`(reason, detail)` se estado, comentário ou link do item são inválidos; senão `None`."""
    if state not in ("pending", "approved", "changes", "alternative", "rejected"):
        return (
            "invalid_item",
            "Decisão desconhecida no trecho " + c["id"] + ". Use a página do "
            "Storyboard para decidir; não edite o arquivo de decisões à mão.",
        )
    if (
        not isinstance(comment, str)
        or not isinstance(suggestion, str)
        or len(comment) > 10000  # noqa: PLR2004 - character cap of the comment field
        or len(suggestion) > 2000  # noqa: PLR2004 - character cap of the suggestion field
    ):
        return (
            "invalid_item",
            "O comentário ou o link do trecho " + c["id"] + " está grande demais "
            "(limite de 10 mil e 2 mil caracteres). Encurte e salve de novo.",
        )
    if state in ("changes", "alternative") and not comment.strip():
        return (
            "invalid_item",
            "Faltou dizer o que mudar no trecho " + c["id"] + ". Abra a página, "
            "escreva uma linha no pedido de ajuste e salve de novo.",
        )
    if suggestion:
        from .http import public_url

        if not public_url(suggestion):
            return (
                "invalid_item",
                "O link que você colou no trecho " + c["id"] + " precisa ser um "
                "endereço https público, sem senha nem código de acesso. "
                "Corrija ou apague o link e salve de novo.",
            )
    return None


def _apply_decision(c, state, by, rules):
    """Aplica a decisão válida a `c`; devolve `(reason, detail)` se as regras bloqueiam, senão `None`."""
    if state == "approved":
        from .rules import allowed

        if rules is not None and not allowed(c, rules):
            return ("invalid_item", "Asset bloqueado pelas regras atuais do usuário: " + c["id"] + ".")
        approve(c, by, "storyboard")
    elif state == "rejected":
        # Same transition as the CLI `reject` command, recorded with the reviewer.
        c["approval"] = {"status": "rejected", "by": by, "at": now(), "revision": None}
        c["state"] = "rejected"
        # Same reset as `commands.mark_rejected`: an already-fetched candidate must
        # stop counting as delivered/verified once rejected here too.
        c["output"] = empty_output()
    else:
        c["approval"] = pending_approval()
        c["state"] = "awaiting_approval"
    return None


def _log_import_review(file_source, item_log, skipped):
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
            applied=len(item_log),
            skipped=len(skipped),
            rejected_file=any(review_state == "rejected" for _, review_state in item_log),
        )
    except Exception:  # noqa: BLE001, S110 - logging must never break a command
        pass


def import_review(ledger, file, by, rules=None):
    """Importa as decisões salvas pela página de revisão; devolve o resumo e o que foi pulado."""
    if not by.strip():
        raise ValueError('Diga quem revisou: acrescente --by "seu nome" ao comando.')
    file_source = "explicit" if file is not None else "latest"
    path = _resolve_review_file(ledger, file)
    items = _load_review_items(ledger, path)
    logs.event(_log, logging.INFO, "review_file_selected", name=path.name, candidates=len(items))
    changes = []
    skipped = []
    seen = set()
    item_log = []
    for item in items:
        _check_item_identity(item, seen)
        c = copy.deepcopy(ledger.get(item["id"]))
        state = item.get("state")
        comment = item.get("comment", "")
        suggestion = item.get("suggestion", "")
        skip = _stale_item_skip(item, c) or _decision_fields_skip(c, state, comment, suggestion)
        if skip is None:
            c["review"] = {
                "state": state,
                "comment": comment,
                "suggestion": suggestion,
                "by": by,
                "at": now(),
                "signature": signature(c),
            }
            skip = _apply_decision(c, state, by, rules)
        if skip is not None:
            skipped.append({"id": c["id"], "reason": skip[0], "detail": skip[1]})
            continue
        item_log.append((c["id"], state))
        changes.append(c)
    if not changes:
        # Nada aplicado: o comando falha e diz, item a item, o que impediu cada um.
        raise ValueError("Nenhuma decisão pôde ser importada. " + " ".join(entry["detail"] for entry in skipped))
    # Só o que passou em toda a validação chega ao disco, num único registro.
    updates = {c["id"]: c for c in changes}
    ledger.data["items"] = [updates.get(c["id"], c) for c in ledger.data["items"]]
    ledger.save_many("import-review", changes)
    _log_import_review(file_source, item_log, skipped)
    return {
        "imported": len(changes),
        "skipped": skipped,
        "by": by,
        "file": str(path),
        "review": str(ledger.root / "review.html"),
    }
