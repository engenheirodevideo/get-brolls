"""Plugin entra no fluxo comum, mas nunca decide o que é humano."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase  # noqa: F401  (MANIFEST reexportado)

from getbrolls import presets, providers
from getbrolls.http import ProviderError
from getbrolls.presets import PERMIT_PRESETS
from getbrolls.sdk.registry import reset_registry

# `demo` some do contributes.providers efetivo porque register() nunca chega
# a chamar `api.provider(...)` — o plugin continua declarando a fonte no manifesto.
REGISTER_RAISES = PLUGIN_CODE.replace(
    "def register(api):\n    api.provider(Fonte(api))\n",
    "def register(api):\n    raise RuntimeError('boom')\n",
)
assert REGISTER_RAISES != PLUGIN_CODE  # replace() sem alvo encontrado devolveria o original e esvaziaria os testes

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
assert GREEDY != PLUGIN_CODE

# Tenta pré-preencher estado de revisão/local que só o
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
assert GREEDY_STATE != PLUGIN_CODE

# `search()` devolve um gerador que quebra no meio.
GENERATOR_THAT_RAISES = PLUGIN_CODE.replace(
    "    def search(self, query, limit, media):\n"
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]\n',
    "    def search(self, query, limit, media):\n"
    "        def gen():\n"
    '            yield self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    "            raise KeyError('x')\n"
    "        return gen()\n",
)
assert GENERATOR_THAT_RAISES != PLUGIN_CODE

# `search()` não devolve lista/tupla/gerador nenhum.
NON_LIST_SEARCH = PLUGIN_CODE.replace(
    "    def search(self, query, limit, media):\n"
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]\n',
    "    def search(self, query, limit, media):\n        return 5\n",
)
assert NON_LIST_SEARCH != PLUGIN_CODE

# `search()` chama sys.exit em vez de estourar uma Exception comum.
SEARCH_SYS_EXIT = PLUGIN_CODE.replace(
    "    def search(self, query, limit, media):\n"
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]\n',
    "    def search(self, query, limit, media):\n        import sys\n        sys.exit(0)\n",
)
assert SEARCH_SYS_EXIT != PLUGIN_CODE

# Media.duration_s vem NaN — `json.dumps` padrão deixa passar.
NAN_MEDIA = PLUGIN_CODE.replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    '        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    '        item["media"]["duration_s"] = float("nan")\n'
    "        return [item]",
)
assert NAN_MEDIA != PLUGIN_CODE

# Cheap minor: search() de um gerador infinito. `CALLS` conta quantos itens o gerador
# de fato produziu — a prova de que `guard.rows` parou em `limit`, não drenou tudo.
INFINITE_SEARCH = "CALLS = 0\n\n" + PLUGIN_CODE.replace(
    "    def search(self, query, limit, media):\n"
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]\n',
    "    def search(self, query, limit, media):\n"
    "        def gen():\n"
    "            global CALLS\n"
    "            i = 0\n"
    "            while True:\n"
    "                CALLS += 1\n"
    "                yield self.api.candidate(\n"
    '                    "demo", str(i), "Demo " + str(i), "https://demo.example/v/" + str(i)\n'
    "                )\n"
    "                i += 1\n"
    "        return gen()\n",
)
assert INFINITE_SEARCH != PLUGIN_CODE

# Cheap minor: fonte com capabilities.download=False (só metadados, como o exemplo
# pasta_local) tenta fingir um acquisition "available" — o guard tem que ignorar isso.
NO_DOWNLOAD_FAKES_ACQUISITION = PLUGIN_CODE.replace(
    '    capabilities = ProviderCapabilities(search=True, url_hosts=("demo.example",))',
    '    capabilities = ProviderCapabilities(search=True, url_hosts=("demo.example",), download=False)',
).replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    '        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    '        item["media_url"] = "https://demo.example/v/1.mp4"\n'
    '        item["acquisition"] = {"status": "available", "method": "https", "evidence": ["x"]}\n'
    "        return [item]",
)
assert NO_DOWNLOAD_FAKES_ACQUISITION != PLUGIN_CODE


class PluginTestCase(LoaderTestCase):
    def enable(self, code=PLUGIN_CODE):
        self.install(code=code)
        pin_plugins("demo")
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
        """`register()` estourou, então "demo" nunca entrou no registro — a
        mensagem tem que apontar o plugin e o status real, não "fonte desconhecida"."""
        self.enable(REGISTER_RAISES)
        with self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        message = str(caught.exception)
        self.assertIn("demo", message)
        self.assertIn("failed", message)
        self.assertIn("plugins --action list", message)

    def test_search_on_a_suspended_plugin_source_names_plugin_and_status(self):
        """Pin quebrado suspende o plugin; a busca por esse nome de fonte tem
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

    def test_plugin_cannot_preset_review_state_or_local_paths(self):
        """A allowlist restringe também dentro de `preview`, e
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
        """Um gerador quebrando no meio nunca deve chegar cru na CLI."""
        self.enable(GENERATOR_THAT_RAISES)
        with self.assertRaises(ProviderError) as caught, self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            providers.search("demo", "mar", 2)
        self.assertIn("Plugin demo", str(caught.exception))
        joined = "\n".join(cm.output)
        self.assertIn("event=plugin_call_failed", joined)
        self.assertIn("error=KeyError", joined)

    def test_search_sys_exit_becomes_provider_error(self):
        """`sys.exit` dentro de `search()` (não no import) tem que passar pelo
        mesmo isolamento de `guard.rows` que qualquer outra exceção de plugin."""
        self.enable(SEARCH_SYS_EXIT)
        with self.assertRaises(ProviderError) as caught, self.assertLogs("getbrolls.sdk", level="WARNING") as cm:
            providers.search("demo", "mar", 1)
        self.assertIn("Plugin demo", str(caught.exception))
        self.assertIn("error=SystemExit", "\n".join(cm.output))

    def test_search_returning_a_non_list_becomes_provider_error(self):
        """`search` devolvendo um int (não lista/tupla/gerador)."""
        self.enable(NON_LIST_SEARCH)
        with self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        self.assertIn("Plugin demo", str(caught.exception))
        self.assertIn("tem que devolver uma lista de candidatos", str(caught.exception))

    def test_nan_media_duration_becomes_provider_error(self):
        """NaN/Infinity sobrevivem a um `json.dumps` padrão (não é JSON
        estrito) — o guard tem que recusar antes que isso vaze no candidato."""
        self.enable(NAN_MEDIA)
        with self.assertRaises(ProviderError) as caught:
            providers.search("demo", "mar", 1)
        self.assertIn("Plugin demo", str(caught.exception))

    def test_infinite_generator_search_is_bounded_by_limit(self):
        """Cheap minor: `guard.rows` materializa só até `limit` (`itertools.islice`) —
        um gerador infinito de um plugin mal-comportado não pode travar a busca nem
        gastar tempo sanitizando candidato que `search` ia descartar de qualquer jeito."""
        import sys

        self.enable(INFINITE_SEARCH)
        items = providers.search("demo", "mar", 3)
        self.assertEqual(3, len(items))
        self.assertEqual(3, sys.modules["getbrolls_plugins.demo"].CALLS)

    def test_no_download_capability_forces_acquisition_unavailable(self):
        """Cheap minor: `capabilities.download=False` (fonte só-metadados, como o
        exemplo pasta_local) tem que forçar acquisition indisponível mesmo quando o
        plugin tenta fingir "available" — docs/SDK.md promete isso para essas fontes."""
        self.enable(NO_DOWNLOAD_FAKES_ACQUISITION)
        item = providers.search("demo", "mar", 1)[0]
        self.assertEqual({"status": "unavailable", "method": None, "evidence": []}, item["acquisition"])

    def test_names_falls_back_to_builtins_when_plugin_dir_is_unreadable(self):
        """OSError (ex.: PermissionError) não pode derrubar o parser da CLI."""
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

    def _rules_project(self, preferred_literal):
        project = Path(tempfile.mkdtemp(prefix="gb-project-"))
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        data = {
            "version": 1,
            "asset_types": ["video", "image", "news_screenshot", "web_screenshot"],
            "video_format": "native",
            "preferred_providers": {"literal": preferred_literal, "illustrative": ["pexels", "pixabay"]},
            "preferred_domains": [],
            "blocked_domains": [],
            "editorial_rules": [],
            "copyright": {"mode": "per_item_evidence", "responsible_person": None, "declaration": None},
            "browser": {
                "viewport": "mobile",
                "mobile_width": 390,
                "mobile_height": 844,
                "desktop_width": 1440,
                "desktop_height": 900,
                "full_page": False,
            },
        }
        (project / "RULES.md").write_text(
            "# Regras\n\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n",
            encoding="utf-8",
        )
        return project

    def test_preferred_providers_drops_a_suspended_plugin_source_with_a_warning(self):
        """Um plugin suspenso citado em preferred_providers não pode quebrar
        `load_rules` (e por tabela, toda busca do projeto) — só sai da lista com aviso."""
        from getbrolls import rules
        from getbrolls.sdk import loader

        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mudou\n", encoding="utf-8")
        reset_registry()
        self.addCleanup(reset_registry)

        project = self._rules_project(["youtube", "demo"])
        with self.assertLogs("getbrolls.rules", level="INFO") as cm:
            loaded = rules.load_rules(project)
        self.assertEqual(["youtube"], loaded["preferred_providers"]["literal"])
        self.assertTrue(any("demo" in w and "suspended" in w for w in loaded["rules_warnings"]))
        self.assertIn("event=rule_source_skipped", "\n".join(cm.output))

    def test_preferred_providers_still_refuses_a_truly_unknown_name(self):
        from getbrolls import rules

        project = self._rules_project(["youtube", "inexistente"])
        with self.assertRaises(ValueError) as caught:
            rules.load_rules(project)
        self.assertIn("preferred_providers.literal", str(caught.exception))

    def test_brief_allowed_sources_names_the_plugin_for_a_suspended_source(self):
        """BRIEF.md continua recusando a fonte suspensa, mas a mensagem
        nomeia o plugin e o status em vez de só listar as fontes válidas."""
        from getbrolls.brief import _sources
        from getbrolls.sdk import loader

        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mudou\n", encoding="utf-8")
        reset_registry()
        self.addCleanup(reset_registry)

        with self.assertRaises(ValueError) as caught:
            _sources(["youtube", "demo"], "defaults.allowed_sources")
        message = str(caught.exception)
        self.assertIn("demo", message)
        self.assertIn("suspended", message)


if __name__ == "__main__":
    unittest.main()
