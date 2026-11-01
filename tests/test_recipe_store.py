"""Receitas: `recipes.seal_project` grava uma versão imutável e final, sem fala nem licença; `show` confere tudo."""

import hashlib
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT
from _schemas import close
from _symlink import needs_symlink

from getbrolls import __version__, clients, layout, open_enums, recipes, renamed, runtime
from getbrolls.errors import UsageError
from getbrolls.sdk import jsonschema

OWNER = "cliente-exemplo"
REF = "cliente-exemplo.reels-split@1"
ROTEIRO = """---
type: roteiro
genero: reels
tema: Lançamento
cliente: cliente-exemplo
---
## Gancho
[A-ROLL]
[LETTERING: "Promoção relâmpago" | titulo-grande]
[SFX: whoosh]
Fala secreta do gancho com o código 48213.

## Marca
[FULL: logo]
[MUSICA: tema]
Outra fala secreta do fim.
"""


def tree_hash(folder):
    """sha256 de todos os caminhos e bytes de `folder`, em ordem."""
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        digest.update(path.relative_to(folder).as_posix().encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _schema(name):
    return json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


def reseal(folder):
    """Regrava o selo com o sha256 do json atual (simula quem adultera os dois)."""
    seal = folder / recipes.SEAL_FILE
    seal.chmod(stat.S_IWUSR | stat.S_IRUSR)
    digest = hashlib.sha256((folder / recipes.RECIPE_FILE).read_bytes()).hexdigest()
    seal.write_text(f"{digest}  {recipes.RECIPE_FILE}\n", encoding="utf-8")


def rewrite_components(folder, change):
    """Edita o components.json e acerta o ponteiro e o selo (adulteração coerente de ponta a ponta)."""
    path = folder / recipes.COMPONENTS_FILE
    path.chmod(stat.S_IWUSR | stat.S_IRUSR)
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    text = json.dumps(doc)
    path.write_text(text, encoding="utf-8")
    recipe = folder / recipes.RECIPE_FILE
    recipe.chmod(stat.S_IWUSR | stat.S_IRUSR)
    data = json.loads(recipe.read_text(encoding="utf-8"))
    data["components"]["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    recipe.write_text(json.dumps(data), encoding="utf-8")
    reseal(folder)


def thaw(root):
    """Libera a escrita de `root` e de tudo abaixo dele: uma versão selada tem pastas somente leitura (0o555)."""
    if root.is_symlink() or not root.exists():
        return
    if root.is_dir():
        root.chmod(stat.S_IRWXU)
        for child in root.iterdir():
            thaw(child)
    else:
        root.chmod(stat.S_IWUSR | stat.S_IRUSR)


class RecipeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-recipe-")).resolve()
        self.addCleanup(self._cleanup)
        self.home = self.tmp / "gb-home"
        env = patch.dict(os.environ, {"GB_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        renamed.reset_old_shape_warnings()
        open_enums.reset()
        (self.tmp / "Clientes").mkdir()
        clients.add(OWNER, str(self.tmp / "Clientes"), name="Cliente Exemplo")
        self.client = self.tmp / "Clientes" / OWNER
        self.project = self.tmp / "video"
        self.project.mkdir()
        layout.write_project(
            self.project,
            layout.new_project_doc(client=OWNER, canvas={"width": 1080, "height": 1920}, fps={"num": 30, "den": 1}),
        )
        (self.project / "ROTEIRO.md").write_text(ROTEIRO, encoding="utf-8")
        self.put(self.project / "assets" / "sfx", "whoosh.wav", b"RIFF-whoosh")
        self.put(self.project / "assets" / "sfx", "whoosh.licenca.json", b'{"origem":"a","licenca":"b","credito":"c"}')
        self.put(self.project / "assets" / "lettering", "titulo-grande.json", b'{"style": 1}')
        self.put(self.client / "assets" / "marca", "logo.svg", b"<svg/>")
        self.put(self.project / "aroll", "c01.mp4", b"video")
        self.put(self.project / "brolls", "manifest.json", b'{"schema_version": 1, "items": []}')

    def _cleanup(self):
        thaw(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def put(folder, name, data):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        return folder / name

    @staticmethod
    def writable(path):
        """`path` gravável, e a pasta dele também (as pastas de uma versão selada são somente leitura)."""
        if path.parent.exists():
            path.parent.chmod(stat.S_IRWXU)
        path.chmod(stat.S_IWUSR | stat.S_IRUSR)
        return path

    def slug_dir(self, slug="reels-split"):
        return self.client / "recipes" / slug

    def seal(self, slug="reels-split", **options):
        return recipes.seal_project(self.project, slug, **options)

    def sealed_with_warnings(self, slug="reels-split", **options):
        """`seal_project` dentro de um evento de comando: (resultado, avisos gravados)."""
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        try:
            return recipes.seal_project(self.project, slug, **options), event["warnings"]
        finally:
            runtime.ACTIVE.reset(token)


class SealTests(RecipeCase):
    def test_two_seals_make_two_versions_and_the_first_never_changes(self):
        first = self.seal(title="Reels split", by="Bruno")
        self.assertEqual("cat:getbrolls/recipe/cliente-exemplo.reels-split@1", first["ref"])
        before = tree_hash(self.slug_dir() / "1")
        (self.project / "assets" / "sfx" / "whoosh.wav").write_bytes(b"RIFF-other")
        second = self.seal()
        self.assertEqual(("cat:getbrolls/recipe/cliente-exemplo.reels-split@2", 2), (second["ref"], second["version"]))
        self.assertEqual(before, tree_hash(self.slug_dir() / "1"))
        self.assertEqual(["1", "2"], sorted(p.name for p in self.slug_dir().iterdir()))
        self.assertEqual([".lock", "reels-split"], sorted(p.name for p in (self.client / "recipes").iterdir()))

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_files_are_read_only_after_the_rename(self):
        self.seal()
        files = [p for p in (self.slug_dir() / "1").rglob("*") if p.is_file()]
        self.assertTrue(files)
        for path in files:
            self.assertEqual(0, path.stat().st_mode & 0o222, path)

    def test_the_version_folder_has_the_final_shape(self):
        self.seal()
        folder = self.slug_dir() / "1"
        found = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
        self.assertEqual(
            ["assets/lettering/titulo-grande.json", "assets/marca/logo.svg", "assets/sfx/whoosh.wav",
             "components.json", "getbrolls-recipe.json", "getbrolls-recipe.sha256"],
            found,
        )  # fmt: skip
        doc = json.loads((folder / recipes.RECIPE_FILE).read_text(encoding="utf-8"))
        self.assertEqual([rel for rel in found if rel not in (recipes.RECIPE_FILE, recipes.SEAL_FILE)], doc["files"])

    def test_recipe_json_is_valid_and_carries_structure_only(self):
        self.seal(title="Reels split", by="Bruno")
        raw = (self.slug_dir() / "1" / recipes.RECIPE_FILE).read_text(encoding="utf-8")
        doc = json.loads(raw)
        self.assertEqual([], jsonschema.errors(doc, _schema("recipe")))
        self.assertEqual([], jsonschema.errors(doc, close(_schema("recipe"))))
        self.assertEqual(
            ("getbrolls.recipe/1", OWNER, "reels-split", 1), (doc["schema"], doc["owner"], doc["slug"], doc["version"])
        )
        self.assertEqual("not_transferred", doc["licence"])
        for retired in ("born_in", "natives"):
            self.assertNotIn(retired, doc)
        self.assertEqual(dict.fromkeys(recipes.STEPS), doc["steps"])
        self.assertEqual((None, None, None, []), (doc["rules"], doc["identity"], doc["style"], doc["tracks"]))
        self.assertEqual(({"width": 1080, "height": 1920}, {"num": 30, "den": 1}), (doc["canvas"], doc["fps"]))
        for secret in ("Fala secreta", "48213", "Promoção relâmpago", "Outra fala"):
            self.assertNotIn(secret, raw)
        gancho, marca = doc["scenes"]
        self.assertEqual(
            ("s01", "Gancho", "[A-ROLL]", None, None),
            tuple(gancho[key] for key in ("id", "title", "layout", "role", "layout_id")),
        )
        self.assertNotIn("base_scene", gancho)
        self.assertEqual(['[LETTERING: "{texto}" | titulo-grande]', "[SFX: whoosh]"], gancho["layers"])
        self.assertEqual(("[FULL: logo]", ["[MUSICA: tema]"]), (marca["layout"], marca["layers"]))
        provenance = doc["provenance"]
        self.assertEqual({"kind": "project", "foreign": False}, provenance["from"])
        self.assertEqual(layout.project_id(self.project), provenance["project_id"])
        self.assertEqual(
            hashlib.sha256((self.project / "ROTEIRO.md").read_bytes()).hexdigest(), provenance["roteiro_sha256"]
        )
        self.assertEqual((__version__, "Bruno"), (provenance["getbrolls_version"], provenance["by"]))

    def test_components_json_lists_hashes_without_licences(self):
        self.seal()
        folder = self.slug_dir() / "1"
        doc = json.loads((folder / recipes.COMPONENTS_FILE).read_text(encoding="utf-8"))
        self.assertEqual([], jsonschema.errors(doc, close(_schema("components"))))
        self.assertEqual(("getbrolls.components/1", "recipe", OWNER), (doc["schema"], doc["scope"], doc["owner"]))
        rows = {row["file"]: row for row in doc["items"]}
        self.assertEqual(
            {"assets/sfx/whoosh.wav", "assets/lettering/titulo-grande.json", "assets/marca/logo.svg"}, set(rows)
        )
        for rel, row in rows.items():
            self.assertEqual(hashlib.sha256((folder / rel).read_bytes()).hexdigest(), row["sha256"])
            self.assertEqual("not_transferred", row["licence"])
        names = {p.name for p in folder.rglob("*")}
        for banned in ("whoosh.licenca.json", "brolls", "broll", "aroll", "manifest.json", "c01.mp4", "ROTEIRO.md"):
            self.assertNotIn(banned, names)
        recipe = json.loads((folder / recipes.RECIPE_FILE).read_text(encoding="utf-8"))
        raw = (folder / recipes.COMPONENTS_FILE).read_bytes()
        self.assertEqual({"file": "components.json", "sha256": hashlib.sha256(raw).hexdigest()}, recipe["components"])

    def test_a_component_that_is_not_found_is_a_recipe_freeze_warning(self):
        result, warnings = self.sealed_with_warnings()
        self.assertFalse(result["licences_transferred"])
        messages = [row["message"] for row in warnings if row["code"] == "RECIPE_FREEZE"]
        self.assertIn('musica "tema" não foi achado: a receita não leva esse componente.', messages)

    def test_interrupted_seal_leaves_no_staging_and_no_version(self):
        with patch.object(recipes.os, "rename", side_effect=OSError("disco cheio")), self.assertRaises(OSError):
            self.seal()
        self.assertEqual([], [p.name for p in self.slug_dir().iterdir()])

    def test_taken_number_moves_to_the_next(self):
        self.slug_dir().mkdir(parents=True)
        (self.slug_dir() / "1").mkdir()
        (self.slug_dir() / "1" / "x").write_text("x", encoding="utf-8")
        self.assertEqual(2, recipes.next_version(OWNER, "reels-split"))
        self.assertEqual(2, self.seal()["version"])
        self.assertEqual("x", (self.slug_dir() / "1" / "x").read_text(encoding="utf-8"))

    def test_bad_slug_is_refused(self):
        for bad in ("Reels", "../x", "a/b", "a%2Fb", "a.b", ""):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "^Referência inválida"):
                self.seal(slug=bad)
        recipes_dir = self.client / "recipes"
        self.assertFalse(recipes_dir.exists() and any(recipes_dir.iterdir()))

    def test_unregistered_or_mismatched_client_is_refused(self):
        with self.assertRaisesRegex(ValueError, "ghost"):
            self.seal(client="ghost")
        clients.remove(OWNER)
        with self.assertRaisesRegex(ValueError, "não está registrado"):
            self.seal()
        self.assertFalse(self.slug_dir().exists())

    def test_client_from_the_argument_when_the_project_has_none(self):
        (self.project / "project.json").unlink()
        with self.assertRaisesRegex(ValueError, "--client"):
            self.seal()
        self.assertEqual(1, self.seal(client=OWNER)["version"])

    def test_roteiro_with_problems_is_refused(self):
        (self.project / "ROTEIRO.md").write_text(ROTEIRO.replace("[A-ROLL]\n", ""), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "layout"):
            self.seal()
        self.assertFalse(self.slug_dir().exists())

    def test_ambiguous_component_is_refused(self):
        self.put(self.project / "assets" / "sfx", "whoosh.mp3", b"mp3")
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            self.seal()
        self.assertFalse(self.slug_dir().exists())

    @needs_symlink
    def test_linked_recipes_folder_is_refused(self):
        outside = self.tmp / "fora"
        outside.mkdir()
        shutil.rmtree(self.client / "recipes", ignore_errors=True)
        (self.client / "recipes").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "link"):
            self.seal()
        self.assertEqual([], list(outside.iterdir()))

    @needs_symlink
    def test_linked_recipes_lock_is_never_followed(self):
        (self.client / "recipes").mkdir(exist_ok=True)
        target = self.tmp / "fora.lock"
        (self.client / "recipes" / ".lock").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "link"):
            self.seal()
        self.assertFalse(os.path.lexists(target))

    def test_the_summary_hint_creates_the_project_by_client_and_slug(self):
        line = self.seal()["summary"]["line"]
        self.assertIn(f"init --client {OWNER} --recipe reels-split@1 --slug SLUG_DO_PROJETO --tema TEMA", line)


class ShowAndListTests(RecipeCase):
    def rewrite_recipe(self, change, *, reseal_it=True):
        """Edita o getbrolls-recipe.json; com `reseal_it`, regrava também o selo (adulteração coerente)."""
        path = self.writable(self.slug_dir() / "1" / recipes.RECIPE_FILE)
        doc = json.loads(path.read_text(encoding="utf-8"))
        change(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")
        if reseal_it:
            reseal(self.slug_dir() / "1")

    def test_list_is_sorted_by_owner_slug_and_version(self):
        (self.tmp / "Outros").mkdir()
        clients.add("beta", str(self.tmp / "Outros"))
        self.seal(slug="zeta")
        self.seal()
        self.seal()
        self.seal(slug="alfa")
        with self.assertRaises(ValueError):
            self.seal(client="beta")  # o project.json é do cliente-exemplo
        (self.project / "project.json").unlink()
        self.seal(client="beta")
        listed = recipes.list_recipes()["recipes"]
        self.assertEqual(
            [("beta", "reels-split", 1), (OWNER, "alfa", 1), (OWNER, "reels-split", 1), (OWNER, "reels-split", 2),
             (OWNER, "zeta", 1)],
            [(row["owner"], row["slug"], row["version"]) for row in listed],
        )  # fmt: skip
        self.assertEqual("cat:getbrolls/recipe/beta.reels-split@1", listed[0]["ref"])

    def test_list_ignores_staging_and_the_lock(self):
        self.seal()
        (self.slug_dir() / ".staging-deadbeef").mkdir()
        self.assertEqual([1], [row["version"] for row in recipes.list_recipes()["recipes"]])

    def test_fresh_recipe_is_intact_and_the_latest_version_is_the_default(self):
        self.seal()
        self.seal()
        shown = recipes.show("cliente-exemplo.reels-split")
        self.assertEqual(
            ("cat:getbrolls/recipe/cliente-exemplo.reels-split@2", True, []),
            (shown["ref"], shown["intact"], shown["problems"]),
        )
        raw = (self.slug_dir() / "2" / recipes.RECIPE_FILE).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), shown["recipe_sha256"])
        self.assertEqual(3, len(shown["components"]))

    def test_a_recipe_sealed_with_the_dev_keys_still_shows_intact(self):
        # A versão de testes da 2.6 selava born_in, natives, base_scene e provenance.from.environment.
        # Saíram do schema; objeto aberto ignora chave desconhecida, então a versão continua íntegra.
        self.seal()

        def dev_shape(doc):
            doc["born_in"] = "getbrolls"
            doc["natives"] = {}
            for scene in doc["scenes"]:
                scene["base_scene"] = None
            doc["provenance"]["from"] = {"kind": "capture", "environment": "getbrolls", "foreign": False}

        self.rewrite_recipe(dev_shape)
        shown = recipes.show(REF)
        self.assertEqual((True, []), (shown["intact"], shown["problems"]))
        self.assertEqual("getbrolls", shown["recipe"]["born_in"])

    def test_recipe_json_is_sealed_outside_itself_and_read_only(self):
        self.seal()
        folder = self.slug_dir() / "1"
        raw = (folder / recipes.RECIPE_FILE).read_bytes()
        self.assertEqual(
            f"{hashlib.sha256(raw).hexdigest()}  getbrolls-recipe.json\n",
            (folder / recipes.SEAL_FILE).read_text(encoding="utf-8"),
        )
        if os.name != "nt":
            self.assertEqual(0o444, stat.S_IMODE((folder / recipes.SEAL_FILE).stat().st_mode))

    def test_edited_recipe_json_is_not_intact(self):
        self.seal()
        self.rewrite_recipe(lambda doc: doc.update(title="Trocado depois"), reseal_it=False)
        shown = recipes.show(REF)
        self.assertFalse(shown["intact"])
        self.assertIn(recipes.SEAL_FILE, " ".join(shown["problems"]))

    def test_missing_or_broken_seal_is_not_intact(self):
        self.seal()
        seal = self.writable(self.slug_dir() / "1" / recipes.SEAL_FILE)
        for text in ("não é hash\n", "0" * 63 + "  getbrolls-recipe.json\n"):
            with self.subTest(text=text):
                seal.write_text(text, encoding="utf-8")
                self.assertFalse(recipes.show(REF)["intact"])
        seal.unlink()
        shown = recipes.show(REF)
        self.assertFalse(shown["intact"])
        self.assertIn(recipes.SEAL_FILE, " ".join(shown["problems"]))

    def test_components_cannot_smuggle_licences_hidden_files_or_cross_folders(self):
        self.seal()
        folder = self.slug_dir() / "1"
        for bad, kind in (
            ("assets/sfx/whoosh.licenca.json", "sfx"),
            ("assets/sfx/WHOOSH.LICENCA.JSON", "sfx"),
            ("assets/sfx/.whoosh.wav", "sfx"),
            ("assets/musica/whoosh.wav", "sfx"),
            ("assets/aroll/c01.mp4", "aroll"),
        ):
            with self.subTest(bad=bad):
                rewrite_components(folder, lambda doc, bad=bad, kind=kind: doc["items"][0].update(file=bad, kind=kind))
                shown = recipes.show(REF)
                self.assertFalse(shown["intact"])
                self.assertTrue(any("inseguro" in p or "schema" in p for p in shown["problems"]), shown["problems"])
        with self.assertRaises(ValueError):
            recipes.safe_relative("assets/musica/x.wav", kind="sfx")
        self.assertEqual(("assets", "sfx", "x.wav"), recipes.safe_relative("assets/sfx/x.wav", kind="sfx"))
        self.assertEqual(("assets", "transicao", "x.json"), recipes.safe_relative("assets/transicao/x.json"))

    def test_tampered_component_is_named(self):
        self.seal()
        self.writable(self.slug_dir() / "1" / "assets" / "sfx" / "whoosh.wav").write_bytes(b"trocado")
        shown = recipes.show(REF)
        self.assertFalse(shown["intact"])
        self.assertIn("assets/sfx/whoosh.wav", " ".join(shown["problems"]))

    def test_edited_components_json_is_named(self):
        self.seal()
        path = self.writable(self.slug_dir() / "1" / recipes.COMPONENTS_FILE)
        path.write_text(path.read_text(encoding="utf-8").replace(f'"{OWNER}"', f'"{OWNER}" '), encoding="utf-8")
        self.assertIn("components.json mudou", " ".join(recipes.show(REF)["problems"]))

    def test_missing_and_extra_files_are_problems(self):
        self.seal()
        folder = self.slug_dir() / "1"
        self.writable(folder / "assets" / "marca" / "logo.svg").unlink()
        thaw(folder / "assets" / "sfx")
        (folder / "assets" / "sfx" / "extra.wav").write_bytes(b"x")
        problems = " ".join(recipes.show(REF)["problems"])
        self.assertIn("assets/marca/logo.svg", problems)
        self.assertIn("assets/sfx/extra.wav é um arquivo a mais", problems)

    def test_traversal_in_a_component_file_is_refused_before_reading(self):
        self.seal()
        folder = self.slug_dir() / "1"
        for bad in ("assets/../../client.json", "/etc/passwd", "assets/sfx/../../../client.json",
                    "assets\\sfx\\whoosh.wav", "C:/x", "assets/outros/x.wav", "assets/sfx"):  # fmt: skip
            with self.subTest(bad=bad):
                rewrite_components(folder, lambda doc, bad=bad: doc["items"][1].update(file=bad))
                shown = recipes.show(REF)
                self.assertFalse(shown["intact"])
                self.assertTrue(any("inseguro" in p or "schema" in p for p in shown["problems"]), shown["problems"])

    def test_recipe_json_that_disagrees_with_its_folder_is_not_intact(self):
        self.seal()
        self.rewrite_recipe(lambda doc: doc.update(version=7, ref="cat:getbrolls/recipe/cliente-exemplo.reels-split@7"))
        shown = recipes.show(REF)
        self.assertFalse(shown["intact"])
        self.assertIn("versão", " ".join(shown["problems"]))

    def test_broken_recipe_json_is_not_intact(self):
        self.seal()
        self.writable(self.slug_dir() / "1" / recipes.RECIPE_FILE).write_text("{", encoding="utf-8")
        shown = recipes.show(REF)
        self.assertEqual((False, None), (shown["intact"], shown["recipe"]))

    @needs_symlink
    def test_linked_component_is_a_problem(self):
        self.seal()
        target = self.writable(self.slug_dir() / "1" / "assets" / "sfx" / "whoosh.wav")
        target.unlink()
        target.symlink_to(self.project / "assets" / "sfx" / "whoosh.wav")
        shown = recipes.show(REF)
        self.assertFalse(shown["intact"])
        self.assertIn("link", " ".join(shown["problems"]))

    def test_unknown_or_bad_ref_is_a_clear_error(self):
        self.seal()
        with self.assertRaisesRegex(ValueError, "não existe"):
            recipes.show("cliente-exemplo.reels-split@9")
        with self.assertRaisesRegex(ValueError, "não tem a receita"):
            recipes.show("cliente-exemplo.outra")
        for bad in (
            "cliente-exemplo.%2E%2E@1",
            "a/b",
            "cliente-exemplo.x@01",
            "cat:hyperframes/recipe/x.y@1",
            "scene:c01",
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "^Referência inválida"):
                recipes.show(bad)
        with self.assertRaisesRegex(ValueError, "ghost"):
            recipes.show("ghost.reels-split@1")

    def test_owner_comes_from_the_ref_or_the_caller(self):
        self.seal()
        self.assertTrue(recipes.show("reels-split", owner=OWNER)["intact"])
        with self.assertRaises(UsageError):
            recipes.show("reels-split")
        with self.assertRaisesRegex(ValueError, "é do cliente"):
            recipes.show("cliente-exemplo.reels-split", owner="outro")
        with self.assertRaisesRegex(ValueError, "pasta de um cliente"):
            recipes.show("pessoal.reels-split")


if __name__ == "__main__":
    unittest.main()
