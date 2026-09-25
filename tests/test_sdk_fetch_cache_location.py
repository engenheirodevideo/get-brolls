"""O arquivo trazido pela rota de `fetch` é achado no cache do projeto atual: um
projeto movido reaproveita o próprio arquivo, e uma cópia nunca lê o cache do
projeto original (nem por uma entrada antiga com caminho absoluto)."""

import json
import shutil
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
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


if __name__ == "__main__":
    unittest.main()
