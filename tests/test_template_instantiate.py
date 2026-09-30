"""`init --template`: projeto novo a partir de uma versão de template do cliente, com `template.lock.json`."""

import argparse
import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT
from _schemas import close
from test_template_freeze import TemplateCase, _schema, reseal_template

from getbrolls import assets, cli, export, layout, roteiro, runtime, templates
from getbrolls.sdk import jsonschema

REF = "cat:getbrolls/template/reels-acme@1"


class InstantiateCase(TemplateCase):
    def setUp(self):
        super().setUp()
        self.freeze("--title", "Reels padrão ACME")
        self.new = self.tmp / "video-02"

    def init(self, *extra, expect=0, tema: str | None = "Novo: vídeo #2"):
        args = ["init", "--template", REF, "--client", "acme", *extra]
        if tema is not None:
            args += ["--tema", tema]
        return run_cli(*args, project=self.new, expect=expect)

    def lock(self):
        return json.loads((self.new / "template.lock.json").read_text(encoding="utf-8"))


class InstantiateTests(InstantiateCase):
    def test_tree_roteiro_and_lock_are_valid(self):
        out = self.init()
        self.assertEqual(REF, out["template"])
        doc = layout.load_project(self.new)
        assert doc is not None
        self.assertEqual(("acme", REF), (doc["client"], doc["template"]))
        self.assertEqual(({"width": 1080, "height": 1920}, {"num": 30, "den": 1}), (doc["canvas"], doc["fps"]))
        for folder in ("aroll", "assets/sfx", "assets/outros", "broll", "analysis"):
            self.assertTrue((self.new / folder).is_dir(), folder)
        parsed = roteiro.parse((self.new / "ROTEIRO.md").read_text(encoding="utf-8"), plugins=frozenset())
        self.assertEqual(
            ("reels", "Novo: vídeo #2", "acme", "draft", True),
            tuple(parsed.meta[key] for key in ("genero", "tema", "cliente", "status", "legenda")),
        )
        self.assertEqual(["Gancho", "Marca"], [scene.title for scene in parsed.scenes])
        self.assertEqual("{fala da cena}", parsed.scenes[0].speech)
        lock = self.lock()
        self.assertEqual([], jsonschema.errors(lock, _schema("template_lock")))
        self.assertEqual([], jsonschema.errors(lock, close(_schema("template_lock"))))
        self.assertEqual((REF, "acme"), (lock["ref"], lock["client"]))
        shown = templates.show(REF, "acme")
        self.assertEqual(shown["template_sha256"], lock["template_sha256"])
        run_cli("init-rules", "--format", "reels", project=self.new)
        check = run_cli("roteiro", "--action", "check", project=self.new)
        self.assertEqual(2, len(check["scenes"]))

    def test_client_owned_component_is_not_copied_and_resolves_from_the_client(self):
        self.init()
        rows = {row["name"]: row for row in self.lock()["components"]}
        self.assertEqual("client", rows["logo"]["installed_as"])
        self.assertEqual([], list((self.new / "assets" / "marca").iterdir()))
        self.assertEqual("client", assets.resolve(self.new, "marca", "logo")["origin"])

    def test_copied_sfx_has_no_sidecar_and_warns(self):
        out = self.init()
        rows = {row["name"]: row for row in self.lock()["components"]}
        self.assertEqual("assets/sfx/whoosh.wav", rows["whoosh"]["installed_as"])
        self.assertEqual("assets/lettering/titulo-grande.json", rows["titulo-grande"]["installed_as"])
        self.assertEqual(b"RIFF-whoosh", (self.new / "assets" / "sfx" / "whoosh.wav").read_bytes())
        self.assertFalse((self.new / "assets" / "sfx" / "whoosh.licenca.json").exists())
        codes = [(w["code"], w["message"]) for w in out["warnings"]]
        licence = [message for code, message in codes if code == "LICENCE_NOT_TRANSFERRED"]
        self.assertEqual(1, len(licence), codes)
        self.assertIn("whoosh", licence[0])
        found = assets.resolve(self.new, "sfx", "whoosh")
        self.assertEqual("project", found["origin"])
        self.assertIn("licença não registrada", " ".join(found["warnings"]))

    def test_no_manifest_candidates_clips_or_approvals(self):
        self.init()
        brolls = self.new / "brolls"
        for name in ("manifest.json", "candidates", "clips", "previews", "events.jsonl", "roteiro-reviews.jsonl"):
            self.assertFalse((brolls / name).exists(), name)
        if brolls.exists():
            self.assertLessEqual(
                {p.name for p in brolls.iterdir()}, {".command.lock", "getbrolls.log", "diagnostics.jsonl"}
            )
        self.assertNotIn("revisado", (self.new / "ROTEIRO.md").read_text(encoding="utf-8"))

    def test_flags_override_the_template_canvas_and_fps(self):
        self.init("--canvas", "1920x1080", "--fps", "30000/1001")
        doc = layout.load_project(self.new)
        assert doc is not None
        self.assertEqual(({"width": 1920, "height": 1080}, {"num": 30000, "den": 1001}), (doc["canvas"], doc["fps"]))

    def test_tampered_template_is_refused_and_nothing_is_created(self):
        target = self.slug_dir() / "1" / "components" / "sfx" / "whoosh.wav"
        target.chmod(0o644)
        target.write_bytes(b"trocado")
        refused = self.init(expect=1)
        self.assertIn("components/sfx/whoosh.wav", refused["error"])
        self.assertFalse((self.new / "project.json").exists())
        self.assertFalse((self.new / "ROTEIRO.md").exists())

    def test_edited_template_json_is_refused_and_nothing_is_created(self):
        path = self.slug_dir() / "1" / "template.json"
        path.chmod(0o644)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["slots"][0]["title"] = "Cena trocada"
        path.write_text(json.dumps(doc), encoding="utf-8")
        refused = self.init(expect=1)
        self.assertIn("template.sha256", refused["error"])
        self.assertFalse(self.new.exists())

    def test_io_error_mid_copy_logs_to_the_home_fallback_and_leaves_no_folder(self):
        argv = [
            "getbrolls",
            "init",
            "--template",
            REF,
            "--client",
            "acme",
            "--tema",
            "Novo",
            "--project",
            str(self.new),
        ]
        err = io.StringIO()
        with (
            patch.object(cli.sys, "argv", argv),
            patch.object(templates, "copy_hashed", side_effect=OSError("leitura falhou")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            code = cli.entrypoint()
        self.assertEqual(1, code, err.getvalue())
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
        argv = [
            "getbrolls",
            "init",
            "--template",
            REF,
            "--client",
            "acme",
            "--tema",
            "Novo",
            "--project",
            str(self.new),
        ]
        with (
            patch.object(cli.sys, "argv", argv),
            patch.object(templates, "copy_hashed", side_effect=OSError("leitura falhou")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = cli.entrypoint()
        self.assertEqual(1, code)
        self.assertEqual(["notas.txt"], sorted(p.name for p in self.new.iterdir() if p.name != "brolls"))
        self.assertFalse((self.new / "project.json").exists())

    def test_template_needs_client_and_tema(self):
        refused = run_cli("init", "--template", REF, "--tema", "x", project=self.new, expect=2)
        self.assertIn("--client", refused["error"])
        refused = self.init(expect=2, tema=None)
        self.assertIn("--tema", refused["error"])
        refused = run_cli("init", "--tema", "x", project=self.new, expect=2)
        self.assertIn("--template", refused["error"])
        for bad in ("cat:getbrolls/template/%2E%2E@1", "cat:getbrolls/template/x@0", "cat:outro/template/x@1"):
            with self.subTest(bad=bad):
                run_cli("init", "--template", bad, "--client", "acme", "--tema", "x", project=self.new, expect=2)
        for name in ("project.json", "ROTEIRO.md", "template.lock.json", "assets"):
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
        refused = run_cli("init", "--template", REF, "--client", "acme", "--tema", "x", project=other, expect=1)
        self.assertIn("ROTEIRO.md", refused["error"])
        self.assertEqual("meu", (other / "ROTEIRO.md").read_text(encoding="utf-8"))
        self.assertFalse((other / "project.json").exists())

    def test_unknown_version_or_other_client_is_refused(self):
        refused = run_cli(
            "init", "--template", "cat:getbrolls/template/reels-acme@4", "--client", "acme", "--tema", "x",
            project=self.new, expect=1,
        )  # fmt: skip
        self.assertIn("não existe", refused["error"])
        refused = run_cli("init", "--template", REF, "--client", "ghost", "--tema", "x", project=self.new, expect=1)
        self.assertIn("ghost", refused["error"])


class RegistryCheckTests(InstantiateCase):
    def test_plain_init_with_an_unregistered_client_is_refused(self):
        refused = run_cli("init", "--client", "ghost", project=self.new, expect=1)
        self.assertIn("ghost", refused["error"])
        self.assertFalse((self.new / "project.json").exists())
        run_cli("init", "--client", "acme", project=self.new)


class LockDriftTests(InstantiateCase):
    def test_changed_component_warns_on_status_and_by_function(self):
        self.init()
        self.assertEqual([], templates.lock_warnings(self.new))
        (self.new / "assets" / "sfx" / "whoosh.wav").write_bytes(b"editado")
        (self.client / "components" / "marca" / "logo.svg").write_bytes(b"<svg>novo</svg>")
        warnings = templates.lock_warnings(self.new)
        self.assertEqual(2, len(warnings), warnings)
        self.assertTrue(any("whoosh" in w for w in warnings))
        self.assertTrue(any("logo" in w for w in warnings))
        status = run_cli("status", project=self.new)
        drift = [w for w in status["warnings"] if w["code"] == "TEMPLATE_LOCK_DRIFT"]
        self.assertEqual(2, len(drift), status["warnings"])

    def test_missing_component_and_broken_lock_warn(self):
        self.init()
        (self.new / "assets" / "sfx" / "whoosh.wav").unlink()
        self.assertTrue(any("whoosh" in w for w in templates.lock_warnings(self.new)))
        (self.new / "template.lock.json").write_text("{", encoding="utf-8")
        self.assertIn("template.lock.json", " ".join(templates.lock_warnings(self.new)))

    def test_a_deleted_copy_is_reported_as_gone_even_if_another_one_resolves(self):
        self.init()
        self.put(self.client / "components" / "sfx", "whoosh.wav", b"outro whoosh do cliente")
        (self.new / "assets" / "sfx" / "whoosh.wav").unlink()
        warnings = [w for w in templates.lock_warnings(self.new) if "whoosh" in w]
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn("sumiu", warnings[0])
        self.assertNotIn("mudou", warnings[0])

    def test_unchanged_components_are_not_hashed_again(self):
        self.init()
        rows = {row["name"]: row for row in self.lock()["components"]}
        self.assertEqual((self.new / "assets" / "sfx" / "whoosh.wav").stat().st_size, rows["whoosh"]["size"])
        with patch.object(templates, "sha256_file", side_effect=AssertionError("hash completo")):
            self.assertEqual([], templates.lock_warnings(self.new))
        path = self.new / "assets" / "sfx" / "whoosh.wav"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        self.assertEqual([], templates.lock_warnings(self.new))

    def test_project_without_lock_has_no_warning(self):
        run_cli("init", project=self.new)
        self.assertEqual([], templates.lock_warnings(self.new))


if __name__ == "__main__":
    unittest.main()


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
            patch.object(templates, "lock_warnings", return_value=["sfx mudou"]),
        ):
            out = export.run(args)
        self.assertEqual(["sfx mudou", "aviso do export", "cliente ghost fora do registro"], out["warnings"])
        # O evento de diagnóstico guarda os avisos com o código, como no status.
        self.assertEqual(
            [("TEMPLATE_LOCK_DRIFT", "sfx mudou"), ("CLIENT_UNREGISTERED", "cliente ghost fora do registro")],
            [(w["code"], w["message"]) for w in event["warnings"]],
        )
        self.assertTrue(event.get("warnings_in_result"))


class HostileTemplateTests(InstantiateCase):
    """Um template.json íntegro no schema, mas com texto que tentaria injetar linhas no roteiro."""

    def rewrite(self, change):
        path = self.slug_dir() / "1" / "template.json"
        path.chmod(0o644)
        if not hasattr(self, "original"):
            self.original = path.read_text(encoding="utf-8")  # pylint: disable=attribute-defined-outside-init
        doc = json.loads(self.original)
        change(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")
        reseal_template(path.parent)

    def test_injected_lines_are_refused(self):
        changes = (
            lambda doc: doc["slots"][0].update(placeholder="[SFX: extra]"),
            lambda doc: doc["slots"][0].update(placeholder="## Cena escondida"),
            lambda doc: doc["slots"][0].update(title="Gancho\n[BROLL: x]"),
            lambda doc: doc.update(genero="reels\ntema: outro"),
        )
        for change in changes:
            with self.subTest(change=change):
                self.rewrite(change)
                refused = self.init(expect=1)
                self.assertIn("template", refused["error"])
                self.assertFalse((self.new / "ROTEIRO.md").exists())
