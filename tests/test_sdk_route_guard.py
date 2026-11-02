"""Guarda-corpos das rotas: o core escreve `plugin:<rota>` e isola o `prepare` do plugin."""

import os
import tempfile
import unittest
from pathlib import Path

# Any só aparece citado em cast("Any", ...); o pyright resolve a string, o pylint não.
from typing import Any, cast  # pylint: disable=unused-import
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase
from test_sdk_routes_contracts import ROUTE_MANIFEST, route_code

from getbrolls import providers
from getbrolls.http import ProviderError
from getbrolls.sdk import RouteResult, guard
from getbrolls.sdk.registry import get_registry

PLUGIN_ROUTE = {"status": "available", "method": "plugin:demo", "evidence": []}

# Sem capabilities.route, o plugin tenta escrever `plugin:*` sozinho.
SELF_ROUTED = PLUGIN_CODE.replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    '        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    '        item["acquisition"] = {"status": "available", "method": "plugin:demo", "evidence": []}\n'
    "        return [item]",
)
assert SELF_ROUTED != PLUGIN_CODE

# Com capabilities.route, o plugin tenta trocar o método por https + evidência própria.
ROUTE_OVERRIDDEN = route_code().replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    '        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    '        item["acquisition"] = {"status": "available", "method": "https", "evidence": ["x"]}\n'
    "        return [item]",
)
assert route_code() != ROUTE_OVERRIDDEN


class RouteAcquisitionTests(LoaderTestCase):
    def enable(self, manifest, code):
        self.install(manifest, code=code)
        pin_plugins("demo")
        patcher = patch.dict(os.environ, {"GB_PLUGINS": "demo"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_capability_route_becomes_the_acquisition_method(self):
        self.enable(ROUTE_MANIFEST, route_code())
        item = providers.search("demo", "mar", 1)[0]
        self.assertEqual(PLUGIN_ROUTE, item["acquisition"])
        resolved = providers.resolve("https://demo.example/v/1")
        self.assertEqual(PLUGIN_ROUTE, resolved["acquisition"])
        caps = providers.capabilities()
        self.assertEqual("demo", caps["demo"]["route"])
        self.assertNotIn("route", caps["youtube"])

    def test_plugin_cannot_write_a_route_without_the_capability(self):
        self.enable(MANIFEST, SELF_ROUTED)
        with self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            item = providers.search("demo", "mar", 1)[0]
        self.assertEqual({"status": "unavailable", "method": None, "evidence": []}, item["acquisition"])
        self.assertIn("acquisition.method", "\n".join(cm.output))

    def test_capability_wins_over_what_the_candidate_says(self):
        self.enable(ROUTE_MANIFEST, ROUTE_OVERRIDDEN)
        with self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            item = providers.search("demo", "mar", 1)[0]
        self.assertEqual(PLUGIN_ROUTE, item["acquisition"])
        self.assertIn("acquisition.route", "\n".join(cm.output))


class _Route:
    name = "demo"
    stage = "preview"

    def __init__(self, behavior):
        self.behavior = behavior

    def prepare(self, item, workdir):
        return self.behavior(item, workdir)


class RouteCallTests(LoaderTestCase):
    def call(self, behavior):
        with tempfile.TemporaryDirectory() as work:
            return guard.route_call("demo", "demo", _Route(behavior), {"id": "demo:1"}, Path(work))

    def test_result_is_reduced_to_plain_path_and_license(self):
        path, license_text = self.call(lambda item, work: RouteResult(work / "v.mp4", "  Licença L-1  "))
        self.assertIsInstance(path, str)
        self.assertTrue(path.endswith("v.mp4"))
        self.assertEqual("Licença L-1", license_text)

    def test_wrong_return_and_bad_license_are_refused(self):
        for behavior in (
            lambda item, work: str(work / "v.mp4"),
            lambda item, work: RouteResult(work / "v.mp4", "x" * 501),
            lambda item, work: RouteResult(work / "v.mp4", cast("Any", 42)),
            lambda item, work: RouteResult(cast("Any", 123)),
        ):
            with self.subTest(behavior=behavior), self.assertRaises(ProviderError) as caught:
                self.call(behavior)
            self.assertTrue(str(caught.exception).startswith("Plugin demo: "))

    def test_plugin_exceptions_and_sys_exit_are_isolated_and_logged(self):
        def boom(item, work):
            raise KeyError("detalhe interno")

        def leave(item, work):
            raise SystemExit(0)

        for behavior, kind in ((boom, "KeyError"), (leave, "SystemExit")):
            with (
                self.subTest(kind=kind),
                self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
                self.assertRaises(ProviderError) as caught,
            ):
                self.call(behavior)
            self.assertIn(kind, str(caught.exception))
            self.assertNotIn("detalhe interno", str(caught.exception))
            self.assertIn("event=plugin_call_failed", "\n".join(cm.output))
            self.assertIn("route=demo", "\n".join(cm.output))

    def test_provider_error_message_is_kept_without_a_double_prefix(self):
        def refused(item, work):
            raise ProviderError("Plugin demo: host x não está em permissions.network.")

        with self.assertRaises(ProviderError) as caught:
            self.call(refused)
        self.assertEqual("Plugin demo: host x não está em permissions.network.", str(caught.exception))


class RegistryStillLoadsTests(LoaderTestCase):
    def test_builtin_rows_are_unchanged_without_plugins(self):
        self.assertNotIn("route", providers.capabilities()["youtube"])
        self.assertEqual((), get_registry().route_names())


if __name__ == "__main__":
    unittest.main()
