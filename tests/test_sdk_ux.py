"""UX do SDK: o que a pessoa e o agente leem aponta o comando que funciona; nomes de segredo residuais."""

import argparse
import json
import os
import shutil
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _media import skip_unless_ffmpeg
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase
from test_sdk_route_fetch import FetchRouteCase

from getbrolls.sdk import loader
from getbrolls.sdk.registry import reset_registry


def _state(home):
    return json.loads((home / "plugins.json").read_text(encoding="utf-8"))


class PinFileMapTests(LoaderTestCase):
    """O pin guarda um mapa por arquivo para o re-enable mostrar o que mudou."""

    def test_enable_stores_a_per_file_sha_map_next_to_the_pin(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        pin = _state(self.home)["enabled"]["demo"]
        self.assertEqual({"getbrolls-plugin.json", "plugin.py"}, set(pin["files"]))
        self.assertEqual(loader.file_digests(folder), pin["files"])
        self.assertEqual(loader.folder_digest(folder), pin["sha256"])

    def test_pin_from_install_path_stores_the_map_too(self):
        folder = self.install()
        loader.pin(MANIFEST, folder, {"source": str(folder), "commit": None})
        self.assertEqual(loader.file_digests(folder), _state(self.home)["enabled"]["demo"]["files"])

    def test_old_pin_without_the_map_stays_valid(self):
        folder = self.install()
        old = {"enabled": {"demo": {"version": "0.1.0", "sha256": loader.folder_digest(folder)}}}
        (self.home / "plugins.json").write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual("enabled", loader.inventory()[0]["status"])


class ReenableSuspendedTests(LoaderTestCase):
    """Re-enable de plugin suspenso mostra o diff e exige --expect, como install/update."""

    def _suspend(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mudou\n", encoding="utf-8")
        (folder / "extra.py").write_text("X = 1\n", encoding="utf-8")
        reset_registry()
        self.assertEqual("suspended", loader.inventory()[0]["status"])
        return folder

    def test_preview_lists_added_removed_and_changed_files(self):
        folder = self._suspend()
        preview = loader.enable("demo", confirm=False)
        self.assertFalse(preview["enabled"])
        self.assertEqual(
            {"added": ["extra.py"], "removed": [], "changed": ["plugin.py"]},
            preview["diff"]["files"],
        )
        self.assertEqual(loader.folder_digest(folder), preview["plugin"]["sha256"])
        self.assertIn("--expect", preview["note"])

    def test_yes_alone_is_refused_and_keeps_the_plugin_suspended(self):
        self._suspend()
        with self.assertRaises(ValueError) as caught:
            loader.enable("demo", confirm=True)
        self.assertIn("--expect", str(caught.exception))
        self.assertEqual("suspended", loader.inventory()[0]["status"])

    def test_wrong_expect_is_refused(self):
        self._suspend()
        with self.assertRaises(ValueError) as caught:
            loader.enable("demo", confirm=True, expect="0" * 64)
        self.assertIn("sha256", str(caught.exception))
        self.assertEqual("suspended", loader.inventory()[0]["status"])

    def test_matching_expect_re_pins_with_the_new_map(self):
        folder = self._suspend()
        sha = loader.enable("demo", confirm=False)["plugin"]["sha256"]
        done = loader.enable("demo", confirm=True, expect=sha)
        self.assertTrue(done["enabled"])
        self.assertNotIn("rode de novo", done["note"])
        self.assertEqual("enabled", loader.inventory()[0]["status"])
        self.assertEqual(loader.file_digests(folder), _state(self.home)["enabled"]["demo"]["files"])

    def test_old_pin_without_the_map_still_requires_expect_and_says_the_diff_is_unknown(self):
        folder = self.install()
        old = {"enabled": {"demo": {"version": "0.1.0", "sha256": "f" * 64}}}
        (self.home / "plugins.json").write_text(json.dumps(old), encoding="utf-8")
        preview = loader.enable("demo", confirm=False)
        self.assertIsNone(preview["diff"]["files"])
        self.assertIn("--expect", preview["note"])
        with self.assertRaises(ValueError):
            loader.enable("demo", confirm=True)
        self.assertTrue(loader.enable("demo", confirm=True, expect=loader.folder_digest(folder))["enabled"])

    def test_cli_enable_accepts_expect(self):
        self._suspend()
        env = {"GB_HOME": str(self.home)}
        preview = run_cli("plugins", "--action", "enable", "--id", "demo", env=env)
        err = run_cli("plugins", "--action", "enable", "--id", "demo", "--yes", expect=2, env=env)
        self.assertIn("--expect", err["error"])
        sha = preview["plugin"]["sha256"]
        done = run_cli("plugins", "--action", "enable", "--id", "demo", "--yes", "--expect", sha, env=env)
        self.assertTrue(done["enabled"])

    def test_first_enable_of_a_never_pinned_plugin_keeps_the_yes_flow(self):
        self.install()
        preview = loader.enable("demo", confirm=False)
        self.assertNotIn("diff", preview)
        self.assertIn("--yes", preview["note"])
        self.assertNotIn("--expect", preview["note"])
        self.assertTrue(loader.enable("demo", confirm=True)["enabled"])

    def test_re_enable_of_an_unchanged_pin_needs_no_expect(self):
        self.install()
        loader.enable("demo", confirm=True)
        self.assertNotIn("diff", loader.enable("demo", confirm=False))
        self.assertTrue(loader.enable("demo", confirm=True)["enabled"])


class NoteTests(LoaderTestCase):
    """A prévia manda o comando que funciona; o sucesso não manda rodar de novo."""

    def _source(self):
        source = self.home / "fonte" / "demo"
        source.mkdir(parents=True)
        (source / "getbrolls-plugin.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (source / "plugin.py").write_text(PLUGIN_CODE, encoding="utf-8")
        return source

    def test_install_preview_names_expect_and_success_does_not_ask_to_rerun(self):
        from getbrolls.sdk import install

        source = self._source()
        preview = install.install(str(source), confirm=False)
        self.assertIn("--yes --expect", preview["note"])
        self.assertIn("não é sandbox", preview["note"])
        done = install.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        self.assertNotIn("rode de novo", done["note"].lower())

    def test_update_preview_names_expect_and_success_does_not_ask_to_rerun(self):
        from getbrolls.sdk import install

        source = self._source()
        sha = install.install(str(source), confirm=False)["plugin"]["sha256"]
        install.install(str(source), confirm=True, expect=sha)
        (source / "plugin.py").write_text(PLUGIN_CODE + "\n# v2\n", encoding="utf-8")
        preview = install.update("demo", confirm=False)
        self.assertIn("--yes --expect", preview["note"])
        done = install.update("demo", confirm=True, expect=preview["plugin"]["sha256"])
        self.assertNotIn("rode de novo", done["note"].lower())

    def test_enable_success_does_not_ask_to_rerun(self):
        self.install()
        self.assertNotIn("rode de novo", loader.enable("demo", confirm=True)["note"].lower())


class GbPluginsSelectionTests(LoaderTestCase):
    """Plugin fora de GB_PLUGINS diz o porquê; a busca aponta GB_PLUGINS, não enable."""

    def test_list_row_names_gb_plugins_as_the_reason(self):
        self.install()
        loader.enable("demo", confirm=True)
        for value in ("off", "outro"):
            with self.subTest(GB_PLUGINS=value), patch.dict(os.environ, {"GB_PLUGINS": value}):
                row = loader.inventory()[0]
                self.assertEqual("disabled", row["status"])
                self.assertIn("desligado por GB_PLUGINS", row["reason"])

    def test_search_error_points_to_gb_plugins(self):
        from getbrolls import providers
        from getbrolls.http import ProviderError

        self.install()
        loader.enable("demo", confirm=True)
        with patch.dict(os.environ, {"GB_PLUGINS": "off"}):
            reset_registry()
            with self.assertRaises(ProviderError) as caught:
                providers.search("demo", "mar", 1)
        message = str(caught.exception)
        self.assertIn("GB_PLUGINS", message)
        self.assertNotIn("--action enable", message)

    def test_cli_list_under_gb_plugins_off(self):
        self.install()
        loader.enable("demo", confirm=True)  # GB_PLUGINS só filtra plugins habilitados
        out = run_cli("plugins", "--action", "list", env={"GB_HOME": str(self.home), "GB_PLUGINS": "off"})
        self.assertIn("desligado por GB_PLUGINS", out["plugins"][0]["reason"])


FAILS_WITH_PLUGIN_ERROR = PLUGIN_CODE.replace(
    "def register(api):\n",
    "def register(api):\n    from getbrolls.sdk import PluginError\n\n"
    "    raise PluginError('Configure DEMO_DIR com a pasta.')\n",
)
assert FAILS_WITH_PLUGIN_ERROR != PLUGIN_CODE
FAILS_WITH_RUNTIME_ERROR = PLUGIN_CODE.replace(
    "def register(api):\n", "def register(api):\n    raise RuntimeError('x')\n"
)
assert FAILS_WITH_RUNTIME_ERROR != PLUGIN_CODE

RULES_DATA = {
    "version": 1,
    "asset_types": ["video", "image", "news_screenshot", "web_screenshot"],
    "video_format": "native",
    "preferred_providers": {"literal": ["youtube", "demo"], "illustrative": ["pexels", "pixabay"]},
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


class UnavailableSourceMessageTests(LoaderTestCase):
    """Sem ".." nem "Plugin X: Plugin X:" nas mensagens de fonte indisponível
    (busca, BRIEF.md e RULES.md usam a mesma frase de status)."""

    def _messages(self):
        from getbrolls import providers, rules
        from getbrolls.brief import _sources
        from getbrolls.http import ProviderError
        from getbrolls.sdk.registry import get_registry

        reset_registry()
        get_registry()  # o registro montado é o que diz "failed" (register() estourou)
        with self.assertRaises(ProviderError) as searched:
            providers.search("demo", "mar", 1)
        with self.assertRaises(ValueError) as briefed:
            _sources(["youtube", "demo"], "defaults.allowed_sources")
        project = self.home / "projeto"
        project.mkdir(exist_ok=True)
        (project / "RULES.md").write_text(
            "# Regras\n\n```json\n" + json.dumps(RULES_DATA) + "\n```\n", encoding="utf-8"
        )
        warned = [w for w in rules.load_rules(project)["rules_warnings"] if "demo" in w]
        self.assertEqual(1, len(warned))
        return [str(searched.exception), str(briefed.exception), warned[0]]

    def _assert_clean(self, messages):
        for message in messages:
            with self.subTest(message=message):
                self.assertNotIn("..", message)
                self.assertNotIn(".;", message)
                self.assertNotIn(": Plugin demo:", message)

    def test_suspended_plugin(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mudou\n", encoding="utf-8")
        messages = self._messages()
        self.assertIn("suspended", messages[0])
        self._assert_clean(messages)

    def test_failed_plugin_with_a_plugin_error(self):
        self.install(code=FAILS_WITH_PLUGIN_ERROR)
        loader.enable("demo", confirm=True)
        messages = self._messages()
        self.assertIn("failed", messages[0])
        self.assertIn("Configure DEMO_DIR", messages[0])
        self._assert_clean(messages)

    def test_failed_plugin_with_a_generic_exception(self):
        self.install(code=FAILS_WITH_RUNTIME_ERROR)
        loader.enable("demo", confirm=True)
        messages = self._messages()
        self.assertIn("(RuntimeError)", messages[0])
        self._assert_clean(messages)


SEARCH_RAISES_PLUGIN_ERROR = PLUGIN_CODE.replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    "        from getbrolls.sdk import PluginError\n\n        raise PluginError('Configure DEMO_DIR com a pasta.')",
)
assert SEARCH_RAISES_PLUGIN_ERROR != PLUGIN_CODE


class SearchPluginErrorTests(LoaderTestCase):
    """`search` não repete o nome da fonte na frente de "Plugin <fonte>: …"."""

    def test_search_error_and_warning_have_a_single_prefix(self):
        import tempfile

        self.install(code=SEARCH_RAISES_PLUGIN_ERROR)
        loader.enable("demo", confirm=True)
        project = tempfile.mkdtemp(prefix="gb-project-", dir=self.home)
        env = {"GB_HOME": str(self.home)}
        run_cli("init-rules", "--project", project, env=env)
        err = run_cli("search", "--provider", "demo", "--query", "mar", "--project", project, expect=2, env=env)
        self.assertIn("Plugin demo: Configure DEMO_DIR com a pasta.", err["error"])
        self.assertNotIn("demo: Plugin demo:", err["error"])
        self.assertNotIn("demo: Plugin demo:", json.dumps(err["warnings"], ensure_ascii=False))


class PluginErrorHintTests(LoaderTestCase):
    """Erro de plugin não leva o "Confira docs/RULES.md." genérico; built-in igual."""

    def _message(self, error):
        from argparse import Namespace

        from getbrolls.runtime import OperationError, audited

        def boom(_args):
            raise error

        with self.assertRaises(OperationError) as caught:
            audited(Namespace(command="providers", project=None), boom)
        return caught.exception.payload["message"]

    def test_plugin_provider_error_gets_a_plugin_hint(self):
        from getbrolls.http import ProviderError

        message = self._message(ProviderError("Plugin meu_route: Falha ao resolver provedor"))
        self.assertNotIn("RULES.md", message)
        self.assertIn("Plugin meu_route: Falha ao resolver provedor.", message)
        self.assertIn("docs/SDK.md", message)
        self.assertNotIn("..", message)

    def test_plugin_error_already_ending_with_a_period(self):
        from getbrolls.http import ProviderError

        message = self._message(ProviderError("Plugin demo: Configure DEMO_DIR com a pasta."))
        self.assertNotIn("RULES.md", message)
        self.assertNotIn("..", message)

    def test_unavailable_plugin_source_keeps_its_own_hint_only(self):
        from getbrolls.http import ProviderError

        text = "Fonte demo é do plugin demo, que está failed. Rode plugins --action list / doctor."
        self.assertEqual(text, self._message(ProviderError(text)))

    def test_builtin_message_is_unchanged(self):
        from getbrolls.http import ProviderError

        self.assertEqual(
            "Falha ao resolver provedor Confira docs/RULES.md.",
            self._message(ProviderError("Falha ao resolver provedor")),
        )


BUILTIN_LIVE = ("commons", "nasa", "pexels", "pixabay", "youtube")
WITH_MEDIA_URL = PLUGIN_CODE.replace(
    '        return [self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")]',
    '        item = self.api.candidate("demo", "1", "Demo " + query, "https://demo.example/v/1")\n'
    '        item["media_url"] = "https://demo.example/v/1.mp4"\n'
    '        item["acquisition"] = {"status": "available", "method": "https", "evidence": []}\n'
    "        return [item]",
)
assert WITH_MEDIA_URL != PLUGIN_CODE


class LivePluginChecksTests(LoaderTestCase):
    """`doctor --live` não marca "failed" uma fonte de plugin só-metadados ou
    com rota, e mostra a mensagem (saneada) do PluginError em vez de uma genérica."""

    def _live(self):
        from getbrolls import providers
        from getbrolls.health import live_checks

        original = providers.search

        def only_plugins(name, query, limit=8, media="any"):
            return [] if name in BUILTIN_LIVE else original(name, query, limit, media)

        reset_registry()
        with patch.object(providers, "search", only_plugins):
            checks = live_checks()["checks"]
        return {row["provider"]: row for row in checks}

    def test_metadata_only_source_is_search_ok_without_media_url(self):
        self.install()
        loader.enable("demo", confirm=True)
        row = self._live()["demo"]
        self.assertEqual("search_ok", row["status"])
        self.assertEqual("no_media_url (fonte só-metadados)", row["refresh"])

    def test_route_source_reports_the_route(self):
        from test_sdk_route_acquisition import ROUTE_MANIFEST, ROUTE_PLUGIN

        self.install(ROUTE_MANIFEST, ROUTE_PLUGIN)
        loader.enable("demo", confirm=True)
        row = self._live()["demo"]
        self.assertEqual("search_ok", row["status"])
        self.assertEqual("route", row["refresh"])

    def test_source_with_media_url_still_refreshes(self):
        self.install(code=WITH_MEDIA_URL)
        loader.enable("demo", confirm=True)
        row = self._live()["demo"]
        self.assertEqual("search_ok", row["status"])
        self.assertEqual("media_url_available", row["refresh"])

    def test_plugin_error_text_reaches_the_detail_sanitized(self):
        self.install(
            code=SEARCH_RAISES_PLUGIN_ERROR.replace("Configure DEMO_DIR", "Configure DEMO_DIR (token=SEGREDO123)")
        )
        loader.enable("demo", confirm=True)
        row = self._live()["demo"]
        self.assertEqual("failed", row["status"])
        self.assertIn("Configure DEMO_DIR", row["detail"])
        self.assertNotIn("SEGREDO123", row["detail"])
        self.assertNotIn("Consulte configuração", row["detail"])


class DoctorSummaryPluginsTests(LoaderTestCase):
    """O `summary` do doctor cita plugin failed/suspended/invalid."""

    def env(self):
        return {"GB_HOME": str(self.home)}

    def test_failed_and_suspended_plugins_show_in_the_summary(self):
        self.install(code=FAILS_WITH_RUNTIME_ERROR)
        loader.enable("demo", confirm=True)
        other = self.install({**MANIFEST, "id": "outro", "contributes": {"providers": ["outro"]}}, code="X = 1\n")
        loader.enable("outro", confirm=True)
        (other / "plugin.py").write_text("X = 2\n", encoding="utf-8")
        doctor = run_cli("doctor", env=self.env())
        line = doctor["summary"]["plugins"]
        self.assertIn("2", line)
        self.assertIn("demo (failed)", line)
        self.assertIn("outro (suspended)", line)

    def test_invalid_plugin_shows_in_the_summary(self):
        (self.home / "plugins" / "quebrado").mkdir(parents=True)
        doctor = run_cli("doctor", env=self.env())
        self.assertIn("quebrado (invalid)", doctor["summary"]["plugins"])

    def test_no_summary_line_without_plugins_or_when_all_are_fine(self):
        self.assertNotIn("plugins", run_cli("doctor", env=self.env())["summary"])
        self.install()
        loader.enable("demo", confirm=True)
        self.assertNotIn("plugins", run_cli("doctor", env=self.env())["summary"])


class InspectLocalCopyTests(LoaderTestCase):
    """`inspect` de fonte que veio por rota não diz "baixar o arquivo inteiro (0.0 MB)"."""

    def test_route_copy_is_named_and_small_sizes_use_kb(self):
        from getbrolls.commands import inspect_warnings

        found = inspect_warnings({"duration_s": 3.0, "downloaded_bytes": 51 * 1024, "local_copy": "pasta_local"})
        self.assertEqual(1, len(found))
        self.assertIn("cópia local", found[0])
        self.assertIn("pasta_local", found[0])
        self.assertIn("51 KB", found[0])
        self.assertNotIn("baixar", found[0])
        self.assertNotIn("0.0 MB", found[0])

    def test_route_copy_of_a_big_file_keeps_mb(self):
        from getbrolls.commands import inspect_warnings

        found = inspect_warnings({"downloaded_bytes": 3 * 1024 * 1024, "local_copy": "pasta_local"})
        self.assertIn("3.0 MB", found[0])

    def test_probe_direct_marks_the_route_copy(self):
        from getbrolls import commands
        from getbrolls.models import candidate

        item = candidate("demo", "1", "Praia")
        item["acquisition"] = {"status": "available", "method": "plugin:pasta_local", "evidence": []}
        target = self.home / "v.mp4"
        target.write_bytes(b"x" * 2048)
        ledger = type("L", (), {})()
        with (
            patch("getbrolls.acquisition.cache_direct_media", return_value=str(target)),
            patch.object(commands, "probe", return_value={"duration_s": 3.0}),
        ):
            probe = commands.probe_direct(ledger, item)
        self.assertEqual("pasta_local", probe["local_copy"])

    def test_builtin_direct_source_is_unchanged(self):
        from getbrolls.commands import inspect_warnings

        found = inspect_warnings({"downloaded_bytes": 51 * 1024})
        self.assertIn("baixar o arquivo inteiro (0.0 MB)", found[0])


def _plugin_fetched():
    from test_delivery import fetched

    c = fetched("a", "praia_por_do_sol")
    c["provider"] = "demo"
    c["id"] = "demo:a"
    c["source_url"] = None
    c["creator"]["name"] = None
    c["acquisition"] = {"status": "available", "method": "plugin:demo", "evidence": []}
    return c


class PluginProvenanceTests(LoaderTestCase):
    """ORIGEM.md/credits.md de candidato de plugin nomeiam o plugin e o arquivo."""

    def test_origin_names_the_plugin_and_local_file(self):
        from getbrolls import delivery

        self.install()
        loader.enable("demo", confirm=True)
        lines = delivery.render_origin(_plugin_fetched(), "00-sem-beat.mp4").splitlines()
        self.assertIn("- Fonte: plugin demo (arquivo local)", lines)
        # Texto de plugin sai com Markdown escapado (o `_` vira `\_`, e renderiza igual).
        self.assertIn("- Título na fonte: praia\\_por\\_do\\_sol", lines)

    def test_origin_with_a_public_url_keeps_it_next_to_the_plugin(self):
        from getbrolls import delivery

        self.install()
        c = _plugin_fetched()
        c["source_url"] = "https://demo.example/v/1"
        # URL de plugin sai com o gatilho do autolink quebrado (`https\\://`), e renderiza igual.
        self.assertIn(
            "- Fonte: plugin demo (https\\://demo.example/v/1)",
            delivery.render_origin(c, "a.mp4").splitlines(),
        )

    def test_credits_name_the_plugin_and_title(self):
        import tempfile

        from test_delivery import project

        from getbrolls.rendering import render

        self.install()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project(tmp, [_plugin_fetched()])
            render(ledger)
            lines = (ledger.root / "credits.md").read_text(encoding="utf-8").splitlines()
        self.assertIn("- Fonte: plugin demo (arquivo local)", lines)
        # Texto de plugin sai com Markdown escapado (o `_` vira `\_`, e renderiza igual).
        self.assertIn("- Título na fonte: praia\\_por\\_do\\_sol", lines)

    def test_builtin_lines_are_unchanged(self):
        import tempfile

        from test_delivery import fetched, project

        from getbrolls import delivery
        from getbrolls.rendering import render

        c = fetched("a", "Palco")
        c["source_url"] = None
        self.assertIn("- Fonte: original local", delivery.render_origin(c, "a.mp4").splitlines())
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project(tmp, [c])
            render(ledger)
            text = (ledger.root / "credits.md").read_text(encoding="utf-8")
        self.assertIn("- Fonte: original local", text.splitlines())
        self.assertNotIn("Título na fonte", text)


class SearchHelpTests(LoaderTestCase):
    """`search --help` cita fontes de plugin e manda rodar `providers`."""

    def test_provider_help_mentions_plugins_and_providers(self):
        from getbrolls.cli import build_parser

        subparsers = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
        search = subparsers.choices["search"]
        provider = next(action for action in search._actions if "--provider" in action.option_strings)
        self.assertIn("plugin", provider.help or "")
        self.assertIn("providers", provider.help or "")


@skip_unless_ffmpeg
class StatusNextAgreesWithDoTests(FetchRouteCase):
    """Com candidato de rota `fetch`, `summary.next` não contradiz `summary.do`."""

    def test_next_says_reference_only_when_do_does(self):
        from test_delivery import with_brief

        self.enable(duration="0")
        with_brief(str(self.project))
        # `search --shot` só consulta as fontes do beat: a fonte do plugin entra na lista.
        brief = self.project / "BRIEF.md"
        brief.write_text(
            brief.read_text(encoding="utf-8").replace(
                '"allowed_sources": [', '"allowed_sources": [\n        "demo",', 1
            ),
            encoding="utf-8",
        )
        self.gb("search", "--provider", "demo", "--query", "mar", "--shot", "abertura")
        summary = run_cli("status", project=self.project, env=self.env)["summary"]
        self.assertEqual("preview", summary["do"]["step"])
        self.assertIn("--reference-only", summary["do"]["command"])
        self.assertIn("--reference-only", summary["next"])
        self.assertNotIn("Gere prévias com preview para os candidatos ainda sem quadro", summary["next"])


class StrictScrubResidualNamesTests(LoaderTestCase):
    """Re-review, observação 1: `api_token`/`apiToken`, `pwd`, `hmac`, `signing_key` e
    `encryption_key` também caem no scrub estrito; chaves de paginação sobrevivem."""

    def test_residual_credential_names_drop(self):
        from getbrolls.http import _scrub

        payload = dict.fromkeys(
            (
                "api_token",
                "apiToken",
                "API-Token",
                "pwd",
                "PWD",
                "hmac",
                "HMAC",
                "signing_key",
                "signingKey",
                "encryption_key",
                "encryptionKey",
            ),
            "SEGREDO",
        )
        self.assertEqual({}, _scrub(payload, strict=True))

    def test_pagination_and_lookalike_keys_survive(self):
        from getbrolls.http import _scrub

        keep = (
            "next_page_token",
            "nextPageToken",
            "page_token",
            "continuation_token",
            "sort_key",
            "cursor_key",
            "cursor",
            "hmac_algorithm",
            "pwd_policy",
            "signing_key_id",
            "encryption_key_id",
            "api_token_expires_at",
        )
        payload = dict.fromkeys(keep, "v")
        self.assertEqual(payload, _scrub(payload, strict=True))

    def test_builtin_path_is_unchanged(self):
        from getbrolls.http import _scrub

        payload = {"api_token": "v", "pwd": "v", "hmac": "v", "signing_key": "v"}
        self.assertEqual(payload, _scrub(payload))


class PluginsEnvelopeTests(LoaderTestCase):
    """Erro de uso em `plugins`/`x` sai sem traceback nem a dica de recovery_pending."""

    def test_plugins_and_x_user_errors_have_no_traceback_or_recovery_hint(self):
        env = {"GB_HOME": str(self.home)}
        for args in (("plugins", "--action", "enable"), ("x", "nao_existe", "cmd")):
            with self.subTest(args=args):
                err = run_cli(*args, expect=2, env=env)
                self.assertNotIn("traceback", err)
                self.assertNotIn("hint", err)
                self.assertTrue(err["error"])

    def test_other_commands_keep_the_full_envelope(self):
        import tempfile

        project = tempfile.mkdtemp(prefix="gb-project-", dir=self.home)
        err = run_cli("inspect", "--url", " ", project=project, expect=2, env={"GB_HOME": str(self.home)})
        self.assertIn("traceback", err)
        self.assertIn("hint", err)


class DocsGapsTests(LoaderTestCase):
    """O que o agente lê primeiro diz o comando que funciona."""

    @staticmethod
    def read(relative):
        from _paths import ROOT

        return (ROOT / relative).read_text(encoding="utf-8")

    def test_skill_names_expect_for_plugin_install_and_suspended_enable(self):
        for relative in ("SKILL.md", "skills/get-brolls/SKILL.md"):
            text = self.read(relative)
            self.assertIn("--yes --expect <sha256>", text, relative)
            self.assertIn("plugin suspenso", text, relative)

    def test_guide_and_sdk_cover_reenable_gb_plugins_and_live(self):
        for relative in ("docs/GUIDE.md", "docs/SDK.md"):
            text = self.read(relative)
            with self.subTest(relative=relative):
                self.assertIn("desligado por GB_PLUGINS", text)
                self.assertIn("no_media_url (fonte", text)
                self.assertIn('refresh: "route"', text)
                self.assertIn("diff", text)
        self.assertIn("`enable` × `install`/`update`", self.read("docs/SDK.md"))

    def test_pasta_local_readme_is_gb_home_aware_and_explains_search_vs_preview(self):
        text = self.read("examples/plugins/pasta_local/README.md")
        self.assertIn("GB_HOME", text)
        self.assertNotIn("cp -r examples/plugins/pasta_local ~/.getbrolls/plugins/", text)
        self.assertIn("a **busca funciona**", text)
        self.assertIn("a **prévia é recusada**", text)

    def test_scaffold_readme_says_to_run_tests_from_the_plugin_folder(self):
        from getbrolls.sdk.scaffold import README

        self.assertIn("diretório atual", README)

    def test_contributing_shows_how_to_run_one_test_file(self):
        self.assertIn('discover -s tests -p "test_x.py"', self.read("CONTRIBUTING.md"))


class MessageFollowUpTests(LoaderTestCase):
    """Ajustes pequenos de texto e fluxo nas mensagens do SDK."""

    def env(self):
        return {"GB_HOME": str(self.home)}

    def test_pin_map_is_capped_like_install(self):
        from getbrolls.sdk import install

        self.assertEqual(install.MAX_FILES, loader.PIN_MAP_MAX_FILES)
        folder = self.install()
        with patch.object(loader, "PIN_MAP_MAX_FILES", 1):
            loader.enable("demo", confirm=True)
            pin = _state(self.home)["enabled"]["demo"]
            self.assertNotIn("files", pin)
            self.assertTrue(pin["files_omitted"])
            self.assertEqual(loader.folder_digest(folder), pin["sha256"])
            (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mudou\n", encoding="utf-8")
            preview = loader.enable("demo", confirm=False)
        self.assertIsNone(preview["diff"]["files"])
        self.assertIn("muitos arquivos, diff omitido", preview["diff"]["note"])
        with self.assertRaises(ValueError):
            loader.enable("demo", confirm=True)

    def test_current_folder_over_the_cap_omits_the_diff_too(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        (folder / "extra.py").write_text("X = 1\n", encoding="utf-8")
        with patch.object(loader, "PIN_MAP_MAX_FILES", 2):
            preview = loader.enable("demo", confirm=False)
        self.assertIsNone(preview["diff"]["files"])
        self.assertIn("muitos arquivos, diff omitido", preview["diff"]["note"])

    def test_disable_keeps_the_last_pin_and_a_changed_re_enable_needs_expect(self):
        folder = self.install()
        loader.enable("demo", confirm=True)
        pinned = _state(self.home)["enabled"]["demo"]
        loader.disable("demo")
        state = _state(self.home)
        self.assertNotIn("demo", state["enabled"])
        self.assertEqual(pinned, state["last_pins"]["demo"])
        self.assertEqual("disabled", loader.inventory()[0]["status"])
        (folder / "plugin.py").write_text(PLUGIN_CODE + "\n# mudou\n", encoding="utf-8")
        preview = loader.enable("demo", confirm=False)
        self.assertEqual(["plugin.py"], preview["diff"]["files"]["changed"])
        self.assertIn("--expect", preview["note"])
        with self.assertRaises(ValueError):
            loader.enable("demo", confirm=True)
        self.assertTrue(loader.enable("demo", confirm=True, expect=preview["plugin"]["sha256"])["enabled"])
        self.assertNotIn("demo", _state(self.home).get("last_pins", {}))

    def test_disable_then_unchanged_re_enable_keeps_plain_yes(self):
        self.install()
        loader.enable("demo", confirm=True)
        loader.disable("demo")
        preview = loader.enable("demo", confirm=False)
        self.assertNotIn("diff", preview)
        self.assertTrue(loader.enable("demo", confirm=True)["enabled"])

    def test_state_without_last_pins_stays_valid_and_a_bad_one_is_refused(self):
        folder = self.install()
        sha = loader.folder_digest(folder)
        (self.home / "plugins.json").write_text(
            json.dumps({"enabled": {"demo": {"version": "0.1.0", "sha256": sha}}}), encoding="utf-8"
        )
        self.assertEqual("enabled", loader.inventory()[0]["status"])
        (self.home / "plugins.json").write_text(json.dumps({"enabled": {}, "last_pins": {"demo": 1}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            loader.read_state()

    def test_update_of_a_disabled_plugin_refreshes_its_last_pin(self):
        from getbrolls.sdk import install

        source = self.home / "fonte" / "demo"
        source.mkdir(parents=True)
        (source / "getbrolls-plugin.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (source / "plugin.py").write_text(PLUGIN_CODE, encoding="utf-8")
        install.install(
            str(source), confirm=True, expect=install.install(str(source), confirm=False)["plugin"]["sha256"]
        )
        loader.disable("demo")
        (source / "plugin.py").write_text(PLUGIN_CODE + "\n# v2\n", encoding="utf-8")
        install.update("demo", confirm=True, expect=install.update("demo", confirm=False)["plugin"]["sha256"])
        # A pessoa já aprovou este conteúdo no update (--expect): religar é só --yes.
        self.assertNotIn("diff", loader.enable("demo", confirm=False))

    def test_search_does_not_prefix_the_source_name_to_an_unavailable_plugin_message(self):
        from getbrolls.commands import provider_error_text

        text = "Fonte pasta_local é do plugin pasta_local, que está suspended. Rode plugins --action list / doctor."
        self.assertEqual(text, provider_error_text("pasta_local", text))
        self.assertEqual("youtube: falhou", provider_error_text("youtube", "falhou"))

    def test_expect_help_mentions_the_suspended_enable(self):
        from getbrolls.cli import build_parser

        subparsers = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
        expect = next(a for a in subparsers.choices["plugins"]._actions if "--expect" in a.option_strings)
        self.assertIn("enable", expect.help or "")

    @unittest.skipIf(os.name == "nt" or not shutil.which("sh"), "comando POSIX do README")
    def test_pasta_local_readme_copy_command_works_on_a_fresh_gb_home(self):
        import re
        import subprocess

        from _paths import ROOT

        text = (ROOT / "examples/plugins/pasta_local/README.md").read_text(encoding="utf-8")
        block = next(b for b in re.findall(r"```sh\n(.*?)```", text, re.DOTALL) if "cp -r" in b)
        fresh = self.home / "novo-home"
        # Duas vezes: a primeira numa pasta pessoal nova, a segunda por cima da cópia.
        for _ in range(2):
            subprocess.run(["sh", "-c", block], cwd=ROOT, env={**os.environ, "GB_HOME": str(fresh)}, check=True)
        plugins = fresh / "plugins"
        self.assertTrue((plugins / "pasta_local" / "getbrolls-plugin.json").is_file())
        self.assertFalse((plugins / "plugin.py").exists())
        self.assertFalse((plugins / "pasta_local" / "pasta_local").exists())
        self.assertEqual(
            [plugins / "pasta_local" / "getbrolls-plugin.json"], sorted(plugins.rglob("getbrolls-plugin.json"))
        )
