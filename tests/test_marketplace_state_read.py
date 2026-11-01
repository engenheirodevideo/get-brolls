"""Leitura do `marketplaces.json`: conteúdo ruim é dado inválido, arquivo que não dá para ler é E/S.

- JSON quebrado, com `NaN`, aninhado demais, grande demais ou fora do schema continua `ValueError`
  (`INVALID_DATA`), com a dica de corrigir ou apagar;
- pasta, FIFO ou arquivo sem permissão no lugar do `marketplaces.json` é `OSError` (`IO_ERROR`), sem
  travar e sem mandar apagar;
- um `marketplaces.json` válido lê igual;
- `doctor` e `capabilities` continuam relatando, com `marketplaces_error` de E/S no lugar de parar;
- um plugin instalado de um marketplace continua listado (`doctor`, `plugins --action list`), com o
  `marketplace_notice` de E/S no lugar de derrubar o inventário.
"""

import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import NOT_READY
from _paths import CLI_ARGV

from getbrolls.sdk import marketplace, plugin_state, schemas

WINDOWS = sys.platform == "win32"
ROOT_USER = not WINDOWS and os.geteuid() == 0
# Um FIFO sem escritor travava a leitura para sempre: o teste falha no prazo em vez de esperar.
CLI_TIMEOUT = 60
INVALID = "marketplaces.json inválido em $GB_HOME/marketplaces.json"


def valid_state():
    """Um `marketplaces.json` válido, com um marketplace fixado (o cache dele não existe)."""
    record = {
        "source": "https://example.com/exemplo.git",
        "ref": None,
        "commit": "a" * 40,
        "index_sha256": "b" * 64,
        "added_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
    }
    return {"schema": f"getbrolls.marketplaces/{schemas.version('marketplaces')}", "marketplaces": {"exemplo": record}}


def folder(path):
    path.mkdir()


def fifo(path):
    os.mkfifo(path)


def link_to_folder(path):
    """Um symlink para uma pasta: o alvo é seguido e também não é um arquivo comum."""
    target = path.with_name("alvo-pasta")
    target.mkdir()
    path.symlink_to(target, target_is_directory=True)


def unreadable(test, path):
    path.write_text(json.dumps(valid_state()), encoding="utf-8")
    path.chmod(0)
    test.addCleanup(path.chmod, 0o600)


def fresh_home(test):
    """Um `$GB_HOME` novo por caso."""
    home = Path(tempfile.mkdtemp(prefix="gb-home-"))
    test.addCleanup(shutil.rmtree, home, ignore_errors=True)
    return home


def run_gb(*args, home):
    """Roda a CLI com prazo: um FIFO que trava a leitura vira falha do teste, não espera eterna."""
    environment = {**os.environ, "GB_HOME": str(home)}
    return subprocess.run(
        [*CLI_ARGV, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
        timeout=CLI_TIMEOUT,
    )


PLUGIN_ID = "exemplo-plugin"


def install_marketplace_plugin(home):
    """Um plugin instalado do marketplace `exemplo`: a pasta em `plugins/` e a origem em `sources`."""
    folder = home / "plugins" / PLUGIN_ID
    folder.mkdir(parents=True)
    manifest = {
        "id": PLUGIN_ID,
        "name": "Exemplo",
        "version": "0.1.0",
        "sdk_api": 1,
        "requires_getbrolls": ">=2.5,<3",
        "entry": "plugin.py",
        "contributes": {"presets": ["exemplo"]},
        "permissions": {},
    }
    (folder / "getbrolls-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "plugin.py").write_text("def register(api):\n    pass\n", encoding="utf-8")
    origin = {
        "source": "https://example.com/exemplo.git",
        "commit": "a" * 40,
        "ref": None,
        "subdir": f"plugins/{PLUGIN_ID}",
        "marketplace": "exemplo",
        "tier": "community",
        "index_commit": "a" * 40,
    }
    state = {
        "schema": f"getbrolls.plugins/{schemas.version(plugin_state.STATE_SCHEMA)}",
        "enabled": {},
        "sources": {PLUGIN_ID: origin},
    }
    (home / "plugins.json").write_text(json.dumps(state), encoding="utf-8")


class MarketplaceStateReadTests(unittest.TestCase):
    """Em processo; o FIFO fica nos testes da CLI, que rodam com prazo e não travam a suíte."""

    def setUp(self):
        self.home = fresh_home(self)
        environ = patch.dict(os.environ, {"GB_HOME": str(self.home)})
        environ.start()
        self.addCleanup(environ.stop)
        self.path = self.home / "marketplaces.json"

    def assert_io_refusal(self, expected_errno):
        with self.assertRaises(OSError) as caught:
            marketplace.read_state()
        self.assertNotIsInstance(caught.exception, ValueError)
        self.assertEqual(expected_errno, caught.exception.errno)
        self.assertEqual(str(self.path), str(caught.exception.filename))
        self.assertNotIn("apague", str(caught.exception))

    def test_a_folder_at_marketplaces_json_is_an_io_refusal(self):
        folder(self.path)
        self.assert_io_refusal(errno.EINVAL)

    @unittest.skipIf(WINDOWS or ROOT_USER, "chmod 000 não barra o root nem vale no Windows")
    def test_an_unreadable_marketplaces_json_is_an_io_refusal(self):
        unreadable(self, self.path)
        self.assert_io_refusal(errno.EACCES)

    def test_bad_content_is_still_the_invalid_state_refusal(self):
        too_large = b'{"schema": "x", "marketplaces": {}}' + b" " * (marketplace.STATE_MAX_BYTES + 1)
        cases = {
            "sintaxe": b"{",
            "NaN": b'{"schema": "getbrolls.marketplaces/1", "marketplaces": NaN}',
            "aninhado demais": b"[" * 200 + b"]" * 200,
            "grande demais": too_large,
            "forma": b'{"marketplaces": {}}',
            "versão mais nova": b'{"schema": "getbrolls.marketplaces/999", "marketplaces": {}}',
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                self.path.write_bytes(raw)
                with self.assertRaises(ValueError) as caught:
                    marketplace.read_state()
                self.assertIn("marketplaces.json", str(caught.exception))

    def test_bad_syntax_keeps_the_exact_wording(self):
        self.path.write_bytes(b"{")
        with self.assertRaises(ValueError) as caught:
            marketplace.read_state()
        self.assertEqual(
            f"{INVALID}. Corrija ou apague o arquivo para recomeçar sem marketplaces.", str(caught.exception)
        )

    def test_a_valid_state_reads_the_same(self):
        state = valid_state()
        self.path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(state, marketplace.read_state())

    def test_no_file_is_still_no_marketplaces(self):
        self.assertEqual({}, marketplace.read_state()["marketplaces"])

    @unittest.skipIf(WINDOWS, "symlink exige privilégio no Windows")
    def test_a_symlink_to_a_folder_is_an_io_refusal(self):
        link_to_folder(self.path)
        self.assert_io_refusal(errno.EINVAL)

    @unittest.skipIf(WINDOWS, "symlink exige privilégio e /dev não existe no Windows")
    def test_a_symlink_to_a_device_is_an_io_refusal_without_reading_it(self):
        # `/dev/zero` nunca termina: ler sem checar o tipo esgotava a memória.
        for device in ("/dev/null", "/dev/zero"):
            with self.subTest(device=device):
                self.path.symlink_to(device)
                try:
                    self.assert_io_refusal(errno.EINVAL)
                finally:
                    self.path.unlink()

    def test_an_unreadable_state_is_not_the_official_index(self):
        folder(self.path)
        self.assertFalse(marketplace.official_marketplace("exemplo"))


class UnreadableCases:
    """Os casos de arquivo que não dá para ler, conforme o sistema e o usuário permitem."""

    def unreadable_cases(self):
        cases = [("pasta", folder)]
        if not WINDOWS:
            cases.append(("FIFO", fifo))
            cases.append(("symlink para pasta", link_to_folder))
        if not (WINDOWS or ROOT_USER):
            cases.append(("chmod 000", lambda path: unreadable(self, path)))
        return cases


class MarketplaceStateCliTests(UnreadableCases, unittest.TestCase):
    """A CLI relata pasta, FIFO ou arquivo sem permissão como `IO_ERROR` (saída 1), sem mandar apagar."""

    def test_marketplace_list_reports_an_unreadable_state_as_io_error(self):
        for label, make in self.unreadable_cases():
            with self.subTest(case=label):
                home = fresh_home(self)
                make(home / "marketplaces.json")
                done = run_gb("plugins", "--action", "marketplace-list", home=home)
                self.assertEqual(1, done.returncode, done.stderr)
                error = json.loads(done.stderr)
                self.assertEqual("IO_ERROR", error["error_code"])
                self.assertIn("$GB_HOME/marketplaces.json", error["error"])
                self.assertNotIn("apague", error["error"])
                self.assertNotIn(str(home), error["error"])

    def test_marketplace_list_still_reports_malformed_content_as_invalid_data(self):
        home = fresh_home(self)
        (home / "marketplaces.json").write_text("{", encoding="utf-8")
        done = run_gb("plugins", "--action", "marketplace-list", home=home)
        self.assertEqual(1, done.returncode, done.stderr)
        error = json.loads(done.stderr)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn(INVALID, error["error"])


class MarketplaceStateDiagnosticsTests(UnreadableCases, unittest.TestCase):
    """`doctor` e `capabilities` não param nem travam: relatam `marketplaces_error` e o resto do relatório."""

    def each_case(self, command, ok_exits):
        for label, make in self.unreadable_cases():
            with self.subTest(case=label):
                home = fresh_home(self)
                make(home / "marketplaces.json")
                done = run_gb(command, home=home)
                self.assertIn(done.returncode, ok_exits, done.stderr)
                payload = json.loads(done.stdout)
                error = payload["marketplaces_error"]
                self.assertIn("$GB_HOME/marketplaces.json", error)
                self.assertNotIn("apague", error)
                self.assertNotIn("inválido", error)
                self.assertNotIn(str(home), error)
                self.assertNotIn(str(home.resolve()), error)
                yield payload

    def test_doctor_reports_marketplaces_error_for_an_unreadable_state(self):
        for doctor in self.each_case("doctor", (0, NOT_READY)):
            self.assertNotIn("marketplaces", doctor)
            self.assertIn("summary", doctor)

    def test_capabilities_reports_marketplaces_error_for_an_unreadable_state(self):
        for manifest in self.each_case("capabilities", (0,)):
            self.assertEqual([], manifest["marketplaces"])
            self.assertIn("commands", manifest)

    def test_capabilities_reads_a_valid_state_the_same(self):
        home = fresh_home(self)
        (home / "marketplaces.json").write_text(json.dumps(valid_state()), encoding="utf-8")
        first = run_gb("capabilities", home=home)
        second = run_gb("capabilities", home=home)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        manifest = json.loads(first.stdout)
        self.assertNotIn("marketplaces_error", manifest)
        rows = manifest["marketplaces"]
        self.assertEqual(["exemplo"], [row["name"] for row in rows])
        self.assertEqual("a" * 40, rows[0]["commit"])
        self.assertIsNone(rows[0]["plugins"])
        self.assertIsNotNone(rows[0]["problem"])


class MarketplaceNoticeTests(UnreadableCases, unittest.TestCase):
    """Plugin instalado de marketplace com `marketplaces.json` ilegível: o inventário fica, o aviso é de E/S."""

    def each_case(self, *args, ok_exits):
        for label, make in self.unreadable_cases():
            with self.subTest(case=label):
                home = fresh_home(self)
                install_marketplace_plugin(home)
                make(home / "marketplaces.json")
                done = run_gb(*args, home=home)
                self.assertIn(done.returncode, ok_exits, done.stderr)
                yield home, json.loads(done.stdout)

    def assert_io_notice(self, home, row):
        notice = row["origin"]["marketplace_notice"]
        self.assertIn("$GB_HOME/marketplaces.json", notice)
        self.assertNotIn("apague", notice)
        self.assertNotIn("inválido", notice)
        self.assertNotIn(str(home), notice)
        self.assertNotIn(str(home.resolve()), notice)
        self.assertEqual("exemplo", row["origin"]["marketplace"])

    def test_doctor_keeps_the_plugin_inventory(self):
        for home, doctor in self.each_case("doctor", ok_exits=(0, NOT_READY)):
            self.assertIsNone(doctor.get("plugins_error"))
            self.assertIn("$GB_HOME/marketplaces.json", doctor["marketplaces_error"])
            self.assertNotIn("apague", doctor["marketplaces_error"])
            rows = {row["id"]: row for row in doctor["plugins"]}
            self.assertIn(PLUGIN_ID, rows)
            self.assert_io_notice(home, rows[PLUGIN_ID])

    def test_plugins_list_reports_the_notice(self):
        for home, listing in self.each_case("plugins", "--action", "list", ok_exits=(0,)):
            rows = {row["id"]: row for row in listing["plugins"]}
            self.assertIn(PLUGIN_ID, rows)
            self.assert_io_notice(home, rows[PLUGIN_ID])

    def test_update_all_preview_skips_the_plugin_with_the_io_reason(self):
        for label, make in self.unreadable_cases():
            with self.subTest(case=label):
                home = fresh_home(self)
                install_marketplace_plugin(home)
                state_path = home / "plugins.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["sources"]["manual-plugin"] = {"source": "https://example.com/manual.git", "commit": "c" * 40}
                state_path.write_text(json.dumps(state), encoding="utf-8")
                make(home / "marketplaces.json")
                done = run_gb("plugins", "--action", "update", "--all", home=home)
                self.assertEqual(0, done.returncode, done.stdout + done.stderr)
                preview = json.loads(done.stdout)
                self.assertEqual([], preview["plugins"])
                self.assertEqual(["manual-plugin"], preview["outside_marketplace"])
                self.assertEqual([PLUGIN_ID], [row["id"] for row in preview["skipped"]])
                row = preview["skipped"][0]
                self.assertEqual("exemplo", row["marketplace"])
                self.assertIn("$GB_HOME/marketplaces.json", row["reason"])
                self.assertNotIn("apague", row["reason"])
                self.assertNotIn("inválido", row["reason"])
                self.assertNotIn(str(home), row["reason"])
                self.assertNotIn(str(home.resolve()), row["reason"])


if __name__ == "__main__":
    unittest.main()
