"""Receitas do getbrolls: selar um projeto numa versão imutável, mostrar e listar as versões.

Uma receita mora na pasta do cliente, em `recipes/<slug>/<N>/`:

- `getbrolls-recipe.json` (`schema` `getbrolls.recipe/1`): cenas, trilhas, modelos de etapa,
  regras, identidade, estilo, nativos, proveniência e a lista `files`;
- `getbrolls-recipe.sha256`: o sha256 do json, fora dele, no formato do `sha256sum`;
- `components.json` (`schema` `getbrolls.components/1`): tipo, nome, arquivo e sha256 de cada componente;
- `assets/<tipo>/<arquivo>`: os bytes dos componentes.

A ref canônica é `cat:getbrolls/recipe/<dono>.<slug>@<N>`; o dono é o slug do cliente.

- **Imutável.** `seal_project` monta tudo numa pasta `.staging-<id>` ao lado, dá o nome `<N>`
  num `rename` só (sob `recipes/.lock`) e só então deixa os arquivos somente leitura (depois do
  rename, porque o Windows não renomeia pasta com arquivo somente leitura aberto por outro
  processo): primeiro os arquivos, depois as pastas (`0555`; para apagar uma versão, libere a escrita antes).
  Uma versão nunca é reescrita: mudar é selar `N+1`.
- **Só estrutura.** A receita leva títulos, diretivas de layout e camadas; a fala vira o marcador
  `{fala da cena}` e todo texto entre aspas vira `{texto}`. Diretiva de plugin fica de fora.
- **Licenças e aprovações nunca vão junto.** `.licenca.json`, `brolls/`, `broll/`, `aroll/`,
  clipes e revisões nunca entram; receita e componentes saem com `licence: "not_transferred"`.
- **Toda sobra é erro.** `files` lista cada arquivo da versão (fora o json e o selo), e só o
  `components.json` e os componentes: arquivo a mais, pasta a mais (até vazia), qualquer link (listado
  ou não), arquivo listado que falta ou que não é nem o `components.json` nem um componente deixam a
  versão não íntegra. Uma pasta que o sistema não deixa abrir é erro de E/S, não ausência: `show`
  levanta, `list` marca a versão com um `problem` (e um `problem_code`).
  Exceção: lixo do macOS (`.DS_Store`, `._<nome>` de um arquivo da versão) é aviso, nunca aberto nem hasheado.

Esta versão da CLI ainda não sela receita (`recipe` só tem `list` e `show`); `seal_project` é a
selagem que o passo futuro vai usar.
"""

import dataclasses
import errno
import hashlib
import json
import logging
import os
import posixpath
import uuid
from pathlib import Path

from . import (
    __version__,
    _paths,
    assets,
    clients,
    effects,
    labels,
    layout,
    logs,
    open_enums,
    refs,
    roteiro,
    runtime,
    versioning,
)
from ._paths import strict_is_dir, strict_is_file
from .errors import UsageError
from .ledger import existing_project_id
from .models import now
from .recipe_files import (  # noqa: F401 -- reexported: recipe_init and tests keep reading these here; pylint: disable=unused-import
    ASSETS,
    TEXT_MAX,
    _check_text,
    _file_name,
    _kind_folder,
    _nfc,
    _refused,
    _shown,
    check_slug,
    component_path,
    copy_hashed,
    refuse_link,
    safe_relative,
    sha256_file,
)
from .roteiro_frontmatter import fold, roteiro_path
from .roteiro_plan import full_role
from .sdk import schemas
from .sdk.files import is_link
from .sdk.jsonschema import errors

_log = logs.get("recipes")

RECIPE_FILE = "getbrolls-recipe.json"
# O sha256 do json mora FORA dele, ao lado, no formato do `sha256sum`: quem edita o json à mão
# não consegue manter a versão "íntegra" sem mexer aqui também.
SEAL_FILE = "getbrolls-recipe.sha256"
COMPONENTS_FILE = "components.json"
FIXED_FILES = (RECIPE_FILE, SEAL_FILE)
FAMILY = "recipe"
COMPONENTS_FAMILY = "components"
SEAL_MAX = 256
LOCK = ".lock"
STAGING_PREFIX = ".staging-"
SPEECH_PLACEHOLDER = "{fala da cena}"
TEXT_PLACEHOLDER = "{texto}"
NOT_TRANSFERRED = "not_transferred"
# De onde a receita nasceu: `project` (um projeto do getbrolls); `migrate` fica para a conversão da versão de testes.
PROVENANCE_KIND = "project"
STEPS = ("briefing", "roteiro", "storyboard", "frame", "checklist", "plano")
RENAME_ATTEMPTS = 5
LOCK_WAIT_S = 5.0
READ_ONLY = 0o444
# Pastas da versão selada: leitura e travessia, sem escrita (um Finder, um zip ou um sincronizador não solta lixo
# dentro). O Windows não apaga pasta marcada somente leitura, então lá a pasta continua gravável.
READ_ONLY_DIR = 0o555 if os.name != "nt" else 0o755
DS_STORE = ".DS_Store"
APPLEDOUBLE = "._"
RECIPE_MAX = 4 * 1024 * 1024
_LAYER_KIND = {"LETTERING": "lettering", "SFX": "sfx", "MUSICA": "musica", "COMP": "composicao"}
_BUSY = "Outro comando está mudando as receitas deste cliente. Aguarde terminar antes de repetir."


def recipes_root(owner):
    """`<pasta do cliente>/recipes/` do cliente registrado, conferida (nunca link)."""
    clients.load_client(owner)
    root = clients.client_root(owner)
    if root is None:  # load_client já recusou; só para o pyright
        raise ValueError(f'O cliente "{owner}" não está registrado.')
    clients.warn_old_shape(owner, root)
    return refuse_link(root / clients.RECIPES, client_root=root)


def recipe_dir(owner, slug, number) -> Path:
    """`<pasta do cliente>/recipes/<slug>/<N>/` (existindo ou não)."""
    refs.recipe_ref(owner, slug, number)
    return recipes_root(owner) / slug / str(number)


def _is_version_name(name):
    """O nome é um número de versão canônico (sem zero à esquerda, de 1 a `refs._VERSION_MAX`)?"""
    if not (name.isascii() and name.isdigit() and not name.startswith("0")):
        return False
    return int(name) <= refs._VERSION_MAX  # pylint: disable=protected-access


def _versions(slug_dir):
    """Os números de versão das pastas de `slug_dir`, em ordem; o que não é uma pasta de versão é ignorado.

    Um link com nome de versão não é versão (nem o `show` o abre): não conta para o próximo número.
    """
    if not strict_is_dir(slug_dir):
        return []
    return sorted(
        int(entry.name)
        for entry in slug_dir.iterdir()
        if _is_version_name(entry.name) and not is_link(entry) and strict_is_dir(entry)
    )


def next_version(owner, slug) -> int:
    """Próximo número livre de `slug` no cliente: o maior que existe + 1 (1 quando não há nenhum)."""
    check_slug(slug)
    versions = _versions(recipes_root(owner) / slug)
    return versions[-1] + 1 if versions else 1


def latest_version(owner, slug) -> int | None:
    """O maior número de versão de `slug` no cliente, ou `None` sem nenhuma."""
    check_slug(slug)
    versions = _versions(recipes_root(owner) / slug)
    return versions[-1] if versions else None


def resolve_ref(text, owner=None):
    """`(dono, slug, versão)` de uma ref de receita: o dono vem da ref ou de `owner`; sem `@N`, a última.

    Dono na ref diferente de `owner` é recusado; sem dono nenhum é erro de uso; `pessoal` ainda
    não tem pasta.
    """
    found, slug, number = refs.recipe_parts(text)
    if found and owner and found != owner:
        raise _refused(
            "lookup",
            "owner_mismatch",
            ValueError(
                f'A receita "{text}" é do cliente "{found}", não de "{owner}"; use uma receita do mesmo cliente.'
            ),
        )
    owner = found or owner
    if not owner:
        raise _refused("lookup", "owner_missing", UsageError(f"Diga de quem é a receita: --ref CLIENTE.{slug}[@N]."))
    if owner == refs.PERSONAL_OWNER:
        raise _refused(
            "lookup",
            "personal_owner",
            ValueError('Receita "pessoal.<slug>": por enquanto só há receitas na pasta de um cliente registrado.'),
        )
    clients.check_slug(owner)
    if number is None:
        number = latest_version(owner, slug)
        if number is None:
            raise _refused("lookup", "not_found", ValueError(f'O cliente {owner} não tem a receita "{slug}".'))
    return owner, slug, number


def _seal_owner(project, client):
    declared = layout.info(project).doc
    declared = declared.get("client") if declared else None
    if client and declared and client != declared:
        raise _refused(
            "seal",
            "client_mismatch",
            ValueError(f"--client {client} não é o cliente do project.json ({declared}); use o mesmo."),
        )
    owner = client or declared
    if not owner:
        raise _refused(
            "seal",
            "client_missing",
            ValueError("Diga de que cliente é a receita: --client SLUG (ou client no project.json)."),
        )
    clients.check_slug(owner)
    clients.load_client(owner)
    return owner


def _recipe_directive(directive):
    """A diretiva sem o conteúdo do projeto: todo argumento entre aspas vira `{texto}`."""
    quoted = directive.quoted or (False,) * len(directive.args)
    args = tuple(TEXT_PLACEHOLDER if flag else text for text, flag in zip(directive.args, quoted, strict=True))
    return roteiro.directive_text(dataclasses.replace(directive, args=args))


def _wanted(project, scene):
    """(tipo, nome) dos componentes que a cena cita: marca do `[FULL]` e as camadas."""
    found = []
    if full_role(project, scene.layout) == "marca":
        found.append(("marca", scene.layout.args[0]))
    for layer in scene.layers:
        if layer.kind == "LETTERING":
            if len(layer.args) > 1:
                found.append(("lettering", layer.args[1]))
            continue
        found.append((_LAYER_KIND[layer.kind], layer.args[0]))
    return found


def _scenes(project, parsed):
    """(cenas da receita, componentes pedidos): uma cena por cena do roteiro, sem a fala."""
    scenes, wanted = [], []
    for index, scene in enumerate(parsed.scenes, start=1):
        scenes.append(
            {
                "id": f"s{index:02d}",
                "title": scene.title,
                "role": None,
                "layout_id": None,
                "layout": _recipe_directive(scene.layout),
                "layers": [_recipe_directive(layer) for layer in scene.layers],
            }
        )
        wanted.extend(_wanted(project, scene))
    return scenes, wanted


def _collect(project, wanted, warnings):
    """[(tipo, nome, arquivo)] dos componentes achados, sem repetir; pendente vira aviso."""
    rows, seen = [], set()
    for kind, name in wanted:
        key = (kind, fold(_nfc(name)))
        if key in seen:
            continue
        seen.add(key)
        try:
            found = assets.resolve(project, kind, name)
        except ValueError as exc:
            raise _refused(
                "seal", "component_error", ValueError(f'Componente {kind} "{name}": {exc}'), kind=kind
            ) from None
        if found["status"] != "found":
            warnings.append(f'{kind} "{name}" não foi achado: a receita não leva esse componente.')
            continue
        rows.append((kind, name, Path(found["path"])))
    return rows


def _stage_components(staging, rows):
    items = []
    for kind, name, path in rows:
        rel = f"{ASSETS}/{_kind_folder(kind)}/{_file_name(name, path)}"
        safe_relative(rel, kind)
        target = staging.joinpath(*rel.split("/"))
        effects.mkdir(target.parent, parents=True, exist_ok=True)
        # `staging` is `<cliente>/recipes/<slug>/.staging-…`.
        sha = copy_hashed(path, target, client_root=staging.parent.parent.parent)
        items.append({"kind": kind, "name": name, "file": rel, "sha256": sha, "licence": NOT_TRANSFERRED})
    return items


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def _validated(doc, family, label):
    """Confere `doc` contra o schema publicado da família; devolve o próprio `doc` ou `ValueError`."""
    if not isinstance(doc, dict):
        raise ValueError(  # noqa: TRY004 - contrato da CLI: dado inválido é ValueError (INVALID_DATA)
            f"{label} tem que ser um objeto JSON."
        )
    versioning.read_schema(doc, family, schemas.version(family), label=label)
    problems = errors(doc, schemas.load(family))
    if problems:
        raise ValueError(f"{label} fora do schema: " + "; ".join(problems[:5]))
    return doc


def validate_recipe(doc, label=RECIPE_FILE):
    """`getbrolls-recipe.json` conferido contra `getbrolls.recipe/1`; `ValueError` senão."""
    return _validated(doc, FAMILY, label)


def validate_components(doc, label=COMPONENTS_FILE):
    """`components.json` conferido contra `getbrolls.components/1`; `ValueError` senão."""
    return _validated(doc, COMPONENTS_FAMILY, label)


def write_new(path, text):
    """Cria `path` com `text` (UTF-8, `\\n`), sem nunca sobrescrever, e força o disco."""
    with effects.op("fs.write", path), path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _write_components(staging, owner, items):
    """Grava `components.json` na versão em montagem; devolve o ponteiro `{file, sha256}` da receita."""
    doc = validate_components(
        versioning.stamp_schema(
            {"scope": "recipe", "owner": owner, "items": items}, COMPONENTS_FAMILY, schemas.version(COMPONENTS_FAMILY)
        )
    )
    text = _dump(doc)
    write_new(staging / COMPONENTS_FILE, text)
    return {"file": COMPONENTS_FILE, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _seal_text(digest):
    return f"{digest}  {RECIPE_FILE}\n"


def _publish(staging, slug_dir, doc):
    """Dá o nome `<N>` à pasta de staging, tentando `N+1` quando o número já existe."""
    number = doc["version"]
    for _ in range(RENAME_ATTEMPTS):
        target = slug_dir / str(number)
        # lenient: os.path.lexists is lenient on every Python; the rename below raises on a locked folder
        if not os.path.lexists(target):
            doc = {**doc, "version": number, "ref": refs.recipe_ref(doc["owner"], doc["slug"], number)}
            validate_recipe(doc)
            text = _dump(doc)
            for name in FIXED_FILES:
                effects.unlink(staging / name, missing_ok=True)
            write_new(staging / RECIPE_FILE, text)
            write_new(staging / SEAL_FILE, _seal_text(hashlib.sha256(text.encode("utf-8")).hexdigest()))
            try:
                # Número já tomado por outra selagem: o seguinte é tentado, não é erro.
                effects.rename(staging, target, fallback=(FileExistsError, errno.EEXIST, errno.ENOTEMPTY))
            except OSError:
                # lenient: os.path.lexists is lenient on every Python; the rename below raises on a locked folder
                if not os.path.lexists(target):
                    raise
            else:
                return target, doc
        number += 1
    raise _refused(
        "seal",
        "no_free_number",
        ValueError(f"Não achei um número livre para a receita {doc['slug']} depois de {RENAME_ATTEMPTS} tentativas."),
    )


def _freeze_files(folder, warnings):
    """Deixa cada arquivo da versão somente leitura e depois cada pasta (a de baixo primeiro), a da versão por
    último; falha vira aviso (a versão já está publicada), um por caminho.

    Pastas depois dos arquivos e de baixo para cima: fechar uma pasta primeiro tiraria a travessia do que está dentro.
    O caminho que já falhou na passada dos arquivos (não dá nem para ver o que é) não é tentado de novo na das pastas.
    """
    paths = sorted(folder.rglob("*"))
    failed = {path for path in paths if not _freeze(path, folder, warnings, READ_ONLY, strict_is_file)}
    for path in [*reversed(paths), folder]:
        if path not in failed:
            _freeze(path, folder, warnings, READ_ONLY_DIR, strict_is_dir)


def _freeze(path, folder, warnings, mode, is_kind):
    """`chmod` de `path` para `mode` se for do tipo `is_kind` (arquivo ou pasta, nunca link); falha vira aviso.

    Devolve `False` quando falhou.
    """
    try:
        # lenient: link test; the strict test on the same line raises inside the try
        if not path.is_symlink() and is_kind(path):
            path.chmod(mode)
    except OSError:
        name = path.relative_to(folder).as_posix() if path != folder else "<versão>"
        warnings.append(f"{name}: não consegui deixar somente leitura.")
        return False
    return True


def _draft(project, slug, owner, parsed, extra):
    """`getbrolls-recipe.json` ainda sem número, cenas, componentes nem `files`; `extra` traz title e by."""
    declared = layout.info(project).doc or {}
    return versioning.stamp_schema(
        {
            "ref": refs.recipe_ref(owner, slug, 1),
            "owner": owner,
            "slug": slug,
            "version": 1,
            "title": extra["title"],
            "genero": parsed.meta["genero"],
            "aspecto": parsed.meta["aspecto"],
            "canvas": declared.get("canvas"),
            "fps": declared.get("fps"),
            "scenes": [],
            "tracks": [],
            "steps": dict.fromkeys(STEPS),
            "rules": None,
            "components": None,
            "identity": None,
            "style": None,
            "provenance": {
                "from": {"kind": PROVENANCE_KIND, "foreign": False},
                "project_id": existing_project_id(project),
                "sealed_at": now(),
                "getbrolls_version": __version__,
                "roteiro_sha256": hashlib.sha256(roteiro_path(project).read_bytes()).hexdigest(),
                "by": extra["by"],
            },
            "files": [],
            "licence": NOT_TRANSFERRED,
        },
        FAMILY,
        schemas.version(FAMILY),
    )


def _write_version(doc, rows):
    """Monta a versão em staging e publica como `<N>` sob `recipes/.lock`; devolve (pasta, doc, itens)."""
    root = recipes_root(doc["owner"])
    effects.mkdir(root, exist_ok=True)
    slug_dir = refuse_link(root / doc["slug"], client_root=root.parent)
    with runtime.exclusive_lock(root / LOCK, _BUSY, wait_s=LOCK_WAIT_S):
        effects.mkdir(slug_dir, exist_ok=True)
        refuse_link(slug_dir, client_root=root.parent)
        doc = {**doc, "version": next_version(doc["owner"], doc["slug"])}
        staging = slug_dir / f"{STAGING_PREFIX}{uuid.uuid4().hex[:8]}"
        effects.mkdir(staging)
        try:
            items = _stage_components(staging, rows)
            pointer = _write_components(staging, doc["owner"], items)
            files = sorted([COMPONENTS_FILE, *(item["file"] for item in items)])
            folder, doc = _publish(staging, slug_dir, {**doc, "components": pointer, "files": files})
        except BaseException:
            effects.rmtree(staging, ignore_errors=True)
            raise
    return folder, doc, items


# pylint: disable-next=too-many-locals  # one name per step of the seal
def seal_project(project, slug, *, client=None, title=None, by=None) -> dict:
    """Sela o roteiro e os componentes de `project` em `recipes/<slug>/<N>/` do cliente."""
    project = Path(project).expanduser().resolve()
    check_slug(slug)
    extra = {"title": _check_text(title, "--title"), "by": _check_text(by, "--by")}
    owner = _seal_owner(project, client)
    parsed = roteiro.parse(roteiro.load_text(project))
    warnings = []
    scenes, wanted = _scenes(project, parsed)
    rows = _collect(project, wanted, warnings)
    folder, doc, items = _write_version({**_draft(project, slug, owner, parsed, extra), "scenes": scenes}, rows)
    _freeze_files(folder, warnings)
    for warning in warnings:
        runtime.record_warning("RECIPE_FREEZE", warning)
    logs.event(
        _log,
        logging.INFO,
        "recipe_sealed",
        projeto_id=existing_project_id(project),
        version=doc["version"],
        scenes=len(doc["scenes"]),
        components=len(items),
        warnings=len(warnings),
    )
    count = f"{len(doc['scenes'])} cena(s), {len(items)} componente(s)"
    use = _paths.cli_hint(
        "init",
        "--client",
        owner,
        "--recipe",
        f"{slug}@{doc['version']}",
        "--slug",
        "SLUG_DO_PROJETO",
        "--tema",
        "TEMA",
    )
    return {
        "ref": doc["ref"],
        "owner": owner,
        "version": doc["version"],
        "path": _shown(folder),
        "scenes": len(doc["scenes"]),
        "components": [{key: item[key] for key in ("kind", "name", "file", "sha256")} for item in items],
        "licences_transferred": False,
        "summary": {
            "line": (
                f"Receita {doc['ref']} selada no cliente {owner}: {count}, sem fala, licença nem aprovação. "
                f"Para usar: {use}."
            )
        },
    }


def _refuse_constant(token):
    raise ValueError(f"{token} não é um número JSON válido.")


def _read_json(path, label, family):
    """(doc, bytes, código, problema) de um JSON da versão, sem seguir link.

    O código do problema é `unreadable` (não é arquivo comum, grande demais, não é JSON) ou `schema` (JSON fora do
    schema ou de outra versão). Só levanta (`OSError`) quando uma pasta do caminho não deixa o sistema olhar: isso
    é erro de E/S, não ausência.
    """
    if is_link(path) or not strict_is_file(path):  # a folder that cannot be read raises: it is not "absent"
        return None, None, "unreadable", f"{label} não é um arquivo comum."
    try:
        if path.stat().st_size > RECIPE_MAX:
            return None, None, "unreadable", f"{label} é grande demais."
        raw = path.read_bytes()
        doc = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
        _validated(doc, family, label)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None, "unreadable", f"{label} não é um JSON legível."
    except ValueError as exc:
        return None, None, "schema", str(exc)
    return doc, raw, None, None


def _seal_check(folder, raw):
    """`(estado, problema)` do selo da versão: `ok`, `missing`, `unreadable` ou `mismatch`."""
    path = folder / SEAL_FILE
    unproven = f"não dá para provar que o {RECIPE_FILE} é o selado."
    if is_link(path) or not strict_is_file(path):
        return "missing", f"{SEAL_FILE} não existe (ou não é um arquivo comum): {unproven}"
    try:
        if path.stat().st_size > SEAL_MAX:
            raise ValueError
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError, ValueError):
        return "unreadable", f"{SEAL_FILE} ilegível: {unproven}"
    if text != _seal_text(hashlib.sha256(raw).hexdigest()):
        return "mismatch", f"{RECIPE_FILE} mudou depois da selagem: o sha256 não é o do {SEAL_FILE}."
    return "ok", None


def _identity_problems(doc, owner, slug, number):
    problems = []
    if doc["owner"] != owner:
        problems.append(f'{RECIPE_FILE} é do cliente "{doc["owner"]}", não de "{owner}".')
    if doc["slug"] != slug:
        problems.append(f'{RECIPE_FILE} é da receita "{doc["slug"]}", não de "{slug}".')
    if doc["version"] != number:
        problems.append(f"{RECIPE_FILE} diz versão {doc['version']}, mas a pasta é a {number}.")
    expected = refs.recipe_ref(owner, slug, number)
    if doc["ref"] != expected:
        problems.append(f"{RECIPE_FILE} traz a ref {doc['ref']}, não {expected}.")
    return problems


def _components_problems(folder, doc):
    """(itens usados, arquivos citados, problemas): o ponteiro da receita, o schema e o sha256 de cada arquivo.

    Todo arquivo citado tem o caminho e o sha256 conferidos, seja qual for o tipo. Item de tipo que esta versão não
    conhece (`ENUM_UNKNOWN`) só não vai para os itens usados (e dá o aviso): o selo cobre a versão inteira, e o
    arquivo continua contando como citado. Os arquivos citados são `None` quando o `components.json` não pôde
    ser lido.
    """
    components, raw, _, problem = _read_json(folder / COMPONENTS_FILE, COMPONENTS_FILE, COMPONENTS_FAMILY)
    # `_read_json` devolve `raw` e `components` juntos, ou `problem`; o teste em `None` é para o pyright.
    if components is None or raw is None:
        return [], None, [problem or f"{COMPONENTS_FILE} não é um JSON legível."]
    problems, used, listed = [], [], set()
    files = {_nfc(rel) for rel in doc["files"]}
    if hashlib.sha256(raw).hexdigest() != doc["components"]["sha256"]:
        problems.append(f"{COMPONENTS_FILE} mudou: o sha256 não é o do {RECIPE_FILE}.")
    if components["owner"] != doc["owner"]:
        problems.append(f'{COMPONENTS_FILE} é do cliente "{components["owner"]}", não de "{doc["owner"]}".')
    for item in components["items"]:
        rel = _nfc(item["file"])
        if rel in listed:
            problems.append(f"{rel} aparece duas vezes em {COMPONENTS_FILE}.")
            continue
        listed.add(rel)
        if open_enums.known("component_kind", item["kind"], assets.ASSET_KINDS):
            used.append(item)
        if rel not in files:
            problems.append(f"{rel} não está na lista files do {RECIPE_FILE}.")
        try:
            path = component_path(folder, rel, item["kind"])
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if sha256_file(path) != item["sha256"]:
            problems.append(f"{rel} mudou: o sha256 não bate com o {COMPONENTS_FILE}.")
    return used, listed, problems


def _raise(exc):
    """`onerror` do `os.walk`: uma pasta que não abre é erro de E/S (o padrão é pular a pasta em silêncio).

    Exceto a que sumiu no meio da varredura (`ENOENT`, `ENOTDIR`): isso é uma corrida, não um erro de E/S, e o
    que estava nela aparece como "está em files mas não existe".
    """
    if exc.errno not in (errno.ENOENT, errno.ENOTDIR):
        raise exc


def _listed_folders(listed):
    """As pastas por onde passam os arquivos de `files` (`assets`, `assets/<tipo>`): as únicas que a versão pode ter."""
    return {"/".join(rel.split("/")[:end]) for rel in listed for end in range(1, rel.count("/") + 1)}


def _tree_problems(folder, doc, component_files):
    """(problemas, avisos) do que a versão tem a mais ou a menos que `files`: arquivo, pasta, link, listado que
    falta ou sobra. Os caminhos são comparados em NFC dos dois lados.

    `component_files` são os arquivos que o `components.json` cita (`None` quando ele não pôde ser lido: aí
    não dá para dizer quais arquivos listados são componentes). Lixo do sistema (`_clutter`) é aviso.
    """
    problems, warnings, seen, taken = [], [], set(), set()
    listed = {_nfc(rel) for rel in doc["files"]}
    wanted = _listed_folders(listed)
    for current, dirs, names in os.walk(folder, onerror=_raise):
        for name in [*dirs, *names]:
            path = Path(current) / name
            rel = _nfc(path.relative_to(folder).as_posix())
            if rel in taken:
                problems.append(f"dois nomes iguais em NFC: {rel}")
                continue
            taken.add(rel)
            if is_link(path):
                seen.add(rel)
                problems.append(f"{rel} é um link dentro da receita.")
            elif name in dirs:
                if rel not in wanted:
                    problems.append(f"{rel} é uma pasta a mais, que o {RECIPE_FILE} não lista.")
            else:
                seen.add(rel)
                if _clutter(rel, listed):
                    warnings.append(f"{rel} é lixo do sistema; a receita o ignora.")
                else:
                    problems += _file_problem(rel, listed, component_files)
        dirs[:] = [d for d in dirs if not is_link(Path(current) / d)]
    problems += [f"{rel} está em files mas não existe na receita." for rel in sorted(listed - seen)]
    return problems, warnings


def _clutter(rel, listed):
    """`rel` é lixo que o macOS solta numa pasta: um `.DS_Store`, ou o `._<nome>` (AppleDouble) de um arquivo da versão?

    Nunca é aberto nem entra no sha256; qualquer outro item a mais continua sendo problema.
    """
    folder, name = posixpath.split(rel)
    if rel in listed or rel in FIXED_FILES:
        return False
    owner = posixpath.join(folder, name.removeprefix(APPLEDOUBLE))
    return name == DS_STORE or (name.startswith(APPLEDOUBLE) and (owner in listed or owner in FIXED_FILES))


def _file_problem(rel, listed, component_files):
    """Os problemas de um arquivo da versão: não listado, ou listado sem ser o `components.json` nem componente."""
    if rel not in listed:
        return [] if rel in FIXED_FILES else [f"{rel} é um arquivo a mais, que o {RECIPE_FILE} não lista."]
    if rel == COMPONENTS_FILE or component_files is None or rel in component_files:
        return []
    return [f"{rel} está em files mas não é o {COMPONENTS_FILE} nem um componente."]


def _doc_problems(folder, doc, identity):
    """(componentes usados, problemas, avisos) de um `getbrolls-recipe.json` lido: identidade, componentes, árvore."""
    items, cited, problems = _components_problems(folder, doc)
    tree, warnings = _tree_problems(folder, doc, cited)
    return items, _identity_problems(doc, *identity) + problems + tree, warnings


def _seal_problems(folder, raw, number):
    """Os problemas do selo da versão (nenhum, ou um), registrando o resultado no log."""
    state, sealed = _seal_check(folder, raw)
    logs.event(
        _log,
        logging.INFO if state == "ok" else logging.WARNING,
        "recipe_seal",
        result=state,
        version=number,
        sha256=hashlib.sha256(raw).hexdigest()[:12],
    )
    return [sealed] if sealed else []


def show(ref, owner=None) -> dict:
    """A versão da receita, com a integridade conferida arquivo por arquivo.

    `ref` é `<dono>.<slug>[@N]`, `<slug>[@N]` com `owner`, ou a ref canônica; sem `@N`, a última versão.
    """
    owner, slug, number = resolve_ref(ref, owner)
    folder = recipe_dir(owner, slug, number)
    for path in (folder.parent, folder):
        # `folder` is `<cliente>/recipes/<slug>/<N>`.
        refuse_link(path, client_root=folder.parent.parent.parent)
    canonical = refs.recipe_ref(owner, slug, number)
    if not strict_is_dir(folder):
        raise _refused("show", "not_found", ValueError(f"A receita {canonical} não existe no cliente {owner}."))
    doc, raw, _, problem = _read_json(folder / RECIPE_FILE, RECIPE_FILE, FAMILY)
    problems = [problem] if problem else []
    if raw is not None:
        problems += _seal_problems(folder, raw, number)
    items, warnings = [], []
    if doc is not None:
        items, found, warnings = _doc_problems(folder, doc, (owner, slug, number))
        problems += found
    for warning in warnings:
        runtime.record_warning("RECIPE_CLUTTER", warning)
    logs.event(
        _log,
        logging.INFO if not problems else logging.WARNING,
        "recipe_check",
        version=number,
        intact=not problems,
        problems=len(problems),
        warnings=len(warnings),
        components=len(items) if doc else None,
    )
    return {
        "ref": canonical,
        "owner": owner,
        "path": _shown(folder),
        "recipe": doc,
        "components": items,
        "recipe_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
        "intact": not problems,
        "problems": problems,
        "warnings": warnings,
    }


def _client_rows(owner):
    """(linhas, problemas) das versões do cliente; cada problema é `(texto, código)`.

    O que não dá para ler vira problema, nunca derruba a lista: uma pasta travada (`io_error`), uma receita que é
    link (`link`) e a pasta do cliente que está quebrada ou indisponível (`unavailable`).
    """
    try:
        root = recipes_root(owner)
    except ValueError as exc:
        return [], [(labels.text(exc), "unavailable")]
    except OSError as exc:  # the client folder itself is locked: `client list` shows it as a problem row too
        return [], [(f"A pasta do cliente {owner} não pôde ser lida: {labels.reason(exc)}.", "io_error")]
    try:
        slug_dirs = sorted(root.iterdir()) if strict_is_dir(root) else []
    except OSError as exc:
        return [], [(f"A pasta {clients.RECIPES} não pôde ser lida: {labels.reason(exc)}.", "io_error")]
    rows, problems = [], []
    for slug_dir in slug_dirs:
        name = slug_dir.name
        if name.startswith(".") or not _valid_slug(name):
            continue
        if is_link(slug_dir):
            problems.append((f'A receita "{name}" é um link; o getbrolls não lê receitas através de links.', "link"))
            continue
        try:
            if not strict_is_dir(slug_dir):
                continue
            numbers = _versions(slug_dir)
            linked = _link_rows(owner, name, slug_dir)
        except OSError as exc:  # one locked recipe is a labelled problem of its own, not the end of the list
            problems.append((f'A receita "{name}" não pôde ser lida: {labels.reason(exc)}.', "io_error"))
            continue
        rows += [_version_row(owner, name, slug_dir, number) for number in numbers]
        rows += linked
    return rows, problems


def _valid_slug(name):
    try:
        check_slug(name)
    except ValueError:
        return False
    return True


def _link_rows(owner, name, slug_dir):
    """As linhas de `list` das versões de `slug_dir` que são link: sem título, com o problema `link`."""
    return [
        {
            "owner": owner,
            "slug": name,
            "version": int(entry.name),
            "ref": refs.recipe_ref(owner, name, int(entry.name)),
            "title": None,
            "problem": "A pasta da versão é um link; o getbrolls não lê receitas através de links.",
            "problem_code": "link",
        }
        for entry in slug_dir.iterdir()
        if _is_version_name(entry.name) and is_link(entry)
    ]


def _version_row(owner, name, slug_dir, number):
    """A linha de `list` de uma versão: título e o problema (texto e código) de ler o `getbrolls-recipe.json`."""
    try:
        doc, _, code, problem = _read_json(slug_dir / str(number) / RECIPE_FILE, RECIPE_FILE, FAMILY)
    except OSError as exc:  # one locked version is a labelled problem of its own, not the end of the list
        doc, code, problem = None, "io_error", f"{RECIPE_FILE} não pôde ser lido: {labels.reason(exc)}."
    return {
        "owner": owner,
        "slug": name,
        "version": number,
        "ref": refs.recipe_ref(owner, name, number),
        "title": doc.get("title") if doc else None,
        "problem": problem,
        "problem_code": code,
    }


def list_recipes() -> dict:
    """Todas as versões de receita dos clientes registrados, sem rehash (ordem: cliente, slug, versão).

    `show` é quem confere a integridade de uma versão.
    """
    rows, broken = [], []
    for entry in clients.load_registry()["clients"]:
        found, problems = _client_rows(entry["slug"])
        rows.extend(found)
        for code in dict.fromkeys(code for _, code in problems):  # one entry per kind of problem, in order
            text = " ".join(problem for problem, kind in problems if kind == code)
            broken.append({"owner": entry["slug"], "problem": text, "problem_code": code})
    rows.sort(key=lambda row: (row["owner"], row["slug"], row["version"]))
    return {"recipes": rows, "clients_with_problems": broken}


# -- CLI ----------------------------------------------------------------------


def run(args):
    """`recipe --action list|show [--ref R]` (os dois só leem)."""
    if args.action == "list":
        return list_recipes()
    if not getattr(args, "ref", None):
        raise UsageError("--ref é obrigatório em recipe --action show.")
    return show(args.ref)
