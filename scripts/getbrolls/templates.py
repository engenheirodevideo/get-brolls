"""Templates de cliente: congelar um projeto numa versão imutável e criar projetos a partir dela.

Um template mora na pasta do cliente, em `templates/<slug>/<N>/`: `template.json`
(`schema` `getbrolls.template/1`) e `components/<tipo>/<arquivo>`, os bytes dos
componentes que o roteiro cita. A ref canônica é `cat:getbrolls/template/<slug>@<N>`.

- **Imutável.** `freeze` monta tudo numa pasta `.staging-<id>` ao lado, dá o nome `<N>`
  num `rename` só (sob `templates/.lock`) e só então deixa os arquivos somente leitura
  (depois do rename, porque o Windows não renomeia pasta com arquivo somente leitura
  aberto por outro processo). Uma versão nunca é reescrita: mudar é congelar `N+1`.
- **Só estrutura.** O template leva títulos, diretivas de layout e camadas; a fala vira
  o marcador `{fala da cena}` e todo texto entre aspas vira `{texto}`. Diretiva de
  plugin fica de fora (depende do plugin habilitado em cada máquina).
- **Licenças e aprovações nunca vão junto.** `.licenca.json`, `brolls/`, `broll/`,
  `aroll/`, clipes e revisões nunca entram; cada componente sai com
  `licence: "not_transferred"`.
"""

import contextlib
import dataclasses
import hashlib
import json
import os
import shutil
import unicodedata
import uuid
from pathlib import Path

from . import __version__, assets, clients, layout, refs, roteiro, runtime, versioning
from .ledger import existing_project_id
from .models import now
from .roteiro_frontmatter import fold, roteiro_path
from .roteiro_plan import full_role
from .sdk import schemas
from .sdk.files import is_link
from .sdk.jsonschema import errors

TEMPLATE_FILE = "template.json"
# O sha256 do `template.json` mora FORA dele, ao lado, no formato do `sha256sum`: quem
# edita o template.json à mão não consegue manter a versão "íntegra" sem mexer aqui também.
SEAL_FILE = "template.sha256"
SEAL_MAX = 256
LOCK_FILE = "template.lock.json"
FAMILY = "template"
LOCK_FAMILY = "template_lock"
SUPPORTED = 1
LOCK = ".lock"
STAGING_PREFIX = ".staging-"
COMPONENTS = "components"
SPEECH_PLACEHOLDER = "{fala da cena}"
TEXT_PLACEHOLDER = "{texto}"
NOT_TRANSFERRED = "not_transferred"
RENAME_ATTEMPTS = 5
LOCK_WAIT_S = 5.0
TEXT_MAX = 200
READ_ONLY = 0o444
TEMPLATE_MAX = 4 * 1024 * 1024
_CHUNK = 1024 * 1024
_COMPONENT_DEPTH = 3  # components/<tipo>/<arquivo>
_LAYER_KIND = {"LETTERING": "lettering", "SFX": "sfx", "MUSICA": "musica", "COMP": "composicao"}
_BUSY = "Outro comando está mudando os templates deste cliente. Aguarde terminar antes de repetir."


def _shown(path):
    return runtime.scrub_home(str(path))


def _check_text(value, flag):
    """Texto opcional de uma linha (`--title`, `--by`); `None` fica `None`."""
    if value is None:
        return None
    text = unicodedata.normalize("NFC", value).strip() if isinstance(value, str) else ""
    if not text or len(text) > TEXT_MAX or any(unicodedata.category(char) == "Cc" for char in text):
        raise ValueError(f"{flag} tem que ser texto de uma linha, de 1 a {TEXT_MAX} caracteres.")
    return text


def check_slug(slug):
    """Slug de template válido (a regra de slug de cliente); `ValueError` senão."""
    refs.template_ref(slug, 1)
    return slug


def engine_ref(text):
    """Ref `cat:` canônica de receita de motor; `ValueError` para outra coisa, inclusive um template."""
    ref = refs.parse(text)
    if ref.parts[:2] == (refs.TEMPLATE_ENGINE, refs.TEMPLATE_TYPE):
        raise ValueError(f'Referência "{text}": --engine-ref é receita de motor, nunca outro template do get-brolls.')
    return refs.format_ref(ref)


def _no_link(path):
    if is_link(path):
        raise ValueError(f"{_shown(path)} é um link; o get-brolls não lê nem grava templates através de links.")
    return path


def templates_root(client_slug):
    """`<pasta do cliente>/templates/` do cliente registrado, conferida (nunca link)."""
    clients.load_client(client_slug)
    root = clients.client_root(client_slug)
    if root is None:  # load_client já recusou; só para o pyright
        raise ValueError(f'O cliente "{client_slug}" não está registrado.')
    return _no_link(root / clients.TEMPLATES)


def template_dir(client_slug, slug, n) -> Path:
    """`<pasta do cliente>/templates/<slug>/<N>/` (existindo ou não)."""
    refs.template_ref(slug, n)
    return templates_root(client_slug) / slug / str(n)


def _versions(slug_dir):
    if not slug_dir.is_dir():
        return []
    found = []
    for entry in slug_dir.iterdir():
        name = entry.name
        if name.isascii() and name.isdigit() and not name.startswith("0"):
            found.append(int(name))
    return sorted(found)


def next_version(client_slug, slug) -> int:
    """Próximo número livre de `slug` no cliente: o maior que existe + 1 (1 quando não há nenhum)."""
    check_slug(slug)
    versions = _versions(templates_root(client_slug) / slug)
    return versions[-1] + 1 if versions else 1


# -- freeze -------------------------------------------------------------------


def _freeze_client(project, client):
    declared = layout.info(project).doc
    declared = declared.get("client") if declared else None
    if client and declared and client != declared:
        raise ValueError(f"--client {client} não é o cliente do project.json ({declared}); use o mesmo.")
    slug = client or declared
    if not slug:
        raise ValueError("Diga de que cliente é o template: --client <slug> (ou client no project.json).")
    clients.check_slug(slug)
    clients.load_client(slug)
    return slug


def _template_directive(directive):
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


def _slots(project, parsed):
    slots, wanted = [], []
    for index, scene in enumerate(parsed.scenes, start=1):
        slots.append(
            {
                "id": f"s{index:02d}",
                "title": scene.title,
                "layout": _template_directive(scene.layout),
                "layers": [_template_directive(layer) for layer in scene.layers],
                "placeholder": SPEECH_PLACEHOLDER,
            }
        )
        wanted.extend(_wanted(project, scene))
    return slots, wanted


def _collect(project, wanted, warnings):
    """[(tipo, nome, arquivo)] dos componentes achados, sem repetir; pendente vira aviso."""
    rows, seen = [], set()
    for kind, name in wanted:
        key = (kind, fold(unicodedata.normalize("NFC", name)))
        if key in seen:
            continue
        seen.add(key)
        try:
            found = assets.resolve(project, kind, name)
        except ValueError as exc:
            raise ValueError(f'Componente {kind} "{name}": {exc}') from None
        if found["status"] != "found":
            warnings.append(f'{kind} "{name}" não foi achado: o template não leva esse componente.')
            continue
        rows.append((kind, name, Path(found["path"])))
    return rows


def _file_name(name, path):
    """Nome do arquivo no template: o do componente, que continua achado pelo mesmo nome."""
    if fold(unicodedata.normalize("NFC", path.stem)) == fold(unicodedata.normalize("NFC", name)):
        return unicodedata.normalize("NFC", path.name)
    return f"{unicodedata.normalize('NFC', name).strip()}{path.suffix.lower()}"


def copy_hashed(source, target, expected=None):
    """Copia `source` para `target` novo (nunca sobrescreve) e devolve o sha256 dos bytes copiados.

    Com `expected`, os bytes que passaram precisam ter esse sha256: senão o arquivo
    novo é apagado e `ValueError` diz qual mudou.
    """
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            while chunk := reader.read(_CHUNK):
                digest.update(chunk)
                writer.write(chunk)
    except FileExistsError:
        raise ValueError(f"{_shown(target)} já existe; nada foi sobrescrito.") from None
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    value = digest.hexdigest()
    if expected is not None and value != expected:
        target.unlink(missing_ok=True)
        raise ValueError(f"{source.name} mudou durante a cópia (sha256 diferente do template); nada foi copiado.")
    return value


def _stage_components(staging, rows):
    out = []
    for kind, name, path in rows:
        rel = f"{COMPONENTS}/{_kind_folder(kind)}/{_file_name(name, path)}"
        safe_relative(rel, kind)
        target = staging.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        sha = copy_hashed(path, target)
        out.append({"kind": kind, "name": name, "file": rel, "sha256": sha, "licence": NOT_TRANSFERRED})
    return out


def _dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def validate_template(doc, label=TEMPLATE_FILE):
    """Confere `doc` contra o schema publicado; devolve o próprio `doc` ou `ValueError`."""
    if not isinstance(doc, dict):
        raise ValueError(f"{label} tem que ser um objeto JSON.")
    versioning.read_schema(doc, FAMILY, SUPPORTED, label=label)
    problems = errors(doc, schemas.load(FAMILY))
    if problems:
        raise ValueError(f"{label} fora do schema: " + "; ".join(problems[:5]))
    return doc


def _write_file(path, text):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _publish(staging, slug_dir, doc):
    """Dá o nome `<N>` à pasta de staging, tentando `N+1` quando o número já existe."""
    number = doc["version"]
    for _ in range(RENAME_ATTEMPTS):
        target = slug_dir / str(number)
        if not os.path.lexists(target):
            doc = {**doc, "version": number, "ref": refs.template_ref(doc["slug"], number)}
            validate_template(doc)
            text = _dump(doc)
            for name in (TEMPLATE_FILE, SEAL_FILE):
                (staging / name).unlink(missing_ok=True)
            _write_file(staging / TEMPLATE_FILE, text)
            _write_file(staging / SEAL_FILE, _seal_text(hashlib.sha256(text.encode("utf-8")).hexdigest()))
            try:
                staging.rename(target)
            except OSError:
                if not os.path.lexists(target):
                    raise
            else:
                return target, doc
        number += 1
    raise ValueError(f"Não achei um número livre para o template {doc['slug']} depois de {RENAME_ATTEMPTS} tentativas.")


def _freeze_files(folder, warnings):
    """Deixa cada arquivo da versão somente leitura; falha vira aviso (a versão já está publicada)."""
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink()):
        try:
            path.chmod(READ_ONLY)
        except OSError:
            warnings.append(f"{path.relative_to(folder).as_posix()}: não consegui deixar somente leitura.")


def _kind_folder(kind):
    return assets.ASSET_KINDS[kind].folder.removeprefix("assets/")


def safe_relative(rel, kind=None):
    """Caminho relativo seguro de componente (`components/<pasta do tipo>/<arquivo>`); `ValueError` senão.

    O arquivo nunca começa com `.` nem termina em `.licenca.json` (uma licença nunca viaja
    no template) e, com `kind`, a pasta tem que ser a do tipo (`sfx` só em `components/sfx/`).
    """
    from .analysis_contract import Invalid, relative

    unsafe = ValueError(f'Caminho de componente inseguro no template: "{rel}".')
    try:
        parts = relative(rel)
    except Invalid:
        raise unsafe from None
    folders = {_kind_folder(name) for name, spec in assets.ASSET_KINDS.items() if spec.personal}
    if len(parts) != _COMPONENT_DEPTH or parts[0] != COMPONENTS or parts[1] not in folders:
        raise unsafe
    name = parts[2]
    if name.startswith(".") or name.lower().endswith(assets.LICENSE_SUFFIX):
        raise unsafe
    if kind is not None and (kind not in assets.ASSET_KINDS or parts[1] != _kind_folder(kind)):
        raise unsafe
    return parts


def _draft(project, slug, client, parsed, extra):
    """`template.json` ainda sem número nem componentes; `extra` traz title, by e engine_refs."""
    declared = layout.info(project).doc or {}
    return versioning.stamp_schema(
        {
            "slug": slug,
            "version": 1,
            "client": client,
            "ref": refs.template_ref(slug, 1),
            "title": extra["title"],
            "genero": parsed.meta["genero"],
            "aspecto": parsed.meta["aspecto"],
            "canvas": declared.get("canvas"),
            "fps": declared.get("fps"),
            "slots": [],
            "components": [],
            "engine_refs": extra["engine_refs"],
            "provenance": {
                "project_id": existing_project_id(project),
                "frozen_at": now(),
                "getbrolls_version": __version__,
                "roteiro_sha256": hashlib.sha256(roteiro_path(project).read_bytes()).hexdigest(),
                "by": extra["by"],
            },
        },
        FAMILY,
    )


def _write_version(doc, rows):
    """Monta a versão em staging e publica como `<N>` sob `templates/.lock`; devolve (pasta, doc)."""
    root = templates_root(doc["client"])
    root.mkdir(exist_ok=True)
    slug_dir = _no_link(root / doc["slug"])
    with runtime.exclusive_lock(root / LOCK, _BUSY, wait_s=LOCK_WAIT_S):
        slug_dir.mkdir(exist_ok=True)
        _no_link(slug_dir)
        doc = {**doc, "version": next_version(doc["client"], doc["slug"])}
        staging = slug_dir / f"{STAGING_PREFIX}{uuid.uuid4().hex[:8]}"
        staging.mkdir()
        try:
            doc = {**doc, "components": _stage_components(staging, rows)}
            return _publish(staging, slug_dir, doc)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise


# pylint: disable-next=too-many-arguments,too-many-locals  # the keyword-only options of the command
def freeze(project, slug, *, client=None, title=None, engine_refs=(), by=None) -> dict:  # noqa: PLR0913 - options
    """Congela o roteiro e os componentes de `project` em `templates/<slug>/<N>/` do cliente."""
    project = Path(project).expanduser().resolve()
    check_slug(slug)
    extra = {
        "title": _check_text(title, "--title"),
        "by": _check_text(by, "--by"),
        "engine_refs": list(dict.fromkeys(engine_ref(text) for text in engine_refs)),
    }
    client = _freeze_client(project, client)
    parsed = roteiro.parse(roteiro.load_text(project))
    warnings = []
    slots, wanted = _slots(project, parsed)
    rows = _collect(project, wanted, warnings)
    folder, doc = _write_version({**_draft(project, slug, client, parsed, extra), "slots": slots}, rows)
    _freeze_files(folder, warnings)
    for warning in warnings:
        runtime.record_warning("TEMPLATE_FREEZE", warning)
    count = f"{len(doc['slots'])} cena(s), {len(doc['components'])} componente(s)"
    return {
        "ref": doc["ref"],
        "client": client,
        "version": doc["version"],
        "path": _shown(folder),
        "slots": len(doc["slots"]),
        "components": [{key: row[key] for key in ("kind", "name", "file", "sha256")} for row in doc["components"]],
        "licences_transferred": False,
        "summary": {
            "line": (
                f"Template {doc['ref']} congelado para o cliente {client}: {count}, sem fala, licença nem "
                f"aprovação. Próximo passo: init --template {doc['ref']} --client {client}."
            )
        },
    }


# -- list / show --------------------------------------------------------------


def _read_template_file(path):
    """(doc, bytes, problema) de `template.json`, sem seguir link e sem nunca levantar."""
    if is_link(path) or not path.is_file():
        return None, None, f"{TEMPLATE_FILE} não é um arquivo comum."
    try:
        if path.stat().st_size > TEMPLATE_MAX:
            return None, None, f"{TEMPLATE_FILE} é grande demais."
        raw = path.read_bytes()
        doc = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
        validate_template(doc)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None, f"{TEMPLATE_FILE} não é um JSON legível."
    except ValueError as exc:
        return None, None, str(exc)
    return doc, raw, None


def _refuse_constant(token):
    raise ValueError(f"{TEMPLATE_FILE}: {token} não é um número JSON válido.")


def _seal_text(digest):
    return f"{digest}  {TEMPLATE_FILE}\n"


def _seal_problem(folder, raw):
    """Problema do `template.sha256` da versão (ausente, ilegível ou outro hash), ou `None`."""
    path = folder / SEAL_FILE
    unproven = f"não dá para provar que o {TEMPLATE_FILE} é o congelado."
    if is_link(path) or not path.is_file():
        return f"{SEAL_FILE} não existe (ou não é um arquivo comum): {unproven}"
    try:
        if path.stat().st_size > SEAL_MAX:
            raise ValueError
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError, ValueError):
        return f"{SEAL_FILE} ilegível: {unproven}"
    if text != _seal_text(hashlib.sha256(raw).hexdigest()):
        return f"{TEMPLATE_FILE} mudou depois do congelamento: o sha256 não é o do {SEAL_FILE}."
    return None


def _identity_problems(doc, client, slug, number):
    problems = []
    if doc["client"] != client:
        problems.append(f'{TEMPLATE_FILE} é do cliente "{doc["client"]}", não de "{client}".')
    if doc["slug"] != slug:
        problems.append(f'{TEMPLATE_FILE} é do template "{doc["slug"]}", não de "{slug}".')
    if doc["version"] != number:
        problems.append(f"{TEMPLATE_FILE} diz versão {doc['version']}, mas a pasta é a {number}.")
    if doc["ref"] != refs.template_ref(slug, number):
        problems.append(f"{TEMPLATE_FILE} traz a ref {doc['ref']}, não {refs.template_ref(slug, number)}.")
    return problems


def component_path(folder, rel, kind=None):
    """Arquivo `rel` de `components/` dentro da versão, conferido antes de qualquer leitura.

    `ValueError` quando o caminho é inseguro (`..`, absoluto, `\\`, `:`, fora de
    `components/<tipo>/`), quando algum pedaço do caminho é link ou quando não é arquivo.
    """
    parts = safe_relative(rel, kind)
    here = folder
    for part in parts:
        here = here / part
        if is_link(here):
            raise ValueError(f"{rel} é um link; o template só aceita arquivos comuns.")
    if not here.is_file():
        raise ValueError(f"{rel} não existe no template.")
    return here


def sha256_file(path):
    """sha256 dos bytes de `path`, lido em blocos."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _component_problems(folder, doc):
    problems, listed = [], set()
    for row in doc["components"]:
        rel = row["file"]
        if rel in listed:
            problems.append(f"{rel} aparece duas vezes em components.")
            continue
        listed.add(rel)
        try:
            path = component_path(folder, rel, row["kind"])
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if sha256_file(path) != row["sha256"]:
            problems.append(f"{rel} mudou: o sha256 não bate com o template.json.")
    for current, dirs, names in os.walk(folder):
        base = Path(current)
        for name in [*dirs, *names]:
            path = base / name
            rel = path.relative_to(folder).as_posix()
            if is_link(path):
                if rel not in listed:
                    problems.append(f"{rel} é um link dentro do template.")
                continue
            if name in names and rel not in (TEMPLATE_FILE, SEAL_FILE) and rel not in listed:
                problems.append(f"{rel} é um arquivo a mais, que o template.json não lista.")
        dirs[:] = [d for d in dirs if not is_link(base / d)]
    return problems


def show(ref, client) -> dict:
    """A versão `ref` do template do cliente, com a integridade conferida arquivo por arquivo."""
    slug, number = refs.template_parts(ref)
    clients.check_slug(client)
    folder = template_dir(client, slug, number)
    for path in (folder.parent, folder):
        _no_link(path)
    if not folder.is_dir():
        raise ValueError(f"O template {refs.template_ref(slug, number)} não existe no cliente {client}.")
    doc, raw, problem = _read_template_file(folder / TEMPLATE_FILE)
    problems = [problem] if problem else []
    if raw is not None:
        sealed = _seal_problem(folder, raw)
        problems += [sealed] if sealed else []
    if doc is not None:
        problems += _identity_problems(doc, client, slug, number)
        problems += _component_problems(folder, doc)
    return {
        "ref": refs.template_ref(slug, number),
        "client": client,
        "path": _shown(folder),
        "template": doc,
        "template_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
        "intact": not problems,
        "problems": problems,
    }


def _client_rows(slug):
    try:
        root = templates_root(slug)
    except ValueError as exc:
        return [], runtime.scrub_home(str(exc))
    rows = []
    if not root.is_dir():
        return rows, None
    for slug_dir in sorted(root.iterdir()):
        name = slug_dir.name
        if name.startswith(".") or is_link(slug_dir) or not slug_dir.is_dir():
            continue
        try:
            check_slug(name)
        except ValueError:
            continue
        for number in _versions(slug_dir):
            folder = slug_dir / str(number)
            if is_link(folder):
                continue
            doc, _, problem = _read_template_file(folder / TEMPLATE_FILE)
            rows.append(
                {
                    "client": slug,
                    "slug": name,
                    "version": number,
                    "ref": refs.template_ref(name, number),
                    "title": doc.get("title") if doc else None,
                    "problem": problem,
                }
            )
    return rows, None


def list_templates(client=None) -> dict:
    """Todas as versões de template dos clientes registrados (ou só de `client`), sem rehash.

    Ordem: cliente, slug, versão. `show` é quem confere a integridade de uma versão.
    """
    slugs = [clients.check_slug(client)] if client else [row["slug"] for row in clients.load_registry()["clients"]]
    if client:
        clients.load_client(client)
    rows, broken = [], []
    for slug in slugs:
        found, problem = _client_rows(slug)
        rows.extend(found)
        if problem:
            broken.append({"client": slug, "problem": problem})
    rows.sort(key=lambda row: (row["client"], row["slug"], row["version"]))
    return {"templates": rows, "clients_with_problems": broken}


# -- init --template -----------------------------------------------------------


def _frontmatter_value(text, flag):
    value = unicodedata.normalize("NFC", text).strip() if isinstance(text, str) else ""
    if not value or any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError(f"Informe {flag} numa linha só.")
    return value.replace('"', '\\"')


def roteiro_text(doc, client, tema):
    """`ROTEIRO.md` novo de um template: frontmatter com tema e cliente, uma cena por vaga."""
    if doc["genero"] not in roteiro.GENRES:
        raise ValueError(f'O template é do gênero "{doc["genero"]}", que esta versão não conhece.')
    head = [
        "---", "type: roteiro", f"genero: {doc['genero']}", f'aspecto: "{doc["aspecto"]}"',
        f'tema: "{_frontmatter_value(tema, "--tema")}"', f"cliente: {client}", "legenda: true", "status: draft",
        "---", "",
    ]  # fmt: skip
    body = []
    for slot in doc["slots"]:
        lines = [slot["title"], slot["layout"], *slot["layers"], slot["placeholder"]]
        if any(unicodedata.category(char) == "Cc" for line in lines for char in line):
            raise ValueError(f"A vaga {slot['id']} do template tem caractere de controle; o template não serve.")
        body += [f"## {slot['title']}", slot["layout"], *slot["layers"], slot["placeholder"], ""]
    return "\n".join(head + body)


def _checked_roteiro(doc, text):
    """O roteiro gerado lê de volta com as mesmas vagas; senão o template não serve."""
    try:
        parsed = roteiro.parse(text, plugins=frozenset())
    except roteiro.RoteiroError as exc:
        raise ValueError(f"O roteiro gerado pelo template não passou no check: {exc}") from None
    scenes = [
        (s.title, roteiro.directive_text(s.layout), [roteiro.directive_text(x) for x in s.layers])
        for s in parsed.scenes
    ]
    wanted = [(slot["title"], slot["layout"], slot["layers"]) for slot in doc["slots"]]
    if scenes != wanted:
        raise ValueError("As vagas do template não viram as mesmas cenas no roteiro; o template não serve.")
    return parsed


def _refuse_existing(project):
    if project.exists() and not project.is_dir():
        raise ValueError(f"{project} existe e não é uma pasta; escolha outra pasta para o projeto.")
    if os.path.lexists(project / layout.PROJECT_FILE):
        raise ValueError(f"{layout.PROJECT_FILE} já existe em {project}; este projeto já foi criado.")
    if os.path.lexists(project / "brolls" / "manifest.json"):
        raise ValueError(
            f"{project} já é um projeto do get-brolls (tem brolls/manifest.json); init --template só cria projeto novo."
        )
    for name in ("ROTEIRO.md", LOCK_FILE):
        if os.path.lexists(project / name):
            raise ValueError(f"{name} já existe em {project}; init --template não sobrescreve nada.")


def _client_match(client, spec, row):
    """O arquivo único da pasta do cliente com o nome do componente, ou `None`."""
    folder = assets.client_components(client, spec)
    if folder is None:
        return None
    matches = assets.component_entries(folder, spec).get(fold(unicodedata.normalize("NFC", row["name"])), [])
    if len(matches) != 1 or is_link(matches[0]) or not matches[0].is_file():
        return None
    return matches[0]


def _install(project, folder, doc, created):
    """Componentes da versão: os que o cliente já tem ficam lá; o resto é copiado sem licença.

    `doc` é o `template.json` que o `show` acabou de conferir; cada arquivo é conferido
    de novo no caminho (`component_path`) e pelo sha256 dos bytes copiados.
    """
    installed, warnings = [], []
    client = doc["client"]
    for row in doc["components"]:
        spec = assets.ASSET_KINDS[row["kind"]]
        source = component_path(folder, row["file"], row["kind"])
        placed = _client_match(client, spec, row)
        if placed is not None and sha256_file(placed) == row["sha256"]:
            where = "client"
        else:
            where = f"{spec.folder}/{source.name}"
            target = project.joinpath(*where.split("/"))
            _no_link(target.parent)
            copy_hashed(source, target, expected=row["sha256"])
            created.append(target)
            placed = target
            if spec.licensed:
                warnings.append(
                    f"{where}: licença não transferida pelo template; registre {source.stem}{assets.LICENSE_SUFFIX} "
                    "antes de usar."
                )
        seen = placed.stat()
        installed.append(
            {
                "kind": row["kind"],
                "name": row["name"],
                "sha256": row["sha256"],
                "installed_as": where,
                # Cache do `status`: tamanho e mtime iguais aos daqui pulam o sha256 do arquivo.
                "size": seen.st_size,
                "mtime_ns": seen.st_mtime_ns,
            }
        )
    return installed, warnings


def validate_lock(doc, label=LOCK_FILE):
    """Confere `doc` contra o schema publicado do `template.lock.json`; `ValueError` senão."""
    if not isinstance(doc, dict):
        raise ValueError(f"{label} tem que ser um objeto JSON.")
    versioning.read_schema(doc, LOCK_FAMILY, SUPPORTED, label=label)
    problems = errors(doc, schemas.load(LOCK_FAMILY))
    if problems:
        raise ValueError(f"{label} fora do schema: " + "; ".join(problems[:5]))
    return doc


def _undo(created):
    for path in reversed(created):
        with contextlib.suppress(OSError):
            if path.is_dir() and not is_link(path):
                path.rmdir()
            else:
                path.unlink()


# pylint: disable-next=too-many-arguments  # the options of init --template
def plan_instance(project, ref, client, tema, *, canvas=None, fps=None) -> dict:  # noqa: PLR0913 - options
    """Tudo o que `init --template` confere antes de criar qualquer coisa; devolve o plano de `write_instance`.

    Recusa projeto existente, pasta de trabalho que é link, template que não está
    íntegro (inclusive `template.json` que não bate com o `template.sha256`) e tema ou
    vagas que não viram um roteiro válido — sem criar nem a pasta do projeto.
    """
    project = Path(project).expanduser().resolve()
    _refuse_existing(project)
    layout.refuse_linked_folders(project)
    clients.check_slug(client)
    shown = show(ref, client)
    if not shown["intact"]:
        raise ValueError(
            f"O template {shown['ref']} do cliente {client} não está íntegro: " + "; ".join(shown["problems"][:5])
        )
    doc = shown["template"]
    text = roteiro_text(doc, client, tema)
    _checked_roteiro(doc, text)
    project_doc = layout.new_project_doc(
        client=client,
        template=shown["ref"],
        canvas=canvas if canvas is not None else doc["canvas"],
        fps=fps if fps is not None else doc["fps"],
    )
    return {
        "project": project,
        "shown": shown,
        "folder": template_dir(client, *refs.template_parts(shown["ref"])),
        "roteiro": text,
        "project_doc": project_doc,
    }


# pylint: disable-next=too-many-arguments  # the options of init --template
def instantiate(project, ref, client, tema, *, canvas=None, fps=None) -> dict:  # noqa: PLR0913 - options
    """Cria o projeto `project` a partir da versão `ref` do template do cliente `client`."""
    return write_instance(plan_instance(project, ref, client, tema, canvas=canvas, fps=fps))


def _format(doc):
    return roteiro.ASPECT_TO_FORMAT[doc["aspecto"]]


def write_instance(plan) -> dict:
    """Grava o plano de `plan_instance`; desfaz o que gravou quando algo falha no meio."""
    from . import guidance, rules  # tardio: o rules puxa brief/queue

    project, shown, project_doc = plan["project"], plan["shown"], plan["project_doc"]
    doc, client = shown["template"], shown["client"]
    created = [] if project.exists() else [project]
    project.mkdir(parents=True, exist_ok=True)
    try:
        folders = _make_folders(project, created)
        _write_file(project / "ROTEIRO.md", plan["roteiro"])
        created.append(project / "ROTEIRO.md")
        seeded = rules.seed_project_rules(project, _format(doc))
        if seeded:
            created.append(seeded)
        installed, warnings = _install(project, plan["folder"], doc, created)
        lock = validate_lock(
            versioning.stamp_schema(
                {
                    "ref": shown["ref"],
                    "client": client,
                    "template_sha256": shown["template_sha256"],
                    "applied": now(),
                    "components": installed,
                },
                LOCK_FAMILY,
            )
        )
        _write_file(project / LOCK_FILE, _dump(lock))
        created.append(project / LOCK_FILE)
        layout.write_project(project, project_doc)
    except BaseException:
        _undo(created)
        raise
    for warning in warnings:
        runtime.record_warning("LICENCE_NOT_TRANSFERRED", warning)
    return {
        "project": str(project),
        "id": project_doc["id"],
        "layout": project_doc["layout"],
        "client": client,
        "template": shown["ref"],
        "folders": folders,
        "components": installed,
        "summary": {
            "line": (
                f"Projeto criado do template {shown['ref']} do cliente {client}: ROTEIRO.md com "
                f"{len(doc['slots'])} cena(s) para preencher, template.lock.json e project.json. Licenças e aprovações "
                "não vêm do template. Próximo passo: preencher o ROTEIRO.md e rodar roteiro --action check."
                + (f' RULES.md criado em "{_format(doc)}", o formato do roteiro.' if seeded else "")
            ),
            "do": guidance.roteiro_check_step(project),
        },
        "rules": str(seeded) if seeded else None,
    }


def _make_folders(project, created):
    for name in layout.PROJECT_FOLDERS:
        path = project
        for part in name.split("/"):
            path = path / part
            _no_link(path)
            if not path.exists():
                path.mkdir()
                created.append(path)
    return list(layout.PROJECT_FOLDERS)


def lock_warnings(project) -> list:
    """Avisos de um projeto com `template.lock.json`: componente achado hoje com outro sha256, sumido,
    ou lock ilegível. Nunca levanta; projeto sem lock não tem aviso."""
    path = Path(project).expanduser().resolve() / LOCK_FILE
    if not os.path.lexists(path):
        return []
    try:
        if is_link(path) or path.stat().st_size > TEMPLATE_MAX:
            raise ValueError("não é um arquivo comum")
        lock = validate_lock(json.loads(path.read_text(encoding="utf-8"), parse_constant=_refuse_constant))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return [runtime.scrub_home(f"{LOCK_FILE} ilegível ({exc}); não dá para conferir os componentes do template.")]
    out = []
    declared = layout.info(project).doc or {}
    if (declared.get("client"), declared.get("template")) != (lock["client"], lock["ref"]):
        out.append(f"{LOCK_FILE} é do template {lock['ref']} do cliente {lock['client']}, diferente do project.json.")
    root = Path(project).expanduser().resolve()
    for row in lock["components"]:
        label = f'{row["kind"]} "{row["name"]}"'
        installed = row["installed_as"]
        if installed != "client" and not os.path.lexists(root.joinpath(*installed.split("/"))):
            # A cópia do init sumiu: outro arquivo com o mesmo nome achado em outro lugar não é "mudou".
            out.append(f"{label} do template {lock['ref']} sumiu de {installed} desde o init.")
            continue
        try:
            found = assets.resolve(project, row["kind"], row["name"])
        except ValueError as exc:
            out.append(runtime.scrub_home(f"{label} do template: {exc}"))
            continue
        if found["status"] != "found":
            out.append(f"{label} do template {lock['ref']} sumiu desde o init.")
        elif not _same_component(Path(found["path"]), row):
            out.append(f"{label} mudou desde o init: o sha256 não é o do {LOCK_FILE} ({lock['ref']}).")
    return out


def _same_component(path, row):
    """O arquivo ainda é o do lock: tamanho e mtime iguais aos gravados pulam o sha256 (cache do `status`)."""
    try:
        seen = path.stat()
    except OSError:
        return False
    if (seen.st_size, seen.st_mtime_ns) == (row.get("size"), row.get("mtime_ns")):
        return True
    return sha256_file(path) == row["sha256"]


def record_lock_warnings(project):
    """`lock_warnings` como avisos `TEMPLATE_LOCK_DRIFT` do comando em curso."""
    for warning in lock_warnings(project):
        runtime.record_warning("TEMPLATE_LOCK_DRIFT", warning)


# -- CLI ----------------------------------------------------------------------


def _required(args, flag, dest=None):
    value = getattr(args, dest or flag.replace("-", "_"))
    if not value:
        from .errors import UsageError

        raise UsageError(f"--{flag} é obrigatório em template --action {args.action}.")
    return value


def run(args):
    """`template --action freeze|list|show`."""
    if args.action == "freeze":
        return freeze(
            _required(args, "from", "source"),
            _required(args, "slug"),
            client=args.client,
            title=args.title,
            engine_refs=args.engine_ref or (),
            by=args.by,
        )
    if args.action == "list":
        return list_templates(args.client)
    return show(_required(args, "ref"), _required(args, "client"))
