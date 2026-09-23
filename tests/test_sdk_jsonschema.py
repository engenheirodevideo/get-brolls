"""Validador stdlib do subconjunto de JSON Schema usado pelo SDK."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT

from getbrolls.sdk.jsonschema import SchemaError, check_schema, errors, validate

PERSON = {
    "type": "object",
    "required": ["name"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 10, "pattern": "^[a-z]+$"},
        "age": {"type": ["integer", "null"], "minimum": 0, "maximum": 150},
        "tags": {"type": "array", "items": {"enum": ["a", "b"]}, "uniqueItems": True, "maxItems": 2},
        "kind": {"const": 1},
    },
}


class SubsetTests(unittest.TestCase):
    def test_valid_value_has_no_errors(self):
        self.assertEqual([], errors({"name": "ana", "age": None, "tags": ["a"], "kind": 1}, PERSON))

    def test_each_keyword_reports_its_path(self):
        found = errors({"name": "Ana1", "age": -1, "tags": ["a", "a", "c"], "kind": 2, "x": 1}, PERSON)
        joined = " | ".join(found)
        for fragment in ("$.name", "$.age", "$.tags", "$.kind", "$.x: campo não previsto"):
            self.assertIn(fragment, joined)

    def test_required_field(self):
        self.assertIn("$.name: obrigatório", errors({}, PERSON))

    def test_bool_is_not_integer_and_enum_is_type_strict(self):
        self.assertTrue(errors({"name": "a", "age": True}, PERSON))
        self.assertTrue(errors(True, {"enum": [1]}))
        self.assertTrue(errors(1.0, {"const": 1}))

    def test_additional_properties_schema_validates_extra_keys(self):
        schema = {"type": "object", "additionalProperties": {"type": "string"}}
        self.assertEqual([], errors({"a": "x"}, schema))
        self.assertTrue(errors({"a": 1}, schema))

    def test_validate_raises_value_error_with_label(self):
        with self.assertRaises(ValueError) as caught:
            validate({}, PERSON, label="Pessoa")
        self.assertIn("Pessoa fora do schema", str(caught.exception))

    def test_check_schema_refuses_keywords_outside_subset(self):
        check_schema(PERSON)
        with self.assertRaises(SchemaError) as caught:
            check_schema({"type": "object", "properties": {"a": {"oneOf": []}}})
        self.assertIn("oneOf", str(caught.exception))
        self.assertIn("#/properties/a", str(caught.exception))

    def test_core_schemas_use_only_the_subset(self):
        import json

        for name in ("brief.schema.json", "candidate.schema.json"):
            check_schema(json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
