"""`init --recipe`: projeto novo a partir de uma versão de receita do cliente, com o `recipe.lock.json`.

O projeto ganha `project.json` (cliente, ref canônica da receita, quadro e fps), um `ROTEIRO.md` em
rascunho com uma cena por cena da receita, as pastas do layout 1, o `RULES.md` no formato do roteiro
(quando falta) e os componentes. O que a pasta do cliente já tem (mesmo nome, mesmo sha256) fica lá,
no nível `client`; o resto é copiado para `assets/<tipo>/` do projeto, no nível `project`, sempre sem
licença. O lock guarda a ref, o sha256 do `getbrolls-recipe.json` e, por item, tipo, nome, nível,
caminho relativo ao nível e sha256: `status` e `export` avisam `RECIPE_LOCK_DRIFT` quando o item achado
hoje não bate. Tamanho e mtime são só cache: iguais aos do arquivo, o sha256 não é recalculado.
"""

import contextlib
import json
import logging
import unicodedata
from pathlib import Path

from . import (
    assets,
    clients,
    effects,
    labels,
    layout,
    logs,
    open_enums,
    recipes,
    refs,
    renamed,
    roteiro,
    runtime,
    versioning,
)
from ._paths import strict_exists, strict_lexists
from .models import now
from .roteiro_frontmatter import fold
from .sdk import schemas
from .sdk.files import is_link
from .sdk.jsonschema import errors

_log = logs.get("recipe_init")

LOCK_FILE = "recipe.lock.json"
LOCK_FAMILY = "recipe_lock"
# O lock da versão de testes da 2.6: não é lido; o `migrate --project` converte.
OLD_LOCK_FILE = "template.lock.json"
LOCK_MAX = 4 * 1024 * 1024
# Níveis que o `init` grava hoje; nível desconhecido no lock é enum aberto (`ENUM_UNKNOWN`).
TIERS = ("project", "client")


def _refused(reason, exc, **fields):
    """Registra a recusa (`recipe_refused`, ação `init`) e devolve `exc` para levantar."""
    return logs.refusal(_log, "recipe_refused", exc, action="init", reason=reason, **fields)


def _frontmatter_value(text, flag):
    value = unicodedata.normalize("NFC", text).strip() if isinstance(text, str) else ""
    if not value or any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError(f"Informe {flag} numa linha só.")
    return value.replace('"', '\\"')


def roteiro_text(doc, owner, tema):
    """`ROTEIRO.md` novo de uma receita: frontmatter com tema e cliente, uma cena por cena da receita."""
    if doc["genero"] not in roteiro.GENRES:
        raise _refused(
            "genre_unknown", ValueError(f'A receita é do gênero "{doc["genero"]}", que esta versão não conhece.')
        )
    head = [
        "---", "type: roteiro", f"genero: {doc['genero']}", f'aspecto: "{doc["aspecto"]}"',
        f'tema: "{_frontmatter_value(tema, "--tema")}"', f"cliente: {owner}", "legenda: true", "status: draft",
        "---", "",
    ]  # fmt: skip
    body = []
    for scene in doc["scenes"]:
        lines = [scene["title"], scene["layout"], *scene["layers"]]
        if any(unicodedata.category(char) == "Cc" for line in lines for char in line):
            raise _refused(
                "control_char",
                ValueError(f"A cena {scene['id']} da receita tem caractere de controle; a receita não serve."),
            )
        body += [f"## {scene['title']}", scene["layout"], *scene["layers"], recipes.SPEECH_PLACEHOLDER, ""]
    return "\n".join(head + body)


def _checked_roteiro(doc, text):
    """O roteiro gerado lê de volta com as mesmas cenas; senão a receita não serve."""
    try:
        parsed = roteiro.parse(text, plugins=frozenset())
    except roteiro.RoteiroError as exc:
        raise _refused(
            "roteiro_invalid", ValueError(f"O roteiro gerado pela receita não passou no check: {exc}")
        ) from None
    scenes = [
        (s.title, roteiro.directive_text(s.layout), [roteiro.directive_text(x) for x in s.layers])
        for s in parsed.scenes
    ]
    wanted = [(scene["title"], scene["layout"], scene["layers"]) for scene in doc["scenes"]]
    if scenes != wanted:
        raise _refused(
            "scenes_differ",
            ValueError("As cenas da receita não viram as mesmas cenas no roteiro; a receita não serve."),
        )
    return parsed


def _client_match(owner, spec, item):
    """O arquivo único da pasta do cliente com o nome do componente, ou `None`."""
    folder = assets.client_components(owner, spec)
    if folder is None:
        return None
    matches = assets.component_entries(folder, spec).get(fold(unicodedata.normalize("NFC", item["name"])), [])
    # lenient: matches were just stat'ed by strict_is_file in component_entries
    if len(matches) != 1 or is_link(matches[0]) or not matches[0].is_file():
        return None
    return matches[0]


def _install(project, folder, owner, items, created):
    """Itens do lock: o que o cliente já tem fica lá (nível `client`); o resto é copiado sem licença.

    `items` são os do `components.json` que o `show` acabou de conferir; cada arquivo é conferido de
    novo no caminho (`recipes.component_path`) e pelo sha256 dos bytes copiados. A cópia é sempre
    arquivo a arquivo (`copy_hashed`, só o que a lista cita): nunca a pasta da versão, que traria o
    modo somente leitura e o lixo do sistema para dentro do projeto. Tipo que esta versão não
    conhece fica de fora, com `ENUM_UNKNOWN`.
    """
    installed, warnings = [], []
    for item in items:
        if not open_enums.known("component_kind", item["kind"], assets.ASSET_KINDS):
            continue
        spec = assets.ASSET_KINDS[item["kind"]]
        source = recipes.component_path(folder, item["file"], item["kind"])
        placed = _client_match(owner, spec, item)
        if placed is not None and recipes.sha256_file(placed) == item["sha256"]:
            tier, path = "client", f"{spec.folder}/{placed.name}"
        else:
            tier, path = "project", f"{spec.folder}/{source.name}"
            target = project.joinpath(*path.split("/"))
            recipes.refuse_link(target.parent, project=project)
            recipes.copy_hashed(source, target, expected=item["sha256"], project=project)
            created.append(target)
            placed = target
            if spec.licensed:
                warnings.append(
                    f"{path}: licença não transferida pela receita; registre {source.stem}{assets.LICENSE_SUFFIX} "
                    "antes de usar."
                )
        seen = placed.stat()
        installed.append(
            {
                "kind": item["kind"],
                "name": item["name"],
                "tier": tier,
                "path": path,
                "sha256": item["sha256"],
                # Cache do `status`: tamanho e mtime iguais aos daqui pulam o sha256 do arquivo.
                "size": seen.st_size,
                "mtime_ns": seen.st_mtime_ns,
            }
        )
    return installed, warnings


def validate_lock(doc, label=LOCK_FILE):
    """Confere `doc` contra o schema publicado do `recipe.lock.json`; `ValueError` senão."""
    if not isinstance(doc, dict):
        raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
            f"{label} tem que ser um objeto JSON."
        )
    versioning.read_schema(doc, LOCK_FAMILY, schemas.version(LOCK_FAMILY), label=label)
    problems = errors(doc, schemas.load(LOCK_FAMILY))
    if problems:
        raise ValueError(f"{label} fora do schema: " + "; ".join(problems[:5]))
    return doc


def _undo(created):
    for path in reversed(created):
        with contextlib.suppress(OSError):
            # lenient: rollback cleanup; must not mask the original error
            if path.is_dir() and not is_link(path):
                effects.rmdir(path)
            else:
                effects.unlink(path)


# pylint: disable-next=too-many-arguments  # the options of init --recipe
def plan_instance(project, ref, client, tema, *, canvas=None, fps=None, typed=None) -> dict:  # noqa: PLR0913 - options
    """Tudo o que `init --recipe` confere antes de criar qualquer coisa; devolve o plano de `write_instance`.

    Recusa projeto existente, pasta de trabalho que é link, receita que não está íntegra (inclusive o
    `getbrolls-recipe.json` que não bate com o selo) e tema ou cenas que não viram um roteiro válido,
    sem criar nem a pasta do projeto.
    """
    project = Path(project).expanduser().resolve()
    # sem `--project` digitado, a pasta pela marca: nunca o caminho da máquina
    layout.refuse_existing(
        project, typed or labels.shown(project, project=project), _refused, files_kept=("ROTEIRO.md", LOCK_FILE)
    )
    layout.refuse_linked_folders(project)
    clients.check_slug(client)
    shown = recipes.show(ref, owner=client)
    if not shown["intact"]:
        raise _refused(
            "not_intact",
            ValueError(
                f"A receita {shown['ref']} do cliente {client} não está íntegra: " + "; ".join(shown["problems"][:5])
            ),
            problems=len(shown["problems"]),
        )
    doc = shown["recipe"]
    text = roteiro_text(doc, client, tema)
    _checked_roteiro(doc, text)
    project_doc = layout.new_project_doc(
        client=client,
        recipe=shown["ref"],
        canvas=canvas if canvas is not None else doc["canvas"],
        fps=fps if fps is not None else doc["fps"],
    )
    return {
        "project": project,
        "shown": shown,
        "folder": recipes.recipe_dir(client, doc["slug"], doc["version"]),
        "roteiro": text,
        "project_doc": project_doc,
    }


def _format(doc):
    return roteiro.ASPECT_TO_FORMAT[doc["aspecto"]]


def _write_lock(project, shown, installed):
    """Grava o `recipe.lock.json` da instância (validado) e devolve o caminho."""
    lock = {
        "recipe": shown["ref"],
        "recipe_sha256": shown["recipe_sha256"],
        "applied": now(),
        "items": installed,
        "style": None,
    }
    text = json.dumps(
        validate_lock(versioning.stamp_schema(lock, LOCK_FAMILY, schemas.version(LOCK_FAMILY))),
        ensure_ascii=False,
        indent=2,
    )
    recipes.write_new(project / LOCK_FILE, text + "\n")
    return project / LOCK_FILE


def _result(plan, installed, folders, seeded, typed):
    """O resultado de `init --recipe`: o que foi criado e o próximo passo (`typed`: o `--project` digitado)."""
    from . import guidance  # tardio: o guidance puxa o resto da CLI

    project, shown, project_doc = plan["project"], plan["shown"], plan["project_doc"]
    return {
        "project": str(project),
        "id": project_doc["id"],
        "layout": project_doc["layout"],
        "client": shown["owner"],
        "recipe": shown["ref"],
        "folders": folders,
        "items": installed,
        "summary": {
            "line": (
                f"Projeto criado da receita {shown['ref']} do cliente {shown['owner']}: ROTEIRO.md com "
                f"{len(shown['recipe']['scenes'])} cena(s) para preencher, {LOCK_FILE} e project.json. Licenças e "
                "aprovações não vêm da receita. Próximo passo: preencher o ROTEIRO.md e rodar "
                "roteiro --action check."
                + (f' RULES.md criado em "{_format(shown["recipe"])}", o formato do roteiro.' if seeded else "")
            ),
            "do": guidance.roteiro_check_step(typed or project),
        },
        "rules": labels.shown(seeded, project=project) if seeded else None,
    }


def write_instance(plan, *, typed=None) -> dict:
    """Grava o plano de `plan_instance`; desfaz o que gravou quando algo falha no meio.

    `typed` é o `--project` como a pessoa o digitou: o comando sugerido o repete assim (sem ele, a pasta do projeto).
    """
    from . import rules  # tardio: o rules puxa brief/queue

    project, shown = plan["project"], plan["shown"]
    created = [] if strict_exists(project) else [project]
    effects.mkdir(project, parents=True, exist_ok=True)
    try:
        folders = _make_folders(project, created)
        recipes.write_new(project / "ROTEIRO.md", plan["roteiro"])
        created.append(project / "ROTEIRO.md")
        seeded = rules.seed_project_rules(project, _format(shown["recipe"]))
        if seeded:
            created.append(seeded)
        installed, warnings = _install(project, plan["folder"], shown["owner"], shown["components"], created)
        created.append(_write_lock(project, shown, installed))
        layout.write_project(project, plan["project_doc"])
    except BaseException:
        _undo(created)
        raise
    for warning in warnings:
        runtime.record_warning("LICENCE_NOT_TRANSFERRED", warning)
    return _result(plan, installed, folders, seeded, typed)


def log_applied(plan, result):
    """`recipe_applied` (INFO): o que `init --recipe` criou, em ids e contagens.

    Quem chama (o `init`) liga o log do projeto antes, porque o projeto só passa a ter `brolls/`
    quando a gravação termina; por isso o evento não sai de `write_instance`.
    """
    doc = plan["shown"]["recipe"]
    items = result["items"]
    logs.event(
        _log,
        logging.INFO,
        "recipe_applied",
        projeto_id=result["id"],
        version=doc["version"],
        scenes=len(doc["scenes"]),
        items=len(items),
        copied=sum(1 for row in items if row["tier"] == "project"),
        rules_seeded=result["rules"] is not None,
    )


def _make_folders(project, created):
    for name in layout.PROJECT_FOLDERS:
        path = project
        for part in name.split("/"):
            path = path / part
            recipes.refuse_link(path, project=project)
            if not strict_exists(path):
                effects.mkdir(path)
                created.append(path)
    return list(layout.PROJECT_FOLDERS)


def _refuse_constant(token):
    raise ValueError(f"{token} não é um número JSON válido.")


def lock_warnings(project) -> list:
    """Avisos de um projeto com `recipe.lock.json`: item achado hoje com outro sha256, sumido ou lock ilegível.

    Nunca levanta; projeto sem lock não tem aviso. O `template.lock.json` da versão de testes vira
    `OLD_SHAPE` com o que apagar à mão.
    """
    root = Path(project).expanduser().resolve()
    path = root / LOCK_FILE
    try:
        if strict_lexists(root / OLD_LOCK_FILE):
            renamed.old_shape(
                f"{OLD_LOCK_FILE} do projeto",
                (
                    f"Apague o {OLD_LOCK_FILE}; o vínculo com uma receita é o {LOCK_FILE}, "
                    "que init --recipe grava num projeto novo."
                ),
            )
        if not strict_lexists(path):
            return []
        if is_link(path) or path.stat().st_size > LOCK_MAX:
            raise ValueError("não é um arquivo comum")
        lock = validate_lock(json.loads(path.read_text(encoding="utf-8"), parse_constant=_refuse_constant))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        # O texto de um `OSError` traz o caminho absoluto do arquivo: só o motivo vai para o aviso.
        what = f"não pôde ser lido ({labels.reason(exc)})" if isinstance(exc, OSError) else f"ilegível ({exc})"
        return [labels.text(f"{LOCK_FILE} {what}; não dá para conferir os componentes da receita.")]
    out = []
    found = layout.info(project)
    declared = found.doc or {}
    owner = refs.recipe_parts(lock["recipe"])[0]
    if (declared.get("client"), declared.get("recipe")) != (owner, lock["recipe"]):
        out.append(f"{LOCK_FILE} é da receita {lock['recipe']}, diferente do project.json.")
    if found.problem:
        # Sem o cliente do project.json o nível `client` não resolve: conferir os itens só acusaria deriva falsa.
        return out
    for item in lock["items"]:
        warning = _item_warning(project, root, lock["recipe"], item)
        if warning:
            out.append(warning)
    return out


def _item_warning(project, root, recipe, item):
    """O aviso de um item do lock, ou `None` quando ele ainda é o do lock (tipo ou nível desconhecido: pulado)."""
    if not (
        open_enums.known("component_kind", item["kind"], assets.ASSET_KINDS)
        and open_enums.known("tier", item["tier"], TIERS)
    ):
        return None
    label = f'{item["kind"]} "{item["name"]}"'
    try:
        return _item_state(project, root, recipe, item, label)
    except OSError as exc:  # a folder the system will not open is not a missing file
        return labels.text(f"{label} da receita {recipe} não pôde ser lido ({labels.reason(exc)}).")


def _item_state(project, root, recipe, item, label):
    """`_item_warning` de um item conhecido; o `OSError` de uma pasta que não abre sobe para quem chamou."""
    if item["tier"] == "project" and not strict_lexists(root.joinpath(*item["path"].split("/"))):
        # A cópia do init sumiu: outro arquivo com o mesmo nome achado em outro lugar não é "mudou".
        return f"{label} da receita {recipe} sumiu de {item['path']} desde o init."
    try:
        found = assets.resolve(project, item["kind"], item["name"])
    except ValueError as exc:
        return labels.text(f"{label} da receita: {exc}")
    if found["status"] != "found":
        return f"{label} da receita {recipe} sumiu desde o init."
    if not _same_component(Path(found["path"]), item):
        return f"{label} mudou desde o init: o sha256 não é o do {LOCK_FILE} ({recipe})."
    return None


def _same_component(path, item):
    """O arquivo ainda é o do lock: tamanho e mtime iguais aos gravados pulam o sha256 (cache do `status`)."""
    try:
        seen = path.stat()
    # lenient: cache probe: a component it cannot stat skips the size cache, the sha256 decides
    except OSError:
        return False
    if (seen.st_size, seen.st_mtime_ns) == (item.get("size"), item.get("mtime_ns")):
        return True
    return recipes.sha256_file(path) == item["sha256"]


def record_lock_warnings(project):
    """`lock_warnings` como avisos `RECIPE_LOCK_DRIFT` do comando em curso."""
    for warning in lock_warnings(project):
        runtime.record_warning("RECIPE_LOCK_DRIFT", warning)
