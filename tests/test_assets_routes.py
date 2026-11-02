"""Rotas de componentes: diretiva → arquivo, projeto antes da biblioteca pessoal."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _isolation import GB_HOME
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

from getbrolls import assets, clients, export, export_plan, layout, renamed, runtime


class AssetRouteTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-assets-"))
        self.personal = GB_HOME / "assets"
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.personal, ignore_errors=True)

    def _put(self, root, folder, name, text="x"):
        path = root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_project_wins_over_personal(self):
        self._put(self.project, "assets/sfx", "Whoosh.wav")
        self._put(self.personal, "sfx", "whoosh.mp3")
        found = assets.resolve(self.project, "sfx", "whoosh")
        self.assertEqual(found["status"], "found")
        self.assertEqual(found["origin"], "project")
        self.assertTrue(found["path"].endswith("Whoosh.wav"))

    def test_personal_library_and_accent_fold(self):
        self._put(self.personal, "musica", "Épica.mp3")
        found = assets.resolve(self.project, "musica", "epica")
        self.assertEqual(found["origin"], "personal")

    def test_pending_and_license_warning(self):
        pending = assets.resolve(self.project, "sfx", "nada")
        self.assertEqual((pending["status"], pending["path"]), ("pending", None))
        self._put(self.project, "assets/sfx", "whoosh.wav")
        found = assets.resolve(self.project, "sfx", "whoosh")
        self.assertIn("licença não registrada", " ".join(found["warnings"]))
        self._put(
            self.project,
            "assets/sfx",
            "whoosh.licenca.json",
            json.dumps({"origem": "banco X", "licenca": "CC0", "credito": "Fulano"}),
        )
        found = assets.resolve(self.project, "sfx", "whoosh")
        self.assertEqual(found["license"]["licenca"], "CC0")
        self.assertEqual(found["warnings"], [])

    def test_license_sidecar_is_never_a_component(self):
        self._put(self.project, "assets/composicao", "abertura.licenca.json", "{}")
        self.assertEqual(assets.resolve(self.project, "composicao", "abertura")["status"], "pending")

    def test_old_composicoes_folder_warns_with_the_migrate_command(self):
        renamed.reset_old_shape_warnings()
        self.addCleanup(renamed.reset_old_shape_warnings)
        self._put(self.project, "assets/composicoes", "abertura.html", "<p/>")
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        found = assets.resolve(self.project, "composicao", "abertura")
        self.assertEqual("pending", found["status"])  # a pasta velha não é lida
        messages = [row["message"] for row in event["warnings"] if row["code"] == "OLD_SHAPE"]
        self.assertEqual(1, len(messages))
        self.assertIn("Renomeie a pasta `assets/composicoes/` para `assets/composicao/`.", messages[0])
        self.assertNotIn("migrate", messages[0])

    def test_the_old_folder_warns_for_each_project(self):
        other = Path(tempfile.mkdtemp(prefix="gb-assets-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        renamed.reset_old_shape_warnings()
        self.addCleanup(renamed.reset_old_shape_warnings)
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        for root in (self.project, other, self.project):
            self._put(root, "assets/composicoes", "abertura.html", "<p/>")
            assets.resolve(root, "composicao", "abertura")
        self.assertEqual(2, len([row for row in event["warnings"] if row["code"] == "OLD_SHAPE"]))

    @unittest.skipUnless(os.name != "nt" and os.geteuid() != 0, "permissões POSIX, sem root")
    def test_a_locked_assets_folder_is_an_io_error_not_an_absent_old_folder(self):
        self._put(self.project, "assets/composicoes", "abertura.html", "<p/>")
        locked = self.project / "assets"
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o755)
        with self.assertRaises(PermissionError) as raised:
            assets.resolve(self.project, "composicao", "abertura")
        self.assertEqual("IO_ERROR", runtime.error_code_for(raised.exception))

    def test_wrong_extension_ignored(self):
        self._put(self.project, "assets/sfx", "whoosh.txt")
        self.assertEqual(assets.resolve(self.project, "sfx", "whoosh")["status"], "pending")

    def test_ambiguity_is_decided_by_listing(self):
        self._put(self.project, "assets/sfx", "whoosh.wav")
        self._put(self.project, "assets/sfx", "whoosh.mp3")
        with self.assertRaises(ValueError) as ctx:
            assets.resolve(self.project, "sfx", "whoosh")
        self.assertIn("whoosh.wav", str(ctx.exception))
        self.assertIn("whoosh.mp3", str(ctx.exception))

    def test_invalid_names(self):
        for name in ("../x", "/etc/passwd", "a/b", "a\\b", "", ".."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                assets.resolve(self.project, "sfx", name)

    @needs_symlink
    def test_symlink_outside_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="gb-out-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "boom.wav").write_text("x", encoding="utf-8")
        (self.project / "assets/sfx").mkdir(parents=True)
        (self.project / "assets/sfx/boom.wav").symlink_to(outside / "boom.wav")
        with self.assertRaises(ValueError) as ctx:
            assets.resolve(self.project, "sfx", "boom")
        self.assertIn("fora", str(ctx.exception))

    def test_listing_does_not_create_folders(self):
        self.assertEqual(assets.listing(self.project), [])
        self.assertFalse((self.project / "assets").exists())
        self._put(self.project, "assets/marca", "logo.svg")
        rows = assets.listing(self.project, "marca")
        self.assertEqual([r["name"] for r in rows], ["logo"])


class FrozenAssetFoldersTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-assets-folders-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_asset_folders_are_frozen_in_portuguese(self):
        self.assertEqual(
            ("marca", "lettering", "sfx", "musica", "imagem", "composicao", "outros"), layout.ASSET_FOLDERS
        )

    def test_asset_folders_are_the_kind_folders_plus_outros(self):
        tails = {
            k.folder.removeprefix("assets/") for k in assets.ASSET_KINDS.values() if k.folder.startswith("assets/")
        }
        self.assertEqual(tails | {"outros"}, set(layout.ASSET_FOLDERS))
        self.assertNotIn("outros", {k.folder.removeprefix("assets/") for k in assets.ASSET_KINDS.values()})

    def test_imagem_kind_is_licensed_and_personal(self):
        spec = assets.ASSET_KINDS["imagem"]
        self.assertEqual("assets/imagem", spec.folder)
        self.assertEqual(assets.IMAGE, spec.extensions)
        self.assertTrue(spec.licensed)
        self.assertTrue(spec.personal)

    def test_where_finds_an_image_with_the_licence_warning(self):
        path = self.project / "assets" / "imagem" / "foto.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        found = run_cli("assets", "--action", "where", "--kind", "imagem", "--name", "foto", project=self.project)
        self.assertEqual("found", found["status"])
        self.assertEqual("project", found["origin"])
        self.assertTrue(found["path"].endswith("foto.png"))
        self.assertIn("foto.png: licença não registrada (foto.licenca.json)", found["warnings"])


class ClientRouteTests(unittest.TestCase):
    """Projeto de layout 1 com cliente: projeto, depois a pasta do cliente, depois a biblioteca pessoal."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-assets-client-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.personal = GB_HOME / "assets"
        self.addCleanup(shutil.rmtree, self.personal, ignore_errors=True)
        self.addCleanup(self._unregister)
        (self.tmp / "Clientes").mkdir()
        self.project = self.tmp / "video"
        self.project.mkdir()
        assets.reset_client_warnings()

    def _unregister(self):
        for row in clients.load_registry()["clients"]:
            clients.remove(row["slug"])

    def _register(self, slug="acme"):
        clients.add(slug, str(self.tmp / "Clientes"))
        return self.tmp / "Clientes" / slug

    def _project_json(self, client="acme"):
        layout.write_project(self.project, layout.new_project_doc(client=client))

    @staticmethod
    def _put(folder, name, data=b"x"):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(data)
        return path

    def test_project_shadows_client_which_shadows_personal(self):
        root = self._register()
        self._project_json()
        self._put(self.personal / "sfx", "whoosh.wav")
        self.assertEqual("personal", assets.resolve(self.project, "sfx", "whoosh")["origin"])
        client_file = self._put(root / "assets" / "sfx", "whoosh.wav")
        found = assets.resolve(self.project, "sfx", "whoosh")
        self.assertEqual(("client", str(client_file.resolve())), (found["origin"], found["path"]))
        self._put(self.project / "assets" / "sfx", "whoosh.wav")
        self.assertEqual("project", assets.resolve(self.project, "sfx", "whoosh")["origin"])

    def test_a_project_with_a_client_on_the_old_shape_warns_once(self):
        root = self._register()
        self._project_json()
        (root / "components" / "sfx").mkdir(parents=True)
        renamed.reset_old_shape_warnings()
        self.addCleanup(renamed.reset_old_shape_warnings)
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        for _ in range(2):
            self.assertEqual("pending", assets.resolve(self.project, "sfx", "whoosh")["status"])
        messages = [row["message"] for row in event["warnings"] if row["code"] == "OLD_SHAPE"]
        self.assertEqual(1, len(messages))
        self.assertIn("Mova cada pasta de `components/<tipo>/` para `assets/<tipo>/`", messages[0])
        self.assertNotIn("migrate", messages[0])

    def test_client_folder_names_follow_the_asset_folders(self):
        root = self._register()
        self._project_json()
        self._put(root / "assets" / "composicao", "abertura.html")
        found = assets.resolve(self.project, "composicao", "abertura")
        self.assertEqual("client", found["origin"])

    def test_client_licence_sidecar_is_honoured(self):
        root = self._register()
        self._project_json()
        self._put(root / "assets" / "musica", "tema.mp3")
        licence = {"origem": "banco X", "licenca": "CC0", "credito": "Fulano"}
        self._put(root / "assets" / "musica", "tema.licenca.json", json.dumps(licence).encode())
        found = assets.resolve(self.project, "musica", "tema")
        self.assertEqual(("client", "CC0", []), (found["origin"], found["license"]["licenca"], found["warnings"]))

    def test_aroll_never_resolves_from_the_client(self):
        root = self._register()
        self._project_json()
        self._put(root / "assets" / "aroll", "c01.mp4")
        self._put(root / "aroll", "c01.mp4")
        self.assertEqual("pending", assets.resolve(self.project, "aroll", "c01")["status"])
        self.assertEqual(
            ["project"], [origin for origin, _ in assets.component_roots(self.project, assets.ASSET_KINDS["aroll"])]
        )

    def test_unregistered_client_warns_once_and_falls_through_to_personal(self):
        self._project_json("ghost")
        self._put(self.personal / "sfx", "whoosh.wav")
        with patch.object(runtime, "record_warning") as warn:
            self.assertEqual("personal", assets.resolve(self.project, "sfx", "whoosh")["origin"])
            assets.resolve(self.project, "sfx", "whoosh")
        self.assertEqual(1, warn.call_count)
        self.assertEqual("CLIENT_UNREGISTERED", warn.call_args.args[0])
        self.assertIn("ghost", warn.call_args.args[1])

    @needs_symlink
    def test_linked_client_component_folder_is_skipped(self):
        root = self._register()
        self._project_json()
        outside = self.tmp / "fora"
        self._put(outside, "whoosh.wav")
        shutil.rmtree(root / "assets" / "sfx")
        (root / "assets" / "sfx").symlink_to(outside, target_is_directory=True)
        self.assertEqual("pending", assets.resolve(self.project, "sfx", "whoosh")["status"])

    def test_layout_zero_project_is_unaffected(self):
        root = self._register()
        self._put(root / "assets" / "sfx", "whoosh.wav")
        self.assertEqual("pending", assets.resolve(self.project, "sfx", "whoosh")["status"])
        self.assertEqual(
            ["project", "personal"], [o for o, _ in assets.component_roots(self.project, assets.ASSET_KINDS["sfx"])]
        )

    def test_listing_shows_client_rows(self):
        root = self._register()
        self._project_json()
        self._put(root / "assets" / "marca", "logo.svg")
        rows = assets.listing(self.project, "marca")
        self.assertEqual([("logo", "client")], [(r["name"], r["origin"]) for r in rows])

    def test_export_scrubs_the_client_root(self):
        root = self._register()
        self._project_json()

        class _Registry:
            @staticmethod
            def resolvers_for(_kind):
                return []

        machine = export._machine_paths(self.project, _Registry(), {})  # pylint: disable=protected-access
        self.assertEqual(("a pasta do cliente", "<cliente>"), machine[str(root)])
        text = export._scrub(f"arquivo em {root}/assets/sfx/whoosh.wav", machine)  # pylint: disable=protected-access
        self.assertNotIn(str(root), text)
        self.assertIn("<cliente>", text)

    def test_export_plan_names_the_client_origin(self):
        root = self._register()
        self._project_json()
        path = self._put(root / "assets" / "sfx", "whoosh.wav")
        collector = export_plan._Collector(self.project, None)  # pylint: disable=protected-access
        where = collector._where("client", path.parent, path)  # pylint: disable=protected-access
        self.assertEqual("cliente acme: sfx/whoosh.wav", where)
