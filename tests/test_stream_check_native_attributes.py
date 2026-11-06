"""Os atributos de uma tag HLS lidos como o downloader nativo do yt-dlp os lê, conferidos contra uma cópia dele.

`parse_m3u8_attributes` é a função `yt_dlp.utils.parse_m3u8_attributes` do yt-dlp 2026.08.19,
copiada sem mudar o sentido (`yt_dlp/utils/_utils.py`). O `stream_check` só aceita uma tag quando os endereços que
ele confere são exatamente os que essa função acharia; um corpus hostil e um sorteio com semente fixa
procuram texto em que os dois leitores discordam. Sem rede: só texto.
"""

import random
import re
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import stream_check

YT_DLP_VERSION = "2026.08.19"
VENDORED_PATTERN = r'(?P<key>[A-Z0-9-]+)=(?P<val>"[^"]+"|[^",]+)(?:,|$)'


# Cópia literal de yt-dlp 2026.08.19, yt_dlp/utils/_utils.py, com o mesmo nome; o formatador só tirou os
# parênteses de `for (key, val) in`, sem mudar o sentido.
def parse_m3u8_attributes(attrib):
    info = {}
    for key, val in re.findall(r'(?P<key>[A-Z0-9-]+)=(?P<val>"[^"]+"|[^",]+)(?:,|$)', attrib):
        if val.startswith('"'):
            val = val[1:-1]  # noqa: PLW2901  (cópia literal)
        info[key] = val
    return info


def uri_values(info):
    return {key: value for key, value in info.items() if key == "URI" or key.endswith("-URI")}


def yt_dlp_line_uris(line):
    """Os endereços que o yt-dlp 2026.08.19 leria na linha inteira, com o corte de linha dele.

    O downloader nativo (`yt_dlp/downloader/hls.py`) acha `#EXT-X-MAP` e `#EXT-X-KEY` só pelo começo da
    linha, sem exigir o `:`, e lê os atributos de `line[11:]`; o extrator (`yt_dlp/extractor/common.py`) lê
    `#EXT-X-MEDIA:` passando a linha inteira.
    """
    if line.startswith(("#EXT-X-MAP", "#EXT-X-KEY")):
        return {value for key, value in parse_m3u8_attributes(line[11:]).items() if key == "URI"}
    if line.startswith("#EXT-X-MEDIA:"):
        return {value for key, value in parse_m3u8_attributes(line).items() if key == "URI"}
    return set()


def checked_uris(line):
    """Os endereços que o `stream_check` confere na tag, ou `None` quando ele a recusa."""
    try:
        return stream_check._tag_uris(line, line.upper())  # pylint: disable=protected-access
    except (stream_check._MalformedPlaylistError, stream_check._CdmDrmError):  # pylint: disable=protected-access
        return None


def entries_of(line):
    """Os endereços que `_entries` confere numa playlist com a linha, ou `None` quando ela é recusada."""
    text = "#EXTM3U\n" + line + "\n#EXTINF:4,\nseg.ts\n"
    try:
        return [uri for uri, _ in stream_check._entries(text)]  # pylint: disable=protected-access
    except (stream_check._MalformedPlaylistError, stream_check._CdmDrmError):  # pylint: disable=protected-access
        return None


ADVERSARIAL = (
    'METHOD=AES-128,URI="https://a.example/k"',
    'URI="https://a.example/k",IV=0x1',
    'URI="https://a.example/k"x,IV=0x1',
    'URI="https://a.example/k" ,IV=0x1',
    'URI="https://a.example/k""https://b.example/k"',
    'X="URI=https://b.example/k",URI="https://a.example/k"',
    'X="a,URI=https://b.example/k"',
    'X="a",URI=https://b.example/k',
    "URI=https://a.example/k,URI=https://b.example/k",
    "X_URI=https://b.example/k",
    "_URI=https://b.example/k",
    "XURI=https://b.example/k",
    "uri=https://b.example/k",
    'URI=""',
    "URI=,IV=1",
    'URI=a"b',
    'URI="https://a.example/k',
    'URI="https://a.example/\\"k"',
    "URI= https://a.example/k",
    'KEY-URI="https://a.example/k",OTHER-URI="https://b.example/k"',
    'X="y"URI="https://b.example/k"',
    'METHOD=AES-128,URI="https://a.example/k",KEYFORMAT="identity"',
    "METHOD=NONE",
    "",
    ",,,",
    "=",
    'A="="',
    'A=",URI=x"',
)
# O começo da linha: as tags que o yt-dlp lê pelo prefixo, seguidas de `:` ou de outro caractere qualquer.
LINE_HEADS = tuple(
    tag + separator
    for tag in ("#EXT-X-KEY", "#EXT-X-MAP", "#EXT-X-MEDIA", "#EXT-X-SESSION-KEY", "#EXTINF", "#EXT-X-FOO")
    for separator in (":", "X", "", " ", "::", ";", "S:", "-URI=")
)
LINE_BODIES = (
    "METHOD=AES-128,URI=https://10.0.0.1/k",
    'METHOD=AES-128,URI="https://10.0.0.1/k"',
    "URI=https://127.0.0.1/init.mp4",
    'TYPE=AUDIO,URI="https://10.0.0.1/a.m3u8"',
    "4,URI=https://10.0.0.1/k",
    "XURI=https://10.0.0.1/k",
)
ALPHABET = ("URI=", '"', ",", "=", "-", "_", "X", "a", " ", "https://a.example/k", "-URI=")


class NativeAttributeDifferentialTests(unittest.TestCase):
    def test_the_module_regex_is_the_yt_dlp_one_verbatim(self):
        self.assertEqual(VENDORED_PATTERN, stream_check._NATIVE_ATTRIBUTE.pattern)  # pylint: disable=protected-access
        self.assertEqual("2026.08.19", YT_DLP_VERSION)

    def assert_agrees(self, attributes):
        line = "#EXT-X-KEY:" + attributes
        native = parse_m3u8_attributes(attributes)
        self.assertEqual(native, stream_check._native_attributes(attributes))  # pylint: disable=protected-access
        uris = checked_uris(line)
        if uris is not None:
            self.assertEqual(sorted(uri_values(native).values()), sorted(uris), line)

    def test_an_adversarial_corpus_never_makes_the_readers_disagree(self):
        for attributes in ADVERSARIAL:
            with self.subTest(attributes=attributes):
                self.assert_agrees(attributes)

    def test_a_seeded_random_corpus_never_makes_the_readers_disagree(self):
        draw = random.Random(20261101)
        for _ in range(4000):
            attributes = "".join(draw.choice(ALPHABET) for _ in range(draw.randint(1, 10)))
            with self.subTest(attributes=attributes):
                self.assert_agrees(attributes)

    def test_whole_lines_through_the_yt_dlp_slicing_never_disagree(self):
        # A linha inteira, com o corte do yt-dlp (`line[11:]` em `#EXT-X-KEY`/`#EXT-X-MAP` sem exigir `:`).
        for head in LINE_HEADS:
            for body in LINE_BODIES:
                line = head + body
                with self.subTest(line=line):
                    native = yt_dlp_line_uris(line)
                    checked = entries_of(line)
                    if checked is not None:
                        self.assertLessEqual(native, set(checked), line)

    def test_a_key_or_map_tag_without_its_colon_is_refused(self):
        for line in (
            "#EXT-X-KEYXMETHOD=AES-128,URI=https://10.0.0.1/k",
            "#EXT-X-MAPXURI=https://127.0.0.1/init.mp4",
            '#EXT-X-KEY METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://x"',
            "#EXT-X-MAP",
        ):
            with self.subTest(line=line):
                self.assertIsNone(entries_of(line))

    def test_a_raw_uri_that_no_attribute_accounts_for_is_refused(self):
        for line in ("#EXTINF:4,URI=https://10.0.0.1/k", "#EXT-X-FOO URI=https://10.0.0.1/k"):
            with self.subTest(line=line):
                self.assertIsNone(entries_of(line))

    def test_text_after_a_closing_quote_without_a_comma_is_refused(self):
        for attributes in (
            'URI="https://a.example/k"x',
            'URI="https://a.example/k" ,IV=1',
            'X="y"URI="https://b.example/k"',
        ):
            with self.subTest(attributes=attributes):
                self.assertIsNone(checked_uris("#EXT-X-KEY:" + attributes))

    def test_uri_inside_a_quoted_value_is_refused(self):
        for attributes in ('X="URI=https://b.example/k",URI="https://a.example/k"', 'X="a, key-uri = b"'):
            with self.subTest(attributes=attributes):
                self.assertIsNone(checked_uris("#EXT-X-KEY:" + attributes))

    def test_the_last_repeated_name_wins_like_yt_dlp(self):
        # Dois `KEY-URI`: o yt-dlp fica com o último; a conferência recusa o nome de endereço repetido.
        attributes = 'KEY-URI="https://a.example/1",KEY-URI="https://a.example/2"'
        self.assertEqual({"KEY-URI": "https://a.example/2"}, parse_m3u8_attributes(attributes))
        self.assertIsNone(checked_uris("#EXT-X-KEY:" + attributes))

    def test_an_ordinary_key_tag_still_passes(self):
        self.assertEqual(
            ["https://a.example/k"], checked_uris('#EXT-X-KEY:METHOD=AES-128,URI="https://a.example/k",IV=0x1')
        )


def yt_dlp_segment_lines(raw):
    """As linhas de segmento que o downloader nativo do yt-dlp 2026.08.19 leria nos bytes da playlist.

    `yt_dlp/downloader/hls.py`: `s_bytes.decode('utf-8', 'ignore')`, `s.splitlines()` e `line.strip()`.
    """
    lines = (line.strip() for line in raw.decode("utf-8", "ignore").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def checked_segment_lines(raw):
    """As linhas soltas que o `stream_check` confere nos mesmos bytes (lidos como o `web_text`), ou `None`."""
    text = raw.decode("utf-8", errors="replace")
    try:
        return [uri for uri, _ in stream_check._entries(text)]  # pylint: disable=protected-access
    except (stream_check._MalformedPlaylistError, stream_check._CdmDrmError):  # pylint: disable=protected-access
        return None


# Quebras que o `str.splitlines()` do yt-dlp reconhece; o `stream_check` só quebra em `\r\n`, `\r` e `\n` e
# recusa as outras, então as duas leituras nunca conferem linhas diferentes.
SPLITLINES_BREAKS = ("\r\n", "\r", "\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


class NativeLineReadingTests(unittest.TestCase):
    def test_every_line_break_reads_the_same_lines_or_is_refused(self):
        for first in SPLITLINES_BREAKS:
            for second in SPLITLINES_BREAKS:
                text = f"#EXTM3U{first}#EXTINF:4,{second}https://a.example/seg-1.ts{first}https://b.example/seg-2.ts"
                with self.subTest(first=repr(first), second=repr(second)):
                    raw = text.encode("utf-8")
                    checked = checked_segment_lines(raw)
                    if checked is not None:
                        self.assertEqual(yt_dlp_segment_lines(raw), checked)
        self.assertEqual(
            ["https://a.example/seg-1.ts", "https://b.example/seg-2.ts"],
            checked_segment_lines(b"#EXTM3U\r\n#EXTINF:4,\rhttps://a.example/seg-1.ts\nhttps://b.example/seg-2.ts"),
        )

    def test_invalid_utf8_is_refused_because_the_reference_reader_drops_it(self):
        # O yt-dlp descarta o byte inválido (`'ignore'`) e leria aqui uma chave; a conferência veria outra linha.
        for raw in (
            b"#EXTM3U\n#\xffEXT-X-KEY:METHOD=AES-128,URI=https://10.0.0.1/k\n#EXTINF:4,\nseg.ts\n",
            b"#EXTM3U\n#EXTINF:4,\nhttps://10.0.\xff0.1/seg.ts\n",
            b"#EXTM3U\n#EXT-X-MAP:UR\xffI=https://127.0.0.1/init.mp4\n#EXTINF:4,\nseg.ts\n",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(checked_segment_lines(raw))
        self.assertEqual(
            {"https://10.0.0.1/k"},
            yt_dlp_line_uris(b"#\xffEXT-X-KEY:METHOD=AES-128,URI=https://10.0.0.1/k".decode("utf-8", "ignore")),
        )


if __name__ == "__main__":
    unittest.main()
