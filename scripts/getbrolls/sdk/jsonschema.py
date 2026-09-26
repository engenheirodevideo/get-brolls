"""Validador stdlib do subconjunto de JSON Schema que o SDK aceita.

O runtime do get-brolls não tem dependência nenhuma; em vez de puxar `jsonschema`,
este módulo cobre só as palavras-chave de `SUPPORTED` e recusa as demais —
validar pela metade seria pior do que recusar.
"""

import json
import re

SUPPORTED = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "default",
        "type",
        "properties",
        "required",
        "enum",
        "const",
        "pattern",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "additionalProperties",
    }
)


class SchemaError(ValueError):
    """O schema usa algo fora do subconjunto suportado."""


def check_schema(schema, path="#"):
    """Recusa (`SchemaError`) palavra-chave fora do subconjunto suportado."""
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: schema tem que ser um objeto JSON")
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise SchemaError(f"{path}: palavra-chave fora do subconjunto suportado: {', '.join(sorted(unknown))}")
    for name, sub in (schema.get("properties") or {}).items():
        check_schema(sub, f"{path}/properties/{name}")
    if isinstance(schema.get("items"), dict):
        check_schema(schema["items"], f"{path}/items")
    if isinstance(schema.get("additionalProperties"), dict):
        check_schema(schema["additionalProperties"], f"{path}/additionalProperties")


_TYPE_CHECKS = {
    "integer": lambda value: type(value) is int,
    "number": lambda value: type(value) in (int, float),
    "boolean": lambda value: type(value) is bool,
    "null": lambda value: value is None,
    "string": lambda value: isinstance(value, str),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


def _is_type(value, name):
    check = _TYPE_CHECKS.get(name)
    return check(value) if check else False


def _same(a, b):
    """Igualdade JSON: `True` não é `1` e `1.0` não é `1`."""
    return type(a) is type(b) and a == b


def _check_scalar(value, schema, path):
    found = []
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            found.append(f"{path}: não casa com o padrão {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            found.append(f"{path}: menor que {schema['minLength']} caracteres")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            found.append(f"{path}: maior que {schema['maxLength']} caracteres")
    if type(value) in (int, float):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"{path}: menor que {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            found.append(f"{path}: maior que {schema['maximum']}")
    return found


def _check_array(value, schema, path):
    found = []
    if "minItems" in schema and len(value) < schema["minItems"]:
        found.append(f"{path}: menos de {schema['minItems']} itens")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        found.append(f"{path}: mais de {schema['maxItems']} itens")
    if schema.get("uniqueItems"):
        seen = [json.dumps(v, sort_keys=True) for v in value]
        if len(seen) != len(set(seen)):
            found.append(f"{path}: itens repetidos")
    if isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            found += errors(item, schema["items"], f"{path}[{index}]")
    return found


def _check_object(value, schema, path):
    found = [f"{path}.{name}: obrigatório" for name in schema.get("required", []) if name not in value]
    props = schema.get("properties", {})
    extra = schema.get("additionalProperties", True)
    for name, sub in value.items():
        if name in props:
            found += errors(sub, props[name], f"{path}.{name}")
        elif extra is False:
            found.append(f"{path}.{name}: campo não previsto no schema")
        elif isinstance(extra, dict):
            found += errors(sub, extra, f"{path}.{name}")
    return found


def errors(value, schema, path="$"):
    """Lista de problemas; vazia quando `value` casa com `schema`."""
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_is_type(value, k) for k in kinds):
            return [f"{path}: esperado {'|'.join(kinds)}"]
    found = []
    if "const" in schema and not _same(value, schema["const"]):
        found.append(f"{path}: tem que ser {schema['const']!r}")
    if "enum" in schema and not any(_same(value, option) for option in schema["enum"]):
        found.append(f"{path}: valor fora de {schema['enum']}")
    found += _check_scalar(value, schema, path)
    if isinstance(value, list):
        found += _check_array(value, schema, path)
    if isinstance(value, dict):
        found += _check_object(value, schema, path)
    return found


def validate(value, schema, label="dados"):
    """Levanta `ValueError` com até 5 problemas quando `value` não segue `schema`."""
    problems = errors(value, schema)
    if problems:
        raise ValueError(f"{label} fora do schema: " + "; ".join(problems[:5]))
