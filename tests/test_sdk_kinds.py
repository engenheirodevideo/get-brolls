"""Uma grafia só para os tipos de extensão: singular (CLI, registro) e plural (manifesto)."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls.cli import build_parser
from getbrolls.sdk import kinds, manifest, registry, scaffold, testing
from getbrolls.sdk.api import PluginApi
from getbrolls.sdk.errors import ApiError
from getbrolls.sdk.registry import Registry


def _kind_choices():
    """As `choices` de `plugins --kind`, lidas do parser de verdade."""
    parser = build_parser()
    subparsers = next(a for a in parser._actions if a.choices and "plugins" in a.choices)  # pylint: disable=protected-access
    plugins = dict(subparsers.choices or {})["plugins"]
    kind = next(a for a in plugins._actions if a.dest == "kind")  # pylint: disable=protected-access
    return list(kind.choices or [])


class KindMapTests(unittest.TestCase):
    def test_maps_are_inverse_and_cover_supported_kinds(self):
        self.assertEqual(("provider", "preset", "route", "command", "exporter", "resolver"), kinds.SUPPORTED_SINGULAR)
        self.assertEqual(
            ("providers", "presets", "routes", "commands", "exporters", "resolvers"), kinds.SUPPORTED_PLURAL
        )
        self.assertEqual(kinds.SINGULAR_TO_PLURAL, {v: k for k, v in kinds.PLURAL_TO_SINGULAR.items()})
        self.assertEqual(set(kinds.SUPPORTED_SINGULAR), set(kinds.SINGULAR_TO_PLURAL))
        for single, plural in zip(kinds.SUPPORTED_SINGULAR, kinds.SUPPORTED_PLURAL, strict=True):
            self.assertEqual(plural, kinds.plural(single))
            self.assertEqual(single, kinds.singular(plural))

    def test_unknown_kinds_raise_value_error(self):
        for bad in ("engine", "providerss", "", "exporte"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    kinds.plural(bad)
                with self.assertRaises(ValueError):
                    kinds.singular(bad)

    def test_manifest_registry_and_testing_use_the_same_kinds(self):
        self.assertEqual(kinds.SUPPORTED_PLURAL, manifest.SUPPORTED_KINDS)
        self.assertEqual(kinds.SUPPORTED_SINGULAR, registry.KINDS)
        checked = testing.check_registry(Registry(), "demo")
        self.assertEqual(kinds.SUPPORTED_PLURAL, tuple(checked))

    def test_scaffold_kinds_are_a_subset(self):
        self.assertLessEqual(set(scaffold.KINDS), set(kinds.SUPPORTED_SINGULAR))

    def test_cli_kind_choices_come_from_scaffold(self):
        self.assertEqual(list(scaffold.KINDS), list(_kind_choices()))

    def test_contributed_lists_non_empty_kinds_in_singular(self):
        contributes = {"routes": ["x"], "providers": ["x"], "presets": [], "exporters": ["y"], "engines": []}
        self.assertEqual(["exporter", "provider", "route"], kinds.contributed(contributes))
        self.assertEqual([], kinds.contributed({}))

    def test_api_error_uses_singular_spelling(self):
        empty = {kind: [] for kind in manifest.CONTRIBUTION_KINDS}
        plugin_manifest = {
            "id": "demo",
            "contributes": empty,
            "permissions": {"network": [], "env": [], "paths": []},
        }
        api = PluginApi(plugin_manifest, Registry())
        with self.assertRaises(ApiError) as caught:
            api._own("exporters", "demo")  # pylint: disable=protected-access
        message = str(caught.exception)
        self.assertIn("exporter 'demo'", message)
        self.assertNotIn("exporte ", message)


if __name__ == "__main__":
    unittest.main()
