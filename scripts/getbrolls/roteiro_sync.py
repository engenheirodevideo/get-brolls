"""Sync do roteiro para os beats do BRIEF.md, e o plano do sync sem escrita.

`run(project, write=False)` é o `plan`: só lê (manifesto com `recover=False`, recusa
se houver gravação interrompida) e nunca levanta por causa de aprovação atingida —
ela vem em `affected_approvals`. `run(project, write=True)` é o `sync`: exige revisão
válida, para diante de alvo mudado em beat aprovado e, com `confirm`, invalida
primeiro no manifesto (journal) e só depois grava ROTEIRO.md e BRIEF.md atômicos,
com cópia `.sync.bak` antes (`.bak` é do `roteiro new --force`: o sync nunca toca).
Só mudar a fala nunca invalida nada: vira aviso.
"""

import json
import re
import shutil
from pathlib import Path

from . import roteiro, roteiro_ids
from .brief import brief_path, read_json_block, validate_brief
from .ledger import Ledger, atomic_write
from .models import invalidate_approval
from .roteiro_plan import aspect_problems, scene_plan
from .roteiro_review import review_state
from .rules import load_rules

OWNED = ("target", "narration", "duration_hint_s")
PENDING = ".pending-transaction.json"
# Cópia do sync; `ROTEIRO.md.bak`/`BRIEF.md.bak` são de `roteiro new --force` e nunca mudam aqui.
SYNC_BAK = ".sync.bak"
_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
REVIEW_MISSING = (
    "O roteiro atual não tem revisão humana registrada (ou mudou depois dela). Mostre o texto à pessoa e "
    'registre com `roteiro --action review --by NOME --channel chat --statement "frase exata"`.'
)


def _empty_report():
    """Forma do relatório quando o plano para antes de comparar beats (recusa de id); listas novas a cada chamada."""
    return {
        "new": [], "target_changed": [], "speech_changed": [], "retired": [], "reactivated": [],
        "order_changed": False, "problems": [], "warnings": [], "affected_approvals": [], "invalidated": [],
        "beats": [], "total_s": 0.0,
    }  # fmt: skip


def _project_dir(project):
    return Path(project).expanduser().resolve()


def ensure_no_pending(project):
    """Leitura nunca conclui gravação alheia: com journal pendente, recusa em vez de ler estado velho."""
    if (_project_dir(project) / "brolls" / PENDING).exists():
        raise ValueError(
            "Há uma gravação interrompida neste projeto (brolls/.pending-transaction.json) e este comando só lê. "
            "Rode um comando que grava (ex.: `review --project <projeto>`, que só regenera o Storyboard) para "
            "concluir a recuperação e repita."
        )


def is_scene_beat(beat_id):
    return isinstance(beat_id, str) and roteiro.SCENE_BEAT_RE.fullmatch(beat_id) is not None


def _active(beats):
    return [b["id"] for b in beats if not b.get("retired")]


def merge_beats(old_beats, planned):
    """(beats novos, relatório). O sync só possui `target`, `narration` e `duration_hint_s`.

    Ordem: beats de cena na ordem do roteiro, depois os manuais (ordem relativa
    mantida), depois os aposentados. `order_changed`: algum beat ativo que continua
    mudou de posição (o próximo deliver renumera `entrega/`).
    """
    by_id = {b.get("id"): b for b in old_beats}
    wanted = {p["id"] for p in planned}
    report = {"new": [], "target_changed": [], "speech_changed": [], "retired": [], "reactivated": []}
    scene_beats = []
    for plan in planned:
        owned = {key: plan[key] for key in OWNED}
        old = by_id.get(plan["id"])
        if old is None:
            report["new"].append(plan["id"])
            scene_beats.append({"id": plan["id"], **owned})
            continue
        if old.get("target") != plan["target"]:
            report["target_changed"].append(plan["id"])
        if (old.get("narration") or None) != plan["narration"]:
            report["speech_changed"].append(plan["id"])
        if old.get("retired"):
            report["reactivated"].append(plan["id"])
        scene_beats.append({**{k: v for k, v in old.items() if k != "retired"}, **owned})
    manual = [b for b in old_beats if not is_scene_beat(b.get("id"))]
    retiring = [b for b in old_beats if is_scene_beat(b.get("id")) and b["id"] not in wanted]
    report["retired"] = [b["id"] for b in retiring if not b.get("retired")]
    merged = scene_beats + manual + [{**b, "retired": True} for b in retiring]
    before, after = _active(old_beats), _active(merged)
    moved = any(before.index(i) != after.index(i) for i in before if i in after)
    return merged, {**report, "order_changed": moved}


def _with_material(report, items):
    """Ids "novos" no BRIEF.md que já têm candidatos no manifesto (id antigo de volta ao roteiro)."""
    shots = {c.get("shot") for c in items}
    return [i for i in report["new"] if i in shots]


def _gated(report, items):
    """Candidatos aprovados que deixam de valer: alvo mudou, ou id "novo" que já tem material no manifesto."""
    ids = set(report["target_changed"]) | set(_with_material(report, items))
    return [c for c in items if c.get("shot") in ids and (c.get("approval") or {}).get("status") == "approved"]


def _brief(project, rules):
    home = _project_dir(project)
    local = home / "BRIEF.md"
    path = brief_path(project)
    if local.is_symlink():
        real = local.resolve()
        where = f"para {real.relative_to(home)}" if real.is_relative_to(home) else "para fora do projeto"
        raise ValueError(
            f"O BRIEF.md do projeto é um link {where}: o sync do roteiro só grava num arquivo de verdade. "
            "Troque o link pelo arquivo de verdade (copie o conteúdo para BRIEF.md) e repita."
        )
    if path != local:
        where = f"para {path.relative_to(home)}" if path.is_relative_to(home) else "para fora do projeto"
        raise ValueError(
            f"GB_BRIEF_FILE aponta {where}: o sync do roteiro só grava no BRIEF.md da pasta do "
            "projeto. Para seguir, apague a variável GB_BRIEF_FILE ou mova o BRIEF.md para dentro do projeto "
            "e repita."
        )
    if not path.is_file():
        raise ValueError(
            "O sync precisa de um BRIEF.md: vídeo, direitos e fontes são decisões da pessoa. Rode "
            '`/get-brolls-brief` antes (com ROTEIRO.md, a entrevista escreve "beats": []).'
        )
    data = read_json_block(
        path,
        missing="O BRIEF.md precisa de exatamente um bloco ```json: conserte antes do sync.",
        syntax="O bloco json do BRIEF.md está com erro de digitação: conserte e rode `brief --validate` antes do sync.",
        not_object="O bloco json do BRIEF.md tem que ser um objeto.",
    )
    validate_brief(data, rules, project=project)
    return path, path.read_text(encoding="utf-8"), data


def _replace_block(raw, data):
    """Troca só o miolo do bloco ```json; a prosa em volta fica byte a byte."""
    match = _JSON_BLOCK.search(raw)
    if match is None:
        raise ValueError("O BRIEF.md não tem o bloco ```json que o sync troca: conserte e repita.")
    return raw[: match.start(1)] + json.dumps(data, ensure_ascii=False, indent=2) + raw[match.end(1) :]


def _warnings(plan, merge, gated_beats, with_material):
    """Tudo o que muda sem portão fica visível: troca de comentário de id mantém a revisão válida."""
    warnings = list(plan["warnings"])
    for i in merge["target_changed"]:
        if i in gated_beats:
            warnings.append(f"alvo mudou em {i}: as aprovações deste beat voltam a pendente")
        else:
            warnings.append(f"alvo mudou em {i}: candidatos antigos deste beat podem não servir mais")
    warnings += [
        f"{i} já tem candidatos no manifesto de antes: confira se ainda servem ao alvo novo"
        for i in with_material
        if i not in gated_beats
    ]
    warnings += [f"fala mudou em {i}: confira se o clipe ainda serve" for i in merge["speech_changed"]]
    warnings += [f"{i} saiu do roteiro: beat aposentado; candidatos e clipes ficam" for i in merge["retired"]]
    if merge["order_changed"]:
        warnings.append("a ordem dos beats mudou: o próximo deliver renumera as pastas de entrega/")
    return warnings


def _ledger(project, write):
    """Manifesto só para leitura; o sync conclui antes uma gravação interrompida (é comando que grava)."""
    brolls = _project_dir(project) / "brolls"
    if write and (brolls / PENDING).exists():
        return Ledger(project)
    return Ledger(project, recover=False) if brolls.is_dir() else None


def _prepare(project, write, plugins):
    """Tudo o que plan e sync precisam, sem gravar nada (fora a recuperação de journal do sync)."""
    if not write:
        ensure_no_pending(project)
    text = roteiro.load_text(project)
    doc = roteiro.parse(text, plugins)
    reviewed = review_state(project, doc)["reviewed"]
    if write and not reviewed:
        raise ValueError(REVIEW_MISSING)
    rules = load_rules(project)
    brief_file, raw_brief, brief_data = _brief(project, rules)
    state = roteiro_ids.read_state(project)
    ledger = _ledger(project, write)
    # Todos os beats (aposentados também) e todos os candidatos (qualquer status): id visto nunca volta.
    old_beats = list(brief_data.get("beats") or [])
    ids = roteiro_ids.plan_ids(doc, old_beats, ledger.data["items"] if ledger else [], state)
    return {
        "text": text, "reviewed": reviewed, "rules": rules, "brief_file": brief_file, "raw_brief": raw_brief,
        "brief_data": brief_data, "ledger": ledger, "state": state, "old_beats": old_beats, "ids": ids,
    }  # fmt: skip


def _commit(project, ctx, hits, new_text, new_data):
    """Ordem de gravação: manifesto (journal) → ROTEIRO.md → BRIEF.md. O estado dos ids vem depois, em `run`."""
    written = []
    if hits:
        for c in hits:
            invalidate_approval(c, bump_revision=True)
        ledger = ctx["ledger"]
        # Manifesto aberto sem recuperação não cria pastas; o journal conclui gravando em candidates/.
        (ledger.root / "candidates").mkdir(parents=True, exist_ok=True)
        ledger.save_many("roteiro_target_changed", hits)
        written.append("brolls/manifest.json")
    path = roteiro.roteiro_path(project)
    if new_text != ctx["text"]:
        # ROTEIRO.md pode ser link (nota do Obsidian): grava no arquivo de verdade e o link continua link.
        shutil.copyfile(path, path.with_name(path.name + SYNC_BAK))
        atomic_write(path.resolve(), new_text)
        written.append(path.name)
    new_raw = _replace_block(ctx["raw_brief"], new_data)
    if new_raw != ctx["raw_brief"]:
        brief_file = ctx["brief_file"]
        shutil.copyfile(brief_file, brief_file.with_name(brief_file.name + SYNC_BAK))
        atomic_write(brief_file, new_raw)
        written.append(brief_file.name)
    return written


def run(project, write=False, confirm=False, plugins=None):
    """`plan` (write=False) ou `sync` (write=True); devolve o relatório do que muda."""
    plugins = roteiro.enabled_plugins() if plugins is None else plugins
    ctx = _prepare(project, write, plugins)
    ids = ctx["ids"]
    base = {
        **_empty_report(), "written": [], "reviewed": ctx["reviewed"], "refusal": ids["refusal"],
        "ids_to_assign": {str(line): i for line, i in ids["assign"].items()},
        "readopted": {str(line): i for line, i in ids["readopted"].items()},
    }  # fmt: skip
    if ids["refusal"]:
        if write:
            raise ValueError(ids["refusal"])
        # Recusado, o plano não comparou beats: None, nunca "zero beats" / "0 s".
        return {**base, "beats": None, "total_s": None}
    # `apply_ids` recebe o texto de `load_text` (sem BOM, `\n`); o doc reparseado já tem os ids novos.
    new_text = roteiro_ids.apply_ids(ctx["text"], ids["assign"])
    doc = roteiro.parse(new_text, plugins)
    plan = scene_plan(project, doc)
    problems = plan["problems"] + aspect_problems(doc.meta, ctx["rules"], ctx["brief_data"])
    merged, merge = merge_beats(ctx["old_beats"], plan["beats"])
    new_data = {**ctx["brief_data"], "beats": merged}
    validate_brief(new_data, ctx["rules"], project=project)
    items = ctx["ledger"].data["items"] if ctx["ledger"] else []
    hits = _gated(merge, items)
    warnings = _warnings(plan, merge, {c["shot"] for c in hits}, _with_material(merge, items))
    result = {
        **base, **merge, "problems": problems, "warnings": warnings,
        "affected_approvals": [{"candidate": c["id"], "beat": c["shot"]} for c in hits],
        "invalidated": [], "beats": _active(merged), "total_s": plan["total_s"],
    }  # fmt: skip
    if not write:
        return result
    if problems:
        raise ValueError("O roteiro ainda tem problema; o sync não grava nada:\n" + "\n".join(problems))
    if hits and not confirm:
        listed = ", ".join(f"{c['id']} ({c['shot']})" for c in hits)
        raise ValueError(
            f"O roteiro muda o alvo de beat com aprovação já dada: {listed}. Explique à pessoa que essas "
            "aprovações voltam a pendente; com o sim dela, repita com --confirm-target-change."
        )
    written = _commit(project, ctx, hits, new_text, new_data)
    new_state = roteiro_ids.next_state(ctx["state"], doc, ids["next_id"])
    if new_state != ctx["state"]:
        roteiro_ids.write_state(project, new_state)
        written.append(f"brolls/{roteiro_ids.STATE_FILE}")
    return {**result, "written": written, "invalidated": [c["id"] for c in hits]}
