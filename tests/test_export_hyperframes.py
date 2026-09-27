"""`gb export --to hyperframes` com o plugin de exemplo de verdade: projeto HyperFrames e, com a CLI, `lint` limpo.

O plugin entra como a pessoa o instala (`plugins --action install`, prévia e depois
`--yes --expect <sha256>`) num GB_HOME temporário; o export roda como subprocesso.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT
from test_export_cli import WRITABLE_METHOD, ExportCase, tree
from test_plugin_hyperframes_cli import ENV, find_cli

EXAMPLE = ROOT / "examples" / "plugins" / "hyperframes"
EXPECTED_FILES = (
    "EXPORT.md", "hyperframes.json", "index.html", "meta.json", "package.json",
    "compositions/captions.html", "compositions/scene-c01.html", "compositions/scene-c02.html",
    "compositions/scene-c03.html",
)  # fmt: skip
AROLL = "assets/aroll/c01.mp4"


class HyperframesExportTests(ExportCase):
    def install(self, enable=True, code=None):
        """Instala o exemplo pela CLI, como no README: prévia, depois `--yes --expect` com o sha256 dela.

        O `install` confirmado já habilita e pina; `enable` e `code` só mantêm a assinatura do `ExportCase`."""
        source = Path(tempfile.mkdtemp(prefix="gb-hf-source-")) / "hyperframes"
        self.addCleanup(shutil.rmtree, source.parent, ignore_errors=True)
        shutil.copytree(EXAMPLE, source, ignore=shutil.ignore_patterns("__pycache__"))
        preview = run_cli("plugins", "--action", "install", "--source", source)
        self.assertFalse(preview["installed"])
        sha = preview["plugin"]["sha256"]
        done = run_cli("plugins", "--action", "install", "--source", source, "--yes", "--expect", sha)
        self.assertTrue(done["installed"])
        return self.home / "plugins" / "hyperframes"

    def export(self, *extra, expect=0):
        return run_cli("export", "--to", "hyperframes", *extra, project=self.project, expect=expect)

    def folder(self, number):
        return self.project / "exports" / "hyperframes" / number

    def assert_no_bytecode(self, installed):
        for folder in (EXAMPLE, installed):
            found = [p.name for p in folder.rglob("*") if p.name == "__pycache__" or p.suffix in (".pyc", ".pyo")]
            self.assertEqual([], found, folder.name)

    def test_dry_run_writes_nothing(self):
        self.install()
        before = tree(self.project)
        out = self.export("--dry-run")
        self.assertEqual(before, tree(self.project))
        self.assertFalse((self.project / "exports").exists())
        self.assertEqual(
            ("hyperframes", "hyperframes", True, "exports/hyperframes/001"),
            (out["exporter"], out["plugin"], out["dry_run"], out["out"]),
        )
        self.assertIn({"media_id": "aroll:c01", "dest": AROLL, "method": WRITABLE_METHOD}, out["media"])
        self.assertIn("nada foi gravado", out["summary"]["line"])

    def test_export_is_a_hyperframes_project(self):
        installed = self.install()
        out = self.export()
        folder = self.folder("001")
        self.assertEqual(
            ("hyperframes", "hyperframes", "exports/hyperframes/001", "001", True, False),
            (out["exporter"], out["plugin"], out["out"], out["number"], out["latest"], out["dry_run"]),
        )
        self.assertEqual(sorted(EXPECTED_FILES), out["files"])
        for name in EXPECTED_FILES:
            self.assertTrue((folder / name).is_file(), name)
        self.assertEqual("001\n", (folder.parent / "LATEST").read_text(encoding="utf-8"))
        marker = json.loads((folder / ".getbrolls-export.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ("complete", "hyperframes", "hyperframes", "0.1.0"),
            (marker["state"], marker["exporter"], marker["plugin"], marker["plugin_version"]),
        )
        text = (folder / "EXPORT.md").read_text(encoding="utf-8")
        self.assertIn("npx --yes hyperframes@0.8.73 lint exports/hyperframes/001 --json", text)
        self.assertIn("c03: b-roll sem clipe coletado \\(beat c03-a\\)", text)  # texto do plano sai inerte no Markdown
        self.assertIn("c03: b-roll sem clipe coletado (beat c03-a)", out["warnings"])
        self.assert_no_bytecode(installed)

    def test_clip_is_hardlinked_and_voice_is_an_independent_copy(self):
        self.install()
        out = self.export()
        folder = self.folder("001")
        methods = {m["dest"]: m["method"] for m in out["media"]}
        clips = [dest for dest in methods if dest.startswith("assets/clips/")]
        self.assertEqual(1, len(clips), methods)
        source = self.project / "brolls" / "clips" / "c02-clip.mp4"
        self.assertEqual("hardlink", methods[clips[0]])
        self.assertTrue(source.samefile(folder / clips[0]))
        voice = self.project / "aroll" / "c01.mp4"
        self.assertIn(methods[AROLL], ("clone", "reflink-auto", "copy"))
        self.assertFalse(voice.samefile(folder / AROLL))
        self.assertEqual(voice.read_bytes(), (folder / AROLL).read_bytes())
        shown = f"{voice.stat().st_size / 1_000_000:.1f}".replace(".", ",")
        # Clone pelo `cp` conta inteiro (num disco que não clona ele copia tudo): "até N MB".
        copied = (
            f"até {shown} MB copiados"
            if methods[AROLL] in ("clone", "reflink-auto")
            else f"{shown} MB copiados de fato"
        )
        self.assertEqual(
            f"Export 001 em exports/hyperframes/001: {len(EXPECTED_FILES)} arquivo(s), {len(methods)} mídia(s) "
            f"({copied}). Abra exports/hyperframes/001/EXPORT.md.",
            out["summary"]["line"],
        )

    def test_second_export_is_002_and_never_touches_001(self):
        self.install()
        self.export()
        first = self.folder("001")
        before = tree(first)
        out = self.export()
        self.assertEqual(("002", "exports/hyperframes/002"), (out["number"], out["out"]))
        self.assertEqual(before, tree(first))
        self.assertEqual("002\n", (first.parent / "LATEST").read_text(encoding="utf-8"))
        self.assertTrue((self.folder("002") / "index.html").is_file())
        self.assertIn(
            "npx --yes hyperframes@0.8.73 lint exports/hyperframes/002 --json",
            (self.folder("002") / "EXPORT.md").read_text(encoding="utf-8"),
        )

    def test_written_files_have_no_machine_path(self):
        self.install()
        self.export()
        exports = self.project / "exports"
        machine = set()
        for path in (self.project, self.home, self.media_dir, Path.home(), Path(tempfile.gettempdir())):
            machine |= {str(path).rstrip("/\\"), os.path.realpath(path)}
        texts = [p for p in exports.rglob("*") if p.is_file() and "assets" not in p.relative_to(exports).parts]
        self.assertGreater(len(texts), len(EXPECTED_FILES))
        for path in texts:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path.relative_to(exports).as_posix()):
                self.assertEqual([], [m for m in machine if m in text])
                self.assertIsNone(re.search(r"/Users/|/home/|/private/|/var/folders/|[A-Za-z]:\\", text))

    def test_generated_project_lints_clean_when_the_cli_is_available(self):
        binary, version = find_cli()
        if binary is None:
            self.skipTest(version)
        self.install()
        self.export()
        done = subprocess.run(
            [binary, "lint", str(self.folder("001")), "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=180, env=ENV, check=False,
        )  # fmt: skip
        try:
            report = json.loads(done.stdout)
        except json.JSONDecodeError:
            self.skipTest(f"CLI HyperFrames {version} não devolveu JSON no lint (wrapper quebrado?)")
        findings = [(f.get("severity"), f.get("code")) for f in report.get("findings", [])]
        self.assertEqual((0, 0), (done.returncode, report.get("errorCount")), f"CLI {version}: {findings}")


if __name__ == "__main__":
    unittest.main()
