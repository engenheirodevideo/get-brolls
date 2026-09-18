"""Caminhos comuns aos testes: raiz do repositório, CLI e o par SKILL.md.

Importar este módulo tem o efeito colateral de inserir `scripts/` em
`sys.path`, exatamente como cada arquivo de teste faz hoje na própria
abertura — aqui isso acontece uma vez só, no import.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "gb.py"
SKILLS = (ROOT / "SKILL.md", ROOT / "skills" / "get-brolls" / "SKILL.md")

sys.path.insert(0, str(ROOT / "scripts"))
