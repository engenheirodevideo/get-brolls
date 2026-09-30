"""Python abaixo do 3.11: `gb.py` e os instaladores param com mensagem legível e código 4.

Não há um Python 3.10 garantido nesta máquina: o guarda do `gb.py` é testado pela função
dele (com uma versão de mentira) e por um `ast.parse` com a gramática mais antiga que o
`ast` aceita; o `install.sh`, com um `python3` de mentira no `PATH`.
"""

import ast
import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

GB = ROOT / "scripts" / "gb.py"
MESSAGE = "getbrolls precisa de Python 3.11 ou mais novo; você tem 3.10."


def _load_shim():
    spec = importlib.util.spec_from_file_location("gb_shim", GB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShimGuardTests(unittest.TestCase):
    def test_old_python_gets_the_friendly_message(self):
        shim = _load_shim()
        self.assertEqual(MESSAGE, shim.python_too_old((3, 10, 14)))
        self.assertIsNone(shim.python_too_old((3, 11, 0)))
        self.assertIsNone(shim.python_too_old((3, 14, 0)))

    def test_old_python_exits_4_before_importing_the_package(self):
        shim = _load_shim()
        err = io.StringIO()
        with (
            patch.object(shim.sys, "version_info", (3, 10, 14, "final", 0)),
            patch.dict(shim.sys.modules, {"getbrolls.cli": None}),  # importar levantaria ImportError
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(4, shim.main())
        self.assertEqual(MESSAGE + "\n", err.getvalue())

    def test_shim_parses_with_an_old_grammar(self):
        """Nada de sintaxe nova antes do guarda: o Python antigo precisa chegar até ele."""
        ast.parse(GB.read_text(encoding="utf-8"), feature_version=(3, 6))


@unittest.skipIf(os.name == "nt", "install.sh é o instalador POSIX")
class InstallShGuardTests(unittest.TestCase):
    def test_install_sh_stops_with_the_friendly_message_and_exit_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "python3"
            # Responde como um Python 3.10: a conferência de versão falha e a versão é 3.10.
            fake.write_text(
                '#!/bin/sh\ncase "$2" in *exit*) exit 1;; *) echo 3.10;; esac\n',
                encoding="utf-8",
            )
            fake.chmod(0o755)
            env = {"PATH": f"{tmp}:/usr/bin:/bin", "HOME": tmp}
            done = subprocess.run(
                ["/bin/bash", str(ROOT / "scripts" / "install.sh"), "--check"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=60,
                check=False,
            )
        self.assertEqual(4, done.returncode, done.stdout + done.stderr)
        self.assertIn(MESSAGE, done.stderr)

    def test_install_ps1_has_the_same_guard(self):
        text = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
        self.assertIn("getbrolls precisa de Python 3.11 ou mais novo; você tem", text)
        self.assertIn("exit 4", text)


if __name__ == "__main__":
    unittest.main()
