"""Runtime compartilhado: uma versão por parte, marcador de pronto e destino da instalação."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

from getbrolls import _paths as paths


def _touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fake_wheel(base):
    """Pacote instalado com os arquivos de dependência do runtime."""
    pkg = base / "site" / "getbrolls"
    data = pkg / "_data"
    _touch(data / "MANIFEST", "")
    _touch(data / "requirements.txt", "yt-dlp==1\n")
    _touch(data / "package.json", '{"name": "t"}\n')
    _touch(data / "package-lock.json", "{}\n")
    return paths.detect(pkg)


def _fake_checkout(base):
    root = base / "repo"
    for sentinel in ("scripts/gb.py", "docs/RULES.md", "assets/brand-logo.png"):
        _touch(root / sentinel)
    _touch(root / "requirements.txt", "yt-dlp==1\n")
    _touch(root / "package.json", "{}\n")
    _touch(root / "package-lock.json", "{}\n")
    pkg = root / "scripts" / "getbrolls"
    pkg.mkdir(parents=True)
    return paths.detect(pkg)


def _clean_env(**values):
    names = ("GB_HOME", "GETBROLLS_HOME", "GB_RUNTIME_DIR", "GB_ENV_FILE")
    env = {key: value for key, value in os.environ.items() if key not in names}
    env.update(values)
    return env


def _build_venv(root, home=None):
    """`.venv` mínima: o `pyvenv.cfg` e o Python da venv que o `part_ready` confere."""
    home = home if home is not None else Path(sys.executable).parent
    _touch(root / ".venv" / "pyvenv.cfg", f"home = {home}\nversion = 3\n")
    _touch(paths.venv_python(root / ".venv"))


class _Layout(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with  # limpo no addCleanup
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home = self.base / "home"
        self.inst = _fake_wheel(self.base)
        self.data = self.base / "site" / "getbrolls" / "_data"
        for active in (
            patch.object(paths, "install", return_value=self.inst),
            patch.dict(os.environ, _clean_env(GB_HOME=str(self.home)), clear=True),
            patch.dict(paths._PROFILE_ENV, {}, clear=True),  # pylint: disable=protected-access
        ):
            active.start()
            self.addCleanup(active.stop)

    def shared_root(self, part):
        sha = paths.part_sha(part)
        assert sha is not None
        return self.home / "runtime" / sha


class ShaTests(_Layout):
    def test_venv_and_tools_have_independent_shas(self):
        venv, tools = paths.part_sha("venv"), paths.part_sha("tools")
        self.assertRegex(str(venv), r"^[0-9a-f]{16}$")
        self.assertNotEqual(venv, tools)
        _touch(self.data / "requirements.txt", "yt-dlp==22\n")
        self.assertNotEqual(venv, paths.part_sha("venv"))
        self.assertEqual(tools, paths.part_sha("tools"))
        venv = paths.part_sha("venv")
        _touch(self.data / "package-lock.json", '{"lockfileVersion": 3}\n')
        self.assertEqual(venv, paths.part_sha("venv"))
        self.assertNotEqual(tools, paths.part_sha("tools"))
        tools = paths.part_sha("tools")
        _touch(self.data / "package.json", '{"name": "other"}\n')
        self.assertNotEqual(tools, paths.part_sha("tools"))

    def test_part_sha_is_none_without_the_files(self):
        (self.data / "package.json").unlink()
        self.assertIsNone(paths.part_sha("tools"))
        self.assertIsNotNone(paths.part_sha("venv"))

    def test_part_sha_is_cached_until_a_file_changes(self):
        first = paths.part_sha("venv")
        with patch.object(paths.hashlib, "sha256", side_effect=AssertionError("recalculou")):
            self.assertEqual(first, paths.part_sha("venv"))
        _touch(self.data / "requirements.txt", "yt-dlp==333\n")
        self.assertNotEqual(first, paths.part_sha("venv"))

    def test_each_part_lives_in_its_own_sha_folder(self):
        self.assertEqual(paths.RuntimePart(self.shared_root("venv") / ".venv", "gb_home"), paths.venv_dir())
        self.assertEqual(paths.RuntimePart(self.shared_root("tools") / ".tools", "gb_home"), paths.tools_dir())
        self.assertEqual((self.shared_root("venv"), "gb_home"), paths.runtime_root("venv"))


class ReadyMarkerTests(_Layout):
    def test_shared_part_counts_only_with_a_ready_marker(self):
        checkout = _fake_checkout(self.base)
        (self.base / "repo" / ".venv").mkdir()
        with patch.object(paths, "install", return_value=checkout):
            root = self.shared_root("venv")
            _build_venv(root)
            self.assertEqual("checkout", paths.venv_dir().source)
            self.assertFalse(paths.runtime_info()["venv"]["managed"])
            paths.write_marker("venv", root, "building")
            self.assertEqual("checkout", paths.venv_dir().source)
            paths.write_marker("venv", root, "ready")
            self.assertEqual(paths.RuntimePart(root / ".venv", "gb_home"), paths.venv_dir())
            info = paths.runtime_info()["venv"]
            self.assertTrue(info["managed"])
            self.assertEqual("ready", info["marker"])

    def test_marker_with_a_stale_sha_is_not_ready(self):
        root = self.base / "explicit"
        _build_venv(root)
        paths.write_marker("venv", root, "ready")
        self.assertTrue(paths.part_ready("venv", root))
        _touch(self.data / "requirements.txt", "yt-dlp==4444\n")
        self.assertFalse(paths.part_ready("venv", root))

    def test_venv_whose_base_python_is_gone_is_not_ready(self):
        root = self.base / "explicit"
        _build_venv(root, home=self.base / "python-removido")
        paths.write_marker("venv", root, "ready")
        self.assertFalse(paths.part_ready("venv", root))
        (root / ".venv" / "pyvenv.cfg").unlink()
        self.assertFalse(paths.part_ready("venv", root))

    def test_tools_are_ready_without_a_pyvenv(self):
        root = self.base / "explicit"
        (root / ".tools").mkdir(parents=True)
        self.assertFalse(paths.part_ready("tools", root))
        paths.write_marker("tools", root, "ready")
        self.assertTrue(paths.part_ready("tools", root))

    def test_marker_format(self):
        root = self.base / "explicit"
        written = paths.write_marker("venv", root, "building")
        self.assertEqual(root / ".getbrolls-runtime-venv.json", written)
        marker = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema", "part", "sha", "status", "python", "getbrolls"},
            set(marker),
        )
        self.assertEqual(paths.MARKER_SCHEMA_VERSION, marker["schema"])
        self.assertEqual(("venv", "building"), (marker["part"], marker["status"]))
        self.assertEqual(paths.part_sha("venv"), marker["sha"])
        self.assertTrue(marker["python"])
        self.assertIsNone(json.loads(paths.write_marker("tools", root, "ready").read_text(encoding="utf-8"))["python"])
        self.assertEqual([], [p.name for p in root.iterdir() if p.name.endswith(".tmp")])

    def test_write_marker_refuses_an_unknown_status(self):
        with self.assertRaises(ValueError):
            paths.write_marker("venv", self.base, "quase")

    def test_read_marker_never_raises(self):
        root = self.base / "explicit"
        self.assertIsNone(paths.read_marker("venv", root))
        marker = paths.marker_path("venv", root)
        _touch(marker, "{nao é json")
        self.assertIsNone(paths.read_marker("venv", root))
        _touch(marker, "[1, 2]")
        self.assertIsNone(paths.read_marker("venv", root))
        marker.write_bytes(b"\xff\xfe\x00")
        self.assertIsNone(paths.read_marker("venv", root))
        marker.unlink()
        marker.mkdir()
        self.assertIsNone(paths.read_marker("venv", root))
        self.assertFalse(paths.part_ready("venv", root))


class TargetTests(_Layout):
    def test_target_is_never_the_checkout(self):
        checkout = _fake_checkout(self.base)
        (self.base / "repo" / ".venv").mkdir()
        with patch.object(paths, "install", return_value=checkout):
            self.assertEqual("checkout", paths.venv_dir().source)
            target = paths.runtime_target("venv")
            self.assertEqual("gb_home", target.source)
            self.assertEqual(self.shared_root("venv") / ".venv", target.path)
            self.assertTrue(target.path.is_relative_to(paths.gb_home() / "runtime"))

    def test_explicit_dir_is_the_target_and_the_reader(self):
        explicit = self.base / "explicit"
        with patch.dict(os.environ, {"GB_RUNTIME_DIR": str(explicit)}):
            self.assertEqual(paths.RuntimePart(explicit / ".venv", "GB_RUNTIME_DIR"), paths.runtime_target("venv"))
            self.assertEqual(paths.RuntimePart(explicit / ".tools", "GB_RUNTIME_DIR"), paths.tools_dir())
            info = paths.runtime_info()
            self.assertTrue(info["explicit"])
            self.assertFalse(info["venv"]["managed"])
            self.assertIsNone(info["venv"]["marker"])

    def test_runtime_dir_from_a_profile_reports_source_profile(self):
        explicit = str(self.base / "explicit")
        paths.note_profile_env({"GB_RUNTIME_DIR": explicit})
        with patch.dict(os.environ, {"GB_RUNTIME_DIR": explicit}):
            self.assertTrue(paths.from_profile("GB_RUNTIME_DIR"))
            self.assertEqual((Path(explicit), "profile"), paths.runtime_root("venv"))
            self.assertEqual("profile", paths.venv_dir().source)
        with patch.dict(os.environ, {"GB_RUNTIME_DIR": str(self.base / "outra")}):
            self.assertFalse(paths.from_profile("GB_RUNTIME_DIR"))
            self.assertEqual("GB_RUNTIME_DIR", paths.venv_dir().source)


class ConflictTests(_Layout):
    def test_explicit_dir_of_another_version_is_a_clear_conflict(self):
        explicit = self.base / "explicit"
        with patch.dict(os.environ, {"GB_RUNTIME_DIR": str(explicit)}):
            self.assertIsNone(paths.runtime_conflict("venv"))
            _build_venv(explicit)
            paths.write_marker("venv", explicit, "ready")
            self.assertIsNone(paths.runtime_conflict("venv"))
            _touch(self.data / "requirements.txt", "yt-dlp==55555\n")
            message = paths.runtime_conflict("venv")
            assert message is not None
            self.assertIn("GB_RUNTIME_DIR", message)
            self.assertIn("outra versão", message)
            self.assertNotIn(str(self.base), message)
            self.assertEqual(message, paths.runtime_info()["venv"]["conflict"])
            self.assertIsNone(paths.runtime_conflict("tools"))

    def test_a_crashed_build_is_not_a_conflict(self):
        explicit = self.base / "explicit"
        with patch.dict(os.environ, {"GB_RUNTIME_DIR": str(explicit)}):
            paths.write_marker("venv", explicit, "building")
            _touch(self.data / "requirements.txt", "yt-dlp==666666\n")
            self.assertIsNone(paths.runtime_conflict("venv"))

    def test_the_shared_layout_never_conflicts(self):
        root = self.shared_root("venv")
        paths.write_marker("venv", root, "ready")
        _touch(self.data / "requirements.txt", "yt-dlp==7777777\n")
        self.assertIsNone(paths.runtime_conflict("venv"))


class HintTests(unittest.TestCase):
    def test_wheel_installer_hint_names_setup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(paths, "install", return_value=_fake_wheel(Path(tmp))):
            hint = paths.installer_hint()
        self.assertIn("getbrolls setup", hint)
        self.assertNotIn("--check", hint)
        self.assertNotIn(tmp, hint)


if __name__ == "__main__":
    unittest.main()
