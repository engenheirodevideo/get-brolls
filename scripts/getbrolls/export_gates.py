"""Portões do `gb export`: só um roteiro do get-brolls, revisado e em sincronia, vira projeto de edição.

Tudo aqui só lê. A ordem importa: gravação interrompida primeiro (nunca ler estado
velho), depois o texto (`load_text` dá a mensagem certa para arquivo que falta, pasta,
link quebrado ou não UTF-8) e só então `is_roteiro`; o BRIEF.md tem que ser o arquivo
da pasta do projeto; problemas do roteiro, revisão e sync fecham a lista.
"""

import os
from pathlib import Path

from . import roteiro, roteiro_ids, roteiro_sync
from .brief import brief_path, load_brief
from .errors import PrerequisiteError
from .roteiro_plan import aspect_problems, scene_plan
from .roteiro_review import review_state
from .rules import load_rules

NOT_ROTEIRO = "O export precisa de um ROTEIRO.md do get-brolls (frontmatter com type: roteiro)."
BRIEF_OUTSIDE = (
    "O export lê o BRIEF.md da pasta do projeto: tire GB_BRIEF_FILE do ambiente "
    "(ou troque o link por um arquivo) e repita."
)
OUT_OF_SYNC = "O BRIEF.md não reflete o roteiro: rode `roteiro --action plan` e `roteiro --action sync` e repita."
# O sync simulado recusou (BRIEF.md inválido depois do sync, por exemplo): o texto dele vai junto.
SYNC_CHECK_FAILED = "O export não conseguiu conferir o BRIEF.md contra o roteiro: {} Conserte e repita o export."
# Qualquer um destes no plano do sync (sem gravar) quer dizer que o BRIEF.md ainda não é o roteiro de agora.
_PENDING_LISTS = ("new", "target_changed", "speech_changed", "retired", "reactivated")
_PENDING_MAPS = ("ids_to_assign", "readopted")


def _brief_file(project):
    """O BRIEF.md do export: o arquivo da pasta, nunca `GB_BRIEF_FILE` nem link."""
    local = Path(project).expanduser().resolve() / "BRIEF.md"
    if os.environ.get("GB_BRIEF_FILE") or local.is_symlink() or brief_path(project) != local:
        raise ValueError(BRIEF_OUTSIDE)
    if not local.is_file():
        raise ValueError(OUT_OF_SYNC)
    return local


def _in_sync(project, plugins):
    if not roteiro_ids.state_path(project).is_file():
        return False
    try:
        report = roteiro_sync.run(project, write=False, plugins=plugins)
    except PrerequisiteError:
        raise
    except ValueError as exc:
        raise ValueError(SYNC_CHECK_FAILED.format(str(exc).rstrip())) from exc
    if report["refusal"] or report["order_changed"]:
        return False
    return not any(report[key] for key in (*_PENDING_LISTS, *_PENDING_MAPS))


def _manifest(project):
    """Manifesto lido sem recuperar gravação (o portão 1 já recusou journal pendente); nunca grava."""
    from .ledger import Ledger

    if not (Path(project).expanduser().resolve() / "brolls").is_dir():
        return {"items": []}
    return Ledger(project, recover=False).data


def check(project, plugins=None):
    """`{"doc", "plan", "items", "project_id"}` de um projeto pronto para exportar; `ValueError` com a
    primeira recusa. `project_id` é o id que o manifesto já tem, ou None: o export nunca cria um."""
    plugins = roteiro.enabled_plugins() if plugins is None else plugins
    roteiro_sync.ensure_no_pending(project)
    text = roteiro.load_text(project)
    if not roteiro.is_roteiro(project):
        raise ValueError(NOT_ROTEIRO)
    _brief_file(project)
    doc = roteiro.parse(text, plugins)
    plan = scene_plan(project, doc)
    problems = plan["problems"] + aspect_problems(doc.meta, load_rules(project), load_brief(project))
    if problems:
        raise ValueError("ROTEIRO.md com problema; o export não começa:\n" + "\n".join(problems))
    if not review_state(project, doc)["reviewed"]:
        raise ValueError(roteiro_sync.REVIEW_MISSING)
    if not _in_sync(project, plugins):
        raise ValueError(OUT_OF_SYNC)
    data = _manifest(project)
    project_id = data.get("project_id")
    return {
        "doc": doc,
        "plan": plan,
        "items": data["items"],
        "project_id": project_id if isinstance(project_id, str) else None,
    }
