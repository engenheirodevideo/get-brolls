"""Ramos de erro da leitura de texto público: falha de leitura dentro e fora do prazo, e limite inválido.

Sem rede: a resposta e o opener são dublês; o prazo vencido é um `deadline_at` no passado.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import web_text
from getbrolls.errors import ProviderError

URL = "https://cdn.videos.example/pagina.html"


class _Clock:
    """Um `_Deadline` falso: só responde se o prazo venceu."""

    def __init__(self, expired):
        self._expired = expired

    def expired(self):
        return self._expired


class _BrokenResponse:
    """Uma resposta cujo `read` levanta `error`."""

    def __init__(self, error):
        self.headers = {}
        self.error = error

    def read(self, _size):
        raise self.error


class ReadBodyTests(unittest.TestCase):
    def read(self, error, expired):
        return web_text._read_body(_BrokenResponse(error), 1024, _Clock(expired))  # pylint: disable=protected-access

    def test_a_read_cut_by_the_deadline_is_the_deadline_refusal(self):
        for error in (ConnectionResetError(), ValueError("closed")):
            with self.subTest(error=type(error).__name__), self.assertRaises(ProviderError) as caught:
                self.read(error, expired=True)
            self.assertIn("passou do prazo", str(caught.exception))

    def test_a_read_error_before_the_deadline_goes_up_unchanged(self):
        error = ConnectionResetError("reset")
        with self.assertRaises(ConnectionResetError) as caught:
            self.read(error, expired=False)
        self.assertIs(error, caught.exception)


class TextOnceTests(unittest.TestCase):
    def text_once(self, error, deadline_at):
        opener = MagicMock()
        opener.open.side_effect = error
        with patch.object(web_text.urllib.request, "build_opener", return_value=opener):
            return web_text._text_once(URL, 1024, deadline_at)  # pylint: disable=protected-access

    def test_a_value_error_after_the_deadline_is_the_deadline_refusal(self):
        with self.assertRaises(ProviderError) as caught:
            self.text_once(ValueError("closed"), time.monotonic() - 1)
        self.assertIn("passou do prazo", str(caught.exception))

    def test_a_value_error_before_the_deadline_goes_up_unchanged(self):
        error = ValueError("bad url")
        with self.assertRaises(ValueError) as caught:
            self.text_once(error, time.monotonic() + 60)
        self.assertIs(error, caught.exception)

    def test_a_connection_error_after_the_deadline_is_the_deadline_refusal(self):
        with self.assertRaises(ProviderError) as caught:
            self.text_once(ConnectionResetError(), time.monotonic() - 1)
        self.assertIn("passou do prazo", str(caught.exception))

    def test_a_connection_error_before_the_deadline_names_only_its_type(self):
        with self.assertRaises(ProviderError) as caught:
            self.text_once(ConnectionResetError("reset by 10.0.0.1"), time.monotonic() + 60)
        self.assertIn("ConnectionResetError", str(caught.exception))
        self.assertNotIn("10.0.0.1", str(caught.exception))


class FollowTests(unittest.TestCase):
    def test_a_byte_limit_of_zero_or_less_is_refused_before_any_request(self):
        for limit in (0, -1):
            with (
                self.subTest(limit=limit),
                patch.object(web_text, "_text_once", side_effect=AssertionError("pediu")),
                self.assertRaises(ProviderError) as caught,
            ):
                web_text.get_text(URL, limit)
            self.assertIn("Limite de bytes", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
