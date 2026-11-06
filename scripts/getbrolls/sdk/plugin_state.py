"""O `plugins.json` (`$GB_HOME/plugins.json`): pins, últimos pins e origens dos plugins instalados.

Lê com os tetos e as conferências de sempre (`json_read`, versão do schema, o schema `plugins_state`) e grava
por troca atômica. Não decide nada sobre o conteúdo do plugin; isso é de `plugin_digest` e de `loader`.
`loader` reexporta `read_state`, `write_state` e `state_path`.
"""

import errno
import json

from .. import _paths, effects, json_read, versioning
from ..ledger import atomic_write
from . import jsonschema, schemas


def state_path():
    """`$GB_HOME/plugins.json`: pins, últimos pins e origens."""
    return _paths.gb_home() / "plugins.json"


def _valid_pin(entry):
    return isinstance(entry, dict) and isinstance(entry.get("sha256"), str) and isinstance(entry.get("version"), str)


# Chaves opcionais da origem gravada em `sources.<id>`, todas `str` ou `null`: a
# ref e a subpasta de um repositório git e, para quem instala de um marketplace, o
# nome dele, o tier da entrada e o commit do índice usado.
ORIGIN_OPTIONAL_KEYS = ("ref", "subdir", "marketplace", "tier", "index_commit")


def _valid_origin(entry):
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("source"), str)
        and all(entry.get(key) is None or isinstance(entry.get(key), str) for key in ("commit", *ORIGIN_OPTIONAL_KEYS))
    )


def _valid_map(value, valid_entry):
    """`value` é um dict cujas entradas passam todas em `valid_entry`?"""
    return isinstance(value, dict) and all(valid_entry(entry) for entry in value.values())


def _valid_state(data):
    """`enabled` obrigatório; `last_pins` (o pin guardado pelo `disable`) e `sources`
    (origem/commit gravados pelo `plugins install`) são opcionais: um plugins.json de
    antes dessas versões continua válido sem eles."""
    return (
        _valid_map(data.get("enabled"), _valid_pin)
        and _valid_map(data.get("last_pins", {}), _valid_pin)
        and _valid_map(data.get("sources", {}), _valid_origin)
    )


# Família do campo `schema` do plugins.json (`getbrolls.plugins/<N>`); o schema é `plugins_state`.
STATE_FAMILY = "plugins"
STATE_SCHEMA = "plugins_state"
# Teto de leitura do plugins.json: pins e o mapa por arquivo de cada plugin cabem com folga em 4 MiB,
# abaixo do teto padrão do `json_read` (o pior caso de memória cai junto).
STATE_MAX_BYTES = 4 * 1024 * 1024


def read_state():
    """`plugins.json` como dict (`{"enabled": {}}` sem arquivo); conteúdo inválido vira `ValueError`.

    `schema` ausente vale 1 (o plugins.json de antes do campo); versão mais nova é recusada com a
    frase padrão, sem tocar no arquivo; o documento passa pelas conferências do código e pelo schema.
    Arquivo que não dá para ler (sem permissão, disco) e pasta, FIFO ou dispositivo no lugar dele são
    `OSError` (IO_ERROR): o arquivo pode estar certo, e a recusa não manda apagá-lo."""
    path = state_path()
    if not _paths.strict_exists(path):
        return {"enabled": {}}
    try:
        data = json_read.read(path, max_bytes=STATE_MAX_BYTES)
    except json_read.JsonReadError as exc:
        if exc.reason == "not_regular":
            raise OSError(errno.EINVAL, "não é um arquivo comum", str(path)) from None
        data = None
    shown = _paths.gb_home_label(path) or str(path)
    invalid = f"plugins.json inválido em {shown}. Corrija ou apague o arquivo para recomeçar sem plugins."
    if isinstance(data, dict):
        version = versioning.read_schema(
            data, STATE_FAMILY, schemas.version(STATE_SCHEMA), label="plugins.json", invalid=invalid
        )
        current = dict(data)  # em memória, o padrão do campo ausente; o arquivo não muda
        current.setdefault(versioning.SCHEMA_FIELD, versioning.schema_name(STATE_FAMILY, version))
        if _valid_state(data) and not jsonschema.errors(current, schemas.load(STATE_SCHEMA)):
            return data
    raise ValueError(invalid)


def write_state(data):
    """Grava `plugins.json` (troca atômica), criando `$GB_HOME` se preciso, com a versão do schema."""
    effects.mkdir(_paths.gb_home(), parents=True, exist_ok=True)
    stamped = versioning.stamp_schema(data, STATE_FAMILY, schemas.version(STATE_SCHEMA))
    atomic_write(state_path(), json.dumps(stamped, ensure_ascii=False, indent=2) + "\n", unique=True)
