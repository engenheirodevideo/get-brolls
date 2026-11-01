"""Receitas: recusas e bordas da selagem, de `show` e de `list` e do `init --recipe` com o `recipe.lock.json`."""

# too-many-lines: as bordas da selagem, de `show`, de `list` e do `init --recipe` com o lock moram juntas, porque
# todas partem da mesma versão selada e quebram a integridade dela do mesmo jeito.
# pylint: disable=too-many-lines

import errno
import hashlib
import json
import os
import shutil
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink
from test_recipe_init import REF, RecipeInitCase
from test_recipe_store import OWNER, ROTEIRO, RecipeCase, reseal, rewrite_components, thaw, tree_hash

from getbrolls import layout, open_enums, recipe_init, recipes, runtime

POSIX = os.name != "nt"
NEEDS_EACCES = unittest.skipUnless(POSIX and os.geteuid() != 0, "permissões POSIX, sem root")


def can_symlink(folder):
    probe = Path(folder) / "probe-link"
    try:
        probe.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


def staging_dirs(slug_dir):
    return [p.name for p in slug_dir.iterdir() if p.name.startswith(".staging-")]


class SealEdgeTests(RecipeCase):
    def test_title_and_by_must_be_one_line_of_text(self):
        for kwargs in (
            {"title": "   "},
            {"title": "a\nb"},
            {"title": "x" * (recipes.TEXT_MAX + 1)},
            {"title": 42},
            {"by": "\t"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, r"--(title|by) tem que ser texto"):
                recipes.seal_project(self.project, "reels-split", **kwargs)
        self.assertFalse(self.slug_dir().exists())

    def test_next_version_of_a_slug_without_folder_is_one(self):
        self.assertEqual(1, recipes.next_version(OWNER, "reels-split"))
        self.seal()
        self.assertEqual(2, recipes.next_version(OWNER, "reels-split"))
        self.assertEqual(1, recipes.next_version(OWNER, "outro-slug"))

    def test_lettering_without_a_style_seals_no_component(self):
        (self.project / "ROTEIRO.md").write_text(
            ROTEIRO.replace('[LETTERING: "Promoção relâmpago" | titulo-grande]', '[LETTERING: "Promoção relâmpago"]'),
            encoding="utf-8",
        )
        out = self.seal()
        self.assertEqual(["logo", "whoosh"], sorted(row["name"] for row in out["components"]))

    def test_component_cited_twice_is_sealed_once(self):
        text = ROTEIRO + "\n## Outra\n[A-ROLL]\n[SFX: Whoosh]\nFala.\n"
        (self.project / "ROTEIRO.md").write_text(text, encoding="utf-8")
        out = self.seal()
        names = [row["name"] for row in out["components"]]
        self.assertEqual(1, sum(name.lower() == "whoosh" for name in names), names)
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(["whoosh.wav"], [p.name for p in (self.slug_dir() / "1" / "assets" / "sfx").iterdir()])

    def test_component_found_through_a_link_keeps_the_cited_name(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        sfx = self.project / "assets" / "sfx"
        (sfx / "whoosh.wav").rename(sfx / "original-whoosh.wav")
        (sfx / "whoosh.wav").symlink_to("original-whoosh.wav")
        out = self.seal()
        rows = {row["name"]: row for row in out["components"]}
        self.assertEqual("assets/sfx/whoosh.wav", rows["whoosh"]["file"])
        self.assertEqual(b"RIFF-whoosh", (self.slug_dir() / "1" / rows["whoosh"]["file"]).read_bytes())

    def test_number_taken_meanwhile_moves_to_the_next_one(self):
        self.seal()
        before = tree_hash(self.slug_dir() / "1")
        with patch.object(recipes, "next_version", return_value=1):
            out = self.seal()
        self.assertEqual(2, out["version"])
        self.assertEqual(before, tree_hash(self.slug_dir() / "1"))
        self.assertEqual([], staging_dirs(self.slug_dir()))

    def test_rename_lost_to_another_seal_tries_the_next_number(self):
        real = recipes.effects.rename
        calls = []

        def taken(staging, target, **kwargs):
            calls.append(Path(target).name)
            if len(calls) == 1:
                Path(target).mkdir()  # a outra selagem chegou antes
                raise OSError(errno.ENOTEMPTY, "diretório não vazio")
            return real(staging, target, **kwargs)

        with patch.object(recipes.effects, "rename", side_effect=taken):
            out = self.seal()
        self.assertEqual(["1", "2"], calls)
        self.assertEqual(2, out["version"])
        self.assertEqual([], staging_dirs(self.slug_dir()))
        self.assertTrue((self.slug_dir() / "2" / recipes.RECIPE_FILE).is_file())

    def test_rename_that_fails_for_real_is_raised_and_cleans_the_staging(self):
        with (
            patch.object(recipes.effects, "rename", side_effect=OSError(errno.EACCES, "negado")),
            self.assertRaises(OSError) as raised,
        ):
            self.seal()
        self.assertEqual(errno.EACCES, raised.exception.errno)
        self.assertEqual([], [p.name for p in self.slug_dir().iterdir()])

    def test_no_free_number_after_five_attempts_is_refused_and_cleans_the_staging(self):
        self.seal()
        for number in range(2, 6):
            (self.slug_dir() / str(number)).mkdir()
        before = tree_hash(self.slug_dir() / "1")
        with (
            patch.object(recipes, "next_version", return_value=1),
            self.assertRaisesRegex(ValueError, "Não achei um número livre .* depois de 5 tentativas"),
        ):
            self.seal()
        self.assertEqual([], staging_dirs(self.slug_dir()))
        self.assertEqual(["1", "2", "3", "4", "5"], sorted(p.name for p in self.slug_dir().iterdir()))
        self.assertEqual(before, tree_hash(self.slug_dir() / "1"))

    def test_the_fifth_attempt_still_publishes(self):
        self.seal()
        for number in range(2, 5):
            (self.slug_dir() / str(number)).mkdir()
        with patch.object(recipes, "next_version", return_value=1):
            out = self.seal()
        self.assertEqual(5, out["version"])
        self.assertTrue((self.slug_dir() / "5" / recipes.RECIPE_FILE).is_file())
        self.assertEqual([], staging_dirs(self.slug_dir()))

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_a_file_that_cannot_be_frozen_is_a_warning_not_an_error(self):
        denied = PermissionError(errno.EPERM, "Operation not permitted")
        with patch.object(Path, "chmod", side_effect=denied):
            out, warnings = self.sealed_with_warnings()
        self.assertEqual(1, out["version"])
        frozen = [
            row["message"]
            for row in warnings
            if row["code"] == "RECIPE_FREEZE" and row["message"].endswith("não consegui deixar somente leitura.")
        ]
        # six files, the version folder, `assets` and its three component folders
        self.assertEqual(11, len(frozen), warnings)
        self.assertIn("components.json: não consegui deixar somente leitura.", frozen)
        self.assertIn("assets/sfx: não consegui deixar somente leitura.", frozen)
        self.assertIn("<versão>: não consegui deixar somente leitura.", frozen)
        self.assertTrue(os.access(self.slug_dir() / "1" / recipes.RECIPE_FILE, os.W_OK))

    def test_a_numbered_folder_with_a_leading_zero_or_too_many_digits_is_not_a_version(self):
        self.seal()
        for name in ("007", "0", "1000000000", "9" * 200, "٣", "x1"):
            (self.slug_dir() / name).mkdir()
        (self.slug_dir() / "9").write_text("arquivo, não pasta", encoding="utf-8")
        self.assertEqual(2, recipes.next_version(OWNER, "reels-split"))
        self.assertEqual(1, recipes.latest_version(OWNER, "reels-split"))
        self.assertEqual(2, self.seal()["version"])
        self.assertEqual(
            [("reels-split", 1), ("reels-split", 2)],
            [(row["slug"], row["version"]) for row in recipes.list_recipes()["recipes"]],
        )
        self.assertTrue(recipes.show("reels-split", owner=OWNER)["intact"])

    def test_the_highest_allowed_version_number_still_counts(self):
        self.seal()
        (self.slug_dir() / "999999999").mkdir()
        self.assertEqual(999999999, recipes.latest_version(OWNER, "reels-split"))
        with self.assertRaisesRegex(ValueError, "Referência inválida"):
            self.seal()
        self.assertEqual([], staging_dirs(self.slug_dir()))


class FrozenFolderTests(RecipeCase):
    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_the_sealed_folders_are_read_only_too(self):
        self.seal()
        version = self.slug_dir() / "1"
        for folder in (version, version / "assets", *(version / "assets").iterdir()):
            self.assertEqual(0o555, folder.stat().st_mode & 0o777, folder)
        for path in version.rglob("*"):
            if path.is_file():
                self.assertEqual(0o444, path.stat().st_mode & 0o777, path)
        self.assertEqual(0o755, self.slug_dir().stat().st_mode & 0o777)

    @NEEDS_EACCES
    def test_a_stray_item_cannot_be_dropped_into_a_sealed_version(self):
        self.seal()
        version = self.slug_dir() / "1"
        for folder in (version, version / "assets" / "sfx"):
            with self.assertRaises(PermissionError):
                (folder / ".DS_Store").write_bytes(b"x")

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_a_frozen_version_is_still_removable_and_the_next_one_still_seals(self):
        self.seal()
        self.assertEqual(2, self.seal()["version"])
        runtime.force_rmtree(self.slug_dir() / "1")
        self.assertFalse((self.slug_dir() / "1").exists())
        self.assertTrue(recipes.show("reels-split@2", owner=OWNER)["intact"])
        self.assertEqual(3, self.seal()["version"])

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_a_failed_seal_still_removes_its_unfrozen_staging(self):
        with patch.object(recipes, "_publish", side_effect=ValueError("boom")), self.assertRaises(ValueError):
            self.seal()
        self.assertEqual([], staging_dirs(self.slug_dir()))


class ClutterTests(RecipeCase):
    def setUp(self):
        super().setUp()
        self.seal()
        self.folder = self.slug_dir() / "1"
        thaw(self.folder)
        self.ref = "cliente-exemplo.reels-split@1"

    def show(self):
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        try:
            return recipes.show(self.ref), event["warnings"]
        finally:
            runtime.ACTIVE.reset(token)

    def test_a_finder_ds_store_is_a_warning_and_never_read(self):
        for where in (self.folder, self.folder / "assets", self.folder / "assets" / "sfx"):
            (where / ".DS_Store").write_bytes(b"\x00Bud1")
        shown, recorded = self.show()
        self.assertEqual((True, []), (shown["intact"], shown["problems"]))
        self.assertEqual(3, len(shown["warnings"]), shown["warnings"])
        self.assertIn(".DS_Store", shown["warnings"][0])
        self.assertEqual(["RECIPE_CLUTTER"] * 3, [row["code"] for row in recorded])
        self.assertEqual(shown["warnings"], [row["message"] for row in recorded])

    @NEEDS_EACCES
    def test_clutter_is_never_opened_or_hashed(self):
        junk = self.folder / "assets" / "sfx" / ".DS_Store"
        junk.write_bytes(b"x")
        junk.chmod(0)
        shown, _ = self.show()
        self.assertEqual((True, 1), (shown["intact"], len(shown["warnings"])))

    def test_an_appledouble_beside_a_listed_file_is_a_warning(self):
        (self.folder / "assets" / "sfx" / "._whoosh.wav").write_bytes(b"\x00\x05\x16\x07")
        (self.folder / f"._{recipes.RECIPE_FILE}").write_bytes(b"\x00\x05")
        (self.folder / f"._{recipes.COMPONENTS_FILE}").write_bytes(b"\x00\x05")
        shown, _ = self.show()
        self.assertEqual((True, []), (shown["intact"], shown["problems"]))
        self.assertEqual(3, len(shown["warnings"]), shown["warnings"])

    def test_every_other_extra_item_is_still_a_problem(self):
        sfx = self.folder / "assets" / "sfx"
        (sfx / "._sem-dono.wav").write_bytes(b"x")  # AppleDouble of a file that is not listed
        (sfx / "._").write_bytes(b"x")
        (sfx / ".DS_Store.bak").write_bytes(b"x")
        (sfx / "x.DS_Store").write_bytes(b"x")  # ends with the clutter name but is not it
        (sfx / "foo.DS_Store").write_bytes(b"x")
        (self.folder / "assets" / "._sfx").write_bytes(b"x")  # AppleDouble of a folder, not of a listed file
        (self.folder / "pasta" / ".DS_Store").mkdir(parents=True)
        shown, _ = self.show()
        self.assertFalse(shown["intact"])
        self.assertEqual([], shown["warnings"])
        for rel in (
            "assets/sfx/._sem-dono.wav",
            "assets/sfx/._",
            "assets/sfx/.DS_Store.bak",
            "assets/sfx/x.DS_Store",
            "assets/sfx/foo.DS_Store",
            "assets/._sfx",
        ):
            self.assertIn(f"{rel} é um arquivo a mais, que o {recipes.RECIPE_FILE} não lista.", shown["problems"])
        self.assertIn(f"pasta é uma pasta a mais, que o {recipes.RECIPE_FILE} não lista.", shown["problems"])
        self.assertIn(f"pasta/.DS_Store é uma pasta a mais, que o {recipes.RECIPE_FILE} não lista.", shown["problems"])

    def test_a_listed_clutter_name_is_a_listed_file_not_clutter(self):
        for name in (".DS_Store", "._whoosh.wav"):
            with self.subTest(name=name):
                rel = f"assets/sfx/{name}"
                (self.folder / rel).write_bytes(b"x")
                recipe = self.folder / recipes.RECIPE_FILE
                recipe.chmod(0o600)
                doc = json.loads(recipe.read_text(encoding="utf-8"))
                doc["files"].append(rel)
                recipe.write_text(json.dumps(doc), encoding="utf-8")
                reseal(self.folder)
                shown, _ = self.show()
                self.assertEqual([], shown["warnings"])
                self.assertIn(
                    f"{rel} está em files mas não é o {recipes.COMPONENTS_FILE} nem um componente.", shown["problems"]
                )
                (self.folder / rel).unlink()

    def test_a_listed_file_that_is_missing_is_still_a_problem_beside_clutter(self):
        (self.folder / "assets" / "sfx" / ".DS_Store").write_bytes(b"x")
        thaw(self.folder)
        (self.folder / "assets" / "sfx" / "whoosh.wav").unlink()
        shown, _ = self.show()
        self.assertFalse(shown["intact"])
        self.assertEqual(1, len(shown["warnings"]))


class NormalizationTests(RecipeCase):
    """Nomes em NFD no disco (HFS+, SMB, zip) e NFC no selo: a mesma versão, íntegra."""

    NAME = "Pão.wav"

    def setUp(self):
        super().setUp()
        self.put(self.project / "assets" / "sfx", self.NAME, b"RIFF-pao")
        (self.project / "ROTEIRO.md").write_text(ROTEIRO.replace("[SFX: whoosh]", "[SFX: Pão]"), encoding="utf-8")
        self.seal()
        self.folder = self.slug_dir() / "1"
        thaw(self.folder)
        self.nfd = unicodedata.normalize("NFD", self.NAME)
        self.assertNotEqual(self.NAME, self.nfd)
        sfx = self.folder / "assets" / "sfx"
        (sfx / self.NAME).rename(sfx / self.nfd)
        if self.nfd not in [entry.name for entry in sfx.iterdir()]:
            self.skipTest("o sistema de arquivos normaliza os nomes")

    def test_a_decomposed_name_on_disk_is_the_sealed_file(self):
        shown = recipes.show("reels-split@1", owner=OWNER)
        self.assertEqual((True, []), (shown["intact"], shown["problems"]), shown["problems"])
        self.assertIn(f"assets/sfx/{self.NAME}", [item["file"] for item in shown["components"]])

    def test_a_decomposed_name_is_opened_by_the_name_on_disk(self):
        found = recipes.component_path(self.folder, f"assets/sfx/{self.NAME}", "sfx")
        self.assertEqual(self.nfd, found.name)
        self.assertEqual(b"RIFF-pao", found.read_bytes())

    def test_a_real_extra_file_is_still_found_beside_a_decomposed_name(self):
        (self.folder / "assets" / "sfx" / "extra.wav").write_bytes(b"x")
        shown = recipes.show("reels-split@1", owner=OWNER)
        self.assertEqual(
            [f"assets/sfx/extra.wav é um arquivo a mais, que o {recipes.RECIPE_FILE} não lista."], shown["problems"]
        )

    def test_a_changed_decomposed_file_is_still_caught(self):
        (self.folder / "assets" / "sfx" / self.nfd).write_bytes(b"outro")
        shown = recipes.show("reels-split@1", owner=OWNER)
        self.assertIn(
            f"assets/sfx/{self.NAME} mudou: o sha256 não bate com o {recipes.COMPONENTS_FILE}.", shown["problems"]
        )


class NfcCollisionTests(RecipeCase):
    """Dois nomes que só diferem em NFC/NFD (ext4, zip): o APFS recusa os dois, então o disco é simulado."""

    NAME = "Pão.wav"

    def setUp(self):
        super().setUp()
        self.put(self.project / "assets" / "sfx", self.NAME, b"RIFF-pao")
        (self.project / "ROTEIRO.md").write_text(ROTEIRO.replace("[SFX: whoosh]", "[SFX: Pão]"), encoding="utf-8")
        self.seal()
        self.folder = self.slug_dir() / "1"
        self.nfd = unicodedata.normalize("NFD", self.NAME)
        self.sfx = self.folder / "assets" / "sfx"

    def test_two_names_with_the_same_nfc_form_are_a_problem(self):
        real = os.walk
        nfd = self.nfd

        def walk(top, *args, **kwargs):
            for current, dirs, names in real(top, *args, **kwargs):
                extra = [nfd] if Path(current) == self.sfx else []
                yield current, dirs, [*names, *extra]

        with patch.object(recipes.os, "walk", walk):
            shown = recipes.show("reels-split@1", owner=OWNER)
        self.assertFalse(shown["intact"])
        self.assertIn(f"dois nomes iguais em NFC: assets/sfx/{self.NAME}", shown["problems"])
        self.assertFalse([p for p in shown["problems"] if str(self.tmp) in p])

    def test_an_ambiguous_name_is_refused_never_picked(self):
        class Entry:
            def __init__(self, name):
                self.name = name

        both = [Entry(self.NAME), Entry(self.nfd)]
        real = Path.iterdir
        sfx = self.sfx

        def iterdir(path):
            return iter(both) if path == sfx else real(path)

        with patch.object(recipes.Path, "iterdir", iterdir), self.assertRaises(ValueError) as ctx:
            recipes.component_path(self.folder, f"assets/sfx/{self.NAME}", "sfx")
        self.assertIn("dois nomes iguais em NFC", str(ctx.exception))


class WalkRaceTests(RecipeCase):
    def setUp(self):
        super().setUp()
        self.seal()
        self.folder = self.slug_dir() / "1"

    def racing_walk(self, code):
        """`os.walk` em que a pasta `assets/lettering` sumiu (ou falhou com `code`) no meio da varredura."""
        real = os.walk
        gone = str(self.folder / "assets" / "lettering")

        def walk(top, *args, onerror, **kwargs):
            for current, dirs, names in real(top, *args, onerror=onerror, **kwargs):
                if current == gone:
                    onerror(OSError(code, os.strerror(code), gone))
                    continue
                yield current, dirs, names

        return patch.object(recipes.os, "walk", walk)

    def test_a_subfolder_removed_during_the_walk_is_a_missing_file_not_a_crash(self):
        for code in (errno.ENOENT, errno.ENOTDIR):
            with self.subTest(code=code), self.racing_walk(code):
                shown = recipes.show("reels-split@1", owner=OWNER)
                self.assertFalse(shown["intact"])
                self.assertIn(
                    "assets/lettering/titulo-grande.json está em files mas não existe na receita.", shown["problems"]
                )

    def test_any_other_walk_error_is_still_an_io_error(self):
        for code in (errno.EIO, errno.EACCES):
            with self.subTest(code=code), self.racing_walk(code), self.assertRaises(OSError) as ctx:
                recipes.show("reels-split@1", owner=OWNER)
            self.assertEqual(code, ctx.exception.errno)


class WriteNewTests(unittest.TestCase):
    def test_an_existing_file_is_refused_and_keeps_its_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.json"
            path.write_text("antigo", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                recipes.write_new(path, "novo")
            self.assertEqual("antigo", path.read_text(encoding="utf-8"))


class CopyHashedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-copy-hashed-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = self.tmp / "origem.bin"
        self.source.write_bytes(b"conteudo da origem")
        self.sha = hashlib.sha256(b"conteudo da origem").hexdigest()

    def test_copy_returns_the_sha256_of_the_copied_bytes(self):
        target = self.tmp / "copia.bin"
        self.assertEqual(self.sha, recipes.copy_hashed(self.source, target, expected=self.sha))
        self.assertEqual(b"conteudo da origem", target.read_bytes())

    def test_existing_target_is_never_overwritten(self):
        target = self.tmp / "copia.bin"
        target.write_bytes(b"meu")
        with self.assertRaisesRegex(ValueError, "já existe; nada foi sobrescrito"):
            recipes.copy_hashed(self.source, target)
        self.assertEqual(b"meu", target.read_bytes())

    def test_other_sha256_than_expected_removes_the_copy(self):
        target = self.tmp / "copia.bin"
        with self.assertRaisesRegex(ValueError, "mudou durante a cópia"):
            recipes.copy_hashed(self.source, target, expected="0" * 64)
        self.assertFalse(target.exists())

    def test_failure_in_the_middle_removes_the_partial_copy(self):
        target = self.tmp / "copia.bin"

        class Boom:
            def update(self, _chunk):
                raise OSError(errno.EIO, "disco")

        with (
            patch.object(recipes.hashlib, "sha256", return_value=Boom()),
            self.assertRaises(OSError) as raised,
        ):
            recipes.copy_hashed(self.source, target)
        self.assertEqual(errno.EIO, raised.exception.errno)
        self.assertFalse(target.exists())


class SafeRelativeTests(unittest.TestCase):
    def test_valid_component_path_is_split(self):
        self.assertEqual(("assets", "sfx", "a.wav"), recipes.safe_relative("assets/sfx/a.wav", "sfx"))

    def test_unsafe_paths_are_refused(self):
        for rel, kind in (
            ("../fora/a.wav", None),
            ("/abs/assets/sfx/a.wav", None),
            ("assets/sfx/a\\b.wav", None),
            ("assets/sfx/.oculto.wav", None),
            ("assets/sfx/a.licenca.json", None),
            ("assets/sfx/A.LICENCA.JSON", None),
            ("assets/sfx/sub/a.wav", None),
            ("assets/Sfx/a.wav", None),
            ("components/sfx/a.wav", None),
            ("assets/sfx/a.wav", "marca"),
            ("assets/sfx/a.wav", "desconhecido"),
        ):
            with self.subTest(rel=rel, kind=kind), self.assertRaisesRegex(ValueError, "inseguro"):
                recipes.safe_relative(rel, kind)


class ValidationTests(unittest.TestCase):
    def test_recipe_components_and_lock_must_be_objects_that_fit_the_schema(self):
        for check in (recipes.validate_recipe, recipes.validate_components, recipe_init.validate_lock):
            with self.subTest(check=check.__name__):
                with self.assertRaisesRegex(ValueError, "tem que ser um objeto JSON"):
                    check([])
                with self.assertRaises(ValueError):
                    check({})
                with self.assertRaisesRegex(ValueError, "x.json tem que ser um objeto JSON"):
                    check("texto", "x.json")


def rewrite_recipe(folder, change):
    """Edita o getbrolls-recipe.json de uma versão e acerta o selo (adulteração coerente)."""
    path = RecipeCase.writable(folder / recipes.RECIPE_FILE)
    doc = json.loads(path.read_text(encoding="utf-8"))
    change(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    reseal(folder)


def make_unknown_kind(folder):
    """Item 0 vira o tipo `transicao`, em `assets/transicao/` (um tipo novo do getbrolls); devolve o arquivo."""
    old = json.loads((folder / recipes.COMPONENTS_FILE).read_text(encoding="utf-8"))["items"][0]["file"]
    rel = f"assets/transicao/{old.rsplit('/', 1)[1]}"
    (folder / "assets" / "transicao").mkdir()
    old_folder = folder / "assets" / old.split("/")[1]
    old_folder.chmod(0o700)
    (folder / old).rename(folder / rel)
    old_folder.rmdir()
    rewrite_components(folder, lambda doc: doc["items"][0].update(kind="transicao", file=rel))
    rewrite_recipe(folder, lambda doc: doc.update(files=[rel if f == old else f for f in doc["files"]]))
    return rel


class ShowEdgeTests(RecipeCase):
    def setUp(self):
        super().setUp()
        self.seal()
        self.folder = self.slug_dir() / "1"
        thaw(self.folder)  # a sealed version has read-only folders; these tests tamper with it
        self.ref = "cliente-exemplo.reels-split@1"

    def show(self):
        return recipes.show(self.ref)

    def rewrite(self, change):
        rewrite_recipe(self.folder, change)

    def test_untouched_version_is_intact(self):
        shown = self.show()
        self.assertEqual((True, []), (shown["intact"], shown["problems"]))

    def test_recipe_json_that_is_a_folder_is_reported_not_read(self):
        self.writable(self.folder / recipes.RECIPE_FILE).unlink()
        (self.folder / recipes.RECIPE_FILE).mkdir()
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn(f"{recipes.RECIPE_FILE} não é um arquivo comum.", shown["problems"])
        self.assertEqual((None, None), (shown["recipe"], shown["recipe_sha256"]))

    def test_oversized_recipe_json_is_reported(self):
        with patch.object(recipes, "RECIPE_MAX", 10):
            shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn(f"{recipes.RECIPE_FILE} é grande demais.", shown["problems"])
        self.assertIsNone(shown["recipe"])

    def test_non_finite_number_in_recipe_json_is_reported(self):
        self.writable(self.folder / recipes.RECIPE_FILE).write_text('{"version": NaN}', encoding="utf-8")
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn("NaN não é um número JSON válido.", shown["problems"])

    def test_seal_bigger_than_the_limit_is_unreadable(self):
        self.writable(self.folder / recipes.SEAL_FILE).write_text("a" * (recipes.SEAL_MAX + 1), encoding="ascii")
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertTrue(
            any(p.startswith(f"{recipes.SEAL_FILE} ilegível") for p in shown["problems"]), shown["problems"]
        )

    def test_recipe_that_names_another_owner_or_slug_is_reported(self):
        def other(doc):
            doc["owner"] = "beta"
            doc["slug"] = "outro-slug"

        self.rewrite(other)
        problems = self.show()["problems"]
        self.assertIn(f'{recipes.RECIPE_FILE} é do cliente "beta", não de "{OWNER}".', problems)
        self.assertIn(f'{recipes.RECIPE_FILE} é da receita "outro-slug", não de "reels-split".', problems)

    def test_component_listed_twice_is_reported(self):
        path = self.writable(self.folder / recipes.COMPONENTS_FILE)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["items"].append(dict(doc["items"][0]))
        text = json.dumps(doc)
        path.write_text(text, encoding="utf-8")
        self.rewrite(lambda recipe: recipe["components"].update(sha256=hashlib.sha256(text.encode()).hexdigest()))
        problems = self.show()["problems"]
        self.assertTrue(
            any(p.endswith(f"aparece duas vezes em {recipes.COMPONENTS_FILE}.") for p in problems), problems
        )

    def test_component_missing_from_files_is_reported(self):
        self.rewrite(lambda doc: doc["files"].remove("assets/sfx/whoosh.wav"))
        problems = self.show()["problems"]
        self.assertIn(f"assets/sfx/whoosh.wav não está na lista files do {recipes.RECIPE_FILE}.", problems)

    def test_link_not_listed_inside_the_version_is_reported(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        (self.folder / "atalho.txt").symlink_to(self.tmp)
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn("atalho.txt é um link dentro da receita.", shown["problems"])

    def assert_problem(self, text):
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn(text, shown["problems"])
        return shown

    def test_a_file_listed_in_files_but_missing_is_reported(self):
        self.rewrite(lambda doc: doc["files"].append("nota.txt"))
        self.assert_problem("nota.txt está em files mas não existe na receita.")

    def test_a_listed_file_that_is_neither_components_json_nor_a_component_is_reported(self):
        (self.folder / "nota.txt").write_text("sem hash", encoding="utf-8")
        self.rewrite(lambda doc: doc["files"].append("nota.txt"))
        shown = self.assert_problem(f"nota.txt está em files mas não é o {recipes.COMPONENTS_FILE} nem um componente.")
        self.assertNotIn("nota.txt é um arquivo a mais, que o getbrolls-recipe.json não lista.", shown["problems"])

    def test_a_file_that_is_not_listed_is_reported(self):
        (self.folder / "nota.txt").write_text("sobra", encoding="utf-8")
        self.assert_problem(f"nota.txt é um arquivo a mais, que o {recipes.RECIPE_FILE} não lista.")

    def test_the_recipe_json_listed_in_files_is_reported(self):
        self.rewrite(lambda doc: doc["files"].append(recipes.RECIPE_FILE))
        self.assert_problem(
            f"{recipes.RECIPE_FILE} está em files mas não é o {recipes.COMPONENTS_FILE} nem um componente."
        )

    def test_a_listed_link_is_reported_too(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        (self.folder / "atalho.txt").symlink_to(self.tmp)
        self.rewrite(lambda doc: doc["files"].append("atalho.txt"))
        shown = self.assert_problem("atalho.txt é um link dentro da receita.")
        self.assertNotIn("atalho.txt está em files mas não existe na receita.", shown["problems"])

    def test_a_link_to_a_folder_is_reported_listed_or_not(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        (self.folder / "pasta-ligada").symlink_to(self.tmp, target_is_directory=True)
        self.assert_problem("pasta-ligada é um link dentro da receita.")
        self.rewrite(lambda doc: doc["files"].append("pasta-ligada"))
        self.assert_problem("pasta-ligada é um link dentro da receita.")

    def test_a_link_inside_assets_is_reported_and_not_followed(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        (self.folder / "assets" / "sfx" / "fora").symlink_to(self.tmp, target_is_directory=True)
        shown = self.assert_problem("assets/sfx/fora é um link dentro da receita.")
        self.assertEqual(1, len([p for p in shown["problems"] if "fora" in p]))

    def test_empty_extra_folders_are_reported(self):
        (self.folder / "zz").mkdir()
        (self.folder / "assets" / "vazia").mkdir()
        (self.folder / "assets" / "sfx" / "sub").mkdir()
        problems = self.show()["problems"]
        for rel in ("zz", "assets/vazia", "assets/sfx/sub"):
            self.assertIn(f"{rel} é uma pasta a mais, que o {recipes.RECIPE_FILE} não lista.", problems)

    def test_the_folders_of_the_listed_components_are_not_reported(self):
        shown = self.show()
        self.assertEqual([], [p for p in shown["problems"] if "pasta" in p])

    def test_component_path_refuses_a_link_in_any_part_of_the_path(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        sfx = self.folder / "assets" / "sfx"
        real = self.tmp / "sfx-real"
        shutil.copytree(sfx, real)
        target = self.writable(sfx / "whoosh.wav")
        target.unlink()
        target.symlink_to(real / "whoosh.wav")
        with self.assertRaisesRegex(ValueError, "é um link"):
            recipes.component_path(self.folder, "assets/sfx/whoosh.wav", "sfx")
        target.unlink()
        shutil.copy(real / "whoosh.wav", target)
        self.assertEqual(target, recipes.component_path(self.folder, "assets/sfx/whoosh.wav", "sfx"))
        shutil.rmtree(sfx)
        sfx.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "é um link"):
            recipes.component_path(self.folder, "assets/sfx/whoosh.wav", "sfx")

    def test_an_unreadable_components_json_is_reported_without_a_flood_of_listed_files(self):
        path = self.writable(self.folder / recipes.COMPONENTS_FILE)
        path.write_text("{", encoding="utf-8")
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn(f"{recipes.COMPONENTS_FILE} não é um JSON legível.", shown["problems"])
        self.assertEqual([], [p for p in shown["problems"] if "nem um componente" in p], shown["problems"])
        self.assertEqual([], shown["components"])

    @NEEDS_EACCES
    def test_a_locked_extra_subfolder_is_an_io_error_on_every_python(self):
        locked = self.folder / "zz"
        locked.mkdir()
        (locked / "escondido.txt").write_text("x", encoding="utf-8")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaises(PermissionError):
            self.show()

    @NEEDS_EACCES
    def test_a_locked_component_subfolder_is_an_io_error_on_every_python(self):
        locked = self.folder / "assets" / "sfx"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaises(PermissionError):
            self.show()

    def test_a_ref_that_differs_while_owner_slug_and_version_match_is_reported(self):
        other = "cat:getbrolls/recipe/cliente-exemplo.reels-split@7"
        self.rewrite(lambda doc: doc.update(ref=other))
        self.assert_problem(f"{recipes.RECIPE_FILE} traz a ref {other}, não cat:getbrolls/recipe/{self.ref}.")

    def test_a_seal_that_is_a_link_to_a_correct_seal_is_not_intact(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        seal = self.folder / recipes.SEAL_FILE
        good = self.tmp / "selo-certo"
        good.write_bytes(seal.read_bytes())
        self.writable(seal).unlink()
        seal.symlink_to(good)
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertTrue(any(p.startswith(f"{recipes.SEAL_FILE} não existe") for p in shown["problems"]), shown)

    def test_a_recipe_json_that_is_a_link_to_a_correct_file_is_not_read(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        path = self.folder / recipes.RECIPE_FILE
        good = self.tmp / "receita-certa.json"
        good.write_bytes(path.read_bytes())
        self.writable(path).unlink()
        path.symlink_to(good)
        shown = self.assert_problem(f"{recipes.RECIPE_FILE} não é um arquivo comum.")
        self.assertEqual((None, None), (shown["recipe"], shown["recipe_sha256"]))

    def make_unknown_kind(self):
        return make_unknown_kind(self.folder)

    def test_an_unknown_component_kind_warns_and_the_item_is_skipped(self):
        self.make_unknown_kind()
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        shown = self.show()
        self.assertIn("ENUM_UNKNOWN", [row["code"] for row in event["warnings"]])
        self.assertEqual(2, len(shown["components"]))
        self.assertNotIn("transicao", [item["kind"] for item in shown["components"]])
        self.assertEqual((True, []), (shown["intact"], shown["problems"]))

    def test_the_bytes_of_an_unknown_component_kind_are_still_verified(self):
        rel = self.make_unknown_kind()
        target = self.writable(self.folder / rel)
        target.write_bytes(target.read_bytes() + b"adulterado")
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn(f"{rel} mudou: o sha256 não bate com o {recipes.COMPONENTS_FILE}.", shown["problems"])
        self.assertNotIn("transicao", [item["kind"] for item in shown["components"]])


class RecipesRootRaceTests(RecipeCase):
    def test_client_removed_between_the_two_registry_reads_is_refused_as_unregistered(self):
        # `load_client` and `client_root` each read the registry: a `client --action remove`
        # from another process between them leaves no folder, and that is a refusal, not a bug.
        with (
            patch.object(recipes.clients, "client_root", return_value=None),
            self.assertRaisesRegex(ValueError, f'O cliente "{OWNER}" não está registrado.'),
        ):
            recipes.recipes_root(OWNER)
        self.assertEqual(self.client / "recipes", recipes.recipes_root(OWNER))


class LabelTests(RecipeCase):
    """A pasta numa mensagem da loja sai pela marca (`<cliente>/…`, `<projeto>/…`), nunca pelo caminho da máquina."""

    @needs_symlink
    def test_a_recipes_folder_link_is_named_without_reading_the_registry(self):
        # `_place` recebe a pasta do cliente de quem chama: uma mensagem de erro não lê o registro de novo.
        root = self.client / "recipes"
        shutil.rmtree(root, ignore_errors=True)
        root.symlink_to(self.tmp, target_is_directory=True)
        with (
            patch.object(recipes.clients, "load_registry", side_effect=OSError("registro ilegível")),
            self.assertRaises(ValueError) as ctx,
        ):
            recipes.refuse_link(root, client_root=self.client)
        self.assertEqual(
            "<cliente>/recipes é um link; o getbrolls não lê nem grava receitas através de links.", str(ctx.exception)
        )

    def test_a_target_that_exists_is_named_by_its_label(self):
        target = self.tmp / "p" / "copia.bin"
        target.parent.mkdir()
        target.write_bytes(b"meu")
        source = self.put(self.tmp, "origem.bin", b"x")
        with self.assertRaises(ValueError) as ctx:
            recipes.copy_hashed(source, target, project=self.tmp / "p")
        self.assertEqual("<projeto>/copia.bin já existe; nada foi sobrescrito.", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            recipes.copy_hashed(source, target, client_root=self.tmp / "p")
        self.assertEqual("<cliente>/copia.bin já existe; nada foi sobrescrito.", str(ctx.exception))


class ListEdgeTests(RecipeCase):
    def test_client_without_recipes_lists_nothing(self):
        shutil.rmtree(self.client / "recipes", ignore_errors=True)
        self.assertEqual({"recipes": [], "clients_with_problems": []}, recipes.list_recipes())

    @needs_symlink
    def test_junk_in_the_recipes_folder_is_skipped(self):
        self.seal()
        root = self.client / "recipes"
        (root / "Nome Inválido!").mkdir()
        (root / "arquivo.txt").write_text("x", encoding="utf-8")
        linked = can_symlink(self.tmp)
        if linked:
            (root / "atalho").symlink_to(self.slug_dir(), target_is_directory=True)
            (self.slug_dir() / "2").symlink_to(self.slug_dir() / "1", target_is_directory=True)
        found = recipes.list_recipes()
        rows = found["recipes"]
        # Junk is skipped, but a link never is: the version link is a row with the `link` problem, the slug link a
        # problem of the client.
        self.assertEqual([None, "link"][: 1 + linked], [row["problem_code"] for row in rows])
        self.assertEqual([1, 2][: 1 + linked], [row["version"] for row in rows])
        self.assertEqual(
            ["link"] if linked else [], [entry["problem_code"] for entry in found["clients_with_problems"]]
        )

    @NEEDS_EACCES
    def test_a_locked_version_is_a_problem_row_and_never_takes_the_list_down(self):
        self.seal()
        self.seal()
        locked = self.slug_dir() / "2"
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        rows = recipes.list_recipes()["recipes"]
        self.assertEqual([("reels-split", 1), ("reels-split", 2)], [(row["slug"], row["version"]) for row in rows])
        good, bad = rows[0], rows[1]
        self.assertIsNone(good["problem"])
        self.assertIsNone(bad["title"])
        self.assertTrue(bad["problem"].startswith(f"{recipes.RECIPE_FILE} não pôde ser lido: "), bad["problem"])
        self.assertNotIn(str(self.tmp), bad["problem"])
        with self.assertRaises(PermissionError):
            recipes.show("reels-split@2", owner=OWNER)
        self.assertTrue(recipes.show("reels-split@1", owner=OWNER)["intact"])

    @needs_symlink
    def test_a_link_named_like_a_version_is_not_a_version(self):
        self.seal()
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        (self.slug_dir() / "5").symlink_to(self.slug_dir() / "1", target_is_directory=True)
        self.assertEqual(1, recipes.latest_version(OWNER, "reels-split"))
        self.assertEqual(2, recipes.next_version(OWNER, "reels-split"))
        self.assertEqual(2, self.seal()["version"])
        listed = recipes.list_recipes()["recipes"]
        self.assertEqual([1, 2, 5], [row["version"] for row in listed])
        self.assertEqual([None, None, "link"], [row["problem_code"] for row in listed])

    def test_the_problem_code_names_the_kind_of_problem(self):
        self.seal()
        for _ in range(3):
            self.seal()
        thaw(self.slug_dir())
        (self.slug_dir() / "2" / recipes.RECIPE_FILE).write_text("{", encoding="utf-8")
        schema = self.slug_dir() / "3" / recipes.RECIPE_FILE
        doc = json.loads(schema.read_text(encoding="utf-8"))
        del doc["scenes"]
        schema.write_text(json.dumps(doc), encoding="utf-8")
        (self.slug_dir() / "4" / recipes.RECIPE_FILE).unlink()
        (self.slug_dir() / "4" / recipes.RECIPE_FILE).mkdir()
        rows = recipes.list_recipes()["recipes"]
        self.assertEqual(
            [None, "unreadable", "schema", "unreadable"],
            [row["problem_code"] for row in rows],
        )
        self.assertEqual([False, True, True, True], [bool(row["problem"]) for row in rows])

    def test_an_unreadable_version_has_the_io_error_code(self):
        self.seal()
        for error in (PermissionError(errno.EACCES, "Permission denied"), OSError(errno.EIO, "Input/output error")):
            with self.subTest(error=error), patch.object(recipes, "_read_json", side_effect=error):
                row = recipes.list_recipes()["recipes"][0]
                self.assertEqual("io_error", row["problem_code"])
                self.assertEqual(f"{recipes.RECIPE_FILE} não pôde ser lido: {error.strerror}.", row["problem"])

    @NEEDS_EACCES
    def test_a_locked_slug_folder_is_a_client_problem_and_never_takes_the_list_down(self):
        self.seal()
        self.seal("outra")
        locked = self.slug_dir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        found = recipes.list_recipes()
        self.assertEqual([("outra", 1)], [(row["slug"], row["version"]) for row in found["recipes"]])
        self.assertEqual(1, len(found["clients_with_problems"]))
        entry = found["clients_with_problems"][0]
        self.assertEqual((OWNER, "io_error"), (entry["owner"], entry["problem_code"]))
        self.assertEqual('A receita "reels-split" não pôde ser lida: Permission denied.', entry["problem"])
        self.assertNotIn(str(self.tmp), entry["problem"])

    @NEEDS_EACCES
    def test_a_locked_recipes_folder_is_a_client_problem(self):
        self.seal()
        root = self.client / "recipes"
        root.chmod(0)
        self.addCleanup(root.chmod, 0o700)
        found = recipes.list_recipes()
        self.assertEqual([], found["recipes"])
        self.assertEqual(1, len(found["clients_with_problems"]))
        entry = found["clients_with_problems"][0]
        self.assertEqual((OWNER, "io_error"), (entry["owner"], entry["problem_code"]))
        self.assertEqual("A pasta recipes não pôde ser lida: Permission denied.", entry["problem"])

    def test_any_os_error_scanning_the_folders_is_a_client_problem(self):
        self.seal()
        eio = OSError(errno.EIO, "Input/output error")
        with patch.object(recipes, "_versions", side_effect=eio):
            found = recipes.list_recipes()
        self.assertEqual([], found["recipes"])
        self.assertEqual("io_error", found["clients_with_problems"][0]["problem_code"])
        self.assertEqual(
            'A receita "reels-split" não pôde ser lida: Input/output error.',
            found["clients_with_problems"][0]["problem"],
        )
        real = recipes.strict_is_dir

        def strict(path):
            if path == self.client / "recipes":
                raise eio
            return real(path)

        with patch.object(recipes, "strict_is_dir", strict):
            found = recipes.list_recipes()
        self.assertEqual(
            "A pasta recipes não pôde ser lida: Input/output error.", found["clients_with_problems"][0]["problem"]
        )

    @needs_symlink
    def test_a_client_problem_that_is_not_an_io_error_has_the_unavailable_code(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        shutil.rmtree(self.client / "recipes", ignore_errors=True)
        (self.client / "recipes").symlink_to(self.tmp, target_is_directory=True)
        entry = recipes.list_recipes()["clients_with_problems"][0]
        self.assertEqual("unavailable", entry["problem_code"])

    def test_recipes_folder_that_is_a_link_is_reported_per_client(self):
        if not can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        shutil.rmtree(self.client / "recipes", ignore_errors=True)
        (self.client / "recipes").symlink_to(elsewhere, target_is_directory=True)
        found = recipes.list_recipes()
        self.assertEqual([], found["recipes"])
        self.assertEqual([OWNER], [row["owner"] for row in found["clients_with_problems"]])
        problem = found["clients_with_problems"][0]["problem"]
        self.assertTrue(problem.startswith("<cliente>/recipes é um link"), problem)
        self.assertNotIn(str(self.tmp), problem.replace(str(elsewhere), ""))
        self.assertEqual([], list(elsewhere.iterdir()))


class InitEdgeTests(RecipeInitCase):
    def test_project_path_that_is_a_file_is_refused_untouched(self):
        self.new.write_text("sou um arquivo", encoding="utf-8")
        refused = self.init(expect=1)
        self.assertIn("existe e não é uma pasta", refused["error"])
        self.assertEqual("sou um arquivo", self.new.read_text(encoding="utf-8"))

    def test_folder_with_a_manifest_is_refused_and_nothing_is_added(self):
        (self.new / "brolls").mkdir(parents=True)
        (self.new / "brolls" / "manifest.json").write_text('{"items": []}', encoding="utf-8")
        refused = self.init(expect=1)
        self.assertIn("já é um projeto do getbrolls", refused["error"])
        for name in ("project.json", "ROTEIRO.md", recipe_init.LOCK_FILE):
            self.assertFalse((self.new / name).exists(), name)

    def test_client_without_the_component_folder_gets_the_copy_from_the_recipe(self):
        shutil.rmtree(self.client / "assets" / "marca")
        self.init()
        rows = {row["name"]: row for row in self.lock()["items"]}
        self.assertEqual(("project", "assets/marca/logo.svg"), (rows["logo"]["tier"], rows["logo"]["path"]))
        self.assertEqual(b"<svg/>", (self.new / "assets" / "marca" / "logo.svg").read_bytes())
        self.assertFalse((self.client / "assets" / "marca").exists())

    def test_existing_rules_file_is_kept_and_not_reported_as_seeded(self):
        self.new.mkdir()
        (self.new / "RULES.md").write_text("minhas regras\n", encoding="utf-8")
        out = self.init()
        self.assertIsNone(out["rules"])
        self.assertEqual("minhas regras\n", (self.new / "RULES.md").read_text(encoding="utf-8"))
        self.assertTrue((self.new / recipe_init.LOCK_FILE).is_file())

    def test_a_client_file_with_other_bytes_is_not_the_recipes_component_and_the_copy_is_made(self):
        (self.client / "assets" / "marca" / "logo.svg").write_bytes(b"<svg>do cliente</svg>")
        self.init()
        rows = {row["name"]: row for row in self.lock()["items"]}
        self.assertEqual(("project", "assets/marca/logo.svg"), (rows["logo"]["tier"], rows["logo"]["path"]))
        self.assertEqual(b"<svg/>", (self.new / "assets" / "marca" / "logo.svg").read_bytes())
        self.assertEqual(b"<svg>do cliente</svg>", (self.client / "assets" / "marca" / "logo.svg").read_bytes())
        self.assertEqual([], recipe_init.lock_warnings(self.new))

    def test_a_component_that_changes_between_show_and_the_copy_is_refused_and_nothing_is_left(self):
        plan = recipe_init.plan_instance(self.new, REF, OWNER, "Novo")  # `show` saw the good bytes
        self.writable(self.slug_dir() / "1" / "assets" / "sfx" / "whoosh.wav").write_bytes(b"trocado")
        with self.assertRaises(ValueError) as ctx:
            recipe_init.write_instance(plan)
        self.assertIn("whoosh.wav mudou durante a cópia", str(ctx.exception))
        self.assertFalse(self.new.exists())

    def test_a_component_kind_this_version_does_not_know_is_not_copied_and_warns(self):
        folder = self.slug_dir() / "1"
        thaw(folder)
        rel = make_unknown_kind(folder)
        open_enums.reset()
        out = self.init()
        self.assertIn("ENUM_UNKNOWN", [row["code"] for row in out["warnings"]])
        name = rel.rsplit("/", 1)[1]
        self.assertEqual([], [p for p in self.new.rglob("*") if p.name == name])
        self.assertFalse((self.new / "assets" / "transicao").exists())
        self.assertNotIn("transicao", [row["kind"] for row in self.lock()["items"]])
        self.assertEqual(len(out["items"]), len(self.lock()["items"]))

    def test_install_skips_an_item_of_an_unknown_kind_without_copying_it(self):
        # `show` already drops the kind it does not know; `_install` is the second guard on the items it is given.
        folder = self.slug_dir() / "1"
        thaw(folder)
        rel = make_unknown_kind(folder)
        item = {"kind": "transicao", "name": "x", "file": rel, "sha256": "0" * 64}
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        open_enums.reset()
        created = []
        installed, warnings = recipe_init._install(  # pylint: disable=protected-access
            self.new, folder, OWNER, [item], created
        )
        self.assertEqual(([], [], []), (installed, warnings, created))
        self.assertIn("ENUM_UNKNOWN", [row["code"] for row in event["warnings"]])
        self.assertFalse(self.new.exists())

    def test_an_existing_lock_file_is_refused_with_the_reason_and_left_alone(self):
        self.new.mkdir()
        (self.new / recipe_init.LOCK_FILE).write_text("meu lock", encoding="utf-8")
        refused = self.init(expect=1)
        self.assertEqual(
            f"{recipe_init.LOCK_FILE} já existe em {self.new}; init --recipe não sobrescreve nada.", refused["error"]
        )
        self.assertEqual("meu lock", (self.new / recipe_init.LOCK_FILE).read_text(encoding="utf-8"))
        self.assertFalse((self.new / "project.json").exists())


class LockEdgeTests(RecipeInitCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.lock_path = self.new / recipe_init.LOCK_FILE

    @needs_symlink
    def test_lock_that_is_too_big_or_a_link_is_unreadable(self):
        with patch.object(recipe_init, "LOCK_MAX", 5):
            warnings = recipe_init.lock_warnings(self.new)
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn(f"{recipe_init.LOCK_FILE} ilegível (não é um arquivo comum)", warnings[0])
        if can_symlink(self.tmp):
            real = self.tmp / "lock-real.json"
            self.lock_path.rename(real)
            self.lock_path.symlink_to(real)
            warnings = recipe_init.lock_warnings(self.new)
            self.assertEqual(1, len(warnings), warnings)
            self.assertIn("não é um arquivo comum", warnings[0])

    def test_project_json_pointing_elsewhere_is_reported(self):
        path = self.new / "project.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["recipe"] = "cat:getbrolls/recipe/cliente-exemplo.outra@1"
        path.write_text(json.dumps(doc), encoding="utf-8")
        warnings = recipe_init.lock_warnings(self.new)
        self.assertEqual([f"{recipe_init.LOCK_FILE} é da receita {REF}, diferente do project.json."], warnings)

    def test_a_project_json_with_a_problem_reports_only_the_mismatch_not_false_drift(self):
        path = self.new / "project.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["template"] = doc.pop("recipe")
        path.write_text(json.dumps(doc), encoding="utf-8")
        self.assertIsNotNone(layout.info(self.new).problem)
        self.assertEqual(
            [f"{recipe_init.LOCK_FILE} é da receita {REF}, diferente do project.json."],
            recipe_init.lock_warnings(self.new),
        )
        status = run_cli("status", project=self.new)
        drift = [w["message"] for w in status["warnings"] if w["code"] == "RECIPE_LOCK_DRIFT"]
        self.assertEqual(1, len(drift), status["warnings"])

    def test_client_component_that_vanished_is_reported_as_gone(self):
        (self.client / "assets" / "marca" / "logo.svg").unlink()
        warnings = recipe_init.lock_warnings(self.new)
        self.assertEqual([f'marca "logo" da receita {REF} sumiu desde o init.'], warnings)

    def test_ambiguous_client_component_is_reported_with_the_cause(self):
        self.put(self.client / "assets" / "marca", "logo.png", b"png")
        warnings = recipe_init.lock_warnings(self.new)
        self.assertEqual(1, len(warnings), warnings)
        self.assertTrue(warnings[0].startswith('marca "logo" da receita: '), warnings)
        self.assertIn("ambíguo em <cliente>/assets/marca", warnings[0])
        self.assertNotIn(str(self.tmp), warnings[0])

    def test_component_that_cannot_be_stat_ed_is_not_the_same_one(self):
        item = {"sha256": hashlib.sha256(b"x").hexdigest(), "size": 1, "mtime_ns": 1}
        self.assertFalse(recipe_init._same_component(self.tmp / "sumiu.bin", item))  # pylint: disable=protected-access
        present = self.put(self.tmp, "presente.bin", b"x")
        self.assertTrue(recipe_init._same_component(present, item))  # pylint: disable=protected-access


if __name__ == "__main__":
    unittest.main()
