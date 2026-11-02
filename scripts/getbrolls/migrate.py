"""`migrate`: adota o layout 1 num projeto antigo acrescentando só o `project.json`.

Nenhum arquivo é movido, renomeado, copiado ou apagado. `--action plan` mostra o que seria
gravado sem tomar trava nem criar nada; `--action apply` grava o `project.json`. O id é
sempre o que o `plan` mostrou: o `project_id` do manifesto quando é um uuid (em
minúsculas), um uuid derivado dele (uuid5) quando é outro texto, e só sem nenhum
`project_id` um id novo, gerado no `apply` (o `plan` diz "será gerado"). Projeto que já
tem `project.json` válido não tem nada a migrar: `plan` e `apply` saem com 0 e
`changed: false`.
"""

import logging
import uuid
from pathlib import Path

from . import clients, effects, labels, layout, logs, roteiro_frontmatter, runtime
from ._paths import strict_exists, strict_is_dir, strict_is_file
from .ledger import Ledger
from .runtime import record_warning

_log = logs.get("migrate")

# Espaço dos ids derivados de um `project_id` antigo que não é uuid: o mesmo texto vira
# sempre o mesmo id, no `plan` e no `apply`.
_LEGACY_ID_NAMESPACE = uuid.UUID("5b0f6d52-8a57-4f0c-9d1e-0c9a3f7b2e61")

ACTIONS = ("plan", "apply")


def _refused(reason, exc):
    """Registra a recusa (`migrate_refused`: motivo e `error_code`) e devolve `exc` para levantar."""
    return logs.refusal(_log, "migrate_refused", exc, reason=reason)


def _refuse_when_not_adoptable(project):
    found = layout.info(project)
    if found.problem:
        raise _refused(
            "project_json_unusable",
            ValueError(
                f"{layout.PROJECT_FILE} existe mas não pôde ser usado ({found.problem}). "
                "O migrate não sobrescreve: corrija o arquivo ou restaure uma cópia válida e rode status."
            ),
        )
    if strict_exists(project / "brolls" / ".pending-transaction.json"):
        raise _refused(
            "pending_transaction",
            ValueError(
                "Há uma gravação interrompida neste projeto (brolls/.pending-transaction.json). O migrate não a "
                "toca: rode um comando que grava (ex.: `review --project PROJETO`) para concluir a recuperação "
                "e repita."
            ),
        )


def _roteiro_client(project):
    """O `cliente` do frontmatter do ROTEIRO.md do getbrolls, ou `None`; nunca levanta."""
    try:
        if not roteiro_frontmatter.is_roteiro(project):
            return None
        lines = roteiro_frontmatter.roteiro_path(project).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    meta, _, _ = roteiro_frontmatter.parse_frontmatter(lines)
    return meta.get("cliente")


def _legacy_clips(project):
    clips = project / "brolls" / "clips"
    if not strict_is_dir(clips):
        return 0
    return sum(1 for path in clips.iterdir() if strict_is_file(path))


def _manifest_id(project):
    """(id que o `project.json` vai ter ou `None` quando será gerado, notas sobre ele, de onde vem).

    De onde vem: `manifest` (o do manifesto, já uuid), `normalized` (o do manifesto em minúsculas),
    `derived` (uuid5 de um texto que não é uuid) ou `generated` (o manifesto não tem id)."""
    try:
        raw = Ledger(project, recover=False).data.get("project_id")
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, str) or not raw.strip():
        return None, ["O manifesto não tem project_id: o id do projeto será gerado no apply."], "generated"
    try:
        canonical = str(uuid.UUID(raw))
    except ValueError:
        derived = str(uuid.uuid5(_LEGACY_ID_NAMESPACE, raw))
        note = (
            f"O project_id do manifesto não é um uuid; o project.json recebe {derived}, derivado dele "
            "(o mesmo no plan e no apply). O manifesto fica como está."
        )
        return derived, [note], "derived"
    if canonical != raw:
        note = f"O project_id do manifesto vai para o project.json normalizado em minúsculas: {canonical}."
        return canonical, [note], "normalized"
    return canonical, [], "manifest"


def _broll_notes(project):
    folder = project / layout.CLIP_FOLDER
    try:
        # lenient: inside try/except OSError, which maps any error to the same answer
        used = folder.is_dir() and any(folder.iterdir())
    except OSError:
        used = False
    if not used:
        return []
    note = (
        f"{layout.CLIP_FOLDER}/ já existe e tem arquivos: depois do apply ela vira a pasta dos clipes finais do "
        "getbrolls (o fetch grava lá, e a pasta aparece em deliver e export). Se ela é sua, renomeie antes do apply."
    )
    return [note]


def _client_notes(client):
    if client is None:
        return []
    try:
        registered = clients.client_root(client) is not None
    except ValueError as exc:
        return [f"Não deu para conferir se o cliente {client} está registrado: {labels.reason(exc)}"]
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
    if not strict_is_dir(project / "brolls"):
        notes.append("O projeto não tem brolls/; só o project.json será criado, sem clipes a adotar.")
    known, id_notes, id_source = _manifest_id(project)
    notes.extend(id_notes)
    notes.extend(_broll_notes(project))
    notes.extend(_client_notes(chosen))
    doc = layout.new_project_doc(project_id=known, client=chosen)
    if known is None:
        doc = {**doc, "id": None}
    return doc, _legacy_clips(project), notes, id_source


def _nothing_to_migrate(project, found, action):
    logs.event(_log, logging.INFO, "migrate_noop", action=action, layout=found.version)
    return {
        "project": str(project),
        "action": action,
        "changed": False,
        "id": found.doc["id"],
        "doc": found.doc,
        "legacy_clips": _legacy_clips(project),
        "notes": [],
        "summary": {"line": f"{layout.PROJECT_FILE} já existe e é válido (layout {found.version}): nada a migrar."},
    }


def run(args):
    """`migrate --action plan|apply`; `ValueError` quando o projeto não pode ser adotado."""
    project = Path(args.project).expanduser().resolve()
    if not strict_is_dir(project):
        raise _refused(
            # A pasta como a pessoa a digitou: resolvida, um link no caminho mudaria a grafia.
            "not_a_folder",
            ValueError(
                f"{getattr(args, 'project_typed', None) or args.project} não é uma pasta; aponte --project "
                "para a pasta do projeto."
            ),
        )
    found = layout.info(project)
    if found.version and not found.problem:
        return _nothing_to_migrate(project, found, args.action)
    _refuse_when_not_adoptable(project)
    doc, clips, notes, id_source = _plan(project, getattr(args, "client", None))
    base = {"project": str(project), "doc": doc, "legacy_clips": clips, "notes": notes}
    if args.action == "plan":
        logs.event(
            _log,
            logging.INFO,
            "migrate_plan",
            legacy_clips=clips,
            notes=len(notes),
            id_source=id_source,
            client_set=doc["client"] is not None,
        )
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
    for note in _broll_notes(project):
        record_warning("MIGRATE_BROLL_EXISTS", note)
    if doc["id"] is None:
        doc = {**doc, "id": layout.new_project_doc()["id"]}
    # Uma linha `migrate.apply`, emitida no fim do bloco: já com o log do projeto ligado.
    with effects.op("migrate.apply", project / layout.PROJECT_FILE):
        # A trava do projeto só agora, depois de conferir que a pasta existe e pode ser adotada.
        with runtime.project_lock(project):
            layout.write_project(project, doc)
        # Sem `brolls/` o log do projeto estava desligado até aqui: a trava acabou de criá-la.
        logs.enable_project(project)
    logs.event(
        _log,
        logging.INFO,
        "migrate_applied",
        projeto_id=doc["id"],
        legacy_clips=clips,
        notes=len(notes),
        id_source=id_source,
        client_set=doc["client"] is not None,
    )
    return {
        **base,
        "action": "apply",
        "changed": True,
        "wrote": layout.PROJECT_FILE,
        "id": doc["id"],
        "summary": {"line": f"Projeto migrado para o layout 1: {layout.PROJECT_FILE} gravado; nada foi movido."},
    }
