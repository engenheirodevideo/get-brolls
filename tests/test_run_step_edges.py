"""bootstrap_steps.py: eco no terminal, queda do grupo de processos e a trava de raiz nunca tomada."""

import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import bootstrap, bootstrap_steps

WINDOWS = sys.platform == "win32"


class RunStepEdgeTests(unittest.TestCase):
    def test_on_a_terminal_the_output_is_echoed_to_stderr_and_still_kept(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True

        terminal = Terminal()
        with patch.object(sys, "stderr", terminal):
            done = bootstrap.run_step([sys.executable, "-c", "print('first'); print('second')"], timeout=60, label="t")
        self.assertEqual(0, done.returncode)
        self.assertEqual(("first", "second"), done.output)
        self.assertEqual("first\nsecond\n", terminal.getvalue())

    def test_off_a_terminal_nothing_is_echoed(self):
        quiet = io.StringIO()
        with patch.object(sys, "stderr", quiet):
            done = bootstrap.run_step([sys.executable, "-c", "print('first')"], timeout=60, label="t")
        self.assertEqual(("first",), done.output)
        self.assertEqual("", quiet.getvalue())

    @unittest.skipIf(WINDOWS, "process groups are POSIX")
    def test_a_timeout_still_kills_the_step_where_process_groups_are_missing(self):
        with patch.object(bootstrap_steps.os, "killpg", None):
            started = time.monotonic()
            done = bootstrap.run_step([sys.executable, "-c", "import time; time.sleep(60)"], timeout=2, label="t")
        self.assertLess(time.monotonic() - started, 30)
        self.assertEqual(124, done.returncode)


class RootLockExitTests(unittest.TestCase):
    def test_leaving_a_lock_that_was_never_taken_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = bootstrap_steps._RootLock(Path(tmp))  # pylint: disable=protected-access
            self.assertIsNone(lock.__exit__(None, None, None))
            self.assertEqual([], list(Path(tmp).iterdir()))


if __name__ == "__main__":
    unittest.main()
