"""Proxy de saída conferida da rota aberta: toda conexão do yt-dlp e do FFmpeg passa pela regra de destino público.

O yt-dlp e o FFmpeg seguem redirecionamento e buscam segmento, chave e variante por conta própria.
Atrás do proxy, cada uma dessas conexões vira um `CONNECT host:443` que o proxy resolve e confere
antes de conectar: loopback, link-local (metadados de nuvem), rede privada, NAT64 e IPv6 que embute
IPv4 são recusados, sem conexão nenhuma ao destino. Tudo offline: só sockets em 127.0.0.1.
"""

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_open_web_video import MEDIA_PLAYLIST, STREAM, fake_get_text, fake_media_run

from getbrolls import effects, egress_proxy, http, social, web_text

PUBLIC_IP = "93.184.216.34"
PRIVATE_TARGETS = (
    ("127.0.0.1", "127.0.0.1"),
    ("[::1]", "::1"),
    ("169.254.169.254", "169.254.169.254"),
    ("10.0.0.5", "10.0.0.5"),
    ("[64:ff9b::7f00:1]", "64:ff9b::7f00:1"),
    ("[::ffff:7f00:1]", "::ffff:7f00:1"),
    ("[::7f00:1]", "::7f00:1"),
    ("metadados.example", "169.254.169.254"),
    ("interno.example", "127.0.0.1"),
    ("nat64.example", "64:ff9b::a00:1"),
)


def answer_for(ip, port=443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


def dial(proxy_url):
    """Um cliente ligado ao proxy pelo IP, sem DNS (o `getaddrinfo` dos testes é falso)."""
    # effect-exempt: test client of the local proxy under test, loopback only
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.settimeout(5)
        client.connect(("127.0.0.1", int(proxy_url.rsplit(":", 1)[1])))
    except OSError:
        client.close()
        raise
    return client


def exchange(proxy_url, request):
    """Manda `request` ao proxy e devolve a primeira linha da resposta."""
    with dial(proxy_url) as client:
        client.sendall(request)
        data = b""
        while b"\r\n" not in data:
            chunk = client.recv(1024)
            if not chunk:
                break
            data += chunk
    return data.split(b"\r\n", 1)[0].decode("latin-1")


class Listener:
    """Um servidor TCP em 127.0.0.1 que conta conexões e ecoa o que recebe (o "destino" dos testes)."""

    def __init__(self, test):
        # effect-exempt: loopback listener that stands in for the destination in the test
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
        test.addCleanup(self.close)

    def serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            self.accepted += 1
            with conn:
                conn.settimeout(2)
                try:
                    data = conn.recv(1024)
                    conn.sendall(b"eco:" + data)
                except OSError:
                    pass

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        self.sock.close()


class ConnectRefusalTests(unittest.TestCase):
    def setUp(self):
        self.proxy = self.enterContext(egress_proxy.GuardedProxy())
        self.connect = self.enterContext(
            patch.object(egress_proxy, "_connect", side_effect=AssertionError("conectou num destino recusado"))
        )

    def test_the_proxy_listens_only_on_loopback(self):
        self.assertRegex(self.proxy.url, r"^http://127\.0\.0\.1:\d+$")

    def test_private_loopback_metadata_and_nat64_destinations_are_refused_before_connecting(self):
        for host, ip in PRIVATE_TARGETS:
            with (
                self.subTest(host=host),
                patch.object(socket, "getaddrinfo", return_value=answer_for(ip)),
                self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
            ):
                line = exchange(self.proxy.url, f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
                self.assertIn("403", line)
            self.assertIn("reason=not_public", "\n".join(cm.output))
        self.connect.assert_not_called()

    def test_a_name_with_one_private_address_among_public_ones_is_refused(self):
        mixed = answer_for(PUBLIC_IP) + answer_for("10.0.0.9")
        with patch.object(socket, "getaddrinfo", return_value=mixed):
            self.assertIn("403", exchange(self.proxy.url, b"CONNECT cdn.example:443 HTTP/1.1\r\n\r\n"))
        self.connect.assert_not_called()

    def test_plain_http_other_ports_and_odd_targets_are_refused_without_dns(self):
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("sem DNS")):
            for request in (
                b"GET http://cdn.example/seg.ts HTTP/1.1\r\nHost: cdn.example\r\n\r\n",
                b"CONNECT cdn.example:80 HTTP/1.1\r\n\r\n",
                b"CONNECT cdn.example:8443 HTTP/1.1\r\n\r\n",
                b"CONNECT user@cdn.example:443 HTTP/1.1\r\n\r\n",
                b"CONNECT cdn.example\x0b@127.0.0.1:443 HTTP/1.1\r\n\r\n",
                b"CONNECT 0177.0.0.1:443 HTTP/1.1\r\n\r\n",
                b"CONNECT 2130706433:443 HTTP/1.1\r\n\r\n",
            ):
                with self.subTest(request=request):
                    line = exchange(self.proxy.url, request)
                    self.assertRegex(line, r" (400|403) ")
        self.connect.assert_not_called()

    def test_the_log_names_only_the_host(self):
        with (
            patch.object(socket, "getaddrinfo", return_value=answer_for("127.0.0.1")),
            self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
        ):
            exchange(self.proxy.url, b"CONNECT interno.example:443 HTTP/1.1\r\n\r\n")
        joined = "\n".join(cm.output)
        self.assertIn("host=interno.example", joined)
        self.assertNotIn("127.0.0.1", joined)


class TunnelTests(unittest.TestCase):
    """Destino público: o túnel liga ao IP conferido (sem novo DNS) e passa os bytes nos dois sentidos."""

    def test_a_public_destination_is_tunnelled_to_the_checked_address(self):
        listener = Listener(self)
        lookups = []

        def resolve(host, port, *args, **kwargs):
            lookups.append(host)
            return answer_for("127.0.0.1", listener.port)

        # Só aqui o loopback conta como "público": o destino de verdade do teste é o listener local.
        real_global = http.public_address
        with (
            egress_proxy.GuardedProxy() as proxy,
            patch.object(socket, "getaddrinfo", side_effect=resolve),
            patch.object(http, "public_address", side_effect=lambda ip: ip == "127.0.0.1" or real_global(ip)),
            dial(proxy.url) as client,
        ):
            client.sendall(b"CONNECT cdn.example:443 HTTP/1.1\r\n\r\nola")
            data = b""
            while not data.endswith(b"eco:ola"):
                chunk = client.recv(1024)
                if not chunk:
                    break
                data += chunk
        self.assertTrue(data.startswith(b"HTTP/1.1 200"))
        self.assertTrue(data.endswith(b"eco:ola"))
        self.assertEqual(["cdn.example"], lookups)
        self.assertEqual(1, listener.accepted)

    def test_a_redirect_to_a_private_host_is_its_own_connect_and_is_refused(self):
        # Um redirecionamento (ou um segmento em outro host) é outra conexão: outro CONNECT, outra conferência.
        answers = {"cdn.example": answer_for(PUBLIC_IP), "interno.example": answer_for("169.254.169.254")}
        opened = []
        with (
            egress_proxy.GuardedProxy() as proxy,
            patch.object(socket, "getaddrinfo", side_effect=lambda host, *a, **k: answers[host]),
            patch.object(
                egress_proxy, "_connect", side_effect=lambda _host, rows: opened.append(rows) or _closed_pair()
            ),
        ):
            self.assertIn("200", exchange(proxy.url, b"CONNECT cdn.example:443 HTTP/1.1\r\n\r\n"))
            self.assertIn("403", exchange(proxy.url, b"CONNECT interno.example:443 HTTP/1.1\r\n\r\n"))
        self.assertEqual([answers["cdn.example"]], opened)


def _closed_pair():
    """Um socket que já fechou do outro lado: o túnel abre e termina logo."""
    left, right = socket.socketpair()
    right.close()
    return left


class EnvironmentTests(unittest.TestCase):
    def test_every_proxy_variable_points_to_the_proxy_and_no_proxy_is_dropped(self):
        base = {"PATH": "/bin", "no_proxy": "*", "NO_PROXY": "*", "HTTPS_PROXY": "http://outro:3128"}
        env = egress_proxy.environment(base, "http://127.0.0.1:5")
        for key in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            self.assertEqual("http://127.0.0.1:5", env[key])
        self.assertNotIn("no_proxy", env)
        self.assertNotIn("NO_PROXY", env)
        self.assertEqual("/bin", env["PATH"])


class YtDlpRunsBehindTheProxyTests(unittest.TestCase):
    """O argv e o ambiente que chegam ao yt-dlp: `--proxy` do proxy no ar, e nada disso para o YouTube."""

    def capture(self, url, pages, call=None):
        seen = []

        def fake_run(argv, **kwargs):
            proxy = argv[argv.index("--proxy") + 1] if "--proxy" in argv else None
            alive = None
            if proxy:
                alive = "403" in exchange(proxy, b"GET http://x.example/ HTTP/1.1\r\n\r\n")
            seen.append({"argv": argv, "env": kwargs.get("env"), "alive": alive})
            if "-o" in argv:
                Path(argv[argv.index("-o") + 1]).write_bytes(b"x")
            return subprocess.CompletedProcess(argv, 0, "{}", "")

        get_text, _ = fake_get_text(pages)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(socket, "getaddrinfo", return_value=answer_for(PUBLIC_IP)),
            patch.object(web_text, "get_text", side_effect=get_text),
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch.object(effects, "run", side_effect=fake_run),
            patch("getbrolls.media.run", side_effect=fake_media_run),
        ):
            if call is None:
                social.download_segment(url, Path(tmp) / "clip.mp4", 0, 2)
            else:
                call(url)
        return seen

    def test_metadata_and_probe_of_an_open_web_link_run_behind_the_proxy_too(self):
        for call in (social.metadata, social.probe_remote):
            with self.subTest(call=call.__name__):
                seen = self.capture(STREAM, {STREAM: MEDIA_PLAYLIST}, call)
                self.assertTrue(seen)
                for run in seen:
                    self.assertIn("--proxy", run["argv"])
                    self.assertTrue(run["alive"])

    def test_an_open_web_link_runs_behind_a_live_proxy(self):
        seen = self.capture(STREAM, {STREAM: MEDIA_PLAYLIST})
        self.assertEqual(1, len(seen))
        argv, env = seen[0]["argv"], seen[0]["env"]
        proxy = argv[argv.index("--proxy") + 1]
        self.assertRegex(proxy, r"^http://127\.0\.0\.1:\d+$")
        self.assertLess(argv.index("--proxy"), argv.index("--"))
        self.assertTrue(seen[0]["alive"], "o proxy estava no ar durante o yt-dlp")
        self.assertEqual(proxy, env["https_proxy"])
        self.assertEqual(proxy, env["http_proxy"])
        self.assertNotIn("no_proxy", env)
        # Depois do comando, o proxy não escuta mais.
        with self.assertRaises(OSError):
            dial(proxy).close()

    def test_a_youtube_link_runs_as_before(self):
        seen = self.capture("https://www.youtube.com/watch?v=aaaaaaaaaaa", {})
        self.assertNotIn("--proxy", seen[0]["argv"])
        self.assertIsNone(seen[0]["env"])


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
class RealToolsHonourTheProxyTests(unittest.TestCase):
    """O FFmpeg e o yt-dlp de verdade passam pelo proxy: o destino local nunca recebe a conexão."""

    def run_tool(self, argv, env):
        # effect-exempt: runs the real FFmpeg/yt-dlp against a loopback-only setup to prove the proxy is used
        return subprocess.run(argv, env=env, capture_output=True, timeout=60, check=False)

    def setUp(self):
        self.listener = Listener(self)
        self.target = f"https://127.0.0.1:{self.listener.port}/v/main.m3u8"

    def test_ffmpeg_sends_https_through_the_proxy_and_is_refused(self):
        with (
            egress_proxy.GuardedProxy() as proxy,
            self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
        ):
            env = egress_proxy.environment(os.environ, proxy.url)
            result = self.run_tool(["ffmpeg", "-v", "error", "-i", self.target, "-f", "null", "-"], env)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(0, self.listener.accepted, "o FFmpeg conectou direto, fora do proxy")
        self.assertIn("reason=port_not_allowed", "\n".join(cm.output))

    @unittest.skipUnless(shutil.which("yt-dlp"), "yt-dlp required")
    def test_yt_dlp_sends_https_through_the_proxy_and_is_refused(self):
        with (
            egress_proxy.GuardedProxy() as proxy,
            self.assertLogs("getbrolls.egress_proxy", level="WARNING") as cm,
        ):
            env = egress_proxy.environment(os.environ, proxy.url)
            argv = [str(shutil.which("yt-dlp")), "--ignore-config", "--proxy", proxy.url, "--retries", "0"]
            result = self.run_tool([*argv, "--socket-timeout", "5", "--skip-download", "--", self.target], env)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(0, self.listener.accepted, "o yt-dlp conectou direto, fora do proxy")
        self.assertIn("reason=port_not_allowed", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
