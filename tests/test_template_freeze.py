"""Templates de cliente: `template --action freeze` grava uma versão imutável, sem fala nem licença."""

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
from _cli import run_cli
from _paths import ROOT
from _schemas import close

from getbrolls import clients, layout, templates
from getbrolls.sdk import jsonschema

ROTEIRO = """---
type: roteiro
genero: reels
tema: Lançamento
cliente: acme
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


def reseal_template(folder):
    """Regrava `template.sha256` com o sha256 do `template.json` atual (simula quem adultera os dois)."""
    seal = folder / "template.sha256"
    seal.chmod(stat.S_IWUSR | stat.S_IRUSR)
    digest = hashlib.sha256((folder / "template.json").read_bytes()).hexdigest()
    seal.write_text(f"{digest}  template.json\n", encoding="utf-8")


def _schema(name):
    return json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


class TemplateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-template-")).resolve()
        self.addCleanup(self._cleanup)
        self.home = self.tmp / "gb-home"
        env = patch.dict(os.environ, {"GB_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        (self.tmp / "Clientes").mkdir()
        clients.add("acme", str(self.tmp / "Clientes"), name="ACME")
        self.client = self.tmp / "Clientes" / "acme"
        self.project = self.tmp / "video"
        self.project.mkdir()
        layout.write_project(
            self.project,
            layout.new_project_doc(client="acme", canvas={"width": 1080, "height": 1920}, fps={"num": 30, "den": 1}),
        )
        (self.project / "ROTEIRO.md").write_text(ROTEIRO, encoding="utf-8")
        self.put(self.project / "assets" / "sfx", "whoosh.wav", b"RIFF-whoosh")
        self.put(self.project / "assets" / "sfx", "whoosh.licenca.json", b'{"origem":"a","licenca":"b","credito":"c"}')
        self.put(self.project / "assets" / "lettering", "titulo-grande.json", b'{"style": 1}')
        self.put(self.client / "components" / "marca", "logo.svg", b"<svg/>")
        self.put(self.project / "aroll", "c01.mp4", b"video")
        self.put(self.project / "brolls", "manifest.json", b'{"schema_version": 1, "items": []}')

    def _cleanup(self):
        for path in self.tmp.rglob("*"):
            if path.is_file() and not path.is_symlink():
                path.chmod(stat.S_IWUSR | stat.S_IRUSR)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def put(folder, name, data):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        return folder / name

    def slug_dir(self, slug="reels-acme"):
        return self.client / "templates" / slug

    def freeze(self, *extra, expect=0, slug="reels-acme"):
        return run_cli("template", "--action", "freeze", "--from", self.project, "--slug", slug, *extra, expect=expect)


class FreezeTests(TemplateCase):
    def test_two_freezes_make_two_versions_and_the_first_never_changes(self):
        first = self.freeze("--title", "Reels padrão ACME", "--by", "Bruno")
        self.assertEqual("cat:getbrolls/template/reels-acme@1", first["ref"])
        before = tree_hash(self.slug_dir() / "1")
        (self.project / "assets" / "sfx" / "whoosh.wav").write_bytes(b"RIFF-other")
        second = self.freeze()
        self.assertEqual(("cat:getbrolls/template/reels-acme@2", 2), (second["ref"], second["version"]))
        self.assertEqual(before, tree_hash(self.slug_dir() / "1"))
        self.assertEqual(["1", "2"], sorted(p.name for p in self.slug_dir().iterdir()))
        self.assertEqual([".lock", "reels-acme"], sorted(p.name for p in (self.client / "templates").iterdir()))

    @unittest.skipIf(os.name == "nt", "modo POSIX")
    def test_files_are_read_only_after_the_rename(self):
        self.freeze()
        files = [p for p in (self.slug_dir() / "1").rglob("*") if p.is_file()]
        self.assertTrue(files)
        for path in files:
            self.assertEqual(0, path.stat().st_mode & 0o222, path)

    def test_template_json_is_valid_and_carries_structure_only(self):
        self.freeze("--title", "Reels padrão ACME", "--by", "Bruno")
        raw = (self.slug_dir() / "1" / "template.json").read_text(encoding="utf-8")
        doc = json.loads(raw)
        self.assertEqual([], jsonschema.errors(doc, _schema("template")))
        self.assertEqual([], jsonschema.errors(doc, close(_schema("template"))))
        self.assertEqual(("getbrolls.template/1", "acme", 1), (doc["schema"], doc["client"], doc["version"]))
        self.assertEqual(({"width": 1080, "height": 1920}, {"num": 30, "den": 1}), (doc["canvas"], doc["fps"]))
        for secret in ("Fala secreta", "48213", "Promoção relâmpago", "Outra fala"):
            self.assertNotIn(secret, raw)
        gancho, marca = doc["slots"]
        self.assertEqual(("s01", "Gancho", "[A-ROLL]"), (gancho["id"], gancho["title"], gancho["layout"]))
        self.assertEqual(['[LETTERING: "{texto}" | titulo-grande]', "[SFX: whoosh]"], gancho["layers"])
        self.assertEqual("{fala da cena}", gancho["placeholder"])
        self.assertEqual(("[FULL: logo]", ["[MUSICA: tema]"]), (marca["layout"], marca["layers"]))
        provenance = doc["provenance"]
        self.assertEqual(layout.project_id(self.project), provenance["project_id"])
        self.assertEqual(
            hashlib.sha256((self.project / "ROTEIRO.md").read_bytes()).hexdigest(), provenance["roteiro_sha256"]
        )
        self.assertEqual(("2.6.0", "Bruno"), (provenance["getbrolls_version"], provenance["by"]))

    def test_components_are_copied_by_hash_without_licences(self):
        out = self.freeze()
        doc = json.loads((self.slug_dir() / "1" / "template.json").read_text(encoding="utf-8"))
        files = {row["file"]: row for row in doc["components"]}
        self.assertEqual(
            {"components/sfx/whoosh.wav", "components/lettering/titulo-grande.json", "components/marca/logo.svg"},
            set(files),
        )
        for rel, row in files.items():
            data = (self.slug_dir() / "1" / rel).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])
            self.assertEqual("not_transferred", row["licence"])
        names = {p.name for p in (self.slug_dir() / "1").rglob("*")}
        for banned in ("whoosh.licenca.json", "brolls", "broll", "aroll", "manifest.json", "c01.mp4", "ROTEIRO.md"):
            self.assertNotIn(banned, names)
        self.assertIn('musica "tema" não foi achado', " ".join(w["message"] for w in out["warnings"]))
        self.assertFalse(out["licences_transferred"])

    def test_interrupted_freeze_leaves_no_staging_and_no_version(self):
        with patch.object(templates.os, "rename", side_effect=OSError("disco cheio")), self.assertRaises(OSError):
            templates.freeze(self.project, "reels-acme")
        self.assertEqual([], [p.name for p in self.slug_dir().iterdir()])

    def test_taken_number_moves_to_the_next(self):
        self.slug_dir().mkdir(parents=True)
        (self.slug_dir() / "1").mkdir()
        (self.slug_dir() / "1" / "x").write_text("x", encoding="utf-8")
        self.assertEqual(2, templates.next_version("acme", "reels-acme"))
        self.assertEqual(2, templates.freeze(self.project, "reels-acme")["version"])
        self.assertEqual("x", (self.slug_dir() / "1" / "x").read_text(encoding="utf-8"))

    def test_engine_refs_are_canonical_and_never_a_template(self):
        self.freeze(
            "--engine-ref", "cat:hyperframes/recipe/yan%2dcortes@7", "--engine-ref", "cat:hyperframes/recipe/b@1"
        )
        doc = json.loads((self.slug_dir() / "1" / "template.json").read_text(encoding="utf-8"))
        self.assertEqual(["cat:hyperframes/recipe/yan-cortes@7", "cat:hyperframes/recipe/b@1"], doc["engine_refs"])
        for bad in ("cat:getbrolls/template/outro@1", "scene:c01", "cat:x", "hyperframes/recipe/a@1"):
            with self.subTest(bad=bad):
                refused = self.freeze("--engine-ref", bad, expect=2, slug="outro")
                self.assertEqual("USAGE_ERROR", refused["error_code"])
        self.assertFalse(self.slug_dir("outro").exists())

    def test_bad_slug_is_a_usage_error(self):
        for bad in ("Reels", "../x", "a/b", "a%2Fb", ""):
            with self.subTest(bad=bad):
                self.assertEqual("USAGE_ERROR", self.freeze(expect=2, slug=bad)["error_code"])

    def test_unregistered_or_mismatched_client_is_refused(self):
        refused = self.freeze("--client", "ghost", expect=1)
        self.assertIn("ghost", refused["error"])
        clients.remove("acme")
        refused = self.freeze(expect=1)
        self.assertIn("não está registrado", refused["error"])
        self.assertFalse(self.slug_dir().exists())

    def test_client_from_flag_when_the_project_has_none(self):
        (self.project / "project.json").unlink()
        refused = self.freeze(expect=1)
        self.assertIn("--client", refused["error"])
        self.assertEqual(1, self.freeze("--client", "acme")["version"])

    def test_roteiro_with_problems_is_refused(self):
        (self.project / "ROTEIRO.md").write_text(ROTEIRO.replace("[A-ROLL]\n", ""), encoding="utf-8")
        refused = self.freeze(expect=1)
        self.assertIn("layout", refused["error"])
        self.assertFalse(self.slug_dir().exists())

    def test_ambiguous_component_is_refused(self):
        self.put(self.project / "assets" / "sfx", "whoosh.mp3", b"mp3")
        refused = self.freeze(expect=1)
        self.assertIn("ambíguo", refused["error"])
        self.assertFalse(self.slug_dir().exists())

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_linked_templates_folder_is_refused(self):
        outside = self.tmp / "fora"
        outside.mkdir()
        shutil.rmtree(self.client / "templates")
        (self.client / "templates").symlink_to(outside, target_is_directory=True)
        refused = self.freeze(expect=1)
        self.assertIn("link", refused["error"])
        self.assertEqual([], list(outside.iterdir()))

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_linked_templates_lock_is_never_followed(self):
        (self.client / "templates").mkdir(exist_ok=True)
        target = self.tmp / "fora.lock"
        (self.client / "templates" / ".lock").symlink_to(target)
        refused = self.freeze(expect=1)
        self.assertIn("link", refused["error"])
        self.assertFalse(os.path.lexists(target))
        self.assertFalse(self.slug_dir().exists() and any(self.slug_dir().iterdir()))

    def test_help_and_capabilities_list_freeze(self):
        described = run_cli("capabilities")
        command = next(row for row in described["commands"] if row["name"] == "template")
        self.assertIn("freeze", json.dumps(command))


if __name__ == "__main__":
    unittest.main()


class ShowAndListTests(TemplateCase):
    def show(self, ref="cat:getbrolls/template/reels-acme@1", client="acme", expect=0):
        return run_cli("template", "--action", "show", "--ref", ref, "--client", client, expect=expect)

    def writable(self, path):
        path.chmod(stat.S_IWUSR | stat.S_IRUSR)
        return path

    def rewrite_template(self, change, *, reseal=True):
        """Edita o template.json; com `reseal`, regrava também o template.sha256 (adulteração coerente)."""
        path = self.writable(self.slug_dir() / "1" / "template.json")
        doc = json.loads(path.read_text(encoding="utf-8"))
        change(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")
        if reseal:
            reseal_template(self.slug_dir() / "1")

    def test_list_is_sorted_by_client_slug_and_version(self):
        (self.tmp / "Outros").mkdir()
        clients.add("beta", str(self.tmp / "Outros"))
        self.freeze(slug="zeta")
        self.freeze()
        self.freeze()
        self.freeze(slug="alfa")
        self.freeze("--client", "beta", expect=1)  # o project.json é do acme
        (self.project / "project.json").unlink()
        self.freeze("--client", "beta")
        listed = run_cli("template", "--action", "list")["templates"]
        self.assertEqual(
            [("acme", "alfa", 1), ("acme", "reels-acme", 1), ("acme", "reels-acme", 2), ("acme", "zeta", 1),
             ("beta", "reels-acme", 1)],
            [(row["client"], row["slug"], row["version"]) for row in listed],
        )  # fmt: skip
        self.assertEqual("cat:getbrolls/template/alfa@1", listed[0]["ref"])
        only = run_cli("template", "--action", "list", "--client", "beta")["templates"]
        self.assertEqual(["beta"], [row["client"] for row in only])

    def test_list_ignores_staging_and_the_lock(self):
        self.freeze()
        (self.slug_dir() / ".staging-deadbeef").mkdir()
        listed = run_cli("template", "--action", "list")["templates"]
        self.assertEqual([1], [row["version"] for row in listed])

    def test_fresh_template_is_intact(self):
        self.freeze()
        shown = self.show()
        self.assertEqual((True, []), (shown["intact"], shown["problems"]))
        self.assertEqual("cat:getbrolls/template/reels-acme@1", shown["template"]["ref"])
        raw = (self.slug_dir() / "1" / "template.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), shown["template_sha256"])

    def test_template_json_is_sealed_outside_itself_and_read_only(self):
        self.freeze()
        folder = self.slug_dir() / "1"
        raw = (folder / "template.json").read_bytes()
        self.assertEqual(
            f"{hashlib.sha256(raw).hexdigest()}  template.json\n",
            (folder / "template.sha256").read_text(encoding="utf-8"),
        )
        if os.name != "nt":
            self.assertEqual(0o444, stat.S_IMODE((folder / "template.sha256").stat().st_mode))

    def test_edited_template_json_is_not_intact(self):
        self.freeze()
        self.rewrite_template(lambda doc: doc.update(title="Trocado depois"), reseal=False)
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn("template.sha256", " ".join(shown["problems"]))

    def test_missing_or_broken_seal_is_not_intact(self):
        self.freeze()
        seal = self.writable(self.slug_dir() / "1" / "template.sha256")
        for text in ("não é hash\n", "0" * 63 + "  template.json\n"):
            with self.subTest(text=text):
                seal.write_text(text, encoding="utf-8")
                self.assertFalse(self.show()["intact"])
        seal.unlink()
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn("template.sha256", " ".join(shown["problems"]))

    def test_components_cannot_smuggle_licences_hidden_files_or_cross_folders(self):
        self.freeze()
        folder = self.slug_dir() / "1"
        for bad, kind in (
            ("components/sfx/whoosh.licenca.json", "sfx"),
            ("components/sfx/WHOOSH.LICENCA.JSON", "sfx"),
            ("components/sfx/.whoosh.wav", "sfx"),
            ("components/musica/whoosh.wav", "sfx"),
        ):
            with self.subTest(bad=bad):
                self.rewrite_template(lambda doc, bad=bad, kind=kind: doc["components"][0].update(file=bad, kind=kind))
                shown = templates.show("cat:getbrolls/template/reels-acme@1", "acme")
                self.assertFalse(shown["intact"])
                self.assertTrue(any("inseguro" in p or "schema" in p for p in shown["problems"]), shown["problems"])
        with self.assertRaises(ValueError):
            templates.safe_relative("components/musica/x.wav", kind="sfx")
        self.assertEqual(("components", "sfx", "x.wav"), templates.safe_relative("components/sfx/x.wav", kind="sfx"))
        del folder

    def test_tampered_component_is_named(self):
        self.freeze()
        self.writable(self.slug_dir() / "1" / "components" / "sfx" / "whoosh.wav").write_bytes(b"trocado")
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn("components/sfx/whoosh.wav", " ".join(shown["problems"]))

    def test_missing_and_extra_files_are_problems(self):
        self.freeze()
        target = self.slug_dir() / "1" / "components" / "marca" / "logo.svg"
        self.writable(target)
        target.unlink()
        (self.slug_dir() / "1" / "components" / "sfx" / "extra.wav").write_bytes(b"x")
        problems = " ".join(self.show()["problems"])
        self.assertIn("components/marca/logo.svg", problems)
        self.assertIn("components/sfx/extra.wav", problems)

    def test_traversal_in_a_component_file_is_refused_before_reading(self):
        self.freeze()
        for bad in ("components/../../client.json", "/etc/passwd", "components/sfx/../../../client.json",
                    "components\\sfx\\whoosh.wav", "C:/x", "components/outros/x.wav", "components/sfx"):  # fmt: skip
            with self.subTest(bad=bad):
                self.rewrite_template(lambda doc, bad=bad: doc["components"][0].update(file=bad))
                shown = templates.show("cat:getbrolls/template/reels-acme@1", "acme")
                self.assertFalse(shown["intact"])
                self.assertTrue(any("inseguro" in p or "schema" in p for p in shown["problems"]), shown["problems"])

    def test_template_json_that_disagrees_with_its_folder_is_not_intact(self):
        self.freeze()
        self.rewrite_template(lambda doc: doc.update(version=7, ref="cat:getbrolls/template/reels-acme@7"))
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn("versão", " ".join(shown["problems"]))

    def test_broken_template_json_is_not_intact(self):
        self.freeze()
        self.writable(self.slug_dir() / "1" / "template.json").write_text("{", encoding="utf-8")
        shown = self.show()
        self.assertEqual((False, None), (shown["intact"], shown["template"]))

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_linked_component_is_a_problem(self):
        self.freeze()
        target = self.slug_dir() / "1" / "components" / "sfx" / "whoosh.wav"
        self.writable(target)
        target.unlink()
        target.symlink_to(self.project / "assets" / "sfx" / "whoosh.wav")
        shown = self.show()
        self.assertFalse(shown["intact"])
        self.assertIn("link", " ".join(shown["problems"]))

    def test_unknown_or_bad_ref_is_a_clear_error(self):
        self.freeze()
        refused = self.show("cat:getbrolls/template/reels-acme@9", expect=1)
        self.assertIn("não existe", refused["error"])
        for bad in ("cat:getbrolls/template/%2E%2E@1", "cat:getbrolls/template/a%2Fb@1", "cat:getbrolls/template/x@01",
                    "cat:hyperframes/recipe/x@1", "scene:c01"):  # fmt: skip
            with self.subTest(bad=bad):
                self.assertEqual("USAGE_ERROR", self.show(bad, expect=2)["error_code"])
        self.assertIn("ghost", self.show(client="ghost", expect=1)["error"])

    def test_list_and_show_are_read_only_actions(self):
        described = run_cli("capabilities")
        command = next(row for row in described["commands"] if row["name"] == "template")
        self.assertEqual(("by_action", ["list", "show"]), (command["read_only"], command["read_only_actions"]))
