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
import subprocess
import sys
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


def suggested_argv(command: str) -> list[str]:
    """Tira o prefixo da CLI de um comando sugerido (status/brief), no formato do SO.

    O comando gerado em processo (`command_for`, `next_action`) traz o prefixo do checkout
    que roda os testes. No modo wheel, o que sai de um subprocesso traz o do pacote
    instalado; aí vale um dos dois, e nenhum outro.
    """
    # Import tardio: `_isolation` precisa vir antes de qualquer `getbrolls`.
    install = importlib.import_module("getbrolls._paths")
    tokens = install.split_command(command)
    prefixes = [install.cli_command()]
    if WHEEL_MODE:
        prefixes.append(list(_wheel_cli_command()))
    for prefix in prefixes:
        if tokens[: len(prefix)] == prefix:
            return tokens[len(prefix) :]
    raise AssertionError((tokens, prefixes))
