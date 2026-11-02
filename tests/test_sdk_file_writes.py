"""The two helpers every private writer shares: refuse a link at the name (`files.refuse_link`), close the handle
when `fdopen` fails (`effects.owned_stream`)."""

import errno
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

from getbrolls import effects
from getbrolls.sdk import files


class RefuseLinkTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-file-writes-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    @needs_symlink
    def test_a_link_is_refused_as_a_loop_whether_or_not_it_resolves(self):
        (self.base / "real.txt").write_text("x", encoding="utf-8")
        for name, target in (("para-arquivo", "real.txt"), ("pendente", "nao-existe.txt")):
            with self.subTest(name=name):
                (self.base / name).symlink_to(self.base / target)
                with self.assertRaises(OSError) as caught:
                    files.refuse_link(self.base / name)
                self.assertEqual(errno.ELOOP, caught.exception.errno)

    def test_a_plain_file_and_a_missing_name_pass(self):
        (self.base / "real.txt").write_text("x", encoding="utf-8")
        files.refuse_link(self.base / "real.txt")
        files.refuse_link(self.base / "novo.txt")

    def test_a_junction_counts_as_a_link(self):
        with patch.object(files, "is_link", return_value=True), self.assertRaises(OSError) as caught:
            files.refuse_link(self.base / "juncao")
        self.assertEqual(errno.ELOOP, caught.exception.errno)


class OwnedStreamTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="gb-file-writes-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.path = base / "arquivo.txt"

    def test_it_returns_a_stream_that_is_the_only_closer(self):
        handle = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        with effects.owned_stream(handle) as stream:
            stream.write(b"ok")
        self.assertEqual(b"ok", self.path.read_bytes())
        with self.assertRaises(OSError):
            os.fstat(handle)

    def test_a_failure_closes_the_handle_once_and_keeps_the_error(self):
        handle = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
        with (
            patch.object(effects.os, "fdopen", side_effect=OSError(errno.ENOSPC, "cheio")),
            patch.object(effects.os, "close", wraps=os.close) as closed,
            self.assertRaises(OSError) as caught,
        ):
            effects.owned_stream(handle)
        self.assertEqual(errno.ENOSPC, caught.exception.errno)
        closed.assert_called_once_with(handle)
        with self.assertRaises(OSError):
            os.fstat(handle)

    def test_a_handle_that_io_open_already_closed_does_not_replace_the_error(self):
        handle = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)

        def closes_then_fails(fd, _mode):
            os.close(fd)
            raise ValueError("modo inválido")

        with patch.object(effects.os, "fdopen", side_effect=closes_then_fails), self.assertRaises(ValueError):
            effects.owned_stream(handle)
