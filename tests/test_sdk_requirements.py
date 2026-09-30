"""`requires` do manifesto: o que falta aparece no `check` e no `doctor`, com a dica certa, sem instalar nada."""

import json
import platform
import sys
import unittest
from importlib import metadata
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls import _paths
from getbrolls.sdk import requirements

EMPTY = {"python": [], "binaries": [], "runtimes": {}, "services": []}
MISSING_DIST = "gbtest-distribuicao-que-nao-existe"


def _manifest(**requires):
    return {"requires": {**EMPTY, **requires}}


class PythonRequirementTests(unittest.TestCase):
    def test_installed_python_package_is_ok(self):
        with patch.object(metadata, "version", return_value="3.2.0"):
            report = requirements.report(_manifest(python=["psycopg[binary]>=3.1"]))
        self.assertTrue(report["ok"])
        self.assertEqual(
            [{"requirement": "psycopg[binary]>=3.1", "name": "psycopg", "installed": "3.2.0", "ok": True}],
            report["python"],
        )
        self.assertIsNone(report["hint"])

    def test_real_distribution_lookup(self):
        present = next(iter(metadata.distributions())).metadata["Name"]
        report = requirements.report(_manifest(python=[present, MISSING_DIST]))
        self.assertEqual([metadata.version(present), None], [row["installed"] for row in report["python"]])
        self.assertEqual([True, False], [row["ok"] for row in report["python"]])
        self.assertFalse(report["ok"])

    def test_missing_package_gives_uv_hint_in_wheel_mode_and_pip_hint_in_checkout(self):
        with patch.object(requirements, "installed_version", side_effect=lambda name: "1.0" if name == "pip" else None):
            wheel, checkout = self._hints(_manifest(python=[f"{MISSING_DIST}>=1.0", "pip"]))
        self.assertIn(f'uv tool install getbrolls --with "{MISSING_DIST}>=1.0"', wheel)
        self.assertIn(f'pipx inject getbrolls "{MISSING_DIST}>=1.0"', wheel)
        self.assertNotIn('"pip"', wheel)
        self.assertIn(f'-m pip install "{MISSING_DIST}>=1.0"', checkout)
        self.assertIn(sys.executable, checkout)
        self.assertNotIn("uv tool", checkout)

    @staticmethod
    def _hints(manifest):
        with patch.object(_paths, "origin", return_value="wheel"):
            wheel = requirements.report(manifest)["hint"] or ""
        with patch.object(_paths, "origin", return_value="checkout"):
            checkout = requirements.report(manifest)["hint"] or ""
        return wheel, checkout

    def test_version_range_is_checked_and_prerelease_is_tolerated(self):
        cases = (("3.0.9", False), ("3.1.0", True), ("3.2.0rc1", True), ("4.0", False), ("3.1.post1", True))
        for installed, expected in cases:
            with self.subTest(installed=installed), patch.object(metadata, "version", return_value=installed):
                row = requirements.report(_manifest(python=["psycopg>=3.1,<4"]))["python"][0]
            self.assertIs(expected, row["ok"])
            self.assertEqual(installed, row["installed"])

    def test_leading_version(self):
        self.assertEqual("3.2.0", requirements.leading_version("3.2.0rc1"))
        self.assertEqual("1.2.3", requirements.leading_version("1.2.3.4"))
        self.assertEqual("2024.1", requirements.leading_version("2024.1+local"))
        self.assertEqual("0", requirements.leading_version("dev"))


class BinaryAndRuntimeTests(unittest.TestCase):
    def test_binary_presence_uses_which_and_never_runs_it(self):
        def found(name):
            return "/bin/x" if name == "node" else None

        with (
            patch("shutil.which", side_effect=found) as which,
            patch("subprocess.run", side_effect=AssertionError("nunca executa")),
            patch("subprocess.Popen", side_effect=AssertionError("nunca executa")),
        ):
            report = requirements.report(_manifest(binaries=["node", "gbtest-sem-binario"]))
        self.assertEqual(
            [{"name": "node", "found": True}, {"name": "gbtest-sem-binario", "found": False}], report["binaries"]
        )
        self.assertEqual(2, which.call_count)
        self.assertFalse(report["ok"])

    def test_python_runtime_range_is_checked(self):
        current = platform.python_version()
        ok = requirements.report(_manifest(runtimes={"python": ">=3.11"}))
        self.assertEqual([{"name": "python", "spec": ">=3.11", "version": current, "ok": True}], ok["runtimes"])
        self.assertTrue(ok["ok"])
        too_new = requirements.report(_manifest(runtimes={"python": ">=99"}))
        self.assertIs(False, too_new["runtimes"][0]["ok"])
        self.assertFalse(too_new["ok"])

    def test_other_runtimes_are_unverified(self):
        report = requirements.report(_manifest(runtimes={"node": ">=18"}, services=["postgres"]))
        self.assertEqual([{"name": "node", "spec": ">=18", "version": None, "ok": None}], report["runtimes"])
        self.assertEqual(["postgres"], report["services"])
        self.assertTrue(report["ok"])

    def test_manifest_without_requires_is_ok(self):
        report = requirements.report({})
        self.assertEqual({**EMPTY, "runtimes": [], "ok": True, "hint": None}, report)


class CheckAndDoctorTests(LoaderTestCase):
    def env(self):
        return {"GB_HOME": str(self.home)}

    def test_check_plugin_includes_requires_report(self):
        folder = self.install({**MANIFEST, "requires": {"python": [MISSING_DIST], "binaries": ["gbtest-sem-binario"]}})
        out = run_cli("plugins", "--action", "check", "--path", folder, env=self.env())
        self.assertTrue(out["ok"])
        self.assertIs(False, out["requires"]["ok"])
        self.assertEqual(MISSING_DIST, out["requires"]["python"][0]["name"])
        self.assertIn(MISSING_DIST, out["requires"]["hint"])

    def test_doctor_lists_plugin_requirements_without_changing_ready(self):
        clean = run_cli("doctor", env=self.env())
        self.install({**MANIFEST, "requires": {"python": [MISSING_DIST]}})
        doctor = run_cli("doctor", env=self.env())
        row = next(r for r in doctor["plugins"] if r["id"] == "demo")
        self.assertIs(False, row["requires"]["ok"])
        self.assertIn(MISSING_DIST, doctor["summary"]["plugins_requires"])
        self.assertEqual(clean["ready"], doctor["ready"])
        self.assertNotIn(MISSING_DIST, json.dumps(doctor["summary"].get("missing", []), ensure_ascii=False))

    def test_doctor_is_quiet_when_every_requirement_is_met(self):
        self.install({**MANIFEST, "requires": {"runtimes": {"python": ">=3"}, "services": ["postgres"]}})
        doctor = run_cli("doctor", env=self.env())
        self.assertTrue(next(r for r in doctor["plugins"] if r["id"] == "demo")["requires"]["ok"])
        self.assertNotIn("plugins_requires", doctor["summary"])


if __name__ == "__main__":
    unittest.main()
