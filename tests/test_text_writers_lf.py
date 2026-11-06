"""Fim de linha LF em todo escritor de texto: o CHANGELOG promete LF no Windows.

Um arquivo aberto em modo texto para escrita (`open`, `Path.open`, `os.fdopen`, `write_text`) sem
`newline="\\n"` grava CRLF no Windows e muda os bytes (e o hash) do que o POSIX grava. A varredura abaixo
olha o código-fonte em vez de uma lista de arquivos: um escritor novo sem `newline=` falha aqui.
"""

import ast
import builtins
import io
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import acquisition, effects, library, queue, rules

SOURCES = sorted([*(ROOT / "scripts").glob("*.py"), *(ROOT / "scripts" / "getbrolls").rglob("*.py")])
_MODE = re.compile(r"[rwxa][bt+]*")

# Aberturas de escrita em modo texto que ficam sem `newline=`, cada uma com o motivo. Vazia de propósito:
# até o handle de trava (`a+`), onde quase nada é gravado, usa `newline="\\n"` como os demais.
EXCEPTIONS: dict[str, str] = {}
_TEMPORARY_FILES = {"NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}


def _mode_of(call, position):
    """O modo de abertura de `call` (texto) ou `None` quando não há modo constante."""
    for keyword in call.keywords:
        if keyword.arg == "mode":
            node = keyword.value
            break
    else:
        if len(call.args) <= position:
            return "r"
        node = call.args[position]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _text_mode(mode):
    """`mode` (texto, ou `None` quando não é constante) escreve em modo texto?"""
    if mode is None:
        return True  # a mode it cannot read: look at it by hand
    return any(char in mode for char in "wax") and "b" not in mode


def _module_writes_text(module, call):
    """Escritores da biblioteca padrão fora de `open`: `io.open`, `gzip.open(..., "wt")`, `tempfile.*`.
    Devolve `None` quando `call` não é um deles."""
    attr = call.func.attr
    if module == "io" and attr == "open":
        return _text_mode(_mode_of(call, 1))
    if module in {"gzip", "bz2", "lzma"} and attr == "open":
        mode = _mode_of(call, 1)
        return mode is None or ("t" in mode and _text_mode(mode.replace("t", "")))  # binary unless "t"
    if module == "tempfile" and attr in _TEMPORARY_FILES:
        mode = _mode_of(call, 1 if attr == "SpooledTemporaryFile" else 0)
        return mode is None or (mode != "r" and any(char in mode for char in "wax+") and "b" not in mode)
    return None


def _writes_text(call):
    """`call` abre um arquivo em modo texto para escrever (ou é `Path.write_text`)?"""
    func = call.func
    module = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
    known = _module_writes_text(module, call) if module else None
    if known is not None:
        return known
    if (isinstance(func, ast.Name) and func.id == "open") or (
        isinstance(func, ast.Attribute) and func.attr == "fdopen"
    ):
        return _text_mode(_mode_of(call, 1))
    if isinstance(func, ast.Attribute) and func.attr == "open":
        first = call.args[0] if call.args else None
        # `opener.open(request)`, `Image.open(path)`, `os.open(...)`: not a file mode
        not_a_file_mode = first is not None and not (
            isinstance(first, ast.Constant) and _MODE.fullmatch(str(first.value))
        )
        return module not in {"os", "opener", "urllib"} and not not_a_file_mode and _text_mode(_mode_of(call, 0))
    if isinstance(func, ast.Attribute) and func.attr == "write_text":
        # `effects.write_text` has its own `newline` (checked in a test of its own below).
        return module != "effects"
    return False


def _has_lf(call):
    for keyword in call.keywords:
        if keyword.arg == "newline":
            return isinstance(keyword.value, ast.Constant) and keyword.value.value == "\n"
    return False


class TextWritersTests(unittest.TestCase):
    def test_every_text_writer_asks_for_lf(self):
        missing = []
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not _writes_text(node) or _has_lf(node):
                    continue
                where = f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                if where not in EXCEPTIONS:
                    missing.append(where)
        self.assertEqual([], missing, 'cada escritor de texto precisa de newline="\\n" (ou uma exceção com motivo)')

    def test_the_sweep_sees_what_it_is_meant_to_see(self):
        def calls(source):
            return [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)]

        for text in (
            'open(p, "w", encoding="utf-8")',
            'os.fdopen(fd, "w", encoding="utf-8")',
            'p.open("x", encoding="utf-8")',
            'p.open(mode="a", encoding="utf-8")',
            'p.write_text("x", encoding="utf-8")',
            'io.open(p, "w", encoding="utf-8")',
            'gzip.open(p, "wt", encoding="utf-8")',
            'tempfile.NamedTemporaryFile(mode="w", delete=False)',
            "tempfile.NamedTemporaryFile(mode=kind)",
            'open(p, "a+")',
            "open(p, mode)",
        ):
            with self.subTest(text=text):
                self.assertTrue(any(_writes_text(call) and not _has_lf(call) for call in calls(text)))
        for text in (
            'open(p, "w", encoding="utf-8", newline="\\n")',
            'open(p, "wb")',
            'p.open("xb")',
            'io.open(p, "rb")',
            'gzip.open(p, "wb")',
            "gzip.open(p)",
            "tempfile.NamedTemporaryFile(delete=False)",
            'tempfile.NamedTemporaryFile("w+b")',
            'tempfile.NamedTemporaryFile(mode="w", newline="\\n")',
            'p.open("r", encoding="utf-8")',
            "open(p)",
            "opener.open(request, timeout=30)",
            "os.open(path, flags)",
            "effects.write_text(p, t, encoding='utf-8')",
        ):
            with self.subTest(text=text):
                self.assertFalse(any(_writes_text(call) and not _has_lf(call) for call in calls(text)))

    def test_effects_write_text_defaults_to_lf(self):
        seen = {}

        def fake_open(_self, mode="r", **kwargs):
            seen.update(mode=mode, **kwargs)
            raise StopIteration  # the file is never touched

        with (
            mock.patch("pathlib.Path.open", fake_open),
            mock.patch.object(effects, "op"),
            self.assertRaises(StopIteration),
        ):
            effects.write_text(ROOT / "nao-existe.txt", "a\nb\n", encoding="utf-8")
        self.assertEqual("\n", seen["newline"])
        self.assertEqual("w", seen["mode"])


class WindowsDefaultTests(unittest.TestCase):
    """The writers run with the Windows default (`newline=None` writes `\\r\\n`) and still write only `\\n`."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-lf-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        real_open = io.open

        def windows_open(file, mode="r", **kwargs):
            # A text file opened without `newline=` writes `\n` as `\r\n`, as on Windows.
            writes = any(char in mode for char in "wax") and "b" not in mode
            if writes and kwargs.get("newline") is None:
                kwargs["newline"] = "\r\n"
            return real_open(file, mode, **kwargs)

        def windows_path_open(path, mode="r", **kwargs):
            return windows_open(path, mode, **kwargs)

        for target, replacement in (
            (builtins, ("open", windows_open)),
            (os, ("fdopen", windows_open)),
            (Path, ("open", windows_path_open)),
        ):
            patcher = mock.patch.object(target, replacement[0], replacement[1])
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_simulation_does_write_crlf_without_newline(self):
        path = self.tmp / "crlf.txt"
        with path.open("w", encoding="utf-8") as stream:
            stream.write("a\nb\n")
        self.assertEqual(b"a\r\nb\r\n", path.read_bytes())

    def test_the_writers_keep_lf(self):
        queue_path = self.tmp / "queue" / "queue.json"
        queue.save(queue_path, {"version": 1, "items": []})
        acquisition_cache = self.tmp / "cache"
        acquisition_cache.mkdir()
        acquisition._save_index(acquisition_cache, {"a": []})  # pylint: disable=protected-access
        library_path = self.tmp / "library.json"
        library._write_private(library_path, '{\n  "a": 1\n}\n')  # pylint: disable=protected-access
        text_path = effects.write_text(self.tmp / "text.md", "a\nb\n", encoding="utf-8")
        project = self.tmp / "project"
        project.mkdir()
        seeded = rules.seed_project_rules(project, "reels")
        self.assertIsNotNone(seeded)
        for path in (
            queue_path,
            acquisition_cache / acquisition.INDEX_NAME,
            library_path,
            text_path,
            seeded or project,
        ):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())
                self.assertNotIn(b"\r", path.read_bytes())


class GitattributesTests(unittest.TestCase):
    def test_the_templates_init_copies_keep_lf_on_checkout(self):
        lines = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        for template in ("docs/RULES.md", "docs/BRIEF.md"):
            with self.subTest(template=template):
                self.assertIn(f"{template} text eol=lf", lines)


if __name__ == "__main__":
    unittest.main()
