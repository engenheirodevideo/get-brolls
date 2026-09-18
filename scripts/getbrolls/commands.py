"""Existing workflow command handlers; CLI parsing and reporting live separately."""

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .guidance import next_action
from .ledger import Ledger, digest
from .media import cut, probe, run
from .models import approve, candidate, require_fetch, set_segment, signature
from .presets import PERMIT_PRESETS
from .queue import execute as queue_execute
from .queue import hint as queue_hint
from .queue import summary_line as queue_summary_line
from .rendering import render
from .runtime import record_warning

# Raiz real da skill/plugin: o comando sugerido não pode depender da pasta atual.
SKILL_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = f'bash "{SKILL_ROOT / "scripts" / "install.sh"}" (ou "{SKILL_ROOT / "scripts" / "install.ps1"}" no Windows)'
SYSTEM_TOOLS = "instale pelo gerenciador do sistema; veja docs/GUIDE.md#instalação"

# Executável obrigatório → (comando que resolve, impacto real da ausência).
REQUIRED_EXECUTABLES = {
    "ffmpeg": (SYSTEM_TOOLS, "Prévia, corte e verificação ficam indisponíveis"),
    "ffprobe": (SYSTEM_TOOLS, "Prévia, corte e verificação ficam indisponíveis"),
    "curl": (SYSTEM_TOOLS, "Download dos pares CDN do Instagram fica indisponível"),
    "node": (SYSTEM_TOOLS, "Playwright CLI e runtime EJS do yt-dlp ficam indisponíveis"),
    "npx": (SYSTEM_TOOLS, "Instalação e execução do Playwright CLI ficam indisponíveis"),
    "yt-dlp": (INSTALLER, "YouTube e TikTok ficam indisponíveis sem ele"),
    "playwright-cli": (INSTALLER, "Instagram indisponível sem ele"),
}

# Ausência esperada em parte dos ambientes: não bloqueia o fluxo principal.
OPTIONAL_EXECUTABLES = {
    "deno": "Alternativa ao Node apenas para o runtime EJS",
    "bash": "Somente os helpers opcionais de YouTube; a CLI não depende dele",
}

OPTIONAL_KEYS = {
    "PEXELS_API_KEY": "Busca no Pexels desativada; defina a chave no ambiente ou no .env",
    "PIXABAY_API_KEY": "Busca no Pixabay desativada; defina a chave no ambiente ou no .env",
}


# Todo executável que o doctor sonda: obrigatórios mais opcionais, sem duplicar.
PROBED_EXECUTABLES = tuple(sorted(set(REQUIRED_EXECUTABLES) | set(OPTIONAL_EXECUTABLES)))


def doctor_overrides():
    """Pins válidos e pins inválidos: um pin quebrado não derruba o diagnóstico."""
    from getbrolls.config import PATH_KEYS, pin_override

    active = {}
    problems = []
    for key in PATH_KEYS:
        try:
            value = pin_override(key)
        except ValueError as exc:
            problems.append({"item": key, "fix": INSTALLER, "note": str(exc)})
            continue
        if value:
            active[key] = str(value)
    return active, problems


def doctor_resolved(overrides):
    """Executável absoluto realmente usado por ferramenta, ou None quando ausente."""
    from getbrolls.config import TOOL_PATH_KEYS

    resolved = {}
    for name in PROBED_EXECUTABLES:
        found = overrides.get(TOOL_PATH_KEYS.get(name) or "") or shutil.which(name)
        if not found and name == "yt-dlp":
            from .social import local_ytdlp

            try:
                found = local_ytdlp()
            except ValueError:
                found = None
        if not found and name == "playwright-cli":
            found = _local_playwright()
        resolved[name] = str(Path(found).resolve()) if found else None
    return resolved


def doctor_summary(executables, pins=()):
    """Veredito humano do doctor: o que funciona, o que falta e o que é opcional."""
    ok = sorted(name for name, present in executables.items() if present)
    missing = [
        {"item": name, "fix": REQUIRED_EXECUTABLES[name][0], "note": REQUIRED_EXECUTABLES[name][1]}
        for name in sorted(REQUIRED_EXECUTABLES)
        if not executables.get(name)
    ]
    missing += list(pins)
    optional = [
        {"item": name, "note": note} for name, note in sorted(OPTIONAL_EXECUTABLES.items()) if not executables.get(name)
    ]
    optional += [{"item": key, "note": note} for key, note in sorted(OPTIONAL_KEYS.items()) if not os.environ.get(key)]
    return {"ok": ok, "missing": missing, "optional": optional}


def doctor_contact_sheet(ffmpeg_present):
    """Whether the contact sheet can be numbered by ffmpeg (drawtext + TrueType font)."""
    from .media import drawtext_available, find_font

    status = {"drawtext": False, "font": None, "labels": False}
    optional = []
    if not ffmpeg_present:
        return {"status": status, "optional": optional}
    status["drawtext"] = drawtext_available()
    try:
        status["font"] = find_font()
    except ValueError as exc:
        optional.append({"item": "GB_FONT_FILE", "note": str(exc)})
    status["labels"] = bool(status["drawtext"] and status["font"])
    if not status["drawtext"]:
        optional.append(
            {
                "item": "drawtext",
                "note": (
                    "FFmpeg sem o filtro drawtext (libfreetype): o contact sheet sai sem número "
                    "e timecode nas células; o Storyboard imprime a legenda de tempos. "
                    "Reinstale o FFmpeg com freetype (Homebrew: brew reinstall ffmpeg; "
                    "Windows: build gyan.dev/BtbN; Ubuntu: apt install ffmpeg)."
                ),
            }
        )
    elif not status["font"]:
        optional.append(
            {
                "item": "font",
                "note": (
                    "Nenhuma fonte TrueType encontrada para rotular o contact sheet; instale "
                    "DejaVu/Liberation/Arial ou defina GB_FONT_FILE."
                ),
            }
        )
    return {"status": status, "optional": optional}


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

# `--shot` de `search` e de `resolve` valem a mesma coisa: o beat vira sufixo do id.
SHOT_RE = r"[A-Za-z0-9_-]{1,80}"

# Storyboard publicado sem nenhuma prévia: a página sobe, mas não há o que decidir.
EMPTY_STORYBOARD = (
    "Storyboard vazio: nenhuma prévia. A página sobe, mas não há nada para decidir — "
    "gere as prévias com `preview` antes de mandar o endereço para alguém."
)

# Nomes que não identificam ninguém: uma declaração precisa de uma pessoa real.
# Comandos que só consultam o projeto: `inspect` grava no máximo `media.duration_s`
# e `references` não grava nada — nenhum dos dois muda formato nem aprovação.
READ_ONLY_CONSULTS = ("references", "inspect")
GENERIC_NAMES = ("usuário", "usuario", "eu", "user", "cliente")


def _count(value, singular, plural):
    return f"{value} {singular if value == 1 else plural}"


def _identifier(result):
    return result.get("id") or "candidato"


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
        f"estado {r.get('state')}, aprovação {(r.get('approval') or {}).get('status')}."
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
    "reject": lambda r: f"Rejeitei {_identifier(r)}: estado {r.get('state')}, revisão invalidada.",
    "review": lambda r: f"Gerei o Storyboard em {r.get('review')}.",
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
    return {**result, "summary": formatter(result)}


# Escada do fluxo: a primeira condição verdadeira nomeia o próximo passo real.
STATUS_LADDER = (
    (
        lambda c: not c["candidates"],
        "Nenhum candidato ainda: registre fontes com search ou resolve.",
    ),
    (
        lambda c: c["pending_preview"] > 0,
        "Gere prévias com preview para os candidatos ainda sem quadro.",
    ),
    (
        lambda c: not c["approved"],
        "Peça a decisão humana: pelo Storyboard (review + import-review) ou pela fala "
        "no chat (approve --candidate ID --by NOME --channel chat --statement "
        '"frase"), com os IDs que você mostrou.',
    ),
    (
        lambda c: c["permitted"] < c["approved"],
        "Registre as condições reais de uso com permit nos itens aprovados.",
    ),
    (
        lambda c: c["delivered"] < c["permitted"],
        "Colete os cortes aprovados e permitidos com fetch.",
    ),
    (
        lambda c: c["verified"] < c["delivered"],
        "Confira os arquivos coletados com verify.",
    ),
    (
        lambda c: c["undelivered"] > 0,
        "Organize os trechos conferidos em entrega/ com deliver.",
    ),
)


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
        return (
            "As regras editoriais mudaram: o próximo comando invalidará "
            + _count(format_pending, "aprovação", "aprovações")
            + "; gere prévia e revisão novamente antes de coletar."
        )
    from .guidance import flow_complete, leftover_aside, leftovers

    state = {"undelivered": counts["undelivered"]}
    counts = {"pending": 0, "rejected": 0, **counts}
    complete = "Fluxo completo: os itens aprovados estão coletados, verificados e organizados em entrega/."
    # Mesma preferência de `guidance.next_action`: o estado humano ganha do rascunho
    # do agente. Com a entrega pronta, sobra de candidato é aparte, não próximo passo.
    if flow_complete(state, counts):
        return complete + leftover_aside(leftovers(state, counts))
    if counts.get("pending"):
        return (
            "Peça a decisão humana: há prévia esperando alguém decidir, pelo Storyboard "
            "(review + import-review) ou pela fala no chat (approve --candidate ID --by "
            'NOME --channel chat --statement "frase").'
        )
    for matches, step in STATUS_LADDER:
        if matches(counts):
            return step
    return complete


def _has_preview(c):
    return any((c.get("preview") or {}).get(key) for key in PREVIEW_ARTIFACTS)


def rules_from_flags(template, mode, responsible, declaration, video_format=None):
    """Reescreve só o bloco ```json do modelo, preservando toda a prosa do arquivo."""
    blocks = re.findall(r"```json\s*\n(.*?)\n```", template, re.S)
    if len(blocks) != 1:
        raise ValueError("Modelo de RULES.md precisa de exatamente um bloco JSON.")
    data = json.loads(blocks[0])
    if video_format is not None:
        # Único campo que `--format` toca: o resto das regras continua do jeito que a
        # pessoa deixou. Era esta a lacuna que fazia o conflito brief×rules travar.
        data["video_format"] = video_format
    rights = data["copyright"]
    rights["mode"] = mode or ("user_declaration" if (responsible or declaration) else rights["mode"])
    if responsible is not None:
        rights["responsible_person"] = responsible.strip() or None
    if declaration is not None:
        rights["declaration"] = declaration.strip() or None
    if rights["mode"] == "user_declaration" and not (
        (rights["responsible_person"] or "").strip() and (rights["declaration"] or "").strip()
    ):
        raise ValueError("Modo user_declaration exige --responsible NOME e --declaration TEXTO.")
    return (
        template.replace(blocks[0], json.dumps(data, ensure_ascii=False, indent=2), 1),
        rights,
    )


def brief_report(args):
    """Beats do vídeo com defaults aplicados, comando pronto e o que já foi registrado.

    Somente leitura, como `status`: não cria a árvore do projeto nem grava no ledger.
    """
    from getbrolls.brief import beat_commands, beat_progress, brief_path, load_brief, validate_brief
    from getbrolls.rules import load_rules

    rules, rules_error = None, None
    try:
        rules = load_rules(args.project)
    except (ValueError, OSError) as exc:
        rules_error = str(exc)
    data, conflicts = validate_brief(load_brief(args.project), rules)
    problems = list(conflicts)
    if rules_error:
        problems.append(f"RULES.md não pôde ser lido, então não conferi o formato: {rules_error}")
    path = str(brief_path(args.project))
    if getattr(args, "validate", False):
        beat_count = _count(len(data["beats"]), "beat", "beats")
        return {
            "summary": {
                # "válido" só quando não sobrou nada para a pessoa resolver: um conflito
                # de formato ou um RULES.md ilegível não é um brief pronto para buscar.
                "line": (
                    f'Brief de "{data["video"]["title"]}" lido, com {beat_count}, mas '
                    + _count(len(problems), "ponto", "pontos")
                    + " para resolver antes de buscar."
                    if problems
                    else f'Brief de "{data["video"]["title"]}" válido: {beat_count}.'
                ),
                "problems": problems,
                "next": (
                    "Resolva os pontos acima e repita `brief --validate --project ...`."
                    if problems
                    else "Pode buscar: `brief --project ...` mostra o comando pronto de cada beat."
                ),
            },
            "brief": path,
            "valid": True,
            "beats": len(data["beats"]),
            "conflicts": conflicts,
        }
    beats = data["beats"]
    if getattr(args, "beat", None):
        chosen = [b for b in beats if b["id"] == args.beat]
        if not chosen:
            raise ValueError(
                f'O BRIEF.md não tem o beat "{args.beat}". Os ids disponíveis são: '
                + ", ".join(b["id"] for b in beats)
                + "."
            )
        beats = chosen
    root = Path(args.project).expanduser().resolve() / "brolls"
    items = Ledger(args.project, recover=False).data["items"] if root.is_dir() else []
    progress = beat_progress(beats, items)
    listed = [
        {
            "id": b["id"],
            "resolved": b["resolved"],
            "commands": beat_commands(args.project, b["resolved"]),
            "candidates": progress[b["id"]],
        }
        for b in beats
    ]
    missing = [entry for entry in listed if not entry["candidates"]]
    covered = len(listed) - len(missing)
    problems += [f'O beat "{entry["id"]}" ainda não tem candidato registrado.' for entry in missing]
    return {
        "summary": {
            "line": f'Brief de "{data["video"]["title"]}": '
            + _count(len(listed), "beat", "beats")
            + f", {covered} com candidato e {len(missing)} sem.",
            "problems": problems,
            # Mesma escada de `status`: a pessoa ouve a mesma frase nos dois comandos.
            "next": next_action(
                {
                    "project": args.project,
                    "counts": {
                        "candidates": len(items),
                        "previews": sum(1 for c in items if _has_preview(c)),
                        "approved": sum(1 for c in items if STAGE_TESTS["approved"](c)),
                        "permitted": sum(1 for c in items if STAGE_TESTS["permitted"](c)),
                        "delivered": sum(1 for c in items if STAGE_TESTS["delivered"](c)),
                        "verified": sum(1 for c in items if STAGE_TESTS["verified"](c)),
                    },
                    "brief": {
                        "beats": len(listed),
                        "covered": covered,
                        "missing": [
                            {
                                "id": entry["id"],
                                "search": entry["commands"].get("search"),
                            }
                            for entry in missing
                        ],
                        "conflicts": conflicts,
                    },
                    "review_page": (root / "review.html").is_file(),
                    "rights_mode": _rights_mode(rules),
                    "candidate": _pending_candidate(items),
                    "duration_unknown": len(_uninspected(items)),
                    "inspect_candidate": next((c["id"] for c in _uninspected(items)), None),
                }
            )["for_human"],
        },
        "brief": path,
        "video": data["video"],
        "rights": data["rights"],
        "defaults": data["defaults"],
        "beats": listed,
        "coverage": {"beats": len(listed), "covered": covered, "missing": len(missing)},
        "conflicts": conflicts,
    }


def library_command(args):
    """`learn` e `library`: memória editorial entre projetos, sem direito junto."""
    from getbrolls import library

    if args.command == "library":
        if not 1 <= args.limit <= 20:
            raise ValueError("Use --limit entre 1 e 20.")
        return library.search(args.search, limit=args.limit)
    given = [
        flag
        for flag, value in (
            ("--query", args.query),
            ("--preference", args.preference),
            ("--from-candidate", args.from_candidate),
        )
        if value
    ]
    if len(given) != 1:
        raise ValueError(
            "Diga o que aprender, uma coisa por vez: --query (com --provider e "
            '--outcome), --preference "frase" ou --from-candidate ID.'
        )
    if args.query:
        return library.learn_query(args.query, args.provider, args.outcome, note=args.note)
    if args.preference:
        return library.learn_preference(args.preference, by=args.by)
    return library.learn_from_candidate(args.project, args.from_candidate, shot=args.shot)


def approve_all(ledger, args, rules, only=None):
    """Aplica a mesma decisão humana a vários itens de uma vez.

    `only` é a lista de IDs que o agente disse ter mostrado à pessoa: aprova
    exatamente esses. Sem `only` (`--all`), o alvo é todo item com prévia e sem
    aprovação válida — e um id desconhecido é erro, nunca silêncio.
    """
    from .rules import allowed

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
        if not _has_preview(c):
            reason = "sem prévia gerada; rode preview antes"
        elif not allowed(c, rules):
            reason = "bloqueado pelas regras atuais do usuário"
        elif _stage_status(c, "approval") == "rejected":
            reason = "rejeitado por decisão humana"
        elif _stage_status(c, "approval") == "approved" and c["approval"].get("signature") == signature(c):
            reason = "já tem aprovação válida para este intervalo"
        elif c["segment"]["start_s"] is None and c.get("media", {}).get("kind") != "image":
            reason = "sem intervalo escolhido; rode preview --start/--end"
        else:
            approve(c, args.by, args.channel, args.statement)
            approved.append(c)
            continue
        skipped.append({"id": c["id"], "reason": reason})
    if approved:
        ledger.save_many("approve-chat" if args.channel == "chat" else "approve", approved)
        render(ledger)
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
        "skipped": skipped,
        "by": args.by,
        "channel": args.channel,
        "statement": args.statement,
    }


def _stage_status(c, field):
    return (c.get(field) or {}).get("status")


# Um predicado por etapa: a mesma leitura serve para contagem, lista e item.
STAGE_TESTS = {
    "candidates": lambda c: True,
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


def status_journal(root):
    """Leitura do journal append-only: quantos eventos e qual foi o último."""
    path = root / "events.jsonl"
    if not path.is_file():
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
    if not path.is_file():
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
    """True quando as regras atuais mudariam o formato-alvo já gravado no item."""
    if rules is None:
        return None
    from getbrolls.rules import format_report

    return c.get("format", {}).get("target", "native") != format_report(c, rules)["target"]


def brief_state(project, rules, items):
    """Cobertura dos beats para a escada de orientação, sem gravar nada no projeto.

    Devolve `None` quando não há BRIEF.md legível: esse é o degrau do topo da escada.
    """
    from getbrolls.brief import (
        beat_commands,
        beat_progress,
        brief_path,
        load_brief,
        validate_brief,
    )

    try:
        data, conflicts = validate_brief(load_brief(project), rules)
    except (ValueError, OSError) as exc:
        # Nada do brief quebra o `status`. Arquivo ausente é "faça o brief"; arquivo
        # presente e errado é "corrija o brief" — degraus e comandos diferentes.
        try:
            exists = brief_path(project).exists()
        except (ValueError, OSError):
            exists = False
        return {"error": str(exc)} if exists else None
    beats = data["beats"]
    progress = beat_progress(beats, items)
    missing = [
        {
            "id": b["id"],
            "search": beat_commands(project, b["resolved"]).get("search"),
            # A frase para a pessoa muda quando o beat não tem alvo literal: prometer
            # busca ali contradiz a guarda "literal primeiro, nada de preenchimento".
            "intent": b["resolved"].get("intent"),
            "target": b["resolved"].get("target"),
        }
        for b in beats
        if not progress[b["id"]]
    ]
    return {
        "beats": len(beats),
        "covered": len(beats) - len(missing),
        "missing": missing,
        "conflicts": conflicts,
    }


def _rights_mode(rules):
    return ((rules or {}).get("copyright") or {}).get("mode", "per_item_evidence")


# Degrau → (item já está nesta etapa?, item precisa estar nesta anterior?).
_PENDING_STAGES = (
    (lambda c: not STAGE_TESTS["previews"](c), lambda c: True),
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


def _uninspected(items):
    """Candidatos sem duração conhecida e ainda sem prévia, com URL pública para analisar.

    É o que separa o degrau `inspect` do degrau `preview`: sem duração, qualquer
    `--start/--end` é palpite, e o palpite custa um pedido à fonte.
    """
    return [
        c for c in items if not (c.get("media") or {}).get("duration_s") and c.get("source_url") and not _has_preview(c)
    ]


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
        for_human=lambda: _flow_next(ledger, rules),
    )
    verb = "Organizaria" if dry_run else "Organizei"
    beats = len({item["beat"] for item in report["items"]})
    line = (
        f"{verb} {_count(len(report['items']), 'trecho', 'trechos')} em "
        + _count(beats, "pasta", "pastas")
        + f" dentro de {report['delivery']}."
    )
    if report["removed"]:
        line += " " + _count(len(report["removed"]), "link órfão removido", "links órfãos removidos") + "."
    if report["kept"]:
        line += " Deixei intocado o que você criou lá: " + ", ".join(report["kept"]) + "."
    # Mesma escada de `status` e `brief`: a pessoa ouve a mesma frase em qualquer comando.
    return {"summary": {"line": line, "next": _flow_next(ledger, rules)}, **report}


def _needs_preview(items):
    """Itens ainda em jogo e sem nenhum quadro: um item rejeitado não trava o fluxo."""
    return [c for c in items if not _has_preview(c) and _stage_status(c, "approval") != "rejected"]


def _undelivered(items):
    """Arquivos já conferidos que ainda não apareceram em `entrega/`."""
    return [c for c in items if STAGE_TESTS["verified"](c) and not (c.get("delivery") or {}).get("path")]


_UNSET = object()


def _flow_state(ledger, rules, counts=None, format_pending=0, brief=_UNSET):
    """Estado que a escada de `guidance` lê: a mesma leitura em status, brief e deliver."""
    items = ledger.data["items"]
    return {
        "project": str(ledger.root.parent),
        "counts": counts or {key: sum(1 for c in items if STAGE_TESTS[key](c)) for key in STAGE_TESTS},
        "format_pending": format_pending,
        "brief": brief_state(ledger.root.parent, rules, items) if brief is _UNSET else brief,
        "review_page": (ledger.root / "review.html").is_file(),
        "rights_mode": _rights_mode(rules),
        "candidate": _pending_candidate(items),
        "duration_unknown": len(_uninspected(items)),
        "inspect_candidate": next((c["id"] for c in _uninspected(items)), None),
        "undelivered": len(_undelivered(items)),
        "pending_preview": len(_needs_preview(items)),
    }


def _flow_next(ledger, rules):
    """A frase única para repassar à pessoa, sem parafrasear."""
    try:
        return next_action(_flow_state(ledger, rules))["for_human"]
    except (ValueError, OSError, KeyError):
        return None


def status_report(ledger, rules=None, rules_error=None, queue=None):
    """Onde o projeto está, por etapa. Somente leitura: não grava nada."""
    items = ledger.data["items"]
    listing = {key: [c["id"] for c in items if STAGE_TESTS[key](c)] for key, _, _ in STATUS_STAGES}
    counts = {key: len(listing[key]) for key, _, _ in STATUS_STAGES}
    pending_format = {c["id"]: _format_pending(c, rules) for c in items}
    format_pending = sum(1 for value in pending_format.values() if value)
    remembered, references_error = status_references(ledger.root)
    review_page = ledger.root / "review.html"
    brief = brief_state(ledger.root.parent, rules, items)
    line = _status_line({"counts": counts})
    if ledger.recovered:
        line += " Há uma gravação interrompida pendente; o próximo comando de escrita a concluirá."
    if queue and queue.get("error"):
        # queue.hint devolveu {"error": ...} (queue.json inválido/OSError): não some do resumo.
        line += f" Fila indisponível: {queue['error']}"
    elif queue and queue.get("line"):
        # Uma linha da fila social, lida sem gravar: contagens e próximo horário permitido.
        line += " " + queue["line"]
    # Veredito primeiro, como no doctor: o JSON completo continua logo abaixo.
    summary = {
        "line": line,
        "stages": [{"stage": plural, "count": counts[key], "items": listing[key]} for key, _, plural in STATUS_STAGES],
        "next": status_next(
            counts,
            format_pending,
            pending_preview=len(_needs_preview(items)),
            undelivered=len(_undelivered(items)),
        ),
        # Aditivo: `line/stages/next` seguem iguais; `do` traz o mesmo passo já em
        # comando pronto e `brief` diz quantos beats ainda estão sem material.
        "do": next_action(
            _flow_state(
                ledger,
                rules,
                counts=counts,
                format_pending=format_pending,
                brief=brief,
            )
        ),
        # Mesmo aviso de `review`: a página existe mas não tem o que revisar.
        "warnings": ([EMPTY_STORYBOARD] if review_page.is_file() and not counts["previews"] else []),
        # `None` enquanto não houver um brief válido para contar (ausente ou inválido).
        "brief": (
            None
            if brief is None or brief.get("error")
            else {
                "beats": brief["beats"],
                "covered": brief["covered"],
                "missing": len(brief["missing"]),
            }
        ),
    }
    return {
        "summary": summary,
        "project": str(ledger.root),
        "counts": counts,
        "stages": listing,
        "items": [
            {
                "id": c["id"],
                "title": c.get("title"),
                "provider": c.get("provider"),
                "source_url": c.get("source_url"),
                "state": c.get("state"),
                "segment": c.get("segment"),
                "approval": (c.get("approval") or {}).get("status"),
                "rights": (c.get("rights") or {}).get("status"),
                "preview": _has_preview(c),
                "format_pending": pending_format[c["id"]],
                "output": (c.get("output") or {}).get("path"),
            }
            for c in items
        ],
        "format_pending": format_pending,
        # Somente leitura: lê o PID file e pergunta ao sistema se o processo vive.
        "serve": serve_state(ledger.root.parent),
        "rules_error": rules_error,
        "references": remembered,
        "references_error": references_error,
        "queue": queue,
        "review_page": str(review_page) if review_page.is_file() else None,
        "journal": {
            **status_journal(ledger.root),
            "recovered_write": "pending" if ledger.recovered else False,
        },
    }


def _local_playwright(root=None):
    """Playwright CLI instalado em `.tools`, ou None quando não há um."""
    root = Path(root) if root is not None else SKILL_ROOT
    root = root / ".tools/node_modules/.bin"
    for name in ("playwright-cli.cmd", "playwright-cli"):
        if (root / name).is_file():
            return root / name
    return None


def execute(args):
    from getbrolls.config import load_env, settings

    if args.env_file and not Path(args.env_file).is_file():
        raise ValueError("--env-file não existe. Confira o caminho.")
    load_env(args.env_file or Path(__file__).resolve().parents[2] / ".env")
    config = settings()
    from getbrolls import providers

    if args.command in ("providers", "doctor"):
        result = providers.capabilities()
        if args.command == "doctor":
            from getbrolls.config import TOOL_PATH_KEYS

            from .social import doctor as social_doctor

            # Pin inválido vira item de `missing`, não morte do diagnóstico.
            overrides, pin_problems = doctor_overrides()
            resolved = doctor_resolved(overrides)
            try:
                social = social_doctor()
            except ValueError as exc:
                social = {"engine": "yt-dlp", "installed": False, "error": str(exc)}
            executables = {
                name: bool(overrides.get(TOOL_PATH_KEYS.get(name) or "") or shutil.which(name))
                for name in PROBED_EXECUTABLES
            }
            executables["yt-dlp"] = social["installed"]
            executables["playwright-cli"] = bool(_local_playwright() or shutil.which("playwright-cli"))
            summary = doctor_summary(executables, pin_problems)
            sheet = doctor_contact_sheet(executables.get("ffmpeg"))
            summary["optional"] += sheet["optional"]
            result = {
                # Veredito primeiro: o JSON continua completo logo abaixo dele.
                "summary": summary,
                "contact_sheet": sheet["status"],
                "get_brolls": __version__,
                "preview": config,
                "python": sys.version.split()[0],
                "tool_paths": overrides,
                "executables": executables,
                "resolved": resolved,
                "providers": result,
                "social": social,
            }
            if args.live:
                from getbrolls.health import live_checks

                result["live"] = live_checks()
        return result
    cmd = args.command
    if cmd == "serve":
        from getbrolls import serve as serve_module

        port = getattr(args, "port", None) or serve_module.DEFAULT_PORT
        if getattr(args, "stop", False):
            return serve_module.stop(args.project)
        if getattr(args, "background", False):
            return serve_module.start_background(args.project, port)
        return serve_module.run(args.project, port)
    from getbrolls.rules import allowed, domain_matches, format_report, load_rules

    if cmd == "status":
        # Somente leitura: nada é criado, nem a árvore do projeto, nem pendências.
        project = Path(args.project).expanduser().resolve()
        if not (project / "brolls").is_dir():
            raise ValueError(f"Projeto não encontrado em {project}; nenhum arquivo foi criado.")
        rules = None
        rules_error = None
        try:
            rules = load_rules(args.project)
        except (ValueError, OSError) as exc:
            rules_error = str(exc)
        return status_report(Ledger(project, recover=False), rules, rules_error, queue_hint(project))
    if cmd == "queue":
        rules = None
        try:
            rules = load_rules(args.project)
        except (ValueError, OSError) as exc:
            record_warning("RULES_UNAVAILABLE", f"RULES.md ignorado para o ritmo: {exc}")
        return queue_execute(args, rules)
    if cmd == "init-rules":
        dest = Path(args.project) / "RULES.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        video_format = getattr(args, "video_format", None)
        has_flags = bool(args.mode or args.responsible or args.declaration or video_format)
        if dest.exists() and not args.force:
            raise ValueError(
                "RULES.md já existe; edite sem sobrescrever suas regras. "
                "Use --force com --mode/--responsible/--declaration/--format para regravar só o bloco JSON."
            )
        if args.force and not has_flags:
            raise ValueError(
                "--force só regrava o bloco JSON: informe --mode, --responsible, --declaration ou --format."
            )
        template = SKILL_ROOT / "docs" / "RULES.md"
        if has_flags:
            # Regravar preserva o que o usuário já escolheu: a base é o arquivo dele.
            source = dest if dest.exists() else template
            text, rights = rules_from_flags(
                source.read_text(encoding="utf-8"),
                args.mode,
                args.responsible,
                args.declaration,
                video_format=video_format,
            )
            dest.write_text(text, encoding="utf-8")
            result = {"rules": str(dest), "copyright": rights}
            if video_format:
                result["video_format"] = video_format
            return result
        shutil.copyfile(template, dest)
        return {"rules": str(dest)}
    if cmd == "init-brief":
        from getbrolls.brief import brief_path

        # O arquivo que conta é o mesmo que `brief` vai ler, GB_BRIEF_FILE incluído:
        # criar um BRIEF.md que ninguém lê seria pior que recusar.
        dest = brief_path(args.project)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            raise ValueError(
                f"{dest} já existe; edite o plano deste vídeo sem sobrescrever o que "
                "você já respondeu. Rode `brief --validate --project ...` para conferi-lo."
            )
        shutil.copyfile(SKILL_ROOT / "docs" / "BRIEF.md", dest)
        return {"brief": str(dest)}
    if cmd == "brief":
        # Somente leitura, como status: orienta a coleta sem criar nada no projeto.
        return brief_report(args)
    if cmd in ("learn", "library"):
        # A biblioteca é pessoal e vive fora do projeto: não depende das regras
        # dele nem passa pelo portão de formato.
        return library_command(args)
    rules = load_rules(args.project)
    if cmd == "rules":
        return rules
    ledger = Ledger(args.project)
    from getbrolls.rules import sync_formats

    # Consultas (`references`, `inspect`) não decidem nada sobre formato: como o
    # `status`, elas nunca podem ser barradas pelo portão de `--confirm-format-change`.
    # `deliver --dry-run` é ensaio: não pode reescrever o manifesto nem por tabela.
    if cmd not in READ_ONLY_CONSULTS and not (cmd == "deliver" and getattr(args, "dry_run", False)):
        sync_formats(ledger, rules, confirm=getattr(args, "confirm_format_change", False))
    if cmd == "deliver":
        return deliver_report(ledger, rules, getattr(args, "dry_run", False))
    if cmd == "browser-plan":
        from getbrolls.browser import plan

        return plan(ledger, args.url, rules)
    if cmd == "references":
        path = ledger.root / "references.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}

    if cmd == "inspect":
        return inspect_source(ledger, args)

    if cmd == "search":
        from getbrolls import library

        if "video" not in rules["asset_types"]:
            return {
                "items": [],
                "errors": [],
                "note": "APIs atuais pesquisam vídeos. Para imagem/notícia use importação local ou browser-plan.",
            }
        args.provider = {"pixel": "pexels", "getbrolls": "auto"}.get(args.provider, args.provider)
        if not 1 <= args.limit <= 50:
            raise ValueError("Use --limit entre 1 e 50.")
        shot = (getattr(args, "shot", None) or "").strip() or None
        if shot:
            # Mesma regra de `resolve --shot`: o beat vira sufixo do id, e é isso que
            # liga o candidato ao BRIEF.md sem precisar re-registrar por URL depois.
            if not re.fullmatch(SHOT_RE, shot):
                raise ValueError("--shot: use 1–80 letras, números, hífen ou underscore.")
        dry_run = bool(getattr(args, "dry_run", False))
        names = rules["preferred_providers"][args.intent] if args.provider == "auto" else [args.provider]
        if not names:
            raise ValueError(
                "Nenhuma fonte configurada: use resolve --file, Commons/NASA ou configure a chave de um banco."
            )
        items = []
        errors = []
        excluded = 0
        for name in names:
            if len(items) >= args.limit:
                break
            try:
                candidates = providers.search(name, args.query, args.limit - len(items))
            except ValueError as e:
                errors.append({"provider": name, "error": str(e)})
                record_warning("PROVIDER_FAILED", f"{name}: {e}")
                # Fonte que falhou é aprendizado barato e honesto; fica marcado
                # como `auto` porque ninguém digitou esse registro.
                try:
                    library.learn_query(args.query, name, "miss", note=str(e), auto=True)
                except (ValueError, OSError) as failure:
                    record_warning("LIBRARY_WRITE_FAILED", str(failure))
                continue
            # ledger.add/save stay outside the provider try: a disk/write error here is not the
            # provider's fault and must not be attributed to it as a search failure.
            for c in candidates:
                if not allowed(c, rules):
                    excluded += 1
                    continue
                c["format"] = format_report(c, rules)
                c["match"] = {
                    "kind": args.intent,
                    "reason": "Candidato de busca: correspondência visual deve ser revisada.",
                }
                if shot:
                    c["id"] += ":shot:" + shot
                    c["shot"] = shot
                if dry_run:
                    # Busca de diagnóstico não entra no manifesto: a contagem do
                    # `status` é do vídeo, não do que o agente experimentou.
                    items.append(c)
                    continue
                c = ledger.add(c)
                ledger.save("search", c)
                items.append(c)
        if not items and errors:
            raise ValueError("; ".join(f"{e['provider']}: {e['error']}" for e in errors))
        items.sort(key=lambda c: not domain_matches(c.get("source_url"), rules["preferred_domains"]))
        result = {
            "items": items,
            "errors": errors,
            "excluded_by_rules": excluded,
            "editorial_rules": rules["editorial_rules"],
            "dry_run": dry_run,
        }
        if dry_run:
            result["note"] = (
                "Busca de diagnóstico: nada foi registrado no projeto. Repita sem "
                "`--dry-run` para guardar os candidatos que você quiser."
            )
        # Pistas da biblioteca são memória editorial, não permissão: cada uma
        # repete `rights_not_transferable` e nenhuma toca no candidato.
        found = library.hints(args.query)
        if found:
            result["library_hints"] = found
        return result
    if cmd == "resolve":
        for flag, value in (("--file", args.file), ("--url", args.url)):
            if value is not None and not value.strip():
                raise ValueError(f"{flag} não pode ser vazio: informe o caminho ou a URL real.")
        if args.file:
            path = Path(args.file).expanduser().resolve()
            if not path.is_file():
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
        else:
            c = providers.resolve(args.url)
        for argument, field in (
            (args.context_image, "context_image"),
            (args.full_preview_file, "full_preview"),
        ):
            if argument:
                if not args.file:
                    raise ValueError("Contexto/composição exigem um B-roll local em --file.")
                auxiliary = Path(argument).expanduser().resolve()
                if not auxiliary.is_file():
                    raise ValueError("Arquivo de contexto/composição não encontrado.")
                if field == "context_image" and auxiliary.suffix.lower() not in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                ):
                    raise ValueError("--context-image deve ser um print PNG/JPG/WebP.")
                c[field + "_path"] = str(auxiliary)
                c[field + "_sha256"] = digest(auxiliary)
                c[field + "_media"] = probe(auxiliary)
        if args.file:
            inferred = (
                "image" if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff") else "video"
            )
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
        elif args.asset_type or args.title or args.captured_at:
            raise ValueError("Metadados locais exigem --file.")
        if args.source_url or args.creator:
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
        if args.shot:
            if not re.fullmatch(SHOT_RE, args.shot):
                raise ValueError("--shot: use 1–80 letras, números, hífen ou underscore.")
            c["id"] += ":shot:" + args.shot
            c["shot"] = args.shot
        if c.get("asset_type") in ("news_screenshot", "web_screenshot") and not c.get("source_url"):
            raise ValueError("Screenshot exige --source-url para manter a origem.")
        if not allowed(c, rules):
            raise ValueError("Fonte ou tipo de asset bloqueado pelas regras do usuário.")
        c["format"] = format_report(c, rules)
        c = ledger.add(c)
        ledger.save(cmd, c)
        return c
    if cmd == "import-review":
        from getbrolls.review import import_review

        result = import_review(ledger, args.file, args.by, rules)
        render(ledger)
        return result
    if cmd == "review":
        page = render(ledger)
        if not any(_has_preview(c) for c in ledger.data["items"]):
            # Publicar uma página sem nada para decidir manda a pessoa abrir uma URL
            # à toa. O aviso aparece aqui e em `status`, no mesmo código.
            record_warning("EMPTY_STORYBOARD", EMPTY_STORYBOARD)
        return {"review": page}
    if cmd == "verify":
        checked = []
        for c in ledger.data["items"]:
            if c["output"]["path"]:
                path = ledger.root / c["output"]["path"]
                info = probe(path)
                if digest(path) != c["output"]["sha256"]:
                    raise ValueError("Arquivo alterado após coleta: " + c["id"])
                run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
                checked.append(
                    {
                        "id": c["id"],
                        "media": info,
                        "hd": min(info["width"], info["height"]) >= 1080,
                    }
                )
        # `entrega/` é camada derivada: refazê-la nunca pode reprovar a conferência dos
        # arquivos canônicos. Se o sistema não deixar ligar/copiar, isso vira aviso.
        from getbrolls import delivery as delivery_module

        try:
            delivery_module.build_delivery(args.project, ledger=ledger, for_human=lambda: _flow_next(ledger, rules))
        except (ValueError, OSError) as exc:
            record_warning(
                "DELIVERY_LINK_FAILED",
                f"Os arquivos estão íntegros, mas não consegui refazer entrega/: {exc}",
            )
        return {"verified": checked, "count": len(checked)}
    if cmd == "preview" and args.scan:
        if args.start is not None or args.end is not None:
            raise ValueError(
                "--scan varre o vídeo inteiro: não combine com --start/--end. "
                "Escolha o intervalo depois, olhando a varredura."
            )
        if args.reference_only:
            raise ValueError("--scan precisa da mídia de trabalho; não use com --reference-only.")

    if cmd == "approve":
        chosen = list(args.candidate or [])
        if args.channel == "chat" and not (args.statement or "").strip():
            raise ValueError("Aprovação pelo chat exige --statement com a frase exata dita pela pessoa.")
        if args.all and chosen:
            raise ValueError("Use --all sozinho ou --candidate ID (repetindo a flag), nunca os dois juntos.")
        if args.all and (args.start is not None or args.end is not None):
            raise ValueError("--all aprova os intervalos já escolhidos; não use --start/--end.")
        if not args.all and not chosen:
            raise ValueError(
                "Informe --candidate ID (repita a flag para vários), ou use --all para "
                "todos os candidatos com prévia."
            )
        if len(chosen) > 1 and (args.start is not None or args.end is not None):
            raise ValueError("--start/--end valem para um candidato só; aprove um por vez para mudar o intervalo.")
        if args.all:
            return approve_all(ledger, args, rules)
        if len(chosen) > 1:
            return approve_all(ledger, args, rules, only=chosen)
        args.candidate = chosen[0]
    c = ledger.get(args.candidate)
    if not allowed(c, rules) and cmd in ("preview", "approve", "permit", "fetch"):
        raise ValueError("Asset bloqueado pelas regras atuais do usuário.")
    if cmd == "remember":
        from getbrolls.memory import remember

        return remember(ledger, c, args.decision, args.reason, args.by)
    if cmd == "preview" and args.scan:
        return scan_candidate(ledger, c, config)
    if cmd in ("preview", "approve"):
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
    if cmd == "approve":
        approve(c, args.by, args.channel, args.statement)
    elif cmd == "permit":
        preset = getattr(args, "preset", None)
        if preset and (args.declaration or args.declared_by or args.declaration_text):
            raise ValueError(
                "--preset registra as condições genéricas da fonte; não combine com "
                "declaração de responsabilidade. Escolha um dos dois."
            )
        if preset:
            # O preset nunca vira licença: ele diz o que a fonte costuma exigir e manda
            # conferir a página do item. Quem assina continua responsável, e `fetch`
            # continua exigindo a aprovação humana.
            evidence = PERMIT_PRESETS[preset]["text"]
            if args.evidence is not None:
                if not args.evidence.strip():
                    raise ValueError("Evidência não pode ser vazia.")
                evidence += " | Verificado por quem pediu: " + args.evidence.strip()
            c["rights"]["basis"] = "per_item_evidence"
        elif args.declared_by or args.declaration_text:
            name = (args.declared_by or "").strip()
            text = (args.declaration_text or "").strip()
            if not name or name.lower() in GENERIC_NAMES:
                raise ValueError("Informe em --declared-by o nome real de quem assume a responsabilidade.")
            if len(text) < 20:
                raise ValueError("--declaration-text precisa da frase literal da pessoa, com 20 caracteres ou mais.")
            evidence = "Declaração do usuário " + name + ": " + text
            c["rights"]["basis"] = "user_declaration"
            c["rights"]["responsible_person"] = name
            c["rights"]["declaration_channel"] = "chat"
        elif args.declaration:
            rights = rules["copyright"]
            if rights["mode"] != "user_declaration":
                raise ValueError("Usuário deve configurar sua declaração em RULES.md primeiro.")
            evidence = "Declaração do usuário " + rights["responsible_person"] + ": " + rights["declaration"]
            c["rights"]["basis"] = "user_declaration"
            c["rights"]["responsible_person"] = rights["responsible_person"]
        else:
            if args.evidence is None:
                raise ValueError(
                    "Diga as condições de uso: --evidence TEXTO, ou "
                    '--declared-by NOME --declaration-text "frase da pessoa".'
                )
            if not args.evidence.strip():
                raise ValueError("Evidência não pode ser vazia.")
            evidence = args.evidence
            c["rights"]["basis"] = "per_item_evidence"
        c["rights"]["status"] = "permitted"
        c["rights"]["evidence"].append(evidence)
    elif cmd == "reject":
        c["approval"]["status"] = "rejected"
        c["state"] = "rejected"
        c.pop("review", None)
    elif cmd == "preview":
        context_before = signature(c)
        if not args.reference_only and c["provider"] != "local":
            # Vídeo sem --start/--end já parou antes, no guard de `preview`/`approve`.
            asked = args.end - args.start  # pyright: ignore[reportOptionalOperand]
            if asked > config["max_seconds"]:
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
        else:
            c["preview"]["warning"] = "Somente referência estática; o trecho animado requer original local autorizado."
            c["state"] = "reference_only"
            # Sem um arquivo de imagem ninguém decide nada, e `status` nem conta o item
            # como tendo prévia. A miniatura pública da fonte já basta para isso.
            reference_poster(ledger, c)
        if c["preview"].get("warning"):
            record_warning("PREVIEW_LIMITATION", c["preview"]["warning"])
        if args.narration is not None:
            c["narration"] = args.narration
        if args.reason is not None:
            c["match"]["reason"] = args.reason
        if context_before != signature(c):
            c["approval"] = {
                "status": "pending",
                "by": None,
                "at": None,
                "revision": None,
            }
            c.pop("review", None)
            c["state"] = "awaiting_approval" if c.get("local_path") else "reference_only"
    elif cmd == "fetch":
        require_fetch(c)
        src = c.get("local_path")
        temp = None
        if src:
            if digest(src) != c["local_sha256"]:
                raise ValueError("Original local mudou: importe novamente e aprove a nova versão.")
        else:
            # Re-resolve from the provider to refresh temporary variant URLs.
            fresh = providers.refresh(c)
            url = fresh.get("media_url")
            if not url:
                raise ValueError(
                    "Esta fonte não disponibilizou arquivo por transporte permitido; "
                    f"execute antes: preview --candidate {c['id']} --start ... --end ..."
                )
            from getbrolls.http import download

            temp = (
                ledger.root / "previews" / ("download-" + hashlib.sha256(c["id"].encode()).hexdigest()[:16] + ".part")
            )
            download(url, temp)
            src = temp
        if c.get("media", {}).get("kind") == "image":
            rel = (
                "clips/"
                + hashlib.sha256(c["id"].encode()).hexdigest()[:16]
                + f"-r{c['segment']['revision']}"
                + Path(src).suffix.lower()
            )
            dest = ledger.root / rel
            if dest.exists():
                raise ValueError(_already_collected(rel))
            from .media import copy_image

            copy_image(src, dest)
            c["output"] = {"path": rel, "sha256": digest(dest), "verified": True}
            c["state"] = "verified"
            ledger.save(cmd, c)
            render(ledger)
            return c
        rel = "clips/" + hashlib.sha256(c["id"].encode()).hexdigest()[:16] + f"-r{c['segment']['revision']}.mp4"
        # O arquivo entregue nasce somente-leitura (delivery._freeze congela o inode
        # compartilhado): sem esta checagem o ffmpeg falharia por permissão, sem dizer
        # o motivo. Recusar aqui, antes de gastar a fonte, explica o que fazer.
        if (ledger.root / rel).exists():
            if temp:
                temp.unlink(missing_ok=True)
            raise ValueError(_already_collected(rel))
        try:
            offset = c.get("local_start_s", 0)
            start = c["segment"]["start_s"] - offset
            end = c["segment"]["end_s"] - offset
            if start < 0 or (c.get("local_duration_s") is not None and end > c["local_duration_s"] + 0.1):
                raise ValueError("Gere uma nova prévia para este intervalo antes da coleta.")
            cut(src, ledger.root / rel, start, end)
        finally:
            if temp:
                temp.unlink(missing_ok=True)
        c["output"] = {
            "path": rel,
            "sha256": digest(ledger.root / rel),
            "verified": True,
        }
        c["state"] = "verified"
        c["output_media"] = probe(ledger.root / rel)
    # O journal distingue a decisão dita no chat da que veio assinada pelo Storyboard.
    ledger.save("approve-chat" if cmd == "approve" and args.channel == "chat" else cmd, c)
    render(ledger)
    if cmd == "preview":
        # Absolute paths for the agent to open the exact files the Storyboard shows.
        # They live only in this response, never in the manifest.
        return {**c, "files": preview_files(ledger, c)}
    return c


def reference_poster(ledger, c):
    """Materializa o cartaz estático da referência: miniatura da fonte, ou 1º quadro local.

    Não baixa o vídeo e não escolhe intervalo: só garante que exista um arquivo de
    imagem para a pessoa olhar (e para `status` contar como prévia). Falhar aqui é
    aceitável — vira aviso, não erro, porque a referência continua válida sem imagem.
    """
    from .media import image_preview

    if c["preview"].get("poster_path"):
        return c["preview"]["poster_path"]
    stem = hashlib.sha256(c["id"].encode()).hexdigest()[:16] + "-ref"
    previews = ledger.root / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    source = c.get("local_path")
    temp = None
    try:
        if not source:
            url = (c.get("preview") or {}).get("poster_url")
            if not url:
                record_warning(
                    "REFERENCE_POSTER_MISSING",
                    "A fonte não ofereceu miniatura pública; a referência fica sem imagem.",
                )
                return None
            from .http import download

            temp = previews / (stem + ".part")
            temp.unlink(missing_ok=True)
            download(url, temp, max_bytes=32 * 1024 * 1024)
            source = temp
        result = image_preview(source, previews, stem)
    except (OSError, ValueError, RuntimeError) as error:
        record_warning(
            "REFERENCE_POSTER_MISSING",
            f"Não consegui montar o cartaz estático desta referência ({error}).",
        )
        return None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    c["preview"]["poster_path"] = result["poster_path"]
    return result["poster_path"]


def _already_collected(rel):
    """Mesma recusa para imagem e vídeo: já existe corte desta revisão, e eu não sobrescrevo."""
    return (
        f"Arquivo final já existe e não foi sobrescrito: `{rel}`. Esta revisão do "
        "trecho já está coletada — rode `verify` para conferir, ou gere uma prévia "
        "nova (novo `--start`/`--end`) se quiser outro corte."
    )


def preview_files(ledger, c):
    """Absolute paths of the preview artifacts that the Storyboard embeds for this item."""
    files = {}
    for key, name in (
        ("contact_sheet_path", "contact_sheet"),
        ("poster_path", "poster"),
        ("gif_path", "gif"),
    ):
        rel = c["preview"].get(key)
        files[name] = str((ledger.root / rel).resolve()) if rel else None
    files["review"] = str((ledger.root / "review.html").resolve())
    return files


def inspect_source(ledger, args):
    """O que a fonte já conta sobre si, antes de qualquer pedido de mídia.

    Somente leitura sobre decisão e intervalo: com `--candidate`, o único campo que
    passa a existir no projeto é `media.duration_s` — nada de aprovação, segmento,
    prévia ou arquivo em `clips/`.
    """
    from .inspecting import candidate_windows
    from .social import probe_remote

    if args.max_windows is not None and not 1 <= args.max_windows <= 20:
        raise ValueError("Use --max-windows entre 1 e 20.")
    c = None
    if args.candidate:
        c = ledger.get(args.candidate)
        url = c.get("source_url")
        if not url:
            raise ValueError(
                "Este candidato não tem URL pública para analisar; use `inspect --url` "
                "ou importe o original local com `resolve --file`."
            )
    else:
        url = args.url
        if not (url or "").strip():
            raise ValueError("--url não pode ser vazio: informe a URL pública da fonte.")
    probe = probe_remote(url, cache=ledger.root.parent / ".getbrolls-sources")
    windows = candidate_windows(probe, args.query, args.max_windows or 3)
    if c is not None and probe["duration_s"]:
        # Único efeito no projeto: agora `set_segment` sabe recusar o que não cabe.
        c["media"]["duration_s"] = probe["duration_s"]
        ledger.save("inspect", c)
    return {
        # Veredito primeiro, como nos outros comandos: quantas janelas e qual a melhor.
        "summary": inspect_summary(windows, probe),
        "candidate": c["id"] if c is not None else None,
        "url": url,
        "title": probe.get("title"),
        "duration_s": probe["duration_s"],
        "chapters": probe["chapters"],
        "subtitle_langs": probe["subtitle_langs"],
        "subtitle_langs_total": probe.get("subtitle_langs_total", len(probe["subtitle_langs"])),
        "candidate_windows": windows,
    }


def _clock(seconds):
    total = int(round(float(seconds or 0)))
    return f"{total // 60}:{total % 60:02d}"


def _has_cues(probe):
    """Alguma legenda chegou de fato, com falas dentro? Anunciar idioma não é ter legenda."""
    return any((entry or {}).get("cues") for entry in (probe.get("subtitles") or {}).values())


def inspect_summary(windows, probe):
    """`{line, next}` em PT-BR: quantas janelas saíram, qual a melhor e o que fazer com ela."""
    if not windows:
        return {
            "line": "A fonte não deu capítulo, legenda nem marcação de tempo: não tenho por onde começar.",
            "next": "Rode `preview --scan` para ver a grade do vídeo inteiro e escolher o trecho olhando.",
        }
    best = windows[0]
    found = best["score"] > 0
    # Janela de relógio sem uma única fala lida: são pontos igualmente espaçados, e
    # chamá-los de "ponto de partida" deixaria parecer que alguém leu o vídeo.
    blind = not found and not _has_cues(probe) and best.get("source") == "even_spacing"
    where = f"{_clock(best['start_s'])}–{_clock(best['end_s'])}"
    if found:
        middle = f"A mais parecida com o que você pediu está em {where}"
    elif blind:
        middle = (
            f"Sem legendas obtidas para esta fonte: não li nenhuma fala, e {where} é só "
            "um ponto igualmente espaçado no relógio, não um trecho encontrado"
        )
    else:
        middle = f"Nenhuma casou com a frase, então a primeira é só um ponto de partida: {where}"
    line = (
        f"Analisei a fonte e separei {_count(len(windows), 'janela', 'janelas')}. "
        + middle
        + (f" ({best['source']})." if best.get("source") else ".")
    )
    if probe.get("duration_s"):
        line += f" O vídeo tem {_clock(probe['duration_s'])}."
    return {
        "line": line,
        "next": (
            f"Confirme olhando: `preview --start {best['start_s']} --end {best['end_s']}`"
            if found
            else "Confirme antes de baixar: `preview --scan` mostra a grade do vídeo "
            f"inteiro, ou gere a prévia de {where} e olhe o contact sheet."
        ),
    }


def _scan_note(span, duration, capped, ceiling):
    """Frase em PT-BR dizendo quanto do vídeo entrou na varredura, e por quê."""
    if capped:
        return (
            f"Varri os primeiros {span:.0f} s de um vídeo de {duration:.0f} s: o teto "
            f"GB_SCAN_MAX_SECONDS está em {int(ceiling)} s. Para ver o resto, aumente o "
            "teto ou varra o candidato de novo depois de escolher um trecho."
        )
    return f"Baixei e varri o vídeo inteiro ({span:.0f} s) para montar a grade."


def scan_candidate(ledger, c, config):
    """Contact sheet de baixa resolução do vídeo inteiro; não escolhe intervalo nenhum."""
    from .media import scan_sheet

    if c.get("media", {}).get("kind") == "image":
        raise ValueError("Imagem estática não tem o que varrer; gere a prévia normal.")
    duration = c["media"].get("duration_s")
    if not duration and c["provider"] != "local":
        from .social import probe_remote

        probe_data = probe_remote(c["source_url"], cache=ledger.root.parent / ".getbrolls-sources")
        duration = probe_data["duration_s"]
        if duration:
            c["media"]["duration_s"] = duration
    if not duration:
        raise ValueError("Duração desconhecida: rode `inspect --candidate " + c["id"] + "` antes de varrer.")
    duration = float(duration)
    # O teto vale sobre a duração real: pedir 900 s de um vídeo de 126 s faz a fonte
    # devolver menos do que o pedido, e a grade sairia rotulada com tempos que não existem.
    span = min(duration, float(config["scan_max_seconds"]))
    if c["provider"] != "local":
        from .acquisition import prepare_source

        # `tolerant`: varrer o vídeo inteiro não pode falhar porque a fonte entregou
        # alguns segundos a menos do que anunciou.
        prepare_source(ledger, c, 0, span, tolerant=True)
    source = c.get("local_path")
    if not source:
        raise ValueError("A varredura precisa da mídia de trabalho; esta fonte só permite referência estática.")
    offset = c.get("local_start_s", 0)
    stem = hashlib.sha256(c["id"].encode()).hexdigest()[:16]
    # Tempo do arquivo de trabalho para o ffmpeg; tempo da fonte nos rótulos.
    local_start = max(0, -offset)
    # A grade se mede pelo que existe no arquivo de trabalho, não pelo que foi pedido:
    # senão os últimos quadros vêm vazios e os rótulos apontam para o nada.
    available = c.get("local_duration_s")
    if available is None:
        from .media import probe as probe_media

        available = probe_media(source).get("duration_s")
    if available:
        span = min(span, max(0.0, float(available) - local_start))
    if span <= 0:
        raise ValueError("A mídia de trabalho não tem quadros para varrer; gere uma prévia do trecho que te interessa.")
    result = scan_sheet(
        source,
        ledger.root / "previews",
        stem,
        local_start,
        span,
        source_offset=offset,
    )
    # `scan` fica fora de `preview`/`segment`: varrer não decide nem invalida nada.
    capped = span < duration - 0.1
    c["scan"] = {
        **result,
        "capped": capped,
        "duration_s": duration,
        # O que realmente entrou na grade, em segundos de mídia baixada.
        "downloaded_seconds": round(span, 3),
        "note": _scan_note(span, duration, capped, config["scan_max_seconds"]),
    }
    ledger.save("preview", c)
    render(ledger)
    return {
        **c,
        "files": {"scan": str((ledger.root / result["scan_path"]).resolve())},
    }
