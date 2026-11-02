"""Listagens de pasta que os testes comparam: sem as travas que só o Windows deixa no disco.

Sem `flock`, o Windows toma duas travas de arquivo que ficam na pasta depois do comando, vazias de
propósito: a de rotação do log (`brolls/.getbrolls.log.lock`) e a do conserto do sal
(`$GB_HOME/.getbrolls-salt.lock`). O desenho está certo e o código não muda; um teste que lista a
pasta para provar que "nada mais foi criado" não pode contar esses dois nomes. No POSIX elas não
existem e a listagem sai inteira, então um arquivo desses ali continua sendo um achado.
"""

import os
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import effects, logs

LOCK_NAMES = frozenset({logs.ROTATION_LOCK, effects.SALT_REPAIR_LOCK})


def is_platform_lock(name):
    """`name` é uma das duas travas que o Windows deixa na pasta? Sempre falso fora do Windows."""
    return os.name == "nt" and name in LOCK_NAMES


def visible_entries(path):
    """Os filhos diretos de `path`, em ordem, sem as travas do Windows."""
    return sorted(child for child in Path(path).iterdir() if not is_platform_lock(child.name))


def visible_names(path):
    """Os nomes de `visible_entries(path)`."""
    return [child.name for child in visible_entries(path)]
