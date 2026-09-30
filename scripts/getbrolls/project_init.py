"""`init`: cria um projeto de layout 1 numa pasta nova ou ainda sem projeto.

Grava `project.json` com um id novo e cria as pastas de trabalho: `aroll/`, as sete
pastas de `assets/`, `broll/` (clipes finais) e `analysis/`. `--client` tem que ser um
cliente registrado; com `--template`, o projeto nasce de uma versão de template do
cliente (`templates.instantiate`). Não cria manifesto,
candidatos nem clipes em `brolls/`; um projeto que já tem `brolls/manifest.json` é
adotado pelo `migrate`, nunca recriado aqui.
"""

import contextlib
import os
from pathlib import Path

from . import layout, runtime
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


def _refuse_existing(project):
    if project.exists() and not project.is_dir():
        raise ValueError(f"{project} existe e não é uma pasta; escolha outra pasta para o projeto.")
    if (project / layout.PROJECT_FILE).exists():
        raise ValueError(f"{layout.PROJECT_FILE} já existe em {project}; este projeto já foi criado.")
    if (project / "brolls" / "manifest.json").exists():
        raise ValueError(
            f"{project} já é um projeto do get-brolls (tem brolls/manifest.json). "
            "Para adotar o layout 1 sem mover nada, use migrate nesse projeto."
        )
    layout.refuse_linked_folders(project)


def _remove_if_empty(path):
    with contextlib.suppress(OSError):
        path.rmdir()


@contextlib.contextmanager
def creating(project):
    """Trava do projeto para gravar, e desfaz o que só ela criou quando a gravação falha.

    Tudo é validado ANTES daqui: um `init` recusado não chega a criar a pasta do
    projeto nem `brolls/`. Se a gravação falhar depois, sai a trava e as pastas que
    esta execução criou (vazias); nada que já existia é tocado.
    """
    had_project = project.exists()
    had_brolls = os.path.lexists(project / "brolls")
    try:
        with runtime.project_lock(project):
            yield
    except BaseException:
        if not had_brolls:
            with contextlib.suppress(OSError):
                (project / "brolls" / runtime.COMMAND_LOCK).unlink()
            _remove_if_empty(project / "brolls")
        if not had_project:
            _remove_if_empty(project)
        raise


def run(args):
    """Cria o projeto em `args.project`; `ValueError` quando ali já existe um."""
    template = _template_args(args)
    client = getattr(args, "client", None)
    if client:
        from . import clients

        clients.load_client(client)
    project = Path(args.project).expanduser().resolve()
    canvas, fps = getattr(args, "canvas", None), getattr(args, "fps", None)
    if template:
        from . import templates

        plan = templates.plan_instance(project, template, client, args.tema, canvas=canvas, fps=fps)
        with creating(project):
            return templates.write_instance(plan)
    _refuse_existing(project)
    doc = layout.new_project_doc(client=client, canvas=canvas, fps=fps)
    with creating(project):
        created = []
        try:
            for folder in FOLDERS:
                here = project
                for part in folder.split("/"):
                    here = here / part
                    if not here.exists():
                        here.mkdir()
                        created.append(here)
            layout.write_project(project, doc)
        except BaseException:
            for path in reversed(created):
                _remove_if_empty(path)
            raise
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
