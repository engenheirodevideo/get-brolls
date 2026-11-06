"""Ajuda dos testes de log: lê as linhas `chave=valor` como dicionários e confere o que nunca pode vazar.

Serve às linhas de um `getbrolls.log` (com `level=` e `logger=` na frente) e às mensagens que o
`assertLogs` entrega (só `event=… chave=valor`): em ambos, `event` é o nome do evento.
"""

import json
import re
import unittest
from pathlib import Path

_KV = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|-|\S+)')


def parse_line(line):
    """Os pares `chave=valor` de uma linha: `-` vira `None` e o valor entre aspas é decodificado."""
    fields = {}
    for match in _KV.finditer(line):
        key, raw = match.group(1), match.group(2)
        if raw == "-":
            value = None
        elif raw.startswith('"'):
            value = json.loads(raw)
        else:
            value = raw
        fields[key] = value
    return fields


def events(lines, name=None):
    """Toda linha com `event=`, em ordem, como dicionário; com `name`, só os eventos desse nome."""
    rows = []
    for line in lines:
        fields = parse_line(line)
        if "event" in fields and (name is None or fields["event"] == name):
            rows.append(fields)
    return rows


def home_log(home):
    """O `$GB_HOME/logs/getbrolls.log` de `home`."""
    return Path(home) / "logs" / "getbrolls.log"


def home_log_lines(home):
    """As linhas do log da instalação de `home` (lista vazia quando o arquivo não existe)."""
    path = home_log(home)
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


class Rows:
    """Leitura das linhas que o `assertLogs` capturou."""

    def __init__(self, capture):
        self.capture = capture

    def lines(self):
        return [record.getMessage() for record in self.capture.records]

    def text(self):
        return "\n".join(self.lines())

    def all(self, name=None):
        return events(self.lines(), name)

    def one(self, name, **expected):
        found = [row for row in self.all(name) if all(row.get(key) == value for key, value in expected.items())]
        if len(found) != 1:
            raise AssertionError(f"esperava 1 evento {name} {expected}, achei {len(found)} em:\n{self.text()}")
        return found[0]

    def level_of(self, name, **expected):
        for record in self.capture.records:
            row = events([record.getMessage()], name)
            if row and all(row[0].get(key) == value for key, value in expected.items()):
                return record.levelno
        raise AssertionError(f"sem evento {name} {expected}")


class LogCase(unittest.TestCase):
    """Base de quem captura os eventos de `getbrolls` e confere que nada sensível vazou."""

    def captured(self, level="DEBUG"):
        return self.assertLogs("getbrolls", level=level)

    def rows(self, manager):
        return Rows(manager)

    def assert_clean(self, rows, *sentinels):
        text = rows.text()
        for sentinel in sentinels:
            self.assertNotIn(str(sentinel), text)
