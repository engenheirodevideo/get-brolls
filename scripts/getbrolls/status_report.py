"""Estado do fluxo de um projeto: `status`, `deliver`, o estado do `brief` e o que vem a seguir."""

import json
import logging

from . import _paths, labels, layout, logs
from .guidance import next_action
from .models import now
from .summaries import (
    EMPTY_STORYBOARD,
    ROTEIRO_NO_BROLL,
    STAGE_TESTS,
    STATUS_STAGES,
    _count,
    _has_preview,
    _stage_status,
    _status_line,
    summary_next,
)

_log = logs.get("commands")


def status_journal(root):
    """Leitura do journal append-only: quantos eventos e qual foi o último."""
    path = root / "events.jsonl"
    if not _paths.strict_is_file(path):
        return {"events": 0, "last": None}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        last = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        # Relatar o estado não pode falhar por causa de um log corrompido.
        return {
            "events": len(lines),
            "last": None,
            "error": "events.jsonl contém JSON inválido no último registro. Preserve o histórico e restaure o log.",
        }
    return {"events": len(lines), "last": last}


def status_references(root):
    """Quantas referências memorizadas o projeto tem, sem derrubar o relatório."""
    path = root / "references.json"
    if not _paths.strict_is_file(path):
        return 0, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return 0, ("references.json inválido ou incompatível. Preserve o arquivo e restaure uma cópia válida.")
    return len(items), None


def _format_pending(c, rules):
    """True quando o formato-alvo das regras atuais desfaria a decisão ou a prévia do item.

    Item rejeitado fica de fora: `sync_formats` só realinha o formato dele, sem invalidar
    nada, então o `status` não pode prometer uma aprovação invalidada nem pedir decisão.
    """
    if rules is None:
        return None
    if (c.get("approval") or {}).get("status") == "rejected":
        return False
    from getbrolls.rules import format_report

    return c.get("format", {}).get("target", "native") != format_report(c, rules)["target"]


def blocked_entries(beats):
    """Beats travados, na ordem do brief: `{id, reason, target}` para a escada ler.

    Uma leitura só para as duas rotas do `brief` e para o `status`: era a divergência
    entre elas que fazia um comando pedir o fato e o outro mandar buscar.
    """
    return [
        {
            "id": beat["id"],
            "reason": beat["resolved"]["blocked_reason"],
            "target": beat["resolved"].get("target"),
        }
        for beat in beats
        if beat["resolved"].get("blocked_reason")
    ]


def retired_beat_message(shot):
    """Frase de recusa para um beat aposentado pelo roteiro, igual em search, resolve e brief."""
    return (
        f'O beat "{shot}" foi aposentado pelo roteiro ("retired": true no BRIEF.md): a cena '
        "saiu do ROTEIRO.md, então não busco material para ele. Se a cena voltou, ajuste o "
        "ROTEIRO.md e rode `roteiro --action sync --project PROJETO`; `status --project PROJETO` "
        "mostra os beats ativos."
    )


# Teto do registro de buscas vazias: é memória de orientação, não histórico.
MAX_EMPTY_SEARCHES = 500


def _empty_log(data):
    """`empty_searches` do manifesto, só com entradas legíveis; lixo vira lista vazia."""
    log = (data or {}).get("empty_searches") if isinstance(data, dict) else None
    if not isinstance(log, list):
        return []
    return [e for e in log if isinstance(e, dict)]


def record_empty_searches(ledger, shot, empty):
    """Guarda no manifesto que `search --shot` voltou vazio nesta fonte, com esta query.

    É o que tira o degrau `brief-search` do laço: sem registro, a escada sugeria a
    mesma fonte e a mesma query para sempre, sem nunca tentar a próxima permitida.
    Um registro ilegível (editado à mão) é refeito, nunca derruba a busca.
    """
    log = _empty_log(ledger.data)
    for provider, query in empty:
        entry = {"shot": shot, "provider": provider, "query": query}
        if not any({k: e.get(k) for k in entry} == entry for e in log):
            log.append({**entry, "at": now()})
    ledger.data["empty_searches"] = log[-MAX_EMPTY_SEARCHES:]
    ledger.save("search-empty")


def tried_searches(data, shot):
    """Pares (fonte, query) em que a busca deste beat já voltou vazia."""
    return {
        (e.get("provider"), e.get("query"))
        for e in _empty_log(data)
        if e.get("shot") == shot and isinstance(e.get("provider"), str) and isinstance(e.get("query"), str)
    }


def missing_beat_entry(beat_id, resolved, commands, plan):
    """Beat sem candidato como a escada lê, igual em `status` e em `brief`."""
    state = plan["state"]
    return {
        "id": beat_id,
        "search": commands.get("search"),
        # Todas as fontes e buscas já voltaram vazias: pergunta para a pessoa.
        "exhausted": plan["sources"] if state == "exhausted" else [],
        "queries": plan["queries"] if state == "exhausted" else [],
        # Frase pronta do motivo (esgotado, falta de chave): a escada repassa como veio.
        "note": plan["note"],
        # A frase para a pessoa muda quando o beat não tem alvo literal: prometer
        # busca ali contradiz a guarda "literal primeiro, nada de preenchimento".
        "intent": resolved.get("intent"),
        "target": resolved.get("target"),
        # Toda fonte que sobra depende de uma chave que falta aqui: o degrau vira
        # pedido de configuração, não pergunta sobre o conteúdo do trecho.
        "unavailable": plan["needs_keys"] if state in ("unavailable", "needs_keys") else [],
        "unavailable_note": plan["note"] if state == "needs_keys" else None,
        # Nenhuma fonte com busca por API: o passo é a pessoa trazer o link ou o arquivo.
        "resolve": commands.get("resolve") if state == "resolve_only" else None,
        "allowed_sources": list(resolved.get("allowed_sources") or []),
    }


def _brief_or_none(project, rules):
    """Carrega e valida o BRIEF.md; devolve (data, conflicts, resultado antecipado ou None)."""
    from getbrolls.brief import brief_path, load_brief, validate_brief

    try:
        data, conflicts = validate_brief(load_brief(project), rules, project=project)
    except (ValueError, OSError) as exc:
        # Nada do brief quebra o `status`. Arquivo ausente é "faça o brief"; arquivo
        # presente e errado é "corrija o brief" — degraus e comandos diferentes.
        try:
            # lenient: inside except (ValueError, OSError), which maps any error to False
            exists = brief_path(project).exists()
        except (ValueError, OSError):
            exists = False
        return None, None, ({"error": str(exc)} if exists else None)
    return data, conflicts, None


def _missing_beat_entries(project, beats, manifest, stuck, progress):
    """Beats sem candidato registrado ainda (fora dos aposentados/travados), com seus comandos."""
    from getbrolls.brief import beat_commands, search_plan

    missing = []
    for b in beats:
        if b["id"] not in progress or b["id"] in stuck or progress[b["id"]]:
            # Fora de `progress` = beat aposentado: não vira degrau de busca.
            continue
        tried = tried_searches(manifest, b["id"])
        commands = beat_commands(project, b["resolved"], tried)
        missing.append(missing_beat_entry(b["id"], b["resolved"], commands, search_plan(b["resolved"], tried)))
    return missing


def brief_state(project, rules, items, data=None):
    """Cobertura dos beats para a escada de orientação, sem gravar nada no projeto.

    Devolve `None` quando não há BRIEF.md legível: esse é o degrau do topo da escada.
    `data` é o manifesto, para o degrau de busca pular a fonte que já voltou vazia.
    """
    from getbrolls.brief import beat_progress

    manifest = data
    data, conflicts, early = _brief_or_none(project, rules)
    if data is None:
        return early
    beats = data["beats"]
    progress = beat_progress(beats, items)
    # Beat travado sai das duas contas: ele não está coberto e também não é "ainda vou
    # buscar" — é pergunta em aberto para a pessoa, e vira degrau próprio na escada.
    blocked = blocked_entries(beats)
    stuck = {entry["id"] for entry in blocked}
    missing = _missing_beat_entries(project, beats, manifest, stuck, progress)
    return {
        "beats": len(beats),
        "covered": len(beats) - len(missing) - len(blocked),
        "missing": missing,
        "blocked": blocked,
        "conflicts": conflicts,
        # O que o `inspect` de um candidato do beat procura: a fala, ou o alvo.
        "beat_queries": {b["id"]: b["resolved"].get("narration") or b["resolved"]["target"] for b in beats},
        # Só com ROTEIRO.md do getbrolls: fora de sincronia, a escada manda para o sync;
        # em dia e sem beat ativo, o `status` diz que o roteiro não pede b-roll e segue.
        **_roteiro_flags(roteiro_sync_needed(project, beats), beats, project),
    }


def roteiro_sync_needed(project, beats):
    """None sem ROTEIRO.md do getbrolls; senão, se o brief ainda não reflete o roteiro. Só lê."""
    from getbrolls import roteiro, roteiro_sync

    if not roteiro.is_roteiro(project):
        return None
    return roteiro_sync.out_of_sync(project, [b["id"] for b in beats])


def _roteiro_reviewed(project):
    """O roteiro atual tem revisão válida? Só lê; roteiro que não abre conta como sem revisão."""
    from getbrolls import roteiro, roteiro_review

    try:
        return roteiro_review.review_state(project, roteiro.parse(roteiro.load_text(project)))["reviewed"]
    except (ValueError, OSError):
        return False


def _roteiro_skeleton(project):
    """Rótulos das cenas com texto de esqueleto do `roteiro new`; só lê, roteiro que não abre dá lista vazia."""
    from getbrolls import roteiro

    try:
        return [label for label, _ in roteiro.skeleton_scenes(roteiro.parse(roteiro.load_text(project)))]
    except (ValueError, OSError):
        return []


def _roteiro_flags(sync_needed, beats, project):
    if sync_needed:
        skeleton = _roteiro_skeleton(project)
        if skeleton:
            # Review e sync recusam texto de esqueleto: o degrau é editar o roteiro.
            return {"roteiro_sync": True, "roteiro_skeleton": skeleton}
        # Sem revisão, o sync recusa: o degrau é o `check`, que mostra o hash para o `review`.
        return {"roteiro_sync": True, **({} if _roteiro_reviewed(project) else {"roteiro_unreviewed": True})}
    if sync_needed is False and not beats:
        return {"roteiro_no_broll": True}
    return {}


def _rights_mode(rules):
    return ((rules or {}).get("copyright") or {}).get("mode", "per_item_evidence")


# Degrau → (item já está nesta etapa?, item precisa estar nesta anterior?).
_PENDING_STAGES = (
    (lambda c: not STAGE_TESTS["previews"](c), lambda _c: True),
    (lambda c: not STAGE_TESTS["permitted"](c), STAGE_TESTS["approved"]),
    (lambda c: not STAGE_TESTS["delivered"](c), STAGE_TESTS["permitted"]),
)


def _pending_candidate(items):
    """Primeiro item que o próximo comando tocaria: o comando sai com id real, não `ID`."""
    for pending, ready in _PENDING_STAGES:
        for c in items:
            if ready(c) and pending(c):
                return c["id"]
    return None


def _step_candidates(items):
    """Um id por degrau, sempre de um item que está mesmo naquela etapa.

    Um único "próximo candidato" para a escada inteira nomeava o primeiro item da
    ordem do manifesto, e num projeto com um rejeitado na frente o degrau `permit`
    saía com o id desse rejeitado: o comando vinha pronto e a CLI recusava na cara
    da pessoa. Cada degrau filtra pela própria etapa, e quem foi rejeitado não
    entra em nenhum deles.
    """
    alive = [c for c in items if _stage_status(c, "approval") != "rejected"]

    def first(pool, test):
        return next((c["id"] for c in pool if test(c)), None)

    return {
        "inspect": first(_uninspected(items), lambda _c: True),
        "preview": first(_needs_preview(items), lambda _c: True),
        "approve": first(alive, STAGE_TESTS["pending"]),
        "permit": first(alive, lambda c: STAGE_TESTS["approved"](c) and not STAGE_TESTS["permitted"](c)),
        "fetch": first(alive, lambda c: STAGE_TESTS["permitted"](c) and not STAGE_TESTS["delivered"](c)),
        "verify": first(alive, lambda c: STAGE_TESTS["delivered"](c) and not STAGE_TESTS["verified"](c)),
    }


def _uninspected(items):
    """Candidatos sem duração conhecida e ainda sem prévia, com URL pública para analisar.

    É o que separa o degrau `inspect` do degrau `preview`: sem duração, qualquer
    `--start/--end` é palpite, e o palpite custa um pedido à fonte. Item rejeitado
    fica de fora: ninguém gasta pedido à fonte por um trecho já descartado. Foto
    também: não tem duração nem trecho a descobrir, e vai direto à prévia estática.
    """
    from .acquisition import fetch_only

    return [
        c
        for c in items
        if not (c.get("media") or {}).get("duration_s")
        and (c.get("media") or {}).get("kind") != "image"
        and c.get("source_url")
        and not _has_preview(c)
        and _stage_status(c, "approval") != "rejected"
        # Fonte que só entrega no `fetch`: `inspect` é recusado antes de rodar o plugin.
        and not fetch_only(c)
    ]


def _reference_only(items):
    """Ids dos itens sem quadro cuja fonte só entrega o arquivo no `fetch` (rota de
    plugin `stage="fetch"`): a prévia deles é `--reference-only`, nunca com mídia."""
    from .acquisition import fetch_only

    return [c["id"] for c in _needs_preview(items) if fetch_only(c)]


def serve_state(project):
    """`serve.running` do relatório: nunca cria nem limpa nada no projeto."""
    from getbrolls.serve import state

    try:
        return state(project)
    except OSError as exc:
        return {"running": False, "error": str(exc)}


def deliver_report(ledger, rules, dry_run=False):
    """Refaz `entrega/` e responde com o resumo humano primeiro, como os outros comandos."""
    from getbrolls.delivery import build_delivery

    # A frase só é calculada depois da materialização (o `build_delivery` chama este
    # callable no fim), senão o índice mandaria rodar o comando que acabou de rodar.
    report = build_delivery(
        ledger.root.parent,
        dry_run=dry_run,
        ledger=ledger,
        for_human=lambda: _delivery_next(ledger, rules),
        rules=rules,
    )
    # A real run raises before returning when any file is in conflict; only the dry
    # run comes back with `conflicts`, which the log line and the summary count.
    conflicts = len(report.get("conflicts", []))
    logs.event(
        _log,
        logging.INFO,
        "deliver",
        delivered=len(report["items"]),
        skipped=len(report["skipped"]),
        conflicts=conflicts,
        dry_run=bool(dry_run),
    )
    verb = "Organizaria" if dry_run else "Organizei"
    beats = len({item["beat"] for item in report["items"]})
    line = (
        f"{verb} {_count(len(report['items']), 'trecho', 'trechos')} em "
        + _count(beats, "pasta", "pastas")
        + f" dentro de {labels.shown(report['delivery'])}."
    )
    if report["removed"]:
        line += " " + _count(len(report["removed"]), "link órfão removido", "links órfãos removidos") + "."
    if report["kept"]:
        line += " Deixei intocado o que você criou lá: " + ", ".join(report["kept"]) + "."
    if conflicts:
        line += (
            " "
            + _count(conflicts, "conflito faria", "conflitos fariam")
            + " a entrega de verdade falhar (veja `conflicts`)."
        )
    # Mesma escada de `status` e `brief`: a pessoa ouve a mesma frase em qualquer comando.
    return {"summary": {"line": line, "next": _flow_next(ledger, rules)}, **report}


def _delivery_next(ledger, rules):
    """O "Próximo passo" do `entrega/README.md`, que não é o mesmo da conversa.

    Com entrega pronta e prévia sem decisão, a escada normal mandaria subir o
    Storyboard — mas quem está lendo esse README está na pasta de arquivos, não na
    conversa, e o servidor pode nem estar de pé. Aqui o texto diz o que está pronto
    e o que ficou pendente, sem prometer nada que este arquivo não possa cumprir.
    """
    items = ledger.data["items"]
    delivered = sum(1 for c in items if (c.get("delivery") or {}).get("path"))
    pending = sum(1 for c in items if STAGE_TESTS["pending"](c))
    if delivered and pending:
        return (
            f"Entrega pronta ({_count(delivered, 'trecho', 'trechos')}). "
            f"Há {_count(pending, 'prévia', 'prévias')} sem decisão no projeto — "
            "decida ou rejeite."
        )
    return _flow_next(ledger, rules)


def _preview_is_image(items):
    """O item que o degrau `preview` nomeia é uma foto? Então o comando vai sem intervalo."""
    chosen = _step_candidates(items)["preview"]
    return any(c["id"] == chosen and (c.get("media") or {}).get("kind") == "image" for c in items)


def _needs_preview(items):
    """Itens ainda em jogo e sem nenhum quadro: um item rejeitado não trava o fluxo."""
    return [c for c in items if not _has_preview(c) and _stage_status(c, "approval") != "rejected"]


def _undelivered(items, retired=frozenset()):
    """Arquivos já conferidos que ainda não apareceram em `entrega/`.

    Clipe de beat aposentado (`retired`, ids do BRIEF.md) fica fora de `entrega/` de
    propósito: contá-lo aqui deixaria o fluxo pedindo `deliver` para sempre.
    """
    return [
        c
        for c in items
        if STAGE_TESTS["verified"](c)
        and not (c.get("delivery") or {}).get("path")
        and not (retired and c.get("shot") in retired)
    ]


def _retired_shots(project):
    from getbrolls.brief import retired_beat_ids

    return retired_beat_ids(project)


_UNSET = object()


def _live_board_url(project):
    """URL do Storyboard quando o servidor desta sessão responde; senão None.

    Somente leitura: `serve.read_pid` lê o arquivo e `serve.state` confirma a
    identidade por um ping. Sem `.serve.pid` não há consulta nenhuma — e sem
    servidor no ar não inventamos porta, porque `serve --background` usa uma porta
    livre qualquer quando a padrão está ocupada.
    """
    from . import serve

    try:
        if not serve.read_pid(project):
            return None
        info = serve.state(project)
    except (OSError, ValueError):
        return None
    if not info.get("running"):
        return None
    urls = [u for u in (info.get("urls") or []) if isinstance(u, str) and u.endswith("review.html")]
    for url in urls:
        if "127.0.0.1" in url:
            return url
    if urls:
        return urls[0]
    port = info.get("port")
    return f"http://127.0.0.1:{port}/review.html" if port else None


def _flow_state(ledger, rules, counts=None, format_pending=0, brief=_UNSET):
    """Estado que a escada de `guidance` lê: a mesma leitura em status, brief e deliver."""
    items = ledger.data["items"]
    review_page = _paths.strict_is_file(ledger.root / "review.html")
    brief_value = brief_state(ledger.root.parent, rules, items, ledger.data) if brief is _UNSET else brief
    return {
        "project": str(ledger.root.parent),
        "counts": counts or {key: sum(1 for c in items if test(c)) for key, test in STAGE_TESTS.items()},
        "format_pending": format_pending,
        "brief": brief_value,
        "review_page": review_page,
        # Só perguntamos ao servidor quando existe página para ele servir.
        "board_url": _live_board_url(ledger.root.parent) if review_page else None,
        "rights_mode": _rights_mode(rules),
        "candidate": _pending_candidate(items),
        # Um id por degrau: o comando pronto nunca nomeia item de outra etapa.
        "candidates": _step_candidates(items),
        "duration_unknown": len(_uninspected(items)),
        "inspect_candidate": next((c["id"] for c in _uninspected(items)), None),
        "reference_only": _reference_only(items),
        "inspect_query": _inspect_query(items, brief_value),
        "undelivered": len(_undelivered(items, _retired_shots(ledger.root.parent))),
        "pending_preview": len(_needs_preview(items)),
        "preview_image": _preview_is_image(items),
    }


def _inspect_query(items, brief):
    """`--query` pronto para o `inspect` do degrau: a fala (ou o alvo) do beat do candidato.

    Sem beat no brief, o lugar fica em MAIÚSCULAS para quem conhece a frase preencher.
    """
    first = next(iter(_uninspected(items)), None)
    queries = (brief or {}).get("beat_queries") or {}
    return queries.get((first or {}).get("shot")) or None


def _flow_next(ledger, rules):
    """A frase única para repassar à pessoa, sem parafrasear."""
    try:
        return next_action(_flow_state(ledger, rules))["for_human"]
    except (ValueError, OSError, KeyError):
        return None


class _StatusContext:  # pylint: disable=too-few-public-methods
    # Contêiner simples de estado derivado, não um objeto com comportamento.
    """Estado derivado de `status`, compartilhado entre o resumo falado e a lista de itens."""

    def __init__(  # noqa: PLR0913, PLR0917 -- pylint: disable=too-many-arguments,too-many-positional-arguments
        self, ledger, rules, items, counts, format_pending, listing, pending_format
    ):
        self.ledger = ledger
        self.rules = rules
        self.items = items
        self.counts = counts
        self.format_pending = format_pending
        self.listing = listing
        self.pending_format = pending_format


def _status_summary(ctx, queue, review_page, brief):
    """Monta o bloco `summary`: linha falada, degrau seguinte e contagem do brief."""
    ledger, rules, counts, format_pending = ctx.ledger, ctx.rules, ctx.counts, ctx.format_pending
    line = _status_line({"counts": counts})
    if brief and brief.get("roteiro_no_broll"):
        line += f" Sem beats ativos: {ROTEIRO_NO_BROLL}."
    if ledger.recovered:
        line += " Há uma gravação interrompida pendente; o próximo comando de escrita a concluirá."
    if queue and queue.get("error"):
        # queue.hint devolveu {"error": ...} (queue.json inválido/OSError): não some do resumo.
        line += f" Fila indisponível: {queue['error']}"
    elif queue and queue.get("line"):
        # Uma linha da fila social, lida sem gravar: contagens e próximo horário permitido.
        line += " " + queue["line"]
    state = _flow_state(
        ledger,
        rules,
        counts=counts,
        format_pending=format_pending,
        brief=brief,
    )
    do = next_action(state)
    # Veredito primeiro, como no doctor: o JSON completo continua logo abaixo.
    return {
        "line": line,
        "stages": [
            {"stage": plural, "count": counts[key], "items": ctx.listing[key]} for key, _, plural in STATUS_STAGES
        ],
        # O mesmo degrau de `do`, no texto para o agente: as duas chaves nunca
        # nomeiam passos diferentes.
        "next": summary_next(do, state),
        # Aditivo: `line/stages/next` seguem iguais; `do` traz o mesmo passo já em
        # comando pronto e `brief` diz quantos beats ainda estão sem material.
        "do": do,
        # Mesmo aviso de `review`: a página existe mas não tem o que revisar.
        "warnings": ([EMPTY_STORYBOARD] if _paths.strict_is_file(review_page) and not counts["previews"] else []),
        # `None` enquanto não houver um brief válido para contar (ausente ou inválido).
        "brief": (
            None
            if brief is None or brief.get("error")
            else {
                "beats": brief["beats"],
                "covered": brief["covered"],
                "missing": len(brief["missing"]),
                # Beats que esperam um fato da pessoa: nem cobertos, nem a buscar.
                "blocked": len(brief.get("blocked") or []),
            }
        ),
    }


def _status_items(ctx):
    """Lista enxuta de itens para o `status`: só os campos que a pessoa decide a partir de."""
    return [
        {
            "id": c["id"],
            "title": c.get("title"),
            "provider": c.get("provider"),
            "source_url": c.get("source_url"),
            "state": c.get("state"),
            "segment": c.get("segment"),
            "approval": (c.get("approval") or {}).get("status"),
            # Por que este saiu: quem lê a lista não precisa abrir o manifesto.
            "rejection_reason": (c.get("rejection") or {}).get("reason"),
            "rights": (c.get("rights") or {}).get("status"),
            "preview": _has_preview(c),
            "format_pending": ctx.pending_format[c["id"]],
            "output": (c.get("output") or {}).get("path"),
        }
        for c in ctx.items
    ]


def _status_layout(project):
    """`{version, source, problem}` do layout do projeto; somente leitura."""
    found = layout.info(project)
    return {"version": found.version, "source": found.source, "problem": found.problem}


def status_report(ledger, rules=None, rules_error=None, queue=None):
    """Onde o projeto está, por etapa. Somente leitura: não grava nada."""
    items = ledger.data["items"]
    listing = {key: [c["id"] for c in items if STAGE_TESTS[key](c)] for key, _, _ in STATUS_STAGES}
    counts = {key: len(listing[key]) for key, _, _ in STATUS_STAGES}
    pending_format = {c["id"]: _format_pending(c, rules) for c in items}
    format_pending = sum(1 for value in pending_format.values() if value)
    ctx = _StatusContext(ledger, rules, items, counts, format_pending, listing, pending_format)
    remembered, references_error = status_references(ledger.root)
    review_page = ledger.root / "review.html"
    brief = brief_state(ledger.root.parent, rules, items, ledger.data)
    app_log = ledger.root / "getbrolls.log"
    return {
        "summary": _status_summary(ctx, queue, review_page, brief),
        "project": str(ledger.root),
        "counts": counts,
        "stages": listing,
        # Aditivo, ao lado de "review_page": onde o getbrolls.log está, se existir.
        # lenient: pointer to the log; logs.log_path is a never-raise view
        "log": str(app_log) if app_log.is_file() else None,
        "items": _status_items(ctx),
        "format_pending": format_pending,
        # Somente leitura: lê o PID file e pergunta ao sistema se o processo vive.
        "serve": serve_state(ledger.root.parent),
        "layout": _status_layout(ledger.root.parent),
        "rules_error": rules_error,
        "references": remembered,
        "references_error": references_error,
        "queue": queue,
        "review_page": str(review_page) if _paths.strict_is_file(review_page) else None,
        "journal": {
            **status_journal(ledger.root),
            "recovered_write": "pending" if ledger.recovered else False,
        },
    }
