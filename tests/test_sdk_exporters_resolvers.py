"""Exportadores e resolvedores de plugin: contratos, registro, `PluginApi` e carga (só os tipos)."""

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.sdk import (
    RESOLVER_KINDS,
    ExporterSpec,
    ExportResult,
    MediaRequest,
    ResolverHit,
    ResolverSpec,
    loader,
)
from getbrolls.sdk.api import ApiError, PluginApi
from getbrolls.sdk.contracts import CORE
from getbrolls.sdk.registry import Registry, RegistryError, get_registry, reset_registry


def export_fn(plan, options):
    return ExportResult({"index.html": "<p>ok</p>"})


def resolve_fn(kind, name):
    return None


def exporter_spec(name="demo_html", description="Exporta HTML", export=export_fn):
    return ExporterSpec(name, description, export)


def resolver_spec(name="demo", kinds=("sfx",), resolve=resolve_fn):
    return ResolverSpec(name, cast("tuple[str, ...]", kinds), resolve)


class ContractTests(unittest.TestCase):
    def test_resolver_kinds_never_include_aroll_or_marca(self):
        self.assertEqual(("sfx", "musica"), RESOLVER_KINDS)

    def test_results_are_frozen_with_empty_defaults(self):
        result = ExportResult({"index.html": "x"})
        self.assertEqual([], result.media)
        self.assertEqual([], result.notes)
        self.assertIsNot(result.media, ExportResult({}).media)
        self.assertIsNone(ResolverHit("/acervo/a.wav").license)
        request = MediaRequest("m1", "assets/a.wav")
        for frozen, field in ((result, "files"), (request, "dest"), (ResolverHit(Path("/a.wav")), "path")):
            with self.subTest(field=field), self.assertRaises(dataclasses.FrozenInstanceError):
                setattr(frozen, field, None)


class RegistryExporterTests(unittest.TestCase):
    def test_exporter_is_registered_with_its_owner(self):
        reg = Registry()
        reg.add_exporter(exporter_spec(), owner="demo")
        self.assertEqual(("demo_html",), reg.exporter_names())
        self.assertEqual("demo", reg.owner("exporter", "demo_html"))
        spec = reg.exporter("demo_html")
        assert spec is not None
        self.assertEqual("Exporta HTML", spec.description)
        self.assertIsNone(reg.exporter("outro"))

    def test_core_wins_on_collision(self):
        reg = Registry()
        reg.add_exporter(exporter_spec("hyperframes"), owner=CORE)
        with self.assertRaises(RegistryError) as caught:
            reg.add_exporter(exporter_spec("hyperframes"), owner="intruso")
        self.assertIn("core", str(caught.exception))
        self.assertEqual(CORE, reg.owner("exporter", "hyperframes"))

    def test_spec_is_rebuilt_as_a_plain_exporter_spec(self):
        class Sneaky(ExporterSpec):
            pass

        reg = Registry()
        reg.add_exporter(Sneaky("demo_html", "Exporta HTML", export_fn), owner="demo")
        self.assertIs(ExporterSpec, type(reg.exporter("demo_html")))

    def test_bad_exporters_are_refused(self):
        cases = {
            "not a spec": object(),
            "bad name": exporter_spec("Demo Html"),
            "empty description": exporter_spec(description="  "),
            "long description": exporter_spec(description="x" * 201),
            "description not text": exporter_spec(description=cast("str", 3)),
            "export not callable": exporter_spec(export=cast("Any", "export")),
        }
        for label, spec in cases.items():
            reg = Registry()
            with self.subTest(label), self.assertRaises(RegistryError):
                reg.add_exporter(cast("ExporterSpec", spec), owner="demo")
            self.assertEqual((), reg.exporter_names())


class RegistryResolverTests(unittest.TestCase):
    def test_resolver_keeps_owner_kinds_and_roots(self):
        reg = Registry()
        reg.add_resolver(resolver_spec(kinds=["sfx", "musica"]), owner="demo", roots=("/acervo",))
        spec = reg.resolver("demo")
        assert spec is not None
        self.assertEqual(("sfx", "musica"), spec.kinds)
        self.assertIs(ResolverSpec, type(spec))
        self.assertEqual(("/acervo",), reg.resolver_roots("demo"))
        self.assertEqual("demo", reg.owner("resolver", "demo"))

    def test_core_wins_on_collision(self):
        reg = Registry()
        reg.add_resolver(resolver_spec("sons"), owner=CORE, roots=())
        with self.assertRaises(RegistryError):
            reg.add_resolver(resolver_spec("sons"), owner="intruso", roots=("/outro",))
        self.assertEqual(CORE, reg.owner("resolver", "sons"))
        self.assertEqual((), reg.resolver_roots("sons"))

    def test_resolvers_are_ordered_by_owner_then_registration(self):
        reg = Registry()
        reg.add_resolver(resolver_spec("zeta"), owner="zeta", roots=())
        reg.add_resolver(resolver_spec("alfa_b", kinds=("sfx", "musica")), owner="alfa", roots=())
        reg.add_resolver(resolver_spec("alfa_a"), owner="alfa", roots=())
        reg.add_resolver(resolver_spec("alfa_musica", kinds=("musica",)), owner="alfa", roots=())
        self.assertEqual(
            [("alfa", "alfa_b"), ("alfa", "alfa_a"), ("zeta", "zeta")],
            [(owner, spec.name) for owner, spec in reg.resolvers_for("sfx")],
        )
        self.assertEqual(["alfa_b", "alfa_musica"], [spec.name for _, spec in reg.resolvers_for("musica")])
        self.assertEqual([], reg.resolvers_for("marca"))

    def test_bad_resolvers_are_refused(self):
        cases = {
            "not a spec": (object(), ()),
            "bad name": (resolver_spec("Sons Ruins"), ()),
            "marca": (resolver_spec(kinds=["marca"]), ()),
            "aroll": (resolver_spec(kinds=["aroll"]), ()),
            "empty kinds": (resolver_spec(kinds=[]), ()),
            "repeated kinds": (resolver_spec(kinds=["sfx", "sfx"]), ()),
            "kinds as text": (resolver_spec(kinds="sfx"), ()),
            "kinds not text": (resolver_spec(kinds=[1]), ()),
            "resolve not callable": (resolver_spec(resolve=cast("Any", None)), ()),
            "roots as list": (resolver_spec(), ["/acervo"]),
            "roots not text": (resolver_spec(), (Path("/acervo"),)),
        }
        for label, (spec, roots) in cases.items():
            reg = Registry()
            with self.subTest(label), self.assertRaises(RegistryError):
                reg.add_resolver(cast("ResolverSpec", spec), owner="demo", roots=cast("tuple[str, ...]", roots))
            self.assertEqual([], reg.resolvers_for("sfx"))

    def test_remove_owner_prunes_specs_and_roots(self):
        reg = Registry()
        reg.add_exporter(exporter_spec(), owner="demo")
        reg.add_resolver(resolver_spec(), owner="demo", roots=("/acervo",))
        reg.add_resolver(resolver_spec("outro"), owner="outro", roots=("/outro",))
        self.assertEqual(
            {"exporter": ["demo_html"], "resolver": ["demo"]},
            {kind: names for kind, names in reg.owned_by("demo").items() if kind in ("exporter", "resolver")},
        )
        reg.remove_owner("demo")
        self.assertEqual((), reg.exporter_names())
        self.assertIsNone(reg.resolver("demo"))
        self.assertEqual((), reg.resolver_roots("demo"))
        self.assertEqual(("/outro",), reg.resolver_roots("outro"))
        self.assertEqual(["outro"], [spec.name for _, spec in reg.resolvers_for("sfx")])


def api_manifest(paths=(), exporters=("demo_html",), resolvers=("demo",)):
    return {
        "id": "demo",
        "contributes": {
            "providers": [],
            "presets": [],
            "routes": [],
            "commands": [],
            "exporters": list(exporters),
            "resolvers": list(resolvers),
        },
        "permissions": {"network": [], "env": [], "paths": list(paths)},
    }


class PluginApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-acervo-"))
        self.addCleanup(self.root.rmdir)

    def api(self, **kwargs):
        registry = Registry()
        return PluginApi(api_manifest(**kwargs), registry), registry

    def test_both_kinds_register_and_finish(self):
        api, reg = self.api(paths=[str(self.root)])
        api.exporter("demo_html", export_fn, "Exporta HTML")
        api.resolver("demo", resolve_fn, ["musica", "sfx"])
        api.finish()
        self.assertEqual("demo", reg.owner("exporter", "demo_html"))
        self.assertEqual([("demo", "demo")], [(o, s.name) for o, s in reg.resolvers_for("musica")])

    def test_roots_come_from_permissions_paths_like_local_file(self):
        paths = [str(self.root), str(Path.home())]  # a pasta pessoal inteira é ampla demais: ignorada
        if os.name != "nt":
            paths.append("D:\\Acervo")  # raiz Windows lida fora do Windows: ignorada
        api, reg = self.api(paths=paths)
        api.resolver("demo", resolve_fn, ["sfx"])
        self.assertEqual((str(self.root.resolve()),), reg.resolver_roots("demo"))

    def test_names_follow_declaration_and_prefix(self):
        cases = {
            "exporter undeclared": ("exporter", "demo_pdf", "contributes.exporters"),
            "exporter prefix": ("exporter", "alheio", "começar por demo_"),
            "resolver undeclared": ("resolver", "demo_sons", "contributes.resolvers"),
            "resolver prefix": ("resolver", "alheio", "começar por demo_"),
        }
        for label, (kind, name, message) in cases.items():
            api, reg = self.api(exporters=("demo_html", "alheio"), resolvers=("demo", "alheio"))
            with self.subTest(label), self.assertRaises(ApiError) as caught:
                if kind == "exporter":
                    api.exporter(name, export_fn, "Exporta")
                else:
                    api.resolver(name, resolve_fn, ["sfx"])
            self.assertIn(message, str(caught.exception))
            self.assertEqual((), reg.exporter_names())
            self.assertEqual([], reg.resolvers_for("sfx"))

    def test_bad_inputs_are_refused(self):
        exporters = {
            "empty description": (export_fn, ""),
            "long description": (export_fn, "x" * 201),
            "description not text": (export_fn, None),
            "export not callable": ("export", "Exporta"),
        }
        for label, (export, description) in exporters.items():
            api, reg = self.api()
            with self.subTest(label), self.assertRaises(RegistryError):
                api.exporter("demo_html", export, description)
            self.assertEqual((), reg.exporter_names())
        resolvers = {
            "marca": (resolve_fn, ["marca"]),
            "aroll": (resolve_fn, ["sfx", "aroll"]),
            "empty kinds": (resolve_fn, []),
            "repeated kinds": (resolve_fn, ["sfx", "sfx"]),
            "kinds as text": (resolve_fn, "sfx"),
            "resolve not callable": (None, ["sfx"]),
        }
        for label, (resolve, kinds) in resolvers.items():
            api, reg = self.api()
            with self.subTest(label), self.assertRaises(RegistryError):
                api.resolver("demo", resolve, kinds)
            self.assertIsNone(reg.resolver("demo"))

    def test_finish_requires_every_declared_exporter_and_resolver(self):
        api, _ = self.api()
        api.resolver("demo", resolve_fn, ["sfx"])
        with self.assertRaises(ApiError) as caught:
            api.finish()
        self.assertIn("contributes.exporters", str(caught.exception))
        self.assertIn("demo_html", str(caught.exception))
        api, _ = self.api()
        api.exporter("demo_html", export_fn, "Exporta HTML")
        with self.assertRaises(ApiError) as caught:
            api.finish()
        self.assertIn("contributes.resolvers", str(caught.exception))


EXPORT_CODE = """
from getbrolls.sdk import ExportResult, ResolverHit


def exporta(plan, options):
    return ExportResult({"index.html": "<p>ok</p>"})


def acha(kind, name):
    return None


def register(api):
    api.exporter("demo_html", exporta, "Exporta o plano em HTML")
    api.resolver("demo", acha, ["sfx", "musica"])
"""


class LoadedPluginTests(LoaderTestCase):
    def manifest(self):
        acervo = self.home / "acervo"
        acervo.mkdir(exist_ok=True)
        return {
            **MANIFEST,
            "contributes": {"exporters": ["demo_html"], "resolvers": ["demo"]},
            "permissions": {"network": [], "env": [], "paths": [str(acervo)]},
        }

    def test_enabled_plugin_registers_an_exporter_and_a_resolver(self):
        self.install(self.manifest(), code=EXPORT_CODE)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}), self.assertLogs("getbrolls.sdk", level="INFO") as cm:
            reg = get_registry()
            declared = (loader.declared("exporters"), loader.declared("resolvers"))
        self.assertEqual("enabled", reg.plugins["demo"]["status"], reg.plugins["demo"]["reason"])
        self.assertEqual(("demo_html",), reg.exporter_names())
        self.assertEqual("demo", reg.owner("exporter", "demo_html"))
        self.assertEqual([("demo", "demo")], [(o, s.name) for o, s in reg.resolvers_for("musica")])
        self.assertEqual((str((self.home / "acervo").resolve()),), reg.resolver_roots("demo"))
        self.assertEqual((["demo_html"], ["demo"]), declared)
        line = next(entry for entry in cm.output if "event=plugin_loaded" in entry)
        self.assertIn("exporters=1", line)
        self.assertIn("resolvers=1", line)

    def test_list_and_check_show_the_new_kinds(self):
        folder = self.install(self.manifest(), code=EXPORT_CODE)
        row = loader.inventory()[0]
        self.assertEqual({"exporters": ["demo_html"], "resolvers": ["demo"]}, row["contributes"])
        checked = loader.trial_load(folder)
        self.assertEqual(["demo_html"], checked["contracts"]["exporters"])
        self.assertEqual(["demo"], checked["contracts"]["resolvers"])

    def test_only_enabled_plugins_contribute(self):
        folder = self.install(self.manifest(), code=EXPORT_CODE)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "outro"}):
            reg = get_registry()
            self.assertEqual((), reg.exporter_names())
            self.assertEqual([], reg.resolvers_for("sfx"))
        (folder / "plugin.py").write_text(EXPORT_CODE + "\n# mudou\n", encoding="utf-8")
        reset_registry()
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
            self.assertEqual("suspended", reg.plugins["demo"]["status"])
            self.assertEqual((), reg.exporter_names())
            self.assertEqual([], reg.resolvers_for("sfx"))

    def test_failed_register_leaves_no_exporter_or_resolver(self):
        code = EXPORT_CODE + '\n\nold = register\n\n\ndef register(api):\n    old(api)\n    raise RuntimeError("x")\n'
        self.install(self.manifest(), code=code)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            reg = get_registry()
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertEqual((), reg.exporter_names())
        self.assertEqual([], reg.resolvers_for("sfx"))
        self.assertEqual((), reg.resolver_roots("demo"))


if __name__ == "__main__":
    unittest.main()
