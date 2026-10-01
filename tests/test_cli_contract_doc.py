"""`docs/CLI_CONTRACT.md` descreve o contrato que a CLI realmente cumpre.

As tabelas do documento são conferidas contra o código, nos dois sentidos: um código
de saída, de erro ou de aviso novo no código precisa de uma linha no documento, e uma
linha do documento precisa existir no código. O erro de uso e o erro de operação são
conferidos numa execução real, com stderr fora de um terminal.
"""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV, ROOT

from getbrolls import capabilities, cli, config, runtime

DOC = ROOT / "docs" / "CLI_CONTRACT.md"
SOURCES = ROOT / "scripts" / "getbrolls"
# `record_warning("CODE", …)`, com a quebra de linha que o formatador põe depois do parêntese.
WARNING_CALL = re.compile(r'record_warning\(\s*"([A-Z][A-Z0-9_]*)"')
# Aviso montado à mão (`{"code": "LOG_UNAVAILABLE", …}`) em vez de `record_warning`.
WARNING_LITERAL = re.compile(r'"code":\s*"([A-Z][A-Z0-9_]*)"')
ERROR_LITERAL = re.compile(r'"error_code":\s*"([A-Z][A-Z0-9_]*)"')
# Códigos de `problems` do `analysis --action check`: não são avisos, têm a própria lista.
ANALYSIS_PROBLEMS = (
    "MEDIA_ID_MISMATCH",
    "MISSING_FILE",
    "ORPHAN_MEDIA",
    "SCHEMA_VIOLATION",
    "UNKNOWN_FILE",
    "UNSAFE_LINK",
)


def doc_text():
    return DOC.read_text(encoding="utf-8")


def section(heading):
    """Texto de `## heading` até o próximo `## `."""
    text = doc_text()
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def code_cells(text):
    """Primeira célula, sem crases, de cada linha de tabela de `text` que começa com um nome entre crases."""
    return [match[1] for match in re.finditer(r"^\| `([^`]+)` \|", text, re.MULTILINE)]


def source_texts():
    for path in sorted(SOURCES.rglob("*.py")):
        yield path.read_text(encoding="utf-8")


def warning_codes_in_code():
    found = set()
    for text in source_texts():
        found.update(WARNING_CALL.findall(text))
        found.update(WARNING_LITERAL.findall(text))
    return found - set(ANALYSIS_PROBLEMS)


def run_cli(*args, cwd=None):
    return subprocess.run(
        [*CLI_ARGV, *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
        cwd=cwd,
    )


class ExitAndErrorCodes(unittest.TestCase):
    def test_every_exit_code_has_its_row_and_no_row_is_invented(self):
        rows = {
            (match[1], match[2])
            for match in re.finditer(r"^\| `(\d+)` \| `([a-z]+)` \|", section("Códigos de saída"), re.MULTILINE)
        }
        expected = {(str(code), name) for code, name, _meaning in cli.EXIT_CODES}
        self.assertEqual(expected, rows)

    def test_every_error_code_has_its_row_with_the_right_exit(self):
        rows = {
            match[1]: int(match[2])
            for match in re.finditer(r"^\| `([A-Z_]+)` \| `(\d+)` \|", section("Códigos de erro"), re.MULTILINE)
        }
        self.assertEqual(set(capabilities.ERROR_CODES), set(rows))
        for code, exit_code in rows.items():
            with self.subTest(code=code):
                self.assertEqual(runtime.exit_code_for(code), exit_code)

    def test_the_catalog_covers_every_error_code_the_code_can_emit(self):
        emitted = set(runtime.ERROR_EXIT)
        for text in source_texts():
            emitted.update(ERROR_LITERAL.findall(text))
        self.assertLessEqual(emitted, set(capabilities.ERROR_CODES))


class Warnings(unittest.TestCase):
    def test_every_warning_code_in_the_code_is_documented(self):
        documented = set(code_cells(section("Avisos (`warnings`)")))
        missing = sorted(warning_codes_in_code() - documented)
        self.assertEqual([], missing)

    def test_no_documented_warning_is_invented(self):
        documented = set(code_cells(section("Avisos (`warnings`)")))
        self.assertEqual([], sorted(documented - warning_codes_in_code()))

    def test_analysis_problem_codes_are_named(self):
        text = section("Avisos (`warnings`)")
        for code in ANALYSIS_PROBLEMS:
            with self.subTest(code=code):
                self.assertIn(f"`{code}`", text)
        joined = "\n".join(source_texts())
        for code in ANALYSIS_PROBLEMS:
            with self.subTest(code=code):
                self.assertIn(f'"{code}"', joined)


class RealErrors(unittest.TestCase):
    def test_usage_error_outside_a_terminal_has_exactly_the_documented_keys(self):
        result = run_cli("status", "--projetc", "x")
        self.assertEqual(cli.EXIT_USAGE_ERROR, result.returncode)
        payload = json.loads(result.stderr.strip().splitlines()[-1])
        example = re.search(r"```json\n(.+?)\n\s*```", section("Erro de uso"), re.DOTALL)
        assert example is not None
        documented = json.loads(example[1])
        self.assertEqual(set(documented), set(payload))
        self.assertEqual("USAGE_ERROR", payload["error_code"])
        self.assertEqual("--project", payload["suggestion"])
        self.assertEqual("", result.stdout)

    def test_operation_error_fields_are_all_documented(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("approve", "--candidate", "nope", "--by", "x", "--project", Path(tmp) / "p")
        self.assertEqual(cli.EXIT_OPERATION_ERROR, result.returncode)
        payload = json.loads(result.stderr.strip().splitlines()[-1])
        documented = set(code_cells(section("Erro de operação")))
        self.assertLessEqual(set(payload), documented)
        self.assertNotIn("traceback", payload)
        self.assertNotIn("repr", payload)
        self.assertIn("hint", documented)


class ReadOnlyAndQuiet(unittest.TestCase):
    def test_every_read_only_command_is_listed(self):
        text = section("O que só lê")
        for command in runtime.READ_ONLY_COMMANDS:
            with self.subTest(command=command):
                self.assertRegex(text, rf"(?m)^\| `{re.escape(command)}` \|", command)

    def test_every_read_only_action_is_listed_under_its_command(self):
        text = section("O que só lê")
        for command, action in sorted(runtime.READ_ONLY_ACTIONS):
            with self.subTest(command=command, action=action):
                row = re.search(rf"^\| `{re.escape(command)}` \| (.+) \|$", text, re.MULTILINE)
                self.assertIsNotNone(row, command)
                assert row is not None
                self.assertIn(f"`{action}`", row[1])

    def test_quiet_error_commands_are_named(self):
        text = section("Erro de operação")
        for command in runtime.QUIET_ERROR_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(f"`{command}`", text)


class EnvironmentAndCapabilities(unittest.TestCase):
    def test_every_core_variable_is_listed(self):
        text = section("Variáveis de ambiente")
        for key in sorted(config.KEYS | config.PROCESS_ONLY_KEYS):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", text)

    def test_every_capabilities_key_is_described(self):
        manifest = capabilities.describe(cli.build_parser())
        text = section("`capabilities --json`")
        for key in (*manifest, "plugins_error", "marketplaces_error"):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", text)
        self.assertIn(f"(`{capabilities.SCHEMA_VERSION}`)", text)
        for key in manifest["commands"][0]:
            with self.subTest(command_key=key):
                self.assertIn(f"`{key}`", text)

    def test_env_file_order_and_alias_prefix_are_stated(self):
        text = section("Precedência: `.env`, perfil e padrão")
        order = [text.index(marker) for marker in ("`--env-file", "`GB_ENV_FILE`", "checkout", "`$GB_HOME/.env`")]
        self.assertEqual(sorted(order), order)
        self.assertIn("`GETBROLLS_X`", section("Variáveis de ambiente"))


if __name__ == "__main__":
    unittest.main()
