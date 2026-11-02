"""A `"template"` key left in `project.json` by the 2.6 test versions: every reader reads it or says how to fix it."""

import json
import subprocess

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import CLI_ARGV
from test_recipe_store import OWNER, RecipeCase

from getbrolls import layout, renamed, runtime

RECIPE_REF = "cat:getbrolls/recipe/cliente-exemplo.reels-split@1"


class TemplateKeyCase(RecipeCase):
    def setUp(self):
        super().setUp()
        self.file = self.project / "project.json"
        self.doc = json.loads(self.file.read_text(encoding="utf-8"))

    def rewrite(self, *, keep_recipe=False, **changes):
        doc = {k: v for k, v in self.doc.items() if keep_recipe or k != "recipe"}
        doc.update(changes)
        self.file.write_text(json.dumps(doc), encoding="utf-8")
        renamed.reset_old_shape_warnings()

    def run_any(self, args):
        """The command's JSON whatever the exit (success or refusal): both carry the warnings."""
        done = subprocess.run(
            [*CLI_ARGV, *args, "--project", str(self.project)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertIn(done.returncode, (0, 1), done.stderr)
        return json.loads(done.stdout if done.returncode == 0 else done.stderr)

    @staticmethod
    def old_shape(result):
        return [w["message"] for w in result.get("warnings", []) if w["code"] == "OLD_SHAPE"]


def readers(project):
    """Every command that resolves the layout of `--project`, with the exit each one has on this fixture."""
    return [
        (["status"], 0),
        (["assets", "--action", "list"], 0),
        (["roteiro", "--action", "check"], 0),
        (["analysis", "--action", "list"], 0),
        (["queue", "--action", "status"], 0),
        (["verify"], 0),
        (["deliver"], 0),
        (["review"], None),
        (["export", "--to", "meu_exporter", "--dry-run"], None),
        (["migrate", "--action", "plan"], None),
    ]


class NullTemplateLeftoverTests(TemplateKeyCase):
    """What every RC `init` wrote: `"template": null`, and no `"recipe"`."""

    def setUp(self):
        super().setUp()
        self.rewrite(template=None)

    def test_the_project_reads_as_layout_one_with_its_client(self):
        found = layout.info(self.project)
        self.assertEqual((1, "project.json", None), (found.version, found.source, found.problem))
        assert found.doc is not None
        self.assertEqual(OWNER, found.doc["client"])
        self.assertNotIn("template", found.doc)
        self.assertIsNone(found.doc["recipe"])
        self.assertEqual(self.doc["id"], layout.project_id(self.project))

    def test_status_reads_layout_one_and_warns_once_to_delete_the_key(self):
        status = run_cli("status", project=self.project)
        self.assertEqual(1, status["layout"]["version"])
        self.assertIsNone(status["layout"]["problem"])
        old = self.old_shape(status)
        self.assertEqual(1, len(old), status["warnings"])
        self.assertIn('"template"', old[0])
        self.assertIn("Apague", old[0])
        self.assertNotIn("migrate", old[0])
        self.assertIn("a chave é ignorada", old[0])
        self.assertNotIn("não é lido", old[0])
        self.assertNotIn("forma antiga", old[0])

    def test_assets_list_keeps_the_client_components(self):
        listed = run_cli("assets", "--action", "list", "--project", self.project)
        self.assertIn("logo", {row["name"] for row in listed["assets"]})
        self.assertEqual(1, len(self.old_shape(listed)), listed.get("warnings"))

    def test_every_reader_warns_to_delete_the_key(self):
        for args, _ in readers(self.project):
            with self.subTest(args=args):
                renamed.reset_old_shape_warnings()
                result = self.run_any(args)
                old = self.old_shape(result)
                self.assertEqual(1, len(old), result.get("warnings"))
                self.assertIn("Apague", old[0])

    def test_migrate_has_nothing_to_convert(self):
        planned = run_cli("migrate", "--action", "plan", "--project", self.project)
        self.assertNotIn("não pôde ser usado", json.dumps(planned, ensure_ascii=False))

    def test_the_warning_comes_once_per_project_in_one_process(self):
        with_event = {"warnings": []}
        token = runtime.ACTIVE.set(with_event)
        try:
            layout.info(self.project)
            layout.info(self.project)
            layout.load_project(self.project)
        finally:
            runtime.ACTIVE.reset(token)
        self.assertEqual(1, len(with_event["warnings"]), with_event["warnings"])
        self.assertEqual("OLD_SHAPE", with_event["warnings"][0]["code"])


class NonNullTemplateTests(TemplateKeyCase):
    """A real value under `"template"`, or both keys: still a layout problem, but every reader says so."""

    def assert_actionable(self, text):
        self.assertIn('"template"', text)
        self.assertIn('"recipe"', text)
        self.assertIn("cat:getbrolls/recipe/", text)
        self.assertNotIn("migrate --action plan", text)

    def test_the_problem_text_is_a_manual_step_and_not_migrate(self):
        self.rewrite(template=RECIPE_REF)
        found = layout.info(self.project)
        self.assertEqual(0, found.version)
        assert found.problem is not None
        self.assert_actionable(found.problem)
        with self.assertRaises(ValueError) as caught:
            layout.load_project(self.project)
        self.assert_actionable(str(caught.exception))

    def test_a_null_template_next_to_a_recipe_key_is_still_a_problem(self):
        self.rewrite(template=None, keep_recipe=True)
        found = layout.info(self.project)
        self.assertEqual(0, found.version)
        assert found.problem is not None
        self.assertIn('Apague a chave "template"', found.problem)
        old = self.old_shape(run_cli("status", project=self.project))
        self.assertEqual(1, len(old), old)

    def test_a_template_value_next_to_a_null_recipe_does_not_say_the_project_has_a_recipe(self):
        self.rewrite(template=RECIPE_REF, recipe=None)
        found = layout.info(self.project)
        assert found.problem is not None
        self.assertNotIn("já tem", found.problem)
        self.assertIn('"recipe" é nulo', found.problem)
        self.assertIn("cat:getbrolls/recipe/", found.problem)

    def test_both_keys_say_to_drop_the_template(self):
        self.rewrite(template=RECIPE_REF, recipe=RECIPE_REF)
        found = layout.info(self.project)
        assert found.problem is not None
        self.assertIn('"template"', found.problem)
        self.assertIn("apague", found.problem.lower())
        self.assertIn('o projeto já tem "recipe"', found.problem)

    def test_status_shows_the_problem_and_the_warning(self):
        self.rewrite(template=RECIPE_REF)
        status = run_cli("status", project=self.project)
        self.assertEqual(0, status["layout"]["version"])
        self.assert_actionable(status["layout"]["problem"])
        self.assertEqual(1, len(self.old_shape(status)), status["warnings"])

    def test_every_reader_surfaces_it_and_none_degrades_silently(self):
        for template in (RECIPE_REF, "x"):
            self.rewrite(template=template)
            for args, _ in readers(self.project):
                with self.subTest(args=args, template=template):
                    renamed.reset_old_shape_warnings()
                    result = self.run_any(args)
                    old = self.old_shape(result)
                    self.assertEqual(1, len(old), result.get("warnings"))
                    self.assert_actionable(old[0])

    def test_init_recipe_into_the_folder_refuses_without_hiding_the_key(self):
        self.seal()
        self.rewrite(template=RECIPE_REF)
        refused = run_cli(
            "init", "--recipe", "reels-split@1", "--client", OWNER, "--tema", "T", project=self.project, expect=1
        )
        self.assertIn("project.json já existe", refused["error"])
        self.assertEqual(1, len(self.old_shape(refused)), refused.get("warnings"))

    def test_migrate_does_not_point_at_itself(self):
        self.rewrite(template=RECIPE_REF)
        refused = run_cli("migrate", "--action", "plan", "--project", self.project, expect=1)
        self.assertNotIn("migrate --action plan", refused["error"])
        self.assertIn('"recipe"', refused["error"])
