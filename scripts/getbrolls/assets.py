"""Rotas de componentes: onde mora cada coisa que o roteiro cita.

`[SFX: whoosh]` procura `whoosh.*` em `assets/sfx/` do projeto, depois, quando o
`project.json` tem cliente registrado, em `<pasta do cliente>/assets/sfx/`, e por
fim em `~/.getbrolls/assets/sfx/`. A-ROLL é só do projeto. Cliente do `project.json`
que não está registrado gera o aviso `CLIENT_UNREGISTERED` e é pulado; pasta de
componentes do cliente que é link também é pulada. Só o nível da pasta, sem recursão, só arquivo regular
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

from . import versioning, vocab
from ._paths import strict_is_dir, strict_is_file
from .roteiro import fold
from .rules import home_dir

LICENSE_SUFFIX = ".licenca.json"
LICENSE_FIELDS = ("origem", "licenca", "credito")
# O `.licenca.json` é arquivo da pessoa, fora de `schemas/`: lê a versão 1 (campo opcional).
LICENSE_SUPPORTED = 1
# Grafia em inglês da versão de testes da 2.6: recusada com a chave em português (sem alias).
_ENGLISH_KEYS = {"source": "origem", "license_name": "licenca", "attribution": "credito"}
_NAME = re.compile(r"^[\w][\w \-]{0,79}$")
VIDEO = vocab.VIDEO_EXTENSIONS
AUDIO = vocab.AUDIO_EXTENSIONS
IMAGE = vocab.IMAGE_EXTENSIONS


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
    "imagem": AssetKind("imagem", "assets/imagem", IMAGE, licensed=True, personal=True),
    "composicao": AssetKind("composicao", "assets/composicao", (".html", ".json"), licensed=False, personal=True),
}

# Pastas de `assets/` do projeto na versão de testes da 2.6, que o código não lê mais.
OLD_PROJECT_FOLDERS = {"composicao": "assets/composicoes"}


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


_WARNED_CLIENTS = set()


def reset_client_warnings():
    """Esquece quais clientes não registrados já foram avisados neste processo."""
    _WARNED_CLIENTS.clear()


def project_client(project):
    """`(slug, pasta registrada)` do cliente do `project.json`; `(None, None)` sem cliente utilizável.

    Cliente fora do registro (ou com a pasta quebrada) vira o aviso `CLIENT_UNREGISTERED`,
    uma vez por processo e cliente, e o projeto segue sem ele.
    """
    from . import clients, labels, layout, runtime  # local: clients importa ledger e runtime

    doc = layout.info(project).doc
    slug = doc.get("client") if doc else None
    if not slug:
        return None, None
    try:
        root = clients.client_root(slug)
        if root is not None:
            clients.load_client(slug)
    except ValueError as exc:
        root, problem = None, str(exc)
    else:
        problem = f'O cliente "{slug}" do project.json não está registrado'
    if root is None:
        if slug not in _WARNED_CLIENTS:
            _WARNED_CLIENTS.add(slug)
            runtime.record_warning(
                "CLIENT_UNREGISTERED",
                labels.text(f"{problem}; componentes do cliente ignorados (client --action add registra)."),
            )
        return slug, None
    clients.warn_old_shape(slug, root)
    return slug, root


def _client_folder(project, spec):
    """`<pasta do cliente>/assets/<tipo>/` do cliente do projeto (ver `client_components`)."""
    _, root = project_client(project)
    return None if root is None else _components_of(root, spec)


def client_components(slug, spec):
    """`<pasta do cliente>/assets/<tipo>/` do cliente registrado `slug`, ou `None`."""
    from . import clients

    try:
        clients.load_client(slug)
        root = clients.client_root(slug)
    except ValueError:
        return None
    return None if root is None else _components_of(root, spec)


def _components_of(root, spec):
    """A pasta do tipo em `root/assets/`, quando existe e nenhuma pasta do caminho é link."""
    from .sdk.files import is_link

    shelf = root / "assets"
    folder = shelf / spec.folder.removeprefix("assets/")
    if is_link(shelf) or is_link(folder) or not strict_is_dir(folder):
        return None
    return folder


def component_roots(project, spec):
    """(origem, pasta) onde um componente pode morar: projeto, cliente do projeto e pessoal, nessa ordem."""
    root = Path(project).expanduser().resolve()
    old = OLD_PROJECT_FOLDERS.get(spec.name)
    if old and strict_is_dir(root / old):
        from . import renamed  # tardio: renamed só depende de logs e _paths

        renamed.old_shape(f"{old}/ do projeto", f"Renomeie a pasta `{old}/` para `{spec.folder}/`.", where=str(root))
    yield "project", root / spec.folder
    if spec.personal:
        folder = _client_folder(project, spec)
        if folder is not None:
            yield "client", folder
        yield "personal", home_dir() / "assets" / spec.folder.removeprefix("assets/")


def component_entries(root, spec):
    """Arquivos candidatos de uma pasta, agrupados pela chave de comparação.

    Fica de fora: arquivo oculto (`.x`, `._x` do macOS), sidecar de licença,
    extensão fora da tabela, pasta e symlink quebrado.
    """
    groups = {}
    if not strict_is_dir(root):
        return groups
    for path in sorted(root.iterdir()):
        name = unicodedata.normalize("NFC", path.name)
        if name.startswith(".") or name.lower().endswith(LICENSE_SUFFIX):
            continue
        if path.suffix.lower() not in spec.extensions or not strict_is_file(path):
            continue
        groups.setdefault(fold(Path(name).stem), []).append(path)
    return groups


def _license(path):
    """(licença, erro): sem sidecar = (None, None); sidecar ruim = (None, motivo), nunca exceção."""
    sidecar = path.with_name(path.stem + LICENSE_SUFFIX)
    # lenient: same folder as a component strict_is_file just stat'ed; documented never-raise
    if not sidecar.is_file():
        return None, None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        from . import labels  # local, como em `_place`

        return None, f"{sidecar.name} não pôde ser lido: {labels.reason(exc)}."
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, f"{sidecar.name} não é um JSON válido: conserte ou apague o arquivo."
    return _normalize_license(sidecar.name, data)


def _normalize_license(name, data):
    """(licença, erro): só as chaves em português; a grafia em inglês é recusada com a chave certa."""
    *first, last = LICENSE_FIELDS
    missing = f"{name} precisa de {', '.join(first)} e {last} preenchidos, em texto."
    if not isinstance(data, dict):
        return None, missing
    english = [
        f"{key} → {portuguese}"
        for english_key, portuguese in _ENGLISH_KEYS.items()
        for key in data
        if key.casefold() == english_key
    ]
    if english:
        return None, f"{name} usa chaves em inglês, que saíram; troque {', '.join(english)}."
    try:
        versioning.read_version(data, name, LICENSE_SUPPORTED)
    except ValueError as exc:
        return None, str(exc)
    fields = {k: v for k, v in data.items() if k != versioning.FIELD}
    if any(not isinstance(v, str) for v in fields.values()) or any(
        not fields.get(k, "").strip() for k in LICENSE_FIELDS
    ):
        return None, missing
    return {
        **{k: fields[k] for k in LICENSE_FIELDS},
        **{k: v for k, v in fields.items() if k not in LICENSE_FIELDS},
    }, None


def _outside(path, root):
    return not path.resolve().is_relative_to(root.resolve())


def _place(project, origin, root):
    """Pasta de componentes para uma mensagem: `<projeto>/…`, `<cliente>/…` ou `$GB_HOME/…`.

    A origem diz de quem é a pasta (`component_roots`): a do cliente é `<cliente>/assets/<tipo>`,
    sem consultar o cliente de novo; nada aqui lê arquivo nem troca o erro de quem chama."""
    from . import labels  # local, como em `project_client`

    extra = [(root.parent.parent, "<cliente>")] if origin == "client" else []
    return labels.shown(root, project=project, extra=extra)


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
            raise ValueError(f'"{name}" é ambíguo em {_place(project, origin, root)}: {listed}. Deixe só um.')
        if matches:
            if _outside(matches[0], root):
                where = _place(project, origin, root)
                raise ValueError(f"{matches[0].name} aponta para fora de {where}: o componente tem que morar na pasta.")
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


def _row(project, spec, key, path, found):
    """Uma linha do inventário; `found` é o `(origem, pasta)` de `component_roots`."""
    origin, root = found
    info, problem = _license(path)
    return {
        "kind": spec.name,
        "name": key,
        "path": str(path),
        "origin": origin,
        "license": info,
        "license_error": problem,
        "error": f"{path.name} aponta para fora de {_place(project, origin, root)}." if _outside(path, root) else None,
    }


def listing(project, kind=None):
    """Inventário por tipo; um arquivo com problema vira linha com o erro, nunca derruba a lista."""
    kinds = [_kind(kind)] if kind else list(ASSET_KINDS.values())
    rows = []
    for spec in kinds:
        for found in component_roots(project, spec):
            for key, paths in sorted(component_entries(found[1], spec).items()):
                rows.extend(_row(project, spec, key, p, found) for p in paths)
    return rows
