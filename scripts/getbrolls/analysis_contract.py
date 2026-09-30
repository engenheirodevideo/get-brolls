"""Contrato dos arquivos de análise de mídia (`analysis/`): leitor estrito e validador.

Duas partes que não se misturam:

- **Leitor** (`read_json`, `parse`, `relative`): abre `analysis/<rel>` sem seguir link
  em nenhum componente e devolve o JSON cru, recusando chave repetida, NaN e infinito,
  aninhamento acima de `MAX_DEPTH`, chave com cara de segredo (recusada, não
  apagada), texto com cara de caminho absoluto (POSIX, `C:\\`, UNC, `~/`) e arquivo
  acima de `MAX_FILE`. Não conhece schema nenhum.
- **Validador** (`schema_name`, `check_doc`, `check_times`): confere um documento já
  em memória contra `schemas/<nome>.schema.json` (subconjunto de `sdk.jsonschema`) e
  as regras entre campos que esse subconjunto não expressa. Não toca o disco, então
  vale igual para o que o core lê e para o que um plugin entrega.

Segue um contrato de ingest externo: as mesmas regras de leitura e os mesmos códigos
de erro onde a regra é a mesma. Mapa entre os dois lados:

- `asset_id` (no contrato de ingest) e `media_id` (aqui) têm a mesma derivação: os 16
  primeiros hex do sha256 dos bytes da mídia. A mesma mídia tem o mesmo id nos dois; o
  sha256 inteiro fica em `media.json`.
- Tempo: o contrato de ingest normaliza para milissegundos inteiros
  (`start_ms = round(s * 1000)`); aqui o tempo fica em segundos, número finito `>= 0`,
  com `time_unit: "s"` no arquivo. A conversão é a mesma `round(s * 1000)`.
- A palavra da transcrição fica em `text` (convenção do sidecar HyperFrames), não em
  `word` como no contrato de ingest.
- Os estados de componente e a exigência de `reason` são os do contrato de ingest
  (`vocab.ANALYSIS_STATUSES`, `vocab.ANALYSIS_STATUSES_WITH_REASON`).

Leitura sem seguir link: no macOS e no Linux, desce componente por componente com
`dir_fd` e `O_NOFOLLOW`. Onde `os.open` não aceita `dir_fd` (Windows), faz `lstat` de
cada componente (link, junção ou pasta trocada por arquivo são recusados), abre o
arquivo e confere pelo `fstat` que é o mesmo que o `lstat` viu, e por fim confere que
nenhuma pasta do caminho mudou durante a leitura.
"""

import errno
import json
import math
import os
import re
import stat
from pathlib import Path, PurePosixPath

from . import versioning, vocab
from .sdk import jsonschema, schemas

COMPONENTS = ("media", "transcript", "scenes", "silence", "speakers", "visual")
FAILURE_STATUSES = vocab.ANALYSIS_STATUSES_WITH_REASON
DOCUMENTS = (*COMPONENTS, "analysis_index", "markers")
NAMESPACE = "getbrolls"
# Maior versão (`getbrolls.<nome>/<N>`) que este leitor entende, por documento.
SUPPORTED: dict[str, int] = dict.fromkeys(DOCUMENTS, 1)

MAX_FILE = 16 * 1024 * 1024
MAX_TEXT = 1024 * 1024
MAX_DEPTH = 32
SECRET_KEYS = ("password", "secret", "token", "credential", "api_key", "authorization")

_SCHEMA_RE = re.compile(r"([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)/([1-9][0-9]{0,5})")
# Texto com cara de caminho absoluto: POSIX (`/x`, não uma barra solta), `//host`, UNC
# (`\\host`), unidade do Windows (`C:\`, `C:/`) e pasta pessoal (`~/`).
_ABSOLUTE_RE = re.compile(r"(?:/[^\s/]|//|\\\\|[A-Za-z]:[\\/]|~[\\/])")
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: link simbólico ou junção no Windows
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_READ_FLAGS = os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
_DIR_FLAGS = _READ_FLAGS | getattr(os, "O_DIRECTORY", 0)


# Mesmo nome da exceção do contrato de ingest externo, de onde as regras vêm.
class Invalid(ValueError):  # noqa: N818
    """Arquivo ou documento fora do contrato: `code` estável e `where` (caminho ou campo)."""

    def __init__(self, code: str, where: str):
        self.code, self.where = code, where
        super().__init__(f"{code} em {where}")

    def as_dict(self) -> dict:
        """O problema como `{code, where}`, a forma que `check_doc` devolve."""
        return {"code": self.code, "where": self.where}


def _schema_string(name: str, version: int) -> str:
    """`getbrolls.<nome>/<N>`, pelo ajudante de versão compartilhado."""
    return versioning.schema_name(name, version)


# -- leitor -------------------------------------------------------------------


def _no_dupes(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Invalid("DUPLICATE_KEY", key)
        result[key] = value
    return result


def _bad_constant(value):
    raise Invalid("NONFINITE_NUMBER", value)


def _walk(value, where: str, depth: int = 1) -> None:
    """Recusa aninhamento, segredo, caminho absoluto, texto enorme e número não finito."""
    if isinstance(value, (dict, list)) and depth > MAX_DEPTH:
        raise Invalid("TOO_DEEP", where)
    if isinstance(value, dict):
        for key, item in value.items():
            if any(part in str(key).lower() for part in SECRET_KEYS):
                raise Invalid("SECRET_FIELD", f"{where}.{key}")
            _walk(item, f"{where}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{where}[{index}]", depth + 1)
    else:
        _check_scalar(value, where)


def _check_scalar(value, where: str) -> None:
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise Invalid("TEXT_TOO_LONG", where)
        if _ABSOLUTE_RE.match(value):
            raise Invalid("ABSOLUTE_PATH", where)
    elif type(value) is float and not math.isfinite(value):
        raise Invalid("NONFINITE_NUMBER", where)


def parse(raw: bytes, where: str) -> dict:
    """JSON UTF-8 de um objeto, pelas regras do leitor; `Invalid` quando não passa."""
    try:
        obj = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_no_dupes, parse_constant=_bad_constant)
    except RecursionError as exc:
        raise Invalid("TOO_DEEP", where) from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Invalid("INVALID_JSON", where) from exc
    if not isinstance(obj, dict):
        raise Invalid("EXPECTED_OBJECT", where)
    _walk(obj, "$")
    return obj


def relative(name) -> tuple[str, ...]:
    """Partes de um caminho relativo seguro (com `/`, sem `.`, `..`, `\\`, `:` nem NUL)."""
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise Invalid("UNSAFE_PATH", str(name))
    if PurePosixPath(name).is_absolute() or any(part in (".", "..", "") for part in name.split("/")):
        raise Invalid("UNSAFE_PATH", name)
    return tuple(name.split("/"))


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _read_fd(fd: int, rel: str, expected: tuple[int, int] | None = None) -> bytes:
    """Bytes do arquivo aberto em `fd`, conferindo tipo, identidade, hardlink e tamanho."""
    info = os.fstat(fd)
    if expected is not None and (info.st_dev, info.st_ino) != expected:
        raise Invalid("UNSAFE_LINK", rel)
    if not stat.S_ISREG(info.st_mode):
        raise Invalid("NOT_REGULAR_FILE", rel)
    if info.st_nlink != 1:
        raise Invalid("UNSAFE_LINK", rel)
    if info.st_size > MAX_FILE:
        raise Invalid("FILE_TOO_LARGE", rel)
    with os.fdopen(fd, "rb", closefd=False) as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise Invalid("FILE_TOO_LARGE", rel)
    return raw


def _refusal(exc: OSError, lstat_now, rel: str) -> Invalid:
    """Traduz a falha de abertura: link é `UNSAFE_LINK`, arquivo no meio é `UNSAFE_PATH`."""
    try:
        info = lstat_now()
    except OSError:
        return Invalid("FILE_UNAVAILABLE", rel)
    if _is_link(info) or exc.errno == errno.ELOOP:
        return Invalid("UNSAFE_LINK", rel)
    if exc.errno == errno.ENOTDIR:
        return Invalid("UNSAFE_PATH", rel)
    return Invalid("FILE_UNAVAILABLE", rel)


def _open_at(name: str, flags: int, dir_fd: int, rel: str) -> int:
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise _refusal(exc, lambda: os.stat(name, dir_fd=dir_fd, follow_symlinks=False), rel) from exc


def _read_with_dir_fd(root: Path, parts: tuple[str, ...], rel: str) -> bytes:
    try:
        fd = os.open(root, _DIR_FLAGS)
    except OSError as exc:
        raise Invalid("FILE_UNAVAILABLE", "root") from exc
    try:
        for component in parts[:-1]:
            nxt = _open_at(component, _DIR_FLAGS, fd, rel)
            os.close(fd)
            fd = nxt
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise Invalid("UNSAFE_PATH", rel)
        leaf = _open_at(parts[-1], _READ_FLAGS, fd, rel)
        try:
            return _read_fd(leaf, rel)
        finally:
            os.close(leaf)
    except OSError as exc:
        raise Invalid("FILE_UNAVAILABLE", rel) from exc
    finally:
        os.close(fd)


def _lstat(path: Path, rel: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise Invalid("FILE_UNAVAILABLE", rel) from exc


def _checked_dirs(root: Path, parts: tuple[str, ...], rel: str) -> list[tuple[Path, tuple[int, int]]]:
    """`lstat` da raiz e de cada pasta do caminho: nenhuma pode ser link ou arquivo."""
    seen: list[tuple[Path, tuple[int, int]]] = []
    current = root
    for index, component in enumerate(("", *parts[:-1])):
        current = current / component if component else current
        info = _lstat(current, rel)
        if _is_link(info) and index:
            raise Invalid("UNSAFE_LINK", rel)
        if not stat.S_ISDIR(info.st_mode):
            raise Invalid("UNSAFE_PATH", rel)
        seen.append((current, (info.st_dev, info.st_ino)))
    return seen


def _read_by_lstat(root: Path, parts: tuple[str, ...], rel: str) -> bytes:
    """Caminho sem `dir_fd`: `lstat` por componente e identidade conferida pelo `fstat`."""
    dirs = _checked_dirs(root, parts, rel)
    leaf = dirs[-1][0] / parts[-1]
    info = _lstat(leaf, rel)
    if _is_link(info):
        raise Invalid("UNSAFE_LINK", rel)
    if not stat.S_ISREG(info.st_mode):
        raise Invalid("NOT_REGULAR_FILE", rel)
    try:
        fd = os.open(leaf, _READ_FLAGS)
    except OSError as exc:
        raise _refusal(exc, lambda: os.lstat(leaf), rel) from exc
    try:
        raw = _read_fd(fd, rel, expected=(info.st_dev, info.st_ino))
    except OSError as exc:
        raise Invalid("FILE_UNAVAILABLE", rel) from exc
    finally:
        os.close(fd)
    for path, identity in dirs:
        now = _lstat(path, rel)
        if (now.st_dev, now.st_ino) != identity or (_is_link(now) and path != root):
            raise Invalid("UNSAFE_LINK", rel)
    return raw


def _dir_fd_available() -> bool:
    return os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd


def read_json(root: Path, rel: str) -> dict:
    """O objeto JSON de `root/rel`, lido sem seguir link; `Invalid` quando não passa."""
    parts = relative(rel)
    reader = _read_with_dir_fd if _dir_fd_available() else _read_by_lstat
    return parse(reader(Path(root), parts, rel), rel)


# -- validador ----------------------------------------------------------------


def schema_name(doc) -> tuple[str, int]:
    """(`nome`, `N`) de `"schema": "getbrolls.<nome>/<N>"`; recusa outro namespace e N novo."""
    value = doc.get("schema") if isinstance(doc, dict) else None
    match = _SCHEMA_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None or match[1] != NAMESPACE or match[2] not in SUPPORTED:
        raise Invalid("UNKNOWN_SCHEMA", "$.schema")
    name, version = match[2], int(match[3])
    if version > SUPPORTED[name]:
        raise Invalid("NEWER_SCHEMA", "$.schema")
    return name, version


def _seconds(value, where: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise Invalid("INVALID_TIME", where)
    return value


def check_times(items, where: str) -> None:
    """Todo tempo é finito e `>= 0`; `end` (quando não é null) não vem antes de `start`."""
    for index, item in enumerate(items):
        at = f"{where}[{index}]"
        if "time" in item:
            _seconds(item["time"], at)
            continue
        start = _seconds(item.get("start"), at)
        if item.get("end") is not None and _seconds(item["end"], at) < start:
            raise Invalid("INVALID_INTERVAL", at)


def _reason(doc):
    if doc["status"] in FAILURE_STATUSES and not (doc.get("reason") or "").strip():
        raise Invalid("MISSING_REASON", "$.reason")


def _media(doc):
    if doc["media_id"] != doc["sha256"][:16]:
        raise Invalid("IDENTITY_MISMATCH", "$.media_id")
    _path(doc["path"], "$.path")


def _path(value, where):
    try:
        relative(value)
    except Invalid as exc:
        raise Invalid("UNSAFE_PATH", where) from exc


def _transcript(doc):
    if doc["word_count"] != len(doc["words"]):
        raise Invalid("WORD_COUNT_MISMATCH", "$.word_count")
    check_times(doc["words"], "$.words")


def _speakers(doc):
    check_times(doc["turns"], "$.turns")
    known = {speaker["id"] for speaker in doc["speakers"]}
    for index, turn in enumerate(doc["turns"]):
        if turn["speaker"] not in known:
            raise Invalid("UNKNOWN_SPEAKER", f"$.turns[{index}].speaker")


def _unique(items, keys, where):
    seen: set = set()
    for index, item in enumerate(items):
        values = [(key, item[key]) for key in keys]
        if any(value in seen for value in values):
            raise Invalid("DUPLICATE_ID", f"{where}[{index}]")
        seen.update(values)


def _markers(doc):
    check_times(doc["markers"], "$.markers")
    _unique(doc["markers"], ("id",), "$.markers")


def _index(doc):
    for index, entry in enumerate(doc["media"]):
        _path(entry["path"], f"$.media[{index}].path")
    _unique(doc["media"], ("media_id", "path"), "$.media")


def _times_of(field):
    return lambda doc: check_times(doc[field], f"$.{field}")


_RULES = {
    "media": (_reason, _media),
    "transcript": (_reason, _transcript),
    "scenes": (_reason, _times_of("scenes")),
    "silence": (_reason, _times_of("silences")),
    "speakers": (_reason, _speakers),
    "visual": (_reason, _times_of("samples")),
    "markers": (_markers,),
    "analysis_index": (_index,),
}


def check_doc(doc, name: str) -> list[dict]:
    """Problemas de `doc` como `analysis/<nome>`, em `[{code, where}]`; vazio quando passa.

    Ordem: versão declarada, regras do leitor (as mesmas de `parse`, para documento que
    não veio do disco), schema publicado e, só com o schema limpo, as regras entre
    campos. `name` fora de `DOCUMENTS` é erro de programação (`ValueError`).
    """
    if name not in SUPPORTED:
        raise ValueError(f"Documento de análise desconhecido: {name}")
    if not isinstance(doc, dict):
        return [Invalid("EXPECTED_OBJECT", "$").as_dict()]
    try:
        found, version = schema_name(doc)
        if doc["schema"] != _schema_string(name, version) or found != name:
            raise Invalid("SCHEMA_MISMATCH", "$.schema")
        _walk(doc, "$")
    except Invalid as exc:
        return [exc.as_dict()]
    problems = [{"code": "SCHEMA_VIOLATION", "where": text} for text in jsonschema.errors(doc, schemas.load(name))]
    if problems:
        return problems
    for rule in _RULES[name]:
        try:
            rule(doc)
        except Invalid as exc:
            problems.append(exc.as_dict())
    return problems
