"""Configuração de distribuição: metadados, dados do wheel e allowlist do sdist.

Estático e rápido: lê `pyproject.toml` com `tomllib`, sem construir nada. O
`uv build` monta o sdist primeiro e o wheel a partir dele, então todo arquivo que
o wheel força para `_data/` precisa estar na allowlist do sdist.
"""

import os
import subprocess
import sys
import tempfile
import tomllib
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import __version__
from getbrolls import _paths as paths

DATA_PREFIX = "getbrolls/_data/"
LEAKS = ("tests", ".github", "eval", "CLAUDE.md", "AGENTS.md", "GEMINI.md")
SHIPPED_DOCS = {"docs/RULES.md", "docs/BRIEF.md"}


def _config() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _covers(parent: str, child: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def _force_include() -> dict[str, str]:
    return _config()["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]


def _sdist_allowlist() -> list[str]:
    return _config()["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"]


def _run(args, cwd, **env):
    return subprocess.run(
        args, cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True, timeout=60, check=False
    )


class DistributionMetadata(unittest.TestCase):
    def test_distribution_metadata(self):
        config = _config()
        project = config["project"]
        self.assertEqual("getbrolls", project["name"])
        self.assertEqual(">=3.11", project["requires-python"])
        self.assertEqual([], project["dependencies"])
        self.assertEqual(["version"], project["dynamic"])
        self.assertEqual({"getbrolls": "getbrolls.cli:entrypoint"}, project["scripts"])
        self.assertEqual("scripts/getbrolls/__init__.py", config["tool"]["hatch"]["version"]["path"])
        self.assertEqual("hatchling.build", config["build-system"]["build-backend"])
        self.assertTrue(any(req.startswith("hatchling") for req in config["build-system"]["requires"]))
        self.assertEqual(["scripts/getbrolls"], config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])


class WheelData(unittest.TestCase):
    def test_force_include_ships_every_required_data_file(self):
        mapping = _force_include()
        self.assertEqual(DATA_PREFIX + "MANIFEST", mapping.get(paths.CHECKOUT_MANIFEST))
        for source, target in mapping.items():
            self.assertTrue(target.startswith(DATA_PREFIX), (source, target))
            self.assertTrue((ROOT / source).exists(), source)
        for entry in paths.REQUIRED_DATA:
            sources = [source for source in mapping if _covers(source, entry)]
            self.assertTrue(sources, entry)
            for source in sources:
                shipped = mapping[source] + entry[len(source) :]
                self.assertEqual(DATA_PREFIX + entry, shipped, entry)


class SdistAllowlist(unittest.TestCase):
    def test_sdist_allowlist_covers_the_wheel_sources(self):
        allowlist = _sdist_allowlist()
        needed = [*_force_include(), "scripts/getbrolls", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"]
        for source in needed:
            self.assertTrue(any(_covers(item, source) for item in allowlist), source)
        for item in allowlist:
            self.assertTrue((ROOT / item).exists(), item)
            for leak in LEAKS:
                self.assertFalse(_covers(leak, item) or _covers(item, leak), (item, leak))
            if _covers("docs", item):
                self.assertIn(item, SHIPPED_DOCS)


class ModuleEntryPoints(unittest.TestCase):
    def test_python_dash_m_getbrolls_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = _run([sys.executable, "-m", "getbrolls", "--version"], tmp, PYTHONPATH=str(ROOT / "scripts"))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn(__version__, done.stdout)

    def test_instagram_pairs_runs_as_a_module_and_as_a_script(self):
        script = ROOT / "scripts" / "getbrolls" / "instagram_pairs.py"
        with tempfile.TemporaryDirectory() as tmp:
            as_module = _run(
                [sys.executable, "-m", "getbrolls.instagram_pairs", "--help"], tmp, PYTHONPATH=str(ROOT / "scripts")
            )
            as_script = _run([sys.executable, str(script), "--help"], tmp)
        self.assertEqual(0, as_module.returncode, as_module.stderr)
        self.assertEqual(0, as_script.returncode, as_script.stderr)


if __name__ == "__main__":
    unittest.main()
