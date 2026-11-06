"""Frases de resumo e de próximo passo dos comandos, e as regras de estágio que elas leem."""

from . import labels
from .queue import summary_line as queue_summary_line

# Etapas do fluxo, na ordem coleta → revisão → entrega, com singular e plural.
STATUS_STAGES = (
    ("candidates", "candidato encontrado", "candidatos encontrados"),
    ("previews", "prévia gerada", "prévias geradas"),
    ("pending", "decisão pendente", "decisões pendentes"),
    ("approved", "decisão aprovada", "decisões aprovadas"),
    ("rejected", "decisão rejeitada", "decisões rejeitadas"),
    ("permitted", "item com permit registrado", "itens com permit registrado"),
    ("delivered", "item entregue", "itens entregues"),
    ("verified", "item verificado", "itens verificados"),
)


PREVIEW_ARTIFACTS = ("gif_path", "contact_sheet_path", "poster_path")


# Storyboard publicado sem nenhuma prévia: a página sobe, mas não há o que decidir.
EMPTY_STORYBOARD = (
    "Storyboard vazio: nenhuma prévia. A página sobe, mas não há nada para decidir — "
    "gere as prévias com `preview` antes de mandar o endereço para alguém."
)


def _count(value, singular, plural):
    return f"{value} {singular if value == 1 else plural}"


def _identifier(result):
    return result.get("id") or "candidato"


def _rejection_note(result):
    """Fecho da linha de `reject`: o motivo dito, e "revisão invalidada" só quando havia uma.

    O item recém-buscado nunca passou por revisão nenhuma. Dizer que a revisão dele
    foi invalidada inventava um passo que não existiu e assustava quem só descartou
    um candidato ruim.
    """
    reason = (result.get("rejection") or {}).get("reason") or result.get("reason")
    tail = f": {reason}" if reason else ""
    # `review` some do candidato ao rejeitar, então quem sabe se havia uma é o próprio
    # resultado: `invalidated` vem dos lotes, `review` da rota de um item só.
    had_review = bool(result.get("invalidated_review") or (result.get("rejection") or {}).get("invalidated_review"))
    return (tail + "; revisão invalidada." if had_review else tail + ".") if (tail or had_review) else "."


def _note(result):
    """Observação do provedor, quando houver, colada ao fim da linha humana."""
    note = result.get("note")
    return f" {note}" if note else ""


def _status_line(result):
    counts = result.get("counts") or {}
    stages = ", ".join(_count(counts.get(key, 0), singular, plural) for key, singular, plural in STATUS_STAGES)
    return f"Resumi o projeto: {stages}."


# Uma linha por comando do fluxo: verbo + objeto + resultado, sempre em PT-BR.
FLOW_SUMMARIES = {
    "search": lambda r: (
        f"Pesquisei candidatos: "
        f"{_count(len(r.get('items') or []), 'registrado', 'registrados')}, "
        f"{_count(r.get('excluded_by_rules') or 0, 'excluído pelas regras', 'excluídos pelas regras')}, "
        f"{_count(len(r.get('errors') or []), 'fonte com erro', 'fontes com erro')}."
        f"{_note(r)}"
        f"{'; ' + str(len(r.get('errors') or [])) + ' fonte(s) falharam' if r.get('errors') else ''}"
    ),
    "resolve": lambda r: f"Registrei o candidato {_identifier(r)}: estado {r.get('state')}.",
    "preview": lambda r: (
        f"Gerei somente a referência estática de {_identifier(r)}: "
        f"estado {r.get('state')}, aprovação {(r.get('approval') or {}).get('status')}." + _plugin_reference_note(r)
        if r.get("state") == "reference_only"
        else f"Gerei a prévia de {_identifier(r)}: "
        f"estado {r.get('state')}, aprovação {(r.get('approval') or {}).get('status')}."
    ),
    "approve": lambda r: (
        f"Registrei a aprovação humana de {r.get('by')} pelo {r.get('channel')} em "
        f"{_count(len(r['approved']), 'item', 'itens')}; "
        f"{_count(len(r.get('skipped') or []), 'item pulado', 'itens pulados')}."
        if isinstance(r.get("approved"), list)
        else f"Registrei a aprovação humana de {_identifier(r)}: "
        f"estado {r.get('state')}, por {(r.get('approval') or {}).get('by')}."
    ),
    "reject": lambda r: (
        f"Rejeitei {_count(len(r['rejected']), 'item', 'itens')}: " + ", ".join(r["rejected"]) + _rejection_note(r)
        if isinstance(r.get("rejected"), list)
        else f"Rejeitei {_identifier(r)}: estado {r.get('state')}" + _rejection_note(r)
    ),
    "review": lambda r: f"Gerei o Storyboard em {labels.shown(r.get('review'))}.",
    "import-review": lambda r: (
        f"Importei {_count(r.get('imported') or 0, 'decisão', 'decisões')} assinada(s) por {r.get('by')}."
    ),
    "permit": lambda r: (
        f"Registrei as condições de uso de {_identifier(r)}: direitos {(r.get('rights') or {}).get('status')}."
    ),
    "fetch": lambda r: f"Coletei o corte final de {_identifier(r)} em {(r.get('output') or {}).get('path')}.",
    "verify": lambda r: (
        f"Verifiquei "
        f"{_count(r.get('count') or 0, 'arquivo coletado', 'arquivos coletados')}: "
        f"{'íntegro e decodificável' if (r.get('count') or 0) == 1 else 'íntegros e decodificáveis'}."
    ),
    "status": _status_line,
    "queue": queue_summary_line,
}


def with_summary(command, result):
    """Acrescenta a linha humana ao JSON do comando sem tocar nas chaves existentes."""
    formatter = FLOW_SUMMARIES.get(command)
    if formatter is None or not isinstance(result, dict) or "summary" in result:
        return result
    line = formatter(result)
    # `summary` é sempre um objeto com `line`. Alguns comandos devolviam a frase solta
    # como string, e quem lê o JSON tinha que saber de cor qual comando fala de que
    # jeito — a mesma leitura (`summary.line`) tem que servir para todos.
    return {**result, "summary": line if isinstance(line, dict) else {"line": line}}


# Escada do fluxo: a primeira condição verdadeira nomeia o próximo passo real.
# Item aprovado segue para permit/fetch/verify/deliver antes de prévia nova: quem
# sobrou sem quadro é rascunho (ver `guidance.flow_complete`), não trava o aprovado.
# Texto para o agente de cada degrau: `summary.next` do `status` sai daqui pelo
# mesmo degrau que `summary.do` (ver `summary_next`).
SEARCH_NEXT = "Nenhum candidato ainda: registre fontes com search ou resolve."


PERMIT_NEXT = "Registre as condições reais de uso com permit nos itens aprovados."


FETCH_NEXT = "Colete os cortes aprovados e permitidos com fetch."


VERIFY_NEXT = "Confira os arquivos coletados com verify."


DELIVER_NEXT = "Organize os trechos conferidos em entrega/ com deliver."


PREVIEW_NEXT = "Gere prévias com preview para os candidatos ainda sem quadro."


INSPECT_NEXT = (
    "Analise com inspect a fonte do candidato sem duração conhecida antes de gerar prévias "
    "para os candidatos ainda sem quadro."
)


APPROVE_NEXT = (
    "Peça a decisão humana: pelo Storyboard (review + import-review) ou pela fala "
    "no chat (approve --candidate ID --by NOME --channel chat --statement "
    '"frase"), com os IDs que você mostrou.'
)


PENDING_DECISION_NEXT = (
    "Peça a decisão humana: há prévia esperando alguém decidir, pelo Storyboard "
    "(review + import-review) ou pela fala no chat (approve --candidate ID --by "
    'NOME --channel chat --statement "frase").'
)


FLOW_COMPLETE_NEXT = "Fluxo completo: os itens aprovados estão coletados, verificados e organizados em entrega/."


STATUS_LADDER = (
    (lambda c: not c["candidates"], SEARCH_NEXT),
    (lambda c: c["permitted"] < c["approved"], PERMIT_NEXT),
    (lambda c: c["approved"] and c["delivered"] < c["permitted"], FETCH_NEXT),
    (lambda c: c["approved"] and c["verified"] < c["delivered"], VERIFY_NEXT),
    (lambda c: c["approved"] and c["undelivered"] > 0, DELIVER_NEXT),
    (lambda c: c["pending_preview"] > 0, PREVIEW_NEXT),
    (lambda c: not c["approved"], APPROVE_NEXT),
)


def provider_error_text(name, error):
    """`<fonte>: <erro>`, sem repetir o nome quando o erro de plugin já vem como
    "Plugin <fonte>: …" ou "Fonte <fonte> é do plugin …" (dava "pasta_local:
    Plugin pasta_local: …" e "pasta_local: Fonte pasta_local …")."""
    text = str(error)
    return text if text.startswith((f"Plugin {name}:", f"Fonte {name} ")) else f"{name}: {text}"


def status_next(counts, format_pending=0, pending_preview=None, undelivered=0):
    """Próximo passo real do fluxo, derivado das contagens por etapa.

    `pending_preview` e `undelivered` completam a mesma escada que `guidance.STEPS`
    percorre: sem eles, `summary.next` e `summary.do` nomeariam etapas diferentes.
    """
    counts = {
        **counts,
        "pending_preview": (counts["candidates"] - counts["previews"]) if pending_preview is None else pending_preview,
        "undelivered": undelivered,
    }
    if format_pending:
        return _format_next(format_pending)
    from .guidance import flow_complete, leftover_aside, leftovers

    state = {"undelivered": counts["undelivered"]}
    counts = {"pending": 0, "rejected": 0, **counts}
    # Mesma ordem de `guidance.next_action`: decisão humana pendente ganha de tudo,
    # inclusive do veredito de fim de fluxo. Só depois dela é que a entrega pronta vira
    # "Fluxo completo", e aí a sobra sem prévia é aparte, não próximo passo.
    if counts.get("pending"):
        return PENDING_DECISION_NEXT
    if flow_complete(state, counts):
        return FLOW_COMPLETE_NEXT + leftover_aside(leftovers(state, counts))
    for matches, step in STATUS_LADDER:
        if matches(counts):
            return step
    return FLOW_COMPLETE_NEXT


def _format_next(format_pending):
    return (
        "As regras editoriais mudaram: o próximo comando invalidará "
        + _count(format_pending, "aprovação", "aprovações")
        + "; gere prévia e revisão novamente antes de coletar."
    )


def summary_next(do, state):
    """`summary.next` do `status`: o texto para o agente do mesmo degrau de `summary.do`.

    `summary.next` vinha de uma escada própria, só de contagens: com um beat do brief
    sem material, ela dizia "Fluxo completo" enquanto `summary.do` mandava buscar.
    Degrau sem texto próprio aqui usa o `why` dele.
    """
    from .guidance import leftover_aside, leftovers

    step = do.get("step")
    counts = {"pending": 0, "rejected": 0, **(state.get("counts") or {})}
    if _reference_only_step(do):
        return REFERENCE_ONLY_NEXT
    if step == "format" and state.get("format_pending"):
        return _format_next(state["format_pending"])
    if step == "approve":
        return PENDING_DECISION_NEXT if counts["pending"] else APPROVE_NEXT
    if step == "done":
        return FLOW_COMPLETE_NEXT + leftover_aside(leftovers(state, counts))
    by_step = {
        "search": SEARCH_NEXT,
        "inspect": INSPECT_NEXT,
        "preview": PREVIEW_NEXT,
        "permit": PERMIT_NEXT,
        "fetch": FETCH_NEXT,
        "verify": VERIFY_NEXT,
        "deliver": DELIVER_NEXT,
        "roteiro-sync": ROTEIRO_SYNC_NEXT,
        "roteiro-check": ROTEIRO_CHECK_NEXT,
    }
    return by_step.get(step) or do.get("why")


def _has_preview(c):
    return any((c.get("preview") or {}).get(key) for key in PREVIEW_ARTIFACTS)


def _is_plugin_candidate(c):
    from .providers import BUILTIN_CAPABILITIES

    return c.get("provider") not in BUILTIN_CAPABILITIES


def _plugin_reference_note(c):
    """Frase extra do resumo de `preview --reference-only` de um candidato de plugin que
    ficou sem imagem: diz se a fonte não mandou miniatura ou se ela não baixou."""
    preview = c.get("preview") or {}
    if not _is_plugin_candidate(c) or preview.get("poster_path"):
        return ""
    if preview.get("poster_url"):
        return (
            " Sem imagem de referência: a miniatura da fonte (poster_url) não pôde ser baixada agora; "
            "tente a prévia de novo mais tarde antes de mostrar à pessoa."
        )
    return (
        " Sem imagem de referência: a fonte do plugin não mandou miniatura (poster_url), "
        "então não há nada para mostrar à pessoa."
    )


def _plugin_nothing_seen(c):
    """Candidato de plugin sem nada que a pessoa possa ter visto: sem prévia
    local, sem `poster_url` e sem `embed_url`. Fonte embutida nunca cai aqui."""
    preview = c.get("preview") or {}
    return _is_plugin_candidate(c) and not (_has_preview(c) or preview.get("poster_url") or preview.get("embed_url"))


def _refuse_blind_plugin_approval(c):
    if _plugin_nothing_seen(c):
        raise ValueError(
            f"Não registrei a aprovação de {c['id']}: a fonte do plugin não deu nada que a pessoa possa ter "
            "visto (nenhuma prévia local, nenhum poster_url nem embed_url). Sem material para mostrar, esta "
            "fonte não pode ser aprovada; peça ao autor do plugin uma miniatura (poster_url) ou um embed_url."
        )


def _stage_status(c, field):
    return (c.get(field) or {}).get("status")


# Um predicado por etapa: a mesma leitura serve para contagem, lista e item.
STAGE_TESTS = {
    "candidates": lambda _c: True,
    "previews": _has_preview,
    # Decisão pendente é decisão que *pode* ser tomada: sem prévia ninguém decide, e
    # contar o candidato recém-buscado como pendente inflava o número e mandava a
    # pessoa decidir algo que ela ainda não tem como ver.
    "pending": lambda c: _has_preview(c) and _stage_status(c, "approval") not in ("approved", "rejected"),
    "approved": lambda c: _stage_status(c, "approval") == "approved",
    "rejected": lambda c: _stage_status(c, "approval") == "rejected",
    "permitted": lambda c: _stage_status(c, "rights") == "permitted",
    "delivered": lambda c: bool((c.get("output") or {}).get("path")),
    "verified": lambda c: bool((c.get("output") or {}).get("verified")),
}


# `summary.next` quando `summary.do` manda a prévia de referência (fonte de plugin
# que só entrega o arquivo no `fetch`): o texto genérico "Gere prévias com preview…"
# contradizia o comando pronto ao lado. Fontes embutidas nunca chegam aqui.
REFERENCE_ONLY_NEXT = (
    "Registre a prévia de referência (preview --reference-only) do candidato cuja fonte só entrega "
    "o arquivo no fetch; depois approve, permit e fetch."
)


# `summary.next` quando o BRIEF.md ainda não tem beats porque o projeto tem ROTEIRO.md:
# buscar sem beat não tem alvo, e repetir `brief --validate` não muda nada.
ROTEIRO_SYNC_NEXT = (
    "Revise o ROTEIRO.md com a pessoa e rode `roteiro --action sync --project PROJETO`: os beats "
    "nascem dele. Depois, `brief --project PROJETO` mostra o comando pronto de cada beat."
)


# Mesmo degrau, antes da revisão: o sync recusaria, então o passo é conferir e mostrar.
ROTEIRO_CHECK_NEXT = (
    "Rode `roteiro --action check --project PROJETO`, mostre o roteiro à pessoa e registre a revisão "
    'com `roteiro --action review --project PROJETO --by NOME --channel chat --statement "…" --expect REVIEW_SHA256`; '
    "depois, `roteiro --action sync`."
)


ROTEIRO_DRIFT_PROBLEM = (
    "O ROTEIRO.md mudou desde o último sync: os beats do BRIEF.md não batem com as cenas. Revise o "
    "roteiro com a pessoa e rode `roteiro --action sync --project PROJETO`."
)


ROTEIRO_NO_BROLL = "o roteiro não pede b-roll"


ROTEIRO_NO_BROLL_NEXT = (
    "Nada para buscar: o roteiro não pede b-roll. Se a pessoa quiser, ponha uma cena [BROLL: ...] no "
    "ROTEIRO.md, revise com ela e rode `roteiro --action sync --project PROJETO`."
)


ROTEIRO_SYNC_PROBLEM = (
    "O BRIEF.md ainda não tem beats: eles vêm do ROTEIRO.md. Revise o roteiro com a pessoa e "
    "rode `roteiro --action sync --project PROJETO`."
)


def _reference_only_step(do):
    return do.get("step") == "preview" and "--reference-only" in (do.get("command") or "")
