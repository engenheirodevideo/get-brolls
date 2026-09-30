"""Layout do projeto: `project.json`, a versão do layout e a identidade do projeto.

O layout de projeto mora aqui, nunca em `_paths` (que é só a instalação).

- **Layout 0**: projeto sem `project.json`. É inferido pela ausência do arquivo e
  nunca é gravado; tudo continua sob `brolls/` como sempre.
- **Layout 1**: projeto com `project.json` (`schema` `getbrolls.project/1`), que
  declara `layout: 1`, o id do projeto e, quando houver, cliente, template, quadro e fps.

O `id` do `project.json` é o mesmo `project_id` de `brolls/manifest.json`: um projeto
tem uma identidade só, e quem lê prefere a do `project.json`.
"""

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


def _slug(value, label):
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
            "client": _slug(client, "client"),
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
