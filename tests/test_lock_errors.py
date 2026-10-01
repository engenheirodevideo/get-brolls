"""Trava ocupada é `LockedError` (LOCKED, saída 1), não erro de uso nem de dados.

O store de perfis confiáveis e o índice da biblioteca pessoal entram na mesma família
das outras travas (projeto, analysis/, runtime, plugins, clientes, templates).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

from getbrolls import library, profile, runtime
from getbrolls.errors import LockedError


class LockedErrorTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gb-locks-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def test_busy_trust_store_is_locked_error(self):
        with (
            patch.object(profile, "LOCK_TIMEOUT_S", 0.0),
            patch.object(runtime, "_acquire_lock", side_effect=BlockingIOError),
            self.assertRaises(LockedError) as raised,
        ):
            profile._update_store(self.home, lambda _profiles: False)  # pylint: disable=protected-access
        self.assertIn(profile.TRUST_FILE, str(raised.exception))

    def test_busy_library_index_is_locked_error(self):
        with patch.dict(os.environ, {"GB_HOME": str(self.home)}):
            lock = library.library_dir() / "index.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("", encoding="utf-8")
            with (
                patch.object(library, "LOCK_TIMEOUT_S", 0.0),
                self.assertRaises(LockedError) as raised,
                library._locked(),  # pylint: disable=protected-access
            ):
                pass
        self.assertIn("index.lock", str(raised.exception))
        self.assertTrue(lock.exists(), "a trava de outro processo não é apagada")


if __name__ == "__main__":
    unittest.main()
