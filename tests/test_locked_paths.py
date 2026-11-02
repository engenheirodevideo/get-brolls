"""Folders and files the system will not open: one labelled PT-BR `IO_ERROR`, the same on 3.11 and 3.14.

Until 3.13 `Path.exists()` raises the EACCES and from 3.14 it answers `False`, so the same locked folder
used to fail in two different places and print two different messages (one of them the raw `[Errno 13]`).
"""

from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_recipe_edges import NEEDS_EACCES, can_symlink
from test_recipe_init import RecipeInitCase
from test_recipe_store import OWNER, RecipeCase

from getbrolls import recipe_init, recipes


class LockedCase(RecipeCase):
    def lock(self, path):
        path.chmod(0)
        self.addCleanup(path.chmod, 0o700)

    def assert_labelled_io_error(self, refused):
        self.assertEqual("IO_ERROR", refused["error_code"], refused)
        self.assertNotIn("Errno", refused["error"])
        self.assertTrue(refused["error"].startswith("Não consegui"), refused["error"])
        self.assertNotIn(str(self.tmp.parent), refused["error"].replace(str(self.tmp), "<tmp>"))


@NEEDS_EACCES
class LockedFolderMessageTests(LockedCase):
    def test_a_locked_projetos_folder_is_labelled_for_init_by_slug(self):
        projetos = self.client / "projetos"
        projetos.mkdir(exist_ok=True)
        self.lock(projetos)
        refused = run_cli("init", "--client", OWNER, "--slug", "s9", expect=1)
        self.assert_labelled_io_error(refused)
        self.assertNotIn(str(self.tmp), refused["error"])
        self.assertIn("<cliente>/projetos/s9", refused["error"])

    def test_a_project_inside_a_locked_projetos_folder_is_labelled_for_status(self):
        projetos = self.client / "projetos"
        inside = projetos / "p"
        inside.mkdir(parents=True)
        self.lock(projetos)
        self.assert_labelled_io_error(run_cli("status", project=inside, expect=1))

    def test_a_locked_project_folder_is_labelled(self):
        self.lock(self.project)
        for args in (["status"], ["assets", "--action", "list"], ["roteiro", "--action", "check"]):
            with self.subTest(args=args):
                self.assert_labelled_io_error(run_cli(*args, project=self.project, expect=1))

    def test_an_overlong_project_path_is_labelled(self):
        name = "a" * 300
        for args in (["status"], ["init"], ["assets", "--action", "list"]):
            with self.subTest(args=args):
                refused = run_cli(*args, project=self.tmp / name / "p", expect=1)
                self.assert_labelled_io_error(refused)
                self.assertIn("o nome é longo demais", refused["error"])


@NEEDS_EACCES
class LockedProjectFileTests(LockedCase):
    def test_a_locked_project_json_is_an_io_error_for_every_reader_but_status(self):
        self.lock(self.project / "project.json")
        for args in (["assets", "--action", "list"], ["roteiro", "--action", "check"], ["queue", "--action", "status"]):
            with self.subTest(args=args):
                refused = run_cli(*args, project=self.project, expect=1)
                self.assert_labelled_io_error(refused)
                self.assertIn("project.json", refused["error"])

    def test_status_still_reports_the_locked_project_json_as_a_layout_problem(self):
        self.lock(self.project / "project.json")
        status = run_cli("status", project=self.project)
        self.assertEqual(0, status["layout"]["version"])
        self.assertIn("project.json", status["layout"]["problem"])
        self.assertIn("Permission denied", status["layout"]["problem"])


class ProjectThatIsAFileTests(LockedCase):
    def test_assets_and_roteiro_refuse_a_file_as_the_project(self):
        afile = self.tmp / "afile"
        afile.write_text("x", encoding="utf-8")
        for args in (["assets", "--action", "list"], ["roteiro", "--action", "check"], ["queue", "--action", "status"]):
            with self.subTest(args=args):
                refused = run_cli(*args, project=afile, expect=1)
                self.assertEqual("INVALID_DATA", refused["error_code"])
                self.assertIn("não é uma pasta", refused["error"])
                self.assertIn(str(afile), refused["error"])  # as typed


class RecipeListProblemTests(LockedCase):
    @NEEDS_EACCES
    def test_a_locked_client_root_is_a_client_row_and_not_a_failure(self):
        self.seal()
        other = self.tmp / "Clientes2"
        other.mkdir()
        run_cli("client", "--action", "add", "--slug", "beta", "--root", other)
        self.lock(self.client)
        listed = run_cli("recipe", "--action", "list")
        self.assertEqual([OWNER], [row["owner"] for row in listed["clients_with_problems"]])
        entry = listed["clients_with_problems"][0]
        self.assertEqual("io_error", entry["problem_code"])
        self.assertNotIn("Errno", entry["problem"])
        self.assertNotIn(str(self.tmp), entry["problem"])
        self.assertIn("Permission denied", entry["problem"])
        self.assertEqual([], listed["recipes"])

    def test_a_linked_slug_folder_is_listed_as_a_problem(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        self.seal()
        (self.client / "recipes" / "atalho").symlink_to(self.slug_dir(), target_is_directory=True)
        found = recipes.list_recipes()
        self.assertEqual(["reels-split"], [row["slug"] for row in found["recipes"]])
        self.assertEqual(1, len(found["clients_with_problems"]))
        entry = found["clients_with_problems"][0]
        self.assertEqual((OWNER, "link"), (entry["owner"], entry["problem_code"]))
        self.assertIn('"atalho" é um link', entry["problem"])
        self.assertNotIn(str(self.tmp), entry["problem"])

    def test_a_linked_version_folder_is_a_row_with_a_problem(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        self.seal()
        (self.slug_dir() / "2").symlink_to(self.slug_dir() / "1", target_is_directory=True)
        rows = {row["version"]: row for row in recipes.list_recipes()["recipes"]}
        self.assertEqual([1, 2], sorted(rows))
        self.assertIsNone(rows[1]["problem"])
        self.assertEqual("link", rows[2]["problem_code"])
        self.assertIn("é um link", rows[2]["problem"])
        self.assertNotIn(str(self.tmp), rows[2]["problem"])
        self.assertIsNone(rows[2]["title"])

    def test_the_list_command_shows_both_links(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        self.seal()
        (self.client / "recipes" / "atalho").symlink_to(self.slug_dir(), target_is_directory=True)
        (self.slug_dir() / "2").symlink_to(self.slug_dir() / "1", target_is_directory=True)
        listed = run_cli("recipe", "--action", "list")
        self.assertEqual({"link"}, {row["problem_code"] for row in listed["clients_with_problems"]})
        self.assertEqual(["link"], [row["problem_code"] for row in listed["recipes"] if row["problem_code"]])


@NEEDS_EACCES
class LockWarningsLockedTests(RecipeInitCase):
    def lock_path(self, path):
        path.chmod(0)
        self.addCleanup(path.chmod, 0o700)

    def test_a_locked_assets_folder_is_unreadable_and_not_gone(self):
        self.init()
        self.lock_path(self.new / "assets")
        warnings = recipe_init.lock_warnings(self.new)
        self.assertTrue(warnings, warnings)
        for warning in warnings:
            self.assertNotIn("sumiu", warning)
            self.assertIn("não pôde ser lido", warning)
            self.assertNotIn(str(self.tmp), warning)
            self.assertNotIn("Errno", warning)

    def test_a_locked_project_folder_is_an_unreadable_lock(self):
        self.init()
        self.lock_path(self.new)
        warnings = recipe_init.lock_warnings(self.new)
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn("não pôde ser lido", warnings[0])
        self.assertNotIn("Errno", warnings[0])
        self.assertNotIn(str(self.tmp), warnings[0])

    def test_a_locked_old_lock_probe_does_not_raise(self):
        self.init()
        self.lock_path(self.new)
        self.assertIsInstance(recipe_init.lock_warnings(Path(self.new)), list)
