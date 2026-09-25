"""Onda de UX do SDK: achados do QA funcional (BUG-05..14, G2..G11) e nomes de segredo residuais."""

import json
import os
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from test_sdk_loader import MANIFEST, PLUGIN_CODE, LoaderTestCase

from getbrolls.sdk import loader
from getbrolls.sdk.registry import reset_registry


def _state(home):
    return json.loads((home / "plugins.json").read_text(encoding="utf-8"))


class PinFileMapTests(LoaderTestCase):
    """BUG-06: o pin guarda um mapa por arquivo para o re-enable mostrar o que mudou."""

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
    """BUG-06: re-enable de plugin suspenso mostra o diff e exige --expect, como install/update."""

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
    """BUG-07: a prévia manda o comando que funciona; o sucesso não manda rodar de novo."""

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
    """BUG-09: plugin fora de GB_PLUGINS diz o porquê; a busca aponta GB_PLUGINS, não enable."""

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
    """BUG-10: sem ".." nem "Plugin X: Plugin X:" nas mensagens de fonte indisponível
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
    """BUG-10: `search` não repete o nome da fonte na frente de "Plugin <fonte>: …"."""

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
    """BUG-08: erro de plugin não leva o "Confira docs/RULES.md." genérico; built-in igual."""

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
