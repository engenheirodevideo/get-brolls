"""O pin guarda o caminho resolvido de cada raiz de `permissions.paths` (`roots_resolved`).

Na carga, uma raiz que hoje resolve para outro lugar fica ignorada, com um aviso que
nomeia a entrada como está escrita no manifesto; um pin antigo, sem o campo, continua
valendo como antes, com um aviso para habilitar de novo. O caminho resolvido fica só
no `plugins.json` (e na prévia de enable/install/update, que mostra o que vai ser gravado).
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_loader import LoaderTestCase

from getbrolls.http import ProviderError
from getbrolls.sdk import api as sdk_api
from getbrolls.sdk import install as install_mod
from getbrolls.sdk import loader
from getbrolls.sdk.api import PluginApi, route_scope
from getbrolls.sdk.files import resolved_roots
from getbrolls.sdk.registry import Registry, get_registry, reset_registry
from getbrolls.sdk.resolvers import resolve_with_plugins

CAN_SYMLINK = os.name != "nt"

RESOLVER_CODE = """
def register(api):
    api.resolver("demo", lambda kind, name: None, ["sfx"])
"""

HIT_CODE = """
from pathlib import Path

from getbrolls.sdk import ResolverHit


def register(api):
    api.resolver("demo", lambda kind, name: ResolverHit(str(Path.home() / "Acervo" / "porta.wav")), ["sfx"])
"""

PATHS_MANIFEST = {
    "id": "demo",
    "name": "Demo",
    "version": "0.1.0",
    "sdk_api": 1,
    "requires_getbrolls": ">=2.5,<3",
    "entry": "plugin.py",
    "contributes": {"resolvers": ["demo"]},
    "permissions": {"network": [], "env": [], "paths": ["~/Acervo", "~/Outra"]},
}


class PinnedRootsTestCase(LoaderTestCase):
    def setUp(self):
        super().setUp()
        self.person = Path(tempfile.mkdtemp(prefix="gb-pessoa-")).resolve()
        self.addCleanup(shutil.rmtree, self.person, ignore_errors=True)
        for name in ("Acervo", "Outra", "Fora"):
            (self.person / name).mkdir()
        (self.person / "Acervo" / "porta.wav").write_bytes(b"de dentro")
        (self.person / "Fora" / "porta.wav").write_bytes(b"de fora")
        home = patch.dict(os.environ, {"HOME": str(self.person), "USERPROFILE": str(self.person)})
        home.start()
        self.addCleanup(home.stop)

    def expected(self, acervo="Acervo", outra="Outra"):
        return {"~/Acervo": str(self.person / acervo), "~/Outra": str(self.person / outra)}

    def state(self):
        return json.loads((self.home / "plugins.json").read_text(encoding="utf-8"))

    def pinned(self):
        return self.state()["enabled"]["demo"]["roots_resolved"]

    def loaded_roots(self):
        reset_registry()
        return get_registry().resolver_roots("demo")

    def move_acervo_elsewhere(self):
        """`~/Acervo` passa a resolver para `~/Fora` (a pasta virou um link)."""
        (self.person / "Acervo").rename(self.person / "Acervo-original")
        (self.person / "Acervo").symlink_to(self.person / "Fora")


class EnableRecordsTests(PinnedRootsTestCase):
    def test_enable_previews_and_records_the_resolved_roots(self):
        self.install(PATHS_MANIFEST, RESOLVER_CODE)
        preview = loader.enable("demo", confirm=False)
        self.assertEqual(self.expected(), preview["plugin"]["roots_resolved"])
        done = loader.enable("demo", confirm=True)
        self.assertEqual(self.expected(), done["plugin"]["roots_resolved"])
        self.assertEqual(self.expected(), self.pinned())
        self.assertEqual(tuple(self.expected().values()), self.loaded_roots())

    def test_a_plugin_without_paths_records_an_empty_map_and_previews_nothing(self):
        manifest = {**PATHS_MANIFEST, "contributes": {}, "permissions": {"network": [], "env": [], "paths": []}}
        self.install(manifest, "def register(api):\n    pass\n")
        self.assertNotIn("roots_resolved", loader.enable("demo", confirm=True)["plugin"])
        self.assertEqual({}, self.pinned())

    @unittest.skipUnless(CAN_SYMLINK, "symlink exige privilégio no Windows")
    def test_reenable_recomputes_the_field(self):
        self.install(PATHS_MANIFEST, RESOLVER_CODE)
        loader.enable("demo", confirm=True)
        loader.disable("demo")
        self.move_acervo_elsewhere()
        preview = loader.enable("demo", confirm=False)
        self.assertEqual(self.expected(acervo="Fora"), preview["plugin"]["roots_resolved"])
        loader.enable("demo", confirm=True)
        self.assertEqual(self.expected(acervo="Fora"), self.pinned())
        self.assertEqual(tuple(self.expected(acervo="Fora").values()), self.loaded_roots())

    def test_the_field_is_outside_the_content_sha256(self):
        folder = self.install(PATHS_MANIFEST, RESOLVER_CODE)
        loader.enable("demo", confirm=True)
        self.assertEqual(loader.folder_digest(folder), self.state()["enabled"]["demo"]["sha256"])


@unittest.skipUnless(CAN_SYMLINK, "symlink exige privilégio no Windows")
class InstallUpdateRecordsTests(PinnedRootsTestCase):
    def test_install_and_update_preview_and_record_the_resolved_roots(self):
        source = self.person / "fonte" / "demo"
        source.mkdir(parents=True)
        (source / "getbrolls-plugin.json").write_text(json.dumps(PATHS_MANIFEST), encoding="utf-8")
        (source / "plugin.py").write_text(RESOLVER_CODE, encoding="utf-8")
        preview = install_mod.install(str(source), confirm=False)
        self.assertEqual(self.expected(), preview["plugin"]["roots_resolved"])
        install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        self.assertEqual(self.expected(), self.pinned())

        self.move_acervo_elsewhere()
        preview = install_mod.update("demo", confirm=False)
        self.assertEqual(self.expected(acervo="Fora"), preview["plugin"]["roots_resolved"])
        install_mod.update("demo", confirm=True, expect=preview["plugin"]["sha256"])
        self.assertEqual(self.expected(acervo="Fora"), self.pinned())


class LoadChecksTests(PinnedRootsTestCase):
    @unittest.skipUnless(CAN_SYMLINK, "symlink exige privilégio no Windows")
    def test_a_root_that_resolves_elsewhere_is_ignored_with_a_warning_naming_the_entry(self):
        self.install(PATHS_MANIFEST, RESOLVER_CODE)
        loader.enable("demo", confirm=True)
        self.move_acervo_elsewhere()
        with patch.object(sdk_api, "record_warning") as warn:
            roots = self.loaded_roots()
        self.assertEqual((str(self.person / "Outra"),), roots)
        self.assertEqual("enabled", get_registry().plugins["demo"]["status"])
        warn.assert_called_once()
        code, message = warn.call_args.args
        self.assertEqual("PLUGIN_PATH_CHANGED", code)
        self.assertIn("~/Acervo", message)
        self.assertNotIn(str(self.person), message)

    def test_an_old_pin_without_the_field_keeps_todays_roots_with_a_warning(self):
        self.install(PATHS_MANIFEST, RESOLVER_CODE)
        loader.enable("demo", confirm=True)
        state = self.state()
        del state["enabled"]["demo"]["roots_resolved"]
        (self.home / "plugins.json").write_text(json.dumps(state), encoding="utf-8")
        with patch.object(sdk_api, "record_warning") as warn:
            roots = self.loaded_roots()
        self.assertEqual(tuple(self.expected().values()), roots)
        warn.assert_called_once()
        code, message = warn.call_args.args
        self.assertEqual("PLUGIN_PIN_OUTDATED", code)
        self.assertIn("plugins --action enable --id demo", message)
        self.assertNotIn("\n", message)
        self.assertNotIn(str(self.person), message)

    def test_local_file_says_the_folders_changed_when_every_root_moved(self):
        manifest = {**PATHS_MANIFEST, "contributes": {}}
        pin = {"roots_resolved": {"~/Acervo": str(self.person / "Fora"), "~/Outra": str(self.person / "Fora")}}
        api = PluginApi(manifest, Registry(), pin=pin)
        work = self.person / "work"
        work.mkdir()
        with (
            patch.object(sdk_api, "record_warning"),
            route_scope("demo", work),
            self.assertRaises(ProviderError) as caught,
        ):
            api.local_file(self.person / "Acervo" / "porta.wav")
        self.assertIn("mudaram desde o enable", str(caught.exception))
        self.assertEqual([], list(work.iterdir()))

    def test_without_a_pin_the_roots_resolved_now_count_and_nothing_is_warned(self):
        with patch.object(sdk_api, "record_warning") as warn:
            api = PluginApi(PATHS_MANIFEST, Registry())
            api.resolver("demo", lambda kind, name: None, ["sfx"])
        warn.assert_not_called()


class EdgeCaseTests(PinnedRootsTestCase):
    def not_utf8(self, paths):
        """`resolved_roots` do loader com `~/Acervo` num caminho que não cabe em UTF-8 (Linux)."""
        real = resolved_roots(paths)
        bad = Path(str(self.person / "Acervo") + "\udcff")
        return [(raw, bad if raw == "~/Acervo" else resolved) for raw, resolved in real[0]], real[1]

    def rewrite_field(self, value):
        state = self.state()
        state["enabled"]["demo"]["roots_resolved"] = value
        (self.home / "plugins.json").write_text(json.dumps(state), encoding="utf-8")

    def test_a_root_path_outside_utf8_stays_out_of_the_pin_and_is_ignored_at_load(self):
        self.install(PATHS_MANIFEST, RESOLVER_CODE)
        with patch.object(loader, "resolved_roots", self.not_utf8):
            preview = loader.enable("demo", confirm=False)
            loader.enable("demo", confirm=True)
        only_outra = {"~/Outra": str(self.person / "Outra")}
        self.assertEqual(only_outra, preview["plugin"]["roots_resolved"])
        self.assertEqual(only_outra, self.pinned())
        with patch.object(sdk_api, "record_warning") as warn:
            self.assertEqual((str(self.person / "Outra"),), self.loaded_roots())
        self.assertEqual("PLUGIN_PATH_CHANGED", warn.call_args.args[0])
        self.assertIn("~/Acervo", warn.call_args.args[1])

    def test_install_with_a_root_path_outside_utf8_is_not_left_half_done(self):
        source = self.person / "fonte" / "demo"
        source.mkdir(parents=True)
        (source / "getbrolls-plugin.json").write_text(json.dumps(PATHS_MANIFEST), encoding="utf-8")
        (source / "plugin.py").write_text(RESOLVER_CODE, encoding="utf-8")
        with patch.object(loader, "resolved_roots", self.not_utf8):
            preview = install_mod.install(str(source), confirm=False)
            install_mod.install(str(source), confirm=True, expect=preview["plugin"]["sha256"])
        self.assertEqual({"~/Outra": str(self.person / "Outra")}, self.pinned())
        self.assertEqual("enabled", loader.inventory()[0]["status"])

    def test_a_malformed_field_ignores_every_root_with_a_warning(self):
        self.install(PATHS_MANIFEST, RESOLVER_CODE)
        loader.enable("demo", confirm=True)
        for value in (["~/Acervo"], {"~/Acervo": 1, "~/Outra": str(self.person / "Outra")}, "x"):
            with self.subTest(value=value):
                self.rewrite_field(value)
                with patch.object(sdk_api, "record_warning") as warn:
                    self.assertEqual((), self.loaded_roots())
                self.assertEqual(["PLUGIN_PATH_CHANGED"] * 2, [call.args[0] for call in warn.call_args_list])

    def test_the_resolver_says_the_folders_changed_when_every_root_was_ignored(self):
        self.install(
            {**PATHS_MANIFEST, "permissions": {**PATHS_MANIFEST["permissions"], "paths": ["~/Acervo"]}}, HIT_CODE
        )
        loader.enable("demo", confirm=True)
        self.rewrite_field([])
        reset_registry()
        with patch.object(sdk_api, "record_warning"):
            hit, warnings = resolve_with_plugins(get_registry(), "sfx", "porta", (".wav",))
        self.assertIsNone(hit)
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn("mudaram desde o enable", warnings[0])
        self.assertNotIn("vale neste sistema", warnings[0])


@unittest.skipUnless(CAN_SYMLINK, "symlink exige privilégio no Windows")
class ResolvedPathStaysLocalTests(PinnedRootsTestCase):
    """O caminho resolvido não sai do plugins.json: nem log, nem list, nem doctor."""

    def test_the_resolved_path_never_shows_in_logs_list_or_doctor(self):
        self.install(PATHS_MANIFEST, RESOLVER_CODE)
        loader.enable("demo", confirm=True)
        self.move_acervo_elsewhere()
        secrets = [str(self.person / name) for name in ("Acervo", "Outra", "Fora")]
        with self.assertLogs("getbrolls", level="DEBUG") as cm:
            self.loaded_roots()
        env = {"GB_HOME": str(self.home), "HOME": str(self.person), "USERPROFILE": str(self.person)}
        outputs = [
            "\n".join(cm.output),
            json.dumps(run_cli("plugins", "--action", "list", env=env), ensure_ascii=False),
            json.dumps(run_cli("doctor", env=env), ensure_ascii=False),
        ]
        outputs += [
            path.read_text(encoding="utf-8", errors="replace")
            for path in self.home.rglob("*")
            if path.is_file() and path.name != "plugins.json" and "plugins" not in path.relative_to(self.home).parts
        ]
        for text in outputs:
            for secret in secrets:
                self.assertNotIn(secret, text)
        self.assertIn("~/Acervo", outputs[2])


if __name__ == "__main__":
    unittest.main()
