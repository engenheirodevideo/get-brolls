"""Exemplo do SDK `exporter_example`: o exportador lista as cenas em CSV (`cortes.csv`) e num EXPORT.md.

Função pura (sem arquivo, rede, relógio nem mídia); todo texto do plano é dado não confiável: no CSV,
uma célula nunca vira fórmula nem quebra a linha; no Markdown, a marcação sai escapada.
"""

import copy
import csv
import io
import json
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT

from getbrolls.sdk import ExportResult, PluginError
from getbrolls.sdk.manifest import read_manifest

EXAMPLE = ROOT / "examples" / "plugins" / "exporter_example"
PLAN = json.loads((ROOT / "examples" / "plans" / "reels.plan.json").read_text(encoding="utf-8"))


def load_plugin():
    """Roda o `plugin.py` como o loader roda: compilado da fonte, num módulo novo."""
    source = (EXAMPLE / "plugin.py").read_bytes()
    namespace: dict = {"__name__": "getbrolls_plugins.exporter_example"}
    exec(compile(source, str(EXAMPLE / "plugin.py"), "exec", dont_inherit=True), namespace)  # noqa: S102  # pylint: disable=exec-used
    return namespace


class ManifestTests(unittest.TestCase):
    def test_manifest_declares_only_the_exporter_and_no_permission(self):
        manifest = read_manifest(EXAMPLE)
        self.assertEqual(
            ("exporter_example", ["exporter_example"]), (manifest["id"], manifest["contributes"]["exporters"])
        )
        self.assertEqual({key: [] for key in manifest["permissions"]}, manifest["permissions"])
        self.assertEqual([], [kind for kind, names in manifest["contributes"].items() if names and kind != "exporters"])

    def test_plugins_check_runs_the_exporter_against_the_sample_plan(self):
        report = run_cli("plugins", "--action", "check", "--path", EXAMPLE)
        self.assertTrue(report["ok"], report)
        self.assertEqual("exporter_example", report["id"])


class CortesTests(unittest.TestCase):
    def setUp(self):
        self.cortes = load_plugin()["cortes"]

    def rows(self, plan):
        result = self.cortes(plan, {"args": {}})
        self.assertIsInstance(result, ExportResult)
        self.assertEqual(["EXPORT.md", "cortes.csv"], sorted(result.files))
        self.assertEqual([], result.media)
        raw = result.files["cortes.csv"]
        self.assertTrue(raw.startswith("\ufeff"), "o Excel em português só lê UTF-8 com BOM")
        return list(csv.reader(io.StringIO(raw.removeprefix("\ufeff"), newline=""), delimiter=";")), result.files[
            "EXPORT.md"
        ]

    def test_one_row_per_scene_in_plan_order(self):
        rows, export_md = self.rows(copy.deepcopy(PLAN))
        self.assertEqual(["cena", "inicio_s", "duracao_s", "layout", "titulo", "fala"], rows[0])
        self.assertEqual([scene["id"] for scene in PLAN["scenes"]], [row[0] for row in rows[1:]])
        self.assertEqual(f"{PLAN['scenes'][0]['duration_s']:.2f}", rows[1][2])
        self.assertIn(f"{len(PLAN['scenes'])} cena(s)", export_md)

    def test_the_csv_is_utf8_with_bom_and_semicolon_for_excel_in_portuguese(self):
        result = self.cortes(copy.deepcopy(PLAN), {"args": {}})
        raw = result.files["cortes.csv"].encode("utf-8")
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        header = raw.decode("utf-8-sig").split("\n", 1)[0]
        self.assertEqual("cena;inicio_s;duracao_s;layout;titulo;fala", header)

    def test_hostile_roteiro_text_becomes_the_exact_neutralised_cell(self):
        # Uma cena por valor hostil: o plano de exemplo tem só 4 cenas, e um zip curto largaria valores.
        hostile = [
            ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
            ("+cmd", "'+cmd"),
            ("-2+3", "'-2+3"),
            ("@SUM(A1)", "'@SUM(A1)"),
            ("linha um\nlinha dois", "linha um linha dois"),
            ("linha um\r\nlinha dois", "linha um linha dois"),
            ("\t=1", "'=1"),
            ("\r=1", "'=1"),
            ('ação; "oi"', 'ação; "oi"'),
        ]
        plan = copy.deepcopy(PLAN)
        base = plan["scenes"][0]
        plan["scenes"] = []
        for number, (text, _) in enumerate(hostile, start=1):
            scene = copy.deepcopy(base)
            scene.update(id=f"c{number:02d}", title=text, speech_clean=text)
            plan["scenes"].append(scene)
        rows, _ = self.rows(plan)
        self.assertEqual(len(hostile) + 1, len(rows))
        for (text, expected), row in zip(hostile, rows[1:], strict=True):
            with self.subTest(text=text):
                self.assertEqual(6, len(row), row)
                self.assertEqual((expected, expected), (row[4], row[5]))

    def test_hostile_text_in_the_scene_id_is_neutralised_too(self):
        plan = copy.deepcopy(PLAN)
        plan["scenes"][0]["id"] = "=cmd|' /C calc'!A1\n"
        rows, _ = self.rows(plan)
        self.assertEqual("'=cmd|' /C calc'!A1", rows[1][0])

    def test_markdown_markup_from_the_plan_is_escaped(self):
        plan = copy.deepcopy(PLAN)
        plan["meta"]["tema"] = "# [clique](http://x) <script>"
        _, export_md = self.rows(plan)
        self.assertIn("\\# \\[clique\\]\\(http://x\\) \\<script\\>", export_md)

    def test_an_unknown_export_version_is_a_plugin_error(self):
        plan = {**copy.deepcopy(PLAN), "export_version": 3}
        with self.assertRaisesRegex(PluginError, "export_version 3 não é suportado"):
            self.cortes(plan, {"args": {}})

    def test_the_example_never_names_hyperframes(self):
        for name in ("plugin.py", "README.md", "getbrolls-plugin.json"):
            with self.subTest(name=name):
                self.assertNotIn("hyperframes", (EXAMPLE / name).read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
