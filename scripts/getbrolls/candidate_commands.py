"""Comandos que agem sobre um candidato: approve, reject, permit, preview e fetch, e o registro de cada um."""

import logging

from . import logs
from .config import CAP_EPSILON
from .fetch_commands import _apply_fetch, preview_files, reference_poster
from .inspect_commands import _validate_scan_flags, scan_candidate
from .models import approve, empty_output, now, pending_approval, set_segment, signature
from .rendering import render
from .runtime import record_warning
from .summaries import (
    _has_preview,
    _is_plugin_candidate,
    _plugin_nothing_seen,
    _refuse_blind_plugin_approval,
    _stage_status,
)

_log = logs.get("commands")


# Nomes de uma palavra que não identificam ninguém. "teste" fica de fora de propósito:
# as evals assinam como "Ana Teste", que é nome + sobrenome e passa na regra de duas
# palavras. A lista só morde quando a palavra vem sozinha.
GENERIC_NAMES = (
    "usuário",
    "usuario",
    "eu",
    "user",
    "cliente",
    "me",
    "admin",
    "você",
    "voce",
    "pessoa",
    "responsável",
    "responsavel",
)


def _check_declared_by(name):
    """Quem assina a declaração precisa ser identificável: nome e sobrenome.

    Duas recusas, com mensagens diferentes porque o conserto é diferente: uma
    palavra genérica ("eu", "cliente") pede o nome da pessoa; uma palavra só, ainda
    que seja um nome de verdade, pede o sobrenome. "teste" continua valendo — as
    evals assinam "Ana Teste", que já cumpre a regra das duas palavras.
    """
    if not name:
        raise ValueError("Informe em --declared-by o nome real de quem assume a responsabilidade.")
    parts = [part for part in name.split() if part.strip(".")]
    if len(parts) == 1 and parts[0].lower() in GENERIC_NAMES:
        raise ValueError(
            f"--declared-by recusa {parts[0]!r}: isso não identifica ninguém. "
            "Escreva o nome e o sobrenome de quem assume a responsabilidade."
        )
    if len(parts) < 2:  # noqa: PLR2004 - first name + surname, the minimum the message below asks for
        raise ValueError(
            "--declared-by precisa de pelo menos duas palavras (nome e sobrenome, ou "
            f"nome e inicial). {name!r} tem só uma: quem assina precisa dar para "
            "identificar depois."
        )


def mark_rejected(c, reason=None):
    """Descarta o candidato e guarda o porquê, quando a pessoa disse por quê.

    O motivo vive em `rejection`, não em `approval`: `approval` é o registro da
    decisão humana e seu formato é lido pela revisão. Sem motivo, nada é gravado —
    inventar um texto aqui seria pôr palavra na boca de quem descartou.
    """
    c["approval"]["status"] = "rejected"
    c["state"] = "rejected"
    # Só há revisão a invalidar quando o item já tinha uma; num candidato recém-buscado
    # não havia passo nenhum, e anunciar que ele foi desfeito assusta à toa.
    had_review = c.pop("review", None) is not None
    # Um candidato já coletado não pode continuar contando como entregue/verificado
    # depois de rejeitado: zera `output` no mesmo formato de `invalidate_approval`,
    # sem tocar no clipe em brolls/ nem em `segment.revision`.
    c["output"] = empty_output()
    c["rejection"] = {
        "reason": (reason or "").strip() or None,
        "at": now(),
        "invalidated_review": had_review,
    }
    return c


def reject_all(ledger, only, reason=None):
    """Rejeita vários itens de uma vez, como `approve` faz com os IDs mostrados.

    Valida antes de gravar: um ID desconhecido derruba a leva inteira, e só depois
    disso as mudanças vão em uma única transação. Não existe `--all` aqui: rejeitar
    em massa o que ninguém olhou apagaria candidato bom sem ninguém ver.
    """
    known = {c["id"]: c for c in ledger.data["items"]}
    missing = [i for i in only if i not in known]
    if missing:
        raise ValueError("Candidato não registrado no projeto: " + ", ".join(missing) + ".")
    # Ordem da pessoa, sem repetir o mesmo item duas vezes na transação.
    chosen, seen = [], set()
    for ident in only:
        if ident in seen:
            continue
        seen.add(ident)
        chosen.append(known[ident])
    for c in chosen:
        mark_rejected(c, reason)
    ledger.save_many("reject", chosen)
    render(ledger)
    try:
        for c in chosen:
            rejection = c.get("rejection") or {}
            logs.event(
                _log,
                logging.INFO,
                "reject",
                candidate=c["id"],
                had_review=bool(rejection.get("invalidated_review")),
                reason_present=bool(rejection.get("reason")),
                output_cleared=True,
            )
    except Exception:  # noqa: BLE001, S110 -- pylint: disable=broad-exception-caught
        # Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0); logging must
        # never break a command.
        pass
    return {
        "rejected": [c["id"] for c in chosen],
        "reason": (reason or "").strip() or None,
        "invalidated_review": any(c["rejection"]["invalidated_review"] for c in chosen),
    }


def approval_gate(c, rules, *, bulk, by_id):
    """Por que este candidato não pode ser aprovado agora, ou None se pode.

    Um portão só para `approve --candidate ID` e para o lote (`--all`, vários IDs): sem
    nada que a pessoa tenha visto, sem prévia, barrado pelas regras ou sem intervalo, a
    aprovação não vale em caminho nenhum. `bulk` diz se o item vem num lote; `by_id`, se
    a pessoa o nomeou por ID (`--candidate`, uma ou várias vezes). Três diferenças ficam
    de propósito:

    - no lote só conta a prévia gravada em disco; com um ID só, a fonte de plugin vale
      também pela miniatura ou pelo embed que mostrou (`docs/SDK.md`);
    - já aprovado sai do lote, para a leva não reassinar o que já vale;
    - rejeitado sai só do `--all`: o ID explícito, um ou vários, pode reaprovar um item
      rejeitado, e o lote sem IDs nunca aprova o que a pessoa descartou.
    """
    from .rules import allowed

    checks = [
        (_plugin_nothing_seen(c), "fonte de plugin sem nada para mostrar (sem prévia, poster_url nem embed_url)"),
        (
            not _has_preview(c) and (bulk or not _is_plugin_candidate(c)),
            "sem prévia gerada; rode preview antes",
        ),
        (not allowed(c, rules), "bloqueado pelas regras atuais do usuário"),
    ]
    if not by_id:
        checks.append((_stage_status(c, "approval") == "rejected", "rejeitado por decisão humana"))
    if bulk:
        checks.append(
            (
                _stage_status(c, "approval") == "approved" and c["approval"].get("signature") == signature(c),
                "já tem aprovação válida para este intervalo",
            )
        )
    checks.append(
        (
            c["segment"]["start_s"] is None and c.get("media", {}).get("kind") != "image",
            "sem intervalo escolhido; rode preview --start e --end",
        )
    )
    for skip, reason in checks:
        if skip:
            return reason
    return None


def _refuse_ungated_approval(c, rules):
    """`approve --candidate ID`: recusa pelo mesmo motivo que tiraria o item do lote."""
    _refuse_blind_plugin_approval(c)
    reason = approval_gate(c, rules, bulk=False, by_id=True)
    if reason is not None:
        raise ValueError(f"Não registrei a aprovação de {c['id']}: {reason}.")


def _log_approve_all(args, approved, skipped):
    """Loga cada aprovação do lote e o resumo; nunca deixa uma falha de log derrubar o comando."""
    try:
        for c in approved:
            logs.event(
                _log,
                logging.INFO,
                "approve",
                candidate=c["id"],
                channel=args.channel,
                revision=c["segment"]["revision"],
                by_present=bool((args.by or "").strip()),
                statement_present=bool((args.statement or "").strip()),
            )
        logs.event(_log, logging.INFO, "approve_all", approved=len(approved), skipped=len(skipped))
    except Exception:  # noqa: BLE001, S110 -- pylint: disable=broad-exception-caught
        # logging must never break a command
        pass


def approve_all(ledger, args, rules, only=None):
    """Aplica a mesma decisão humana a vários itens de uma vez.

    `only` é a lista de IDs que o agente disse ter mostrado à pessoa: aprova
    exatamente esses, inclusive um que tinha sido rejeitado — e um id desconhecido é
    erro, nunca silêncio. Sem `only` (`--all`), o alvo é todo item com prévia e sem
    aprovação válida, nunca um rejeitado.
    """
    approved, skipped = [], []
    wanted = list(only) if only else None
    if wanted is not None:
        known = {c["id"] for c in ledger.data["items"]}
        missing = [i for i in wanted if i not in known]
        if missing:
            raise ValueError("Candidato não registrado no projeto: " + ", ".join(missing) + ".")
    for c in ledger.data["items"]:
        if wanted is not None and c["id"] not in wanted:
            continue
        reason = approval_gate(c, rules, bulk=True, by_id=wanted is not None)
        if reason is None:
            approve(c, args.by, args.channel, args.statement)
            approved.append(c)
            continue
        skipped.append({"id": c["id"], "reason": reason})
    if approved:
        ledger.save_many("approve-chat" if args.channel == "chat" else "approve", approved)
        render(ledger)
    _log_approve_all(args, approved, skipped)
    if wanted is None and approved:
        # `--all` mira o disco, não a conversa: a prévia de um candidato descartado
        # continua lá e entra na leva. Dizer em voz alta o que foi aprovado é o que
        # permite desfazer na hora, enquanto a pessoa ainda está na frente.
        record_warning(
            "APPROVE_ALL_WIDE",
            f"`--all` aprovou {len(approved)} item(ns) em nome de {args.by}: "
            + ", ".join(c["id"] for c in approved)
            + ". Ele pega todo candidato com prévia em disco, inclusive o que você "
            "descartou sem rejeitar. Se a pessoa não viu exatamente esses, rejeite o "
            "que sobrou com `reject` — e da próxima vez aprove pelos IDs que você "
            "mostrou: `approve --candidate ID1 --candidate ID2`.",
        )
    return {
        "approved": [c["id"] for c in approved],
        # A trilha de auditoria precisa dizer *o quê* foi aprovado, não só quantos:
        # a folha de contato é a imagem que a pessoa viu e o intervalo é o que ela
        # aceitou. Sem isso, `--all` vira um número sem como conferir depois.
        "approved_items": [_approved_row(c) for c in approved],
        "skipped": skipped,
        "by": args.by,
        "channel": args.channel,
        "statement": args.statement,
    }


def _approved_row(c):
    """O registro mínimo para conferir uma aprovação: id, o que foi visto, e o intervalo."""
    return {
        "id": c["id"],
        "title": c.get("title"),
        "contact_sheet": (c.get("preview") or {}).get("contact_sheet_path"),
        "segment": {
            "start_s": c["segment"]["start_s"],
            "end_s": c["segment"]["end_s"],
            "revision": c["segment"]["revision"],
        },
    }


def _bulk_approve_result(args, ledger, rules):
    """Resolve `approve --all`/vários `--candidate`, ou normaliza `args.candidate` para um só."""
    chosen = list(args.candidate or [])
    if args.channel == "chat" and not (args.statement or "").strip():
        raise ValueError("Aprovação pelo chat exige --statement com a frase exata dita pela pessoa.")
    if args.all and chosen:
        raise ValueError("Use --all sozinho ou --candidate ID (repetindo a flag), nunca os dois juntos.")
    if args.all and (args.start is not None or args.end is not None):
        raise ValueError("--all aprova os intervalos já escolhidos; não use --start/--end.")
    if not args.all and not chosen:
        raise ValueError(
            "Informe --candidate ID (repita a flag para vários), ou use --all para todos os candidatos com prévia."
        )
    if len(chosen) > 1 and (args.start is not None or args.end is not None):
        raise ValueError("--start/--end valem para um candidato só; aprove um por vez para mudar o intervalo.")
    if args.all:
        return approve_all(ledger, args, rules)
    if len(chosen) > 1:
        return approve_all(ledger, args, rules, only=chosen)
    args.candidate = chosen[0]
    return None


def _bulk_reject_result(args, ledger):
    """Resolve vários `--candidate` de `reject` de uma vez, ou normaliza para um candidato só."""
    # `--candidate` é repetível: `argparse` entrega lista mesmo com um ID só.
    chosen = list(args.candidate or [])
    if len(chosen) > 1:
        return reject_all(ledger, chosen, getattr(args, "reason", None))
    args.candidate = chosen[0]
    return None


def _bulk_candidate_result(cmd, args, ledger, rules):
    """Resolve `approve --all`/vários `--candidate` de uma vez, ou normaliza para um candidato só."""
    if cmd == "approve":
        return _bulk_approve_result(args, ledger, rules)
    if cmd == "reject":
        return _bulk_reject_result(args, ledger)
    return None


def _validate_candidate_guard(c, rules, cmd):
    """Bloqueia preview/approve/permit/fetch num asset que as regras atuais não permitem."""
    from getbrolls.rules import allowed

    if not allowed(c, rules) and cmd in ("preview", "approve", "permit", "fetch"):
        raise ValueError("Asset bloqueado pelas regras atuais do usuário.")


def _apply_start_end(cmd, c, args):
    """Confere/grava o intervalo de origem para preview/approve, ou recusa quando não cabe."""
    if cmd not in ("preview", "approve"):
        return
    # `--reference-only` não pede mídia nenhuma: é o cartaz estático de um vídeo que
    # a fonte não deixa baixar. Exigir intervalo aqui obrigaria a inventar um.
    reference_without_range = cmd == "preview" and args.reference_only and args.start is None and args.end is None
    # `approve --candidate ID` sozinho confirma o intervalo que a pessoa acabou de
    # ver na prévia: exigir `--start/--end` de novo obrigaria a redigitar o que já
    # está gravado, e digitar errado apagaria a prévia que ela aprovou.
    approving_current = cmd == "approve" and args.start is None and args.end is None
    if approving_current:
        pass
    elif c.get("media", {}).get("kind") != "image" and not reference_without_range:
        if args.start is None or args.end is None:
            raise ValueError(
                "Vídeo exige --start e --end. Se a fonte não libera o trecho, use "
                "`--reference-only` sozinho e eu gero só o cartaz estático."
            )
        set_segment(c, args.start, args.end)
    elif args.start is not None or args.end is not None:
        raise ValueError("Imagem estática não precisa de intervalo de origem.")


def _permit_via_preset(c, args, preset):
    """Rota `--preset`: condição genérica da fonte, com verificação opcional anexada."""
    # O preset nunca vira licença: ele diz o que a fonte costuma exigir e manda
    # conferir a página do item. Quem assina continua responsável, e `fetch`
    # continua exigindo a aprovação humana.
    from getbrolls import presets

    evidence = presets.get(preset)["text"]
    if args.evidence is not None:
        if not args.evidence.strip():
            raise ValueError("Evidência não pode ser vazia.")
        evidence += " | Verificado por quem pediu: " + args.evidence.strip()
    c["rights"]["basis"] = "per_item_evidence"
    return evidence, "preset", preset


def _permit_via_declared_by(c, args):
    """Rota `--declared-by`/`--declaration-text`: declaração literal dita no chat."""
    name = (args.declared_by or "").strip()
    text = (args.declaration_text or "").strip()
    _check_declared_by(name)
    if len(text) < 20:  # noqa: PLR2004 - matches the "20 caracteres ou mais" message below
        raise ValueError("--declaration-text precisa da frase literal da pessoa, com 20 caracteres ou mais.")
    evidence = "Declaração do usuário " + name + ": " + text
    c["rights"]["basis"] = "user_declaration"
    c["rights"]["responsible_person"] = name
    c["rights"]["declaration_channel"] = "chat"
    return evidence, "declaration", None


def _permit_via_rules_declaration(c, rules):
    """Rota `--declaration`: usa a declaração já configurada em RULES.md."""
    rights = rules["copyright"]
    if rights["mode"] != "user_declaration":
        raise ValueError("Usuário deve configurar sua declaração em RULES.md primeiro.")
    evidence = "Declaração do usuário " + rights["responsible_person"] + ": " + rights["declaration"]
    c["rights"]["basis"] = "user_declaration"
    c["rights"]["responsible_person"] = rights["responsible_person"]
    return evidence, "declaration", None


def _permit_via_evidence(c, args):
    """Rota padrão: `--evidence` com o texto literal das condições de uso."""
    if args.evidence is None:
        raise ValueError(
            'Diga as condições de uso: --evidence TEXTO, ou --declared-by NOME --declaration-text "frase da pessoa".'
        )
    if not args.evidence.strip():
        raise ValueError("Evidência não pode ser vazia.")
    c["rights"]["basis"] = "per_item_evidence"
    return args.evidence, "evidence", None


def _apply_permit(c, args, rules):
    """Aplica `permit`: registra a evidência/preset/declaração e devolve (rota, nome do preset)."""
    preset = getattr(args, "preset", None)
    if preset and (args.declaration or args.declared_by or args.declaration_text):
        raise ValueError(
            "--preset registra as condições genéricas da fonte; não combine com "
            "declaração de responsabilidade. Escolha um dos dois."
        )
    if preset:
        evidence, permit_route, permit_preset_name = _permit_via_preset(c, args, preset)
    elif args.declared_by or args.declaration_text:
        evidence, permit_route, permit_preset_name = _permit_via_declared_by(c, args)
    elif args.declaration:
        evidence, permit_route, permit_preset_name = _permit_via_rules_declaration(c, rules)
    else:
        evidence, permit_route, permit_preset_name = _permit_via_evidence(c, args)
    c["rights"]["status"] = "permitted"
    c["rights"]["evidence"].append(evidence)
    return permit_route, permit_preset_name


def _apply_preview(c, args, ledger, config):
    """Aplica `preview`: gera a mídia (ou referência), atualiza estado e devolve (modo, invalidou_aprovação)."""
    context_before = signature(c)
    if not args.reference_only and c["provider"] != "local" and c.get("media", {}).get("kind") == "image":
        # Foto remota (NASA, Commons): sem intervalo nem teto de segundos. A prévia
        # é o cartaz da própria foto, baixada uma vez para o cache privado.
        from .acquisition import prepare_image_source

        prepare_image_source(ledger, c)
    elif not args.reference_only and c["provider"] != "local":
        # Vídeo sem --start/--end já parou antes, no guard de `preview`/`approve`.
        asked = args.end - args.start  # pyright: ignore[reportOptionalOperand]
        if asked > float(config["max_seconds"]) + CAP_EPSILON:
            raise ValueError(
                f"Trecho de {asked:g} s excede o teto de prévia: GB_PREVIEW_MAX_SECONDS "
                f"está em {config['max_seconds']} s. Encurte o intervalo, ou aumente a "
                "variável se você realmente precisa de uma prévia mais longa."
            )
        from .acquisition import prepare_source

        prepare_source(ledger, c, args.start, args.end)
    if c.get("local_path") and not args.reference_only:
        from .previewing import prepare_preview

        prepare_preview(ledger, c, args.start, args.end, config)
        if c["approval"]["status"] == "approved" and c["approval"].get("signature") == signature(c):
            c["state"] = "verified" if c["output"].get("verified") else "approved"
        else:
            c["state"] = "rejected" if c["approval"]["status"] == "rejected" else "awaiting_approval"
        preview_mode = "image" if c.get("media", {}).get("kind") == "image" else "cut"
    else:
        c["preview"]["warning"] = "Somente referência estática; o trecho animado requer original local autorizado."
        c["state"] = "reference_only"
        # Sem um arquivo de imagem ninguém decide nada, e `status` nem conta o item
        # como tendo prévia. A miniatura pública da fonte já basta para isso.
        reference_poster(ledger, c)
        preview_mode = "reference_only"
    if c["preview"].get("warning"):
        record_warning("PREVIEW_LIMITATION", c["preview"]["warning"])
    if args.narration is not None:
        c["narration"] = args.narration
    if args.reason is not None:
        c["match"]["reason"] = args.reason
    approval_invalidated = False
    if context_before != signature(c):
        c["approval"] = pending_approval()
        c.pop("review", None)
        c["state"] = "awaiting_approval" if c.get("local_path") else "reference_only"
        approval_invalidated = True
    return preview_mode, approval_invalidated


def _log_approve(args, c):
    """Loga a decisão de `approve` para um candidato."""
    logs.event(
        _log,
        logging.INFO,
        "approve",
        candidate=c["id"],
        channel=args.channel,
        revision=c["segment"]["revision"],
        by_present=bool((args.by or "").strip()),
        statement_present=bool((args.statement or "").strip()),
    )


def _log_permit(c, permit_route, permit_preset_name):
    """Loga a rota de `permit` (preset/declaração/evidência) usada para o candidato."""
    logs.event(_log, logging.INFO, "permit", candidate=c["id"], route=permit_route, preset=permit_preset_name)


def _log_reject(c):
    """Loga a rejeição de um candidato, com o motivo (se houver) e o que foi invalidado."""
    rejection = c.get("rejection") or {}
    logs.event(
        _log,
        logging.INFO,
        "reject",
        candidate=c["id"],
        had_review=bool(rejection.get("invalidated_review")),
        reason_present=bool(rejection.get("reason")),
        output_cleared=True,
    )


def _log_preview(c, approval_invalidated, preview_mode):
    """Loga a geração de prévia e, se a aprovação anterior caiu, o aviso correspondente."""
    if approval_invalidated:
        logs.event(
            _log,
            logging.INFO,
            "approval_invalidated",
            candidate=c["id"],
            reason="segment_changed",
            revision=c["segment"]["revision"],
        )
    logs.event(
        _log,
        logging.INFO,
        "preview",
        candidate=c["id"],
        start_s=c["segment"]["start_s"],
        end_s=c["segment"]["end_s"],
        revision=c["segment"]["revision"],
        mode=preview_mode,
    )


def _log_candidate_command(cmd, args, c, extra):
    """Loga approve/permit/reject/preview; nunca deixa uma falha de log derrubar o comando.

    `extra` = {permit_route, permit_preset_name, approval_invalidated, preview_mode}.
    """
    try:
        if cmd == "approve":
            _log_approve(args, c)
        elif cmd == "permit":
            _log_permit(c, extra["permit_route"], extra["permit_preset_name"])
        elif cmd == "reject":
            _log_reject(c)
        elif cmd == "preview":
            _log_preview(c, extra["approval_invalidated"], extra["preview_mode"])
    except Exception:  # noqa: BLE001, S110 -- pylint: disable=broad-exception-caught
        # logging must never break a command
        pass


def _execute_candidate_command(cmd, args, config, rules, ledger):
    """Pipeline compartilhado de preview/approve/permit/reject/fetch/remember para um candidato só."""
    _validate_scan_flags(cmd, args)
    bulk = _bulk_candidate_result(cmd, args, ledger, rules)
    if bulk is not None:
        return bulk
    c = ledger.get(args.candidate)
    _validate_candidate_guard(c, rules, cmd)
    if cmd == "remember":
        from getbrolls.memory import remember

        return remember(ledger, c, args.decision, args.reason, args.by)
    if cmd == "preview" and args.scan:
        return scan_candidate(ledger, c, config)
    _apply_start_end(cmd, c, args)
    # Defaults for the audit-trail fields the elif branches below fill in;
    # only used after the shared save at the end of this function, for logging.
    permit_route = None
    permit_preset_name = None
    preview_mode = None
    approval_invalidated = False
    if cmd == "approve":
        # Depois de `_apply_start_end`: um intervalo novo no `approve` derruba a
        # prévia, e o trecho que ninguém viu não pode ser aprovado.
        _refuse_ungated_approval(c, rules)
        approve(c, args.by, args.channel, args.statement)
    elif cmd == "permit":
        permit_route, permit_preset_name = _apply_permit(c, args, rules)
    elif cmd == "reject":
        mark_rejected(c, getattr(args, "reason", None))
    elif cmd == "preview":
        preview_mode, approval_invalidated = _apply_preview(c, args, ledger, config)
    elif cmd == "fetch":
        return _apply_fetch(args, ledger, c)
    # O journal distingue a decisão dita no chat da que veio assinada pelo Storyboard.
    ledger.save("approve-chat" if cmd == "approve" and args.channel == "chat" else cmd, c)
    render(ledger)
    _log_candidate_command(
        cmd,
        args,
        c,
        {
            "permit_route": permit_route,
            "permit_preset_name": permit_preset_name,
            "approval_invalidated": approval_invalidated,
            "preview_mode": preview_mode,
        },
    )
    if cmd == "preview":
        # Absolute paths for the agent to open the exact files the Storyboard shows.
        # They live only in this response, never in the manifest.
        return {**c, "files": preview_files(ledger, c)}
    return c
