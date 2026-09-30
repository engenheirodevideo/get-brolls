"""Rotas de componentes: onde mora cada coisa que o roteiro cita.

`[SFX: whoosh]` procura `whoosh.*` em `assets/sfx/` do projeto e depois em
`~/.getbrolls/assets/sfx/`. Só o nível da pasta, sem recursão, só arquivo regular
(symlink para arquivo vale se não sair da pasta). A decisão de ambiguidade sai da
listagem da pasta (e não do sistema de arquivos), então dá o mesmo resultado em
Linux, macOS e Windows. A biblioteca pessoal não transfere licença: cada arquivo
carrega a própria, em `<nome>.licenca.json`.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import versioning
from .roteiro import fold
from .rules import home_dir

LICENSE_SUFFIX = ".licenca.json"
LICENSE_FIELDS = ("origem", "licenca", "credito")
# Grafia em inglês aceita na leitura; a gravação continua em português.
LICENSE_ALIASES = {"origem": ("source",), "licenca": ("license_name",), "credito": ("attribution",)}
_NAME = re.compile(r"^[\w][\w \-]{0,79}$")
VIDEO = (".mp4", ".mov", ".m4v")
AUDIO = (".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".ogg")
IMAGE = (".png", ".svg", ".webp", ".jpg", ".jpeg")


@dataclass(frozen=True)
class AssetKind:
    """Tipo de componente: pasta onde mora, extensões aceitas e regras de licença/pessoal."""

    name: str
    folder: str
    extensions: tuple[str, ...]
    licensed: bool
    personal: bool


ASSET_KINDS = {
    "aroll": AssetKind("aroll", "aroll", VIDEO, licensed=False, personal=False),
    "marca": AssetKind("marca", "assets/marca", (*IMAGE, *VIDEO[:2]), licensed=True, personal=True),
    "lettering": AssetKind("lettering", "assets/lettering", (".json", ".html"), licensed=False, personal=True),
    "sfx": AssetKind("sfx", "assets/sfx", AUDIO, licensed=True, personal=True),
    "musica": AssetKind("musica", "assets/musica", AUDIO, licensed=True, personal=True),
    "composicao": AssetKind("composicao", "assets/composicoes", (".html", ".json"), licensed=False, personal=True),
}


def _kind(kind):
    if kind not in ASSET_KINDS:
        raise ValueError(f'Tipo de componente desconhecido "{kind}"; use: {", ".join(ASSET_KINDS)}.')
    return ASSET_KINDS[kind]


def valid_name(name):
    """Nome de componente utilizável: letras, números, espaço, - ou _; nunca caminho."""
    if not isinstance(name, str):
        return False
    name = unicodedata.normalize("NFC", name).strip()
    return ".." not in name and _NAME.match(name) is not None


def component_roots(project, spec):
    """(origem, pasta) onde um componente pode morar: a do projeto e, se aplicável, a pessoal."""
    yield "project", Path(project).expanduser().resolve() / spec.folder
    if spec.personal:
        yield "personal", home_dir() / "assets" / spec.folder.removeprefix("assets/")


def component_entries(root, spec):
    """Arquivos candidatos de uma pasta, agrupados pela chave de comparação.

    Fica de fora: arquivo oculto (`.x`, `._x` do macOS), sidecar de licença,
    extensão fora da tabela, pasta e symlink quebrado.
    """
    groups = {}
    if not root.is_dir():
        return groups
    for path in sorted(root.iterdir()):
        name = unicodedata.normalize("NFC", path.name)
        if name.startswith(".") or name.lower().endswith(LICENSE_SUFFIX):
            continue
        if path.suffix.lower() not in spec.extensions or not path.is_file():
            continue
        groups.setdefault(fold(Path(name).stem), []).append(path)
    return groups


def _license(path):
    """(licença, erro): sem sidecar = (None, None); sidecar ruim = (None, motivo), nunca exceção."""
    sidecar = path.with_name(path.stem + LICENSE_SUFFIX)
    if not sidecar.is_file():
        return None, None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, f"{sidecar.name} não é um JSON válido: conserte ou apague o arquivo."
    return _normalize_license(sidecar.name, data)


def _normalize_license(name, data):
    """(licença com chaves em português, erro): aceita o sidecar em português ou inglês."""
    *first, last = (f"{k} (ou {' ou '.join(LICENSE_ALIASES[k])})" for k in LICENSE_FIELDS)
    missing = f"{name} precisa de {', '.join(first)} e {last} preenchidos, em texto."
    if not isinstance(data, dict):
        return None, missing
    try:
        versioning.read_version(data, name)
    except ValueError as exc:
        return None, str(exc)
    fields = {k: v for k, v in data.items() if k != versioning.FIELD}
    if any(not isinstance(v, str) for v in fields.values()):
        return None, missing
    aliases = {alias for spellings in LICENSE_ALIASES.values() for alias in spellings}
    license_ = {}
    for key in LICENSE_FIELDS:
        spelled = [(k, fields[k]) for k in (key, *LICENSE_ALIASES[key]) if k in fields]
        if len({v for _, v in spelled}) > 1:
            return None, f"{name}: {spelled[0][0]} e {spelled[1][0]} dizem coisas diferentes"
        value = spelled[0][1] if spelled else ""
        if not value.strip():
            return None, missing
        license_[key] = value
    extras = {k: v for k, v in fields.items() if k not in LICENSE_FIELDS and k not in aliases}
    return {**license_, **extras}, None


def _outside(path, root):
    return not path.resolve().is_relative_to(root.resolve())


def resolve(project, kind, name):
    """Acha o componente pelo nome, valida a licença e devolve o status ("found" ou "pending")."""
    spec = _kind(kind)
    if not valid_name(name):
        raise ValueError(f'Nome de componente inválido "{name}": use letras, números, espaço, - ou _.')
    key = fold(unicodedata.normalize("NFC", name))
    for origin, root in component_roots(project, spec):
        matches = component_entries(root, spec).get(key, [])
        if len(matches) > 1:
            listed = ", ".join(p.name for p in matches)
            raise ValueError(f'"{name}" é ambíguo em {root}: {listed}. Deixe só um.')
        if matches:
            if _outside(matches[0], root):
                raise ValueError(f"{matches[0].name} aponta para fora de {root}: o componente tem que morar na pasta.")
            info, problem = _license(matches[0])
            warnings = [problem] if problem else []
            if spec.licensed and info is None and problem is None:
                warnings.append(f"{matches[0].name}: licença não registrada ({matches[0].stem}{LICENSE_SUFFIX})")
            return {
                "kind": kind,
                "name": name,
                "status": "found",
                "path": str(matches[0].resolve()),
                "origin": origin,
                "license": info,
                "license_error": problem,
                "warnings": warnings,
            }
    return {
        "kind": kind,
        "name": name,
        "status": "pending",
        "path": None,
        "origin": None,
        "license": None,
        "license_error": None,
        "warnings": [],
    }


def _row(spec, key, path, origin, root):
    info, problem = _license(path)
    return {
        "kind": spec.name,
        "name": key,
        "path": str(path),
        "origin": origin,
        "license": info,
        "license_error": problem,
        "error": f"{path.name} aponta para fora de {root}." if _outside(path, root) else None,
    }


def listing(project, kind=None):
    """Inventário por tipo; um arquivo com problema vira linha com o erro, nunca derruba a lista."""
    kinds = [_kind(kind)] if kind else list(ASSET_KINDS.values())
    rows = []
    for spec in kinds:
        for origin, root in component_roots(project, spec):
            for key, paths in sorted(component_entries(root, spec).items()):
                rows.extend(_row(spec, key, p, origin, root) for p in paths)
    return rows
