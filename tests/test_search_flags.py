"""`search --shot` e `search --dry-run`: ligar ao beat, e diagnosticar sem sujar o projeto.

Quatro dos cinco executores da rodada cega bateram em `search --shot` recusado embora
SKILL.md, `references/` e `brief --beat` prometessem a flag; três poluíram as contagens
de `status` com buscas que eram só diagnóstico.
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)

from getbrolls import providers
from getbrolls.commands import execute
from getbrolls.models import candidate
from getbrolls.runtime import audited


def found(n=2):
    return [
        candidate("youtube", f"id{i}", f"Vídeo {i}", f"https://www.youtube.com/watch?v=aaaaaaaaaa{i}") for i in range(n)
    ]


def args(project, **extra):
    base = dict(
        command="search",
        project=str(project),
        env_file=None,
        confirm_format_change=False,
        provider="youtube",
        query="foguete SLS decolando",
        limit=5,
        intent="literal",
        shot=None,
        dry_run=False,
    )
    base.update(extra)
    return types.SimpleNamespace(**base)


def manifest(project):
    """Manifesto como está no disco; `{"items": []}` quando o projeto nem foi criado."""
    path = Path(project) / "brolls" / "manifest.json"
    if not path.is_file():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


class ShotFlag(unittest.TestCase):
    def test_shot_suffixes_the_id_and_records_the_beat_like_resolve_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found()):
                result = audited(args(tmp, shot="abertura"), execute)
            for item in result["items"]:
                self.assertTrue(item["id"].endswith(":shot:abertura"), item["id"])
                self.assertEqual("abertura", item["shot"])
            saved = manifest(tmp)["items"]
            self.assertEqual({"abertura"}, {c["shot"] for c in saved})

    def test_without_shot_nothing_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp), execute)
            self.assertNotIn("shot", result["items"][0])
            self.assertNotIn(":shot:", result["items"][0]["id"])

    def test_a_shot_with_a_path_separator_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("../fuga", "com espaço", "a/b", ""):
                with self.subTest(bad=bad):
                    with patch.object(providers, "search", return_value=found(1)):
                        if bad == "":
                            # Vazio é "não informado", não erro: o comportamento antigo.
                            audited(args(tmp + "/vazio", shot=bad), execute)
                            continue
                        with self.assertRaises(Exception) as caught:
                            audited(args(tmp, shot=bad), execute)
                    self.assertIn("--shot", str(caught.exception))


class DryRun(unittest.TestCase):
    def test_dry_run_lists_results_without_touching_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(3)):
                first = audited(args(tmp), execute)
            before = manifest(tmp)
            self.assertEqual(3, len(before["items"]))
            with patch.object(providers, "search", return_value=found(3)):
                result = audited(args(tmp, dry_run=True), execute)
            self.assertTrue(result["dry_run"])
            self.assertEqual(3, len(result["items"]))
            self.assertIn("nada foi registrado", result["note"])
            # Byte por byte: a busca de diagnóstico não pode mexer no projeto.
            self.assertEqual(before, manifest(tmp))
            self.assertEqual(len(first["items"]), len(before["items"]))

    def test_dry_run_on_an_empty_project_registers_nothing_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(2)):
                result = audited(args(tmp, dry_run=True), execute)
            self.assertEqual(2, len(result["items"]))
            self.assertEqual([], manifest(tmp)["items"])

    def test_dry_run_still_carries_the_shot_in_the_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp, dry_run=True, shot="abertura"), execute)
            self.assertEqual("abertura", result["items"][0]["shot"])
            self.assertEqual([], manifest(tmp)["items"])

    def test_a_normal_search_reports_dry_run_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(providers, "search", return_value=found(1)):
                result = audited(args(tmp), execute)
            self.assertFalse(result["dry_run"])
            self.assertNotIn("nada foi registrado", result.get("note") or "")


if __name__ == "__main__":
    unittest.main()
