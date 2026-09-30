"""`init`: cria um projeto de layout 1 numa pasta nova ou ainda sem projeto.

Grava `project.json` com um id novo e cria as pastas de trabalho: `aroll/`, as sete
pastas de `assets/`, `broll/` (clipes finais) e `analysis/`. `--client` tem que ser um
cliente registrado; com `--template`, o projeto nasce de uma versão de template do
cliente (`templates.instantiate`). Não cria manifesto,
candidatos nem clipes em `brolls/`; um projeto que já tem `brolls/manifest.json` é
adotado pelo `migrate`, nunca recriado aqui.
"""

from pathlib import Path

from . import layout
from .errors import UsageError

FOLDERS = layout.PROJECT_FOLDERS


def _template_args(args):
    """`--template` pede `--client` e `--tema`; `--tema` só vale com `--template` (erro de uso)."""
    template, client, tema = (getattr(args, name, None) for name in ("template", "client", "tema"))
    if template and not client:
        raise UsageError("init --template precisa de --client <slug>: o template é de um cliente registrado.")
    if template and tema is None:
        raise UsageError("init --template precisa de --tema: o tema do roteiro novo.")
    if tema is not None and not template:
        raise UsageError("--tema só vale com --template (sem template, o roteiro nasce com roteiro --action new).")
    return template


def run(args):
    """Cria o projeto em `args.project`; `ValueError` quando ali já existe um."""
    template = _template_args(args)
    client = getattr(args, "client", None)
    if client:
        from . import clients

        clients.load_client(client)
    if template:
        from . import templates

        return templates.instantiate(
            args.project,
            template,
            client,
            args.tema,
            canvas=getattr(args, "canvas", None),
            fps=getattr(args, "fps", None),
        )
    project = Path(args.project).expanduser().resolve()
    if project.exists() and not project.is_dir():
        raise ValueError(f"{project} existe e não é uma pasta; escolha outra pasta para o projeto.")
    if (project / layout.PROJECT_FILE).exists():
        raise ValueError(f"{layout.PROJECT_FILE} já existe em {project}; este projeto já foi criado.")
    if (project / "brolls" / "manifest.json").exists():
        raise ValueError(
            f"{project} já é um projeto do get-brolls (tem brolls/manifest.json). "
            "Para adotar o layout 1 sem mover nada, use migrate nesse projeto."
        )
    doc = layout.new_project_doc(
        client=client,
        canvas=getattr(args, "canvas", None),
        fps=getattr(args, "fps", None),
    )
    project.mkdir(parents=True, exist_ok=True)
    layout.write_project(project, doc)
    for folder in FOLDERS:
        (project / folder).mkdir(parents=True, exist_ok=True)
    return {
        "project": str(project),
        "id": doc["id"],
        "layout": doc["layout"],
        "folders": list(FOLDERS),
        "summary": {
            "line": (
                "Projeto criado pelo init (layout 1): project.json e as pastas aroll/, assets/, broll/ e analysis/. "
                "Próximo passo: roteiro --action new ou init-brief."
            )
        },
    }
