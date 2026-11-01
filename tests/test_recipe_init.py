"""`init --recipe`: projeto novo a partir de uma versão de receita do cliente, com o `recipe.lock.json`."""

import argparse
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _listing import visible_names
from _paths import ROOT
from _schemas import close
from _symlink import needs_symlink
from test_recipe_store import OWNER, RecipeCase, _schema, reseal

from getbrolls import assets, cli, export, layout, open_enums, recipe_init, recipes, renamed, roteiro, runtime
from getbrolls.sdk import jsonschema

REF = "cat:getbrolls/recipe/cliente-exemplo.reels-split@1"


class RecipeInitCase(RecipeCase):
    def setUp(self):
        super().setUp()
        recipes.seal_project(self.project, "reels-split", title="Reels split")
        self.new = self.tmp / "video-02"

    def init(self, *extra, expect=0, tema: str | None = "Novo: vídeo #2", ref="reels-split@1"):
        args = ["init", "--recipe", ref, "--client", OWNER, *extra]
        if tema is not None:
            args += ["--tema", tema]
        return run_cli(*args, project=self.new, expect=expect)

    def lock(self):
        return json.loads((self.new / recipe_init.LOCK_FILE).read_text(encoding="utf-8"))


class InitBySlugRefusalTests(RecipeInitCase):
    def init_slug(self, *, expect=1):
        return run_cli(
            "init", "--recipe", "reels-split@1", "--client", OWNER, "--slug", "s2", "--tema", "x", expect=expect
        )

    @needs_symlink
    def test_a_linked_project_folder_is_refused_live_and_dangling(self):
        elsewhere = self.tmp / "fora"
        elsewhere.mkdir()
        (self.client / "projetos").mkdir(exist_ok=True)
        link = self.client / "projetos" / "s2"
        for target in (elsewhere, self.tmp / "nao-existe"):
            link.symlink_to(target, target_is_directory=True)
            refused = self.init_slug()
            self.assertEqual(
                "<cliente>/projetos/s2 é um link; o getbrolls não cria projeto através de links.", refused["error"]
            )
            self.assertEqual([], list(elsewhere.iterdir()))
            self.assertFalse((self.tmp / "nao-existe").exists())
            link.unlink()

    def test_projetos_that_is_a_file_is_refused_by_name(self):
        (self.client / "projetos").rmdir()
        (self.client / "projetos").write_text("x", encoding="utf-8")
        refused = self.init_slug()
        self.assertEqual("<cliente>/projetos existe e não é uma pasta.", refused["error"])

    @unittest.skipUnless(os.name != "nt" and os.geteuid() != 0, "permissões POSIX, sem root")
    def test_a_read_only_projetos_or_client_folder_is_a_labelled_io_error(self):
        projetos = self.client / "projetos"
        projetos.mkdir(exist_ok=True)
        for locked in (projetos, self.client):
            if locked is self.client:
                projetos.rmdir()
            locked.chmod(0o500)
            self.addCleanup(lambda path=locked: path.exists() and path.chmod(0o700))
            refused = self.init_slug()
            self.assertEqual("IO_ERROR", refused["error_code"])
            self.assertNotIn(str(self.tmp), refused["error"])
            self.assertNotIn("Errno", refused["error"])
            self.assertIn("<cliente>/projetos/s2", refused["error"])
            locked.chmod(0o700)


class InitTests(RecipeInitCase):
    @needs_symlink
    def test_a_dangling_project_json_link_counts_as_existing_and_the_typed_path_is_echoed(self):
        self.new.mkdir()
        (self.new / "project.json").symlink_to(self.tmp / "nao-existe")
        recipe = self.init(expect=1)
        plain = run_cli("init", project=self.new, expect=1)
        self.assertEqual("INVALID_DATA", recipe["error_code"])
        self.assertEqual(recipe["error"], plain["error"])
        self.assertEqual(f"project.json já existe em {self.new}; este projeto já foi criado.", recipe["error"])
        self.assertFalse((self.tmp / "nao-existe").exists())

    def test_tree_roteiro_and_lock_are_valid(self):
        out = self.init()
        self.assertEqual(REF, out["recipe"])
        doc = layout.load_project(self.new)
        assert doc is not None
        self.assertEqual((OWNER, REF), (doc["client"], doc["recipe"]))
        self.assertNotIn("template", doc)
        self.assertEqual(({"width": 1080, "height": 1920}, {"num": 30, "den": 1}), (doc["canvas"], doc["fps"]))
        for folder in ("aroll", "assets/sfx", "assets/composicao", "assets/outros", "broll", "analysis"):
            self.assertTrue((self.new / folder).is_dir(), folder)
        parsed = roteiro.parse((self.new / "ROTEIRO.md").read_text(encoding="utf-8"), plugins=frozenset())
        self.assertEqual(
            ("reels", "Novo: vídeo #2", OWNER, "draft", True),
            tuple(parsed.meta[key] for key in ("genero", "tema", "cliente", "status", "legenda")),
        )
        self.assertEqual(["Gancho", "Marca"], [scene.title for scene in parsed.scenes])
        self.assertEqual("{fala da cena}", parsed.scenes[0].speech)
        lock = self.lock()
        self.assertEqual([], jsonschema.errors(lock, _schema("recipe_lock")))
        self.assertEqual([], jsonschema.errors(lock, close(_schema("recipe_lock"))))
        self.assertEqual((REF, None), (lock["recipe"], lock["style"]))
        self.assertEqual(recipes.show(REF)["recipe_sha256"], lock["recipe_sha256"])
        self.assertIn('"video_format": "reels"', (self.new / "RULES.md").read_text(encoding="utf-8"))
        self.assertEqual("roteiro-check", out["summary"]["do"]["step"])
        self.assertIn("roteiro --action check", out["summary"]["do"]["command"])
        check = run_cli("roteiro", "--action", "check", project=self.new)
        self.assertEqual(2, len(check["scenes"]))

    def test_the_three_ref_forms_reach_the_same_version(self):
        for index, ref in enumerate(("reels-split", "cliente-exemplo.reels-split@1", REF)):
            with self.subTest(ref=ref):
                out = run_cli(
                    "init", "--recipe", ref, "--client", OWNER, "--tema", "x", project=self.tmp / f"forma-{index}"
                )
                self.assertEqual(REF, out["recipe"])

    def test_client_owned_component_is_not_copied_and_resolves_from_the_client(self):
        self.init()
        rows = {row["name"]: row for row in self.lock()["items"]}
        self.assertEqual(("client", "assets/marca/logo.svg"), (rows["logo"]["tier"], rows["logo"]["path"]))
        self.assertEqual([], list((self.new / "assets" / "marca").iterdir()))
        self.assertEqual("client", assets.resolve(self.new, "marca", "logo")["origin"])

    def test_copied_sfx_has_no_sidecar_and_warns(self):
        out = self.init()
        rows = {row["name"]: row for row in self.lock()["items"]}
        self.assertEqual(("project", "assets/sfx/whoosh.wav"), (rows["whoosh"]["tier"], rows["whoosh"]["path"]))
        self.assertEqual("assets/lettering/titulo-grande.json", rows["titulo-grande"]["path"])
        self.assertEqual(b"RIFF-whoosh", (self.new / "assets" / "sfx" / "whoosh.wav").read_bytes())
        self.assertFalse((self.new / "assets" / "sfx" / "whoosh.licenca.json").exists())
        licence = [w["message"] for w in out["warnings"] if w["code"] == "LICENCE_NOT_TRANSFERRED"]
        self.assertEqual(1, len(licence), out["warnings"])
        self.assertIn("whoosh", licence[0])

    def test_the_project_copy_is_writable_and_carries_no_system_clutter(self):
        # A sealed version is 0444/0555 and may hold macOS clutter; the project gets only the listed
        # files, copied one by one, so it never inherits either.
        sfx = self.slug_dir() / "1" / "assets" / "sfx"
        sfx.chmod(0o700)
        (sfx / ".DS_Store").write_bytes(b"lixo")
        (sfx / "._whoosh.wav").write_bytes(b"lixo")
        out = self.init()
        self.assertEqual(REF, out["recipe"])
        found = sorted(self.new.rglob("*"))
        self.assertEqual([], [p for p in found if p.name == ".DS_Store" or p.name.startswith("._")])
        self.assertEqual(b"RIFF-whoosh", (self.new / "assets" / "sfx" / "whoosh.wav").read_bytes())
        for path in (self.new, *found):
            with self.subTest(path=path.relative_to(self.new).as_posix()):
                self.assertTrue(os.access(path, os.W_OK))
                if os.name != "nt":
                    self.assertEqual(0o200, path.stat().st_mode & 0o200)
        (self.new / "assets" / "sfx" / "whoosh.wav").write_bytes(b"editado")
        (self.new / "assets" / "sfx" / "novo.wav").write_bytes(b"novo")

    def test_no_manifest_candidates_clips_or_approvals(self):
        self.init()
        brolls = self.new / "brolls"
        for name in ("manifest.json", "candidates", "clips", "previews", "events.jsonl", "roteiro-reviews.jsonl"):
            self.assertFalse((brolls / name).exists(), name)
        if brolls.exists():
            self.assertLessEqual(set(visible_names(brolls)), {".command.lock", "getbrolls.log", "diagnostics.jsonl"})
        self.assertNotIn("revisado", (self.new / "ROTEIRO.md").read_text(encoding="utf-8"))

    def test_flags_override_the_recipe_canvas_and_fps(self):
        self.init("--canvas", "1920x1080", "--fps", "30000/1001")
        doc = layout.load_project(self.new)
        assert doc is not None
        self.assertEqual(({"width": 1920, "height": 1080}, {"num": 30000, "den": 1001}), (doc["canvas"], doc["fps"]))

    def test_tampered_recipe_is_refused_and_nothing_is_created(self):
        self.writable(self.slug_dir() / "1" / "assets" / "sfx" / "whoosh.wav").write_bytes(b"trocado")
        refused = self.init(expect=1)
        self.assertIn("assets/sfx/whoosh.wav", refused["error"])
        self.assertFalse((self.new / "project.json").exists())
        self.assertFalse((self.new / "ROTEIRO.md").exists())

    def test_edited_recipe_json_is_refused_and_nothing_is_created(self):
        path = self.writable(self.slug_dir() / "1" / recipes.RECIPE_FILE)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["scenes"][0]["title"] = "Cena trocada"
        path.write_text(json.dumps(doc), encoding="utf-8")
        refused = self.init(expect=1)
        self.assertIn(recipes.SEAL_FILE, refused["error"])
        self.assertFalse(self.new.exists())

    def _entrypoint(self, err):
        argv = ["getbrolls", "init", "--recipe", REF, "--client", OWNER, "--tema", "Novo", "--project", str(self.new)]
        with (
            patch.object(cli.sys, "argv", argv),
            patch.object(recipes, "copy_hashed", side_effect=OSError("leitura falhou")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            return cli.entrypoint()

    def test_io_error_mid_copy_logs_to_the_home_fallback_and_leaves_no_folder(self):
        err = io.StringIO()
        self.assertEqual(1, self._entrypoint(err), err.getvalue())
        self.assertFalse(self.new.exists(), "a pasta que o init criou tem que sumir, log incluído")
        events = [
            json.loads(line) for line in (self.home / "diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(["init"], [e["operation"] for e in events])
        self.assertEqual("IO_ERROR", events[0]["error_code"])
        self.assertEqual(str(self.home / "diagnostics.jsonl"), json.loads(err.getvalue())["log"])

    def test_io_error_in_a_pre_existing_folder_leaves_it_and_its_content(self):
        self.new.mkdir()
        (self.new / "notas.txt").write_text("meu", encoding="utf-8")
        self.assertEqual(1, self._entrypoint(io.StringIO()))
        self.assertEqual(["notas.txt"], sorted(p.name for p in self.new.iterdir() if p.name != "brolls"))
        self.assertFalse((self.new / "project.json").exists())

    def test_recipe_needs_client_and_tema(self):
        refused = run_cli("init", "--recipe", REF, "--tema", "x", project=self.new, expect=2)
        self.assertIn("--client", refused["error"])
        refused = self.init(expect=2, tema=None)
        self.assertIn("--tema", refused["error"])
        refused = run_cli("init", "--tema", "x", project=self.new, expect=2)
        self.assertIn("--recipe", refused["error"])
        for bad in ("cliente-exemplo.%2E%2E@1", "x@0", "cat:outro/recipe/a.b@1", "a.b.c"):
            with self.subTest(bad=bad):
                run_cli("init", "--recipe", bad, "--client", OWNER, "--tema", "x", project=self.new, expect=2)
        for name in ("project.json", "ROTEIRO.md", recipe_init.LOCK_FILE, "assets"):
            self.assertFalse((self.new / name).exists(), name)

    def test_bad_tema_is_refused_before_anything_is_created(self):
        refused = self.init(expect=1, tema="   ")
        self.assertIn("tema", refused["error"])
        self.assertFalse((self.new / "project.json").exists())

    def test_existing_project_is_refused(self):
        self.init()
        before = (self.new / "project.json").read_bytes()
        self.assertIn("project.json", self.init(expect=1)["error"])
        self.assertEqual(before, (self.new / "project.json").read_bytes())
        other = self.tmp / "com-roteiro"
        other.mkdir()
        (other / "ROTEIRO.md").write_text("meu", encoding="utf-8")
        refused = run_cli("init", "--recipe", REF, "--client", OWNER, "--tema", "x", project=other, expect=1)
        self.assertIn("ROTEIRO.md", refused["error"])
        self.assertEqual("meu", (other / "ROTEIRO.md").read_text(encoding="utf-8"))
        self.assertFalse((other / "project.json").exists())

    def test_unknown_version_or_other_client_is_refused(self):
        self.assertIn("não existe", self.init(expect=1, ref="reels-split@4")["error"])
        refused = run_cli(
            "init", "--recipe", "reels-split@1", "--client", "ghost", "--tema", "x", project=self.new, expect=1
        )
        self.assertIn("ghost", refused["error"])
        self.assertIn("é do cliente", self.init(expect=1, ref="outro-cliente.reels-split@1")["error"])


class RenamedTests(RecipeInitCase):
    def test_init_template_is_refused_with_the_recipe_flag(self):
        refused = run_cli(
            "init", "--template", "cat:getbrolls/template/reels-acme@1", "--client", OWNER, "--tema", "x",
            project=self.new, expect=2,
        )  # fmt: skip
        self.assertEqual("USAGE_ERROR", refused["error_code"])
        self.assertIn("`init --template` saiu: agora é `init --recipe RECEITA`", refused["error"])
        self.assertIn("recipe --action list", refused["error"])
        self.assertNotIn("migrate", refused["error"])
        self.assertFalse(self.new.exists())

    def test_old_freeze_line_with_every_old_flag_is_refused_with_the_new_path(self):
        refused = run_cli(
            "template", "--action", "freeze", "--from", self.project, "--slug", "reels-split", "--client", OWNER,
            "--title", "T", "--engine-ref", "cat:hyperframes/recipe/x@1", "--by", "Bruno", expect=2,
        )  # fmt: skip
        self.assertEqual("USAGE_ERROR", refused["error_code"])
        self.assertIn("não tem substituto nesta versão", refused["error"])
        self.assertIn("recipe --action list", refused["error"])
        self.assertNotIn("capture", refused["error"])
        self.assertNotIn("recipe --action freeze", refused["error"])
        self.assertEqual(["1"], sorted(p.name for p in self.slug_dir().iterdir()))

    def test_template_list_and_show_point_to_recipe(self):
        for action, new in (("list", "recipe --action list"), ("show", "recipe --action show --ref")):
            with self.subTest(action=action):
                refused = run_cli("template", "--action", action, "--ref", "x", "--client", "y", expect=2)
                self.assertIn(new, refused["error"])

    @unittest.skipUnless(os.name != "nt" and os.geteuid() != 0, "permissões POSIX, sem root")
    def test_a_locked_version_is_an_io_error_for_show_and_for_init(self):
        folder = self.slug_dir() / "1"
        folder.chmod(0)
        self.addCleanup(folder.chmod, 0o700)
        shown = run_cli("recipe", "--action", "show", "--ref", "cliente-exemplo.reels-split@1", expect=1)
        self.assertEqual("IO_ERROR", shown["error_code"])
        refused = self.init(expect=1)
        self.assertEqual("IO_ERROR", refused["error_code"])
        self.assertFalse(self.new.exists())

    def test_recipe_list_and_show_from_the_cli(self):
        self.assertEqual([REF], [row["ref"] for row in run_cli("recipe", "--action", "list")["recipes"]])
        shown = run_cli("recipe", "--action", "show", "--ref", "cliente-exemplo.reels-split")
        self.assertEqual((REF, True), (shown["ref"], shown["intact"]))
        path = shown["path"].replace("\\", "/")  # one of the fields the contract leaves absolute
        self.assertTrue(path.endswith("/recipes/reels-split/1"), path)
        refused = run_cli("recipe", "--action", "show", "--ref", "reels-split", expect=2)
        self.assertIn("Diga de quem é a receita", refused["error"])
        self.assertEqual("USAGE_ERROR", run_cli("recipe", "--action", "show", expect=2)["error_code"])
        described = run_cli("capabilities")
        command = next(row for row in described["commands"] if row["name"] == "recipe")
        self.assertEqual(("by_action", ["list", "show"]), (command["read_only"], command["read_only_actions"]))

    def test_old_project_json_template_key_names_the_manual_fix(self):
        run_cli("init", project=self.new)
        path = self.new / "project.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        del doc["recipe"]
        doc["template"] = "cat:getbrolls/template/cliente-exemplo.reels@1"
        path.write_text(json.dumps(doc), encoding="utf-8")
        found = layout.info(self.new)
        self.assertEqual(0, found.version)
        assert found.problem is not None
        self.assertIn('chave "template"', found.problem)
        self.assertIn('troque "template" por "recipe"', found.problem)
        self.assertNotIn("migrate", found.problem)

    def test_a_project_json_with_both_keys_is_not_read_as_a_recipe_project(self):
        run_cli("init", project=self.new)
        path = self.new / "project.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["template"] = "cat:getbrolls/template/cliente-exemplo.reels@1"
        path.write_text(json.dumps(doc), encoding="utf-8")
        found = layout.info(self.new)
        self.assertEqual(0, found.version)
        assert found.problem is not None
        self.assertIn('chave "template"', found.problem)
        self.assertIn('Apague a chave "template"', found.problem)
        self.assertNotIn("migrate", found.problem)
        with self.assertRaises(ValueError):
            layout.load_project(self.new)

    def test_old_template_lock_warns(self):
        self.init()
        (self.new / recipe_init.OLD_LOCK_FILE).write_text("{}", encoding="utf-8")
        status = run_cli("status", project=self.new)
        old = [w["message"] for w in status["warnings"] if w["code"] == "OLD_SHAPE"]
        self.assertEqual(1, len(old), status["warnings"])
        self.assertIn("template.lock.json do projeto", old[0])
        self.assertIn("Apague o template.lock.json", old[0])
        self.assertIn("init --recipe", old[0])
        self.assertNotIn("migrate", old[0])

    def test_old_templates_folder_of_the_client_warns(self):
        renamed.reset_old_shape_warnings()
        (self.client / "templates" / "reels").mkdir(parents=True)
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        recipes.list_recipes()
        old = [w["message"] for w in event["warnings"] if w["code"] == "OLD_SHAPE"]
        self.assertEqual(1, len(old), event["warnings"])
        self.assertIn("templates/ do cliente cliente-exemplo", old[0])
        self.assertIn("recipe --action list", old[0])
        self.assertNotIn("migrate", old[0])
        self.assertNotIn("capture", old[0])


class RegistryCheckTests(RecipeInitCase):
    def test_plain_init_with_an_unregistered_client_is_refused(self):
        refused = run_cli("init", "--client", "ghost", project=self.new, expect=1)
        self.assertIn("ghost", refused["error"])
        self.assertFalse((self.new / "project.json").exists())
        run_cli("init", "--client", OWNER, project=self.new)


class LockDriftTests(RecipeInitCase):
    def test_changed_component_warns_on_status_and_by_function(self):
        self.init()
        self.assertEqual([], recipe_init.lock_warnings(self.new))
        (self.new / "assets" / "sfx" / "whoosh.wav").write_bytes(b"editado")
        (self.client / "assets" / "marca" / "logo.svg").write_bytes(b"<svg>novo</svg>")
        warnings = recipe_init.lock_warnings(self.new)
        self.assertEqual(2, len(warnings), warnings)
        self.assertTrue(any("whoosh" in w for w in warnings))
        self.assertTrue(any("logo" in w for w in warnings))
        status = run_cli("status", project=self.new)
        drift = [w for w in status["warnings"] if w["code"] == "RECIPE_LOCK_DRIFT"]
        self.assertEqual(2, len(drift), status["warnings"])

    def test_missing_component_and_broken_lock_warn(self):
        self.init()
        (self.new / "assets" / "sfx" / "whoosh.wav").unlink()
        self.assertTrue(any("whoosh" in w for w in recipe_init.lock_warnings(self.new)))
        (self.new / recipe_init.LOCK_FILE).write_text("{", encoding="utf-8")
        self.assertIn(recipe_init.LOCK_FILE, " ".join(recipe_init.lock_warnings(self.new)))

    def test_a_deleted_copy_is_reported_as_gone_even_if_another_one_resolves(self):
        self.init()
        self.put(self.client / "assets" / "sfx", "whoosh.wav", b"outro whoosh do cliente")
        (self.new / "assets" / "sfx" / "whoosh.wav").unlink()
        warnings = [w for w in recipe_init.lock_warnings(self.new) if "whoosh" in w]
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn("sumiu", warnings[0])
        self.assertNotIn("mudou", warnings[0])

    def test_unchanged_components_are_not_hashed_again(self):
        self.init()
        rows = {row["name"]: row for row in self.lock()["items"]}
        self.assertEqual((self.new / "assets" / "sfx" / "whoosh.wav").stat().st_size, rows["whoosh"]["size"])
        with patch.object(recipes, "sha256_file", side_effect=AssertionError("hash completo")):
            self.assertEqual([], recipe_init.lock_warnings(self.new))
        path = self.new / "assets" / "sfx" / "whoosh.wav"
        seen = path.stat()
        os.utime(path, ns=(seen.st_atime_ns, seen.st_mtime_ns + 1_000_000_000))
        self.assertEqual([], recipe_init.lock_warnings(self.new))

    def test_project_without_lock_has_no_warning(self):
        run_cli("init", project=self.new)
        self.assertEqual([], recipe_init.lock_warnings(self.new))

    def test_an_unknown_tier_in_the_lock_warns_enum_unknown(self):
        self.init()
        path = self.new / recipe_init.LOCK_FILE
        lock = json.loads(path.read_text(encoding="utf-8"))
        for item in lock["items"]:
            item["tier"] = "nivel_futuro"  # a value no real tier will ever take; the schema pattern has no hyphen
        path.write_text(json.dumps(lock), encoding="utf-8")
        open_enums.reset()
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        self.assertEqual([], recipe_init.lock_warnings(self.new))
        self.assertEqual(1, [row["code"] for row in event["warnings"]].count("ENUM_UNKNOWN"))


class ExportWarningTests(unittest.TestCase):
    def test_export_keeps_its_warnings_and_adds_drift_and_recorded_ones(self):
        def fake_run(_args, _name, _project):
            runtime.record_warning("CLIENT_UNREGISTERED", "cliente ghost fora do registro")
            return {"warnings": ["aviso do export"]}

        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        args = argparse.Namespace(to="demo", project=str(ROOT))
        with (
            patch.object(export, "_run", side_effect=fake_run),
            patch.object(recipe_init, "lock_warnings", return_value=["sfx mudou"]),
        ):
            out = export.run(args)
        self.assertEqual(["sfx mudou", "aviso do export", "cliente ghost fora do registro"], out["warnings"])
        self.assertEqual(
            [("RECIPE_LOCK_DRIFT", "sfx mudou"), ("CLIENT_UNREGISTERED", "cliente ghost fora do registro")],
            [(w["code"], w["message"]) for w in event["warnings"]],
        )
        self.assertTrue(event.get("warnings_in_result"))


class HostileRecipeTests(RecipeInitCase):
    """Um getbrolls-recipe.json íntegro no schema, mas com texto que tentaria injetar linhas no roteiro."""

    def rewrite(self, change):
        path = self.writable(self.slug_dir() / "1" / recipes.RECIPE_FILE)
        if not hasattr(self, "original"):
            self.original = path.read_text(encoding="utf-8")  # pylint: disable=attribute-defined-outside-init
        doc = json.loads(self.original)
        change(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")
        reseal(path.parent)

    def test_injected_lines_are_refused(self):
        changes = (
            lambda doc: doc["scenes"][0].update(title="Gancho\n[BROLL: x]"),
            lambda doc: doc["scenes"][0]["layers"].append("[SFX: extra]\n## Cena escondida"),
            lambda doc: doc.update(genero="reels\ntema: outro"),
        )
        for change in changes:
            with self.subTest(change=change):
                self.rewrite(change)
                refused = self.init(expect=1)
                self.assertIn("receita", refused["error"])
                self.assertFalse((self.new / "ROTEIRO.md").exists())


if __name__ == "__main__":
    unittest.main()
