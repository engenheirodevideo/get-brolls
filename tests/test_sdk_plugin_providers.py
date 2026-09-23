"""Plugin entra no fluxo comum, mas nunca decide o que é humano."""

import os
import unittest
from unittest.mock import patch

from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase  # noqa: F401  (MANIFEST reexportado)

from getbrolls import presets, providers
from getbrolls.http import ProviderError
from getbrolls.presets import PERMIT_PRESETS
from getbrolls.sdk.registry import reset_registry

# Fix final: `demo` some do contributes.providers efetivo porque register() nunca chega
# a chamar `api.provider(...)` — o plugin continua declarando a fonte no manifesto.
REGISTER_RAISES = PLUGIN_CODE.replace(
    "def register(api):\n    api.provider(Fonte(api))\n",
    "def register(api):\n    raise RuntimeError('boom')\n",
)

GREEDY = PLUGIN_CODE.replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    '        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    '        item["approval"] = {"status": "approved", "by": "plugin", "at": "x", "revision": 0}\n'
    '        item["rights"]["status"] = "permitted"\n'
    '        item["state"] = "approved"\n'
    '        item["campo_solto"] = 1\n'
    '        item["media_url"] = "http://inseguro.example/v.mp4"\n'
    "        return [item]",
)

# Fix round 1 / Finding 2: tenta pré-preencher estado de revisão/local que só o
# core pode gravar (contact_sheet_path, local_path/sha256, review, segment).
GREEDY_STATE = PLUGIN_CODE.replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    '        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    '        item["preview"]["contact_sheet_path"] = "/etc/passwd"\n'
    '        item["local_path"] = "/etc/passwd"\n'
    '        item["local_sha256"] = "a" * 64\n'
    '        item["review"] = {"ok": True}\n'
    '        item["segment"] = {"start_s": 1, "end_s": 2, "revision": 5}\n'
    "        return [item]",
)

# Fix round 1 / Finding 1: search() devolve um gerador que quebra no meio.
GENERATOR_THAT_RAISES = PLUGIN_CODE.replace(
    "    def search(self, query, limit, media):\n"
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]\n',
    "    def search(self, query, limit, media):\n"
    "        def gen():\n"
    '            yield self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    "            raise KeyError('x')\n"
    "        return gen()\n",
)

# Fix round 1 / Finding 1: search() não devolve lista/tupla/gerador nenhum.
NON_LIST_SEARCH = PLUGIN_CODE.replace(
    "    def search(self, query, limit, media):\n"
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]\n',
    "    def search(self, query, limit, media):\n        return 5\n",
)

# Fix final / Finding 2: search() chama sys.exit em vez de estourar uma Exception comum.
SEARCH_SYS_EXIT = PLUGIN_CODE.replace(
    "    def search(self, query, limit, media):\n"
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]\n',
    "    def search(self, query, limit, media):\n        import sys\n        sys.exit(0)\n",
)
assert SEARCH_SYS_EXIT != PLUGIN_CODE


class PluginTestCase(LoaderTestCase):
    def enable(self, code=PLUGIN_CODE):
        self.install(code=code)
        patcher = patch.dict(os.environ, {"GB_PLUGINS": "demo"})
        patcher.start()
        self.addCleanup(patcher.stop)


class PluginProviderTests(PluginTestCase):
    def test_plugin_search_goes_through_the_common_facade(self):
        self.enable()
        items = providers.search("demo", "mar", 3)
        self.assertEqual(["demo:1"], [i["id"] for i in items])
        self.assertEqual("mar", items[0]["query"])
        self.assertEqual("demo", providers.capabilities()["demo"]["plugin"])

    def test_plugin_cannot_approve_permit_or_add_fields(self):
        self.enable(GREEDY)
        with self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            item = providers.search("demo", "mar", 1)[0]
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_candidate_sanitized", joined)
        self.assertIn("campo_solto", joined)
        self.assertEqual("pending", item["approval"]["status"])
        self.assertEqual("unknown", item["rights"]["status"])
        self.assertEqual("candidate", item["state"])
        self.assertNotIn("campo_solto", item)
        self.assertIsNone(item["media_url"])

    def test_plugin_exception_becomes_provider_error_with_plugin_id(self):
        self.enable(
            PLUGIN_CODE.replace(
                "    def search(self, query, limit, media):\n",
                "    def search(self, query, limit, media):\n        raise KeyError('x')\n",
            )
        )
        with self.assertRaises(ProviderError) as caught, self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            providers.search("demo", "mar", 1)
        self.assertIn("Plugin demo", str(caught.exception))
        self.assertIn("event=plugin_call_failed", "\n".join(cm.output))
        self.assertIn("error=KeyError", "\n".join(cm.output))

    def test_resolve_routes_plugin_hosts_and_keeps_builtin_errors(self):
        self.enable()
        self.assertEqual("demo:1", providers.resolve("https://demo.example/v/1")["id"])
        with self.assertRaises(ProviderError) as caught:
            providers.resolve("https://desconhecido.example/v/1")
        self.assertIn("Fonte de URL não suportada", str(caught.exception))

    def test_search_on_a_failed_plugin_source_names_plugin_and_status(self):
        """Finding 1: register() estourou, então "demo" nunca entrou no registro — a
        mensagem tem que apontar o plugin e o status real, não "fonte desconhecida"."""
        self.enable(REGISTER_RAISES)
        with self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        message = str(caught.exception)
        self.assertIn("demo", message)
        self.assertIn("failed", message)
        self.assertIn("plugins --action list", message)

    def test_search_on_a_suspended_plugin_source_names_plugin_and_status(self):
        """Finding 1: pin quebrado suspende o plugin; a busca por esse nome de fonte tem
        que dizer isso, não "fonte desconhecida"."""
        folder = self.install()
        from getbrolls.sdk import loader

        loader.enable("demo", confirm=True)
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mudou\n", encoding="utf-8")
        reset_registry()
        self.addCleanup(reset_registry)
        with self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        self.assertIn("suspended", str(caught.exception))

    def test_search_on_a_truly_unknown_source_keeps_the_original_message(self):
        self.enable()
        with self.assertRaises(ProviderError) as caught:
            providers.search("inexistente", "mar", 1)
        self.assertIn("Busca indisponível nesta fonte", str(caught.exception))

    def test_refresh_only_takes_media_url_from_plugin(self):
        self.enable()
        item = providers.search("demo", "mar", 1)[0]
        item["approval"]["status"] = "approved"
        with patch.object(
            type(providers._registry().provider("demo")),
            "refresh",
            lambda self, it: {**it, "media_url": "https://demo.example/novo.mp4", "title": "trocado"},
        ):
            fresh = providers.refresh(item)
        self.assertEqual("https://demo.example/novo.mp4", fresh["media_url"])
        self.assertEqual(item["title"], fresh["title"])
        self.assertEqual("approved", fresh["approval"]["status"])

    def test_disabled_plugin_items_refresh_as_unchanged_copies(self):
        item = providers.resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        item["provider"] = "demo"
        self.assertEqual(item, providers.refresh(item))

    def test_plugin_preset_is_listed_from_the_manifest_and_served(self):
        self.enable()
        self.assertIn("demo", presets.names())
        self.assertIn("verifique a página da fonte", presets.get("demo")["text"])

    def test_cli_preset_choices_include_enabled_plugin(self):
        self.enable()
        from getbrolls.cli import build_parser

        args = build_parser().parse_args(["permit", "--project", ".", "--candidate", "x", "--preset", "demo"])
        self.assertEqual("demo", args.preset)

    # --- Fix round 1 -----------------------------------------------------

    def test_plugin_cannot_preset_review_state_or_local_paths(self):
        """Finding 2: a allowlist restringe também dentro de `preview`, e
        `local_path`/`local_sha256`/`review`/`segment` nunca vêm do plugin."""
        self.enable(GREEDY_STATE)
        with self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            item = providers.search("demo", "mar", 1)[0]
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_candidate_sanitized", joined)
        for field in ("preview.contact_sheet_path", "local_path", "local_sha256", "review", "segment"):
            self.assertIn(field, joined)
        self.assertIsNone(item["preview"]["contact_sheet_path"])
        self.assertNotIn("local_path", item)
        self.assertNotIn("local_sha256", item)
        self.assertNotIn("review", item)
        self.assertEqual({"start_s": None, "end_s": None, "revision": 0}, item["segment"])

    def test_clean_plugin_candidate_still_passes_the_core_schema(self):
        from getbrolls.sdk.jsonschema import errors as schema_errors
        from getbrolls.sdk.schemas import load as load_schema

        self.enable()
        item = providers.search("demo", "mar", 1)[0]
        self.assertEqual([], schema_errors(item, load_schema("candidate")))

    def test_generator_search_that_raises_midway_becomes_provider_error(self):
        """Finding 1: um gerador quebrando no meio nunca deve chegar cru na CLI."""
        self.enable(GENERATOR_THAT_RAISES)
        with self.assertRaises(ProviderError) as caught, self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            providers.search("demo", "mar", 2)
        self.assertIn("Plugin demo", str(caught.exception))
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_call_failed", joined)
        self.assertIn("error=KeyError", joined)

    def test_search_sys_exit_becomes_provider_error(self):
        """Finding 2: `sys.exit` dentro de `search()` (não no import) tem que passar pelo
        mesmo isolamento de `guard.rows` que qualquer outra exceção de plugin."""
        self.enable(SEARCH_SYS_EXIT)
        with self.assertRaises(ProviderError) as caught, self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            providers.search("demo", "mar", 1)
        self.assertIn("Plugin demo", str(caught.exception))
        self.assertIn("error=SystemExit", "\n".join(cm.output))

    def test_search_returning_a_non_list_becomes_provider_error(self):
        """Finding 1: `search` devolvendo um int (não lista/tupla/gerador)."""
        self.enable(NON_LIST_SEARCH)
        with self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        self.assertIn("Plugin demo", str(caught.exception))
        self.assertIn("tem que devolver uma lista de candidatos", str(caught.exception))

    def test_names_falls_back_to_builtins_when_plugin_dir_is_unreadable(self):
        """Finding 3: OSError (ex.: PermissionError) não pode derrubar o parser da CLI."""
        with patch("getbrolls.sdk.loader.declared", side_effect=PermissionError("sem permissão")):
            self.assertEqual(sorted(PERMIT_PRESETS), presets.names())


class RegistryDrivenValidationTests(PluginTestCase):
    def test_brief_accepts_enabled_plugin_source_and_refuses_unknown(self):
        from getbrolls import brief

        self.enable()
        self.assertIn("demo", brief.sources())
        self.assertIn("demo", brief.searchable())
        self.assertNotIn("inexistente", brief.sources())
        self.assertEqual(("pexels", "pixabay"), brief.stock_sources())
        self.assertEqual(("commons", "nasa"), brief.still_sources())

    def test_builtin_lists_are_unchanged_without_plugins(self):
        from getbrolls import brief

        self.assertEqual(brief.SOURCES, brief.sources())
        self.assertEqual(brief.SEARCHABLE, brief.searchable())

    def test_rules_accept_plugin_source_in_preferred_providers(self):
        from getbrolls import rules

        self.enable()
        self.assertIn("demo", rules.searchable_providers())


if __name__ == "__main__":
    unittest.main()
