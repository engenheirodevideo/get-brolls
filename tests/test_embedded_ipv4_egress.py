"""Uma regra só de destino público para toda saída: o transporte interno (`http`) e o pin do curl do Instagram.

Cada forma de IPv6 que embute ou traduz um IPv4 (mapeado `::ffff:a.b.c.d`, compatível `::a.b.c.d`, NAT64
`64:ff9b::/96` e `64:ff9b:1::/48`, SIIT `::ffff:0:a.b.c.d`, 6to4 `2002::/16`, Teredo `2001::/32`) e o bloco
SRv6 `5f00::/16` são recusados, inclusive quando o IPv4 dentro é público: uma pilha que traduz o prefixo
alcança o IPv4, e a resposta do `ipaddress` para eles muda entre versões do Python. O relay anycast do 6to4
em IPv4 (`192.88.99.0/24`) também. E o seletor de formato do link aberto aceita só `https` e HLS
(`m3u8_native`, `m3u8`), com o protocolo inteiro: nunca `http` em claro, segmento DASH nem `https\n`.
Tudo offline: DNS sintético e, quando instalado, o yt-dlp lendo um info.json local com `--simulate`.
"""

import contextlib
import io
import json
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import http as gb_http
from getbrolls import instagram_urls, social
from getbrolls.http import ProviderError
from getbrolls.instagram_common import CollectError

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"

# (forma, endereço): o IPv4 de dentro é loopback, privado, link-local (metadados de nuvem) ou público.
EMBEDDED_FORMS = (
    ("mapped_loopback", "::ffff:127.0.0.1"),
    ("mapped_private", "::ffff:10.0.0.1"),
    ("mapped_link_local", "::ffff:169.254.169.254"),
    ("mapped_public", "::ffff:" + PUBLIC_V4),
    ("compatible_loopback", "::127.0.0.1"),
    ("compatible_public", "::" + PUBLIC_V4),
    ("nat64_loopback", "64:ff9b::7f00:1"),
    ("nat64_link_local", "64:ff9b::a9fe:a9fe"),
    ("nat64_public", "64:ff9b::5db8:d822"),
    ("nat64_local_use", "64:ff9b:1::a00:1"),
    ("siit_loopback", "::ffff:0:7f00:1"),
    ("siit_private", "::ffff:0:a00:1"),
    ("sixtofour_loopback", "2002:7f00:1::1"),
    ("sixtofour_link_local", "2002:a9fe:a9fe::1"),
    ("sixtofour_public", "2002:5db8:d822::1"),
    ("teredo", "2001:0:4136:e378:8000:63bf:3fff:fdd2"),
    ("srv6", "5f00::1"),
)


def answer_for(ip):
    """Uma linha de `getaddrinfo` para `ip` na porta 443."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, 443, 0, 0) if family == socket.AF_INET6 else (ip, 443)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


class SharedRuleTests(unittest.TestCase):
    """`http.public_address` é a regra única; nenhuma das formas conta como destino público."""

    def test_every_embedded_or_translated_form_is_not_public(self):
        for form, ip in EMBEDDED_FORMS:
            with self.subTest(form=form):
                self.assertFalse(gb_http.public_address(ip))

    def test_the_sixtofour_relay_anycast_is_not_public(self):
        # 192.88.99.0/24 (RFC 7526): o relay anycast do 6to4 é o lado IPv4 do mesmo túnel de `2002::/16`.
        for ip in ("192.88.99.1", "192.88.99.0", "192.88.99.255"):
            with self.subTest(ip=ip):
                self.assertFalse(gb_http.public_address(ip))
                self.assertIsNone(gb_http.public_url(f"https://{ip}/x.mp4"))
        self.assertTrue(gb_http.public_address("192.88.98.255"))
        self.assertTrue(gb_http.public_address("192.88.100.0"))

    def test_ordinary_public_addresses_still_are(self):
        for ip in (PUBLIC_V4, PUBLIC_V6):
            with self.subTest(ip=ip):
                self.assertTrue(gb_http.public_address(ip))


class TransportPathTests(unittest.TestCase):
    """Os caminhos do `http`: o IP literal na URL (`public_url`) e o endereço que o DNS devolve (`_safe_network`)."""

    def test_public_url_drops_a_literal_embedded_address(self):
        for form, ip in EMBEDDED_FORMS:
            with self.subTest(form=form):
                self.assertIsNone(gb_http.public_url(f"https://[{ip}]/x.mp4"))
        self.assertIsNotNone(gb_http.public_url(f"https://[{PUBLIC_V6}]/x.mp4"))

    def test_a_name_resolving_to_an_embedded_address_is_refused(self):
        for form, ip in EMBEDDED_FORMS:
            with (
                self.subTest(form=form),
                patch.object(socket, "getaddrinfo", return_value=answer_for(ip)),
                self.assertLogs("getbrolls.http", level="WARNING") as cm,
                self.assertRaises(ProviderError),
            ):
                gb_http.public_destination("https://cdn.videos.example/x.m3u8")
            self.assertIn("reason=private_address", "\n".join(cm.output))

    def test_a_name_resolving_to_a_public_address_passes(self):
        with patch.object(socket, "getaddrinfo", return_value=answer_for(PUBLIC_V6)):
            gb_http.public_destination("https://cdn.videos.example/x.m3u8")


class InstagramPathTests(unittest.TestCase):
    """O pin do curl do Instagram usa a mesma regra: IP literal na URL e endereço resolvido do host."""

    def refuse(self, url):
        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            self.assertLogs("getbrolls.instagram_pairs", level="WARNING") as cm,
            self.assertRaises(CollectError),
        ):
            instagram_urls.validate_media_url(url, Path("cfg.conf"))
        self.assertIn("reason=private_address", "\n".join(cm.output))

    def test_a_literal_embedded_address_is_refused(self):
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("sem DNS")):
            for form, ip in EMBEDDED_FORMS:
                with self.subTest(form=form):
                    self.refuse(f"https://[{ip}]/media")

    def test_a_host_resolving_to_an_embedded_address_is_refused(self):
        for form, ip in EMBEDDED_FORMS:
            # O endereço suspeito vem ao lado de um público: basta um para recusar o host inteiro.
            rows = answer_for(PUBLIC_V4) + answer_for(ip)
            with self.subTest(form=form), patch.object(socket, "getaddrinfo", return_value=rows):
                self.refuse("https://media.example.org/media")

    def test_public_addresses_still_get_pinned(self):
        rows = answer_for(PUBLIC_V4) + answer_for(PUBLIC_V6)
        with (
            patch.object(socket, "getaddrinfo", return_value=rows),
            patch.object(instagram_urls, "_curl_takes_address_list", return_value=True),
        ):
            url, resolve = instagram_urls.validate_media_url("https://media.example.org/media", Path("cfg.conf"))
        self.assertEqual("https://media.example.org/media", url)
        self.assertEqual(f"media.example.org:443:{PUBLIC_V4},[{PUBLIC_V6}]", resolve)
        self.assertIsNone(instagram_urls.validate_media_url(f"https://[{PUBLIC_V6}]/m", Path("cfg.conf"))[1])


# Os protocolos que o yt-dlp atribui a um formato (`determine_protocol` e os extratores).
ACCEPTED_PROTOCOLS = ("https", "m3u8_native", "m3u8")
REFUSED_PROTOCOLS = (
    "http",
    "http_dash_segments",
    "http_dash_segments_generator",
    "m3u8_native+https",
    "https+http",
    "ftp",
    "ftps",
    "rtmp",
    "rtmps",
    "f4m",
    "ism",
    "websocket_frag",
    "mhtml",
    "xhttps",
    "httpsx",
)


class ProtocolFilterTests(unittest.TestCase):
    """O filtro do link aberto casa o protocolo inteiro: só `https`, `m3u8_native` e `m3u8`."""

    def pattern(self):
        found = re.fullmatch(r"\[protocol~='(.*)'\]", social.HTTP_PROTOCOL_FILTER)
        self.assertIsNotNone(found, social.HTTP_PROTOCOL_FILTER)
        return re.compile(found.group(1) if found else "")

    def test_only_https_and_hls_protocols_match(self):
        # O `~=` do yt-dlp é `re.search`: sem as duas âncoras, `http` e `m3u8_native+https` passariam.
        pattern = self.pattern()
        for protocol in ACCEPTED_PROTOCOLS:
            with self.subTest(protocol=protocol):
                self.assertIsNotNone(pattern.search(protocol))
        for protocol in REFUSED_PROTOCOLS:
            with self.subTest(protocol=protocol):
                self.assertIsNone(pattern.search(protocol))

    def test_a_trailing_line_break_does_not_match(self):
        # `$` também casa antes de um `\n` final; a âncora do fim precisa ser exata (`\Z`).
        pattern = self.pattern()
        for protocol in ACCEPTED_PROTOCOLS:
            for tail in ("\n", "\r\n", "\n\n"):
                with self.subTest(protocol=protocol, tail=tail):
                    self.assertIsNone(pattern.search(protocol + tail))


YT_DLP = shutil.which("yt-dlp")


@unittest.skipUnless(YT_DLP, "yt-dlp required")
class ProtocolFilterWithYtDlpTests(unittest.TestCase):
    """O seletor real do link aberto (`OPEN_WEB_FORMAT`) no yt-dlp, lendo um info.json local com `--simulate`."""

    def selected(self, formats):
        info = {
            "id": "x",
            "title": "x",
            "extractor": "generic",
            "extractor_key": "Generic",
            "webpage_url": "https://cdn.videos.example/v/x.mp4",
            "formats": [{"ext": "mp4", "vcodec": "h264", "acodec": "aac", "height": 720, **fmt} for fmt in formats],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.info.json"
            path.write_text(json.dumps(info), encoding="utf-8")
            argv = [str(YT_DLP), "--ignore-config", "--simulate", "--print", "format_id"]
            argv += ["-f", social.OPEN_WEB_FORMAT, "--load-info-json", str(path)]
            done = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
        return done.stdout.strip() if done.returncode == 0 else None

    def test_a_plain_http_format_is_filtered_out(self):
        http_only = {"format_id": "plain", "url": "http://cdn.videos.example/v/x.mp4", "protocol": "http"}
        self.assertIsNone(self.selected([http_only]))
        https = {"format_id": "tls", "url": "https://cdn.videos.example/v/x.mp4", "protocol": "https"}
        self.assertEqual("tls", self.selected([https, {**http_only, "height": 1080}]))

    def test_a_protocol_with_a_trailing_line_break_is_filtered_out(self):
        newline = {"format_id": "nl", "url": "https://cdn.videos.example/v/x.mp4", "protocol": "https\n"}
        self.assertIsNone(self.selected([newline]))

    def test_dash_segments_are_filtered_out(self):
        dash = {"format_id": "dash", "url": "https://cdn.videos.example/v/x.mpd", "protocol": "http_dash_segments"}
        hls = {"format_id": "hls", "url": "https://cdn.videos.example/v/x.m3u8", "protocol": "m3u8_native"}
        self.assertIsNone(self.selected([dash]))
        self.assertEqual("hls", self.selected([hls, {**dash, "height": 1080}]))


if __name__ == "__main__":
    unittest.main()
