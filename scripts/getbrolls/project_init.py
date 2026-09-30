"""`init`: cria um projeto de layout 1 numa pasta nova ou ainda sem projeto.

Grava `project.json` com um id novo e cria as pastas de trabalho: `aroll/`, as sete
pastas de `assets/`, `broll/` (clipes finais) e `analysis/`. Não cria manifesto,
candidatos nem clipes em `brolls/`; um projeto que já tem `brolls/manifest.json` é
adotado pelo `migrate`, nunca recriado aqui.
"""

from pathlib import Path

from . import layout

FOLDERS = ("aroll", *(f"assets/{name}" for name in layout.ASSET_FOLDERS), "broll", "analysis")


def run(args):
    """Cria o projeto em `args.project`; `ValueError` quando ali já existe um."""
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
        client=getattr(args, "client", None),
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
