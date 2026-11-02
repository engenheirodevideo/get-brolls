"""Plugins oficiais: fonte em `plugins_official/`, pin gerado no core, fim de linha LF e o bytecode do instalador."""

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import __version__
from getbrolls.sdk import contracts, loader, official_pins
from getbrolls.sdk.manifest import read_manifest

# isort: split
import gen_official_pins

OFFICIAL = ROOT / "plugins_official"


def official_folders():
    return sorted(path for path in OFFICIAL.iterdir() if path.is_dir())


class OfficialSourceTests(unittest.TestCase):
    def test_shipped_pins_and_manifests_name_the_same_environments(self):
        ids = {folder.name for folder in official_folders()}
        self.assertEqual(set(contracts.SHIPPED_ENVIRONMENTS), set(official_pins.PINS))
        self.assertEqual(set(contracts.SHIPPED_ENVIRONMENTS), ids)
        for folder in official_folders():
            manifest = read_manifest(folder, official=True)
            with self.subTest(plugin=folder.name):
                self.assertEqual([folder.name], manifest["contributes"]["environments"])
                self.assertEqual(__version__, manifest["version"])
                self.assertEqual({"network": [], "env": [], "paths": [], "project_write": []}, manifest["permissions"])

    def test_the_pin_is_the_digest_of_the_folder(self):
        for folder in official_folders():
            with self.subTest(plugin=folder.name):
                self.assertEqual(official_pins.PINS[folder.name], loader.official_digest(folder))

    def test_the_generator_check_catches_a_changed_byte(self):
        tmp = Path(tempfile.mkdtemp(prefix="gb-pins-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        shutil.copytree(OFFICIAL, tmp / "plugins_official")
        (tmp / "scripts" / "getbrolls" / "sdk").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "getbrolls" / "sdk" / "official_pins.py", tmp / "scripts" / "getbrolls" / "sdk")

        def run(*flags):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = gen_official_pins.main([*flags, "--root", str(tmp)])
            return code, out.getvalue()

        self.assertEqual((0, "official_pins.py confere com plugins_official/.\n"), run("--check"))
        with (tmp / "plugins_official" / "hyperframes" / "plugin.py").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        self.assertEqual(1, run("--check")[0])
        self.assertEqual(0, run()[0])
        self.assertEqual(0, run("--check")[0])

    def test_official_sources_are_lf_everywhere(self):
        for path in sorted(OFFICIAL.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                with self.subTest(path=path.relative_to(ROOT).as_posix()):
                    self.assertNotIn(b"\r\n", path.read_bytes())
        git = shutil.which("git")
        if git is None or not (ROOT / ".git").exists():
            self.skipTest("sem git: o atributo de fim de linha só se confere num checkout")
        done = subprocess.run(
            [git, "-C", str(ROOT), "check-attr", "eol", "--", "plugins_official/hyperframes/plugin.py"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertTrue(done.stdout.strip().endswith("eol: lf"), done.stdout)


class InstallerBytecodeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="gb-official-")) / "hyperframes"
        self.addCleanup(shutil.rmtree, self.folder.parent, ignore_errors=True)
        shutil.copytree(OFFICIAL / "hyperframes", self.folder)
        self.pin = loader.official_digest(self.folder)

    def compiled(self, name="plugin.cpython-311.pyc"):
        cache = self.folder / "__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / name).write_bytes(b"\x00bytecode")

    def test_the_installer_cache_is_tolerated_and_out_of_the_pin(self):
        for name in ("plugin.cpython-311.pyc", "plugin.cpython-314.opt-1.pyc", "plugin.pypy311.pyc"):
            self.compiled(name)
        tolerated = loader.installer_bytecode(self.folder) or frozenset()
        self.assertEqual(4, len(tolerated))
        self.assertIsNone(loader.content_problem(self.folder, tolerated))
        self.assertEqual(self.pin, loader.official_digest(self.folder))

    def test_any_other_bytecode_is_not_tolerated(self):
        cases = {
            "loose pyc": lambda: (self.folder / "plugin.pyc").write_bytes(b"x"),
            "pyc without source": lambda: self.compiled("outro.cpython-311.pyc"),
            "other file in cache": lambda: self.compiled("notas.txt"),
            "nested cache": lambda: (self.folder / "sub" / "__pycache__").mkdir(parents=True),
        }
        for name, plant in cases.items():
            with self.subTest(case=name):
                shutil.rmtree(self.folder)
                shutil.copytree(OFFICIAL / "hyperframes", self.folder)
                plant()
                tolerated = loader.installer_bytecode(self.folder) or frozenset()
                self.assertEqual("bytecode", (loader.content_problem(self.folder, tolerated) or ("", ""))[0])

    def test_a_cache_that_is_a_file_or_a_link_is_not_tolerated(self):
        (self.folder / "__pycache__").write_bytes(b"x")
        self.assertIsNone(loader.installer_bytecode(self.folder))
        (self.folder / "__pycache__").unlink()
        if sys.platform != "win32":
            (self.folder / "__pycache__").symlink_to(self.folder.parent)
            self.assertIsNone(loader.installer_bytecode(self.folder))

    def test_a_changed_byte_changes_the_pin(self):
        with (self.folder / "README.md").open("a", encoding="utf-8") as stream:
            stream.write("x")
        self.assertNotEqual(self.pin, loader.official_digest(self.folder))

    def test_the_cache_of_a_shipped_example_is_tolerated_only_inside_the_package_data(self):
        example = self.folder.parent / "exporter_example"
        shutil.copytree(ROOT / "examples" / "plugins" / "exporter_example", example)
        (example / "__pycache__").mkdir()
        (example / "__pycache__" / "plugin.cpython-311.pyc").write_bytes(b"x")
        self.assertEqual(frozenset(), loader.shipped_bytecode(example))
        with mock.patch.object(loader._paths, "data_root", return_value=self.folder.parent):
            self.assertEqual(2, len(loader.shipped_bytecode(example)))
        with mock.patch.object(loader._paths, "data_root", side_effect=OSError):
            self.assertEqual(frozenset(), loader.shipped_bytecode(example))


if __name__ == "__main__":
    unittest.main()
