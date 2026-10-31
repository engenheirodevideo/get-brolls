"""Leitura do `plugins.json`: conteúdo ruim é dado inválido, arquivo que não dá para ler é E/S.

- JSON quebrado ou fora do schema continua `ValueError` (`INVALID_DATA`), com a dica de corrigir ou apagar;
- pasta, FIFO ou arquivo sem permissão no lugar do `plugins.json` é `OSError` (`IO_ERROR`), sem mandar apagar;
- um `plugins.json` válido lê igual;
- `doctor` e `capabilities` continuam relatando, com `plugins_error` de E/S no lugar de parar.
"""

import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli

from getbrolls.sdk import loader, plugin_state

WINDOWS = sys.platform == "win32"
ROOT_USER = not WINDOWS and os.geteuid() == 0


class PluginStateReadTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gb-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        environ = patch.dict(os.environ, {"GB_HOME": str(self.home)})
        environ.start()
        self.addCleanup(environ.stop)
        self.path = self.home / "plugins.json"

    def assert_io_refusal(self, expected_errno=None):
        with self.assertRaises(OSError) as caught:
            plugin_state.read_state()
        self.assertNotIsInstance(caught.exception, ValueError)
        if expected_errno is not None:
            self.assertEqual(expected_errno, caught.exception.errno)
        self.assertEqual(str(self.path), str(caught.exception.filename))
        self.assertNotIn("apague", str(caught.exception))
        return caught.exception

    def unreadable(self):
        self.path.write_text(json.dumps({"enabled": {}}), encoding="utf-8")
        self.path.chmod(0)
        self.addCleanup(self.path.chmod, 0o600)

    def test_a_folder_at_plugins_json_is_an_io_refusal(self):
        self.path.mkdir()
        self.assert_io_refusal(errno.EINVAL)

    @unittest.skipIf(WINDOWS, "FIFO não existe no Windows")
    def test_a_fifo_at_plugins_json_is_an_io_refusal(self):
        os.mkfifo(self.path)
        self.assert_io_refusal(errno.EINVAL)

    @unittest.skipIf(WINDOWS or ROOT_USER, "chmod 000 não barra o root nem vale no Windows")
    def test_an_unreadable_plugins_json_is_an_io_refusal(self):
        self.unreadable()
        self.assert_io_refusal(errno.EACCES)

    def test_malformed_content_is_still_the_invalid_state_refusal(self):
        for raw in (b"{", b'{"enabled": []}', b'{"enabled": {}, "x": NaN}'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(ValueError) as caught:
                    plugin_state.read_state()
                self.assertIn("plugins.json inválido em $GB_HOME/plugins.json", str(caught.exception))
                self.assertIn("apague", str(caught.exception))

    def test_a_valid_state_reads_the_same(self):
        state = {"enabled": {"demo": {"sha256": "a" * 64, "version": "0.1.0"}}, "sources": {}}
        self.path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(state, plugin_state.read_state())
        self.assertEqual(state, loader.read_state())

    def test_no_file_is_still_no_plugins(self):
        self.assertEqual({"enabled": {}}, plugin_state.read_state())


class PluginStateCliTests(unittest.TestCase):
    """A CLI relata a pasta ou o arquivo sem permissão como `IO_ERROR` (saída 1), sem mandar apagar."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gb-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.path = self.home / "plugins.json"

    def run_plugins(self, *args):
        return run_cli("plugins", *args, expect=1, env={"GB_HOME": str(self.home)})

    def assert_io_error(self, error):
        self.assertEqual("IO_ERROR", error["error_code"])
        self.assertIn("$GB_HOME/plugins.json", error["error"])
        self.assertNotIn("apague", error["error"])
        self.assertNotIn(str(self.home), error["error"])

    def test_list_and_disable_report_a_folder_as_io_error(self):
        self.path.mkdir()
        for args in (("--action", "list"), ("--action", "disable", "--id", "demo")):
            with self.subTest(args=args):
                self.assert_io_error(self.run_plugins(*args))

    @unittest.skipIf(WINDOWS or ROOT_USER, "chmod 000 não barra o root nem vale no Windows")
    def test_list_reports_an_unreadable_file_as_io_error(self):
        self.path.write_text(json.dumps({"enabled": {}}), encoding="utf-8")
        self.path.chmod(0)
        self.addCleanup(self.path.chmod, 0o600)
        self.assert_io_error(self.run_plugins("--action", "list"))

    def test_list_still_reports_malformed_content_as_invalid_data(self):
        self.path.write_text("{", encoding="utf-8")
        error = self.run_plugins("--action", "list")
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("plugins.json inválido em $GB_HOME/plugins.json", error["error"])


class PluginStateDiagnosticsTests(unittest.TestCase):
    """`doctor` e `capabilities` não param quando o `plugins.json` não dá para ler: relatam `plugins_error`."""

    def fresh_home(self):
        """Um `$GB_HOME` novo por caso, com `plugins/` (o `capabilities` só lê o inventário com a pasta)."""
        home = Path(tempfile.mkdtemp(prefix="gb-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / "plugins").mkdir()
        return home

    @staticmethod
    def folder(path):
        path.mkdir()

    @staticmethod
    def fifo(path):
        os.mkfifo(path)

    def unreadable(self, path):
        path.write_text(json.dumps({"enabled": {}}), encoding="utf-8")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)

    def unreadable_cases(self):
        cases = [("pasta", self.folder)]
        if not WINDOWS:
            cases.append(("FIFO", self.fifo))
        if not (WINDOWS or ROOT_USER):
            cases.append(("chmod 000", self.unreadable))
        return cases

    def each_case(self, command):
        for label, make in self.unreadable_cases():
            with self.subTest(case=label):
                home = self.fresh_home()
                make(home / "plugins.json")
                payload = run_cli(command, env={"GB_HOME": str(home)})
                error = payload["plugins_error"]
                self.assertIn("$GB_HOME/plugins.json", error)
                self.assertNotIn("apague", error)
                self.assertNotIn(str(home), error)
                yield payload

    def test_doctor_reports_plugins_error_for_an_unreadable_plugins_json(self):
        for doctor in self.each_case("doctor"):
            self.assertNotIn("plugins", doctor)

    def test_capabilities_reports_plugins_error_for_an_unreadable_plugins_json(self):
        for manifest in self.each_case("capabilities"):
            self.assertEqual([], manifest["plugin_commands"])
            self.assertEqual([], manifest["plugins_problems"])


if __name__ == "__main__":
    unittest.main()
