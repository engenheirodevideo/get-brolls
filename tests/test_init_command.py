"""`init`: cria um projeto de layout 1 (`project.json`, `aroll/`, `assets/*`, `broll/`, `analysis/`)."""

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _listing import visible_names
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

from getbrolls import clients, layout
from getbrolls.sdk import schemas
from getbrolls.sdk.jsonschema import errors

FOLDERS = [
    "aroll",
    "assets/marca",
    "assets/lettering",
    "assets/sfx",
    "assets/musica",
    "assets/imagem",
    "assets/composicao",
    "assets/outros",
    "broll",
    "analysis",
]


class InitCommandTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-init-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.project = self.base / "video-01"

    @needs_symlink
    def test_a_linked_project_path_is_refused_live_and_dangling_with_the_typed_path(self):
        elsewhere = self.base / "fora"
        elsewhere.mkdir()
        link = self.base / "ligado"
        for live, target in ((True, elsewhere), (False, self.base / "nao-existe")):
            link.unlink(missing_ok=True)
            link.symlink_to(target, target_is_directory=True)
            with self.subTest(live=live):
                refused = run_cli("init", project=link, expect=1)
                self.assertEqual(f"{link} é um link; o getbrolls não cria projeto através de links.", refused["error"])
                self.assertEqual([], list(elsewhere.iterdir()))
                self.assertFalse((self.base / "nao-existe").exists())

    def test_fresh_directory_gets_the_tree_and_a_valid_project_json(self):
        result = run_cli("init", project=self.project)
        self.assertEqual(1, result["layout"])
        self.assertEqual(str(self.project), result["project"])
        self.assertEqual(FOLDERS, result["folders"])
        self.assertIn("init", result["summary"]["line"].lower())
        for folder in FOLDERS:
            self.assertTrue((self.project / folder).is_dir(), folder)
        doc = json.loads((self.project / "project.json").read_text(encoding="utf-8"))
        self.assertEqual([], errors(doc, schemas.load("project")))
        self.assertEqual(result["id"], doc["id"])
        self.assertEqual(str(uuid.UUID(doc["id"])), doc["id"])
        self.assertEqual((1, "project.json"), (layout.info(self.project).version, layout.info(self.project).source))
        for key in ("client", "recipe", "canvas", "fps"):
            self.assertIsNone(doc[key])

    def test_no_manifest_candidates_or_clips_are_created(self):
        run_cli("init", project=self.project)
        brolls = self.project / "brolls"
        for name in ("manifest.json", "candidates", "clips", "previews", "events.jsonl"):
            self.assertFalse((brolls / name).exists(), name)
        # A trava e o log do comando são esperados; nada mais sob brolls/.
        if brolls.exists():
            allowed = {".command.lock", "getbrolls.log", "diagnostics.jsonl"}
            self.assertLessEqual(set(visible_names(brolls)), allowed)

    def test_client_canvas_and_fps_are_recorded(self):
        (self.base / "Clientes").mkdir()
        clients.add("acme", str(self.base / "Clientes"))
        self.addCleanup(clients.remove, "acme")
        run_cli("init", "--client", "acme", "--canvas", "1080x1920", "--fps", "30000/1001", project=self.project)
        doc = layout.load_project(self.project)
        assert doc is not None
        self.assertEqual("acme", doc["client"])
        self.assertEqual({"width": 1080, "height": 1920}, doc["canvas"])
        self.assertEqual({"num": 30000, "den": 1001}, doc["fps"])

    def test_second_init_fails_with_invalid_data(self):
        run_cli("init", project=self.project)
        before = (self.project / "project.json").read_bytes()
        refused = run_cli("init", project=self.project, expect=1)
        self.assertEqual("INVALID_DATA", refused["error_code"])
        self.assertIn("project.json", refused["error"])
        self.assertEqual(before, (self.project / "project.json").read_bytes())

    def test_existing_manifest_is_refused_with_the_migrate_hint(self):
        (self.project / "brolls").mkdir(parents=True)
        (self.project / "brolls" / "manifest.json").write_text('{"schema_version": 1, "items": []}', encoding="utf-8")
        refused = run_cli("init", project=self.project, expect=1)
        self.assertEqual("INVALID_DATA", refused["error_code"])
        self.assertIn("migrate", refused["error"])
        self.assertFalse((self.project / "project.json").exists())
        self.assertFalse((self.project / "broll").exists())

    def test_bad_fps_canvas_or_client_is_a_usage_error(self):
        for flag, value in (
            ("--fps", "0"),
            ("--fps", "30/0"),
            ("--fps", "abc"),
            ("--canvas", "0x1920"),
            ("--canvas", "1080"),
            ("--client", "ACME"),
        ):
            with self.subTest(flag=flag, value=value):
                refused = run_cli("init", flag, value, project=self.project, expect=2)
                self.assertEqual("USAGE_ERROR", refused["error_code"])
                self.assertFalse((self.project / "project.json").exists())

    def test_refused_init_leaves_no_folder_behind(self):
        """Cliente desconhecido, flag de uso errada: nada nasce, nem a pasta nem brolls/."""
        run_cli("init", "--client", "ghost", project=self.project, expect=1)
        run_cli("init", "--tema", "x", project=self.project, expect=2)
        self.assertFalse(self.project.exists())

    def test_refused_init_in_an_existing_folder_does_not_create_brolls(self):
        self.project.mkdir()
        (self.project / "notas.txt").write_text("meu", encoding="utf-8")
        run_cli("init", "--client", "ghost", project=self.project, expect=1)
        self.assertEqual(["notas.txt"], sorted(p.name for p in self.project.iterdir()))

    def test_linked_broll_or_analysis_is_refused_before_anything_is_created(self):
        outside = self.base / "fora"
        outside.mkdir()
        for name in ("broll", "analysis"):
            with self.subTest(name=name):
                shutil.rmtree(self.project, ignore_errors=True)
                self.project.mkdir()
                try:
                    (self.project / name).symlink_to(outside, target_is_directory=True)
                except OSError:
                    self.skipTest("este sistema não cria symlink")
                refused = run_cli("init", project=self.project, expect=1)
                self.assertIn("link", refused["error"])
                self.assertEqual([name], sorted(p.name for p in self.project.iterdir()))
                self.assertEqual([], list(outside.iterdir()))

    def test_project_path_that_is_a_file_is_refused(self):
        self.project.write_text("x", encoding="utf-8")
        run_cli("init", project=self.project, expect=1)


class InitBySlugTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-init-slug-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        env = patch.dict(os.environ, {"GB_HOME": str(self.base / "gb-home")})
        env.start()
        self.addCleanup(env.stop)
        (self.base / "Clientes").mkdir()
        clients.add("cliente-exemplo", str(self.base / "Clientes"))
        self.client = self.base / "Clientes" / "cliente-exemplo"

    def test_client_and_slug_create_the_project_in_projetos(self):
        out = run_cli("init", "--client", "cliente-exemplo", "--slug", "video-01")
        target = self.client / "projetos" / "video-01"
        self.assertEqual(str(target), out["project"])
        doc = layout.load_project(target)
        assert doc is not None
        self.assertEqual("cliente-exemplo", doc["client"])
        for folder in FOLDERS:
            self.assertTrue((target / folder).is_dir(), folder)

    def test_project_or_the_pair_is_required_and_never_both(self):
        for args in (("init",), ("init", "--slug", "video-01"), ("init", "--client", "cliente-exemplo")):
            with self.subTest(args=args):
                refused = run_cli(*args, expect=2)
                self.assertIn("--project (ou --client e --slug)", refused["error"])
                self.assertEqual("--project", refused["suggestion"])
        refused = run_cli(
            "init", "--client", "cliente-exemplo", "--slug", "video-01", project=self.base / "p", expect=2
        )
        self.assertIn("--slug", refused["error"])
        self.assertFalse((self.client / "projetos" / "video-01").exists())
        self.assertFalse((self.base / "p").exists())

    def test_bad_slug_or_unregistered_client(self):
        refused = run_cli("init", "--client", "cliente-exemplo", "--slug", "Video 01", expect=2)
        self.assertEqual("USAGE_ERROR", refused["error_code"])
        refused = run_cli("init", "--client", "ghost", "--slug", "video-01", expect=1)
        self.assertIn("ghost", refused["error"])

    @needs_symlink
    def test_a_linked_projetos_folder_is_refused(self):
        elsewhere = self.base / "fora"
        elsewhere.mkdir()
        (self.client / "projetos").rmdir()
        (self.client / "projetos").symlink_to(elsewhere, target_is_directory=True)
        refused = run_cli("init", "--client", "cliente-exemplo", "--slug", "video-01", expect=1)
        self.assertTrue(refused["error"].startswith("<cliente>/projetos é um link"), refused["error"])
        self.assertNotIn(str(self.base), refused["error"])
        self.assertEqual([], list(elsewhere.iterdir()))

    def test_an_existing_project_by_slug_is_named_under_the_client(self):
        run_cli("init", "--client", "cliente-exemplo", "--slug", "video-01")
        refused = run_cli("init", "--client", "cliente-exemplo", "--slug", "video-01", expect=1)
        self.assertEqual(
            "project.json já existe em <cliente>/projetos/video-01; este projeto já foi criado.", refused["error"]
        )

    def test_capabilities_names_the_alternative(self):
        rows = {row["name"]: row for row in run_cli("capabilities")["commands"]}
        self.assertEqual(
            (True, [["--client", "--slug"]]), (rows["init"]["requires_project"], rows["init"]["project_alternatives"])
        )
        self.assertEqual((True, []), (rows["status"]["requires_project"], rows["status"]["project_alternatives"]))
        init_project = next(o for o in rows["init"]["options"] if "--project" in o["flags"])
        self.assertFalse(init_project["required"])

    def _init(self, *extra, expect=1):
        return run_cli("init", "--client", "cliente-exemplo", "--slug", "s2", *extra, expect=expect)

    @needs_symlink
    def test_a_linked_project_folder_is_refused_live_and_dangling(self):
        elsewhere = self.base / "fora"
        elsewhere.mkdir()
        projetos = self.client / "projetos"
        projetos.mkdir(exist_ok=True)
        link = projetos / "s2"
        link.symlink_to(elsewhere, target_is_directory=True)
        for extra in ((),):
            with self.subTest(live=True, extra=extra):
                refused = self._init(*extra)
                self.assertEqual(
                    "<cliente>/projetos/s2 é um link; o getbrolls não cria projeto através de links.", refused["error"]
                )
                self.assertEqual([], list(elsewhere.iterdir()))
        link.unlink()
        gone = self.base / "nao-existe"
        link.symlink_to(gone, target_is_directory=True)
        for extra in ((),):
            with self.subTest(live=False, extra=extra):
                refused = self._init(*extra)
                self.assertIn("é um link", refused["error"])
                self.assertFalse(gone.exists())

    def test_projetos_that_is_a_file_is_refused_by_name(self):
        (self.client / "projetos").rmdir()
        (self.client / "projetos").write_text("x", encoding="utf-8")
        for extra in ((),):
            with self.subTest(extra=extra):
                refused = self._init(*extra)
                self.assertEqual("<cliente>/projetos existe e não é uma pasta.", refused["error"])
                self.assertEqual("x", (self.client / "projetos").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name != "nt" and os.geteuid() != 0, "permissões POSIX, sem root")
    def test_a_read_only_projetos_or_client_folder_is_a_labelled_io_error(self):
        projetos = self.client / "projetos"
        for locked in (projetos, self.client):
            if locked is self.client:
                projetos.rmdir()  # sem `projetos/`, é a pasta do cliente que tem de aceitar a criação
            locked.chmod(0o500)
            self.addCleanup(lambda path=locked: path.exists() and path.chmod(0o700))
            for extra in ((),):
                with self.subTest(locked=locked.name, extra=extra):
                    refused = self._init(*extra)
                    self.assertEqual("IO_ERROR", refused["error_code"])
                    self.assertNotIn(str(self.base), refused["error"])
                    self.assertNotIn("Errno", refused["error"])
                    self.assertIn("<cliente>/projetos/s2", refused["error"])
            locked.chmod(0o700)

    def test_an_empty_project_is_a_usage_error(self):
        for args in (("--client", "cliente-exemplo"), ()):
            with self.subTest(args=args):
                refused = run_cli("init", *args, "--project", "", expect=2)
                self.assertEqual("USAGE_ERROR", refused["error_code"])
                self.assertIn("--project", refused["error"])
                self.assertIn("vazio", refused["error"])


if __name__ == "__main__":
    unittest.main()
