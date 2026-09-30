"""Existing workflow command handlers; CLI parsing and reporting live separately."""

# pylint: disable=too-many-lines,fixme,cyclic-import
# `cyclic-import`: `_execute_toolchain` importa `cli.build_parser` e `capabilities` de forma tardia (só roda
# com tudo carregado); o pylint só permite suprimir R0401 no módulo.
# Legado: módulo já ultrapassava 1000 linhas antes da 2.6.0; `fixme` é o comentário
# "Todo executável..." pré-existente (não é um TODO de verdade, é o nome da variável).

import contextlib
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

from . import __version__, _paths, logs
from .acquisition import candidate_arg
from .config import CAP_EPSILON
from .errors import DataRootError
from .guidance import blocked_beats_question, next_action
from .ledger import Ledger, digest
from .media import cut, probe, run
from .models import (
    approve,
    candidate,
    empty_output,
    id_stem,
    now,
    pending_approval,
    require_fetch,
    set_segment,
    signature,
)
from .presets import PERMIT_PRESETS  # noqa: F401 -- tests importam daqui; pylint: disable=unused-import
from .queue import execute as queue_execute
from .queue import hint as queue_hint
from .queue import summary_line as queue_summary_line
from .rendering import render
from .runtime import record_warning

_log = logs.get("commands")

# Raiz real da skill/plugin: o comando sugerido não pode depender da pasta atual.
INSTALLER = _paths.installer_hint()
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


def data_fix():
    """Como repor os arquivos de dados: o instalador no checkout, reinstalar no pacote."""
    return _paths.installer_hint() if _paths.origin() == "checkout" else _paths.REINSTALL_COMMAND


def data_missing():
    """Arquivos de dados que faltam (todos, quando nem a pasta de dados existe)."""
    try:
        return _paths.verify_data()
    except DataRootError:
        return list(_paths.REQUIRED_DATA)


def readiness_probe():
    """A sondagem que decide o `ready` do `doctor` e do `setup --check`: pins, engine social
    e a presença de cada executável sondado, pela mesma regra nos dois."""
    from .social import doctor as social_doctor

    # Pin inválido vira item de `missing`, não morte do diagnóstico.
    overrides, pin_problems = doctor_overrides()
    try:
        social = social_doctor()
    except ValueError as exc:
        social = {"engine": "yt-dlp", "installed": False, "error": str(exc)}
    return overrides, pin_problems, social, _doctor_executables(overrides, social)


def doctor_summary(executables, pins=(), data_missing=()):
    """Veredito humano do doctor: o que funciona, o que falta e o que é opcional."""
    ok = sorted(name for name, present in executables.items() if present)
    missing = [
        {"item": name, "fix": REQUIRED_EXECUTABLES[name][0], "note": REQUIRED_EXECUTABLES[name][1]}
        for name in sorted(REQUIRED_EXECUTABLES)
        if not executables.get(name)
    ]
    missing += list(pins)
    if data_missing:
        missing.append({"item": "dados da instalação", "fix": data_fix(), "note": "Faltam: " + ", ".join(data_missing)})
    optional = [
        {"item": name, "note": note} for name, note in sorted(OPTIONAL_EXECUTABLES.items()) if not executables.get(name)
    ]
    optional += [{"item": key, "note": note} for key, note in sorted(OPTIONAL_KEYS.items()) if not os.environ.get(key)]
    return {"ok": ok, "missing": missing, "optional": optional}


PLUGIN_PROBLEM_STATUSES = ("failed", "suspended", "invalid", "incompatible")


def doctor_plugin_problems(rows):
    """Uma linha para o `summary` quando algum plugin não carregou: quem lê
    só o veredito vê a falha sem abrir `plugins[]`. `None` quando está tudo certo."""
    broken = [row for row in rows if row.get("status") in PLUGIN_PROBLEM_STATUSES]
    if not broken:
        return None
    names = ", ".join(f"{row['id']} ({row['status']})" for row in broken)
    label = "plugin com problema" if len(broken) == 1 else "plugins com problema"
    return (
        f"{len(broken)} {label}: {names}. O motivo de cada um está em plugins[] deste doctor; "
        "conserte a pasta do plugin (ou reinstale) e habilite de novo com plugins --action enable."
    )


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

# Teto de palavras que uma busca leva à fonte. Acima disso a query é uma oração, e
# API de vídeo casa por palavra: a frase inteira volta vazia sem explicar por quê.
SEARCH_QUERY_TOKENS = 6

# Quantos títulos o resumo falado de busca mostra antes de dizer "e mais N na lista".
SEARCH_SUMMARY_PREVIEW_TITLES = 3

# `--shot` de `search` e de `resolve` valem a mesma coisa: o beat vira sufixo do id.
SHOT_RE = r"[A-Za-z0-9_-]{1,80}"

# Storyboard publicado sem nenhuma prévia: a página sobe, mas não há o que decidir.
EMPTY_STORYBOARD = (
    "Storyboard vazio: nenhuma prévia. A página sobe, mas não há nada para decidir — "
    "gere as prévias com `preview` antes de mandar o endereço para alguém."
)

# Comandos que só consultam o projeto: `inspect` grava no máximo `media.duration_s`
# e `references` não grava nada — nenhum dos dois muda formato nem aprovação.
READ_ONLY_CONSULTS = ("references", "inspect")
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
    line = formatter(result)
    # `summary` é sempre um objeto com `line`. Alguns comandos devolviam a frase solta
    # como string, e quem lê o JSON tinha que saber de cor qual comando fala de que
    # jeito — a mesma leitura (`summary.line`) tem que servir para todos.
    return {**result, "summary": line if isinstance(line, dict) else {"line": line}}


# Escada do fluxo: a primeira condição verdadeira nomeia o próximo passo real.
# Item aprovado segue para permit/fetch/verify/deliver antes de prévia nova: quem
# sobrou sem quadro é rascunho (ver `guidance.flow_complete`), não trava o aprovado.
STATUS_LADDER = (
    (
        lambda c: not c["candidates"],
        "Nenhum candidato ainda: registre fontes com search ou resolve.",
    ),
    (
        lambda c: c["permitted"] < c["approved"],
        "Registre as condições reais de uso com permit nos itens aprovados.",
    ),
    (
        lambda c: c["approved"] and c["delivered"] < c["permitted"],
        "Colete os cortes aprovados e permitidos com fetch.",
    ),
    (
        lambda c: c["approved"] and c["verified"] < c["delivered"],
        "Confira os arquivos coletados com verify.",
    ),
    (
        lambda c: c["approved"] and c["undelivered"] > 0,
        "Organize os trechos conferidos em entrega/ com deliver.",
    ),
    (
        lambda c: c["pending_preview"] > 0,
        "Gere prévias com preview para os candidatos ainda sem quadro.",
    ),
    (
        lambda c: not c["approved"],
        (
            "Peça a decisão humana: pelo Storyboard (review + import-review) ou pela fala "
            "no chat (approve --candidate ID --by NOME --channel chat --statement "
            '"frase"), com os IDs que você mostrou.'
        ),
    ),
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
        return (
            "As regras editoriais mudaram: o próximo comando invalidará "
            + _count(format_pending, "aprovação", "aprovações")
            + "; gere prévia e revisão novamente antes de coletar."
        )
    from .guidance import flow_complete, leftover_aside, leftovers

    state = {"undelivered": counts["undelivered"]}
    counts = {"pending": 0, "rejected": 0, **counts}
    complete = "Fluxo completo: os itens aprovados estão coletados, verificados e organizados em entrega/."
    # Mesma ordem de `guidance.next_action`: decisão humana pendente ganha de tudo,
    # inclusive do veredito de fim de fluxo. Só depois dela é que a entrega pronta vira
    # "Fluxo completo", e aí a sobra sem prévia é aparte, não próximo passo.
    if counts.get("pending"):
        return (
            "Peça a decisão humana: há prévia esperando alguém decidir, pelo Storyboard "
            "(review + import-review) ou pela fala no chat (approve --candidate ID --by "
            'NOME --channel chat --statement "frase").'
        )
    if flow_complete(state, counts):
        return complete + leftover_aside(leftovers(state, counts))
    for matches, step in STATUS_LADDER:
        if matches(counts):
            return step
    return complete


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


def rules_from_flags(template, mode, responsible, declaration, video_format=None):
    """Reescreve só o bloco ```json do modelo, preservando toda a prosa do arquivo."""
    blocks = re.findall(r"```json\s*\n(.*?)\n```", template, re.DOTALL)
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
    # Só com ROTEIRO.md do get-brolls: fora de sincronia, os beats vêm do sync do roteiro;
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
                else "Pode buscar: `brief --project ...` mostra o comando pronto de cada beat."
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


def _brief_next_action_state(ctx, args, root, manifest, coverage):
    """Monta o mesmo `state` que `status` usa, para `next_action` devolver a mesma frase."""
    from getbrolls.brief import search_plan

    items = manifest["items"]
    listed, covered, missing, blocked = (
        coverage["listed"],
        coverage["covered"],
        coverage["missing"],
        coverage["blocked"],
    )
    return {
        "project": args.project,
        "counts": {
            "candidates": len(items),
            "previews": sum(1 for c in items if _has_preview(c)),
            # Sem estes dois a escada nunca via a decisão humana daqui, e o
            # `brief` mandava buscar o beat vazio enquanto o `status` pedia
            # aprovação do mesmo projeto: dois comandos, dois próximos passos.
            "pending": sum(1 for c in items if STAGE_TESTS["pending"](c)),
            "rejected": sum(1 for c in items if STAGE_TESTS["rejected"](c)),
            "approved": sum(1 for c in items if STAGE_TESTS["approved"](c)),
            "permitted": sum(1 for c in items if STAGE_TESTS["permitted"](c)),
            "delivered": sum(1 for c in items if STAGE_TESTS["delivered"](c)),
            "verified": sum(1 for c in items if STAGE_TESTS["verified"](c)),
        },
        "brief": {
            "beats": len(listed),
            "covered": covered,
            "missing": [
                missing_beat_entry(
                    entry["id"],
                    entry["resolved"],
                    entry["commands"],
                    search_plan(entry["resolved"], tried_searches(manifest, entry["id"])),
                )
                for entry in missing
            ],
            "blocked": blocked,
            "conflicts": ctx.conflicts,
            # Só com ROTEIRO.md do get-brolls fora de sincronia: a escada manda para o sync.
            **({"roteiro_sync": True} if ctx.awaiting_sync else {}),
        },
        "review_page": (root / "review.html").is_file(),
        "board_url": _live_board_url(args.project),
        "rights_mode": _rights_mode(ctx.rules),
        "candidate": _pending_candidate(items),
        "candidates": _step_candidates(items),
        "duration_unknown": len(_uninspected(items)),
        "inspect_candidate": next((c["id"] for c in _uninspected(items)), None),
        "reference_only": _reference_only(items),
        "preview_image": _preview_is_image(items),
    }


def _brief_full_report(ctx, args):
    """Resposta completa de `brief`: beats com comando pronto, cobertura e o próximo passo."""
    root = Path(args.project).expanduser().resolve() / "brolls"
    manifest = Ledger(args.project, recover=False).data if root.is_dir() else {"items": []}
    coverage = _brief_coverage(ctx, args, manifest)
    listed, covered, missing, blocked = (
        coverage["listed"],
        coverage["covered"],
        coverage["missing"],
        coverage["blocked"],
    )
    data = ctx.data
    state = _brief_next_action_state(ctx, args, root, manifest, coverage)
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


def library_command(args):
    """`learn` e `library`: memória editorial entre projetos, sem direito junto."""
    from getbrolls import library

    if args.command == "library":
        if not 1 <= args.limit <= 20:  # noqa: PLR2004 - matches the "--limit entre 1 e 20" message below
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


def _approve_all_skip_reason(c, rules):
    """Por que este candidato NÃO entra no `--all`/lote, ou None se pode ser aprovado."""
    from .rules import allowed

    checks = (
        (_plugin_nothing_seen(c), "fonte de plugin sem nada para mostrar (sem prévia, poster_url nem embed_url)"),
        (not _has_preview(c), "sem prévia gerada; rode preview antes"),
        (not allowed(c, rules), "bloqueado pelas regras atuais do usuário"),
        (_stage_status(c, "approval") == "rejected", "rejeitado por decisão humana"),
        (
            _stage_status(c, "approval") == "approved" and c["approval"].get("signature") == signature(c),
            "já tem aprovação válida para este intervalo",
        ),
        (
            c["segment"]["start_s"] is None and c.get("media", {}).get("kind") != "image",
            "sem intervalo escolhido; rode preview --start/--end",
        ),
    )
    for skip, reason in checks:
        if skip:
            return reason
    return None


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
    exatamente esses. Sem `only` (`--all`), o alvo é todo item com prévia e sem
    aprovação válida — e um id desconhecido é erro, nunca silêncio.
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
        reason = _approve_all_skip_reason(c, rules)
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
        "ROTEIRO.md e rode `roteiro --action sync --project ...`; `status --project ...` "
        "mostra os beats ativos."
    )


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
        # Só com ROTEIRO.md do get-brolls: fora de sincronia, a escada manda para o sync;
        # em dia e sem beat ativo, o `status` diz que o roteiro não pede b-roll e segue.
        **_roteiro_flags(roteiro_sync_needed(project, beats), beats),
    }


def roteiro_sync_needed(project, beats):
    """None sem ROTEIRO.md do get-brolls; senão, se o brief ainda não reflete o roteiro. Só lê."""
    from getbrolls import roteiro, roteiro_sync

    if not roteiro.is_roteiro(project):
        return None
    return roteiro_sync.out_of_sync(project, [b["id"] for b in beats])


def _roteiro_flags(sync_needed, beats):
    if sync_needed:
        return {"roteiro_sync": True}
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
    )
    # `build_delivery` raises before returning when any file is in conflict, so
    # reaching here means zero conflicts this run.
    logs.event(
        _log,
        logging.INFO,
        "deliver",
        delivered=len(report["items"]),
        skipped=len(report["skipped"]),
        conflicts=0,
        dry_run=bool(dry_run),
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
    review_page = (ledger.root / "review.html").is_file()
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
    "Revise o ROTEIRO.md com a pessoa e rode `roteiro --action sync --project ...`: os beats "
    "nascem dele. Depois, `brief --project ...` mostra o comando pronto de cada beat."
)
ROTEIRO_DRIFT_PROBLEM = (
    "O ROTEIRO.md mudou desde o último sync: os beats do BRIEF.md não batem com as cenas. Revise o "
    "roteiro com a pessoa e rode `roteiro --action sync --project <projeto>`."
)
ROTEIRO_NO_BROLL = "o roteiro não pede b-roll"
ROTEIRO_NO_BROLL_NEXT = (
    "Nada para buscar: o roteiro não pede b-roll. Se a pessoa quiser, ponha uma cena [BROLL: ...] no "
    "ROTEIRO.md, revise com ela e rode `roteiro --action sync --project ...`."
)
ROTEIRO_SYNC_PROBLEM = (
    "O BRIEF.md ainda não tem beats: eles vêm do ROTEIRO.md. Revise o roteiro com a pessoa e "
    "rode `roteiro --action sync --project <projeto>`."
)


def _reference_only_step(do):
    return do.get("step") == "preview" and "--reference-only" in (do.get("command") or "")


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
    do = next_action(
        _flow_state(
            ledger,
            rules,
            counts=counts,
            format_pending=format_pending,
            brief=brief,
        )
    )
    # Veredito primeiro, como no doctor: o JSON completo continua logo abaixo.
    return {
        "line": line,
        "stages": [
            {"stage": plural, "count": counts[key], "items": ctx.listing[key]} for key, _, plural in STATUS_STAGES
        ],
        "next": (
            REFERENCE_ONLY_NEXT
            if _reference_only_step(do)
            else ROTEIRO_SYNC_NEXT
            if do.get("step") == "roteiro-sync"
            else status_next(
                counts,
                format_pending,
                pending_preview=len(_needs_preview(ctx.items)),
                undelivered=len(_undelivered(ctx.items, _retired_shots(ledger.root.parent))),
            )
        ),
        # Aditivo: `line/stages/next` seguem iguais; `do` traz o mesmo passo já em
        # comando pronto e `brief` diz quantos beats ainda estão sem material.
        "do": do,
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
        "log": str(app_log) if app_log.is_file() else None,
        "items": _status_items(ctx),
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
    if root is not None:
        bin_dir = Path(root) / ".tools/node_modules/.bin"
    else:
        bin_dir = _paths.tools_dir().path / "node_modules/.bin"
    for name in ("playwright-cli.cmd", "playwright-cli"):
        if (bin_dir / name).is_file():
            return bin_dir / name
    return None


# --- Audit-trail logging helpers -------------------------------------------
# Pure, defensive readers used only to build fields for `logs.event()` calls.
# Each one swallows its own failure and returns a safe default instead of
# raising, so a logging call site can never change command behavior.


def _safe_size(path):
    """File size in bytes, or None when the file is missing/unreadable."""
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def _sha256_prefix(value):
    """First 12 chars of a sha256 hex digest, or None."""
    return value[:12] if isinstance(value, str) and value else None


def _rules_project_present(args):
    """Whether the target project already has a RULES.md, for the `config` event."""
    project = getattr(args, "project", None)
    if not project:
        return False
    try:
        return (Path(project).expanduser() / "RULES.md").is_file()
    except OSError:
        return False


def _brief_present(args):
    """Whether the target project already has a BRIEF.md, for the `config` event."""
    project = getattr(args, "project", None)
    if not project:
        return False
    try:
        from getbrolls.brief import brief_path

        return brief_path(project).is_file()
    except (ValueError, OSError):
        return False


def _provider_keys_set():
    """Comma list of which provider API keys are SET in the environment, never their values."""
    names = (("pexels", "PEXELS_API_KEY"), ("pixabay", "PIXABAY_API_KEY"), ("youtube", "YOUTUBE_API_KEY"))
    return ",".join(name for name, key in names if os.environ.get(key))


def _inspect_windows_source(windows):
    """Which kind of source data produced the candidate windows, for the `inspect` event."""
    sources = {(w or {}).get("source") for w in windows}
    if "subtitle" in sources:
        return "subtitles"
    if "chapter" in sources:
        return "chapters"
    if "description_timestamp" in sources:
        return "description"
    return "none"


def _validate_scan_flags(cmd, args):
    """`preview --scan` não combina com --start/--end nem --reference-only."""
    if not (cmd == "preview" and args.scan):
        return
    if args.start is not None or args.end is not None:
        raise ValueError(
            "--scan varre o vídeo inteiro: não combine com --start/--end. "
            "Escolha o intervalo depois, olhando a varredura."
        )
    if args.reference_only:
        raise ValueError("--scan precisa da mídia de trabalho; não use com --reference-only.")


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


def _fetch_local_source(c):
    """Confirma que o original local importado ainda bate com o hash registrado."""
    src = c["local_path"]
    if digest(src) != c["local_sha256"]:
        raise ValueError("Original local mudou: importe novamente e aprove a nova versão.")
    return src


def _fetch_routed_check_existing(ledger, c):
    """Recusa a rota de plugin se a revisão atual já foi coletada (sem gastar licença/cota)."""
    stem = id_stem(c["id"]) + f"-r{c['segment']['revision']}"
    if c.get("media", {}).get("kind") == "image":
        existing = sorted((ledger.root / "clips").glob(stem + ".*"))
        if existing:
            # Recusa antes de gastar licença/cota numa revisão já coletada, seja
            # qual for a extensão que a imagem coletada usou.
            raise ValueError(_already_collected("clips/" + existing[0].name))
        return
    planned = "clips/" + stem + ".mp4"
    if (ledger.root / planned).exists():
        # Recusa antes de gastar licença/cota numa revisão já coletada.
        raise ValueError(_already_collected(planned))


def _fetch_routed_record_license(c, routed, reacquire):
    """Grava consumo/reaquisição de licença da rota de plugin; devolve se algo mudou."""
    from .acquisition import license_evidence

    changed = False
    if reacquire and not routed.reused and c["acquisition"].get("route_consumed_at"):
        # Nova aquisição confirmada pela pessoa: a primeira data fica, e esta
        # entra na lista — o ledger mostra que a licença foi consumida de novo.
        c["acquisition"].setdefault("route_reacquired_at", []).append(now())
        logs.event(_log, logging.INFO, "route_reacquired", candidate=c["id"], plugin=routed.plugin)
        changed = True
    if routed.license:
        # Evidência a mais, gravada depois do permit humano — nunca no lugar dele.
        evidence = license_evidence(routed.plugin, routed.license)
        if evidence not in c["rights"]["evidence"]:
            c["rights"]["evidence"].append(evidence)
            changed = True
    if not c["acquisition"].get("route_consumed_at"):
        c["acquisition"]["route_consumed_at"] = now()
        changed = True
    return changed


def _fetch_routed_validate_format(c, routed):
    """Confere formato de imagem, ou duração mínima do vídeo, entregues pela rota de plugin."""
    if c.get("media", {}).get("kind") == "image":
        # O formato só é conferido DEPOIS que cache e licença já estão
        # gravados (acima) — uma imagem de formato não reconhecido é recusada sem
        # custar a rota (e a licença) de novo a cada retry; o cache já existe.
        # A extensão vem do conteúdo, como na foto das fontes embutidas
        # (`media.image_suffix`), nunca do nome que o plugin deu ao arquivo.
        from .media import SNIFFED_IMAGE_SUFFIXES, image_suffix

        try:
            image_suffix(routed.path)
        except ValueError:
            from .http import ProviderError

            raise ProviderError(
                f"Plugin {routed.plugin}: a rota devolveu uma imagem de formato não "
                f"reconhecido; entregue {', '.join(SNIFFED_IMAGE_SUFFIXES)}."
            ) from None
        return
    if routed.duration_s is not None and c["segment"]["end_s"] > routed.duration_s + 0.1:
        raise ValueError(
            f"A fonte entregou {routed.duration_s:g} s, mas o trecho aprovado vai até "
            f"{c['segment']['end_s']:g} s: a duração real é menor. Gere a prévia com um "
            "intervalo dentro dela (preview --reference-only), aprove e rode fetch de novo; "
            "o arquivo já baixado é reaproveitado."
        )


def _fetch_routed_source(args, ledger, c):
    """Fonte de plugin: cache privado reaproveitável, licença registrada e formato conferido."""
    from .acquisition import fetch_routed_source

    # Rota de plugin: roda só aqui, depois de aprovação + permit (require_fetch antes
    # de chamar). O plugin traz o arquivo; corte, hash e ledger seguem com o core.
    _fetch_routed_check_existing(ledger, c)
    # O arquivo da rota vai para o cache privado (fora de `brolls/`) e é
    # reaproveitado se este `fetch` falhar adiante: a licença é consumida uma vez.
    reacquire = bool(getattr(args, "reacquire", False))
    routed = fetch_routed_source(ledger, c, reacquire=reacquire)
    if _fetch_routed_record_license(c, routed, reacquire):
        # Grava já, antes do corte: se o corte falhar, a licença consumida e o
        # marcador ficam no ledger (a rota não roda de novo no próximo fetch).
        ledger.save("fetch-route", c)
    _fetch_routed_validate_format(c, routed)
    return routed.path


def _fetch_downloaded_source(ledger, c):
    """Sem original local nem rota de plugin: baixa direto do provedor para um arquivo temporário."""
    from getbrolls import providers
    from getbrolls.http import download_rendition

    # Re-resolve from the provider to refresh temporary variant URLs.
    fresh = providers.refresh(c)
    url = fresh.get("media_url")
    if not url:
        raise ValueError(
            "Esta fonte não disponibilizou arquivo por transporte permitido; "
            f"execute antes: preview --candidate {candidate_arg(c)} --start ... --end ..."
        )
    temp = ledger.root / "previews" / ("download-" + id_stem(c["id"]) + ".part")
    download_rendition(fresh, temp)
    return temp


def _fetch_source(args, ledger, c):
    """Resolve de onde vem o arquivo do `fetch`: local, rota de plugin, ou download direto.

    Devolve {src, temp, routed_remote}. `temp` é o arquivo temporário a apagar depois
    (só no caso de download); `routed_remote` marca a rota de plugin para o log final.
    """
    from .acquisition import route_name

    if c.get("local_path"):
        return {"src": _fetch_local_source(c), "temp": None, "routed_remote": False}
    if route_name(c) is not None:
        return {"src": _fetch_routed_source(args, ledger, c), "temp": None, "routed_remote": True}
    temp = _fetch_downloaded_source(ledger, c)
    return {"src": temp, "temp": temp, "routed_remote": False}


def _fetch_image_output(ledger, c, source):
    """Copia a imagem baixada/roteada/local para clips/, com extensão pelo conteúdo real."""
    from .media import IMAGE_SUFFIXES, copy_image, image_suffix

    src, temp = source["src"], source["temp"]
    # O download chega como `.part` (e cópia antiga do cache como `.mp4`), e a
    # rota de plugin nomeia o arquivo como quiser: a extensão de `clips/` — e,
    # dela, a de `entrega/` — sai do conteúdo real, só de formato conhecido; o
    # resto é recusado, nunca herda a da URL nem a do nome. Só o original
    # local importado pela pessoa, já com extensão de imagem, fica como está.
    suffix = Path(src).suffix.lower()
    try:
        if c["provider"] != "local" or suffix not in IMAGE_SUFFIXES:
            suffix = image_suffix(src)
        rel = "clips/" + id_stem(c["id"]) + f"-r{c['segment']['revision']}" + suffix
        dest = ledger.root / rel
        if dest.exists():
            raise ValueError(_already_collected(rel))
        copy_image(src, dest)
    finally:
        if temp:
            temp.unlink(missing_ok=True)
    c["output"] = {"path": rel, "sha256": digest(dest), "verified": True}
    c["state"] = "verified"
    return dest


def _fetch_finish_image(ledger, c, source, fetch_started_at):
    """Grava e loga o `fetch` de imagem; devolve o candidato (retorno antecipado do pipeline)."""
    dest = _fetch_image_output(ledger, c, source)
    ledger.save("fetch", c)
    render(ledger)
    logs.event(
        _log,
        logging.INFO,
        "fetch",
        candidate=c["id"],
        kind="remote" if c["provider"] != "local" else "local",
        bytes=_safe_size(dest),
        sha256_prefix=_sha256_prefix(c["output"]["sha256"]),
        ms=round((time.monotonic() - fetch_started_at) * 1000),
    )
    return c


def _fetch_video_output(ledger, c, source):
    """Corta o vídeo para clips/, recusando sobrescrever uma revisão já coletada."""
    src, temp = source["src"], source["temp"]
    rel = "clips/" + id_stem(c["id"]) + f"-r{c['segment']['revision']}.mp4"
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


def _fetch_finish_video(ledger, c, source, fetch_started_at):
    """Corta, grava e loga o `fetch` de vídeo; devolve o candidato."""
    _fetch_video_output(ledger, c, source)
    ledger.save("fetch", c)
    render(ledger)
    with contextlib.suppress(Exception):  # logging must never break a command
        logs.event(
            _log,
            logging.INFO,
            "fetch",
            candidate=c["id"],
            kind="remote" if source["temp"] or source["routed_remote"] else "local",
            bytes=_safe_size(ledger.root / c["output"]["path"]) if c["output"].get("path") else None,
            sha256_prefix=_sha256_prefix(c["output"].get("sha256")),
            ms=round((time.monotonic() - fetch_started_at) * 1000),
        )
    return c


def _apply_fetch(args, ledger, c):
    """Aplica `fetch` por completo (rota local/plugin/download, corte, hash) e devolve o candidato salvo."""
    fetch_started_at = time.monotonic()
    require_fetch(c)
    source = _fetch_source(args, ledger, c)
    if c.get("media", {}).get("kind") == "image":
        return _fetch_finish_image(ledger, c, source, fetch_started_at)
    return _fetch_finish_video(ledger, c, source, fetch_started_at)


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
        _refuse_blind_plugin_approval(c)
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


def _doctor_plugin_inventory(result, summary):
    """Sobrepõe status/reason reais do registro no inventário de plugins do `doctor`."""
    from getbrolls.sdk import loader as sdk_loader
    from getbrolls.sdk.registry import get_registry

    try:
        installed = sdk_loader.inventory()
    except ValueError as exc:
        result["plugins_error"] = str(exc)
        return
    if not installed:
        return
    # `loader.inventory()` só lê manifesto e pin (pré-carga: nunca roda
    # código). `get_registry().plugins` reflete o carregamento de verdade
    # (já rodou, porque `providers.capabilities()` acima monta o registro
    # antes) — sobrepomos status/reason por id, sem perder nenhuma pasta
    # que o inventário viu.
    loaded = get_registry().plugins
    result["plugins"] = [
        {**row, "status": loaded[row["id"]]["status"], "reason": loaded[row["id"]]["reason"]}
        if row["id"] in loaded
        else row
        for row in installed
    ]
    problems = doctor_plugin_problems(result["plugins"])
    if problems:
        summary["plugins"] = problems


def _doctor_executables(overrides, social):
    """Presença de cada executável sondado: pin, `PATH` ou o runtime da instalação."""
    from getbrolls.config import TOOL_PATH_KEYS

    executables = {
        name: bool(overrides.get(TOOL_PATH_KEYS.get(name) or "") or shutil.which(name)) for name in PROBED_EXECUTABLES
    }
    executables["yt-dlp"] = social["installed"]
    executables["playwright-cli"] = bool(_local_playwright() or shutil.which("playwright-cli"))
    return executables


def _doctor_report(config, providers_result, live, env_flag=None):
    """Monta o relatório completo de `doctor`: executáveis, engine social, plugins e instalação."""
    overrides, pin_problems, social, executables = readiness_probe()
    install = _paths.install_report(env_flag)
    summary = doctor_summary(executables, pin_problems, install.get("data_missing") or ())
    sheet = doctor_contact_sheet(executables.get("ffmpeg"))
    summary["optional"] += sheet["optional"]
    result = {
        # Veredito primeiro: o JSON continua completo logo abaixo dele. `ready` decide a
        # saída (4 quando falta algo obrigatório; ver `cli.result_exit`).
        "summary": summary,
        "ready": not summary["missing"],
        "contact_sheet": sheet["status"],
        "get_brolls": __version__,
        "preview": config,
        "python": sys.version.split()[0],
        "tool_paths": overrides,
        "executables": executables,
        "resolved": doctor_resolved(overrides),
        "providers": providers_result,
        "social": social,
        "install": install,
    }
    _doctor_plugin_inventory(result, summary)
    if live:
        from getbrolls.health import live_checks

        result["live"] = live_checks()
    return result


def _execute_providers_or_doctor(args, config):
    """`providers`/`doctor`: capacidades das fontes e, para `doctor`, o diagnóstico completo."""
    from getbrolls import providers

    result = providers.capabilities()
    if args.command != "doctor":
        return result
    return _doctor_report(config, result, args.live, getattr(args, "env_file", None))


def _execute_toolchain(args):
    """`plugins`/`x` vão ao SDK, `setup` ao `bootstrap`, `capabilities` ao manifesto; nenhum toca projeto."""
    if args.command == "capabilities":
        from getbrolls import capabilities
        from getbrolls.cli import build_parser

        return capabilities.describe(build_parser())
    if args.command == "setup":
        from getbrolls import bootstrap

        return bootstrap.run(args)
    if args.command == "plugins":
        from getbrolls.sdk import cli as sdk_cli

        return sdk_cli.run(args)
    from getbrolls.sdk import plugin_commands

    return plugin_commands.run(args)


def _execute_serve(args):
    """`serve`: sobe/derruba o servidor local do Storyboard em segundo plano, ou roda em primeiro plano."""
    from getbrolls import serve as serve_module

    port = getattr(args, "port", None) or serve_module.DEFAULT_PORT
    if getattr(args, "stop", False):
        return serve_module.stop(args.project)
    if getattr(args, "background", False):
        return serve_module.start_background(args.project, port)
    return serve_module.run(args.project, port)


def _execute_init_rules(args):
    """`init-rules`: cria RULES.md a partir do template, ou regrava só o bloco JSON com --force."""
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
        raise ValueError("--force só regrava o bloco JSON: informe --mode, --responsible, --declaration ou --format.")
    template = _paths.data_path("docs", "RULES.md")
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


def _execute_status_or_queue(cmd, args):
    """`status`/`queue`: leem RULES.md se conseguirem, mas nunca travam nele."""
    from getbrolls.rules import load_rules

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
    rules = None
    try:
        rules = load_rules(args.project)
    except (ValueError, OSError) as exc:
        record_warning("RULES_UNAVAILABLE", f"RULES.md ignorado para o ritmo: {exc}")
    return queue_execute(args, rules)


def _execute_init_brief(args):
    """`init-brief`: cria BRIEF.md a partir do template, recusando sobrescrever um existente."""
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
    shutil.copyfile(_paths.data_path("docs", "BRIEF.md"), dest)
    return {"brief": str(dest)}


def _execute_roteiro_or_assets(args):
    """`roteiro`/`assets`: organização de conteúdo, sem portão de formato — o roteiro confere o
    aspecto contra RULES.md/BRIEF.md por conta própria."""
    from getbrolls import roteiro_commands

    return roteiro_commands.run(args)


def _execute_export(args):
    """`export`: delega ao exporter escolhido; sem portão de formato, sem `sync_formats`, sem
    recuperar o manifesto (os portões recusam gravação pendente antes disso)."""
    from getbrolls import export

    return export.run(args)


# Comandos administrativos que não tocam em `rules`/`ledger` do vídeo. `learn`/`library` são a
# biblioteca pessoal, que vive fora do projeto e não depende das regras dele.
_ADMIN_COMMAND_HANDLERS = {
    "init-rules": _execute_init_rules,
    "init-brief": _execute_init_brief,
    # Somente leitura, como status: orienta a coleta sem criar nada no projeto.
    "brief": brief_report,
    "learn": library_command,
    "library": library_command,
    "roteiro": _execute_roteiro_or_assets,
    "assets": _execute_roteiro_or_assets,
    "export": _execute_export,
}


def _execute_config_free_command(cmd, args):
    """Comandos que não dependem de `rules`/`ledger`: status, queue e os administrativos."""
    if cmd in ("status", "queue"):
        return _execute_status_or_queue(cmd, args)
    handler = _ADMIN_COMMAND_HANDLERS.get(cmd)
    return handler(args) if handler else None


def _execute_read_only_project_command(cmd, args, config, rules, ledger):
    """Consultas e `deliver`: já com `rules`/`ledger` prontos, mas sem tocar num candidato só."""
    if cmd == "deliver":
        return deliver_report(ledger, rules, getattr(args, "dry_run", False))
    if cmd == "browser-plan":
        from getbrolls.browser import plan

        return plan(ledger, args.url, rules)
    if cmd == "references":
        path = ledger.root / "references.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}
    if cmd == "inspect":
        return inspect_source(ledger, args, config)
    return None


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


def _search_keep_candidates(ctx, candidates):
    """Filtra/decora os candidatos de uma fonte; devolve (itens mantidos, excluídos, registrados)."""
    from getbrolls.rules import allowed, format_report

    args, rules, ledger = ctx.args, ctx.rules, ctx.ledger
    kept_items = []
    excluded = 0
    registered = 0
    for c in candidates:
        if not allowed(c, rules):
            excluded += 1
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
        added = ledger.add(c)
        ledger.save("search", added)
        kept_items.append(added)
        registered += 1
    return kept_items, excluded, registered


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
        library.learn_query(query, name, "miss", note=str(exc), auto=True)
    except (ValueError, OSError) as failure:
        record_warning("LIBRARY_WRITE_FAILED", str(failure))


def _search_one_provider(ctx, name, query, so_far, retry):
    """Busca numa fonte só; devolve (itens mantidos, erro-ou-None, quantos ficaram registrados)."""
    from getbrolls import providers

    args = ctx.args
    try:
        candidates = providers.search(name, query, args.limit - so_far, media=getattr(args, "media", "any"))
    except ValueError as exc:
        _search_record_provider_failure(ctx, name, query, exc)
        return [], {"provider": name, "error": str(exc)}, 0, 0
    # ledger.add/save stay outside the provider try: a disk/write error here is not the
    # provider's fault and must not be attributed to it as a search failure.
    kept_items, kept_excluded, registered = _search_keep_candidates(ctx, candidates)
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
    return kept_items, None, registered, kept_excluded


def _search_sweep(ctx, query, retry=False):
    """Uma varredura pelos provedores escolhidos, com esta query exata."""
    args = ctx.args
    items, errors, excluded = [], [], 0
    answered = []
    for name in ctx.names:
        if len(items) >= args.limit:
            break
        kept_items, error, registered, kept_excluded = _search_one_provider(ctx, name, query, len(items), retry)
        items.extend(kept_items)
        excluded += kept_excluded
        if error is not None:
            errors.append(error)
            continue
        answered.append((name, query, registered))
    return items, errors, excluded, answered


def _search_with_shortening(ctx):
    """Varre com a query pedida; se vier vazia e for longa, tenta de novo encurtada uma vez."""
    args = ctx.args
    query_used = args.query
    items, errors, excluded, answered = _search_sweep(ctx, query_used)
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
        items, errors, excluded, retry_answered = _search_sweep(ctx, short, retry=True)
        answered = answered + retry_answered
        retry = {
            "from": args.query,
            "to": short,
            "note": (
                f'A busca por "{args.query}" não trouxe nada, então repeti uma vez '
                f'com as {SEARCH_QUERY_TOKENS} primeiras palavras ("{short}"): '
                "banco e YouTube casam por palavra, não por frase inteira."
            ),
        }
        query_used = short
    return {
        "query_used": query_used,
        "items": items,
        "errors": errors,
        "excluded": excluded,
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
    found = library.hints(args.query)
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
        if not auxiliary.is_file():
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


def _verify_failure_result(exc, existed):
    """Classifica a falha de verificação (missing/mismatch/undecodable) para o log."""
    if not existed:
        return "missing"
    if "Arquivo alterado após coleta" in str(exc):
        return "mismatch"
    return "undecodable"


def _verify_clip_failure(ledger, c, exc, existed):
    """Reage a uma falha de verificação: derruba `verified` (se estava true) e loga."""
    # Um clipe que não bate mais com o registrado, ou que não decodifica,
    # não pode continuar marcado como verificado: quem entrega depois
    # confiaria num hash velho. `sha256` fica como está — é a prova do
    # que foi coletado — só `verified` cai (e o estado volta a
    # `approved`), e uma nova `verify` bem-sucedida volta a marcar.
    if c["output"]["verified"]:
        c["output"]["verified"] = False
        if c["state"] == "verified":
            c["state"] = "approved"
        ledger.save("verify", c)
    with contextlib.suppress(Exception):  # logging must never break a command
        logs.event(_log, logging.WARNING, "verify", candidate=c["id"], result=_verify_failure_result(exc, existed))


def _verify_clip_success(ledger, c, info):
    """Registra a verificação bem-sucedida e devolve a entrada de `checked`."""
    # Probe, hash e decodificação bateram: se uma verificação anterior tinha
    # derrubado a flag (arquivo trocado e depois restaurado), volta a True.
    if not c["output"]["verified"]:
        c["output"]["verified"] = True
        if c["state"] == "approved":
            c["state"] = "verified"
        ledger.save("verify", c)
    logs.event(_log, logging.INFO, "verify", candidate=c["id"], result="ok")
    is_hd = min(info["width"], info["height"]) >= 1080  # noqa: PLR2004 - short side of 1080p, the usual floor for "HD"
    return {"id": c["id"], "media": info, "hd": is_hd}


def _verify_one_clip(ledger, c):
    """Reconfere um clipe coletado: devolve (entrada p/ `checked`, exceção de falha) — um dos dois é None."""
    if not c["output"]["path"]:
        return None, None
    path = ledger.root / c["output"]["path"]
    existed = path.exists()
    try:
        info = probe(path)
        if digest(path) != c["output"]["sha256"]:
            raise ValueError("Arquivo alterado após coleta: " + c["id"])
        run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
    except ValueError as exc:
        _verify_clip_failure(ledger, c, exc, existed)
        return None, exc
    return _verify_clip_success(ledger, c, info), None


def _execute_verify(args, rules, ledger):
    """`verify`: reconfere hash/decodificação de cada clipe coletado e tenta refazer entrega/."""
    checked = []
    # Every collected clip is checked even after one fails: stopping at the first
    # would leave a second altered clip marked as verified, and `deliver` would
    # ship it. The first failure is raised once the whole list has been flagged.
    first_failure = None
    for c in ledger.data["items"]:
        entry, failure = _verify_one_clip(ledger, c)
        if entry is not None:
            checked.append(entry)
        if failure is not None and first_failure is None:
            first_failure = failure
    if first_failure is not None:
        raise first_failure
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


def _execute_review_command(cmd, args, rules, ledger):
    """`import-review`/`review`/`verify`: publica ou confere a página, sem tocar num candidato só."""
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
        return _execute_verify(args, rules, ledger)
    return None


def _execute_project_command(cmd, args, config, rules, ledger):
    """Comandos que já têm `rules`/`ledger` prontos e passaram pelo portão de formato."""
    read_only = _execute_read_only_project_command(cmd, args, config, rules, ledger)
    if read_only is not None:
        return read_only
    if cmd == "search":
        return _execute_search(args, rules, ledger)
    if cmd == "resolve":
        return _execute_resolve(args, rules, ledger)
    review_result = _execute_review_command(cmd, args, rules, ledger)
    if review_result is not None:
        return review_result
    return _execute_candidate_command(cmd, args, config, rules, ledger)


def execute(args):
    """Prepara ambiente/config e despacha o comando para o handler certo, em ordem de dependência crescente."""
    from getbrolls import _paths
    from getbrolls.config import load_env_choice, settings

    # `UsageError` quando `--env-file` ou `GB_ENV_FILE` apontam para um arquivo que não existe.
    choice = _paths.env_file(getattr(args, "env_file", None))
    load_env_choice(choice, warn=True)
    config = settings()
    with contextlib.suppress(Exception):
        logs.event(
            _log,
            logging.DEBUG,
            "config",
            env_file_present=bool(getattr(args, "env_file", None)),
            env_file_source=choice.source,
            rules_project=_rules_project_present(args),
            brief_present=_brief_present(args),
            provider_keys=_provider_keys_set(),
        )
    if args.command in ("plugins", "x", "setup", "capabilities"):
        return _execute_toolchain(args)
    if args.command in ("providers", "doctor"):
        return _execute_providers_or_doctor(args, config)
    cmd = args.command
    if cmd == "serve":
        return _execute_serve(args)
    config_free = _execute_config_free_command(cmd, args)
    if config_free is not None:
        return config_free
    from getbrolls.rules import load_rules, sync_formats

    rules = load_rules(args.project)
    if cmd == "rules":
        return rules
    ledger = Ledger(args.project)
    # Consultas (`references`, `inspect`) não decidem nada sobre formato: como o
    # `status`, elas nunca podem ser barradas pelo portão de `--confirm-format-change`.
    # `deliver --dry-run` é ensaio: não pode reescrever o manifesto nem por tabela.
    if cmd not in READ_ONLY_CONSULTS and not (cmd == "deliver" and getattr(args, "dry_run", False)):
        sync_formats(ledger, rules, confirm=getattr(args, "confirm_format_change", False))
    return _execute_project_command(cmd, args, config, rules, ledger)


def reference_poster(ledger, c):
    """Materializa o cartaz estático da referência: miniatura da fonte, ou 1º quadro local.

    Não baixa o vídeo e não escolhe intervalo: só garante que exista um arquivo de
    imagem para a pessoa olhar (e para `status` contar como prévia). Falhar aqui é
    aceitável — vira aviso, não erro, porque a referência continua válida sem imagem.
    """
    from .media import image_preview

    if c["preview"].get("poster_path"):
        return c["preview"]["poster_path"]
    stem = id_stem(c["id"]) + "-ref"
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


# Páginas em que o yt-dlp lê título, canal e duração sem baixar mídia. Instagram fica
# de fora: a rota dele é o navegador, e um pedido solto ali só gasta bloqueio.
METADATA_PROVIDERS = ("youtube", "tiktok")


def fill_remote_metadata(c):
    """Preenche título, autoria e duração na hora do `resolve`, com um pedido só.

    Sem isto o candidato entrava com `title: "TikTok · 7312…"`, `creator: null` e
    `duration_s: null`, e o C2 — "título, canal, duração" — não tinha o que listar.
    Nada aqui é obrigatório: a página pode recusar, e a URL continua registrada.
    """
    if c.get("provider") not in METADATA_PROVIDERS or not c.get("source_url"):
        return c
    from .social import metadata

    try:
        found = metadata(c["source_url"])
    except (ValueError, OSError):
        return c
    if found.get("title"):
        c["title"] = found["title"]
    # O handle público é o que a pessoa reconhece; o nome de exibição vem junto.
    name = found.get("creator") or found.get("handle")
    if name:
        c["creator"]["name"] = name
    if found.get("handle"):
        c["creator"]["handle"] = found["handle"]
    if found.get("creator_url"):
        c["creator"]["url"] = found["creator_url"]
    if found.get("duration_s"):
        c["media"]["duration_s"] = found["duration_s"]
    return c


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


# Fonte acima disto já é vídeo de evento inteiro/livestream: o trecho existe, mas
# achar onde ele está custa caro, e vale avisar antes de pedir mídia.
LONG_SOURCE_S = 1800

# Folga de ponto flutuante ao comparar tempos de mídia (start/end/duração) em segundos.
TIME_TOLERANCE_S = 0.1
# "360" solto no título ou nas tags do vídeo; `360p` e `1360` não contam.
_THREE_SIXTY = re.compile(r"(?<![0-9a-zA-Z])360(?![0-9a-zA-Z])", re.IGNORECASE)


def clamp_windows(windows, cap):
    """Encurta a janela sugerida até o teto de prévia: sugerir o que `preview` recusa é pior que sugerir menos.

    Mexe só no fim, e no lugar: a janela continua começando onde a fonte disse que o
    assunto começa, e o contrato de chaves de `candidate_windows` fica igual.
    """
    if not cap or cap <= 0:
        return windows
    for window in windows:
        if window["end_s"] - window["start_s"] > cap + CAP_EPSILON:
            window["end_s"] = round(window["start_s"] + cap, 3)
    return windows


LANGUAGE_NAMES = {"pt": "PT", "en": "EN", "es": "ES", "fr": "FR", "de": "DE", "it": "IT", "ja": "JA"}


def language_warning(probe, query):  # pylint: disable=redefined-outer-name
    # Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0).
    """Aviso de idioma: a frase da pessoa e a legenda da fonte não se falam."""
    from .inspecting import language_mismatch

    found = language_mismatch(probe, query)
    if not found:
        return None
    spoken, asked = found
    return (
        f"legenda em {LANGUAGE_NAMES.get(spoken, spoken.upper())}, sua --query está em "
        f"{LANGUAGE_NAMES.get(asked, asked.upper())}: traduza a fala ao idioma da fonte"
    )


def _byte_size(size):
    """MB com uma casa; abaixo de 0,1 MB, KB inteiro — "0.0 MB" não diz nada."""
    megabytes = size / (1024 * 1024)
    if megabytes >= 0.1:  # noqa: PLR2004 - 0,1 MB: abaixo disso "0.0 MB"
        return f"{megabytes:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def inspect_warnings(probe_data, query=None):
    """Avisos sobre a fonte em si — o que costuma virar retrabalho depois da prévia."""
    found = []
    language = language_warning(probe_data, query)
    if language:
        found.append(language)
    duration = probe_data.get("duration_s")
    if duration and float(duration) > LONG_SOURCE_S:
        found.append(f"fonte longa: {round(float(duration) / 60)} min")
    haystack = " ".join([str(probe_data.get("title") or ""), *(probe_data.get("tags") or [])])
    if _THREE_SIXTY.search(haystack):
        found.append("vídeo 360°")
    downloaded = probe_data.get("downloaded_bytes")
    if downloaded and probe_data.get("local_copy"):
        # Rota de plugin: o arquivo chegou pela rota (cópia local ou download do próprio
        # plugin), não por um download do core — "baixar ... (0.0 MB)" confundia.
        found.append(
            f"esta fonte não tem metadados públicos, então a análise usou a cópia local do arquivo "
            f"({_byte_size(downloaded)}), trazida pela rota {probe_data['local_copy']} para o cache privado"
        )
    elif downloaded:
        # Esta fonte não tem página de metadados: a análise só existe porque o arquivo
        # veio inteiro. Dizer o preço evita repetir a conta sem perceber.
        found.append(
            f"esta fonte não tem metadados públicos, então analisá-la exigiu baixar o "
            f"arquivo inteiro ({downloaded / (1024 * 1024):.1f} MB) para o cache privado"
        )
    return found


def inspect_source(ledger, args, config=None):
    """O que a fonte já conta sobre si, antes de escolher intervalo.

    Na rota normal (página que o yt-dlp lê) nada de mídia é pedido: só metadados e
    legenda. Numa fonte de arquivo direto (NASA, Commons, bancos), a duração que a
    própria fonte publicou vale e nada é baixado. Sem ela (vídeo da NASA), a duração
    só sai do arquivo, então o `inspect` **baixa o arquivo inteiro** uma vez para o
    cache privado — e diz isso, com o tamanho, no resumo e em `warnings[]`.

    Somente leitura sobre decisão e intervalo em qualquer rota: com `--candidate`, o
    único campo que passa a existir no projeto é `media.duration_s` — nada de
    aprovação, segmento, prévia ou arquivo em `clips/`.
    """
    from .acquisition import direct_media
    from .inspecting import candidate_windows
    from .social import probe_remote

    max_windows = args.max_windows
    if max_windows is not None and not 1 <= max_windows <= 20:  # noqa: PLR2004 - ints do msg abaixo
        raise ValueError("Use --max-windows entre 1 e 20.")
    c = None
    if args.candidate:
        c = ledger.get(args.candidate)
        if (c.get("media") or {}).get("kind") == "image":
            raise ValueError(
                "Imagem estática não tem duração nem trecho para analisar: gere a prévia "
                f"dela direto com `preview --candidate {candidate_arg(c)}`, sem `--start/--end`."
            )
        url = c.get("source_url")
        if not url and not direct_media(c):
            raise ValueError(
                "Este candidato não tem URL pública para analisar; use `inspect --url` "
                "ou importe o original local com `resolve --file`."
            )
        source = c
    else:
        url = args.url
        if not (url or "").strip():
            raise ValueError("--url não pode ser vazio: informe a URL pública da fonte.")
        # `probe_remote` resolveria a mesma URL logo abaixo: resolver aqui não custa
        # pedido a mais e revela a fonte de arquivo direto antes de chamar o yt-dlp.
        from getbrolls import providers

        source = providers.resolve(url)
        if (source.get("media") or {}).get("kind") == "image":
            raise ValueError(
                "Esta URL é uma imagem estática, sem trecho para analisar: registre com "
                "`resolve --url` e gere a prévia com `preview --candidate ID`, sem `--start/--end`."
            )
    if direct_media(source):
        # NASA, Commons e os bancos publicam o arquivo; `source_url` é a página do
        # item, e o yt-dlp responde "Unsupported URL" para ela. A duração sai do
        # ffprobe do próprio arquivo, e legenda não existe nessa rota.
        probe_data = probe_direct(ledger, source, url)
    else:
        probe_data = probe_remote(url, cache=ledger.root.parent / ".getbrolls-sources")
    cap = float((config or {}).get("max_seconds") or 0)
    windows = clamp_windows(candidate_windows(probe_data, args.query, args.max_windows or 3), cap)
    if c is not None and probe_data["duration_s"]:
        # Único efeito no projeto: agora `set_segment` sabe recusar o que não cabe.
        c["media"]["duration_s"] = probe_data["duration_s"]
        ledger.save("inspect", c)
    logs.event(
        _log,
        logging.INFO,
        "inspect",
        candidate=c["id"] if c is not None else None,
        windows=len(windows),
        source=_inspect_windows_source(windows),
    )
    return {
        # Veredito primeiro, como nos outros comandos: quantas janelas e qual a melhor.
        "summary": inspect_summary(windows, probe_data, cap, args.query),
        "warnings": inspect_warnings(probe_data, args.query),
        "candidate": c["id"] if c is not None else None,
        "url": url,
        "title": probe_data.get("title"),
        "duration_s": probe_data["duration_s"],
        "chapters": probe_data["chapters"],
        "subtitle_langs": probe_data["subtitle_langs"],
        "subtitle_langs_total": probe_data.get("subtitle_langs_total", len(probe_data["subtitle_langs"])),
        "candidate_windows": windows,
    }


def probe_direct(ledger, source, url=None, stage="inspect"):
    """O mesmo contrato de `social.probe_remote`, lido do arquivo direto da fonte.

    Sem capítulo e sem legenda: um mp4 servido por URL não traz nenhum dos dois. O
    que ele traz é a duração real, que é o que separa a janela do palpite. Quando a
    fonte já publicou a duração (Commons, bancos), ela vale e nada é baixado; só a
    fonte sem esse metadado (vídeo da NASA) custa o download do arquivo inteiro.
    """
    from .acquisition import cache_direct_media, route_name

    known = (source.get("media") or {}).get("duration_s")
    # Rota de plugin segue pelo `cache_direct_media`, que recusa a rota de `fetch` e diz
    # o estágio: o atalho da duração publicada vale só para as fontes embutidas.
    if route_name(source) is None and isinstance(known, (int, float)) and not isinstance(known, bool) and known > 0:
        # A fonte já publicou a duração nos metadados (o Commons manda no `imageinfo`):
        # baixar o arquivo inteiro só para medir o que já se sabe custava dezenas de MB
        # antes de a pessoa confirmar qualquer coisa.
        return {
            "downloaded_bytes": 0,
            "local_copy": None,
            "url": url or source.get("source_url") or source.get("media_url"),
            "title": source.get("title"),
            "duration_s": float(known),
            "chapters": [],
            "subtitle_langs": [],
            "subtitle_langs_total": 0,
            "description": "",
            "tags": [],
            "subtitles": {},
        }
    path = cache_direct_media(ledger, source, stage=stage)
    info = probe(path)
    duration = info.get("duration_s")
    try:
        downloaded = Path(path).stat().st_size
    except OSError:
        downloaded = 0
    return {
        # Analisar esta fonte custou o arquivo inteiro; quem lê o resumo precisa saber.
        "downloaded_bytes": downloaded,
        # Rota de plugin: nome da rota que trouxe a cópia local (o aviso muda de frase).
        "local_copy": route_name(source),
        "url": url or source.get("source_url") or source.get("media_url"),
        "title": source.get("title"),
        "duration_s": float(duration) if duration else None,
        "chapters": [],
        "subtitle_langs": [],
        "subtitle_langs_total": 0,
        "description": "",
        "tags": [],
        "subtitles": {},
    }


def _clock(seconds):
    total = round(float(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


def _has_cues(probe):  # pylint: disable=redefined-outer-name
    # Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0).
    """Alguma legenda chegou de fato, com falas dentro? Anunciar idioma não é ter legenda."""
    return any((entry or {}).get("cues") for entry in (probe.get("subtitles") or {}).values())


def inspect_summary(windows, probe, cap=0.0, query=None):  # pylint: disable=redefined-outer-name
    # Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0).
    """`{line, next}` em PT-BR: quantas janelas saíram, qual a melhor e o que fazer com ela."""
    # Sem isto, "nenhuma janela casou" parece resposta sobre o conteúdo da fonte
    # quando o que houve foi a frase e a legenda estarem em idiomas diferentes.
    mismatch = language_warning(probe, query)
    if not windows:
        return {
            "line": "A fonte não deu capítulo, legenda nem marcação de tempo: não tenho por onde começar."
            + (f" Atenção: {mismatch}." if mismatch else ""),
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
    if cap:
        # A janela já sai cortada no teto; dizer o teto evita pedir um intervalo que
        # o `preview` recusaria logo depois.
        line += f" A prévia aceita no máximo {cap:g} s por vez (GB_PREVIEW_MAX_SECONDS)."
    for warning in inspect_warnings(probe, query):
        line += f" Atenção — {warning}."
    return {
        "line": line,
        "next": (
            f"Confirme olhando: `preview --start {best['start_s']} --end {best['end_s']}`"
            if found
            else "Confirme antes de baixar: `preview --scan` mostra a grade do vídeo "
            f"inteiro, ou gere a prévia de {where} e olhe o contact sheet."
        ),
    }


def _scan_note(start, end, duration, ceiling, has_segment=False):
    """Frase em PT-BR dizendo que trecho da fonte entrou na grade, e por quê.

    `start`/`end` são tempo da fonte, os mesmos números dos rótulos: dizer "os
    primeiros N s" quando a mídia de trabalho começa no minuto 2 mandaria a pessoa
    procurar no lugar errado.
    """
    span = end - start
    ignored = (
        " A varredura ignora o intervalo já escolhido neste candidato: ela é "
        "exploratória, não define nem invalida segmento."
        if has_segment
        else ""
    )
    if end >= duration - TIME_TOLERANCE_S and start <= TIME_TOLERANCE_S:
        return f"Baixei e varri o vídeo inteiro ({span:.0f} s) para montar a grade." + ignored
    if span >= float(ceiling) - 0.1 and duration > float(ceiling):
        return (
            f"Varri de {_clock(start)} a {_clock(end)} ({span:.0f} s) de um vídeo de "
            f"{duration:.0f} s: o teto GB_SCAN_MAX_SECONDS está em {int(ceiling)} s. Para "
            "ver o resto, aumente o teto ou varra o candidato de novo depois de escolher "
            "um trecho." + ignored
        )
    return (
        f"Varri de {_clock(start)} a {_clock(end)} ({span:.0f} s) de um vídeo de "
        f"{duration:.0f} s: a mídia de trabalho que tenho aqui não cobre o resto. Os "
        "rótulos da grade são tempo da fonte, não do arquivo baixado." + ignored
    )


def _scan_known_duration(ledger, c):
    """Duração conhecida do candidato, consultando a fonte se ela ainda não tiver sido inspecionada."""
    duration = c["media"].get("duration_s")
    if duration or c["provider"] == "local":
        return duration
    from .acquisition import direct_media

    if direct_media(c):
        # Fonte de arquivo direto: o yt-dlp não lê a página dela, mas o ffprobe lê
        # o arquivo — e é o mesmo arquivo que a varredura vai usar logo em seguida.
        probe_data = probe_direct(ledger, c, stage="scan")
    else:
        from .social import probe_remote

        probe_data = probe_remote(c["source_url"], cache=ledger.root.parent / ".getbrolls-sources")
    duration = probe_data["duration_s"]
    if duration:
        c["media"]["duration_s"] = duration
    return duration


def _scan_prepare_span(ledger, c, config, duration):
    """Garante a mídia de trabalho e devolve {source, offset, local_start, span, duration}."""
    # O teto vale sobre a duração real: pedir 900 s de um vídeo de 126 s faz a fonte
    # devolver menos do que o pedido, e a grade sairia rotulada com tempos que não existem.
    span = min(duration, float(config["scan_max_seconds"]))
    if c["provider"] != "local":
        from .acquisition import prepare_source

        # `tolerant`: varrer o vídeo inteiro não pode falhar porque a fonte entregou
        # alguns segundos a menos do que anunciou.
        prepare_source(ledger, c, 0, span, tolerant=True, stage="scan")
    source = c.get("local_path")
    if not source:
        raise ValueError("A varredura precisa da mídia de trabalho; esta fonte só permite referência estática.")
    offset = c.get("local_start_s", 0)
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
    return {"source": source, "offset": offset, "local_start": local_start, "span": span, "duration": duration}


def _scan_record(ledger, c, config, result, span_info):
    """Grava o resultado da varredura no candidato e loga o evento de preview."""
    offset, local_start, span, duration = (
        span_info["offset"],
        span_info["local_start"],
        span_info["span"],
        span_info["duration"],
    )
    # `scan` fica fora de `preview`/`segment`: varrer não decide nem invalida nada.
    # Os rótulos saem em tempo da fonte, e é esse mesmo trecho que a nota descreve.
    covered_start = float(offset) + local_start
    covered_end = covered_start + span
    capped = span < duration - 0.1
    c["scan"] = {
        **result,
        "capped": capped,
        "duration_s": duration,
        # Trecho da fonte que a grade cobre, no mesmo relógio de `frame_times_s`.
        "start_s": round(covered_start, 3),
        "end_s": round(covered_end, 3),
        # O que realmente entrou na grade, em segundos de mídia baixada.
        "downloaded_seconds": round(span, 3),
        "note": _scan_note(
            covered_start,
            covered_end,
            duration,
            config["scan_max_seconds"],
            has_segment=c["segment"]["start_s"] is not None,
        ),
    }
    ledger.save("preview", c)
    render(ledger)
    logs.event(
        _log,
        logging.INFO,
        "preview",
        candidate=c["id"],
        start_s=c["scan"]["start_s"],
        end_s=c["scan"]["end_s"],
        revision=c["segment"]["revision"],
        mode="scan",
    )


def scan_candidate(ledger, c, config):
    """Contact sheet de baixa resolução do vídeo inteiro; não escolhe intervalo nenhum."""
    from .media import scan_sheet

    if c.get("media", {}).get("kind") == "image":
        raise ValueError(
            "Imagem estática não tem o que varrer: gere a prévia dela com "
            f"`preview --candidate {candidate_arg(c)}`, sem `--start/--end`."
        )
    duration = _scan_known_duration(ledger, c)
    if not duration:
        raise ValueError("Duração desconhecida: rode `inspect --candidate " + candidate_arg(c) + "` antes de varrer.")
    span_info = _scan_prepare_span(ledger, c, config, float(duration))
    stem = id_stem(c["id"])
    result = scan_sheet(
        span_info["source"],
        ledger.root / "previews",
        stem,
        span_info["local_start"],
        span_info["span"],
        source_offset=span_info["offset"],
    )
    _scan_record(ledger, c, config, result, span_info)
    return {
        **c,
        # Mesmo contrato das outras rotas de `preview`: caminho absoluto de tudo o que
        # existe para olhar, e não só da varredura recém-gerada.
        "files": {**preview_files(ledger, c), "scan": str((ledger.root / result["scan_path"]).resolve())},
    }
