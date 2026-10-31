"""Mensagens para a pessoa nunca levam o caminho da máquina, nem com o projeto fora da pasta pessoal.

`runtime.scrub_home` só troca o prefixo da pasta pessoal: um projeto em `/var/folders`
(o `TMPDIR` padrão do macOS) ou num disco externo sairia com o caminho inteiro. Aqui a
pasta pessoal aponta para outro lugar, então o resultado não depende do `TMPDIR` de quem roda.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import test_recipe_init as init_tests
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_recipe_store import thaw

from getbrolls import _paths, assets, recipe_init
from getbrolls.sdk import marketplace


def _can_symlink(folder):
    probe = Path(folder) / "probe-link"
    try:
        probe.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-label-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_path_under_an_anchor_gets_its_tag_and_the_relative_rest(self):
        project = self.tmp / "video"
        self.assertEqual("<projeto>/assets/sfx", _paths.label(project / "assets" / "sfx", [(project, "<projeto>")]))
        self.assertEqual("<projeto>", _paths.label(project, [(project, "<projeto>")]))

    def test_the_deepest_anchor_that_holds_the_path_wins_in_any_order_and_none_roots_are_skipped(self):
        client = self.tmp / "Clientes" / "acme"
        project = client / "videos" / "v1"
        (project / "assets").mkdir(parents=True)
        anchors = [(None, "<projeto>"), (self.tmp, "$GB_HOME"), (client, "<cliente>"), (project, "<projeto>")]
        for order in (anchors, list(reversed(anchors))):
            with self.subTest(order=[tag for _, tag in order]):
                self.assertEqual("<cliente>/templates", _paths.label(client / "templates", order))
                self.assertEqual("<projeto>/assets", _paths.label(project / "assets", order))
                self.assertEqual("$GB_HOME/outra", _paths.label(self.tmp / "outra", order))

    def test_two_anchors_on_the_same_folder_keep_the_first_tag(self):
        project = self.tmp / "video"
        project.mkdir()
        anchors = [(project, "<projeto>"), (project, "<cliente>")]
        self.assertEqual("<projeto>/x", _paths.label(project / "x", anchors))
        self.assertEqual("<cliente>/x", _paths.label(project / "x", list(reversed(anchors))))

    def test_a_sibling_that_only_shares_the_text_prefix_is_not_inside(self):
        project = self.tmp / "video"
        project.mkdir()
        (self.tmp / "video2").mkdir()
        self.assertIsNone(_paths.label(self.tmp / "video2" / "x", [(project, "<projeto>")]))
        self.assertIsNone(_paths.label(Path(f"{project}-copia"), [(project, "<projeto>")]))

    def test_dot_dot_that_leaves_every_anchor_gets_no_label(self):
        project = self.tmp / "gb" / "video"
        project.mkdir(parents=True)
        anchors = [(project, "<projeto>"), (self.tmp / "gb", "$GB_HOME")]
        self.assertIsNone(_paths.label(project / ".." / ".." / "fora", anchors))
        self.assertEqual("$GB_HOME/outro", _paths.label(project / ".." / "outro", anchors))
        self.assertEqual("<projeto>/assets", _paths.label(project / "x" / ".." / "assets", anchors))

    def test_a_link_from_outside_that_points_inside_gets_no_label(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        project = self.tmp / "video"
        (project / "assets").mkdir(parents=True)
        link = self.tmp / "fora-link"
        link.symlink_to(project / "assets", target_is_directory=True)
        self.assertIsNone(_paths.label(link, [(project, "<projeto>")]))
        self.assertIsNone(_paths.label(link / "sfx", [(project, "<projeto>")]))

    def test_nested_clients_with_mixed_spellings_pick_the_deepest_real_folder(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        outer = self.tmp / "outer"
        (outer / "inner" / "templates").mkdir(parents=True)
        link = self.tmp / "l"
        link.symlink_to(outer, target_is_directory=True)
        # The outer root spelled long (real), the inner one short (through the link): text length lies.
        anchors = [(Path(os.path.realpath(outer)), "<cliente A>"), (link / "inner", "<cliente B>")]
        for order in (anchors, list(reversed(anchors))):
            with self.subTest(order=[tag for _, tag in order]):
                self.assertEqual("<cliente B>/templates", _paths.label(outer / "inner" / "templates", order))
                self.assertEqual("<cliente B>/templates", _paths.label(link / "inner" / "templates", order))

    def test_a_case_only_difference_on_a_case_insensitive_disk_still_matches(self):
        project = self.tmp / "Video"
        (project / "assets").mkdir(parents=True)
        swapped = self.tmp / "vIDEO"
        if not swapped.exists():
            self.skipTest("disco sensível a maiúsculas")
        self.assertEqual("<projeto>/assets", _paths.label(swapped / "assets", [(project, "<projeto>")]))

    def test_outside_every_anchor_is_none(self):
        self.assertIsNone(_paths.label(self.tmp / "x", [(self.tmp / "y", "<projeto>")]))
        self.assertIsNone(_paths.label(self.tmp, []))

    def test_a_link_spelling_on_either_side_still_matches(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        real = self.tmp / "real"
        (real / "assets").mkdir(parents=True)
        link = self.tmp / "link"
        link.symlink_to(real, target_is_directory=True)
        self.assertEqual("<projeto>/assets", _paths.label(link / "assets", [(real, "<projeto>")]))
        self.assertEqual("<projeto>/assets", _paths.label(real / "assets", [(link, "<projeto>")]))


class RefusalsOutsideHomeTests(init_tests.RecipeInitCase):
    """Recusas representativas de receita e componentes, com a pasta pessoal em outro lugar."""

    def setUp(self):
        super().setUp()
        elsewhere = Path(self.tmp.anchor) / "gb-pessoa-que-nao-existe"
        env = patch.dict(os.environ, {"HOME": str(elsewhere), "USERPROFILE": str(elsewhere)})
        env.start()
        self.addCleanup(env.stop)
        self.assertFalse(self.tmp.is_relative_to(Path.home()))

    def assert_no_machine_path(self, text):
        real = os.path.realpath(self.tmp)
        spellings = {str(self.tmp), real, real.removeprefix("/private")}
        for spelling in spellings:
            self.assertNotIn(spelling, text)
        self.assertNotIn(os.path.realpath(tempfile.gettempdir()), text)

    def test_a_recipes_folder_that_is_a_link_is_named_under_the_client(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        elsewhere = self.tmp / "elsewhere"
        shutil.copytree(self.client / "recipes", elsewhere)
        thaw(self.client / "recipes")  # a sealed version has read-only files and folders
        thaw(elsewhere)
        shutil.rmtree(self.client / "recipes")
        (self.client / "recipes").symlink_to(elsewhere, target_is_directory=True)
        found = run_cli("recipe", "--action", "list")
        problem = found["clients_with_problems"][0]["problem"]
        self.assertTrue(problem.startswith("<cliente>/recipes é um link"), problem)
        self.assert_no_machine_path(json.dumps(found, ensure_ascii=False))
        error = run_cli("recipe", "--action", "show", "--ref", init_tests.REF, expect=1)
        self.assertIn("<cliente>/recipes é um link", error["error"])
        self.assert_no_machine_path(error["error"])

    def test_init_through_a_project_folder_that_is_a_link_is_named_under_the_project(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        (self.tmp / "fora").mkdir()
        self.new.mkdir()
        (self.new / "assets").symlink_to(self.tmp / "fora", target_is_directory=True)
        error = self.init(expect=1)
        self.assertIn("assets/ é um link", error["error"])
        self.assert_no_machine_path(error["error"])
        self.assertEqual([], list((self.tmp / "fora").iterdir()))

    def plan(self):
        return recipe_init.plan_instance(self.new, init_tests.REF, init_tests.OWNER, "Novo: vídeo #2")

    def test_a_folder_that_turns_into_a_link_after_the_plan_is_named_under_the_project(self):
        # `plan_instance` refuses a linked folder up front; one planted between the plan and the
        # writing (another process) is refused again by `_make_folders`, before it writes through it.
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        plan = self.plan()
        (self.tmp / "fora").mkdir()
        (self.new / "assets").mkdir(parents=True)
        (self.new / "assets" / "sfx").symlink_to(self.tmp / "fora", target_is_directory=True)
        with self.assertRaises(ValueError) as ctx:
            recipe_init.write_instance(plan)
        self.assertEqual(
            "<projeto>/assets/sfx é um link; o getbrolls não lê nem grava receitas através de links.",
            str(ctx.exception),
        )
        self.assertEqual([], list((self.tmp / "fora").iterdir()))

    def test_a_component_folder_that_turns_into_a_link_before_the_copy_is_named_under_the_project(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        plan = self.plan()
        (self.tmp / "fora").mkdir()
        original = recipe_init._make_folders  # pylint: disable=protected-access

        def then_link(project, created):
            folders = original(project, created)
            (project / "assets" / "sfx").rmdir()
            (project / "assets" / "sfx").symlink_to(self.tmp / "fora", target_is_directory=True)
            return folders

        with patch.object(recipe_init, "_make_folders", side_effect=then_link), self.assertRaises(ValueError) as ctx:
            recipe_init.write_instance(plan)
        self.assertEqual(
            "<projeto>/assets/sfx é um link; o getbrolls não lê nem grava receitas através de links.",
            str(ctx.exception),
        )
        self.assertEqual([], list((self.tmp / "fora").iterdir()))

    def test_a_component_already_in_the_new_project_is_named_under_the_project(self):
        self.put(self.new / "assets" / "sfx", "whoosh.wav", b"RIFF-outro")
        error = self.init(expect=1)
        self.assertEqual("<projeto>/assets/sfx/whoosh.wav já existe; nada foi sobrescrito.", error["error"])
        self.assert_no_machine_path(error["error"])
        self.assertEqual(b"RIFF-outro", (self.new / "assets" / "sfx" / "whoosh.wav").read_bytes())

    def test_a_component_already_in_the_project_is_named_under_it_also_outside_a_command(self):
        # In-process (no command running, so no project for `labels` to fall back on): only the
        # `project=` that `_install` passes to `copy_hashed` gives the `<projeto>` label.
        plan = self.plan()
        self.put(self.new / "assets" / "sfx", "whoosh.wav", b"RIFF-outro")
        with self.assertRaises(ValueError) as ctx:
            recipe_init.write_instance(plan)
        self.assertEqual("<projeto>/assets/sfx/whoosh.wav já existe; nada foi sobrescrito.", str(ctx.exception))

    def test_an_ambiguous_client_component_is_named_under_the_client(self):
        self.init()
        self.put(self.client / "assets" / "marca", "logo.png", b"png")
        warnings = recipe_init.lock_warnings(self.new)
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn('"logo" é ambíguo em <cliente>/assets/marca: logo.png, logo.svg.', warnings[0])
        self.assert_no_machine_path(warnings[0])

    @unittest.skipIf(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0, "chmod 000 POSIX, sem root")
    def test_a_lock_that_cannot_be_opened_is_reported_without_its_path(self):
        self.init()
        lock = self.new / recipe_init.LOCK_FILE
        lock.chmod(0)
        self.addCleanup(lock.chmod, 0o644)
        warnings = recipe_init.lock_warnings(self.new)
        self.assertEqual(1, len(warnings), warnings)
        self.assertTrue(
            warnings[0].startswith(f"{recipe_init.LOCK_FILE} não pôde ser lido (Permission denied)"), warnings
        )
        self.assert_no_machine_path(warnings[0])

    def test_an_ambiguous_project_component_is_named_under_the_project(self):
        self.put(self.project / "assets" / "sfx", "whoosh.mp3", b"ID3")
        with self.assertRaises(ValueError) as ctx:
            assets.resolve(self.project, "sfx", "whoosh")
        self.assertIn("é ambíguo em <projeto>/assets/sfx:", str(ctx.exception))
        self.assert_no_machine_path(str(ctx.exception))

    def test_a_project_only_kind_never_looks_up_the_client_and_keeps_its_own_error(self):
        self.put(self.project / "aroll", "c01.mov", b"video")
        with (
            patch.object(assets, "project_client", side_effect=OSError("clients.json sumiu")),
            self.assertRaises(ValueError) as ctx,
        ):
            assets.resolve(self.project, "aroll", "c01")
        self.assertEqual('"c01" é ambíguo em <projeto>/aroll: c01.mov, c01.mp4. Deixe só um.', str(ctx.exception))

    def test_an_ambiguous_personal_component_is_named_under_gb_home(self):
        personal = Path(os.environ["GB_HOME"]) / "assets" / "sfx"
        self.put(personal, "ding.wav", b"RIFF")
        self.put(personal, "ding.mp3", b"ID3")
        self.addCleanup(shutil.rmtree, personal, ignore_errors=True)
        with self.assertRaises(ValueError) as ctx:
            assets.resolve(self.project, "sfx", "ding")
        self.assertEqual(
            '"ding" é ambíguo em $GB_HOME/assets/sfx: ding.mp3, ding.wav. Deixe só um.', str(ctx.exception)
        )

    def test_a_component_link_that_leaves_its_folder_is_named_under_the_project(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        outside = self.put(self.tmp / "fora", "boom.wav", b"RIFF")
        (self.project / "assets" / "sfx" / "boom.wav").symlink_to(outside)
        rows = [row for row in assets.listing(self.project, "sfx") if row["name"] == "boom"]
        self.assertEqual("boom.wav aponta para fora de <projeto>/assets/sfx.", rows[0]["error"])
        with self.assertRaises(ValueError) as ctx:
            assets.resolve(self.project, "sfx", "boom")
        self.assertIn("aponta para fora de <projeto>/assets/sfx:", str(ctx.exception))
        self.assert_no_machine_path(str(ctx.exception))


class GbHomeOutsideHomeTests(unittest.TestCase):
    """Os arquivos do `$GB_HOME` saem como `$GB_HOME/…`, também com ele fora da pasta pessoal."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-gbhome-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "gb-home"
        self.home.mkdir()
        elsewhere = Path(self.tmp.anchor) / "gb-pessoa-que-nao-existe"
        env = patch.dict(os.environ, {"GB_HOME": str(self.home), "HOME": str(elsewhere), "USERPROFILE": str(elsewhere)})
        env.start()
        self.addCleanup(env.stop)

    def assert_no_machine_path(self, text):
        real = os.path.realpath(self.tmp)
        for spelling in (str(self.tmp), real, real.removeprefix("/private")):
            self.assertNotIn(spelling, text)

    def test_portable_text_names_both_spellings_of_a_linked_gb_home(self):
        # GB_HOME is a link whose own spelling is a text prefix of the real folder's: replacing
        # the shorter spelling first would leave `$GB_HOME-real/…`. Built here, so it holds on any
        # system and with any TMPDIR (not only macOS `/var` → `/private/var`).
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        real = Path(os.path.realpath(self.tmp)) / "gb-real"
        real.mkdir()
        link = Path(os.path.realpath(self.tmp)) / "gb"
        link.symlink_to(real, target_is_directory=True)
        with patch.dict(os.environ, {"GB_HOME": str(link)}):
            text = marketplace.portable_text(f"a {real}/plugins/x b {link}/plugins/y")
        self.assertEqual("a $GB_HOME/plugins/x b $GB_HOME/plugins/y", text)

    def test_portable_text_replaces_only_whole_path_components(self):
        real = Path(os.path.realpath(self.home))
        text = f"a {real}/plugins/p b {real}2/outra c {real}-copia e {real}. f '{real}' g {real}"
        shown = marketplace.portable_text(text)
        self.assertTrue(shown.startswith("a $GB_HOME/plugins/p b "), shown)
        self.assertTrue(shown.endswith(" e $GB_HOME. f '$GB_HOME' g $GB_HOME"), shown)
        # The siblings are other folders: never `$GB_HOME2` nor `$GB_HOME-copia`, and never the machine path.
        self.assertNotIn("$GB_HOME2", shown)
        self.assertNotIn("$GB_HOME-copia", shown)
        self.assertIn("gb-home2/outra", shown)
        self.assert_no_machine_path(shown)

    def test_portable_text_names_every_known_folder_and_hides_the_rest(self):
        # `capabilities` (`plugins_problems[].reason`, `plugins_error`, `marketplaces_error`), the marketplace
        # `problem` and `doctor`: the same labels as every message, not only `$GB_HOME` and `~`.
        project = self.tmp / "proj"
        project.mkdir()
        loose = Path(tempfile.gettempdir()) / "gb-solto" / "plugin.json"
        outside = "/Volumes/Externo/plugins/demo/plugin.json"
        with patch("getbrolls.logs.project_root", return_value=project):
            shown = marketplace.portable_text(f"a {project}/x b {loose} c {self.home}/plugins d {outside}")
        self.assertIn(
            "a <projeto>/x b $TMPDIR/gb-solto/plugin.json c $GB_HOME/plugins d <pasta fora do projeto>/#", shown
        )
        self.assertTrue(shown.endswith(".json"), shown)  # the folder outside runs to the end of the line
        self.assertNotIn("Externo", shown)
        self.assert_no_machine_path(shown)

    def test_portable_text_with_a_relative_gb_home(self):
        cwd = Path.cwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        with patch.dict(os.environ, {"GB_HOME": "gb-home"}):
            absolute = Path.cwd() / "gb-home"
            text = marketplace.portable_text(f"a gb-home/plugins/p b {absolute}/x c gb-homeless d gb-home")
        self.assertEqual("a $GB_HOME/plugins/p b $GB_HOME/x c gb-homeless d gb-home", text)
        with patch.dict(os.environ, {"GB_HOME": "gb-home"}):
            # Only as the start of a path: inside `outra/gb-home/x` it is another folder of that name.
            kept = "outra/gb-home/x e x-gb-home/y"
            self.assertEqual(kept, marketplace.portable_text(kept))

    def test_client_add_list_and_show_echo_the_root_as_typed(self):
        # The root is the person's own data: `add`, `list` and `show` give it back as typed at `add` (the
        # registered value). Messages and the registry file still go by the label.
        from getbrolls import clients  # pylint: disable=import-outside-toplevel

        inside = self.home / "clientes"
        inside.mkdir()
        outside = self.tmp / "disco" / "Clientes"
        outside.mkdir(parents=True)
        added = [clients.add("acme", str(inside)), clients.add("beta", str(outside))]
        typed = {"acme": str(inside / "acme"), "beta": str(outside / "beta")}
        self.assertEqual(list(typed.values()), [row["client"]["root"] for row in added])
        listed = {row["slug"]: row["root"] for row in clients.list_clients()["clients"]}
        self.assertEqual(typed, listed)
        self.assertEqual(typed["acme"], clients.show("acme")["client"]["root"])
        self.assertEqual("$GB_HOME/clients.json", clients.list_clients()["registry"])
        with self.assertRaises(ValueError) as caught:
            clients.add("acme", str(inside))
        self.assert_no_machine_path(str(caught.exception))

    def test_a_root_typed_through_a_link_is_kept_as_typed_and_registered_resolved(self):
        from getbrolls import clients  # pylint: disable=import-outside-toplevel

        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        # A link above the root (as `/var` is on macOS): the root itself is a real folder, so `add` takes it.
        real = self.tmp / "disco-real"
        (real / "Clientes").mkdir(parents=True)
        link = self.tmp / "atalho"
        link.symlink_to(real, target_is_directory=True)
        clients.add("acme", str(link / "Clientes"))
        row = json.loads((self.home / "clients.json").read_text(encoding="utf-8"))["clients"][0]
        self.assertEqual(str(Path(os.path.realpath(real)) / "Clientes" / "acme"), row["root"])
        self.assertEqual(str(link / "Clientes" / "acme"), row["root_typed"])
        self.assertEqual(str(link / "Clientes" / "acme"), clients.show("acme")["client"]["root"])

    def test_a_typed_root_whose_link_now_leads_elsewhere_is_shown_with_a_problem(self):
        from getbrolls import clients  # pylint: disable=import-outside-toplevel

        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        first, second = self.tmp / "disco-1", self.tmp / "disco-2"
        (first / "Clientes").mkdir(parents=True)
        (second / "Clientes" / "acme").mkdir(parents=True)
        link = self.tmp / "atalho"
        link.symlink_to(first, target_is_directory=True)
        clients.add("acme", str(link / "Clientes"))
        self.assertIsNone(clients.show("acme")["client"]["problem"])
        link.unlink()
        link.symlink_to(second, target_is_directory=True)
        for row in (clients.show("acme")["client"], clients.list_clients()["clients"][0]):
            with self.subTest(row=row["slug"]):
                self.assertEqual(str(link / "Clientes" / "acme"), row["root"])
                self.assertIn("agora leva a outra pasta", row["problem"] or "")
                self.assert_no_machine_path(row["problem"])

    def test_the_typed_root_is_written_only_as_an_absolute_or_tilde_path(self):
        from getbrolls import clients  # pylint: disable=import-outside-toplevel

        with self.assertRaises(ValueError):
            clients._checked_typed("relativa/acme")  # pylint: disable=protected-access
        self.assertEqual("~/Clientes/acme", clients._checked_typed("~/Clientes/acme"))  # pylint: disable=protected-access
        absolute = str(self.tmp / "Clientes" / "acme")
        self.assertEqual(absolute, clients._checked_typed(absolute))  # pylint: disable=protected-access
        # A registry whose `root_typed` is not one of them shows the resolved `root` it has.
        folder = Path(os.path.realpath(self.tmp)) / "x" / "acme"
        entry = {"slug": "acme", "root": str(folder), "root_typed": "relativa/acme", "added": "x"}
        self.assertEqual(str(folder), clients._typed_root(entry))  # pylint: disable=protected-access

    def test_a_client_registered_before_shows_the_registered_root(self):
        # An entry written by an earlier version has only `root` (resolved): that is what the person registered,
        # and it comes back as it is; nothing is rewritten.
        from getbrolls import clients  # pylint: disable=import-outside-toplevel

        folder = Path(os.path.realpath(self.tmp)) / "antigo" / "acme"
        folder.mkdir(parents=True)
        (folder / "client.json").write_text(
            json.dumps(
                {
                    "schema": "getbrolls.client/1",
                    "slug": "acme",
                    "name": "Acme",
                    "created": "2026-01-02T03:04:05+00:00",
                    "defaults": {},
                }
            ),
            encoding="utf-8",
        )
        registry = {
            "schema": "getbrolls.clients/1",
            "clients": [{"slug": "acme", "root": str(folder), "added": "2026-01-02T03:04:05+00:00"}],
        }
        before = json.dumps(registry)
        (self.home / "clients.json").write_text(before, encoding="utf-8")
        self.assertEqual(str(folder), clients.show("acme")["client"]["root"])
        self.assertEqual(str(folder), clients.list_clients()["clients"][0]["root"])
        self.assertEqual(before, (self.home / "clients.json").read_text(encoding="utf-8"))

    def relative_to_tmpdir(self, path):
        return Path(os.path.realpath(path)).relative_to(os.path.realpath(tempfile.gettempdir())).as_posix()

    def test_client_list_names_the_registry_under_gb_home(self):
        found = run_cli("client", "--action", "list")
        self.assertEqual("$GB_HOME/clients.json", found["registry"])

    def test_a_broken_client_registry_is_named_under_gb_home(self):
        (self.home / "clients.json").write_text("{", encoding="utf-8")
        error = run_cli("client", "--action", "list", expect=1)
        self.assertIn("clients.json inválido em $GB_HOME/clients.json", error["error"])
        self.assert_no_machine_path(error["error"])

    def test_gb_home_label_is_none_outside_gb_home_or_without_a_usable_one(self):
        self.assertEqual("$GB_HOME/clients.json", _paths.gb_home_label(self.home / "clients.json"))
        self.assertIsNone(_paths.gb_home_label(self.tmp / "outra"))
        with patch.object(_paths, "gb_home", side_effect=ValueError("GB_HOME inválido")):
            self.assertIsNone(_paths.gb_home_label(self.home / "clients.json"))

    def test_a_broken_plugins_json_is_named_under_gb_home(self):
        (self.home / "plugins.json").write_text("{", encoding="utf-8")
        error = run_cli("plugins", "--action", "list", expect=1)
        self.assertIn("plugins.json inválido em $GB_HOME/plugins.json", error["error"])
        self.assert_no_machine_path(error["error"])
        # The loader's own message, before any envelope labels it (`capabilities` and `doctor` show it too).
        from getbrolls.sdk import loader  # pylint: disable=import-outside-toplevel

        with self.assertRaises(ValueError) as caught:
            loader.read_state()
        self.assertIn("plugins.json inválido em $GB_HOME/plugins.json.", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
