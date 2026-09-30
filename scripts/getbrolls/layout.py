"""Layout do projeto: `project.json`, a versão do layout e a identidade do projeto.

O layout de projeto mora aqui, nunca em `_paths` (que é só a instalação).

- **Layout 0**: projeto sem `project.json`. É inferido pela ausência do arquivo e
  nunca é gravado; tudo continua sob `brolls/` como sempre.
- **Layout 1**: projeto com `project.json` (`schema` `getbrolls.project/1`), que
  declara `layout: 1`, o id do projeto e, quando houver, cliente, template, quadro e fps.

O `id` do `project.json` é o mesmo `project_id` de `brolls/manifest.json`: um projeto
tem uma identidade só, e quem lê prefere a do `project.json`.
"""

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import versioning
from .models import now
from .roteiro_frontmatter import SLUG_MAX, SLUG_RE
from .sdk import files, schemas
from .sdk.jsonschema import errors

# Pastas de `assets/`, congeladas em português: cada uma é a pasta de um tipo de
# componente (`assets.ASSET_KINDS`), menos `outros`, que é só pasta e nunca é resolvida.
ASSET_FOLDERS = ("marca", "lettering", "sfx", "musica", "imagem", "composicoes", "outros")
# Pastas de trabalho que `init` cria num projeto de layout 1.
PROJECT_FOLDERS = ("aroll", *(f"assets/{name}" for name in ASSET_FOLDERS), "broll", "analysis")
PROJECT_FILE = "project.json"
PROJECT_FAMILY = "project"
PROJECT_SCHEMA = versioning.schema_name(PROJECT_FAMILY, 1)
SUPPORTED_SCHEMA = 1
LAYOUT_VERSION = 1
_CANVAS = re.compile(r"([0-9]+)x([0-9]+)")
_FPS = re.compile(r"([0-9]+)(?:/([0-9]+))?")


@dataclass(frozen=True)
class Layout:
    """Layout de um projeto: `version` 0 ou 1, de onde veio e, quando der, o documento.

    `source` é `"project.json"` (lido e válido) ou `"inferred"` (sem arquivo, ou arquivo
    que não deu para usar: aí `problem` diz o motivo e `version` fica 0).
    """

    version: int
    source: str
    doc: dict | None
    problem: str | None


def _refuse_constant(token):
    raise ValueError(f"{PROJECT_FILE} é incompatível: {token} não é um número JSON válido.")


def _positive_int(value, where):
    # `type(...) is int`: `True`, `30.0` e NaN nunca passam por inteiro.
    if type(value) is not int or value < 1:
        raise ValueError(
            f"{PROJECT_FILE} é incompatível: {where} tem que ser um inteiro a partir de 1 (veio {value!r})."
        )


def _check_pair(doc, field, keys):
    value = doc.get(field)
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{PROJECT_FILE} é incompatível: {field} tem que ser um objeto ou null.")
    for key in keys:
        found = value.get(key)
        if isinstance(found, float) and not math.isfinite(found):
            raise ValueError(f"{PROJECT_FILE} é incompatível: {field}.{key} não é um número finito.")
        _positive_int(found, f"{field}.{key}")


def validate_project(doc):
    """Confere `doc` contra o schema publicado e as regras que o schema não expressa.

    Devolve o próprio `doc`; `ValueError` com o motivo quando não serve.
    """
    if not isinstance(doc, dict):
        raise ValueError(f"{PROJECT_FILE} é incompatível: tem que ser um objeto JSON.")
    versioning.read_schema(doc, PROJECT_FAMILY, SUPPORTED_SCHEMA, label=PROJECT_FILE)
    _check_pair(doc, "canvas", ("width", "height"))
    _check_pair(doc, "fps", ("num", "den"))
    problems = errors(doc, schemas.load(PROJECT_FAMILY))
    if problems:
        raise ValueError(f"{PROJECT_FILE} fora do schema: " + "; ".join(problems[:5]))
    ident = doc["id"]
    try:
        canonical = str(uuid.UUID(ident))
    except ValueError:
        canonical = None
    if canonical != ident:
        raise ValueError(f"{PROJECT_FILE} é incompatível: id tem que ser um uuid em minúsculas.")
    return doc


def _path(project):
    return Path(project).expanduser().resolve() / PROJECT_FILE


def load_project(project):
    """O `project.json` validado, ou `None` sem arquivo; `ValueError` quando não dá para usar."""
    path = _path(project)
    if files.is_link(path):
        raise ValueError(f"{PROJECT_FILE} é um link; o arquivo do projeto tem que ser um arquivo comum.")
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{PROJECT_FILE} não está em UTF-8.") from None
    try:
        doc = json.loads(text, parse_constant=_refuse_constant)
    except json.JSONDecodeError:
        raise ValueError(
            f"{PROJECT_FILE} contém JSON inválido. Preserve o arquivo e restaure uma cópia válida."
        ) from None
    return validate_project(doc)


def info(project):
    """`Layout` do projeto; nunca levanta (arquivo ruim vira versão 0 com `problem`)."""
    try:
        doc = load_project(project)
    except (OSError, ValueError) as exc:
        return Layout(0, "inferred", None, str(exc) if isinstance(exc, ValueError) else f"{PROJECT_FILE}: {exc}")
    if doc is None:
        return Layout(0, "inferred", None, None)
    return Layout(doc["layout"], PROJECT_FILE, doc, None)


def project_id(project):
    """O `id` do `project.json`, ou `None` sem arquivo; `ValueError` quando o arquivo é inválido."""
    doc = load_project(project)
    return None if doc is None else doc["id"]


def write_project(project, doc):
    """Valida e grava `project.json` de uma vez; recusa sobrescrever um que já existe."""
    validate_project(doc)
    path = _path(project)
    if path.exists() or files.is_link(path):
        raise ValueError(f"{PROJECT_FILE} já existe neste projeto; nada foi sobrescrito.")
    _write_new(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return path


def _write_new(path, text):
    """Cria `path` com `text` inteiro (UTF-8, `\\n` em qualquer sistema), sem nunca sobrescrever.

    O conteúdo vai para um temporário e só então ganha o nome final por hardlink, que
    falha se o nome já existe: não sobra `project.json` pela metade nem um trocado por
    outro. Sistema de arquivos sem hardlink cai na troca comum, depois da conferência.
    """
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            raise ValueError(f"{PROJECT_FILE} já existe neste projeto; nada foi sobrescrito.") from None
        except OSError:
            temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def check_client(value, label="client"):
    """O slug de cliente como veio (ou `None`); `ValueError` quando não é um slug válido."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > SLUG_MAX or not SLUG_RE.fullmatch(value):
        raise ValueError(
            f"{label} tem que ser um slug de até {SLUG_MAX} caracteres (minúsculas, números e hífen, como acme-corp)."
        )
    return value


# pylint: disable-next=redefined-outer-name  # the keyword names the field it fills
def new_project_doc(*, project_id=None, client=None, template=None, canvas=None, fps=None):
    """Documento de `project.json` novo e válido (id novo quando `project_id` é `None`)."""
    doc = versioning.stamp_schema(
        {
            "id": project_id or str(uuid.uuid4()),
            "layout": LAYOUT_VERSION,
            "client": check_client(client),
            "template": template,
            "canvas": canvas,
            "fps": fps,
            "created": now(),
            "ext": {},
        },
        PROJECT_FAMILY,
    )
    return validate_project(doc)


def parse_canvas(text):
    """`"1080x1920"` → `{"width": 1080, "height": 1920}`; `ValueError` fora disso."""
    found = _CANVAS.fullmatch(text.strip()) if isinstance(text, str) else None
    if found is None or int(found.group(1)) < 1 or int(found.group(2)) < 1:
        raise ValueError(f"Quadro inválido {text!r}: use LARGURAxALTURA em pixels, como 1080x1920.")
    return {"width": int(found.group(1)), "height": int(found.group(2))}


def parse_fps(text):
    """`"30"` → `{"num": 30, "den": 1}`; `"30000/1001"` → `{"num": 30000, "den": 1001}`."""
    found = _FPS.fullmatch(text.strip()) if isinstance(text, str) else None
    num = int(found.group(1)) if found else 0
    den = int(found.group(2) or 1) if found else 0
    if num < 1 or den < 1:
        raise ValueError(f"fps inválido {text!r}: use um inteiro (30) ou uma fração (30000/1001).")
    return {"num": num, "den": den}


# --- clipes finais -----------------------------------------------------------
#
# O manifesto guarda o clipe final pelo caminho lógico `clips/<arquivo>` (relativo a
# `brolls/`, como sempre). Só este módulo sabe em que pasta ele está de fato:
#
# - layout 0: `brolls/clips/`, e nenhuma outra (uma `broll/` da pessoa nunca é lida);
# - layout 1: `broll/`, na raiz do projeto, e, só para leitura, a antiga
#   `brolls/clips/` quando ela existe (projeto adotado pelo `migrate` sem mover nada).
#
# `project.json` que não dá para usar conta como layout 0, a menos que já exista
# `broll/` ao lado: aí a leitura olha as duas pastas e a escrita recusa, em vez de
# adivinhar para qual delas o clipe iria.

CLIPS_PREFIX = "clips/"
CLIP_FOLDER = "broll"
LEGACY_CLIPS = ("brolls", "clips")


class ClipConflictError(ValueError):
    """O mesmo nome de clipe em `broll/` e em `brolls/clips/`, sem `sha256` que decida."""


def _project_root(project):
    return Path(project).expanduser().resolve()


def _clip_folders(project):
    """(pasta de escrita ou `None` quando a escrita é recusada, pastas de leitura, motivo da recusa)."""
    root = _project_root(project)
    legacy = root.joinpath(*LEGACY_CLIPS)
    broll = root / CLIP_FOLDER
    found = info(root)
    if found.version == 0 and found.problem is None:
        return legacy, (legacy,), None
    if files.is_link(broll):
        raise ValueError(
            f"A pasta {CLIP_FOLDER}/ é um link: os clipes finais precisam de uma pasta real dentro do "
            "projeto. Troque o link pela pasta real e rode o comando de novo."
        )
    if found.version == 0:
        if not broll.is_dir():
            return legacy, (legacy,), None
        # `project.json` quebrado ao lado de `broll/`: não dá para saber qual pasta vale.
        refusal = f"{found.problem} Corrija o {PROJECT_FILE} antes de gravar clipes: {CLIP_FOLDER}/ já existe aqui."
        return None, (broll, legacy) if legacy.is_dir() else (broll,), refusal
    return broll, (broll, legacy) if legacy.is_dir() else (broll,), None


def clips_dir(project):
    """Pasta onde o `fetch` grava o clipe final; `ValueError` quando não há uma segura."""
    write, _, refusal = _clip_folders(project)
    if write is None:
        raise ValueError(refusal)
    return write


def clip_roots(project):
    """Pastas onde um clipe final pode estar, na ordem de busca (a de escrita primeiro)."""
    return _clip_folders(project)[1]


def _clip_tail(rel):
    if (
        not isinstance(rel, str)
        or not rel.startswith(CLIPS_PREFIX)
        or rel == CLIPS_PREFIX
        or "\\" in rel
        or ".." in Path(rel).parts
    ):
        raise ValueError(f"Caminho de clipe inválido {rel!r}: tem que ser clips/<arquivo>, sem .. nem barra invertida.")
    return rel[len(CLIPS_PREFIX) :]


def _digest(path):
    # O mesmo sha256 em blocos de `ledger.digest`, aqui para `layout` não importar `ledger`.
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _label(project, path):
    try:
        return path.relative_to(_project_root(project)).as_posix()
    except ValueError:
        return str(path)


def clip_file(project, rel, *, for_write=False, sha256=None):
    """Arquivo do clipe lógico `rel` (`clips/<arquivo>`).

    Na escrita, sempre em `clips_dir`. Na leitura, a primeira pasta de `clip_roots` que
    tem o arquivo (ou a de escrita, quando nenhuma tem). Achado nas duas pastas, só o
    `sha256` registrado desempata; sem ele, ou sem nenhuma cópia que bata, levanta
    `ClipConflictError` nomeando as duas, nunca escolhe calado.
    """
    tail = _clip_tail(rel)
    if for_write:
        return clips_dir(project) / tail
    roots = clip_roots(project)
    found = [root / tail for root in roots if (root / tail).exists() or (root / tail).is_symlink()]
    if not found:
        return roots[0] / tail
    if len(found) == 1:
        return found[0]
    if sha256:
        matching = [path for path in found if path.is_file() and _digest(path) == sha256]
        if matching:
            # Mais de uma cópia com o mesmo conteúdo: são o mesmo clipe, qualquer uma serve.
            return matching[0]
    places = " e ".join(f"{_label(project, path.parent)}/" for path in found)
    raise ClipConflictError(
        f"O clipe {rel} existe em {places}"
        + (" e nenhuma cópia bate com o sha256 registrado" if sha256 else "")
        + ": deixe só a cópia certa (a outra pode ir para fora do projeto) e rode o comando de novo."
    )


def output_file(project, rel, *, sha256=None):
    """Arquivo de um caminho lógico do manifesto (relativo a `brolls/`); `clips/` passa por `clip_file`."""
    if isinstance(rel, str) and rel.startswith(CLIPS_PREFIX):
        return clip_file(project, rel, sha256=sha256)
    return _project_root(project) / "brolls" / rel


def clip_taken(project, rel):
    """Caminho lógico de uma cópia já existente de `rel` em qualquer pasta de clipes, ou `None`."""
    tail = _clip_tail(rel)
    for root in clip_roots(project):
        if (root / tail).exists():
            return rel
    return None


def existing_clips(project, pattern):
    """Caminhos lógicos (`clips/<nome>`, em ordem) dos arquivos que casam `pattern` em qualquer pasta de clipes."""
    names = {path.name for root in clip_roots(project) if root.is_dir() for path in root.glob(pattern)}
    return [CLIPS_PREFIX + name for name in sorted(names)]
