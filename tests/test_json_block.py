"""Pino de caracterização de `read_json_block` e dos dois chamadores que ele substitui.

`getbrolls.brief.load_brief` e `getbrolls.rules.read_rules_block` compartilham a mesma
extração de "exatamente um bloco ```json", mas com textos de erro diferentes. Este
teste fixa tipo de exceção + mensagem, caractere a caractere, para os dois chamadores
nos três modos de falha (nenhum bloco, JSON inválido, e — só em `rules` — bloco que não
é objeto) e para o caminho de sucesso, para garantir que a extração do helper
compartilhado não mudou nenhum comportamento observável.
"""

import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import brief as brief_module
from getbrolls import rules as rules_module

BRIEF_MISSING = (
    "O BRIEF.md precisa de exatamente um bloco ```json — apague os blocos extras "
    "ou rode `init-brief` numa pasta limpa para começar de um modelo."
)
BRIEF_SYNTAX = (
    "O bloco json do BRIEF.md está com erro de digitação (vírgula ou aspas "
    "sobrando). Conserte essa linha e rode `brief --validate` de novo."
)
RULES_MISSING = (
    "O RULES.md precisa de exatamente um bloco ```json — apague os blocos "
    "extras ou rode `init-rules --force` para gerar um arquivo limpo."
)
RULES_SYNTAX = (
    "O bloco json do RULES.md está com erro de digitação (vírgula ou aspas "
    "sobrando). Rode `init-rules --force` para gerar um arquivo limpo."
)
RULES_NOT_OBJECT = (
    "O bloco json do RULES.md tem que ser um objeto entre chaves. Rode "
    "`init-rules --force` para gerar um arquivo limpo."
)


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class LoadBriefJsonBlockTests(unittest.TestCase):
    """`getbrolls.brief.load_brief`, isolado da checagem de existência do arquivo."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "BRIEF.md"

    def _load(self):
        # load_brief() lê `brief_path(project)`; usamos GB_BRIEF_FILE para apontar
        # direto ao arquivo do teste sem depender do layout de projeto.
        import os

        old = os.environ.get("GB_BRIEF_FILE")
        os.environ["GB_BRIEF_FILE"] = str(self.path)
        try:
            return brief_module.load_brief(str(self.path.parent))
        finally:
            if old is None:
                os.environ.pop("GB_BRIEF_FILE", None)
            else:
                os.environ["GB_BRIEF_FILE"] = old

    def test_no_block_raises_missing_message(self):
        write(self.path, "# Brief\n\nSem bloco nenhum.\n")
        with self.assertRaises(ValueError) as ctx:
            self._load()
        self.assertEqual(BRIEF_MISSING, str(ctx.exception))

    def test_two_blocks_raises_missing_message(self):
        write(
            self.path,
            '# Brief\n\n```json\n{"a": 1}\n```\n\nOutro:\n\n```json\n{"b": 2}\n```\n',
        )
        with self.assertRaises(ValueError) as ctx:
            self._load()
        self.assertEqual(BRIEF_MISSING, str(ctx.exception))

    def test_invalid_json_raises_syntax_message(self):
        write(self.path, '# Brief\n\n```json\n{"a": 1,}\n```\n')
        with self.assertRaises(ValueError) as ctx:
            self._load()
        self.assertEqual(BRIEF_SYNTAX, str(ctx.exception))

    def test_success_returns_the_decoded_value(self):
        write(self.path, '# Brief\n\n```json\n{"a": 1, "b": [1, 2]}\n```\n')
        self.assertEqual({"a": 1, "b": [1, 2]}, self._load())


class ReadRulesBlockTests(unittest.TestCase):
    """`getbrolls.rules.read_rules_block` recebe o path direto, sem passos extras."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "RULES.md"

    def test_no_block_raises_missing_message(self):
        write(self.path, "# Regras\n\nSem bloco nenhum.\n")
        with self.assertRaises(ValueError) as ctx:
            rules_module.read_rules_block(self.path)
        self.assertEqual(RULES_MISSING, str(ctx.exception))

    def test_two_blocks_raises_missing_message(self):
        write(
            self.path,
            '# Regras\n\n```json\n{"a": 1}\n```\n\nOutro:\n\n```json\n{"b": 2}\n```\n',
        )
        with self.assertRaises(ValueError) as ctx:
            rules_module.read_rules_block(self.path)
        self.assertEqual(RULES_MISSING, str(ctx.exception))

    def test_invalid_json_raises_syntax_message(self):
        write(self.path, '# Regras\n\n```json\n{"a": 1,}\n```\n')
        with self.assertRaises(ValueError) as ctx:
            rules_module.read_rules_block(self.path)
        self.assertEqual(RULES_SYNTAX, str(ctx.exception))

    def test_non_object_raises_not_object_message(self):
        write(self.path, "# Regras\n\n```json\n[1, 2, 3]\n```\n")
        with self.assertRaises(ValueError) as ctx:
            rules_module.read_rules_block(self.path)
        self.assertEqual(RULES_NOT_OBJECT, str(ctx.exception))

    def test_success_returns_the_decoded_object(self):
        write(self.path, '# Regras\n\n```json\n{"a": 1, "b": [1, 2]}\n```\n')
        self.assertEqual({"a": 1, "b": [1, 2]}, rules_module.read_rules_block(self.path))


if __name__ == "__main__":
    unittest.main()
