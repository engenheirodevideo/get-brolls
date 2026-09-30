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
FAMILY = "template"
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
        tail = assets.ASSET_KINDS[kind].folder.removeprefix("assets/")
        rel = f"{COMPONENTS}/{tail}/{_file_name(name, path)}"
        safe_relative(rel)
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
            (staging / TEMPLATE_FILE).unlink(missing_ok=True)
            _write_file(staging / TEMPLATE_FILE, _dump(doc))
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


def safe_relative(rel):
    """Caminho relativo seguro de componente (`components/<tipo>/<arquivo>`); `ValueError` senão."""
    from .analysis_contract import Invalid, relative

    try:
        parts = relative(rel)
    except Invalid:
        raise ValueError(f'Caminho de componente inseguro no template: "{rel}".') from None
    folders = {spec.folder.removeprefix("assets/") for spec in assets.ASSET_KINDS.values() if spec.personal}
    if len(parts) != _COMPONENT_DEPTH or parts[0] != COMPONENTS or parts[1] not in folders:
        raise ValueError(f'Caminho de componente inseguro no template: "{rel}".')
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
    raise ValueError(f"template --action {args.action} ainda não existe.")
