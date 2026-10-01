"""Subcomandos `roteiro` e `assets`: organização do conteúdo, fora do portão de formato.

`roteiro --action check|plan` e `assets` só leem (entram em `READ_ONLY_ACTIONS`);
`new`, `review` e `sync` gravam com a trava exclusiva de sempre. ROTEIRO.md pode ser
link (nota do Obsidian): quem grava escreve no arquivo de verdade e o link continua link.
"""

import shutil
import time
from pathlib import Path

from . import assets, layout, roteiro, roteiro_plan, roteiro_review, roteiro_sync
from .brief import brief_path, load_brief
from .export import _OS_REASONS_COMMON as _OS_REASONS
from .guidance import roteiro_check_step
from .ledger import atomic_write
from .rules import load_rules, seed_project_rules, video_format_unchosen

NEW_FOLDERS = ("aroll", *(f"assets/{name}" for name in layout.ASSET_FOLDERS))
BACKUP_SUFFIX = ".bak"


def skeleton(genero, tema):
    """Esqueleto do gênero: frontmatter + uma cena por etapa, com `{placeholders}` que o `check` aponta."""
    if genero not in roteiro.GENRES:
        raise ValueError(f'Gênero desconhecido "{genero}"; use: {", ".join(roteiro.GENRES)}.')
    if not (tema or "").strip() or any(ch in tema for ch in "\r\n"):
        raise ValueError("Informe --tema numa linha só.")
    spec = roteiro.GENRES[genero]
    safe = tema.strip().replace('"', '\\"')
    head = [
        "---", "type: roteiro", f"genero: {genero}", f'aspecto: "{spec["aspecto"]}"',
        f'tema: "{safe}"', "legenda: true", "status: draft", "---", "",
    ]  # fmt: skip
    body = []
    for title, directive, speech in spec["cenas"]:
        body += [f"## {title}", directive, speech, ""]
    return "\n".join(head + body)


def _backup_path(path):
    """`ROTEIRO.md.bak`; se ele já existe, `ROTEIRO.md.<AAAAmmdd-HHMMSS>.bak` — nunca sobrescreve cópia."""
    first = path.with_name(path.name + BACKUP_SUFFIX)
    if not first.exists() and not first.is_symlink():
        return first
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate, n = path.with_name(f"{path.name}.{stamp}{BACKUP_SUFFIX}"), 2
    while candidate.exists() or candidate.is_symlink():
        candidate, n = path.with_name(f"{path.name}.{stamp}-{n}{BACKUP_SUFFIX}"), n + 1
    return candidate


def _new(args):
    if not args.genero or not (args.tema or "").strip():
        raise ValueError("roteiro --action new precisa de --genero e --tema.")
    path = roteiro.roteiro_path(args.project)
    problem = roteiro.path_problem(path)
    if problem:
        # Link quebrado ou pasta: nem com --force (não há o que copiar para o .bak).
        raise ValueError(problem)
    if path.exists() and not args.force:
        raise ValueError("ROTEIRO.md já existe; edite o que está lá ou use --force para recomeçar do esqueleto.")
    text = skeleton(args.genero, args.tema)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = _backup_path(path)
        shutil.copyfile(path, backup)
    atomic_write(path.resolve(), text)
    root = Path(args.project).expanduser().resolve()
    for folder in NEW_FOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    video_format = roteiro.ASPECT_TO_FORMAT[roteiro.GENRES[args.genero]["aspecto"]]
    rules = seed_project_rules(root, video_format)
    line = "ROTEIRO.md criado: escreva a fala de cada cena e rode roteiro --action check."
    if rules:
        line += f' RULES.md criado em "{video_format}", o formato do roteiro.'
    if backup:
        line += f" O anterior ficou em {backup.name}."
    return {
        "roteiro": str(path),
        "backup": str(backup) if backup else None,
        "rules": str(rules) if rules else None,
        "folders": list(NEW_FOLDERS),
        "summary": {"line": line, "do": roteiro_check_step(args.project)},
    }


def _brief_for_check(project):
    """(dados do BRIEF.md, aviso) só para conferir o aspecto; o check nunca cai por causa do brief."""
    if not brief_path(project).is_file():
        return None, None
    try:
        data = load_brief(project)
    except ValueError as exc:
        return None, f"BRIEF.md não pôde ser lido para conferir o aspecto: {exc}"
    if not isinstance(data, dict):
        return None, "BRIEF.md não pôde ser lido para conferir o aspecto: o bloco json não é um objeto."
    return data, None


def _check(args):
    roteiro_sync.ensure_no_pending(args.project)
    doc = roteiro.parse(roteiro.load_text(args.project))
    plan = roteiro_plan.scene_plan(args.project, doc)
    brief_data, brief_warning = _brief_for_check(args.project)
    rules = load_rules(args.project)
    if video_format_unchosen(args.project, rules):
        # Ninguém escolheu formato (sem RULES.md no projeto): vale o aspecto do roteiro.
        rules = {**rules, "video_format": roteiro.ASPECT_TO_FORMAT[doc.meta["aspecto"]]}
    problems = plan["problems"] + roteiro_plan.aspect_problems(doc.meta, rules, brief_data)
    if problems:
        raise ValueError("ROTEIRO.md com problema:\n" + "\n".join(problems))
    warnings = plan["warnings"] + ([brief_warning] if brief_warning else [])
    review = roteiro_review.review_state(args.project, doc)
    line = f"{len(plan['scenes'])} cenas, ~{plan['total_s']} s, {len(warnings)} aviso(s); " + (
        "revisão válida." if review["reviewed"] else "falta a revisão da pessoa."
    )
    return {"meta": doc.meta, **plan, "warnings": warnings, "review": review, "summary": {"line": line}}


REVIEW_CHANGED = "O roteiro mudou desde a versão revisada; mostre de novo e revise."
EXPECT_MISSING = (
    "roteiro --action review exige --expect <sha256>: o review.sha256 que check e plan mostram, "
    "junto com o roteiro que a pessoa revisou."
)


def _review(args):
    expect = (getattr(args, "expect", None) or "").strip().lower()
    if not expect:
        raise ValueError(EXPECT_MISSING)
    path = roteiro.roteiro_path(args.project)
    # `load_text` tira BOM e CRLF: `set_status` e o hash da revisão trabalham sobre o mesmo texto.
    text = roteiro.load_text(args.project)
    doc = roteiro.parse(text)
    roteiro_review.check_review_args(args.by, args.channel, args.statement)
    if roteiro_review.review_hash(doc, args.project) != expect:
        raise ValueError(REVIEW_CHANGED)
    marked = roteiro_review.set_status(text, "revisado")
    # Compare-and-swap sob a trava do projeto: o Obsidian ou um sync de nuvem podem gravar no meio.
    if roteiro.load_text(args.project) != text:
        raise ValueError(REVIEW_CHANGED)
    if marked != text:
        # Link (nota do Obsidian) continua link: grava no arquivo de verdade, como o sync.
        atomic_write(path.resolve(), marked)
    entry = roteiro_review.record_review(args.project, doc, args.by, args.channel, args.statement)
    return {
        "review": entry,
        "summary": {"line": f"Revisão de {entry['by']} registrada; pode rodar roteiro --action plan."},
    }


# O plano roda sem revisão; o sync não: o resumo avisa antes de alguém tentar.
UNREVIEWED = " Sem revisão válida, o sync vai recusar: falta a revisão da pessoa (ou o roteiro mudou depois dela)."


def _sync(args, write):
    result = roteiro_sync.run(args.project, write=write, confirm=getattr(args, "confirm_target_change", False))
    unreviewed = "" if result["reviewed"] else UNREVIEWED
    if result["refusal"]:
        # Só o plano chega aqui (o sync levanta): recusado não comparou beats, nunca "0 beats".
        return {**result, "summary": {"line": f"Plano recusado: {result['refusal']}{unreviewed}"}}
    counts = (
        f"{len(result['new'])} beat(s) novo(s), {len(result['target_changed'])} com alvo novo, "
        f"{len(result['speech_changed'])} com fala nova, {len(result['retired'])} aposentado(s)"
    )
    if write:
        line = f"Sincronizado: {counts}."
        if result["invalidated"]:
            # O --confirm-target-change é um sim só; a lista mostra exatamente o que ele derrubou.
            invalidated = ", ".join(result["invalidated"])
            line += f" Aprovações que voltaram a pendente (--confirm-target-change): {invalidated}."
        return {**result, "summary": {"line": line}}
    extra = ""
    if result["affected_approvals"]:
        listed = ", ".join(f"{a['candidate']} ({a['beat']})" for a in result["affected_approvals"])
        extra = f" Voltariam a pendente com --confirm-target-change: {listed}."
    if result["problems"]:
        extra += f" {len(result['problems'])} problema(s) a resolver antes do sync."
    return {**result, "summary": {"line": f"Plano: {counts}.{extra}{unreviewed}"}}


def _assets(args):
    if args.action == "list":
        rows = assets.listing(args.project, args.kind)
        return {"assets": rows, "summary": {"line": f"{len(rows)} componente(s)."}}
    if not args.kind or not args.name:
        raise ValueError("assets --action where precisa de --kind e --name.")
    found = assets.resolve(args.project, args.kind, args.name)
    return {**found, "summary": {"line": f'{args.kind} "{args.name}": {found["status"]}.'}}


def _os_error(exc, project):
    """Erro do sistema de arquivos em pt-BR: o que falhou, onde e o motivo, sem `[Errno N]`."""
    reason = _OS_REASONS.get(exc.errno) or (f"erro do sistema: {exc.strerror}" if exc.strerror else str(exc))
    where = "um arquivo do projeto"
    if exc.filename:
        name = Path(exc.filename)
        root = Path(project).expanduser().resolve()
        where = str(name.relative_to(root)) if name.is_relative_to(root) else str(name)
    return OSError(
        f"Não consegui ler ou gravar {where} ({reason}). Confira as permissões e o espaço em disco e repita."
    )


def run(args):
    """Despacha `roteiro`/`assets` para a ação certa, conforme `args.action`."""
    actions = {
        "new": _new,
        "check": _check,
        "review": _review,
        "plan": lambda parsed: _sync(parsed, write=False),
        "sync": lambda parsed: _sync(parsed, write=True),
    }
    handler = _assets if args.command == "assets" else actions[args.action]
    try:
        return handler(args)
    except OSError as exc:
        raise _os_error(exc, args.project) from exc
