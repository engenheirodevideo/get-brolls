"""Rotas e comandos de plugin: contratos, registro, manifesto e o que o `finish()` confere."""

import os
import tempfile
import unittest
from pathlib import Path

# Any só aparece citado em cast("Any", ...); o pyright resolve a string, o pylint não.
from typing import Any, cast  # pylint: disable=unused-import
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase
from test_sdk_manifest import BASE, write_plugin

from getbrolls.sdk import CommandContext, CommandSpec, ProviderCapabilities, RouteResult
from getbrolls.sdk.manifest import ManifestError, read_manifest
from getbrolls.sdk.registry import Registry, RegistryError, get_registry

ROUTE_MANIFEST = {
    **MANIFEST,
    "contributes": {"providers": ["demo"], "routes": ["demo"], "commands": ["ola"]},
}

ROUTE_CODE = """
from getbrolls.sdk import ProviderCapabilities, RouteResult


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True, url_hosts=("demo.example",), route=ROUTE_NAME)

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]

    def resolve(self, url):
        return self.api.candidate("demo", "1", "Demo", url)

    def refresh(self, item):
        return item


class Copia:
    name = "demo"
    stage = "preview"

    def prepare(self, item, workdir):
        return RouteResult(workdir / "v.mp4")


def ola(args, ctx):
    return {"ola": args.get("nome", "mundo")}


def register(api):
    api.provider(Fonte(api))
    api.route(Copia())
    api.command("ola", ola, "Diz olá")
"""


def route_code(route_name='"demo"'):
    return ROUTE_CODE.replace("ROUTE_NAME", route_name)


class FakeRoute:
    def __init__(self, name, stage="preview"):
        self.name = name
        self.stage = stage

    def prepare(self, item, workdir):
        return RouteResult(workdir / "x.mp4")


class ContractTests(unittest.TestCase):
    def test_public_surface_exports_route_and_command_contracts(self):
        result = RouteResult(Path("a.mp4"))
        self.assertIsNone(result.license)
        self.assertIsNone(ProviderCapabilities().route)
        spec = CommandSpec("ola", "Diz olá", lambda args, ctx: {})
        self.assertEqual("ola", spec.name)

    def test_command_context_reads_copies_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = CommandContext("demo", Path(tmp))
            self.assertEqual([], ctx.candidates())
            self.assertIsNone(ctx.brief())
        self.assertEqual([], CommandContext("demo", None).candidates())


class RegistryRouteTests(unittest.TestCase):
    def test_route_is_registered_with_owner_and_stage(self):
        reg = Registry()
        reg.add_route(FakeRoute("demo", "fetch"), owner="demo")
        self.assertEqual(("demo",), reg.route_names())
        self.assertEqual("demo", reg.owner("route", "demo"))
        self.assertEqual("fetch", reg.route_stage("demo"))

    def test_bad_stage_name_or_missing_prepare_is_refused(self):
        reg = Registry()
        for bad in (FakeRoute("demo", "sempre"), FakeRoute("Demo Ruim"), object()):
            with self.subTest(bad=bad), self.assertRaises(RegistryError):
                reg.add_route(cast("Any", bad), owner="demo")

    def test_commands_are_namespaced_by_plugin(self):
        reg = Registry()
        reg.add_command(CommandSpec("sync", "Sincroniza", lambda a, c: {}), owner="um")
        reg.add_command(CommandSpec("sync", "Sincroniza", lambda a, c: {}), owner="dois")
        self.assertEqual((("um", "sync"), ("dois", "sync")), reg.command_keys())
        self.assertIsNotNone(reg.command("dois", "sync"))
        with self.assertRaises(RegistryError):
            reg.add_command(CommandSpec("sync", "Outra vez", lambda a, c: {}), owner="um")
        with self.assertRaises(RegistryError):
            reg.add_command(CommandSpec("vazio", " ", lambda a, c: {}), owner="um")

    def test_remove_owner_drops_routes_and_commands(self):
        reg = Registry()
        reg.add_route(FakeRoute("demo"), owner="demo")
        reg.add_command(CommandSpec("ola", "Diz olá", lambda a, c: {}), owner="demo")
        self.assertEqual(
            {"provider": [], "preset": [], "route": ["demo"], "command": ["demo:ola"], "exporter": [], "resolver": []},
            reg.owned_by("demo"),
        )
        reg.remove_owner("demo")
        self.assertEqual((), reg.route_names())
        self.assertIsNone(reg.route_stage("demo"))
        self.assertEqual((), reg.command_keys())


class ManifestRouteTests(unittest.TestCase):
    def test_routes_commands_and_paths_are_supported(self):
        manifest = {
            **BASE,
            "contributes": {"providers": ["acme_drive"], "routes": ["acme_drive"], "commands": ["sync"]},
            "permissions": {"network": [], "env": [], "paths": ["~/Movies", "/Volumes/NAS/brolls"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            data = read_manifest(write_plugin(tmp, manifest))
        self.assertEqual(["acme_drive"], data["contributes"]["routes"])
        self.assertEqual(["sync"], data["contributes"]["commands"])
        self.assertEqual(["~/Movies", "/Volumes/NAS/brolls"], data["permissions"]["paths"])

    def test_paths_default_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], read_manifest(write_plugin(tmp, BASE))["permissions"]["paths"])

    def test_relative_parent_or_user_paths_are_refused(self):
        for bad in ("relativa/pasta", "/Volumes/../etc", "~outro/Movies", "", 3):
            manifest = {**BASE, "permissions": {"paths": [bad]}}
            with (
                self.subTest(bad=bad),
                tempfile.TemporaryDirectory() as tmp,
                self.assertRaises(ManifestError) as caught,
            ):
                read_manifest(write_plugin(tmp, manifest))
            self.assertIn("permissions.paths", str(caught.exception))


class PluginApiRouteTests(LoaderTestCase):
    # ROUTE_MANIFEST nunca é mutado; serve só de fixture padrão compartilhada.
    def load(self, manifest=ROUTE_MANIFEST, code=None):  # pylint: disable=dangerous-default-value
        self.install(manifest, code=code or route_code())
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            return get_registry()

    def test_provider_route_and_command_register_together(self):
        reg = self.load()
        self.assertEqual("enabled", reg.plugins["demo"]["status"], reg.plugins["demo"]["reason"])
        self.assertEqual("demo", reg.owner("route", "demo"))
        self.assertEqual("preview", reg.route_stage("demo"))
        spec = reg.command("demo", "ola")
        self.assertIsNotNone(spec)
        self.assertEqual("Diz olá", cast("CommandSpec", spec).help)

    def test_route_name_follows_the_prefix_rule(self):
        # A capability aponta de fato para a rota `alheia` (antes o `.replace`
        # de `route=ROUTE_NAME` não achava nada — `route_code()` já tinha trocado o nome).
        code = route_code('"alheia"').replace('class Copia:\n    name = "demo"', 'class Copia:\n    name = "alheia"')
        self.assertNotEqual(route_code('"alheia"'), code)
        self.assertIn('route="alheia"', code)
        manifest = {**ROUTE_MANIFEST, "contributes": {**ROUTE_MANIFEST["contributes"], "routes": ["alheia"]}}
        reg = self.load(manifest, code)
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("começar por demo_", reg.plugins["demo"]["reason"])

    def test_capability_must_point_to_a_route_of_the_same_plugin(self):
        reg = self.load(code=route_code('"demo_outra"'))
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("capabilities.route", reg.plugins["demo"]["reason"])
        self.assertIsNone(reg.route("demo"))

    def test_undeclared_command_fails_the_plugin(self):
        manifest = {**ROUTE_MANIFEST, "contributes": {"providers": ["demo"], "routes": ["demo"]}}
        reg = self.load(manifest)
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("contributes.commands", reg.plugins["demo"]["reason"])


if __name__ == "__main__":
    unittest.main()
