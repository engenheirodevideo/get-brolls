"""No Windows o módulo de log carrega sem `fcntl` (a rotação do log do projeto não usa `flock`).

O teste carrega uma cópia de `getbrolls.logs` com `sys.platform` trocado; o módulo em uso fica intacto.
"""

import importlib.util
import logging
import sys
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import logs


def _load_copy(platform):
    """Uma cópia nova de `getbrolls.logs`, executada como se `sys.platform` fosse `platform`."""
    spec = importlib.util.spec_from_file_location("getbrolls._logs_platform_copy", logs.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "platform", platform):
        spec.loader.exec_module(module)
    return module


@unittest.skipIf(sys.platform == "win32", "no Windows o módulo em uso já carrega sem fcntl")
class WithoutFcntlTests(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger("getbrolls")
        handlers = list(root.handlers)
        self.addCleanup(setattr, root, "handlers", handlers)

    def test_on_windows_the_module_loads_with_no_fcntl(self):
        copy = _load_copy("win32")
        self.assertIsNone(copy.fcntl)
        self.assertIsNotNone(logs.fcntl, "a cópia não pode mexer no módulo em uso")

    def test_elsewhere_the_module_keeps_fcntl(self):
        self.assertIsNotNone(_load_copy("linux").fcntl)


if __name__ == "__main__":
    unittest.main()
