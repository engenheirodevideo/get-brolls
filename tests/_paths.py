"""Caminhos comuns aos testes: raiz do repositório, CLI e o par SKILL.md.

Também escolhe *qual* CLI os testes de subprocesso exercitam:

- `GB_TEST_CLI=checkout` (padrão): `python scripts/gb.py`, direto do repositório.
- `GB_TEST_CLI=wheel`: `GB_TEST_WHEEL_PYTHON -P -m getbrolls`, o pacote instalado
  de um wheel num venv fora do repositório (`-P` impede o diretório atual de
  entrar em `sys.path`; `_isolation` também remove `PYTHONPATH` nesse modo).

Só os subprocessos mudam. Os testes em processo (`cli.main`, imports de
`getbrolls`) exercitam sempre o checkout, em qualquer modo.

Importar este módulo tem o efeito colateral de inserir `scripts/` em
`sys.path`, exatamente como cada arquivo de teste faz hoje na própria
abertura — aqui isso acontece uma vez só, no import.
"""

import functools
import importlib
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "gb.py"
SKILLS = (ROOT / "SKILL.md", ROOT / "skills" / "get-brolls" / "SKILL.md")

sys.path.insert(0, str(ROOT / "scripts"))

MODE = os.environ.get("GB_TEST_CLI", "checkout")
if MODE not in {"checkout", "wheel"}:
    raise RuntimeError(f"GB_TEST_CLI deve ser 'checkout' ou 'wheel', não {MODE!r}.")
WHEEL_MODE = MODE == "wheel"
WHEEL_PYTHON = os.environ.get("GB_TEST_WHEEL_PYTHON")
if WHEEL_MODE and not WHEEL_PYTHON:
    raise RuntimeError("GB_TEST_CLI=wheel exige GB_TEST_WHEEL_PYTHON com o python do venv que tem o wheel instalado.")

# O que todo teste de subprocesso usa no lugar de `[sys.executable, CLI]`.
CLI_ARGV: tuple[str, ...] = (str(WHEEL_PYTHON), "-P", "-m", "getbrolls") if WHEEL_MODE else (sys.executable, str(CLI))


def cli(*args) -> list[str]:
    """Argv completo de uma chamada à CLI sob teste."""
    return [*CLI_ARGV, *map(str, args)]


def wheel_origin(version_output: str) -> str | None:
    """Origem dos dados que `--version` informa ("wheel" ou "checkout"); None se não informa."""
    found = re.search(r"\b(wheel|checkout)\b", version_output, re.IGNORECASE)
    return found.group(1).lower() if found else None


@functools.cache
def _wheel_cli_command() -> tuple[str, ...]:
    """Prefixo que o pacote instalado põe nos comandos sugeridos (perguntado a ele, uma vez)."""
    probe = "import json; from getbrolls import _paths; print(json.dumps(_paths.cli_command()))"
    done = subprocess.run(
        [str(WHEEL_PYTHON), "-P", "-c", probe], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return tuple(json.loads(done.stdout))


def _install():
    # Import tardio: `_isolation` precisa vir antes de qualquer `getbrolls`.
    return importlib.import_module("getbrolls._paths")


def command_text(*args: str, os_name: str | None = None) -> str:
    """Comando completo da CLI desta instalação, como o `status` o escreveria."""
    install = _install()
    return " ".join([install.cli_prefix_text(os_name), *(install.quote_arg(arg, os_name) for arg in args)])


def _nt_backslashes(text: str, start: int) -> tuple[str, int]:
    """Barras a partir de `start`: (texto literal, próximo índice)."""
    end = start
    while end < len(text) and text[end] == "\\":
        end += 1
    count = end - start
    if end < len(text) and text[end] == '"':
        if count % 2:
            return "\\" * (count // 2) + '"', end + 1
        return "\\" * (count // 2), end
    return "\\" * count, end


def _nt_token(text: str, start: int) -> tuple[str, int]:
    """Um argumento pelas regras do CommandLineToArgvW, a partir de `start`."""
    out: list[str] = []
    quoted = False
    index = start
    while index < len(text):
        char = text[index]
        if char in " \t" and not quoted:
            break
        if char == "\\":
            literal, index = _nt_backslashes(text, index)
            out.append(literal)
        elif char == '"':
            if quoted and text[index + 1 : index + 2] == '"':
                out.append('"')
                index += 2
            else:
                quoted = not quoted
                index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out), index


def _split_nt(text: str) -> Iterator[str]:
    index = 0
    while True:
        while index < len(text) and text[index] in " \t":
            index += 1
        if index >= len(text):
            return
        token, index = _nt_token(text, index)
        yield token


def split_command(text: str, os_name: str | None = None) -> list[str]:
    """Inverso de `command_text`: o argv que o terminal do sistema veria (sem SO, o da instalação)."""
    system = os_name if os_name is not None else _install()._os_name()  # pylint: disable=protected-access
    return list(_split_nt(text)) if system == "nt" else shlex.split(text)


def suggested_argv(command: str) -> list[str]:
    """Tira o prefixo da CLI de um comando sugerido (status/brief), no formato do SO.

    O comando gerado em processo (`command_for`, `next_action`) traz o prefixo do checkout
    que roda os testes. No modo wheel, o que sai de um subprocesso traz o do pacote
    instalado; aí vale um dos dois, e nenhum outro.
    """
    install = _install()
    tokens = split_command(command)
    prefixes = [install.cli_command()]
    if WHEEL_MODE:
        prefixes.append(list(_wheel_cli_command()))
    for prefix in prefixes:
        if tokens[: len(prefix)] == prefix:
            return tokens[len(prefix) :]
    raise AssertionError((tokens, prefixes))
