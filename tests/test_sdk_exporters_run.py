"""`run_exporter` e o validador de `ExportResult`: uma regra por teste, isolamento e disponibilidade."""

import io
import os
import sys
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _plugin_pins import pin_plugins
from test_sdk_exporters_resolvers import EXPORT_CODE
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.sdk import ExporterSpec, ExportResult, MediaRequest, PluginError, exporters, loader, testing
from getbrolls.sdk.exporters import (
    FILE_MAX_BYTES,
    MINIMAL_PLAN,
    ExportValidationError,
    ValidatedExport,
    find_local_paths,
    run_exporter,
    validate_export_result,
)
from getbrolls.sdk.registry import Registry, get_registry, reset_registry

PLAN = {"version": 1, "title": "Praia", "out_dir": "exports/demo_html/001", "scenes": [{"id": "s1"}], "media": []}


def good(**overrides):
    values: dict[str, Any] = {
        "files": {"index.html": "<p>ok</p>", "css/site.css": "body{}"},
        "media": [MediaRequest("m1", "assets/som.wav")],
        "notes": ["Abra index.html"],
    }
    values.update(overrides)
    return ExportResult(**values)


class ValidatorTests(unittest.TestCase):
    def refused(self, result, fragment=None):
        with self.assertRaises(ExportValidationError) as caught:
            validate_export_result(result)
        if fragment is not None:
            self.assertIn(fragment, str(caught.exception))
        return str(caught.exception)

    def test_a_good_result_becomes_core_types(self):
        checked = validate_export_result(good())
        self.assertIs(ValidatedExport, type(checked))
        self.assertEqual({"index.html": "<p>ok</p>", "css/site.css": "body{}"}, checked.files)
        self.assertEqual((("m1", "assets/som.wav"),), checked.media)
        self.assertEqual(("Abra index.html",), checked.notes)

    def test_result_and_containers_must_have_the_exact_types(self):
        class Files(dict):
            pass

        self.refused(cast("ExportResult", {"files": {}}), "ExportResult")
        self.refused(good(files=Files({"index.html": "x"})), "files tem que ser um dict")
        self.refused(good(media=(MediaRequest("m1", "assets/a.wav"),)), "media tem que ser uma lista")
        self.refused(good(media=[("m1", "assets/a.wav")]), "MediaRequest")
        self.refused(good(notes="nota"), "notes tem que ser uma lista")
        self.refused(good(notes=[3]), "cada item tem que ser texto")
        self.refused(good(files={"index.html": b"x"}), "tem que ser texto")
        self.refused(good(files={3: "x"}), "caminho tem que ser texto")

    def test_paths_are_relative_posix_names(self):
        cases = {
            "absolute": "/index.html",
            "parent": "a/../index.html",
            "only parent": "../index.html",
            "backslash": "css\\site.css",
            "drive": "C:/index.html",
            "empty segment": "css//site.css",
            "leading dot": ".config/site.css",
            "hidden file": "css/.site.css",
            "trailing dot": "css./site.css",
            "reserved stem": "CON.html",
            "reserved stem in folder": "nul/site.css",
            "reserved COM0": "COM0.md",
            "reserved lpt0": "docs/lpt0.txt",
            "too long": "d" * 100 + "/" + "d" * 100 + "/" + "e" * 40 + ".html",
            "too deep": "a/b/c/d/e/f/g.html",
        }
        for label, path in cases.items():
            with self.subTest(label):
                self.refused(good(files={path: "x"}))

    def test_file_extensions_are_a_lowercase_allowlist(self):
        for path in ("app.py", "index.HTML", "LEIAME", "video.mp4"):
            with self.subTest(path=path):
                self.refused(good(files={path: "x"}), "extensão")
        for path in ("a.html", "a.json", "a.css", "a.js", "a.md", "a.txt"):
            with self.subTest(path=path):
                validate_export_result(good(files={path: "x"}))

    def test_collisions_ignore_case_and_files_cannot_be_folders(self):
        self.refused(good(files={"Index.html": "a", "index.html": "b"}), "mesmo caminho")
        self.refused(
            good(media=[MediaRequest("m1", "assets/Som.wav"), MediaRequest("m2", "assets/som.wav")]), "mesmo caminho"
        )
        self.refused(good(files={"a.html": "x", "a.html/b.css": "y"}), "arquivo e pasta")
        self.refused(
            good(media=[MediaRequest("m1", "assets/a.wav"), MediaRequest("m2", "assets/a.wav/b.wav")]),
            "arquivo e pasta",
        )

    def test_assets_is_reserved_for_media(self):
        self.refused(good(files={"assets/leia.txt": "x"}), "assets/")
        self.refused(good(files={"Assets/leia.txt": "x"}), "assets/")
        for dest in ("media/som.wav", "som.wav", "Assets/som.wav", "assets"):
            with self.subTest(dest=dest):
                self.refused(good(media=[MediaRequest("m1", dest)]))
        self.refused(good(media=[MediaRequest("m1", "/assets/som.wav")]), "relativo")

    def test_text_must_be_strict_utf8_without_nul(self):
        self.refused(good(files={"index.html": "a\x00b"}), "NUL")
        self.refused(good(files={"index.html": "\ud800"}), "UTF-8")
        self.refused(good(media=[MediaRequest("m\x00", "assets/a.wav")]), "NUL")

    def test_caps(self):
        many = {f"f{index}.txt": "x" for index in range(201)}
        self.refused(good(files=many), "200 arquivos")
        self.refused(good(files={"big.txt": "x" * (FILE_MAX_BYTES + 1)}), "MB")
        near = "x" * (FILE_MAX_BYTES - 10)
        self.refused(good(files={f"f{index}.txt": near for index in range(5)}), "no total")
        self.refused(good(media=[MediaRequest(f"m{i}", f"assets/{i}.wav") for i in range(501)]), "500 pedidos")
        self.refused(good(media=[MediaRequest("m" * 201, "assets/a.wav")]), "media_id")
        self.refused(good(media=[MediaRequest("", "assets/a.wav")]), "media_id")
        self.refused(good(notes=["nota"] * 51), "50 itens")

    def test_snapshot_failure_is_a_normal_refusal(self):
        def changing(value):
            raise RuntimeError("dictionary changed size during iteration")

        with self.assertRaises(ExportValidationError) as caught:
            exporters._snapshot({"index.html": "x"}, changing)
        self.assertIn("mudou enquanto era conferido", str(caught.exception))

    def test_checked_files_are_a_snapshot(self):
        files = {"index.html": "x"}
        checked = validate_export_result(good(files=files))
        files["outro.html"] = "y"
        self.assertEqual({"index.html": "x"}, checked.files)
        self.assertIsNot(files, checked.files)

    def test_notes_are_sanitized_to_one_line(self):
        checked = validate_export_result(good(notes=["primeira\nsegunda\u202e", "x" * 400]))
        self.assertEqual("primeira segunda", checked.notes[0])
        self.assertLessEqual(len(checked.notes[1]), 300)

    def test_note_line_is_inert_and_prefixed(self):
        line = exporters.note_line("demo", "Veja [aqui](http://exemplo.com) <img src=x> **já**")
        self.assertTrue(line.startswith("Nota do plugin demo: "))
        self.assertIn("\\[aqui\\]", line)
        self.assertIn("\\<img", line)
        self.assertIn("\\*\\*já\\*\\*", line)


def export_registry(export, status="enabled"):
    registry = Registry()
    registry.add_exporter(ExporterSpec("demo_html", "Exporta HTML", export), owner="demo")
    registry.plugins["demo"] = {
        "id": "demo",
        "status": status,
        "reason": None,
        "contributes": {"exporters": ["demo_html"]},
    }
    return registry


class RunExporterTests(unittest.TestCase):
    def run_with(self, export, plan=PLAN, status="enabled"):
        return run_exporter(export_registry(export, status), "demo_html", plan, {"args": {}})

    def failure(self, export):
        with self.assertRaises(ValueError) as caught:
            self.run_with(export)
        return str(caught.exception)

    def test_result_is_validated_and_logged(self):
        with self.assertLogs("getbrolls.sdk", level="INFO") as cm:
            checked = self.run_with(lambda plan, options: good())
        self.assertEqual((("m1", "assets/som.wav"),), checked.media)
        self.assertIn("event=plugin_export", "\n".join(cm.output))

    def test_exporter_gets_copies_and_cannot_change_the_plan(self):
        plan = {"title": "Praia", "scenes": [{"id": "s1"}]}
        options = {"args": {}}
        seen = []

        def export(got_plan, got_options):
            seen.append(got_plan is plan or got_options is options)
            got_plan["title"] = "Trocado"
            got_plan["scenes"].append({"id": "intruso"})
            got_options["args"]["x"] = "1"
            return good()

        run_exporter(export_registry(export), "demo_html", plan, options)
        self.assertEqual([False], seen)
        self.assertEqual({"title": "Praia", "scenes": [{"id": "s1"}]}, plan)
        self.assertEqual({"args": {}}, options)

    def test_plugin_error_text_is_shown_other_exceptions_only_by_type(self):
        def says(plan, options):
            raise PluginError("Configure DEMO_TOKEN antes de exportar.")

        def leaks(plan, options):
            raise RuntimeError("segredo-do-plugin")

        self.assertEqual("Plugin demo: Configure DEMO_TOKEN antes de exportar.", self.failure(says))
        message = self.failure(leaks)
        self.assertIn("Plugin demo:", message)
        self.assertIn("RuntimeError", message)
        self.assertNotIn("segredo-do-plugin", message)

    def test_system_exit_and_custom_base_exceptions_are_isolated(self):
        class Fuga(BaseException):
            pass

        def exits(plan, options):
            raise SystemExit(0)

        def escapes(plan, options):
            raise Fuga

        self.assertIn("SystemExit", self.failure(exits))
        self.assertIn("Fuga", self.failure(escapes))

    def test_plugin_stdout_goes_to_stderr(self):
        def noisy(plan, options):
            sys.stdout.write("barulho do plugin\n")
            return good()

        with patch("sys.stdout", new=io.StringIO()) as out, patch("sys.stderr", new=io.StringIO()) as err:
            self.run_with(noisy)
        self.assertNotIn("barulho", out.getvalue())
        self.assertIn("barulho", err.getvalue())

    def test_invalid_result_names_the_plugin_and_the_rule(self):
        message = self.failure(lambda plan, options: good(files={"/etc/x.html": "x"}))
        self.assertTrue(message.startswith("Plugin demo: o exportador demo_html devolveu um resultado fora das regras"))
        self.assertIn("relativo", message)

    def test_unknown_or_unloaded_exporter_is_refused(self):
        registry = export_registry(lambda plan, options: good())
        with self.assertRaises(ValueError) as caught:
            run_exporter(registry, "outro", PLAN, {})
        self.assertIn("não existe", str(caught.exception))
        with self.assertRaises(ValueError):
            run_exporter(registry, "../x", PLAN, {})
        with self.assertRaises(ValueError) as caught:
            self.run_with(lambda plan, options: good(), status="failed")
        self.assertIn("que está failed", str(caught.exception))

    def test_plan_must_be_json(self):
        with self.assertRaises(ValueError) as caught:
            self.run_with(lambda plan, options: good(), plan={"x": float("nan")})
        self.assertIn("JSON", str(caught.exception))

    def test_plan_nested_too_deep_is_refused_not_crashed(self):
        deep: dict = {}
        for _ in range(10_000):
            deep = {"x": deep}
        # Conforme a versão do Python, o `json` aguenta esse aninhamento ou levanta
        # RecursionError; no segundo caso a recusa é a mesma de um plano que não é JSON.
        try:
            self.run_with(lambda plan, options: good(), plan=deep)
        except ValueError as exc:
            self.assertIn("JSON", str(exc))
        with (
            patch.object(exporters.json, "dumps", side_effect=RecursionError),
            self.assertRaises(ValueError) as caught,
        ):
            self.run_with(lambda plan, options: good(), plan=deep)
        self.assertIn("O plano do export não é JSON válido", str(caught.exception))


class LocalPathScanTests(unittest.TestCase):
    def test_absolute_local_paths_are_found_anywhere(self):
        home = str(Path.home())
        plan = {
            "title": "Praia",
            "out_dir": "exports/demo_html/001",
            "credit": "https://example.com/home/fotos",
            "relative": "clips/tmp/a.mp4",
            "scenes": [
                # Montados na hora: o repositório não guarda caminho de máquina, nem de exemplo.
                {"clip": "/" + "home/ana/clips/a.mp4"},
                {"clip": "C:\\" + "Users\\ana\\a.mp4"},
                {"clip": "$HOME/Musica/a.wav"},
                {"clip": home + "/Musica/b.wav"},
                {"clip": "~/Musica/c.wav"},
            ],
        }
        self.assertEqual(
            [
                "$['scenes'][0]['clip']",
                "$['scenes'][1]['clip']",
                "$['scenes'][2]['clip']",
                "$['scenes'][3]['clip']",
                "$['scenes'][4]['clip']",
            ],
            find_local_paths(plan),
        )

    def test_home_only_counts_as_a_whole_path(self):
        cases = (
            ("/root", {"a": "veja github.com/rootless-containers", "b": "/rootless e /root.bak"}, []),
            ("/root", {"a": "/root/clips/a.mp4"}, ["$['a']"]),
            ("/srv/bruno", {"a": "/srv/brunoteca", "b": "x/srv/bruno/a", "c": "https://s.com/srv/bruno/a"}, []),
            (
                "/srv/bruno",
                {"a": "/srv/bruno", "b": "src='/srv/bruno/a'", "c": "dir=/srv/bruno\\a", "d": "em /srv/bruno/a"},
                ["$['a']", "$['b']", "$['c']", "$['d']"],
            ),
        )
        for home, value, expected in cases:
            with self.subTest(home=home, value=value), patch.object(Path, "home", return_value=Path(home)):
                self.assertEqual(expected, find_local_paths(value))

    def test_a_validated_export_of_a_clean_plan_has_no_local_paths(self):
        def export(plan, options):
            return ExportResult({"index.html": f"<h1>{plan['title']}</h1>", "plan.json": str(plan)})

        checked = run_exporter(export_registry(export), "demo_html", PLAN, {"args": {}})
        self.assertEqual([], find_local_paths(PLAN))
        self.assertEqual([], find_local_paths(checked.files))
        self.assertEqual([], find_local_paths(MINIMAL_PLAN))


class CheckExporterTests(unittest.TestCase):
    def test_check_runs_the_minimal_plan_through_the_same_validator(self):
        testing.check_exporter(ExporterSpec("demo_html", "Exporta", lambda plan, options: good()))
        with self.assertRaises(AssertionError) as caught:
            testing.check_exporter(
                ExporterSpec("demo_html", "Exporta", lambda plan, options: good(media=[MediaRequest("m", "a.wav")]))
            )
        self.assertIn("plano mínimo", str(caught.exception))
        self.assertIn("assets/", str(caught.exception))

    def test_check_reads_the_shape_first(self):
        for spec, fragment in (
            (object(), "api.exporter"),
            (ExporterSpec("demo_html", " ", lambda plan, options: good()), "description"),
            (ExporterSpec("demo_html", "Exporta", cast("Any", lambda plan: good())), "(plan, options)"),
        ):
            with self.subTest(fragment=fragment), self.assertRaises(AssertionError) as caught:
                testing.check_exporter(spec)
            self.assertIn(fragment, str(caught.exception))


BAD_DEST_CODE = EXPORT_CODE.replace(
    'return ExportResult({"index.html": "<p>ok</p>"})',
    'from getbrolls.sdk import MediaRequest\n    return ExportResult({"index.html": "x"}, [MediaRequest("m1", "media/a.wav")])',
)


class ExporterAvailabilityTests(LoaderTestCase):
    def manifest(self):
        return {**MANIFEST, "contributes": {"exporters": ["demo_html"], "resolvers": ["demo"]}}

    def test_plugins_check_catches_a_bad_dest(self):
        self.assertNotEqual(EXPORT_CODE, BAD_DEST_CODE)
        folder = self.install(self.manifest(), code=BAD_DEST_CODE)
        with self.assertRaises(ValueError) as caught:
            loader.trial_load(folder)
        self.assertIn("contrato", str(caught.exception))
        self.assertIn("assets/", str(caught.exception))

    def test_excluded_by_gb_plugins_names_the_variable(self):
        self.install(self.manifest(), code=EXPORT_CODE)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "outro"}), self.assertRaises(ValueError) as caught:
            run_exporter(get_registry(), "demo_html", PLAN, {"args": {}})
        self.assertIn("Exportador demo_html é do plugin demo", str(caught.exception))
        self.assertIn("GB_PLUGINS", str(caught.exception))

    def test_suspended_plugin_says_so(self):
        folder = self.install(self.manifest(), code=EXPORT_CODE)
        pin_plugins("demo")
        (folder / "plugin.py").write_text(EXPORT_CODE + "\n# mudou\n", encoding="utf-8")
        reset_registry()
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}), self.assertRaises(ValueError) as caught:
            run_exporter(get_registry(), "demo_html", PLAN, {"args": {}})
        self.assertIn("que está suspended", str(caught.exception))
        self.assertIn("plugins --action list", str(caught.exception))

    def test_enabled_plugin_exports(self):
        self.install(self.manifest(), code=EXPORT_CODE)
        pin_plugins("demo")
        with patch.dict(os.environ, {"GB_PLUGINS": "demo"}):
            checked = run_exporter(get_registry(), "demo_html", PLAN, {"args": {}})
        self.assertEqual({"index.html": "<p>ok</p>"}, checked.files)


if __name__ == "__main__":
    unittest.main()
