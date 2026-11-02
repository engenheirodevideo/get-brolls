"""instagram_pairs: recusas de URL do config curl, falhas do curl/ffmpeg e do resumo (sem rede)."""

import contextlib
import errno
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _logs import LogCase
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _symlink import needs_symlink

from getbrolls import instagram_common, instagram_media, labels, queue
from getbrolls import instagram_pairs as ig

PUBLIC_ROW = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
PUBLIC_DNS = [PUBLIC_ROW]
LOGGER = "getbrolls.instagram_pairs"


def write_conf(path, url, output=None):
    lines = [f'url = "{url}"']
    if output is not None:
        lines.append(f'output = "{output}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def curl_failure(returncode, stderr):
    def fail(cmd, **_kwargs):
        raise subprocess.CalledProcessError(returncode, cmd, stderr=stderr)

    return fail


class QuietCase(LogCase):
    """Terminal em silêncio, com o stderr guardado em `self.err`."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-igpairs-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.out = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.err = self.enterContext(contextlib.redirect_stderr(io.StringIO()))


class ShellEchoTests(QuietCase):
    def test_sh_quote_leaves_safe_words_and_quotes_the_rest(self):
        self.assertEqual("''", ig.sh_quote(""))
        self.assertEqual("https://a.b/c_d-e=1", ig.sh_quote("https://a.b/c_d-e=1"))
        self.assertEqual("'a b'", ig.sh_quote("a b"))
        self.assertEqual("'it'\\''s'", ig.sh_quote("it's"))

    def test_run_echoes_the_quoted_command_unless_quiet(self):
        done = subprocess.CompletedProcess(["echo"], 0, "ok", "")
        with patch.object(subprocess, "run", return_value=done):
            self.assertIs(done, ig.run(["echo", "a b", ""]))
            self.assertEqual("+ echo 'a b' ''\n", self.err.getvalue())
            self.err.truncate(0)
            self.err.seek(0)
            ig.run(["echo", "x"], quiet=True)
        self.assertEqual("", self.err.getvalue())

    def test_a_failed_command_is_a_collect_error_with_its_exit_and_stderr_tail(self):
        failing = curl_failure(3, "boom: falhou feio")
        with patch.object(subprocess, "run", side_effect=failing), self.assertRaises(ig.CollectError) as caught:
            ig.run(["ffmpeg", "-i", "x"], quiet=True)
        self.assertEqual(1, caught.exception.code)
        self.assertIn("ffmpeg falhou (exit 3): boom: falhou feio", caught.exception.message)
        self.assertIn("ERROR: ffmpeg falhou (exit 3)", self.err.getvalue())


class UrlRefusalTests(QuietCase):
    def refusal(self, url):
        conf = write_conf(self.root / "c.conf", url)
        with (
            patch.object(socket, "getaddrinfo", return_value=PUBLIC_DNS),
            self.captured() as logged,
            self.assertRaises(ig.CollectError) as caught,
        ):
            ig.parse_curl_config(conf)
        self.assertEqual(1, caught.exception.code)
        self.assertIn(str(conf), caught.exception.message)
        return self.rows(logged).all("media_url_refused"), caught.exception

    def test_every_refusal_reports_its_reason_and_never_the_url(self):
        cases = {
            "https://[::1/secret": "invalid_url",
            "https://segredo.example.org:99999/x": "invalid_url",
            "https://segredo.example.org/a\x00b": "invalid_url",
            "https://segredo.example.org/a\x1fb": "invalid_url",
            "https://segredo.example.org/a\x7fb": "invalid_url",
            "https://segredo.example.org/a\tb": "invalid_url",
            "ftp://segredo.example.org/x": "scheme_or_host_invalid",
            "http://segredo.example.org/x": "scheme_or_host_invalid",
            "https:///segredo": "scheme_or_host_invalid",
            "https://user:pw@segredo.example.org/x": "credentials_in_url",
            "https://user@segredo.example.org/x": "credentials_in_url",
            "https://segredo.example.org:8443/x": "nonstandard_port",
            "https://segredo.example.org./x": "trailing_dot_host",
            "https://localhost/segredo": "local_host",
            "https://a.localhost/segredo": "local_host",
            "https://printer.local/segredo": "local_host",
            "https://segredo_x.example.org/x": "invalid_hostname",
            "https://-segredo.example.org/x": "invalid_hostname",
            "https://10.0.0.2/segredo": "private_address",
            "https://127.0.0.1/segredo": "private_address",
            "https://[::1]/segredo": "private_address",
        }
        for url, reason in cases.items():
            with self.subTest(url=url):
                rows, error = self.refusal(url)
                self.assertEqual([reason], [row["reason"] for row in rows])
                self.assertNotIn("segredo", error.message)
                self.assertNotIn("segredo", self.err.getvalue())

    def test_dns_failures_and_private_answers_are_refused(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.9", 443))]
        mixed = [PUBLIC_ROW, (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fd00::1", 443, 0, 0))]
        for name, dns, reason in (
            ("gaierror", socket.gaierror(socket.EAI_NONAME, "sem nome"), "dns_resolution_failed"),
            ("oserror", OSError("rede fora"), "dns_resolution_failed"),
            ("empty", [], "dns_no_address"),
            ("private", private, "private_address"),
            ("mixed", mixed, "private_address"),
        ):
            with self.subTest(name=name):
                conf = write_conf(self.root / "c.conf", "https://media.example.test/v")
                lookup: Any = {"side_effect": dns} if isinstance(dns, BaseException) else {"return_value": dns}
                with (
                    patch.object(socket, "getaddrinfo", **lookup),
                    self.captured() as logged,
                    self.assertRaises(ig.CollectError),
                ):
                    ig.parse_curl_config(conf)
                self.assertEqual([reason], [row["reason"] for row in self.rows(logged).all("media_url_refused")])

    def test_a_public_ip_literal_needs_no_dns_and_no_pin(self):
        for url in ("https://93.184.216.34/v", "https://[2606:2800:220:1:248:1893:25c8:1946]/v"):
            with self.subTest(url=url):
                conf = write_conf(self.root / "c.conf", url)
                with patch.object(socket, "getaddrinfo", side_effect=AssertionError("sem DNS")):
                    parsed = ig.parse_curl_config(conf)
                self.assertEqual({"url": url, "output": None, "curl_resolve": None}, parsed)

    def test_the_host_is_pinned_in_lower_case_and_the_port_443_is_accepted(self):
        conf = write_conf(self.root / "c.conf", "https://Media.Example.TEST:443/v")
        with patch.object(socket, "getaddrinfo", return_value=PUBLIC_DNS):
            parsed = ig.parse_curl_config(conf)
        self.assertEqual("media.example.test:443:93.184.216.34", parsed["curl_resolve"])

    def test_a_config_without_a_url_line_is_refused(self):
        conf = self.root / "c.conf"
        conf.write_text('# só comentário\noutput = "x.mp4"\n', encoding="utf-8")
        with self.assertRaisesRegex(ig.CollectError, "linha url ausente no config curl"):
            ig.parse_curl_config(conf)

    def test_the_last_url_and_output_lines_win(self):
        conf = self.root / "c.conf"
        conf.write_text('url = "https://93.184.216.34/a"\nurl = "https://93.184.216.34/b"\noutput = "o.mp4"\n')
        parsed = ig.parse_curl_config(conf)
        self.assertEqual(("https://93.184.216.34/b", "o.mp4"), (parsed["url"], parsed["output"]))


class StemAndPairTests(QuietCase):
    def test_flat_student_and_auto_layouts(self):
        out = self.root / "out"
        self.assertEqual(out / "ana_01_AbC.mp4", ig.infer_output_for_stem("ana_01_AbC", out, "flat"))
        self.assertEqual(out / "ana" / "01_AbC.mp4", ig.infer_output_for_stem("ana_01_AbC", out, "student"))
        self.assertEqual(out / "ana" / "01_AbC.mp4", ig.infer_output_for_stem("ana_01_AbC", out, "auto"))
        # `01_A_x`: o "usuário" é só um número de duas casas, então não é stem de aluno.
        self.assertEqual(out / "01_02_x.mp4", ig.infer_output_for_stem("01_02_x", out, "auto"))
        self.assertEqual(out / "solto.mp4", ig.infer_output_for_stem("solto", out, "auto"))

    def test_layout_student_demands_the_student_shape(self):
        with self.assertRaisesRegex(ig.CollectError, "layout=student exige stem"):
            ig.infer_output_for_stem("solto", self.root / "out", "student")

    @needs_symlink
    def test_a_user_folder_that_links_outside_the_output_dir_is_refused(self):
        out, fora = self.root / "out", self.root / "fora"
        out.mkdir()
        fora.mkdir()
        (out / "ana").symlink_to(fora)
        with self.assertRaisesRegex(ig.CollectError, "dentro de --output-dir"):
            ig.infer_output_for_stem("ana_01_AbC", out, "student")

    def test_pair_configs_pairs_by_stem_in_sorted_order(self):
        for stem in ("b", "a"):
            write_conf(self.root / f"{stem}_video.conf", "https://example.org/v")
            write_conf(self.root / f"{stem}_audio.conf", "https://example.org/a")
        pairs = ig.pair_configs(self.root)
        self.assertEqual(["a", "b"], [stem for stem, _video, _audio in pairs])
        self.assertEqual(self.root / "a_audio.conf", pairs[0][2])

    def test_a_video_config_without_its_audio_and_an_empty_folder_are_refused(self):
        with self.assertRaisesRegex(ig.CollectError, "nenhum \\*_video.conf encontrado"):
            ig.pair_configs(self.root)
        write_conf(self.root / "a_video.conf", "https://example.org/v")
        with self.assertRaisesRegex(ig.CollectError, "config de áudio ausente para a"):
            ig.pair_configs(self.root)

    def test_ensure_tool_refuses_a_missing_command(self):
        with (
            patch.object(shutil, "which", return_value=None),
            self.assertRaisesRegex(ig.CollectError, "comando obrigatório não encontrado: curl"),
        ):
            ig.ensure_tool("curl")
        with patch.object(shutil, "which", return_value="/usr/bin/curl"):
            self.assertIsNone(ig.ensure_tool("curl"))


class DownloadEdgeTests(QuietCase):
    def setUp(self):
        super().setUp()
        self.part = self.root / "parts" / "v.mp4"
        self.getaddrinfo = self.enterContext(patch.object(socket, "getaddrinfo", return_value=PUBLIC_DNS))

    def download(self, conf, **overrides):
        kwargs: dict[str, Any] = {
            "cfg_path": conf,
            "part_path": self.part,
            "config_output_root": self.root,
            "force_download": False,
            "prefer_config_output": False,
        }
        kwargs.update(overrides)
        return ig.download_or_reuse(**kwargs)

    def test_a_cdn_block_with_text_stderr_is_a_cooldown_that_keeps_the_status(self):
        conf = write_conf(self.root / "v.conf", "https://example.org/v")
        failing = curl_failure(22, "curl: (22) The requested URL returned error: 429")
        with (
            patch.object(subprocess, "run", side_effect=failing),
            self.captured() as logged,
            self.assertRaises(ig.CollectError) as caught,
        ):
            self.download(conf)
        self.assertTrue(caught.exception.cooldown)
        self.assertEqual("429", caught.exception.http_status)
        self.assertIn("HTTP 429", caught.exception.message)
        self.assertEqual("error", self.rows(logged).one("pair_stage")["status"])
        self.assertFalse(self.part.exists())

    def test_a_missing_curl_is_a_plain_failure_without_exit_code_or_cooldown(self):
        conf = write_conf(self.root / "v.conf", "https://example.org/v")
        with (
            patch.object(subprocess, "run", side_effect=FileNotFoundError("curl")),
            self.captured() as logged,
            self.assertRaises(ig.CollectError) as caught,
        ):
            self.download(conf, stage="video")
        self.assertFalse(caught.exception.cooldown)
        self.assertIn("curl falhou para o config", caught.exception.message)
        self.assertNotIn("exit", caught.exception.message)
        self.assertEqual("video", self.rows(logged).one("pair_stage")["stage"])

    def test_the_config_is_named_by_its_label_so_the_prose_after_it_survives(self):
        conf = write_conf(self.root / "v.conf", "https://example.org/v")
        for failure, tail in (
            (FileNotFoundError("curl"), "recapture URLs expiradas/proibidas e tente de novo"),
            (curl_failure(22, "curl: (22) The requested URL returned error: 429"), "aguarde o cooldown"),
        ):
            with (
                self.subTest(tail=tail),
                patch.object(subprocess, "run", side_effect=failure),
                self.captured(),
                self.assertRaises(ig.CollectError) as caught,
            ):
                self.download(conf)
            message = caught.exception.message
            self.assertNotIn(str(self.root), message)
            self.assertIn(tail, labels.for_log(message))

    def test_a_transfer_that_writes_nothing_is_an_empty_media_error(self):
        conf = write_conf(self.root / "v.conf", "https://example.org/v")

        def writes_nothing(_cmd, **_kwargs):
            return subprocess.CompletedProcess([], 0)

        def writes_zero_bytes(cmd, **_kwargs):
            Path(cmd[cmd.index("--output") + 1]).write_bytes(b"")
            return subprocess.CompletedProcess([], 0)

        for name, fake in (("missing", writes_nothing), ("zero", writes_zero_bytes)):
            with self.subTest(name=name):
                with patch.object(subprocess, "run", side_effect=fake), self.assertRaises(ig.CollectError) as caught:
                    self.download(conf)
                self.assertIn("mídia vazia para o config", caught.exception.message)
                self.assertFalse(self.part.exists())
                self.assertEqual([], list(self.part.parent.iterdir()))

    def test_an_ip_literal_url_goes_to_curl_without_resolve(self):
        conf = write_conf(self.root / "v.conf", "https://93.184.216.34/v")
        commands, inputs = [], []

        def ok(cmd, **kwargs):
            commands.append(cmd)
            inputs.append(kwargs.get("input"))
            Path(cmd[cmd.index("--output") + 1]).write_bytes(b"midia")
            return subprocess.CompletedProcess(cmd, 0)

        with patch.object(subprocess, "run", side_effect=ok):
            self.assertEqual("downloaded", self.download(conf))
        self.assertNotIn("--resolve", commands[0])
        self.assertEqual(b'url = "https://93.184.216.34/v"\n', inputs[0])
        self.assertEqual(b"midia", self.part.read_bytes())

    def run_download(self, url):
        """Baixa `url` com um curl de mentira e devolve (argv, stdin, kwargs) da chamada."""
        conf = write_conf(self.root / "v.conf", url)
        seen = {}

        def ok(cmd, **kwargs):
            seen.update(cmd=cmd, input=kwargs.get("input"), kwargs=kwargs)
            Path(cmd[cmd.index("--output") + 1]).write_bytes(b"midia")
            return subprocess.CompletedProcess(cmd, 0)

        with patch.object(subprocess, "run", side_effect=ok):
            self.assertEqual("downloaded", self.download(conf))
        return seen["cmd"], seen["input"], seen["kwargs"]

    def test_the_signed_url_reaches_curl_on_stdin_and_never_in_the_argument_list(self):
        url = "https://media.example.test/v.mp4?oe=6A1B2C3D&oh=ASSINATURA-SECRETA&bytestart=0"
        cmd, stdin, _kwargs = self.run_download(url)
        self.assertFalse(any("ASSINATURA-SECRETA" in arg or url in arg for arg in cmd), cmd)
        self.assertFalse(any(arg.startswith("http") for arg in cmd), cmd)
        self.assertEqual(["--config", "-"], cmd[-2:])
        self.assertEqual(f'url = "{url}"\n'.encode(), stdin)

    def test_curl_skips_the_user_curlrc_with_q_as_its_first_option(self):
        cmd, _stdin, _kwargs = self.run_download("https://media.example.test/v.mp4?oe=1")
        self.assertEqual(["curl", "-q"], cmd[:2])
        self.assertEqual(1, cmd.count("-q"))

    def test_the_stdin_config_keeps_the_options_and_the_pin_of_the_transfer(self):
        cmd, _stdin, _kwargs = self.run_download("https://media.example.test/v.mp4?oe=1")
        for option, value in (("--proto", "=https"), ("--max-time", "180")):
            self.assertEqual(value, cmd[cmd.index(option) + 1])
        self.assertIn("--fail", cmd)
        self.assertNotIn("--retry", cmd, "a retry would hit a 429 again")
        self.assertEqual("media.example.test:443:93.184.216.34", cmd[cmd.index("--resolve") + 1])

    def test_a_quote_and_a_backslash_in_the_url_are_escaped_for_the_curl_config(self):
        self.assertEqual(b'url = "https://h.example/a\\"b\\\\c"\n', ig.curl_config_input('https://h.example/a"b\\c'))
        self.assertEqual(b'url = "https://h.example/\\tx"\n', ig.curl_config_input("https://h.example/\tx"))
        self.assertEqual('url = "caf\u00e9"\n'.encode(), ig.curl_config_input("caf\u00e9"))

    def test_a_corrupt_config_output_is_refused_instead_of_reused(self):
        source = self.root / "ja-baixado.mp4"
        source.write_bytes(b"truncado")
        conf = write_conf(self.root / "v.conf", "https://example.org/v", output=source)
        with (
            patch.object(subprocess, "run", side_effect=curl_failure(1, "Invalid data")),
            self.assertRaisesRegex(ig.CollectError, "parte reutilizada está corrompida; use --force-download"),
        ):
            self.download(conf, prefer_config_output=True)
        self.assertFalse(self.part.exists())

    def test_a_valid_config_output_is_copied_to_the_part_or_left_alone_when_it_is_the_part(self):
        source = self.root / "ja-baixado.mp4"
        source.write_bytes(b"completo")
        probe_ok = subprocess.CompletedProcess([], 0, "{}", "")
        with patch.object(subprocess, "run", return_value=probe_ok) as probe:
            conf = write_conf(self.root / "v.conf", "https://example.org/v", output=source)
            self.assertEqual("reused-config-output", self.download(conf, prefer_config_output=True))
            self.assertEqual(b"completo", self.part.read_bytes())
            self.assertEqual("ffprobe", probe.call_args.args[0][0])
            same = write_conf(self.root / "same.conf", "https://example.org/v", output=self.part)
            self.part.write_bytes(b"intacto")
            self.assertEqual("reused-config-output", self.download(same, prefer_config_output=True))
        self.assertEqual(b"intacto", self.part.read_bytes())


class MergeAndVerifyTests(QuietCase):
    def test_merge_copies_the_streams_or_reencodes_them(self):
        commands = []
        with patch.object(instagram_common, "run", side_effect=lambda cmd, quiet=False: commands.append((cmd, quiet))):
            ig.merge_parts(Path("v.mp4"), Path("a.mp4"), self.root / "sub" / "out.mp4", copy_streams=True)
            ig.merge_parts(Path("v.mp4"), Path("a.mp4"), self.root / "sub" / "out.mp4", copy_streams=False)
        self.assertEqual(2, len(commands))
        copied, quiet_a = commands[0]
        encoded, quiet_b = commands[1]
        self.assertTrue(quiet_a and quiet_b)
        self.assertEqual(["-c", "copy"], copied[copied.index("-c") : copied.index("-c") + 2])
        self.assertNotIn("libx264", copied)
        self.assertIn("libx264", encoded)
        self.assertIn("aac", encoded)
        self.assertEqual(str(self.root / "sub" / "out.mp4"), encoded[-1])
        self.assertTrue((self.root / "sub").is_dir())

    def test_an_output_without_video_or_without_audio_is_refused(self):
        audio = {"codec_type": "audio", "codec_name": "aac"}
        video = {"codec_type": "video", "codec_name": "h264"}
        for streams, message in (
            ([audio], "não tem stream de vídeo"),
            ([video], "não tem stream de áudio"),
            ([], "não tem stream de vídeo"),
        ):
            with (
                self.subTest(message=message),
                patch.object(instagram_media, "ffprobe_json", return_value={"streams": streams}),
                self.assertRaisesRegex(ig.CollectError, message),
            ):
                ig.verify_output(Path("x.mp4"))

    def test_the_audio_hash_is_only_computed_on_request(self):
        data = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": 2, "height": 4},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"size": "10", "duration": "1.5"},
        }
        with (
            patch.object(instagram_media, "ffprobe_json", return_value=data),
            patch.object(instagram_media, "audio_hash", return_value="abc") as hashed,
        ):
            quick = ig.verify_output(Path("x.mp4"), compute_audio_hash=False)
            hashed.assert_not_called()
            full = ig.verify_output(Path("x.mp4"))
        self.assertIsNone(quick["audio_hash_sha256"])
        self.assertEqual("abc", full["audio_hash_sha256"])
        self.assertEqual(
            (10, 1.5, "h264", "yuv420p", 2, 4, "aac"),
            tuple(quick[k] for k in ("size", "duration", "video_codec", "pix_fmt", "width", "height", "audio_codec")),
        )
        with patch.object(instagram_media, "ffprobe_json", return_value={"streams": data["streams"]}):
            bare = ig.verify_output(Path("x.mp4"), compute_audio_hash=False)
        self.assertEqual((0, 0.0), (bare["size"], bare["duration"]))


class PublishAndProcessTests(QuietCase):
    def test_a_write_that_fails_after_the_file_was_created_removes_the_partial_output(self):
        source = self.root / "src.mp4"
        source.write_bytes(b"midia")
        destination = self.root / "out.mp4"
        with patch.object(os, "fsync", side_effect=OSError(errno.EIO, "disco")), self.assertRaises(OSError):
            ig.publish_exclusive(source, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(b"midia", source.read_bytes())

    def test_a_missing_source_fails_without_touching_the_destination(self):
        destination = self.root / "out.mp4"
        destination.write_bytes(b"de outra pessoa")
        with self.assertRaises(FileNotFoundError):
            ig.publish_exclusive(self.root / "nao-existe.mp4", destination)
        self.assertEqual(b"de outra pessoa", destination.read_bytes())

    def process(self, **patches):
        output = self.root / "out" / "x.mp4"
        with (
            patch.object(ig, "download_or_reuse", return_value="downloaded"),
            patch.object(ig, "merge_parts", side_effect=patches.get("merge")),
            patch.object(ig, "verify_output", side_effect=patches.get("verify")),
            self.captured() as logged,
            self.assertRaises(ig.CollectError) as caught,
        ):
            ig.process_one(
                stem="x",
                video_config=self.root / "v.conf",
                audio_config=self.root / "a.conf",
                output=output,
                parts_dir=self.root / "parts",
                config_output_root=self.root,
                force_download=False,
                prefer_config_output=False,
                copy_streams=True,
            )
        self.assertFalse(output.exists())
        self.assertEqual([], list(output.parent.iterdir()), "a pasta temporária do estágio some")
        return self.rows(logged), caught.exception

    def test_a_failed_merge_is_logged_and_leaves_no_output(self):
        rows, error = self.process(merge=ig.CollectError("ffmpeg falhou"))
        self.assertEqual("ffmpeg falhou", error.message)
        self.assertEqual("error", rows.one("pair_stage", stage="merge")["status"])
        self.assertEqual(["merge"], [row["stage"] for row in rows.all("pair_stage")], "a validação nem começou")

    def test_a_failed_validation_is_logged_and_publishes_nothing(self):
        def merged(_video, _audio, pending, *, copy_streams):
            self.assertTrue(copy_streams)
            pending.write_bytes(b"mesclado")

        rows, error = self.process(merge=merged, verify=ig.CollectError("sem áudio"))
        self.assertEqual("sem áudio", error.message)
        self.assertEqual("ok", rows.one("pair_stage", stage="merge")["status"])
        self.assertEqual("error", rows.one("pair_stage", stage="validation")["status"])


class SummaryWriteTests(QuietCase):
    def test_no_path_writes_nothing(self):
        self.assertIsNone(ig.write_summary(None, {"a": 1}))
        self.assertEqual("", self.err.getvalue())

    def test_an_unwritable_summary_is_a_warning_and_never_stops_the_batch(self):
        blocker = self.root / "arquivo"
        blocker.write_text("não é pasta", encoding="utf-8")
        self.assertIsNone(ig.write_summary(blocker / "sub" / "s.json", {"a": 1}))
        self.assertIn("WARNING: não foi possível gravar --summary-json", self.err.getvalue())
        self.assertEqual("não é pasta", blocker.read_text(encoding="utf-8"))

    def test_the_summary_is_written_as_utf8_json(self):
        path = self.root / "pasta" / "s.json"
        ig.write_summary(path, {"motivo": "ação"})
        self.assertEqual({"motivo": "ação"}, json.loads(path.read_text(encoding="utf-8")))


class CooldownRecordTests(QuietCase):
    def setUp(self):
        super().setUp()
        queue.save(queue.queue_path(self.root), queue.empty_state())

    def test_a_missing_project_records_nothing(self):
        self.assertIsNone(ig.record_queue_cooldown(None, "403"))
        self.assertIn("nenhum --project informado", self.err.getvalue())

    def test_missing_dependencies_and_lock_failures_are_warnings(self):
        with patch.dict(sys.modules, {"getbrolls.queue": None}):
            self.assertIsNone(ig.record_queue_cooldown(self.root, "403"))
        self.assertIn("dependências indisponíveis", self.err.getvalue())
        with (
            patch("getbrolls.runtime.project_lock", side_effect=OSError("trava ocupada")),
            self.assertRaises(ig.QueueNotRecordedError),
        ):
            ig.record_queue_cooldown(self.root, "403")
        self.assertIn("cooldown não registrado: Erro de E/S", self.err.getvalue())
        with patch("getbrolls.queue.record_cooldown", side_effect=ValueError("fila ruim")):
            self.assertIsNone(ig.record_queue_cooldown(self.root, "403"))
        self.assertIn("cooldown não registrado (fila ruim)", self.err.getvalue())
        self.assertFalse(
            queue.load(queue.queue_path(self.root))["providers"].get("instagram", {}).get("cooldown_until")
        )

    def test_a_project_without_queue_json_records_nothing(self):
        empty = self.root / "outro"
        empty.mkdir()
        self.assertIsNone(ig.record_queue_cooldown(empty, "403"))
        self.assertIn("nenhum work/queue.json", self.err.getvalue())


class BatchEdgeTests(QuietCase):
    def setUp(self):
        super().setUp()
        self.real_ensure_tool = ig.ensure_tool
        self.enterContext(patch.object(ig, "ensure_tool"))
        self.enterContext(patch.object(time, "sleep"))
        configs = self.root / "configs"
        configs.mkdir()
        for stem in ("01_A", "02_B"):
            write_conf(configs / f"{stem}_video.conf", "https://example.org/v")
            write_conf(configs / f"{stem}_audio.conf", "https://example.org/a")

    def argv(self, *extra):
        return [
            *("--config-dir", str(self.root / "configs"), "--output-dir", str(self.root / "out")),
            *("--parts-dir", str(self.root / "parts"), "--config-output-root", str(self.root)),
            *("--layout", "flat", "--pace", "0", "--summary-json", str(self.root / "s.json"), *extra),
        ]

    def summary(self):
        return json.loads((self.root / "s.json").read_text(encoding="utf-8"))

    def test_an_unexpected_exception_is_recorded_by_type_and_the_batch_goes_on(self):
        def flaky(**kwargs):
            if kwargs["stem"] == "01_A":
                raise RuntimeError("falha https://cdn.example/segredo?x=1 inesperada")
            return {"output": str(kwargs["output"]), "stem": kwargs["stem"], "audio_hash_sha256": "h"}

        with patch.object(ig, "process_one", side_effect=flaky):
            self.assertEqual(1, ig.main(self.argv("--continue-on-error")))
        results = self.summary()["results"]
        self.assertEqual(["failed", "done"], [item["status"] for item in results])
        self.assertTrue(results[0]["reason"].startswith("RuntimeError: falha"))
        self.assertNotIn("cdn.example", results[0]["reason"])

    def test_an_unexpected_exception_without_continue_ends_the_batch_with_exit_1(self):
        with patch.object(ig, "process_one", side_effect=RuntimeError("quebrou")) as processed:
            self.assertEqual(1, ig.main(self.argv()))
        self.assertEqual(1, processed.call_count)
        self.assertIn("-- lote interrompido: RuntimeError: quebrou", self.err.getvalue())
        self.assertEqual(["failed", "skipped"], [item["status"] for item in self.summary()["results"]])

    def test_duplicate_audio_is_only_an_error_when_the_hashes_repeat(self):
        def hashed(value):
            return lambda **kw: {"output": str(kw["output"]), "stem": kw["stem"], "audio_hash_sha256": value(kw)}

        with patch.object(ig, "process_one", side_effect=hashed(lambda kw: kw["stem"])):
            self.assertEqual(0, ig.main(self.argv("--fail-on-duplicate-audio")))
        with patch.object(ig, "process_one", side_effect=hashed(lambda kw: "igual")):
            self.assertEqual(1, ig.main(self.argv("--fail-on-duplicate-audio", "--output-dir", str(self.root / "o2"))))
        self.assertIn("hash de áudio duplicado detectado", self.err.getvalue())

    def assert_refused(self, argv, message):
        """A setup refusal ends like every batch error: exit 1 and the PT message, never a traceback."""
        self.assertEqual(1, ig.main(argv))
        self.assertIn(f"ERROR: {message}", self.err.getvalue())
        self.assertIn(f"-- lote interrompido: {message}", self.err.getvalue())
        self.assertEqual("", self.out.getvalue())

    def test_the_single_pair_mode_demands_audio_config_and_output(self):
        video = write_conf(self.root / "one_video.conf", "https://example.org/v")
        self.assert_refused(
            ["--video-config", str(video), "--audio-config", str(video)],
            "--audio-config e --output são obrigatórios com --video-config",
        )

    def test_the_batch_mode_demands_the_output_dir(self):
        self.assert_refused(["--config-dir", str(self.root / "configs")], "--output-dir é obrigatório com --config-dir")

    def test_a_config_dir_without_pairs_is_refused_with_exit_1(self):
        empty = self.root / "vazio"
        empty.mkdir()
        self.assert_refused(
            ["--config-dir", str(empty), "--output-dir", str(self.root / "out")],
            f"nenhum *_video.conf encontrado em {empty}",
        )

    def test_a_missing_tool_is_refused_with_exit_1(self):
        for tool in ("curl", "ffmpeg", "ffprobe"):
            with self.subTest(tool=tool):
                self.err.seek(0)
                self.err.truncate()
                with (
                    patch.object(ig, "ensure_tool", self.real_ensure_tool),
                    patch.object(shutil, "which", side_effect=lambda name, t=tool: None if name == t else name),
                    patch.object(ig, "run_batch") as batch,
                ):
                    self.assert_refused(self.argv(), f"comando obrigatório não encontrado: {tool}")
                batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
