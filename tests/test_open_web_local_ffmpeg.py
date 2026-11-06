"""Rota aberta: o FFmpeg nunca lê playlist ou endereço remoto.

O HLS do FFmpeg aceita segmento e chave `httpproxy://HOST:PORTA/...`, que abre TCP direto em
`HOST:PORTA`, fora do proxy de saída conferida. Na rota aberta, então, o yt-dlp roda sem FFmpeg
nenhum (`--ffmpeg-location` numa pasta vazia) e baixa o vídeo com o downloader nativo dele (cada
fragmento e a chave AES-128 passam pelo proxy; esquema desconhecido falha), e o corte é local, com
`-protocol_whitelist file` e uma lista de formatos de vídeo. Tudo offline: sockets só em 127.0.0.1.
"""

import base64
import functools
import http.server
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_egress_proxy import PUBLIC_IP, Listener, answer_for
from test_open_web_video import MEDIA_PLAYLIST, STREAM, fake_get_text, fake_media_run

from getbrolls import effects, media, social, web_text
from getbrolls.http import ProviderError

YT_DLP = shutil.which("yt-dlp")
FFMPEG = shutil.which("ffmpeg")


def _run_tool(argv, **kwargs):
    # effect-exempt: runs the real FFmpeg/yt-dlp against a loopback-only setup to prove what they open
    return subprocess.run(argv, capture_output=True, timeout=60, check=False, **kwargs)


def _hostile_playlist(port):
    """Playlist cujo segmento e cuja chave usam `httpproxy://`, que o FFmpeg abriria direto no `port`."""
    return (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
        f'#EXT-X-KEY:METHOD=AES-128,URI="httpproxy://127.0.0.1:{port}/169.254.169.254:80/k"\n'
        f"#EXTINF:2.0,\nhttpproxy://127.0.0.1:{port}/169.254.169.254:80/x.ts\n#EXT-X-ENDLIST\n"
    )


def _testsrc(path, seconds=3, extra=()):
    made = _run_tool(
        [
            *(FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"testsrc=d={seconds}:s=64x64:r=10"),
            *("-f", "lavfi", "-i", f"sine=d={seconds}", "-c:v", "libx264", "-c:a", "aac", "-shortest"),
            *extra,
            str(path),
        ]
    )
    assert made.returncode == 0, made.stderr[-300:]


class OpenWebArgvTests(unittest.TestCase):
    """O argv do yt-dlp e do corte no link aberto: sem FFmpeg no yt-dlp, download nativo, corte só local."""

    def capture(self, url, pages):
        seen, cuts = [], []

        def fake_run(argv, **_kwargs):
            folder = argv[argv.index("--ffmpeg-location") + 1] if "--ffmpeg-location" in argv else None
            empty = None if folder is None else Path(folder).is_dir() and not any(Path(folder).iterdir())
            seen.append({"argv": argv, "ffmpeg_folder_empty": empty})
            if "-o" in argv:
                Path(argv[argv.index("-o") + 1]).write_bytes(b"x")
            return subprocess.CompletedProcess(argv, 0, "{}", "")

        def media_run(args, *, op=None):
            cuts.append({"args": args, "op": op})
            return fake_media_run(args, op=op)

        get_text, _ = fake_get_text(pages)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(socket, "getaddrinfo", return_value=answer_for(PUBLIC_IP)),
            patch.object(web_text, "get_text", side_effect=get_text),
            patch.object(social, "command", return_value=["yt-dlp"]),
            patch.object(effects, "run", side_effect=fake_run),
            patch.object(media, "run", side_effect=media_run),
        ):
            social.download_segment(url, Path(tmp) / "clip.mp4", 0, 2)
            self.assertTrue((Path(tmp) / "clip.mp4").is_file())
        self.assertEqual(1, len(seen))
        return seen[0], cuts

    def test_yt_dlp_of_an_open_web_link_runs_without_ffmpeg_and_downloads_natively(self):
        run, _ = self.capture(STREAM, {STREAM: MEDIA_PLAYLIST})
        argv = run["argv"]
        self.assertTrue(run["ffmpeg_folder_empty"], "o yt-dlp precisa achar uma pasta vazia, sem FFmpeg")
        self.assertEqual("native", argv[argv.index("--downloader") + 1])
        self.assertEqual("never", argv[argv.index("--fixup") + 1])
        for flag in ("--download-sections", "--force-keyframes-at-cuts", "--remux-video", "--merge-output-format"):
            with self.subTest(flag=flag):
                self.assertNotIn(flag, argv)
        self.assertNotIn("+", argv[argv.index("-f") + 1], "juntar faixas exigiria o FFmpeg dentro do yt-dlp")
        self.assertLess(argv.index("--ffmpeg-location"), argv.index("--"))

    def test_the_cut_of_an_open_web_link_opens_only_local_video_files(self):
        _, cuts = self.capture(STREAM, {STREAM: MEDIA_PLAYLIST})
        self.assertTrue(cuts)
        for call in cuts:
            args = call["args"]
            with self.subTest(op=call["op"]):
                self.assertEqual("ffmpeg", args[0])
                self.assertEqual("file", args[args.index("-protocol_whitelist") + 1])
                formats = set(args[args.index("-format_whitelist") + 1].split(","))
                self.assertNotIn("hls", formats)
                self.assertNotIn("dash", formats)
                self.assertLess(args.index("-format_whitelist"), args.index("-i"))

    def test_a_youtube_link_keeps_its_sections_and_ffmpeg_as_before(self):
        run, _ = self.capture("https://www.youtube.com/watch?v=aaaaaaaaaaa", {})
        self.assertIn("--download-sections", run["argv"])
        self.assertNotIn("--ffmpeg-location", run["argv"])
        self.assertIsNone(run["ffmpeg_folder_empty"])


@unittest.skipUnless(YT_DLP and FFMPEG, "yt-dlp and FFmpeg required")
class HttpproxySchemeNeverConnectsTests(unittest.TestCase):
    """Com o yt-dlp de verdade: segmento e chave `httpproxy://` para o loopback não recebem conexão."""

    def download(self, playlist):
        data_uri = "data:application/vnd.apple.mpegurl;base64," + base64.b64encode(playlist.encode()).decode()
        info = {
            "id": "x",
            "title": "x",
            "extractor": "generic",
            "extractor_key": "Generic",
            "webpage_url": "https://cdn.videos.example/v/x.m3u8",
            "formats": [
                {
                    "format_id": "0",
                    "ext": "mp4",
                    "vcodec": "h264",
                    "acodec": "aac",
                    "url": data_uri,
                    "manifest_url": data_uri,
                    "protocol": "m3u8",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.info.json"
            path.write_text(json.dumps(info), encoding="utf-8")
            arguments = social.segment_arguments(0, 2, Path(tmp) / "out.mp4", open_web=True)
            base = [str(YT_DLP), "--ignore-config", "--socket-timeout", "5", "--retries", "0"]
            with (
                patch.object(social, "command", return_value=base),
                social._egress(True),
                self.assertRaises(ProviderError),
            ):
                social.run([*arguments, "--load-info-json", str(path)], timeout=60, op="segment")
            self.assertFalse((Path(tmp) / "out.mp4").exists())

    def test_an_httpproxy_segment_and_key_never_reach_the_loopback(self):
        listener = Listener(self)
        self.download(_hostile_playlist(listener.port))
        time.sleep(0.3)
        self.assertEqual(0, listener.accepted, "o downloader abriu conexão direta, fora do proxy")

    def test_https_and_http_segments_for_the_loopback_are_refused_by_the_proxy(self):
        listener = Listener(self)
        for scheme in ("https", "http", "tcp"):
            with self.subTest(scheme=scheme):
                playlist = _hostile_playlist(listener.port).replace("httpproxy://", scheme + "://")
                self.download(playlist)
        time.sleep(0.3)
        self.assertEqual(0, listener.accepted)


@unittest.skipUnless(FFMPEG, "FFmpeg required")
class LocalCutTests(unittest.TestCase):
    """O corte local: só arquivo local e formato de vídeo; uma playlist disfarçada não abre conexão."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with  # enterContext limpa
        self.folder = Path(self.enterContext(tmp))

    def test_a_playlist_in_place_of_the_video_opens_nothing(self):
        listener = Listener(self)
        for name in ("full.mp4", "full.m3u8", "full.ts"):
            with self.subTest(name=name):
                source = self.folder / name
                source.write_text(_hostile_playlist(listener.port), encoding="utf-8")
                with self.assertRaises(ValueError):
                    social.cut_local(source, self.folder / f"out-{name}.mp4", 0, 1)
                self.assertFalse((self.folder / f"out-{name}.mp4").exists())
        time.sleep(0.3)
        self.assertEqual(0, listener.accepted, "o FFmpeg do corte abriu conexão")

    def test_a_downloaded_mp4_or_mpegts_is_cut_to_the_interval(self):
        for name, extra in (("full.mp4", ()), ("full.ts", ("-f", "mpegts"))):
            with self.subTest(name=name):
                source = self.folder / name
                _testsrc(source, extra=extra)
                output = self.folder / f"cut-{name}.mp4"
                social.cut_local(source, output, 1, 2)
                self.assertAlmostEqual(1.0, media.probe(output)["duration_s"], delta=0.25)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002  # pylint: disable=redefined-builtin
        pass


@unittest.skipUnless(YT_DLP and FFMPEG, "yt-dlp and FFmpeg required")
class NativeAesHlsTests(unittest.TestCase):
    """O HLS com AES-128 (playlist, chave e segmentos) baixa pelo yt-dlp sem FFmpeg e corta local."""

    def test_an_aes_128_hls_downloads_natively_and_cuts_locally(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with  # enterContext limpa
        folder = Path(self.enterContext(tmp))
        handler = functools.partial(_QuietHandler, directory=str(folder))
        server = self.enterContext(http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        (folder / "k.key").write_bytes(bytes(range(16)))
        (folder / "k.info").write_text(f"http://127.0.0.1:{port}/k.key\n{folder / 'k.key'}\n", encoding="utf-8")
        _testsrc(folder / "main.m3u8", extra=("-hls_time", "1", "-hls_key_info_file", str(folder / "k.info")))
        self.assertIn("METHOD=AES-128", (folder / "main.m3u8").read_text(encoding="utf-8"))
        empty = folder / "sem-ffmpeg"
        empty.mkdir()
        full = folder / "full.mp4"
        # Sem o proxy (ele recusaria o loopback): o que se prova aqui é o download nativo sem FFmpeg.
        argv = [str(YT_DLP), "--ignore-config", "--ffmpeg-location", str(empty), "--socket-timeout", "5"]
        arguments = social.segment_arguments(0, 2, full, open_web=True)
        result = _run_tool([*argv, *arguments, "--", f"http://127.0.0.1:{port}/main.m3u8"])
        self.assertEqual(0, result.returncode, result.stderr[-400:])
        output = folder / "cut.mp4"
        social.cut_local(full, output, 1, 2)
        self.assertAlmostEqual(1.0, media.probe(output)["duration_s"], delta=0.25)


if __name__ == "__main__":
    unittest.main()
