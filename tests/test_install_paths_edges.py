"""`getbrolls._paths` nas bordas: instalação sem dados para o manifesto, `pyvenv.cfg` sem `home`
e o `sys.argv` vazio de um processo embutido (sem o arquivo que o iniciou)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths as _test_paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

from getbrolls import _paths as paths
from getbrolls.errors import DataRootError

# pylint: disable=protected-access


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-paths-edges-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)


class ManifestEntriesTests(TmpCase):
    def test_an_unknown_installation_has_no_manifest_to_read(self):
        inst = paths.detect(self.tmp / "site" / "getbrolls")
        self.assertEqual("unknown", inst.origin)
        with self.assertRaises(DataRootError) as caught:
            paths.manifest_entries(inst)
        self.assertIn("não estão junto do pacote", str(caught.exception))
        self.assertNotIn(str(self.tmp), str(caught.exception))


class VenvBaseTests(TmpCase):
    def cfg(self, text):
        (self.tmp / "pyvenv.cfg").write_text(text, encoding="utf-8")
        return self.tmp

    def test_a_cfg_without_home_has_no_base_python(self):
        venv = self.cfg("include-system-site-packages = false\nversion = 3.11.9\nlinha sem igual\n")
        self.assertFalse(paths._venv_base_exists(venv))

    def test_home_is_found_after_other_keys_and_must_exist(self):
        base = self.tmp / "python-base"
        base.mkdir()
        venv = self.cfg(f"version = 3.11.9\nsem-separador\nHome = {base}\n")
        self.assertTrue(paths._venv_base_exists(venv))
        shutil.rmtree(base)
        self.assertFalse(paths._venv_base_exists(venv))


class StartedFromTests(unittest.TestCase):
    def test_an_empty_argv_never_matches_the_found_script(self):
        found = Path("C:/tools/getbrolls.exe")
        for argv in ([], [""]):
            with self.subTest(argv=argv), patch.object(paths.sys, "argv", argv):
                self.assertFalse(paths._started_from(found))

    def test_on_windows_an_embedded_process_never_counts_the_path_script_as_its_own(self):
        with (
            patch.object(paths.sys, "argv", []),
            patch.object(paths.shutil, "which", return_value=str(Path("/elsewhere/bin/getbrolls.exe"))),
        ):
            self.assertFalse(paths._console_script_is_ours("nt"))


if __name__ == "__main__":
    unittest.main()
