"""Offline regressions for stale reviews and the HTTPS connection boundary."""

import io
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls import http
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, set_segment
from getbrolls.rendering import render
from getbrolls.review import import_review


def _review_payload(page):
    match = re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
    assert match, "review.html sem o payload embutido"
    return match.group(1)


def exported_review(ledger):
    page = Path(render(ledger)).read_text(encoding="utf-8")
    payload = json.loads(_review_payload(page))
    for item in payload["items"]:
        item["state"] = "approved"
    return payload


class ReviewRegressionTests(unittest.TestCase):
    def test_old_export_cannot_restore_a_rejected_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            c = candidate("local", "fixture", "Synthetic")
            set_segment(c, 0, 1)
            ledger.add(c)
            payload = exported_review(ledger)
            c["approval"]["status"] = "rejected"
            c["state"] = "rejected"
            ledger.save("reject", c)
            before = ledger.path.read_bytes()
            path = Path(folder) / "old.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mudou depois que você decidiu"):
                import_review(ledger, path, "Human")
            self.assertEqual(ledger.path.read_bytes(), before)
            self.assertEqual(ledger.get(c["id"])["approval"]["status"], "rejected")

    def test_missing_epoch_is_rejected_without_saving_any_items(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            for ident in ("one", "two"):
                c = candidate("local", ident, "Synthetic")
                set_segment(c, 0, 1)
                ledger.add(c)
            payload = exported_review(ledger)
            del payload["items"][1]["reviewEpoch"]
            path = Path(folder) / "old.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = ledger.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "mudou depois que você decidiu"):
                import_review(ledger, path, "Human")
            self.assertEqual(ledger.path.read_bytes(), before)
            self.assertTrue(all(c["approval"]["status"] == "pending" for c in ledger.data["items"]))

    def test_current_export_works_but_cannot_be_replayed_after_import(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            c = candidate("local", "fixture", "Synthetic")
            set_segment(c, 0, 1)
            ledger.add(c)
            path = Path(folder) / "review.json"
            path.write_text(json.dumps(exported_review(ledger)), encoding="utf-8")
            import_review(ledger, path, "First reviewer")
            self.assertEqual(ledger.get(c["id"])["approval"]["by"], "First reviewer")
            before = ledger.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "mudou depois que você decidiu"):
                import_review(ledger, path, "Second reviewer")
            self.assertEqual(ledger.path.read_bytes(), before)
            path.write_text(json.dumps(exported_review(ledger)), encoding="utf-8")
            import_review(ledger, path, "Second reviewer")
            self.assertEqual(ledger.get(c["id"])["approval"]["by"], "Second reviewer")


class WireSocket:
    """Socket boundary double: HTTP framing/urllib remain real; no real network."""

    def __init__(self, connections, requests, response):
        self.connections = connections
        self.requests = requests
        self.response = response

    def settimeout(self, value):
        pass

    def setsockopt(self, *args):
        pass

    def connect(self, address):
        self.connections.append(address)

    def sendall(self, data):
        self.requests.append(data)

    def makefile(self, *args):
        return io.BytesIO(self.response)

    def close(self):
        pass


class NetworkRegressionTests(unittest.TestCase):
    def exercise(self, operation, answers, response=None, certificate_error=False, environment=None):
        connections, requests, hostnames, lookups = [], [], [], []
        response = response or b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"

        def resolve(host, port, *args, **kwargs):
            lookups.append(host)
            ip = answers[min(len(lookups) - 1, len(answers) - 1)]
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            address = (ip, 443, 0, 0) if family == socket.AF_INET6 else (ip, 443)
            return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", address)]

        def wrap(context, sock, *, server_hostname=None, **kwargs):
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            hostnames.append(server_hostname)
            if certificate_error:
                raise ssl.SSLCertVerificationError("fixture certificate mismatch")
            return sock

        with (
            tempfile.TemporaryDirectory() as home,
            patch.dict(os.environ, environment or {}, clear=True),
            # Windows cannot discover a home after its environment is cleared.
            # Keep the network fixture isolated from the real user's cache.
            patch.object(Path, "home", return_value=Path(home)),
            patch.object(socket, "getaddrinfo", side_effect=resolve),
            patch.object(socket, "socket", side_effect=lambda *a, **k: WireSocket(connections, requests, response)),
            patch.object(ssl.SSLContext, "wrap_socket", wrap),
            patch.object(http.time, "sleep"),
        ):
            operation()
        return connections, b"".join(requests), hostnames, lookups

    def test_download_pins_public_dns_answer_and_keeps_tls_hostname(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "clip.part"
            connections, request, names, _ = self.exercise(
                lambda: http.download("https://media.example/video.mp4", target),
                ["93.184.216.34", "127.0.0.1"],
            )
            self.assertEqual(target.read_bytes(), b"{}")
            self.assertEqual(connections, [("93.184.216.34", 443)])
            self.assertIn(b"Host: media.example\r\n", request)
            self.assertEqual(names, ["media.example"])

    def test_json_pins_ipv6_without_using_environment_proxy(self):
        results = []
        connections, request, names, lookups = self.exercise(
            lambda: results.append(http.get_json("https://api.example/data")),
            ["2606:4700:4700::1111", "::1"],
            environment={"https_proxy": "http://proxy.example:8080"},
        )
        self.assertEqual(results, [{}])
        self.assertEqual(connections, [("2606:4700:4700::1111", 443, 0, 0)])
        self.assertEqual(lookups, ["api.example"])
        self.assertIn(b"Host: api.example\r\n", request)
        self.assertEqual(names, ["api.example"])

    def test_retry_revalidates_dns_before_a_second_connection(self):
        def request():
            with self.assertRaises(http.ProviderError):
                http.get_json("https://api.example/data")

        connections, _, _, _ = self.exercise(
            request,
            ["93.184.216.34", "127.0.0.1"],
            response=b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n",
        )
        self.assertEqual(connections, [("93.184.216.34", 443)])

    def test_certificate_failure_does_not_create_a_download(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "clip.part"

            def request():
                with self.assertRaises(http.ProviderError):
                    http.download("https://media.example/video.mp4", target)

            self.exercise(request, ["93.184.216.34"], certificate_error=True)
            self.assertFalse(target.exists())

    def test_json_rejects_non_https_and_nonstandard_ports_before_connecting(self):
        for url in ("http://api.example/data", "file:///tmp/data", "https://api.example:8443/data"):
            with (
                self.subTest(url=url),
                patch.object(socket, "socket", side_effect=AssertionError("Unexpected network")),
            ):
                with self.assertRaises(http.ProviderError):
                    http.get_json(url)

    def test_mixed_public_private_dns_is_rejected_before_connecting(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", 443, 0, 0)),
        ]
        with (
            patch.object(socket, "getaddrinfo", return_value=answers),
            patch.object(socket, "socket", side_effect=AssertionError("Unexpected network")),
        ):
            with self.assertRaisesRegex(http.ProviderError, "Destino de rede"):
                http.get_json("https://api.example/data")

    def test_redirects_are_not_followed(self):
        def request():
            with self.assertRaisesRegex(http.ProviderError, "Redirecionamento"):
                http.get_json("https://api.example/data")

        connections, _, _, _ = self.exercise(
            request,
            ["93.184.216.34"],
            response=b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1/private\r\nContent-Length: 0\r\n\r\n",
        )
        self.assertEqual(connections, [("93.184.216.34", 443)])


if __name__ == "__main__":
    unittest.main()
