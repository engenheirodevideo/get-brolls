"""Relatório do comando `brief`: validação do BRIEF.md e a lista de beats com o próximo comando de cada um."""

from pathlib import Path

from .guidance import blocked_beats_question, next_action
from .ledger import Ledger
from .status_report import (
    _flow_state,
    _format_pending,
    _roteiro_flags,
    blocked_entries,
    missing_beat_entry,
    retired_beat_message,
    roteiro_sync_needed,
    tried_searches,
)
from .summaries import (
    ROTEIRO_DRIFT_PROBLEM,
    ROTEIRO_NO_BROLL,
    ROTEIRO_NO_BROLL_NEXT,
    ROTEIRO_SYNC_NEXT,
    ROTEIRO_SYNC_PROBLEM,
    _count,
)


class _BriefContext:
    # pylint: disable=too-few-public-methods,too-many-arguments,too-many-positional-arguments
    # pylint: disable=too-many-instance-attributes
    # Contêiner simples de estado, não um objeto com comportamento.
    """Estado do BRIEF.md já carregado/validado, compartilhado pelas duas rotas de `brief`."""

    def __init__(  # noqa: PLR0913, PLR0917
        self, data, conflicts, problems, path, stalled, rules, awaiting_sync, no_broll, sync_problem
    ):
        self.data = data
        self.conflicts = conflicts
        self.problems = problems
        self.path = path
        self.stalled = stalled
        self.rules = rules
        self.awaiting_sync = awaiting_sync
        self.no_broll = no_broll
        self.sync_problem = sync_problem


def _load_brief_rules(args):
    """Lê RULES.md para o brief; devolve (rules, mensagem de erro ou None)."""
    from getbrolls.rules import load_rules

    try:
        return load_rules(args.project), None
    except (ValueError, OSError) as exc:
        return None, str(exc)


def _brief_sync_flags(project, beats):
    """Situação do sync com ROTEIRO.md: aguardando, sem beat ativo, e o texto do problema."""
    # Só com ROTEIRO.md do getbrolls: fora de sincronia, os beats vêm do sync do roteiro;
    # em dia e sem beat ativo, o roteiro simplesmente não pede b-roll.
    sync_needed = roteiro_sync_needed(project, beats)
    awaiting_sync = bool(sync_needed)
    no_broll = sync_needed is False and not beats
    sync_problem = ROTEIRO_SYNC_PROBLEM if not beats else ROTEIRO_DRIFT_PROBLEM
    return awaiting_sync, no_broll, sync_problem


def _load_brief_context(args):
    """Carrega e valida o BRIEF.md; monta a lista de problemas comuns às duas rotas de `brief`."""
    from getbrolls.brief import brief_path, load_brief, template_leftovers, validate_brief

    rules, rules_error = _load_brief_rules(args)
    data, conflicts = validate_brief(load_brief(args.project), rules, project=args.project)
    problems = list(conflicts)
    if rules_error:
        problems.append(f"RULES.md não pôde ser lido, então não conferi o formato: {rules_error}")
    path = str(brief_path(args.project))
    # Beats travados valem nas duas rotas: `--validate` chamava de "pode buscar" um
    # brief com todos os seis beats esperando um fato da pessoa.
    stalled = blocked_entries(data["beats"])
    problems += [f'O beat "{entry["id"]}" está travado esperando você: {entry["reason"]}' for entry in stalled]
    # Modelo intocado passa na validação de formato, mas não é um brief pronto.
    problems += template_leftovers(data)
    awaiting_sync, no_broll, sync_problem = _brief_sync_flags(args.project, data["beats"])
    if awaiting_sync:
        problems.append(sync_problem)
    return _BriefContext(data, conflicts, problems, path, stalled, rules, awaiting_sync, no_broll, sync_problem)


def _brief_validate_report(ctx):
    """Resposta de `brief --validate`: só o veredito e os pontos a resolver, sem comandos."""
    from getbrolls.brief import provider_warnings

    data, problems, stalled = ctx.data, ctx.problems, ctx.stalled
    beat_count = _count(len(data["beats"]), "beat", "beats")
    # Brief válido que só espera o sync: não é "ponto para resolver" no brief.
    only_sync = ctx.awaiting_sync and problems == [ctx.sync_problem]
    title = data["video"]["title"]
    return {
        "summary": {
            # "válido" só quando não sobrou nada para a pessoa resolver: um conflito
            # de formato ou um RULES.md ilegível não é um brief pronto para buscar.
            "line": (
                (
                    f'Brief de "{title}" válido, ainda sem beats: eles vêm do ROTEIRO.md.'
                    if not data["beats"]
                    else f'Brief de "{title}" válido, com {beat_count}, mas o ROTEIRO.md mudou desde o último sync.'
                )
                if only_sync
                else f'Brief de "{title}" lido, com {beat_count}, mas '
                + _count(len(problems), "ponto", "pontos")
                + " para resolver antes de buscar."
                if problems
                else f'Brief de "{title}" válido, sem beats ativos: {ROTEIRO_NO_BROLL}.'
                if ctx.no_broll
                else f'Brief de "{title}" válido: {beat_count}.'
            ),
            "problems": problems,
            "next": (
                # A mesma frase que `status.summary.do` daria: beat travado é
                # pergunta para a pessoa, e nenhum dos dois comandos pode dizer
                # "pode buscar" enquanto ela não responder.
                blocked_beats_question(stalled)
                if stalled
                else ROTEIRO_SYNC_NEXT
                if only_sync
                else "Resolva os pontos acima e repita `brief --validate --project ...`."
                if problems
                else ROTEIRO_NO_BROLL_NEXT
                if ctx.no_broll
                else "Pode buscar: `brief --project PROJETO` mostra o comando pronto de cada beat."
            ),
        },
        "brief": ctx.path,
        "valid": True,
        "beats": len(data["beats"]),
        "conflicts": ctx.conflicts,
        # Ambiente, não conteúdo: o brief segue válido sem a chave do provedor.
        "warnings": provider_warnings(data["beats"]),
    }


def _brief_select_beats(ctx, args):
    """Filtra para o beat pedido por --beat, ou recusa se ele não existir/está aposentado."""
    from getbrolls.brief import retired_beat_ids

    beats = ctx.data["beats"]
    beat_id = getattr(args, "beat", None)
    if not beat_id:
        return beats
    chosen = [b for b in beats if b["id"] == beat_id]
    if chosen:
        return chosen
    if beat_id in retired_beat_ids(args.project):
        # Aposentado não tem comando de busca: a frase diz por quê e lista os ativos.
        raise ValueError(
            retired_beat_message(beat_id) + " Os ids ativos são: " + ", ".join(b["id"] for b in beats) + "."
        )
    raise ValueError(
        f'O BRIEF.md não tem o beat "{beat_id}". Os ids disponíveis são: ' + ", ".join(b["id"] for b in beats) + "."
    )


def _brief_listed_beats(args, beats, manifest):
    """Cada beat com o comando pronto e os candidatos já registrados; pula os aposentados."""
    from getbrolls.brief import beat_commands, beat_progress

    progress = beat_progress(beats, manifest["items"])
    return [
        {
            "id": b["id"],
            "resolved": b["resolved"],
            "commands": beat_commands(args.project, b["resolved"], tried=tried_searches(manifest, b["id"])),
            "candidates": progress[b["id"]],
        }
        for b in beats
        # Fora de `progress` = beat aposentado: nem lista, nem degrau de busca.
        if b["id"] in progress
    ]


def _brief_coverage(ctx, args, manifest):
    """Beats com candidato/travado/faltando; acrescenta os que faltam a `ctx.problems`."""
    beats = _brief_select_beats(ctx, args)
    listed = _brief_listed_beats(args, beats, manifest)
    # Beat travado espera um fato da pessoa: ele não é "sem candidato ainda". A lista
    # já entrou em `problems` lá em cima, junto com a da rota `--validate`.
    blocked = [entry for entry in ctx.stalled if entry["id"] in {b["id"] for b in listed}]
    stuck = {entry["id"] for entry in blocked}
    missing = [entry for entry in listed if entry["id"] not in stuck and not entry["candidates"]]
    covered = len(listed) - len(missing) - len(blocked)
    ctx.problems += [f'O beat "{entry["id"]}" ainda não tem candidato registrado.' for entry in missing]
    return {"listed": listed, "covered": covered, "missing": missing, "blocked": blocked}


def _brief_next_action_state(ctx, args, ledger, coverage):
    """O mesmo `state` de `status` (`_flow_state`), com o bloco de beats deste `brief`.

    Montar um `state` à parte deixava de fora o que só `_flow_state` lê (o que falta
    entregar, quem ainda precisa de prévia, o formato pendente, a revisão do roteiro):
    o `brief` dizia "Fluxo completo" enquanto o `status` mandava rodar `deliver`.
    """
    from getbrolls.brief import search_plan

    manifest = ledger.data
    beats = ctx.data["beats"]
    block = {
        "beats": len(coverage["listed"]),
        "covered": coverage["covered"],
        "missing": [
            missing_beat_entry(
                entry["id"],
                entry["resolved"],
                entry["commands"],
                search_plan(entry["resolved"], tried_searches(manifest, entry["id"])),
            )
            for entry in coverage["missing"]
        ],
        "blocked": coverage["blocked"],
        "conflicts": ctx.conflicts,
        # Os mesmos sinais do roteiro que o `status` lê (sync, esqueleto, revisão).
        **_roteiro_flags(roteiro_sync_needed(args.project, beats), beats, args.project),
    }
    items = manifest["items"]
    format_pending = sum(1 for c in items if _format_pending(c, ctx.rules))
    return _flow_state(ledger, ctx.rules, format_pending=format_pending, brief=block)


def _brief_full_report(ctx, args):
    """Resposta completa de `brief`: beats com comando pronto, cobertura e o próximo passo."""
    # Só leitura: sem `brolls/`, o manifesto é vazio e nada é criado.
    ledger = Ledger(Path(args.project).expanduser(), recover=False)
    manifest = ledger.data
    coverage = _brief_coverage(ctx, args, manifest)
    listed, covered, missing, blocked = (
        coverage["listed"],
        coverage["covered"],
        coverage["missing"],
        coverage["blocked"],
    )
    data = ctx.data
    state = _brief_next_action_state(ctx, args, ledger, coverage)
    return {
        "summary": {
            "line": f'Brief de "{data["video"]["title"]}": '
            + _count(len(listed), "beat", "beats")
            + f", {covered} com candidato e {len(missing)} sem"
            + (f", {len(blocked)} travado(s) esperando você." if blocked else ".")
            + (f" Sem beats ativos: {ROTEIRO_NO_BROLL}." if ctx.no_broll else ""),
            "problems": ctx.problems,
            # Mesma escada de `status`: a pessoa ouve a mesma frase nos dois comandos.
            "next": next_action(state)["for_human"],
        },
        "brief": ctx.path,
        "video": data["video"],
        "rights": data["rights"],
        "defaults": data["defaults"],
        "beats": listed,
        "coverage": {
            "beats": len(listed),
            "covered": covered,
            "missing": len(missing),
            "blocked": len(blocked),
        },
        "conflicts": ctx.conflicts,
    }


def brief_report(args):
    """Beats do vídeo com defaults aplicados, comando pronto e o que já foi registrado.

    Somente leitura, como `status`: não cria a árvore do projeto nem grava no ledger.
    """
    ctx = _load_brief_context(args)
    if getattr(args, "validate", False):
        return _brief_validate_report(ctx)
    return _brief_full_report(ctx, args)
