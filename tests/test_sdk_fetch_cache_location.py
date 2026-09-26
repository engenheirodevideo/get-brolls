"""O arquivo trazido pela rota de `fetch` é achado no cache do projeto atual: um
projeto movido reaproveita o próprio arquivo, e uma cópia nunca lê o cache do
projeto original (nem por uma entrada antiga com caminho absoluto)."""

import json
import os
import shutil
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _media import skip_unless_ffmpeg
from test_sdk_route_fetch import FetchRouteCase

from getbrolls.runtime import OperationError


@skip_unless_ffmpeg
class FetchCacheLocationTests(FetchRouteCase):
    def first_fetch(self):
        self.enable()
        ident = self.gb("search", "--provider", "demo", "--query", "mar")["items"][0]["id"]
        self.approve_range(ident, 0, 1)
        self.gb("fetch", "--candidate", ident)
        self.assertEqual(["demo:1"], self.calls_made())
        return ident

    def index(self, project):
        return project / ".getbrolls-sources" / "index.json"

    def relocate(self, name, copy=False):
        target = self.project.parent / (self.project.name + name)
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        if copy:
            shutil.copytree(self.project, target)
        else:
            shutil.move(str(self.project), str(target))
        return target

    def test_index_stores_a_path_relative_to_the_cache(self):
        self.first_fetch()
        data = json.loads(self.index(self.project).read_text(encoding="utf-8"))
        (entry,) = data["demo:1#fetch"]
        self.assertEqual(entry["path"], entry["path"].split("/")[-1])

    def test_moved_project_reuses_its_own_file(self):
        ident = self.first_fetch()
        self.project = self.relocate("-movido")
        self.approve_range(ident, 1, 2)
        done = self.gb("fetch", "--candidate", ident)
        self.assertTrue(done["output"]["verified"])
        self.assertEqual(["demo:1"], self.calls_made())

    def test_copy_never_reads_the_original_cache(self):
        ident = self.first_fetch()
        original = self.project
        self.project = self.relocate("-copia", copy=True)
        cache = self.project / ".getbrolls-sources"
        # Entrada no formato antigo (absoluto, apontando para o original) e sem o
        # arquivo na cópia: o original continua lá, mas a cópia não pode usá-lo.
        data = json.loads(self.index(self.project).read_text(encoding="utf-8"))
        for entries in data.values():
            for entry in entries:
                if isinstance(entry, dict) and entry.get("path"):
                    name = entry["path"].split("/")[-1]
                    entry["path"] = str(original / ".getbrolls-sources" / name)
                    (cache / name).unlink(missing_ok=True)
        self.index(self.project).write_text(json.dumps(data), encoding="utf-8")
        self.approve_range(ident, 1, 2)
        with self.assertRaises(OperationError) as caught:
            self.gb("fetch", "--candidate", ident)
        self.assertIn("--reacquire", str(caught.exception))
        self.assertEqual(["demo:1"], self.calls_made())

    def test_legacy_absolute_entry_is_found_by_name_in_this_cache(self):
        ident = self.first_fetch()
        original = self.project
        self.project = self.relocate("-legado", copy=True)
        data = json.loads(self.index(self.project).read_text(encoding="utf-8"))
        for entries in data.values():
            for entry in entries:
                if isinstance(entry, dict) and entry.get("path"):
                    entry["path"] = str(original / ".getbrolls-sources" / entry["path"].split("/")[-1])
        self.index(self.project).write_text(json.dumps(data), encoding="utf-8")
        shutil.rmtree(original / ".getbrolls-sources")
        self.approve_range(ident, 1, 2)
        self.assertTrue(self.gb("fetch", "--candidate", ident)["output"]["verified"])
        self.assertEqual(["demo:1"], self.calls_made())

    def refetch_after(self, ident, rewrite):
        """Reescreve cada entrada de fetch do índice com `rewrite(cache, entry)` e tenta
        um novo intervalo: sem arquivo válido no cache, o fetch é recusado."""
        cache = self.project / ".getbrolls-sources"
        data = json.loads(self.index(self.project).read_text(encoding="utf-8"))
        for entries in data.values():
            for entry in entries:
                if isinstance(entry, dict) and entry.get("path"):
                    rewrite(cache, entry)
        self.index(self.project).write_text(json.dumps(data), encoding="utf-8")
        self.approve_range(ident, 1, 2)
        with self.assertRaises(OperationError) as caught:
            self.gb("fetch", "--candidate", ident)
        self.assertIn("--reacquire", str(caught.exception))
        self.assertEqual(["demo:1"], self.calls_made())

    def test_drive_relative_name_is_not_accepted(self):
        ident = self.first_fetch()

        def drive_relative(cache, entry):
            name = entry["path"]
            (cache / name).rename(cache / ("C:" + name))
            entry["path"] = "C:" + name

        self.refetch_after(ident, drive_relative)

    @unittest.skipIf(os.name == "nt", "symlink exige privilégio no Windows")
    def test_symlink_planted_in_the_cache_is_not_followed(self):
        ident = self.first_fetch()
        outside = self.project.parent / (self.project.name + "-fora.bin")
        self.addCleanup(outside.unlink, missing_ok=True)

        def plant_link(cache, entry):
            real = cache / entry["path"]
            outside.write_bytes(real.read_bytes())
            real.unlink()
            real.symlink_to(outside)

        self.refetch_after(ident, plant_link)


if __name__ == "__main__":
    unittest.main()
