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
from pathlib import Path

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


def _wheel() -> dict:
    return _config()["tool"]["hatch"]["build"]["targets"]["wheel"]


def _force_include() -> dict[str, str]:
    return _wheel()["force-include"]


def _wheel_mapping() -> dict[str, str]:
    """Origem no repositório → destino no wheel: pastas por `sources`, arquivos por `force-include`."""
    return {**_wheel()["sources"], **_force_include()}


def _sdist_allowlist() -> list[str]:
    return _config()["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"]


def _run(args, cwd, **env):
    return subprocess.run(
        args,
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
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
        self.assertEqual("getbrolls", _wheel()["sources"]["scripts/getbrolls"])
        self.assertIn("scripts/getbrolls", _wheel()["only-include"])


class WheelData(unittest.TestCase):
    def test_wheel_mapping_ships_every_required_data_file(self):
        mapping = _wheel_mapping()
        self.assertEqual(DATA_PREFIX + "MANIFEST", mapping.get(paths.CHECKOUT_MANIFEST))
        for source, target in mapping.items():
            if source != "scripts/getbrolls":
                self.assertTrue(target.startswith(DATA_PREFIX), (source, target))
            self.assertTrue((ROOT / source).exists(), source)
        self.assertEqual(sorted(_wheel()["sources"]), sorted(_wheel()["only-include"]))
        for entry in paths.REQUIRED_DATA:
            sources = [source for source in mapping if _covers(source, entry)]
            self.assertTrue(sources, entry)
            for source in sources:
                shipped = mapping[source] + entry[len(source) :]
                self.assertEqual(DATA_PREFIX + entry, shipped, entry)


class NoSecretsShipped(unittest.TestCase):
    """`force-include` ignora o `exclude` do hatch: por ele só passam arquivos com nome, nunca pastas."""

    ENV_PATTERNS = ("**/.env", "**/.env.*")

    def test_force_include_is_only_named_files(self):
        for source in _force_include():
            self.assertTrue((ROOT / source).is_file(), source)
            self.assertFalse(Path(source).name.startswith(".env"), source)

    def test_every_build_excludes_env_files(self):
        build = _config()["tool"]["hatch"]["build"]
        for label, excludes in (("build", build["exclude"]), ("sdist", build["targets"]["sdist"]["exclude"])):
            for pattern in (*self.ENV_PATTERNS, "**/__pycache__", "**/*.pyc"):
                self.assertIn(pattern, excludes, label)
        self.assertNotIn("exclude", _wheel(), "o wheel herda o `exclude` global")


class SdistAllowlist(unittest.TestCase):
    def test_sdist_allowlist_covers_the_wheel_sources(self):
        allowlist = _sdist_allowlist()
        needed = [*_wheel_mapping(), "scripts/getbrolls", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"]
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
