"""`migrate`: adota o layout 1 num projeto antigo acrescentando só o `project.json`.

Nenhum arquivo é movido, renomeado, copiado ou apagado. `--action plan` mostra o que seria
gravado sem tomar trava nem criar nada; `--action apply` grava o `project.json` com o id que
o projeto já tem (o `project_id` do manifesto, quando é um uuid) ou com um id novo.
"""

import uuid
from pathlib import Path

from . import clients, layout, roteiro_frontmatter
from .ledger import existing_project_id
from .runtime import record_warning

ACTIONS = ("plan", "apply")


def _refuse_when_not_adoptable(project):
    found = layout.info(project)
    if found.problem:
        raise ValueError(
            f"{layout.PROJECT_FILE} existe mas não pôde ser usado ({found.problem}). "
            "O migrate não sobrescreve: corrija o arquivo ou restaure uma cópia válida e rode status."
        )
    if found.version:
        raise ValueError(
            f"{layout.PROJECT_FILE} já existe e é válido (layout {found.version}); não há nada para migrar."
        )
    if (project / "brolls" / ".pending-transaction.json").exists():
        raise ValueError(
            "Há uma gravação interrompida neste projeto (brolls/.pending-transaction.json). O migrate não a toca: "
            "rode um comando que grava (ex.: `review --project <projeto>`) para concluir a recuperação e repita."
        )


def _roteiro_client(project):
    """O `cliente` do frontmatter do ROTEIRO.md do get-brolls, ou `None`; nunca levanta."""
    try:
        if not roteiro_frontmatter.is_roteiro(project):
            return None
        lines = roteiro_frontmatter.roteiro_path(project).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    meta, _, _ = roteiro_frontmatter.parse_frontmatter(lines)
    return meta.get("cliente")


def _legacy_clips(project):
    clips = project / "brolls" / "clips"
    if not clips.is_dir():
        return 0
    return sum(1 for path in clips.iterdir() if path.is_file())


def _manifest_id(project):
    """O `project_id` do manifesto em minúsculas (forma do `project.json`), ou `None`."""
    found = existing_project_id(project)
    return None if found is None else str(uuid.UUID(found))


def _client_notes(client):
    if client is None:
        return []
    try:
        registered = clients.client_root(client) is not None
    except ValueError as exc:
        return [f"Não deu para conferir se o cliente {client} está registrado: {exc}"]
    if registered:
        return []
    message = (
        f'O cliente "{client}" não está registrado em clients.json; o project.json o guarda assim mesmo. '
        "Rode client --action add para registrá-lo."
    )
    return [message]


def _plan(project, client):
    notes = []
    chosen = client or _roteiro_client(project)
    if not (project / "brolls").is_dir():
        notes.append("O projeto não tem brolls/; só o project.json será criado, sem clipes a adotar.")
    known = _manifest_id(project)
    if known is None:
        notes.append("O manifesto não tem um project_id válido; o projeto recebe um id novo.")
    notes.extend(_client_notes(chosen))
    doc = layout.new_project_doc(project_id=known, client=chosen)
    return doc, _legacy_clips(project), notes


def run(args):
    """`migrate --action plan|apply`; `ValueError` quando o projeto não pode ser adotado."""
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f"{project} não é uma pasta; aponte --project para a pasta do projeto.")
    _refuse_when_not_adoptable(project)
    doc, clips, notes = _plan(project, getattr(args, "client", None))
    base = {"project": str(project), "doc": doc, "legacy_clips": clips, "notes": notes}
    if args.action == "plan":
        return {
            **base,
            "action": "plan",
            "changed": False,
            "would_write": layout.PROJECT_FILE,
            "summary": {"line": f"Plano do migrate: gravaria só {layout.PROJECT_FILE}; nenhum arquivo é movido."},
        }
    for note in notes:
        if "registrad" in note:
            record_warning("CLIENT_NOT_REGISTERED", note)
    layout.write_project(project, doc)
    return {
        **base,
        "action": "apply",
        "changed": True,
        "wrote": layout.PROJECT_FILE,
        "id": doc["id"],
        "summary": {"line": f"Projeto migrado para o layout 1: {layout.PROJECT_FILE} gravado; nada foi movido."},
    }
