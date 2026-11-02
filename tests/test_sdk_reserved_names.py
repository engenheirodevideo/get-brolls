"""Nomes reservados do SDK: tipos de contribuição, chave `engines` e ids de plugin."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_loader import MANIFEST, LoaderTestCase
from test_sdk_manifest import BASE, write_plugin

from getbrolls.sdk import loader, scaffold
from getbrolls.sdk.contracts import CORE, NAME_RE, OFFICIAL_IDS, RESERVED_IDS
from getbrolls.sdk.manifest import (
    CONTRIBUTION_KINDS,
    PERMISSION_KEYS,
    SUPPORTED_KINDS,
    ManifestError,
    read_manifest,
)

FUTURE_KINDS = (
    "analyzers",
    "templates",
    "styles",
    "recipes",
    "rules",
    "hooks",
    "catalogs",
    "eval_rubrics",
)
RETIRED = {
    "engines": "contributes.environments",
    "brief_templates": "contributes.templates",
    "roteiro_templates": "contributes.templates",
    "themes": "contributes.styles",
    "capturers": "contributes.environments",
}
RESERVED = ("cliente", "catalogo", "direcao", "template", "projeto", "estilo", "recipe", "receita")


class ReservedContributionTests(unittest.TestCase):
    def test_future_kinds_are_known_but_not_supported(self):
        for kind in FUTURE_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(kind, CONTRIBUTION_KINDS)
                self.assertNotIn(kind, SUPPORTED_KINDS)
                manifest = {**BASE, "contributes": {kind: ["acme_drive"]}}
                with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError) as caught:
                    read_manifest(write_plugin(tmp, manifest))
                self.assertIn(f"contributes.{kind} ainda não é suportado nesta versão", str(caught.exception))

    def test_an_empty_future_kind_is_accepted(self):
        manifest = {**BASE, "contributes": {**BASE["contributes"], **{kind: [] for kind in FUTURE_KINDS}}}
        with tempfile.TemporaryDirectory() as tmp:
            data = read_manifest(write_plugin(tmp, manifest))
        self.assertEqual(["acme_drive"], data["contributes"]["providers"])

    def test_retired_kinds_point_to_the_new_name(self):
        for old, new in RETIRED.items():
            self.assertNotIn(old, CONTRIBUTION_KINDS)
            for names in ([], ["acme_drive"]):
                with self.subTest(kind=old, names=names):
                    manifest = {**BASE, "contributes": {**BASE["contributes"], old: names}}
                    with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError) as caught:
                        read_manifest(write_plugin(tmp, manifest))
                    self.assertIn(f"contributes.{old} saiu", str(caught.exception))
                    self.assertIn(new, str(caught.exception))


class EnginesKeyTests(unittest.TestCase):
    def test_top_level_engines_with_content_points_to_requires(self):
        manifest = {**BASE, "engines": {"hyperframes": ">=0.8.73,<0.9"}}
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError) as caught:
            read_manifest(write_plugin(tmp, manifest))
        self.assertIn("engines foi substituído por requires (requires.runtimes)", str(caught.exception))
        self.assertNotIn("campo desconhecido", str(caught.exception))

    def test_empty_engines_is_accepted_like_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = read_manifest(write_plugin(tmp, {**BASE, "engines": {}}))
        self.assertEqual("acme_drive", data["id"])


class ReservedIdTests(LoaderTestCase):
    def test_the_list_of_reserved_ids(self):
        self.assertEqual((CORE, *RESERVED), RESERVED_IDS)

    def test_manifest_refuses_a_reserved_id(self):
        for ident in RESERVED:
            with self.subTest(ident=ident), tempfile.TemporaryDirectory() as tmp:
                folder = write_plugin(tmp, {**BASE, "id": ident, "contributes": {}})
                with self.assertRaises(ManifestError) as caught:
                    read_manifest(folder)
                self.assertIn(f'id "{ident}" é reservado do getbrolls', str(caught.exception))

    def test_new_refuses_a_reserved_id(self):
        parent = Path(tempfile.mkdtemp(prefix="gb-new-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        for ident in RESERVED:
            with self.subTest(ident=ident):
                with self.assertRaises(ValueError) as caught:
                    scaffold.new(ident, "provider", parent=parent)
                self.assertIn("reservado", str(caught.exception))
                self.assertFalse((parent / ident).exists())
        out = run_cli("plugins", "--action", "new", "--id", "direcao", "--kind", "command", "--path", parent, expect=1)
        self.assertIn("reservado", out["message"])

    def test_install_refuses_a_reserved_id(self):
        source = Path(tempfile.mkdtemp(prefix="gb-src-")) / "cliente"
        self.addCleanup(shutil.rmtree, source.parent, ignore_errors=True)
        source.mkdir()
        (source / "getbrolls-plugin.json").write_text(
            json.dumps({**MANIFEST, "id": "cliente", "contributes": {}}), encoding="utf-8"
        )
        (source / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
        out = run_cli("plugins", "--action", "install", "--source", source, expect=1, env={"GB_HOME": str(self.home)})
        self.assertIn('id "cliente" é reservado do getbrolls', out["message"])
        self.assertFalse((self.home / "plugins" / "cliente").exists())

    def test_enable_refuses_a_reserved_id(self):
        self.install({**MANIFEST, "id": "projeto", "contributes": {}}, code="def register(api):\n    pass\n")
        rows = {row["id"]: row for row in loader.inventory()}
        self.assertEqual("invalid", rows["projeto"]["status"])
        with self.assertRaises(ValueError) as caught:
            loader.enable("projeto", confirm=True)
        self.assertIn('id "projeto" é reservado do getbrolls', str(caught.exception))
        self.assertFalse(loader.state_path().exists())


class OfficialIdTests(LoaderTestCase):
    def test_the_official_ids_are_valid_plugin_names(self):
        self.assertEqual(
            ("hyperframes", "davinci", "asr", "analysis_vision", "analysis_audio", "analysis_ocr", "analysis_game"),
            OFFICIAL_IDS,
        )
        for ident in OFFICIAL_IDS:
            with self.subTest(ident=ident):
                self.assertIsNotNone(NAME_RE.fullmatch(ident))
                self.assertNotIn(ident, RESERVED_IDS)

    def test_new_refuses_an_official_id(self):
        parent = Path(tempfile.mkdtemp(prefix="gb-new-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        for ident in OFFICIAL_IDS:
            with self.subTest(ident=ident), self.assertRaises(ValueError) as caught:
                scaffold.new(ident, "exporter", parent=parent)
            self.assertIn("plugin oficial do getbrolls", str(caught.exception))
            self.assertFalse((parent / ident).exists())
        out = run_cli("plugins", "--action", "new", "--id", "davinci", "--kind", "command", "--path", parent, expect=1)
        self.assertIn("plugin oficial do getbrolls", out["message"])

    def test_a_manifest_with_an_official_id_is_refused_outside_the_installation(self):
        # Plugin oficial só carrega da instalação do getbrolls (a origem oficial); de qualquer pasta de
        # plugins, inclusive a cópia do antigo exemplo `hyperframes`, o manifesto é recusado.
        for ident in OFFICIAL_IDS:
            with (
                self.subTest(ident=ident),
                tempfile.TemporaryDirectory() as tmp,
                self.assertRaises(ManifestError) as caught,
            ):
                read_manifest(write_plugin(tmp, {**BASE, "id": ident}))
            self.assertIn("reservado para plugin oficial do getbrolls", str(caught.exception))

    def test_an_installed_copy_of_the_old_example_is_invalid_and_the_others_still_load(self):
        noop = "def register(api):\n    pass\n"
        self.install({**MANIFEST, "id": "hyperframes", "contributes": {}}, code=noop)
        self.install({**MANIFEST, "id": "vizinho", "contributes": {}}, code=noop)
        rows = {row["id"]: row for row in loader.inventory()}
        self.assertEqual("invalid", rows["hyperframes"]["status"])
        self.assertIn("reservado para plugin oficial do getbrolls", rows["hyperframes"]["reason"])
        self.assertNotEqual("invalid", rows["vizinho"]["status"])

    def test_official_prefixes_are_reserved_too_and_lookalikes_are_not(self):
        # `<oficial>_x` fica de fora (ninguém se passa por irmão de plugin oficial); `davinci2` e `hyper_frames`
        # não começam por `<oficial>_` e seguem livres.
        for ident in ("hyperframes_x", "davinci_media", "asr_cloud", "analysis_vision_x", "analysis_game_plus"):
            with (
                self.subTest(ident=ident),
                tempfile.TemporaryDirectory() as tmp,
                self.assertRaises(ManifestError) as caught,
            ):
                read_manifest(write_plugin(tmp, {**BASE, "id": ident}))
            self.assertIn("reservado para plugin oficial do getbrolls", str(caught.exception))
            self.assertIn(ident, str(caught.exception))
        for ident in ("davinci2", "hyper_frames", "asrx", "analysis_other", "hyperframe"):
            with self.subTest(free=ident), tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(ident, read_manifest(write_plugin(tmp, {**BASE, "id": ident}))["id"])

    def test_new_refuses_an_official_prefix(self):
        parent = Path(tempfile.mkdtemp(prefix="gb-new-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        for ident in ("hyperframes_x", "analysis_vision_x"):
            with self.subTest(ident=ident), self.assertRaises(ValueError) as caught:
                scaffold.new(ident, "exporter", parent=parent)
            self.assertIn("plugin oficial do getbrolls", str(caught.exception))
            self.assertFalse((parent / ident).exists())
        scaffold.new("davinci2", "exporter", parent=parent)
        self.assertTrue((parent / "davinci2").is_dir())

    def test_install_from_a_folder_refuses_an_official_id_and_a_prefix(self):
        for ident in ("hyperframes", "hyperframes_x"):
            with self.subTest(ident=ident):
                source = Path(tempfile.mkdtemp(prefix="gb-src-")) / ident
                self.addCleanup(shutil.rmtree, source.parent, ignore_errors=True)
                source.mkdir()
                (source / "getbrolls-plugin.json").write_text(
                    json.dumps({**MANIFEST, "id": ident, "contributes": {}}), encoding="utf-8"
                )
                (source / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
                out = run_cli(
                    "plugins", "--action", "install", "--source", source, expect=1, env={"GB_HOME": str(self.home)}
                )
                self.assertIn("reservado para plugin oficial do getbrolls", out["message"])
                self.assertFalse((self.home / "plugins" / ident).exists())

    def test_enable_of_an_installed_official_id_is_refused_and_its_code_never_runs(self):
        marker = self.home / "ran.txt"
        code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\ndef register(api):\n    pass\n"
        for ident in ("hyperframes", "analysis_ocr_x"):
            self.install({**MANIFEST, "id": ident, "contributes": {}}, code=code)
        rows = {row["id"]: row for row in loader.inventory()}
        for ident in ("hyperframes", "analysis_ocr_x"):
            with self.subTest(ident=ident):
                self.assertEqual("invalid", rows[ident]["status"])
                self.assertIn("reservado para plugin oficial do getbrolls", rows[ident]["reason"])
                with self.assertRaises(ValueError) as caught:
                    loader.enable(ident, confirm=True)
                self.assertIn("reservado para plugin oficial do getbrolls", str(caught.exception))
                # `hyperframes` vem com o getbrolls: a porta única recusa antes (uso, 2); o prefixo segue recusado (1).
                expect, phrase = (
                    (2, "é plugin oficial e vem com o getbrolls") if ident == "hyperframes" else (1, "reservado")
                )
                out = run_cli(
                    "plugins",
                    "--action",
                    "enable",
                    "--id",
                    ident,
                    "--yes",
                    expect=expect,
                    env={"GB_HOME": str(self.home)},
                )
                self.assertIn(phrase, out["error"])
        self.assertFalse(marker.exists())
        self.assertFalse(loader.state_path().exists())


class RetiredPermissionTests(unittest.TestCase):
    def test_host_paths_and_run_left_and_say_so_even_empty(self):
        for key in ("host_paths", "run"):
            for value in ([], ["davinci.scripts"]):
                with (
                    self.subTest(key=key, value=value),
                    tempfile.TemporaryDirectory() as tmp,
                    self.assertRaises(ManifestError) as caught,
                ):
                    read_manifest(write_plugin(tmp, {**BASE, "permissions": {key: value}}))
                self.assertIn(f"permissions.{key} saiu nesta versão", str(caught.exception))

    def test_the_four_permission_keys_remain(self):
        self.assertEqual(("network", "env", "paths", "project_write"), PERMISSION_KEYS)
        with tempfile.TemporaryDirectory() as tmp:
            data = read_manifest(write_plugin(tmp, {**BASE, "permissions": {"network": [], "env": [], "paths": []}}))
        self.assertEqual({"network": [], "env": [], "paths": [], "project_write": []}, data["permissions"])


if __name__ == "__main__":
    unittest.main()
