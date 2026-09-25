"""Rotas de componentes: onde mora cada coisa que o roteiro cita.

`[SFX: whoosh]` procura `whoosh.*` em `assets/sfx/` do projeto e depois em
`~/.getbrolls/assets/sfx/`. Só o nível da pasta, sem recursão. A decisão de
ambiguidade sai da listagem da pasta (e não do sistema de arquivos), então dá o
mesmo resultado em Linux, macOS e Windows. A biblioteca pessoal não transfere
licença: cada arquivo carrega a própria, em `<nome>.licenca.json`.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .roteiro import fold
from .rules import home_dir

LICENSE_SUFFIX = ".licenca.json"
_NAME = re.compile(r"^[\w][\w \-]{0,79}$")
VIDEO = (".mp4", ".mov", ".m4v")
AUDIO = (".wav", ".mp3", ".m4a", ".aac")


@dataclass(frozen=True)
class AssetKind:
    name: str
    folder: str
    extensions: tuple[str, ...]
    licensed: bool
    personal: bool


ASSET_KINDS = {
    "aroll": AssetKind("aroll", "aroll", VIDEO, licensed=False, personal=False),
    "marca": AssetKind("marca", "assets/marca", (".png", ".svg", ".webp", *VIDEO[:2]), licensed=True, personal=True),
    "lettering": AssetKind("lettering", "assets/lettering", (".json", ".html"), licensed=False, personal=True),
    "sfx": AssetKind("sfx", "assets/sfx", AUDIO, licensed=True, personal=True),
    "musica": AssetKind("musica", "assets/musica", AUDIO, licensed=True, personal=True),
    "composicao": AssetKind("composicao", "assets/composicoes", (".html", ".json"), licensed=False, personal=True),
}


def _kind(kind):
    if kind not in ASSET_KINDS:
        raise ValueError(f'Tipo de componente desconhecido "{kind}"; use: {", ".join(ASSET_KINDS)}.')
    return ASSET_KINDS[kind]


def _roots(project, spec):
    yield "project", Path(project).expanduser().resolve() / spec.folder
    if spec.personal:
        yield "personal", home_dir() / "assets" / spec.folder.removeprefix("assets/")


def _entries(root, spec):
    """Arquivos candidatos de uma pasta, agrupados pela chave de comparação."""
    groups = {}
    if not root.is_dir():
        return groups
    for path in sorted(root.iterdir()):
        name = path.name
        if name.lower().endswith(LICENSE_SUFFIX) or path.suffix.lower() not in spec.extensions:
            continue
        if not (path.is_file() or path.is_symlink()):
            continue
        groups.setdefault(fold(path.stem), []).append(path)
    return groups


def _license(path):
    sidecar = path.with_name(path.stem + LICENSE_SUFFIX)
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"{sidecar.name} não é um JSON válido: conserte ou apague o arquivo.") from None
    if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
        raise ValueError(f"{sidecar.name} precisa ser um objeto com origem, licenca e credito em texto.")
    return data


def _confined(path, root):
    real = path.resolve()
    if not real.is_relative_to(root.resolve()):
        raise ValueError(f"{path.name} aponta para fora de {root}: o componente tem que morar na pasta.")
    return real


def resolve(project, kind, name):
    spec = _kind(kind)
    if not isinstance(name, str) or ".." in name or not _NAME.match(name.strip()):
        raise ValueError(f'Nome de componente inválido "{name}": use letras, números, espaço, - ou _.')
    key = fold(name)
    for origin, root in _roots(project, spec):
        matches = _entries(root, spec).get(key, [])
        if len(matches) > 1:
            listed = ", ".join(p.name for p in matches)
            raise ValueError(f'"{name}" é ambíguo em {root}: {listed}. Deixe só um.')
        if matches:
            path = _confined(matches[0], root)
            info = _license(matches[0])
            warnings = []
            if spec.licensed and info is None:
                warnings.append(f"{matches[0].name}: licença não registrada ({matches[0].stem}{LICENSE_SUFFIX})")
            return {
                "kind": kind,
                "name": name,
                "status": "found",
                "path": str(path),
                "origin": origin,
                "license": info,
                "warnings": warnings,
            }
    return {
        "kind": kind,
        "name": name,
        "status": "pending",
        "path": None,
        "origin": None,
        "license": None,
        "warnings": [],
    }


def listing(project, kind=None):
    kinds = [_kind(kind)] if kind else list(ASSET_KINDS.values())
    rows = []
    for spec in kinds:
        for origin, root in _roots(project, spec):
            for key, paths in sorted(_entries(root, spec).items()):
                rows.extend(
                    {"kind": spec.name, "name": key, "path": str(p), "origin": origin, "license": _license(p)}
                    for p in paths
                )
    return rows
