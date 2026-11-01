"""`search` e `resolve`: busca nas fontes, registro dos candidatos e o resumo falado da busca."""

import logging
import re
from pathlib import Path

from . import _paths, labels, logs
from .fetch_commands import fill_remote_metadata
from .ledger import digest
from .media import probe
from .models import candidate
from .runtime import record_warning
from .status_report import record_empty_searches, retired_beat_message
from .summaries import _count, provider_error_text

_log = logs.get("commands")


# Teto de palavras que uma busca leva à fonte. Acima disso a query é uma oração, e
# API de vídeo casa por palavra: a frase inteira volta vazia sem explicar por quê.
SEARCH_QUERY_TOKENS = 6


# Quantos títulos o resumo falado de busca mostra antes de dizer "e mais N na lista".
SEARCH_SUMMARY_PREVIEW_TITLES = 3


# `--shot` de `search` e de `resolve` valem a mesma coisa: o beat vira sufixo do id.
SHOT_RE = r"[A-Za-z0-9_-]{1,80}"


def _search_row(c):
    """A cópia que sai no JSON: o candidato mais o que a fonte já sabe e o manifesto não guarda.

    Cópia rasa de propósito. `channel`/`uploader` e `duration_s` são atalhos de
    leitura para quem consome a busca; colar isso no candidato guardado mudaria o
    schema do manifesto sem necessidade.
    """
    row = dict(c)
    channel = ((c.get("creator") or {}).get("name") or "").strip()
    if channel:
        row["channel"] = channel
        row["uploader"] = channel
    duration = (c.get("media") or {}).get("duration_s")
    if duration is not None:
        row["duration_s"] = duration
    return row


def search_summary_line(  # noqa: PLR0913, PLR0917 - existing size; one field per fact the spoken summary line reports
    rows,
    excluded,
    errors,
    dry_run,
    query_used=None,
    retry=None,
):  # pylint: disable=too-many-arguments,too-many-positional-arguments
    # Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0).
    """Quantos vieram e quais são os três primeiros — o resumo que cabe numa fala.

    Zero candidatos nunca sai calado: a linha diz qual query a fonte recebeu e, se
    houve encurtamento automático, que ele aconteceu.
    """
    if not rows:
        line = "Nenhum candidato veio dessa busca."
        if query_used:
            line += f' A fonte procurou por "{query_used}".'
        if retry:
            line += " " + retry["note"]
        else:
            line += " Tente termos mais curtos (entidade + ação), ou registre a URL direto com `resolve --url`."
    else:
        titles = [str(r.get("title") or r.get("id")) for r in rows[:SEARCH_SUMMARY_PREVIEW_TITLES]]
        line = f"{_count(len(rows), 'candidato', 'candidatos')}: " + ", ".join(titles) + "."
        if len(rows) > SEARCH_SUMMARY_PREVIEW_TITLES:
            line += f" (+{len(rows) - SEARCH_SUMMARY_PREVIEW_TITLES} na lista)"
    if excluded:
        line += f" {_count(excluded, 'excluído pelas regras', 'excluídos pelas regras')}."
    if errors:
        line += f" {_count(len(errors), 'fonte falhou', 'fontes falharam')}."
    if dry_run:
        line += " Diagnóstico: nada foi registrado no projeto."
    if rows and retry:
        line += " " + retry["note"]
    return line


def _refuse_retired_shot(project, shot):
    """`--shot` de beat aposentado não liga material novo a ele: recusa antes de buscar ou baixar."""
    from getbrolls.brief import retired_beat_ids

    if shot in retired_beat_ids(project):
        raise ValueError(retired_beat_message(shot))


def _beat_search_names(project, shot, rules, provider, names):
    """Fontes que `search --shot` pode consultar: as que o beat do BRIEF.md permite.

    Sem BRIEF.md válido, ou com um `--shot` que não é beat dele, nada muda: o beat
    livre continua valendo como antes. Com o beat no brief, fonte fora de
    `allowed_sources` é recusada com a lista certa, em vez de virar candidato ligado
    ao beat como se a pessoa tivesse permitido aquela origem.
    """
    from getbrolls.brief import beat_sources
    from getbrolls.brief import searchable as searchable_sources

    _refuse_retired_shot(project, shot)
    allowed = beat_sources(project, shot, rules)
    if allowed is None:
        return names
    searchable = [name for name in allowed if name in searchable_sources()]
    if provider != "auto":
        if provider in allowed:
            return names
        route = (
            f"Busque com --provider {searchable[0]} (ou --provider auto, que fica nas fontes do beat)."
            if searchable
            else f"Nenhuma dessas fontes tem busca por API: registre o material com "
            f"`resolve --url URL_PUBLICA --shot {shot}` ou `resolve --file ARQUIVO --shot {shot}`."
        )
        raise ValueError(
            f'O beat "{shot}" do BRIEF.md só aceita material de {", ".join(allowed)}, e {provider} '
            f"não está nessa lista. {route} Se {provider} também serve para esse trecho, "
            "acrescente a fonte em allowed_sources do beat e rode `brief --validate`."
        )
    chosen = [name for name in names if name in allowed] or searchable
    if not chosen:
        raise ValueError(
            f'O beat "{shot}" do BRIEF.md só aceita {", ".join(allowed)}, e nenhuma dessas '
            f"fontes tem busca por API: registre o material com `resolve --url URL_PUBLICA "
            f"--shot {shot}` ou `resolve --file ARQUIVO --shot {shot}`."
        )
    return chosen


class _SearchContext:  # pylint: disable=too-few-public-methods
    # Contêiner simples de estado, não um objeto com comportamento.
    """Estado compartilhado de uma chamada `search`: request, regras, ledger e fontes escolhidas."""

    def __init__(  # noqa: PLR0913, PLR0917 -- pylint: disable=too-many-arguments,too-many-positional-arguments
        self, args, rules, ledger, names, shot, dry_run
    ):
        self.args = args
        self.rules = rules
        self.ledger = ledger
        self.names = names
        self.shot = shot
        self.dry_run = dry_run
        # Ids que as regras barraram, somando a frase inteira e o retry encurtado: a query
        # curta é mais ampla e costuma devolver os mesmos barrados, que contam uma vez só.
        self.excluded = set()


def _search_keep_candidates(ctx, candidates):
    """Filtra/decora os candidatos de uma fonte; devolve (itens mantidos, registrados).

    Os barrados pelas regras vão para `ctx.excluded`.
    """
    from getbrolls.rules import allowed, format_report

    args, rules, ledger = ctx.args, ctx.rules, ctx.ledger
    kept_items = []
    registered = 0
    for c in candidates:
        if not allowed(c, rules):
            ctx.excluded.add(c["id"])
            continue
        c["format"] = format_report(c, rules)
        c["match"] = {
            "kind": args.intent,
            "reason": "Candidato de busca: correspondência visual deve ser revisada.",
        }
        if ctx.shot:
            c["id"] += ":shot:" + ctx.shot
            c["shot"] = ctx.shot
        if ctx.dry_run:
            # Busca de diagnóstico não entra no manifesto: a contagem do
            # `status` é do vídeo, não do que o agente experimentou.
            kept_items.append(c)
            continue
        # Já registrado: vale o que está no manifesto. A fonte pode ter editado o
        # título desde então, e o `add` recusaria a procedência nova — derrubando a
        # busca inteira no meio, com metade já gravada. O `resolve` continua recusando.
        known = next((x for x in ledger.data["items"] if x["id"] == c["id"]), None)
        added = known if known is not None else ledger.add(c)
        ledger.save("search", added)
        kept_items.append(added)
        registered += 1
    return kept_items, registered


def _search_record_provider_failure(ctx, name, query, exc):
    """Registra a falha de uma fonte na busca e, fora de --dry-run, na biblioteca como `miss`."""
    from getbrolls import library

    record_warning("PROVIDER_FAILED", provider_error_text(name, exc))
    # Fonte que falhou é aprendizado barato e honesto; fica marcado
    # como `auto` porque ninguém digitou esse registro. Em `--dry-run`,
    # não: a busca de diagnóstico não escreve em lugar nenhum, e uma
    # falha de teste não pode virar memória editorial do usuário.
    if ctx.dry_run:
        return
    try:
        library.learn_query(query, name, "miss", note=str(exc), auto=True, project=ctx.args.project)
    except (ValueError, OSError) as failure:
        record_warning("LIBRARY_WRITE_FAILED", labels.reason(failure))


def _search_one_provider(ctx, name, query, so_far, retry):
    """Busca numa fonte só; devolve (itens mantidos, erro-ou-None, quantos ficaram registrados)."""
    from getbrolls import providers

    args = ctx.args
    try:
        candidates = providers.search(name, query, args.limit - so_far, media=getattr(args, "media", "any"))
    except ValueError as exc:
        _search_record_provider_failure(ctx, name, query, exc)
        return [], {"provider": name, "error": str(exc)}, 0
    # ledger.add/save stay outside the provider try: a disk/write error here is not the
    # provider's fault and must not be attributed to it as a search failure.
    kept_items, registered = _search_keep_candidates(ctx, candidates)
    logs.event(
        _log,
        logging.INFO,
        "search",
        provider=name,
        media=getattr(args, "media", "any"),
        intent=args.intent,
        shot=ctx.shot,
        query_words=len(query.split()),
        results=len(kept_items),
        retry=retry,
    )
    return kept_items, None, registered


def _search_sweep(ctx, query, retry=False):
    """Uma varredura pelos provedores escolhidos, com esta query exata."""
    args = ctx.args
    items, errors = [], []
    answered = []
    for name in ctx.names:
        if len(items) >= args.limit:
            break
        kept_items, error, registered = _search_one_provider(ctx, name, query, len(items), retry)
        items.extend(kept_items)
        if error is not None:
            errors.append(error)
            continue
        answered.append((name, query, registered))
    return items, errors, answered


def _search_with_shortening(ctx):
    """Varre com a query pedida; se vier vazia e for longa, tenta de novo encurtada uma vez."""
    args = ctx.args
    query_used = args.query
    items, errors, answered = _search_sweep(ctx, query_used)
    # Frase inteira vira query e volta vazia: as APIs casam por palavra, e uma
    # oração de doze palavras não casa com título nenhum. Em vez de devolver
    # `items: []` calado, encurta uma vez e conta o que fez.
    retry = None
    tokens = args.query.split()
    if not items and not errors and len(tokens) > SEARCH_QUERY_TOKENS:
        # Mesmo corte que `brief --beat` usa para montar a query do beat: tira as
        # palavras que não estreitam nada e fica com as primeiras que sobraram.
        from getbrolls.brief import search_query

        short = search_query({"target": args.query, "queries": []}, SEARCH_QUERY_TOKENS)
        # Veio e foi barrado não é "não trouxe nada": a nota diz qual dos dois aconteceu.
        outcome = "só trouxe material barrado pelas regras" if ctx.excluded else "não trouxe nada"
        items, errors, retry_answered = _search_sweep(ctx, short, retry=True)
        answered = answered + retry_answered
        retry = {
            "from": args.query,
            "to": short,
            "note": (
                f'A busca por "{args.query}" {outcome}, então repeti uma vez '
                f'com as {SEARCH_QUERY_TOKENS} primeiras palavras ("{short}"): '
                "banco e YouTube casam por palavra, não por frase inteira."
            ),
        }
        query_used = short
    return {
        "query_used": query_used,
        "items": items,
        "errors": errors,
        # O que as regras barraram na frase inteira continua barrado depois do retry.
        "excluded": len(ctx.excluded),
        "retry": retry,
        "answered": answered,
    }


def _build_search_result(ctx, outcome):
    """Monta o envelope de resposta de `search`: resumo, aviso de dry-run e pistas da biblioteca."""
    from getbrolls import library
    from getbrolls.rules import domain_matches

    args, rules, dry_run = ctx.args, ctx.rules, ctx.dry_run
    query_used, items, errors, excluded, retry = (
        outcome["query_used"],
        outcome["items"],
        outcome["errors"],
        outcome["excluded"],
        outcome["retry"],
    )
    items.sort(key=lambda c: not domain_matches(c.get("source_url"), rules["preferred_domains"]))
    shown = [_search_row(c) for c in items]
    result = {
        # Veredito primeiro: quem lê o JSON quer saber o que apareceu antes de
        # abrir a lista inteira.
        "summary": {"line": search_summary_line(shown, excluded, errors, dry_run, query_used, retry)},
        "items": shown,
        "errors": errors,
        "excluded_by_rules": excluded,
        "editorial_rules": rules["editorial_rules"],
        "dry_run": dry_run,
        "query": args.query,
        # O que a fonte recebeu de fato: sem isto o encurtamento seria invisível.
        "query_used": query_used,
    }
    if retry:
        result["retry"] = retry
    if dry_run:
        result["note"] = (
            "Busca de diagnóstico: nada foi registrado no projeto. Repita sem "
            "`--dry-run` para guardar os candidatos que você quiser."
        )
    # Pistas da biblioteca são memória editorial, não permissão: cada uma
    # repete `rights_not_transferable` e nenhuma toca no candidato.
    found = library.hints(args.query, project=args.project)
    if found:
        result["library_hints"] = found
    return result


def _search_names(args, rules, shot):
    """Lista de fontes a varrer: as preferidas do usuário, ou uma só se --provider foi explícito."""
    names = rules["preferred_providers"][args.intent] if args.provider == "auto" else [args.provider]
    if shot:
        names = _beat_search_names(args.project, shot, rules, args.provider, names)
    if not names:
        raise ValueError(
            "Nenhuma fonte configurada: use resolve --file, Commons/NASA ou configure a chave de um banco."
        )
    return names


def _execute_search(args, rules, ledger):
    """`search`: varre os provedores escolhidos, com o encurtamento automático de frase longa."""
    if "video" not in rules["asset_types"]:
        return {
            "items": [],
            "errors": [],
            "note": "APIs atuais pesquisam vídeos. Para imagem/notícia use importação local ou browser-plan.",
        }
    args.provider = {"pixel": "pexels", "getbrolls": "auto"}.get(args.provider, args.provider)
    if not 1 <= args.limit <= 50:  # noqa: PLR2004 - matches the "--limit entre 1 e 50" message below
        raise ValueError("Use --limit entre 1 e 50.")
    shot = (getattr(args, "shot", None) or "").strip() or None
    # Mesma regra de `resolve --shot`: o beat vira sufixo do id, e é isso que
    # liga o candidato ao BRIEF.md sem precisar re-registrar por URL depois.
    if shot and not re.fullmatch(SHOT_RE, shot):
        raise ValueError("--shot: use 1–80 letras, números, hífen ou underscore.")
    dry_run = bool(getattr(args, "dry_run", False))
    names = _search_names(args, rules, shot)
    ctx = _SearchContext(args, rules, ledger, names, shot, dry_run)
    outcome = _search_with_shortening(ctx)
    # Fonte que respondeu sem nada para este beat: a escada não sugere de novo.
    # Só o resultado final conta: se o encurtamento achou algo, a busca não foi vazia.
    # O registro leva a query pedida, que é a que a escada sugere de novo.
    if shot and not outcome["items"] and not dry_run:
        empty = [(name, args.query) for name, _query, _kept in outcome["answered"]]
        if empty:
            record_empty_searches(ledger, shot, empty)
    if not outcome["items"] and outcome["errors"]:
        raise ValueError("; ".join(provider_error_text(e["provider"], e["error"]) for e in outcome["errors"]))
    return _build_search_result(ctx, outcome)


def _resolve_build_candidate(args):
    """Cria o candidato a partir de --file (import local) ou --url (fonte remota)."""
    from getbrolls import providers

    if args.file:
        path = Path(args.file).expanduser().resolve()
        if not _paths.strict_is_file(path):
            raise ValueError("Arquivo local inexistente.")
        sha = digest(path)
        c = candidate("local", sha[:16], path.name)
        c["local_path"] = str(path)
        c["local_sha256"] = sha
        c["media"] = probe(path)
        c["acquisition"] = {
            "status": "available",
            "method": "local",
            "evidence": [],
        }
        c["preview"]["seek_mode"] = "local"
        return c
    c = providers.resolve(args.url)
    fill_remote_metadata(c)
    return c


def _resolve_context_files(c, args):
    """Anexa --context-image/--full-preview-file ao candidato, com hash e mídia."""
    for argument, field in (
        (args.context_image, "context_image"),
        (args.full_preview_file, "full_preview"),
    ):
        if not argument:
            continue
        if not args.file:
            raise ValueError("Contexto/composição exigem um B-roll local em --file.")
        auxiliary = Path(argument).expanduser().resolve()
        if not _paths.strict_is_file(auxiliary):
            raise ValueError("Arquivo de contexto/composição não encontrado.")
        if field == "context_image" and auxiliary.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise ValueError("--context-image deve ser um print PNG/JPG/WebP.")
        c[field + "_path"] = str(auxiliary)
        c[field + "_sha256"] = digest(auxiliary)
        c[field + "_media"] = probe(auxiliary)


def _resolve_local_metadata(c, args):
    """Infere/valida asset-type, título e data de captura para um `resolve --file`."""
    if not args.file:
        if args.asset_type or args.title or args.captured_at:
            raise ValueError("Metadados locais exigem --file.")
        return
    path = Path(args.file).expanduser().resolve()
    inferred = "image" if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff") else "video"
    c["asset_type"] = args.asset_type or inferred
    if (c["asset_type"] == "video") != (inferred == "video"):
        raise ValueError("asset-type não corresponde ao formato do arquivo.")
    c["media"]["kind"] = "video" if c["asset_type"] == "video" else "image"
    if args.title:
        c["title"] = args.title
    if args.captured_at:
        from datetime import datetime

        datetime.fromisoformat(args.captured_at)
        c["captured_at"] = args.captured_at


def _resolve_source_info(c, args):
    """Grava --source-url/--creator, exclusivos de --file."""
    if not (args.source_url or args.creator):
        return
    if not args.file:
        raise ValueError("--source-url/--creator são exclusivos de --file.")
    if args.source_url:
        from getbrolls.http import public_url

        url = public_url(args.source_url)
        if not url:
            raise ValueError("Fonte deve ser URL HTTPS pública sem credenciais.")
        c["source_url"] = url
    if args.creator:
        c["creator"]["name"] = args.creator


def _resolve_shot_suffix(c, args):
    """Sufixa o id do candidato com o beat, quando --shot foi informado."""
    if not args.shot:
        return
    if not re.fullmatch(SHOT_RE, args.shot):
        raise ValueError("--shot: use 1–80 letras, números, hífen ou underscore.")
    c["id"] += ":shot:" + args.shot
    c["shot"] = args.shot


def _execute_resolve(args, rules, ledger):
    """`resolve`: registra um candidato a partir de URL pública ou arquivo local já autorizado."""
    from getbrolls.rules import allowed, format_report

    for flag, value in (("--file", args.file), ("--url", args.url)):
        if value is not None and not value.strip():
            raise ValueError(f"{flag} não pode ser vazio: informe o caminho ou a URL real.")
    if args.shot:
        # Antes de ler o arquivo ou a URL: nada novo entra num beat aposentado.
        _refuse_retired_shot(args.project, args.shot)
    c = _resolve_build_candidate(args)
    # Mesmo registro que `search` faz: a intenção é da pessoa, e sem ela o
    # candidato de URL entrava sempre como "literal", inclusive quando não era.
    c["match"]["kind"] = getattr(args, "intent", None) or c["match"].get("kind") or "literal"
    _resolve_context_files(c, args)
    _resolve_local_metadata(c, args)
    _resolve_source_info(c, args)
    _resolve_shot_suffix(c, args)
    if c.get("asset_type") in ("news_screenshot", "web_screenshot") and not c.get("source_url"):
        raise ValueError("Screenshot exige --source-url para manter a origem.")
    if not allowed(c, rules):
        raise ValueError("Fonte ou tipo de asset bloqueado pelas regras do usuário.")
    c["format"] = format_report(c, rules)
    c = ledger.add(c)
    ledger.save("resolve", c)
    logs.event(
        _log,
        logging.INFO,
        "resolve",
        provider=c["provider"],
        candidate=c["id"],
        kind="file" if args.file else "url",
    )
    # Mesmos atalhos planos que a busca devolve (`channel`, `uploader`,
    # `duration_s`): quem lista o C2 lê os dois comandos do mesmo jeito. São só
    # da resposta — no manifesto continuam em `creator.name` e `media.duration_s`.
    return _search_row(c)
