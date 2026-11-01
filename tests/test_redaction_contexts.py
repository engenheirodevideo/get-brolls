"""Nenhuma pasta da máquina sobra num texto redigido, em nenhum contexto nem codificação.

`marketplace.portable_text`, `labels.text` e `runtime.scrub_home` recebem cada grafia de uma pasta
(como escrita, resolvida, sem o `/private` do macOS, com barra no fim) em cada contexto em que um
caminho aparece numa mensagem ou numa linha de log (depois de `:`, entre aspas, entre parênteses,
antes de vírgula, espaço, quebra de linha, `\\`, no fim, como `file://`, depois de `-`, `=` ou `~`) e em
cada codificação (JSON com `\\/`, com `\\\\` e `\\uXXXX`, URL com e sem `/` codificada e, onde o disco
ignora maiúsculas, em maiúsculas). Nenhuma
forma da pasta pode sobrar na saída.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import labels, runtime
from getbrolls.sdk import marketplace

CONTEXTS = (
    "{p}",
    "a {p}",
    "{p}:x",
    '"{p}"',
    "'{p}'",
    "({p})",
    "{p},",
    "{p} x",
    "{p}\nx",
    "{p}\\x",
    "{p}/",
    "{p}/x.json",
    "file://{p}/x",
    "-{p}/x",
    "--opção={p}",
    "~{p}",
)


def _lower_hex(text):
    return re.sub(r"%[0-9A-F]{2}", lambda found: found.group(0).lower(), text)


def encodings(path):
    """As formas em que `path` aparece num texto: cru e em NFD; JSON (`\\/`, `\\\\/`, `\\\\`, `\\uXXXX`,
    `\\u002f`); URL (com e sem `/` codificada, `%2f` minúsculo, codificada duas vezes, `file://`); e, onde o
    disco ignora maiúsculas, em maiúsculas."""
    forms = set()
    for spelling in (unicodedata.normalize("NFC", path), unicodedata.normalize("NFD", path)):
        quoted = urllib.parse.quote(spelling, safe="")
        forms |= {
            spelling,
            spelling.replace("/", "\\/"),
            spelling.replace("/", "\\\\/"),
            spelling.replace("/", "\\u002f"),
            json.dumps(spelling)[1:-1],
            json.dumps(spelling, ensure_ascii=False)[1:-1],
            urllib.parse.quote(spelling),
            quoted,
            _lower_hex(quoted),
            urllib.parse.quote(quoted, safe=""),
            "file://" + urllib.parse.quote(spelling, safe="/"),
            *((spelling.upper(),) if sys.platform in ("darwin", "win32") else ()),
        }
    return forms


@unittest.skipIf(os.name == "nt", "grafias POSIX; o Windows tem o teste de maiúsculas do scrub_home")
class RedactionContextTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-redact-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / unicodedata.normalize("NFC", "gb hóme")
        self.root.mkdir()
        real = os.path.realpath(self.root)
        self.spellings = {str(self.root), real, real.removeprefix("/private"), str(self.root) + "/"}

    def leaks(self, redact, env):
        found = []
        forms = {form.lower() for spelling in self.spellings for form in encodings(spelling.rstrip("/"))}
        with patch.dict(os.environ, env):
            for spelling in sorted(self.spellings):
                for encoded in sorted(encodings(spelling)):
                    for context in CONTEXTS:
                        text = context.format(p=encoded)
                        out = redact(text).lower()
                        if any(form in out for form in forms):
                            found.append(f"{context!r} with {encoded[-40:]!r} -> {out[-80:]!r}")
        return found

    def test_portable_text_leaves_no_form_of_gb_home(self):
        self.assertEqual([], self.leaks(marketplace.portable_text, {"GB_HOME": str(self.root) + "/"}))

    def test_labels_text_leaves_no_form_of_gb_home(self):
        self.assertEqual([], self.leaks(labels.text, {"GB_HOME": str(self.root)}))

    def test_on_a_case_sensitive_disk_both_cases_of_the_url_hex_are_matched(self):
        # Linux: no IGNORECASE, so `%2F` and `%2f` are two forms, and each has to be there on its own.
        quoted = urllib.parse.quote(str(self.root), safe="")
        with patch.object(labels._paths, "_CASELESS", False), patch.dict(os.environ, {"GB_HOME": str(self.root)}):
            for form in (quoted, _lower_hex(quoted)):
                with self.subTest(form=form[-20:]):
                    self.assertEqual("a $GB_HOME%2Fx", labels.text(f"a {form}%2Fx"))

    def test_scrub_home_leaves_no_form_of_the_home_folder(self):
        with patch.object(runtime.Path, "home", return_value=self.root):
            self.assertEqual([], self.leaks(runtime.scrub_home, {}))


class WindowsHomeTests(unittest.TestCase):
    """Uma pasta pessoal do Windows (simulada em qualquer sistema): sem diferença de maiúsculas, com `\\` e com
    `/`, em JSON, em URL e como `file:///C:/…`."""

    HOME = "C:\\Users\\Ana Lú"

    def forms(self):
        forward = self.HOME.replace("\\", "/")
        quoted = urllib.parse.quote(forward, safe="")
        return {
            self.HOME,
            self.HOME.lower(),
            forward,
            json.dumps(self.HOME)[1:-1],
            self.HOME.replace("\\", "\\u005c"),
            quoted,
            _lower_hex(quoted),
            urllib.parse.quote(forward),
        }

    def test_no_form_of_a_windows_home_survives(self):
        uri = "file:///" + urllib.parse.quote(self.HOME.replace("\\", "/"), safe="/:")
        with patch.object(runtime.Path, "home", return_value=Path(self.HOME)):
            for form in sorted(self.forms()):
                for context in ("{p}", "erro em {p}\\x.json", '"{p}"', "{p}/x", "{p}%2Fx"):
                    text = context.format(p=form)
                    with self.subTest(text=text):
                        out = runtime.scrub_home(text)
                        self.assertNotIn("ana", out.lower().replace("~", ""), out)
            self.assertEqual("abra ~/x", runtime.scrub_home(f"abra {uri}/x").replace("file:///", ""))


class TokenShapeTests(unittest.TestCase):
    """`sk-…` é sempre redigido, nome de palavras inclusive (`sk-learn-pipeline`): só cobertura."""

    def test_words_after_sk_go_too(self):
        for text, tail in (
            ("sk-learn-pipeline-tutorial-for-beginners", "tutorial-for-beginners"),
            ("a pasta sk-reels-acme-2026-video-final-v2", "video-final-v2"),
            ("ask-me-anything-session-recording-2026", "session-recording-2026"),
        ):
            with self.subTest(text=text):
                self.assertNotIn(tail, runtime.redact(text))

    def test_key_shaped_values_go(self):
        for key in (
            "sk-" + "Z9y8X7w6V5u4" * 4,
            "sk-proj-" + "Ab3dE5fG7hJ9kL1mN3pQ5rS7" * 2 + "_" + "tU9vW1xY3z" * 2,
            "sk-ant-api03-" + "Qw3rTy7uIo9pAs1dFg5hJk" * 3 + "-" + "Zx2cV4bN6m" * 2,
            "sk-" + "0123456789abcdef" * 2,
        ):
            with self.subTest(key=key[:16]):
                self.assertEqual("chave [REDACTED] fim", runtime.redact(f"chave {key} fim"))


if __name__ == "__main__":
    unittest.main()
