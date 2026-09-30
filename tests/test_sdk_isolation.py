"""Isolamento uniforme de código de plugin: toda porta de entrada passa pelo mesmo guarda-corpo.

Cobre exceção hostil (`__str__`/metaclasse/`BaseException`), capabilities mal
tipadas ou que mudam depois do registro, e segredo de `permissions.env` que nunca
pode chegar à mensagem, ao traceback, ao log nem ao `diagnostics.jsonl`.
"""

import json
import os
import shutil
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import providers, rules
from getbrolls.http import ProviderError
from getbrolls.sdk import guard, loader, testing
from getbrolls.sdk.registry import Registry, get_registry, reset_registry

SECRET = "sk_test_SECRET123"

BASE_CODE = """
from getbrolls.sdk import ProviderCapabilities


class Fonte:
    name = "demo"
    capabilities = ProviderCapabilities(search=True, url_hosts=("demo.example",))

    def __init__(self, api):
        self.api = api

    def search(self, query, limit, media):
        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]

    def resolve(self, url):
        return self.api.candidate("demo", "1", "Demo", url)

    def refresh(self, item):
        return item


def register(api):
    api.provider(Fonte(api))
"""

PROVIDER_MANIFEST = {
    **MANIFEST,
    "contributes": {"providers": ["demo"]},
    "permissions": {"network": ["demo.example"], "env": ["DEMO_TOKEN"]},
}


def _with_search(body):
    code = BASE_CODE.replace(
        "    def search(self, query, limit, media):\n"
        '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]\n',
        "    def search(self, query, limit, media):\n" + body,
    )
    assert code != BASE_CODE
    return code


HOSTILE_HELPERS = f"""
import asyncio


class HostileStr(Exception):
    def __init__(self, status):
        self.status = status

    def __str__(self):
        return f"HTTP {{self.status}}: {{self.body}}"


class ExitStr(Exception):
    def __str__(self):
        raise SystemExit(0)


class Meta(type):
    @property
    def __name__(cls):
        raise SystemExit(0)


class MetaNamed(Exception, metaclass=Meta):
    pass


class Boom(BaseException):
    def __str__(self):
        return "{SECRET}"


class ExitItems(dict):
    def items(self):
        raise SystemExit(0)
"""


class RegisterIsolationTests(LoaderTestCase):
    """`register()` nunca derruba o registro, nem vaza o texto da exceção."""

    def load(self, register_body, helpers=HOSTILE_HELPERS):
        code = helpers + "\n\ndef register(api):\n" + register_body
        self.install(PROVIDER_MANIFEST, code=code)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            return get_registry()

    def test_broken_str_in_register_marks_failed_and_keeps_builtins(self):
        reg = self.load("    raise HostileStr(401)\n")
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("HostileStr", reg.plugins["demo"]["reason"])
        self.assertIn("youtube", reg.provider_names())

    def test_system_exit_from_str_and_from_metaclass_name_are_isolated(self):
        for body in ("    raise ExitStr()\n", "    raise MetaNamed()\n", "    raise Boom()\n"):
            with self.subTest(body=body):
                reset_registry()
                reg = self.load(body)
                self.assertEqual("failed", reg.plugins["demo"]["status"])
                self.assertIn("youtube", reg.provider_names())
                self.assertNotIn(SECRET, json.dumps(reg.plugins["demo"]))

    def test_reason_keeps_only_the_type_of_a_plugin_exception(self):
        reg = self.load(f"    raise ValueError('config inválida: token=abc {SECRET}')\n")
        reason = reg.plugins["demo"]["reason"]
        self.assertIn("ValueError", reason)
        self.assertNotIn(SECRET, reason)
        self.assertNotIn("token=abc", reason)

    def test_core_refusal_text_still_reaches_the_reason(self):
        reg = self.load("    api.preset('outro', 'https://x.example', 'x')\n")
        self.assertEqual("failed", reg.plugins["demo"]["status"])
        self.assertIn("contributes.presets", reg.plugins["demo"]["reason"])

    def test_a_failure_while_building_the_registry_never_breaks_builtins(self):
        self.install(PROVIDER_MANIFEST, code=BASE_CODE)
        pin_plugins("demo")
        with (
            patch.dict(os.environ, {"GB_PLUGINS": "demo"}),
            patch.object(loader, "entries", side_effect=MemoryError()),
        ):
            reg = get_registry()
        self.assertIn("youtube", reg.provider_names())
        self.assertNotIn("demo", reg.provider_names())


class RegisterIsolationCliTests(LoaderTestCase):
    def env(self):
        pin_plugins("demo")
        return {"GB_HOME": str(self.home), "GB_PLUGINS": "demo"}

    def test_builtin_commands_survive_a_hostile_register(self):
        for body in ("    raise HostileStr(401)\n", "    raise ExitStr()\n", "    raise Boom()\n"):
            with self.subTest(body=body):
                code = HOSTILE_HELPERS + "\n\ndef register(api):\n" + body
                self.install(PROVIDER_MANIFEST, code=code)
                self.assertIn("youtube", run_cli("providers", env=self.env()))
                doctor = run_cli("doctor", env=self.env())
                self.assertEqual("failed", doctor["plugins"][0]["status"])
                self.assertNotIn(SECRET, json.dumps(doctor))

    def test_doctor_never_prints_the_register_exception_text(self):
        code = f"def register(api):\n    raise ValueError('config inválida: token=abc {SECRET}')\n"
        self.install(PROVIDER_MANIFEST, code=code)
        doctor = run_cli("doctor", env=self.env())
        self.assertNotIn(SECRET, json.dumps(doctor))
        self.assertIn("ValueError", doctor["plugins"][0]["reason"])


class CapabilitySnapshotTests(LoaderTestCase):
    """Capabilities são lidas uma vez, validadas por tipo, e viram snapshot."""

    def load(self, capabilities_line):
        code = BASE_CODE.replace(
            '    capabilities = ProviderCapabilities(search=True, url_hosts=("demo.example",))\n', capabilities_line
        )
        assert code != BASE_CODE
        self.install(PROVIDER_MANIFEST, code=code)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            return get_registry()

    def test_badly_typed_capabilities_fail_the_plugin_not_the_builtins(self):
        for line in (
            "    capabilities = ProviderCapabilities(search=True, env_key=123)\n",
            "    capabilities = ProviderCapabilities(search=True, transport=b'x')\n",
            "    capabilities = ProviderCapabilities(search=True, seek=float('nan'))\n",
            "    capabilities = ProviderCapabilities(search=1)\n",
            "    capabilities = ProviderCapabilities(search=True, url_hosts=('ok.example', 7))\n",
            "    capabilities = ProviderCapabilities(search=True, route=7)\n",
        ):
            with self.subTest(line=line):
                reset_registry()
                reg = self.load(line)
                self.assertEqual("failed", reg.plugins["demo"]["status"])
                self.assertIn("capabilities", reg.plugins["demo"]["reason"])
                self.assertIn("youtube", providers.capabilities())
                self.assertIn("youtube", rules.searchable_providers())

    def test_capabilities_property_is_read_once_at_registration(self):
        line = (
            "    reads = []\n\n"
            "    @property\n"
            "    def capabilities(self):\n"
            "        type(self).reads.append(1)\n"
            "        if len(type(self).reads) > 1:\n"
            f"            raise RuntimeError('{SECRET}')\n"
            "        return ProviderCapabilities(search=True, url_hosts=('demo.example',))\n"
        )
        reg = self.load(line)
        self.assertEqual("enabled", reg.plugins["demo"]["status"], reg.plugins["demo"]["reason"])
        for _ in range(3):
            self.assertTrue(providers.capabilities()["demo"]["search"])
            self.assertIn("demo", rules.searchable_providers())
        self.assertEqual("demo:1", providers.search("demo", "mar", 1)[0]["id"])

    def test_snapshot_is_a_plain_core_copy(self):
        reg = self.load("    capabilities = ProviderCapabilities(search=True, url_hosts=['demo.example'])\n")
        caps = reg.provider("demo").capabilities  # type: ignore[union-attr]
        self.assertIs(tuple, type(caps.url_hosts))
        self.assertEqual(("demo.example",), caps.url_hosts)


class CallIsolationTests(LoaderTestCase):
    """`search`/`resolve` nunca saem do guarda-corpo, nem com BaseException."""

    def enable(self, code):
        self.install(PROVIDER_MANIFEST, code=HOSTILE_HELPERS + code)
        pin_plugins("demo")
        patcher = patch.dict(os.environ, {"GB_PLUGINS": "demo", "DEMO_TOKEN": SECRET})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_hostile_search_becomes_a_provider_error(self):
        bodies = {
            "CancelledError": f"        raise asyncio.CancelledError('{SECRET}')\n",
            "Boom": "        raise Boom()\n",
            "GeneratorExit": "        def gen():\n"
            '            yield self.api.candidate("demo", "1", "x")\n'
            "            raise GeneratorExit()\n"
            "        return gen()\n",
            "ExitStr": "        raise ExitStr()\n",
            "MetaNamed": "        raise MetaNamed()\n",
            "ExitItems": '        return [ExitItems(self.api.candidate("demo", "1", "x"))]\n',
            "HostileProviderError": "        from getbrolls.http import ProviderError\n\n"
            "        class Bad(ProviderError):\n"
            "            def __str__(self):\n"
            "                raise SystemExit(0)\n\n"
            "        raise Bad('x')\n",
        }
        for label, body in bodies.items():
            with self.subTest(label):
                reset_registry()
                self.enable(_with_search(body))
                with self.assertRaises(ProviderError) as caught:
                    providers.search("demo", "mar", 2)
                message = str(caught.exception)
                self.assertTrue(message.startswith("Plugin demo:"), message)
                self.assertNotIn(SECRET, message)
                self.assertIsNone(caught.exception.__cause__)
                self.assertNotIn(SECRET, "".join(traceback.format_exception(caught.exception)))

    def test_plugin_exception_text_never_reaches_the_chain(self):
        body = "        raise RuntimeError(f\"upstream rejected {self.api.env('DEMO_TOKEN')}\")\n"
        self.enable(_with_search(body))
        with self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn(SECRET, rendered)
        self.assertIn("RuntimeError", str(caught.exception))

    def test_resolve_and_refresh_are_isolated_too(self):
        code = BASE_CODE.replace(
            '    def resolve(self, url):\n        return self.api.candidate("demo", "1", "Demo", url)\n',
            "    def resolve(self, url):\n        raise Boom()\n",
        ).replace(
            "    def refresh(self, item):\n        return item\n",
            "    def refresh(self, item):\n        raise Boom()\n",
        )
        self.enable(code)
        with self.assertRaises(ProviderError):
            providers.resolve("https://demo.example/v/1")
        item = providers.search("demo", "mar", 1)[0]
        with self.assertRaises(ProviderError):
            providers.refresh(item)


class ResolveLeakCliTests(LoaderTestCase):
    """Ponta a ponta: o valor de api.env nunca aparece em stderr, log nem diagnostics."""

    def test_env_value_never_reaches_stderr_log_or_diagnostics(self):
        code = BASE_CODE.replace(
            '    def resolve(self, url):\n        return self.api.candidate("demo", "1", "Demo", url)\n',
            "    def resolve(self, url):\n"
            "        raise RuntimeError(f\"... {{'pw': '{self.api.env('DEMO_TOKEN')}'}}\")\n",
        )
        self.install(PROVIDER_MANIFEST, code=code)
        project = Path(tempfile.mkdtemp(prefix="gb-project-"))
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        pin_plugins("demo")
        env = {"GB_HOME": str(self.home), "GB_PLUGINS": "demo", "DEMO_TOKEN": SECRET}
        err = run_cli("resolve", "--url", "https://demo.example/v/1", project=project, expect=1, env=env)
        self.assertNotIn(SECRET, json.dumps(err))
        for name in ("diagnostics.jsonl", "getbrolls.log"):
            path = project / "brolls" / name
            if path.exists():
                self.assertNotIn(SECRET, path.read_text(encoding="utf-8"), name)


class TrialLoadIsolationTests(LoaderTestCase):
    def folder(self, code):
        return self.install(PROVIDER_MANIFEST, code=code)

    def test_check_turns_any_base_exception_into_a_plugin_error(self):
        for body in (
            "    raise Boom()\n",
            "    raise ExitStr()\n",
            "    raise MetaNamed()\n",
            "    raise HostileStr(1)\n",
        ):
            with self.subTest(body=body), self.assertRaises(ValueError) as caught:
                testing.check_plugin(self.folder(HOSTILE_HELPERS + "\n\ndef register(api):\n" + body))
            self.assertTrue(str(caught.exception).startswith("Plugin demo:"))
            self.assertNotIn(SECRET, str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)

    def test_contract_check_that_runs_plugin_code_is_isolated(self):
        code = BASE_CODE.replace(
            "    def search(self, query, limit, media):\n",
            "    @property\n    def search(self):\n        raise SystemExit(0)\n\n"
            "    def _old(self, query, limit, media):\n",
        )
        with self.assertRaises(ValueError) as caught:
            testing.check_plugin(self.folder(code))
        self.assertTrue(str(caught.exception).startswith("Plugin demo:"))


class PresetAndNameTypeTests(unittest.TestCase):
    def test_str_subclass_names_and_preset_text_are_refused(self):
        class Sneaky(str):
            def __eq__(self, other):
                raise SystemExit(0)

            __hash__ = str.__hash__

        reg = Registry()
        url = "https://x.example"
        with self.assertRaises(ValueError):
            reg.add_preset("demo", url, Sneaky(f"x — verifique a página da fonte: {url}"), owner="demo")
        with self.assertRaises(ValueError):
            reg.add_preset(Sneaky("demo"), url, f"x — verifique a página da fonte: {url}", owner="demo")


class MessageSanitizerTests(unittest.TestCase):
    def test_trusted_message_is_one_line_redacted_and_capped(self):
        guard.remember_env("demo", ["DEMO_TOKEN"])
        with patch.dict(os.environ, {"DEMO_TOKEN": SECRET}):
            text = guard.plugin_text("demo", ProviderError(f"linha 1\nlinha\x07 2 {SECRET} " + "x" * 400))
        assert text is not None
        self.assertNotIn("\n", text)
        self.assertNotIn("\x07", text)
        self.assertNotIn(SECRET, text)
        self.assertIn("[REDACTED]", text)
        self.assertLessEqual(len(text), 300)

    def test_untrusted_types_and_odd_args_give_no_text(self):
        self.assertIsNone(guard.plugin_text("demo", ValueError("x")))
        self.assertIsNone(guard.plugin_text("demo", ProviderError(1)))
        self.assertIsNone(guard.plugin_text("demo", ProviderError("a", "b")))

        class SubError(ProviderError):
            pass

        self.assertIsNone(guard.plugin_text("demo", SubError("x")))


if __name__ == "__main__":
    unittest.main()
