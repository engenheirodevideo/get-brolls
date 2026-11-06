"""`init`: cria um projeto de layout 1 numa pasta nova ou ainda sem projeto.

Grava `project.json` com um id novo e cria as pastas de trabalho: `aroll/`, as sete
pastas de `assets/`, `broll/` (clipes finais) e `analysis/`. `--client` tem que ser um
cliente registrado; com `--recipe`, o projeto nasce de uma versão de receita do
cliente (`recipe_init`). Não cria manifesto,
candidatos nem clipes em `brolls/`; um projeto que já tem `brolls/manifest.json` é
adotado pelo `migrate`, nunca recriado aqui.
"""

import contextlib
import functools
import logging
from pathlib import Path

from . import effects, labels, layout, logs, renamed, runtime
from ._paths import strict_exists, strict_is_dir, strict_lexists
from .errors import UsageError
from .sdk.files import is_link

_log = logs.get("project_init")

FOLDERS = layout.PROJECT_FOLDERS


def _refused(reason, exc):
    """Registra a recusa (`init_refused`: motivo e `error_code`) e devolve `exc` para levantar."""
    return logs.refusal(_log, "init_refused", exc, reason=reason)


def _recipe_args(args):
    """`--recipe` pede `--client` e `--tema`; `--tema` só vale com `--recipe`; `--template` saiu (erro de uso)."""
    if getattr(args, "old_template", None):
        raise renamed.refuse(renamed.init_template())
    # lenient: dynamic getattr over argparse flag names, not an existence check on a path
    recipe, client, tema = (getattr(args, name, None) for name in ("recipe", "client", "tema"))
    if recipe and not client:
        raise _refused(
            "recipe_needs_client",
            UsageError("init --recipe precisa de --client SLUG: a receita é de um cliente registrado."),
        )
    if recipe and tema is None:
        raise _refused("recipe_needs_tema", UsageError("init --recipe precisa de --tema: o tema do roteiro novo."))
    if tema is not None and not recipe:
        raise _refused(
            "tema_without_recipe",
            UsageError("--tema só vale com --recipe (sem receita, o roteiro nasce com roteiro --action new)."),
        )
    return recipe


def _refuse_existing(project, typed):
    """Recusa criar onde já há algo (`layout.refuse_existing`) e onde há pasta de trabalho que é link."""
    layout.refuse_existing(project, typed, _refused)
    layout.refuse_linked_folders(project)


def _remove_if_empty(path):
    with contextlib.suppress(OSError):
        effects.rmdir(path)


@contextlib.contextmanager
def creating(project):
    """Trava do projeto para gravar, e desfaz o que só ela criou quando a gravação falha.

    Tudo é validado ANTES daqui: um `init` recusado não chega a criar a pasta do
    projeto nem `brolls/`. Se a gravação falhar depois, sai a trava e as pastas que
    esta execução criou (vazias); nada que já existia é tocado.
    """
    had_project = strict_exists(project)
    had_brolls = strict_lexists(project / "brolls")
    try:
        with runtime.project_lock(project):
            yield
    except BaseException:
        if not had_brolls:
            with contextlib.suppress(OSError):
                effects.unlink(project / "brolls" / runtime.COMMAND_LOCK)
            _remove_if_empty(project / "brolls")
        if not had_project:
            _remove_if_empty(project)
        raise


@contextlib.contextmanager
def _labelled_lookup(path, root):
    """O `OSError` de olhar `path` (uma pasta travada: o 3.11 o levanta, o 3.14 só mais adiante) sai como o de criar."""
    try:
        yield
    except OSError as exc:
        where = labels.shown(path, extra=[(root, "<cliente>")])
        raise OSError(f"Não consegui criar {where} ({labels.reason(exc)}). Confira as permissões e repita.") from exc


def _target(args):
    """(pasta do projeto, como as mensagens a nomeiam): `--project` ou `<pasta do cliente>/projetos/<slug>/`."""
    if args.project:
        typed = getattr(args, "project_typed", None) or args.project
        # O último trecho como digitado, antes de resolver: um link (vivo ou pendurado) levaria o projeto ao seu alvo.
        if is_link(Path(args.project).expanduser()):
            raise _refused(
                "project_link",
                ValueError(f"{typed} é um link; o getbrolls não cria projeto através de links."),
            )
        return Path(args.project).expanduser().resolve(), typed
    from . import clients

    root = clients.client_root(args.client)
    if root is None:  # `run` já carregou o cliente; só para o pyright
        raise _refused("client_unregistered", ValueError(f'O cliente "{args.client}" não está registrado.'))
    projects = root / clients.PROJECTS
    with _labelled_lookup(projects / args.slug, root):
        projects_linked = is_link(projects)
        projects_odd = strict_lexists(projects) and not strict_is_dir(projects)
        target_linked = is_link(projects / args.slug)
    if projects_linked:
        raise _refused(
            "projects_link",
            ValueError(
                f"{labels.shown(projects, extra=[(root, '<cliente>')])} é um link; "
                "o getbrolls não cria projeto através de links."
            ),
        )
    if projects_odd:
        raise _refused(
            "projects_not_a_folder",
            ValueError(f"{labels.shown(projects, extra=[(root, '<cliente>')])} existe e não é uma pasta."),
        )
    target = projects / args.slug
    typed = labels.shown(target, extra=[(root, "<cliente>")])
    if target_linked:  # também o link pendurado: criar o projeto seguiria o link para fora do cliente
        raise _refused(
            "project_link",
            ValueError(f"{typed} é um link; o getbrolls não cria projeto através de links."),
        )
    return target, typed


@contextlib.contextmanager
def _labelled_io(args, typed):
    """Com `--client --slug`, o `OSError` de criar o projeto sai com a marca `<cliente>/…`, sem o caminho da máquina."""
    try:
        yield
    except OSError as exc:
        if args.project:  # o `--project` digitado já sai pela mensagem de E/S do comando, com o caminho dele
            raise
        raise OSError(f"Não consegui criar {typed} ({labels.reason(exc)}). Confira as permissões e repita.") from exc


def run(args):
    """Cria o projeto em `args.project`; `ValueError` quando ali já existe um."""
    recipe = _recipe_args(args)
    client = getattr(args, "client", None)
    if client:
        from . import clients

        clients.load_client(client)
    project, typed = _target(args)
    canvas, fps = getattr(args, "canvas", None), getattr(args, "fps", None)
    applied = None
    if recipe:
        from . import recipe_init

        plan = recipe_init.plan_instance(project, recipe, client, args.tema, canvas=canvas, fps=fps, typed=typed)
        with _labelled_io(args, typed), creating(project):
            result = recipe_init.write_instance(plan, typed=args.project and typed)
        applied = functools.partial(recipe_init.log_applied, plan, result)
    else:
        with _labelled_io(args, typed):
            result = _create(project, client, canvas, fps, typed=typed)
    # O projeto só tem `brolls/` agora: é a hora de ligar o log dele e registrar o que nasceu.
    logs.enable_project(project)
    logs.event(
        _log,
        logging.INFO,
        "init_created",
        projeto_id=result["id"],
        layout=result["layout"],
        folders=len(result["folders"]),
        recipe=bool(recipe),
        client_set=bool(client),
        canvas_set=canvas is not None,
        fps_set=fps is not None,
    )
    if applied is not None:
        applied()
    return result


def _create(project, client, canvas, fps, *, typed):
    """O `init` sem receita: `project.json` e as pastas de trabalho; devolve o resultado do comando."""
    _refuse_existing(project, typed)
    doc = layout.new_project_doc(client=client, canvas=canvas, fps=fps)
    with creating(project):
        created = []
        try:
            for folder in FOLDERS:
                here = project
                for part in folder.split("/"):
                    here = here / part
                    if not strict_exists(here):
                        effects.mkdir(here)
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
