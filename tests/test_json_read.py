"""JSON persistido hostil vira recusa limpa: aninhamento fundo, NaN/Infinity e arquivo grande demais.

Os leitores `queue.load`, `sdk.loader.read_state`, `profile.read_store` e `_paths.read_marker` usam
`getbrolls.json_read`; cada um traduz a recusa para a sua (`INVALID_DATA`, `store_corrupt`, `None`),
nunca para `INTERNAL_ERROR` (exit 3) e nunca aceitando o documento.
"""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV

from getbrolls import _paths, json_read, library, profile, queue, runtime_parts
from getbrolls.errors import UsageError
from getbrolls.sdk import loader

# Fundo o bastante para estourar o limite de recursão do decodificador C do `json`.
DEEP = b"[" * 100_000 + b"]" * 100_000
NON_FINITE = (b"NaN", b"Infinity", b"-Infinity", b"1e400", b"-1e400")
# Escapes `\\uD800`/`\\uDC00` sozinhos: o `json` aceita, mas a string não codifica em UTF-8.
LONE_SURROGATES = (b'"\\ud800"', b'["\\udc00"]', b'{"x\\udc00": 1}', b'{"a": {"b": ["ok", "\\ud83d"]}}')


def nested(depth):
    """Uma lista aninhada `depth` níveis, abaixo do limite de recursão do `json`."""
    return b"[" * depth + b"]" * depth


class HelperTests(unittest.TestCase):
    def test_valid_documents_parse_exactly_like_json_loads(self):
        for raw in (b'{"a": [1, 2.5, "x\\u00e9", null, true]}', b"[]", b'"s"', b"0", '{"é": 1}'.encode()):
            with self.subTest(raw=raw):
                self.assertEqual(json.loads(raw.decode("utf-8")), json_read.loads(raw))

    def test_non_finite_numbers_are_refused(self):
        for constant in NON_FINITE:
            with self.subTest(constant=constant), self.assertRaises(json_read.JsonReadError) as caught:
                json_read.loads(b'{"x": [' + constant + b"]}")
            self.assertEqual("non_finite", caught.exception.reason)

    def test_deep_nesting_is_refused_whether_or_not_the_decoder_recurses_out(self):
        for raw in (DEEP, nested(json_read.MAX_DEPTH + 1), b'{"a": ' * 200 + b"1" + b"}" * 200):
            with self.subTest(size=len(raw)), self.assertRaises(json_read.JsonReadError) as caught:
                json_read.loads(raw)
            self.assertEqual("too_deep", caught.exception.reason)

    def test_lone_surrogates_in_keys_or_strings_are_an_encoding_refusal(self):
        for raw in LONE_SURROGATES:
            with self.subTest(raw=raw), self.assertRaises(json_read.JsonReadError) as caught:
                json_read.loads(raw)
            self.assertEqual("encoding", caught.exception.reason)

    def test_lone_surrogates_are_accepted_only_on_explicit_opt_in(self):
        for raw in LONE_SURROGATES:
            with self.subTest(raw=raw):
                self.assertEqual(json.loads(raw), json_read.loads(raw, allow_lone_surrogates=True))

    def test_the_surrogate_opt_in_keeps_the_depth_cap(self):
        with self.assertRaises(json_read.JsonReadError) as caught:
            json_read.loads(nested(json_read.MAX_DEPTH + 1), allow_lone_surrogates=True)
        self.assertEqual("too_deep", caught.exception.reason)

    def test_a_surrogate_pair_escape_is_a_valid_character(self):
        self.assertEqual({"k": "\U0001f600"}, json_read.loads(b'{"k": "\\ud83d\\ude00"}'))

    def test_the_default_size_cap_stays_a_few_mib(self):
        # 32 MiB de listas vazias custavam ~1,6 GB de RSS; o teto padrão fica bem abaixo disso.
        self.assertLessEqual(json_read.MAX_BYTES, 16 * 1024 * 1024)

    def test_the_depth_cap_counts_levels_not_siblings(self):
        wide = b"[" + b",".join([b"[]"] * 50_000) + b"]"
        self.assertEqual([[]] * 50_000, json_read.loads(wide, max_depth=2))
        with self.assertRaises(json_read.JsonReadError) as caught:
            json_read.loads(b"[" + wide + b"]", max_depth=2)
        self.assertEqual("too_deep", caught.exception.reason)
        self.assertEqual({"a": [{}]}, json_read.loads(b'{"a": [{}]}', max_depth=3))

    def test_the_depth_cap_itself_is_accepted(self):
        self.assertIsInstance(json_read.loads(nested(json_read.MAX_DEPTH)), list)

    def test_size_cap_is_counted_in_bytes(self):
        self.assertEqual([1], json_read.loads(b"[1]", max_bytes=3))
        with self.assertRaises(json_read.JsonReadError) as caught:
            json_read.loads(b"[1] ", max_bytes=3)
        self.assertEqual("too_large", caught.exception.reason)

    def test_syntax_and_encoding_errors_are_the_same_refusal_type(self):
        for raw, reason in ((b"{", "syntax"), (b"\xff", "encoding"), (b"\xef\xbb\xbf{}", "syntax")):
            with self.subTest(raw=raw), self.assertRaises(json_read.JsonReadError) as caught:
                json_read.loads(raw)
            self.assertEqual(reason, caught.exception.reason)

    def test_the_refusal_is_a_value_error_with_a_fixed_message(self):
        with self.assertRaises(ValueError) as caught:
            json_read.loads(DEEP)
        self.assertNotIn("[[[", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_read_caps_the_bytes_it_reads_and_lets_os_errors_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.json"
            path.write_bytes(b'{"a": 1}')
            self.assertEqual({"a": 1}, json_read.read(path))
            with self.assertRaises(json_read.JsonReadError):
                json_read.read(path, max_bytes=4)
            with self.assertRaises(FileNotFoundError):
                json_read.read(Path(tmp) / "missing.json")


class OpenFlagsTests(unittest.TestCase):
    """Um dispositivo de terminal no caminho do estado nunca vira o terminal de controle do processo."""

    @unittest.skipUnless(hasattr(os, "O_NOCTTY"), "O_NOCTTY só existe no POSIX")
    def test_the_open_flags_include_o_noctty(self):
        self.assertTrue(json_read._OPEN_FLAGS & os.O_NOCTTY)  # pylint: disable=protected-access


class NonRegularFileTests(unittest.TestCase):
    """Só arquivo comum é lido: um FIFO no caminho do estado travaria a leitura para sempre."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    @staticmethod
    def in_thread(call):
        """`call()` numa thread; devolve `(terminou, resultado ou exceção)` depois de no máximo 5 s."""
        outcome = []

        def target():
            try:
                outcome.append(call())
            except Exception as exc:  # noqa: BLE001 - o teste inspeciona a exceção  # pylint: disable=broad-exception-caught
                outcome.append(exc)

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(timeout=5)
        return (not worker.is_alive()), (outcome[0] if outcome else None)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO só existe em POSIX")
    def test_a_fifo_is_refused_without_blocking(self):
        path = self.tmp / "queue.json"
        os.mkfifo(path)
        self.addCleanup(self.unblock, path)
        finished, outcome = self.in_thread(lambda: json_read.read(path))
        self.assertTrue(finished, "json_read.read travou num FIFO")
        self.assertIsInstance(outcome, json_read.JsonReadError)
        assert isinstance(outcome, json_read.JsonReadError)
        self.assertEqual("not_regular", outcome.reason)
        self.assertIn(outcome.reason, json_read.REASONS)

    @staticmethod
    def unblock(path):
        """Se a leitura ficou presa no `open` do FIFO, abre o lado de escrita e fecha: o leitor vê o fim."""
        with contextlib.suppress(OSError):
            os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))

    @unittest.skipIf(os.name == "nt", "no Windows abrir uma pasta já é `PermissionError`")
    def test_a_directory_is_the_same_refusal(self):
        path = self.tmp / "queue.json"
        path.mkdir()
        with self.assertRaises(json_read.JsonReadError) as caught:
            json_read.read(path)
        self.assertEqual("not_regular", caught.exception.reason)

    def test_a_regular_file_still_reads(self):
        path = self.tmp / "a.json"
        path.write_bytes(b'{"a": [1]}')
        self.assertEqual({"a": [1]}, json_read.read(path))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO só existe em POSIX")
    def test_a_library_index_that_is_a_fifo_is_an_io_refusal_without_blocking(self):
        home = self.tmp / "home"
        home.mkdir()
        with patch.dict(os.environ, {"GB_HOME": str(home)}):
            index = library.index_path()
            index.parent.mkdir(parents=True, exist_ok=True)
            os.mkfifo(index)
            self.addCleanup(self.unblock, index)
            with patch.object(library, "enabled", return_value=True):
                finished, outcome = self.in_thread(library.load_index)
        self.assertTrue(finished, "library.load_index travou num FIFO")
        # Não é JSON inválido (nada a apagar): é E/S, o IO_ERROR da CLI.
        self.assertIsInstance(outcome, OSError)
        self.assertNotIsInstance(outcome, ValueError)
        self.assertIn("não é um arquivo comum", str(outcome))


class MemoryErrorTests(unittest.TestCase):
    def test_a_memory_error_while_decoding_is_the_too_large_refusal(self):
        with (
            patch.object(json_read.json, "loads", side_effect=MemoryError),
            self.assertRaises(json_read.JsonReadError) as caught,
        ):
            json_read.loads(b"[1]")
        self.assertEqual("too_large", caught.exception.reason)
        self.assertIsNone(caught.exception.__cause__)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "work" / "queue.json"
        self.path.parent.mkdir()

    def refused(self, raw):
        self.path.write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            queue.load(self.path)
        self.assertIn("queue.json inválido em", str(caught.exception))
        return caught.exception

    def test_deep_nesting_is_the_invalid_queue_refusal(self):
        self.refused(DEEP)

    def test_nan_and_infinity_are_refused_even_in_a_key_the_schema_ignores(self):
        for constant in NON_FINITE:
            with self.subTest(constant=constant):
                self.refused(b'{"schema_version": 2, "items": [], "providers": {}, "x": ' + constant + b"}")

    def test_a_queue_over_the_size_cap_is_refused(self):
        with patch.object(json_read, "MAX_BYTES", 8):
            self.refused(b'{"schema_version": 2, "items": [], "providers": {}}')

    def test_a_valid_queue_still_loads_unchanged(self):
        state = queue.empty_state()
        queue.save(self.path, state)
        self.assertEqual(state, queue.load(self.path))

    def test_a_lone_surrogate_in_a_provider_name_is_the_invalid_queue_refusal(self):
        self.refused(b'{"schema_version": 2, "items": [], "providers": {"x\\udc00": {"cooldown_strikes": 1}}}')

    def test_cli_status_on_a_surrogate_queue_is_invalid_data_not_internal_error(self):
        version = queue.empty_state()["schema_version"]
        self.path.write_bytes(
            b'{"schema_version": %d, "items": [], "providers": {"x\\udc00": {"cooldown_strikes": 1}}}' % version
        )
        self.assert_cli_invalid()

    def test_cli_status_on_a_deep_queue_is_invalid_data_not_internal_error(self):
        self.path.write_bytes(DEEP)
        self.assert_cli_invalid()

    def assert_cli_invalid(self):
        done = subprocess.run(
            [*CLI_ARGV, "queue", "--action", "status", "--project", str(self.tmp)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(1, done.returncode, done.stderr)
        error = json.loads(done.stderr)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("queue.json inválido", error["error"])


class PluginStateTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        environ = patch.dict(os.environ, {"GB_HOME": str(self.home)})
        environ.start()
        self.addCleanup(environ.stop)

    def refused(self, raw):
        (self.home / "plugins.json").write_bytes(raw)
        with self.assertRaises(ValueError) as caught:
            loader.read_state()
        self.assertIn("plugins.json inválido em", str(caught.exception))

    def test_deep_nesting_is_the_invalid_state_refusal(self):
        self.refused(DEEP)

    def test_nan_and_infinity_are_refused(self):
        for constant in NON_FINITE:
            with self.subTest(constant=constant):
                self.refused(b'{"enabled": {}, "x": ' + constant + b"}")

    def test_a_lone_surrogate_is_the_invalid_state_refusal(self):
        self.refused(b'{"enabled": {"x\\udc00": true}}')

    def test_a_state_over_its_own_cap_is_refused_below_the_default(self):
        padding = b" " * (loader.STATE_MAX_BYTES + 1)
        self.assertLess(loader.STATE_MAX_BYTES, json_read.MAX_BYTES)
        self.refused(b'{"enabled": {}}' + padding)

    def test_a_valid_state_still_reads_back(self):
        loader.write_state({"enabled": {}})
        self.assertEqual({}, loader.read_state()["enabled"])

    def test_cli_plugins_list_on_a_deep_state_is_invalid_data_not_internal_error(self):
        (self.home / "plugins.json").write_bytes(DEEP)
        done = subprocess.run(
            [*CLI_ARGV, "plugins", "--action", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "GB_HOME": str(self.home)},
            check=False,
        )
        self.assertEqual(1, done.returncode, done.stderr)
        self.assertEqual("INVALID_DATA", json.loads(done.stderr)["error_code"])


class TrustStoreTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)

    def write(self, raw):
        path = self.home / profile.TRUST_FILE
        path.write_bytes(raw)
        path.chmod(0o600)

    def refused(self, raw):
        self.write(raw)
        with self.assertRaises(UsageError) as caught:
            profile.read_store(self.home)
        self.assertEqual("store_corrupt", caught.exception.reason)

    def test_deep_nesting_is_store_corrupt(self):
        self.refused(b"[" * 30_000 + b"]" * 30_000)  # dentro do teto de 64 KiB do store

    def test_nan_and_infinity_are_store_corrupt(self):
        version = profile.read_store(self.home)["schema_version"]
        for constant in NON_FINITE:
            with self.subTest(constant=constant):
                self.refused(
                    b'{"schema_version": %d, "profiles": {"k": {"sha256": "a", "trusted_at": %s}}}'
                    % (version, constant)
                )

    def test_a_valid_store_still_reads(self):
        version = profile.read_store(self.home)["schema_version"]
        store = {"schema_version": version, "profiles": {}}
        self.write(json.dumps(store).encode())
        self.assertEqual(store, profile.read_store(self.home))


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = _paths.marker_path("venv", self.root)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def test_deep_or_non_finite_markers_read_as_absent_without_raising(self):
        for raw in (DEEP, b'{"schema": NaN}', b'{"schema": 1, "x": 1e400}', b"\xff"):
            with self.subTest(raw=raw[:16]):
                self.path.write_bytes(raw)
                self.assertIsNone(_paths.read_marker("venv", self.root))

    def test_a_valid_marker_still_reads(self):
        self.path.write_text(json.dumps({"schema": 1, "status": "ready"}), encoding="utf-8")
        self.assertEqual({"schema": 1, "status": "ready"}, _paths.read_marker("venv", self.root))

    def test_a_marker_with_a_surrogate_escaped_python_path_round_trips(self):
        # POSIX com bytes fora do UTF-8: `sys._base_executable` vira `str` com surrogate; o `json.dumps`
        # grava como escape `\\udcff`, e o marcador tem de continuar legível (senão a `.venv` é refeita).
        with patch.object(sys, "_base_executable", "/opt/py\udcff/bin/python3"):
            runtime_parts.write_marker("venv", self.root, "ready")
        marker = _paths.read_marker("venv", self.root)
        assert marker is not None
        self.assertEqual("/opt/py\udcff/bin/python3", marker["python"])

    def test_a_queue_style_read_of_the_same_marker_still_refuses_surrogates(self):
        self.path.write_text('{"python": "/opt/py\\udcff"}', encoding="utf-8")
        with self.assertRaises(json_read.JsonReadError):
            json_read.read(self.path)


if __name__ == "__main__":
    unittest.main()
