"""`getbrolls.sdk` é preguiçoso (PEP 562): importar não carrega submódulos e os 18 nomes resolvem sob demanda."""

import pickle
import subprocess
import sys
import unittest
from pathlib import Path

import _isolation  # (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import sdk
from getbrolls.sdk import contracts, registry

SCRIPTS = ROOT / "scripts"


def _run_isolated(code):
    return subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(SCRIPTS)!r})\n{code}"],
        check=False,
        capture_output=True,
        text=True,
        env={"GB_HOME": str(Path(_isolation.__file__).parent)},
    )


class LazyInitTest(unittest.TestCase):
    def test_import_loads_no_submodule(self):
        proc = _run_isolated(
            "import sys\n"
            "import getbrolls.sdk\n"
            "print(sorted(m for m in sys.modules if m.startswith('getbrolls.sdk.')))\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "[]")

    def test_attribute_access_loads_only_its_source(self):
        proc = _run_isolated(
            "import sys\nimport getbrolls.sdk as s\ns.Preset\n"
            "print('getbrolls.sdk.registry' in sys.modules, 'getbrolls.sdk.contracts' in sys.modules)\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False True")

    def test_every_name_is_identical_to_its_source(self):
        self.assertEqual(len(sdk.__all__), 18)
        for name in sdk.__all__:
            source = registry if name == "get_registry" else contracts
            self.assertIs(getattr(sdk, name), getattr(source, name), name)

    def test_star_import_and_dir(self):
        namespace = {}
        exec("from getbrolls.sdk import *", namespace)  # noqa: S102  # pylint: disable=exec-used
        for name in sdk.__all__:
            self.assertIn(name, namespace)
            self.assertIn(name, dir(sdk))

    def test_a_resolved_name_is_cached_in_the_module_globals(self):
        proc = _run_isolated(
            "import getbrolls.sdk as s\n"
            "before = 'Preset' in vars(s)\n"
            "first = s.Preset\n"
            "after = vars(s)['Preset'] is first\n"
            "registry_before = 'get_registry' in vars(s)\n"
            "getter = s.get_registry\n"
            "print(before, after, registry_before, vars(s)['get_registry'] is getter)\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False True False True")

    def test_dir_lists_every_public_name_even_before_it_is_resolved(self):
        proc = _run_isolated(
            "import getbrolls.sdk as s\n"
            "listed = dir(s)\n"
            "print(all(name in listed for name in s.__all__), listed == sorted(set(listed)), 'Preset' in vars(s))\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "True True False")

    def test_submodule_imports_work(self):
        from getbrolls.sdk import testing  # pylint: disable=import-outside-toplevel

        self.assertTrue(hasattr(testing, "__name__"))
        self.assertIs(sdk.testing, testing)

    def test_unknown_attribute_raises(self):
        with self.assertRaises(AttributeError):
            sdk.not_a_name  # noqa: B018  # pylint: disable=pointless-statement

    def test_pickle_and_isinstance_unaffected(self):
        self.assertIs(pickle.loads(pickle.dumps(sdk.PluginError)), contracts.PluginError)  # noqa: S301
        self.assertTrue(issubclass(sdk.PluginError, contracts.PluginError))
        self.assertIsInstance(sdk.PluginError("x"), contracts.PluginError)
        restored = pickle.loads(pickle.dumps(sdk.PluginError("boom")))  # noqa: S301
        self.assertIsInstance(restored, sdk.PluginError)

    def test_plugin_import_style_from_docs(self):
        proc = _run_isolated(
            "from getbrolls.sdk import ExportResult, MediaRequest, PluginError, ProviderCapabilities, RouteResult\n"
            "import getbrolls.sdk.contracts as c\n"
            "assert PluginError is c.PluginError\n"
            "print('ok')\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
