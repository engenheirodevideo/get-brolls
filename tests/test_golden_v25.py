"""Ouro da 2.5: a CLI em teste repete byte a byte a matriz gravada; contra a v2.5.0, só a lista fechada.

Regravar: `GB_UPDATE_GOLDEN=1 GB_GOLDEN_REASON="<motivo>"` nesta suíte (o motivo vai para
`REGRAVACOES.md`). Diferença nova contra a v2.5.0 só passa com item datado no `accepted.json`.
"""

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import _golden
import _golden_diff
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV, ROOT, WHEEL_MODE
from test_repository import LOCAL_PATH_PATTERN

from getbrolls import _paths as gb_paths

UPDATE = os.environ.get("GB_UPDATE_GOLDEN") == "1"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
# UTC-12 e UTC+14: a qualquer hora do dia, ao menos um dos dois já está em outra data.
SHIFTED_ZONES = ("Etc/GMT+12", "Etc/GMT-14")
FRONTMATTER_DATE = re.compile(r"(?:created|updated): (\d{4}-\d\d-\d\d)")
RECORDING_DAY = re.compile(r"^- (\d{4}-\d\d-\d\d): ", re.MULTILINE)


def _expected(scenario, case):
    return (_golden.EXPECTED / scenario / f"{case}.json").read_text(encoding="utf-8")


def assert_matches_golden(test, scenario, record):
    """O registro é igual ao arquivo do ouro, salvo tamanho e sha do clipe cortado pelo ffmpeg (variam com o build)."""
    # A máscara não esconde campo que sumiu: as linhas `fetch` gravadas ainda têm os dois, com um clipe de verdade.
    actual = _golden.serialize(record)
    test.assertEqual([], _golden.fetch_lines_without_clip_fields(actual))
    # A chave da venv do `doctor` tem de ser a que o checkout calcula dos arquivos de requisitos; o arquivo do ouro
    # guarda a do dia em que foi gravado.
    venv_key = gb_paths.part_sha("venv")
    test.assertEqual(
        _golden.comparable(_expected(scenario, record["case"])), _golden.comparable(actual, venv_key=venv_key)
    )


def skip_unless_pinned(test, tmp=None):
    """Pula quando a rodada não consegue fixar um ffmpeg sem `drawtext`: com o `drawtext` do host o ouro não vale."""
    reason = _golden.pin_skipped_reason(tmp)
    if reason:
        test.skipTest(f"ffmpeg not pinned: {reason}")


@unittest.skipIf(WHEEL_MODE, "o ouro grava o checkout: no pacote instalado os caminhos sugeridos são outros")
@unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
class GoldenMatrixTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        skip_unless_pinned(self)

    def run_scenario(self, scenario):
        with tempfile.TemporaryDirectory(prefix="gb-ouro-") as raw:
            skip_unless_pinned(self, Path(raw))
            records = _golden.record_scenario(list(CLI_ARGV), ROOT, scenario, Path(raw))
        if UPDATE:
            reason = _golden.update_reason(os.environ)
            _golden.dump(records, _golden.EXPECTED / scenario)
            _golden.log_rewrite(f"{scenario}: {reason}", datetime.now(UTC).date().isoformat())
            return
        recorded = sorted(path.stem for path in (_golden.EXPECTED / scenario).glob("*.json"))
        self.assertEqual(recorded, sorted(record["case"] for record in records))
        for record in records:
            with self.subTest(case=record["case"]):
                assert_matches_golden(self, scenario, record)

    def test_fluxo_matches_the_golden_byte_for_byte(self):
        self.run_scenario("fluxo")

    def test_layout0_from_v250_matches_the_golden_byte_for_byte(self):
        self.run_scenario("layout0")


@unittest.skipIf(WHEEL_MODE, "o ouro grava o checkout: no pacote instalado os caminhos sugeridos são outros")
@unittest.skipUnless(HAS_FFMPEG, "FFmpeg required")
@unittest.skipIf(os.name == "nt", "o TZ do Windows não entende Etc/GMT±N")
class GoldenOnAnotherDayTests(unittest.TestCase):
    """O ouro vale em qualquer dia: a CLI grava num fuso em que hoje é outra data e repete os mesmos bytes."""

    maxDiff = None

    def setUp(self):
        skip_unless_pinned(self)

    def _other_day(self, raw):
        today = datetime.now(UTC).date().isoformat()
        shifted = []
        for zone in SHIFTED_ZONES:
            with unittest.mock.patch.object(_golden, "HERMETIC_TZ", zone):
                if _golden.run_date(Path(raw) / "sonda") != today:
                    shifted.append(zone)
        self.assertTrue(shifted, f"nenhum de {SHIFTED_ZONES} está em outro dia")
        return shifted[0]

    def test_a_run_dated_another_day_repeats_the_golden(self):
        with tempfile.TemporaryDirectory(prefix="gb-ouro-") as raw:
            zone = self._other_day(raw)
        # O fluxo até o `reject` (o último caso que grava frontmatter) e o layout 0 inteiro.
        for scenario, stop in (("fluxo", "reject"), ("layout0", None)):
            with (
                tempfile.TemporaryDirectory(prefix="gb-ouro-") as raw,
                unittest.mock.patch.object(_golden, "HERMETIC_TZ", zone),
            ):
                records = _golden.record_scenario(list(CLI_ARGV), ROOT, scenario, Path(raw), stop_after=stop)
            for record in records:
                with self.subTest(zone=zone, case=record["case"]):
                    assert_matches_golden(self, scenario, record)


class IcaclsLineMaskTests(unittest.TestCase):
    """A linha `proc.run` do `icacls` (só no Windows) sai da comparação; nenhuma outra linha sai."""

    LOG = "work/proj/brolls/getbrolls.log"
    ICACLS = (
        "level=INFO logger=getbrolls.effects event=op operation=proc.run target=icacls outcome=ok "
        "duration_ms=<MS> error_code=- error=- command={command} exit=0"
    )

    @staticmethod
    def golden():
        return json.loads((_golden.EXPECTED / "fluxo" / "resolve_file.json").read_text(encoding="utf-8"))

    def with_line(self, line, rel=None):
        record = self.golden()
        lines = record["appends"].setdefault(rel or self.LOG, [])
        lines.insert(len(lines) - 1, line)
        return record

    def assert_masked(self, record):
        golden = (_golden.EXPECTED / "fluxo" / "resolve_file.json").read_text(encoding="utf-8")
        self.assertEqual(_golden.comparable(golden), _golden.comparable(_golden.serialize(record)))

    def assert_not_masked(self, record):
        golden = (_golden.EXPECTED / "fluxo" / "resolve_file.json").read_text(encoding="utf-8")
        self.assertNotEqual(_golden.comparable(golden), _golden.comparable(_golden.serialize(record)))

    def test_the_icacls_line_of_any_command_compares_equal_and_the_golden_file_is_untouched(self):
        for command in ("resolve", "learn", "queue"):
            with self.subTest(command=command):
                self.assert_masked(self.with_line(self.ICACLS.format(command=command)))
        self.assertFalse(any("target=icacls" in line for line in self.golden()["appends"][self.LOG]))

    def test_two_icacls_lines_compare_equal_on_both_sides(self):
        record = self.with_line(self.ICACLS.format(command="resolve"))
        record["appends"][self.LOG].insert(1, self.ICACLS.format(command="resolve"))
        self.assert_masked(record)
        self.assertEqual(_golden.comparable(_golden.serialize(record)), _golden.comparable(_golden.serialize(record)))

    def test_another_proc_run_line_still_fails(self):
        line = self.ICACLS.format(command="resolve")
        others = (
            ("another tool", line.replace("target=icacls", "target=ffprobe")),
            ("a failed icacls", line.replace("outcome=ok", "outcome=error")),
            ("another exit", line.replace("exit=0", "exit=5")),
            ("a field more", line + " count=2"),
            ("another level", line.replace("level=INFO", "level=WARNING")),
            ("another operation", line.replace("operation=proc.run", "operation=fs.write")),
            ("a path as target", line.replace("target=icacls", "target=C:/Windows/System32/icacls.exe")),
        )
        for label, other in others:
            with self.subTest(label):
                self.assert_not_masked(self.with_line(other))

    def test_the_line_counts_outside_the_appends_of_a_getbrolls_log(self):
        line = self.ICACLS.format(command="resolve")
        self.assert_not_masked(self.with_line(line, rel="work/proj/brolls/diagnostics.jsonl"))
        record = self.golden()
        record["stderr"] = {"text": [line]}
        self.assert_not_masked(record)

    def test_a_text_that_is_not_a_record_is_left_as_it_is(self):
        for text in ("not json", "[1, 2]\n", '{"appends": []}\n'):
            with self.subTest(text=text):
                self.assertEqual(text, _golden.comparable(text))


class VenvKeyMaskTests(unittest.TestCase):
    """A chave da venv do `doctor` (sha dos arquivos de requisitos) é a única diferença que os dois lados podem ter."""

    OTHER_SHA = "0123456789abcdef"

    @staticmethod
    def doctor():
        return json.loads((_golden.EXPECTED / "fluxo" / "doctor.json").read_text(encoding="utf-8"))

    @staticmethod
    def text(record):
        return json.dumps(record, ensure_ascii=False, indent=1) + "\n"

    def with_key(self, key):
        other = self.doctor()
        other["stdout"]["json"]["install"]["runtime"]["shas"]["venv"] = key
        return other

    def test_the_key_the_checkout_computes_compares_equal_and_the_golden_file_is_untouched(self):
        golden = self.doctor()
        other = self.with_key(self.OTHER_SHA)
        self.assertNotEqual(self.text(golden), self.text(other))
        self.assertEqual(
            _golden.comparable(self.text(golden)), _golden.comparable(self.text(other), venv_key=self.OTHER_SHA)
        )
        self.assertIn(_golden.VENV_KEY_MARK, _golden.comparable(self.text(other), venv_key=self.OTHER_SHA))
        # O arquivo do ouro segue como foi gravado: a máscara só existe na comparação.
        self.assertEqual("297cf4d1dab00b59", golden["stdout"]["json"]["install"]["runtime"]["shas"]["venv"])

    def test_the_golden_file_text_compares_equal_to_a_record_with_the_expected_key(self):
        raw = (_golden.EXPECTED / "fluxo" / "doctor.json").read_text(encoding="utf-8")
        self.assertEqual(
            _golden.comparable(raw),
            _golden.comparable(_golden.serialize(self.with_key(self.OTHER_SHA)), venv_key=self.OTHER_SHA),
        )

    def test_a_wrong_or_stale_key_in_the_record_still_fails(self):
        # A chave das ferramentas no lugar da da venv, a chave velha do ouro e uma qualquer: nenhuma é a calculada.
        golden = self.doctor()
        for wrong in ("7ff9d2913771b98c", "297cf4d1dab00b59", "fedcba9876543210"):
            with self.subTest(wrong=wrong):
                text = self.text(self.with_key(wrong))
                self.assertNotEqual(
                    _golden.comparable(self.text(golden)), _golden.comparable(text, venv_key=self.OTHER_SHA)
                )
                self.assertNotIn(_golden.VENV_KEY_MARK, _golden.comparable(text, venv_key=self.OTHER_SHA))

    def test_any_other_change_in_the_doctor_still_fails(self):
        golden = self.doctor()
        changes = (
            ("tools key", ("stdout", "json", "install", "runtime", "shas", "tools"), "0123456789abcdef"),
            ("python", ("stdout", "json", "install", "python"), "<OTHER>"),
            ("exit", ("exit",), 1),
            ("ready", ("stdout", "json", "ready"), None),
        )
        for label, path, value in changes:
            other = self.with_key(self.OTHER_SHA)
            node = other
            for key in path[:-1]:
                node = node[key]
            self.assertNotEqual(value, node.get(path[-1]), label)
            node[path[-1]] = value
            with self.subTest(label):
                self.assertNotEqual(
                    _golden.comparable(self.text(golden)), _golden.comparable(self.text(other), venv_key=self.OTHER_SHA)
                )

    def test_only_a_16_hex_key_at_that_path_is_masked(self):
        golden = self.doctor()
        for bad in ("zzzz", "0123456789abcde", "0123456789ABCDEF", "0123456789abcdef0", None, 7):
            with self.subTest(venv=bad):
                text = self.text(self.with_key(bad))
                self.assertNotEqual(_golden.comparable(self.text(golden)), _golden.comparable(text))
                self.assertNotIn(_golden.VENV_KEY_MARK, _golden.comparable(text))
        # O mesmo campo em outro lugar não é mascarado.
        moved = {"stdout": {"json": {"venv": self.OTHER_SHA}}}
        self.assertEqual(self.text(moved), _golden.comparable(self.text(moved)))
        misplaced = self.doctor()
        misplaced["stdout"]["json"]["other"] = {"shas": {"venv": self.OTHER_SHA}}
        self.assertIn(self.OTHER_SHA, _golden.comparable(self.text(misplaced)))

    def test_the_checkout_key_is_the_sha_of_the_requirements_files(self):
        # O que o teste do ouro compara com o `doctor`: a mesma conta do `runtime/<sha>`.
        key = gb_paths.part_sha("venv")
        self.assertRegex(key or "", r"[0-9a-f]{16}")
        blob = b"\0".join(
            (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
            for name in ("requirements-bootstrap.txt", "requirements.txt")
        )
        self.assertEqual(hashlib.sha256(blob).hexdigest()[:16], key)

    def test_text_that_is_not_a_record_is_left_alone(self):
        for text in ("", "not json", '"level=INFO event=op"\n', "[1, 2]", '{"stdout": null}'):
            with self.subTest(text=text):
                self.assertNotIn(_golden.VENV_KEY_MARK, _golden.comparable(text))
                self.assertNotIn(_golden.VENV_KEY_MARK, _golden.comparable(text, venv_key=self.OTHER_SHA))


class ClipFieldsMaskTests(unittest.TestCase):
    """Tamanho e sha do clipe do ffmpeg dependem do build, não do sistema: os dois lados da comparação os escondem."""

    LINE = (
        '"level=INFO logger=getbrolls.commands event=fetch candidate=local:f6c96bb15a4f7e25:shot:abertura '
        'kind=local bytes={size} sha256_prefix={sha} ms=<MS> duration_ms=<MS>",\n'
    )

    def line(self, size="4727", sha="83b140333582"):
        return self.LINE.format(size=size, sha=sha)

    def test_two_builds_of_the_same_clip_compare_equal(self):
        one, other = self.line(), self.line("4718", "06f5a12cc56d")
        self.assertNotEqual(one, other)
        self.assertEqual(_golden.comparable(one), _golden.comparable(other))
        self.assertIn("bytes=<N> sha256_prefix=<SHA> ms=<MS>", _golden.comparable(one))

    def test_the_mask_touches_only_the_fetch_line_fields(self):
        other_line = '"level=INFO logger=getbrolls.x event=other bytes=12 sha256_prefix=83b140333582",\n'
        self.assertEqual(other_line, _golden.comparable(other_line))
        self.assertNotEqual(
            _golden.comparable(self.line()),
            _golden.comparable(self.line().replace("candidate=local", "candidate=remote")),
        )
        self.assertNotEqual(
            _golden.comparable(self.line()), _golden.comparable(self.line().replace("kind=local", "kind=remote"))
        )

    def test_an_empty_clip_is_not_hidden_by_the_mask(self):
        # O que não tem forma de clipe fica como está na comparação: o ouro exato (`bytes=4727`) o recusa.
        for empty in (
            self.line("0", "e3b0c44298fc"),
            self.line("0", "83b140333582"),
            self.line("4727", "e3b0c44298fc"),
        ):
            with self.subTest(line=empty):
                self.assertNotIn("<N>", _golden.comparable(empty))
                self.assertNotEqual(_golden.comparable(self.line()), _golden.comparable(empty))

    def test_a_fetch_line_without_the_fields_is_reported(self):
        self.assertEqual([], _golden.fetch_lines_without_clip_fields(self.line()))
        for broken in (
            self.line("None", "83b140333582"),
            self.line("4727", "-"),
            self.line("4727", "83b14033358"),
            self.line("4727", "83B140333582"),
            self.line("0", "83b140333582"),  # clipe vazio
            self.line("00", "83b140333582"),
            self.line("4727", "e3b0c44298fc"),  # sha do arquivo vazio
        ):
            with self.subTest(line=broken):
                self.assertEqual(1, len(_golden.fetch_lines_without_clip_fields(broken)))
        self.assertEqual(
            1, len(_golden.fetch_lines_without_clip_fields('"x event=fetch candidate=a kind=local ms=1",'))
        )


FAKE_FFMPEG = """#!{python}
import sys
if "-filters" in sys.argv[1:]:
    out = sys.stdout.buffer
    out.write(b" ... blend             VV->V      Blend two video frames.\\n")
    out.write(b" T.C drawtext          V->V       Draw text on top of video frames.\\n")
    out.write(b" ... odd               V->V       Not utf-8: \\xff\\xfe caf\\xe9\\n")
    out.write(b" ... drawbox           V->V       Draw a colored box on the input video.\\n")
    sys.stderr.write("filters listed\\n")
    sys.exit(3)
sys.stdout.write("fake ffmpeg " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(7 if "-fail" in sys.argv[1:] else 0)
"""


@unittest.skipIf(os.name == "nt", "o shim é POSIX")
class FfmpegPinTests(unittest.TestCase):
    """O ouro foi gravado com um ffmpeg sem o filtro `drawtext`: a rodada fixa um ffmpeg sem ele."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="gb-pin-")  # pylint: disable=consider-using-with
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name).resolve()
        (self.tmp / "host").mkdir()
        fake = self.tmp / "host" / "ffmpeg"
        fake.write_text(FAKE_FFMPEG.format(python=sys.executable), encoding="utf-8")
        fake.chmod(0o755)
        self.fake = fake
        self.enterContext(unittest.mock.patch.dict(os.environ, {"PATH": str(self.tmp / "host")}))

    def call(self, path, *args):
        return subprocess.run([path, *args], capture_output=True, check=False)

    def needs_runnable_shebang(self):
        # O ffmpeg falso e o shim usam o Python como shebang: com espaço no caminho dele não rodam.
        if _golden.pin_skipped_reason():
            self.skipTest(f"ffmpeg not pinned: {_golden.pin_skipped_reason()}")

    def test_the_pin_hides_drawtext_and_passes_everything_else_through(self):
        self.needs_runnable_shebang()
        pin = _golden.ffmpeg_pin(self.tmp)
        self.assertEqual(self.tmp / "pin" / "ffmpeg", Path(pin))
        listed = self.call(str(self.fake), "-hide_banner", "-filters")
        self.assertIn(b" drawtext ", listed.stdout)
        pinned = self.call(pin, "-hide_banner", "-filters")
        self.assertNotIn(b"drawtext", pinned.stdout)
        self.assertIn(b" blend ", pinned.stdout)
        self.assertIn(b" drawbox ", pinned.stdout)
        # A listagem passa em bytes: o que não é UTF-8 sai como entrou, e o código de saída é o do ffmpeg real.
        self.assertIn(b"Not utf-8: \xff\xfe caf\xe9\n", pinned.stdout)
        self.assertEqual(
            [row for row in listed.stdout.split(b"\n") if b" drawtext " not in row], pinned.stdout.split(b"\n")
        )
        self.assertEqual((b"filters listed\n", 3), (pinned.stderr, pinned.returncode))
        self.assertEqual((listed.stderr, listed.returncode), (pinned.stderr, pinned.returncode))
        version = self.call(pin, "-version", "-x")
        direct = self.call(str(self.fake), "-version", "-x")
        self.assertEqual((direct.stdout, direct.stderr, direct.returncode), (version.stdout, version.stderr, 0))
        self.assertEqual(b"fake ffmpeg -version -x\n", version.stdout)

    def test_the_pin_stays_out_of_the_run_path(self):
        self.needs_runnable_shebang()
        # `tmp/bin` é o PATH da rodada: o caso `doctor` (bare) segue sem achar ffmpeg ali.
        _golden.ffmpeg_pin(self.tmp)
        self.assertFalse((self.tmp / "bin" / "ffmpeg").exists())
        self.assertEqual("", _golden.hermetic_env(self.tmp, bare=True).get("GB_FFMPEG_PATH", ""))
        self.assertEqual(str(self.tmp / "pin" / "ffmpeg"), _golden.hermetic_env(self.tmp)["GB_FFMPEG_PATH"])

    def test_a_python_path_with_a_space_keeps_the_real_ffmpeg_and_says_why(self):
        with unittest.mock.patch.object(_golden.sys, "executable", "/some where/python"):
            self.assertEqual("espaço no caminho do Python", _golden.pin_skipped_reason())
            self.assertEqual(str(self.fake), _golden.ffmpeg_pin(self.tmp))
        self.assertFalse((self.tmp / "pin").exists())

    def test_without_ffmpeg_there_is_no_pin(self):
        with unittest.mock.patch.dict(os.environ, {"PATH": str(self.tmp / "none")}):
            self.assertEqual("", _golden.ffmpeg_pin(self.tmp))

    def test_the_golden_classes_skip_when_the_pin_cannot_apply(self):
        with (
            unittest.mock.patch.object(_golden.sys, "executable", "/a b/python"),
            self.assertRaises(unittest.SkipTest) as caught,
        ):
            skip_unless_pinned(self)
        self.assertEqual("ffmpeg not pinned: espaço no caminho do Python", str(caught.exception))

    def test_the_exec_shim_and_the_spawn_shim_say_the_same_thing(self):
        # O shim do Windows (espera o filho) é o mesmo texto, salvo a última linha: roda aqui contra o falso.
        for spawn in (False, True):
            with self.subTest(spawn=spawn):
                script = self.tmp / f"shim-{spawn}.py"
                script.write_text(_golden.shim_source(sys.executable, str(self.fake), spawn=spawn), encoding="utf-8")
                self.assertEqual(not spawn, script.read_text(encoding="utf-8").startswith("#!"))
                listed = self.call(sys.executable, str(script), "-hide_banner", "-filters")
                self.assertNotIn(b"drawtext", listed.stdout)
                self.assertIn(b" blend ", listed.stdout)
                self.assertIn(b"Not utf-8: \xff\xfe caf\xe9\n", listed.stdout)
                self.assertEqual((b"filters listed\n", 3), (listed.stderr, listed.returncode))
                version = self.call(sys.executable, str(script), "-version", "-x")
                self.assertEqual((b"fake ffmpeg -version -x\n", 0), (version.stdout, version.returncode))
                # O código de saída do ffmpeg real chega a quem chamou, e o que ele escreveu também.
                failed = self.call(sys.executable, str(script), "-fail", "a b")
                self.assertEqual((b"fake ffmpeg -fail a b\n", 7), (failed.stdout, failed.returncode))


@unittest.skipIf(os.name == "nt", "o ffmpeg falso é um script com shebang")
class WindowsPinTests(unittest.TestCase):
    """No Windows o pino é `ffmpeg.cmd` + `ffmpeg.py`. Aqui o ramo é simulado (`_windows`); o `.cmd` de verdade só
    roda no Windows (`RealCmdPinTests`)."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="gb-pin-nt-")  # pylint: disable=consider-using-with
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name).resolve()
        (self.tmp / "host").mkdir()
        self.fake = self.tmp / "host" / "ffmpeg"
        self.fake.write_text(FAKE_FFMPEG.format(python=sys.executable), encoding="utf-8")
        self.fake.chmod(0o755)
        self.enterContext(unittest.mock.patch.dict(os.environ, {"PATH": str(self.tmp / "host")}))
        self.enterContext(unittest.mock.patch.object(_golden, "_windows", return_value=True))

    def test_a_windows_run_pins_a_cmd_that_starts_the_python_shim(self):
        self.assertIsNone(_golden.pin_skipped_reason(self.tmp))
        pin = _golden.ffmpeg_pin(self.tmp)
        self.assertEqual(self.tmp / "pin" / "ffmpeg.cmd", Path(pin))
        self.assertEqual(
            f'@"{sys.executable}" "{self.tmp / "pin" / "ffmpeg.py"}" %*\r\n'.encode(),
            (self.tmp / "pin" / "ffmpeg.cmd").read_bytes(),
        )
        self.assertEqual(
            _golden.shim_source(sys.executable, str(self.fake), spawn=True),
            (self.tmp / "pin" / "ffmpeg.py").read_text(encoding="utf-8"),
        )
        self.assertFalse((self.tmp / "pin" / "ffmpeg").exists())
        self.assertEqual(pin, _golden.hermetic_env(self.tmp)["GB_FFMPEG_PATH"])
        self.assertEqual("", _golden.hermetic_env(self.tmp, bare=True).get("GB_FFMPEG_PATH", ""))

    def test_the_pin_files_are_written_once_per_run(self):
        _golden.ffmpeg_pin(self.tmp)
        before = {name: (self.tmp / "pin" / name).stat().st_mtime_ns for name in ("ffmpeg.cmd", "ffmpeg.py")}
        _golden.ffmpeg_pin(self.tmp)
        self.assertEqual(before, {name: (self.tmp / "pin" / name).stat().st_mtime_ns for name in before})

    def test_a_python_path_with_a_space_is_quoted_in_the_cmd(self):
        with unittest.mock.patch.object(_golden.sys, "executable", "C:\\Program Files\\Python\\python.exe"):
            self.assertIsNone(_golden.pin_skipped_reason(self.tmp))
            _golden.ffmpeg_pin(self.tmp)
        cmd = (self.tmp / "pin" / "ffmpeg.cmd").read_text(encoding="utf-8")
        self.assertTrue(cmd.startswith('@"C:\\Program Files\\Python\\python.exe" "'), cmd)

    def test_a_percent_in_a_path_keeps_the_real_ffmpeg_and_says_why(self):
        with unittest.mock.patch.object(_golden.sys, "executable", "C:\\100%\\python.exe"):
            self.assertIn("%", _golden.pin_skipped_reason(self.tmp) or "")
            self.assertEqual(str(self.fake), _golden.ffmpeg_pin(self.tmp))
            with self.assertRaises(unittest.SkipTest):
                skip_unless_pinned(self)
        self.assertIn("%", _golden.pin_skipped_reason(Path("C:/100%/run")) or "")
        self.assertFalse((self.tmp / "pin").exists())

    def test_the_log_target_of_the_cmd_pin_reads_as_ffmpeg(self):
        roots = _golden.Roots(Path("/t"), Path("/r"), {})
        norm = _golden.Normalizer(roots, "2.6.0", windows=True)
        self.assertEqual("event=op target=ffmpeg ms=<MS>", norm.text("event=op target=ffmpeg.cmd ms=7"))
        self.assertEqual(
            "target=ffmpeg.cmd", _golden.Normalizer(roots, "2.6.0", windows=False).text("target=ffmpeg.cmd")
        )
        self.assertEqual("target=ffmpeg ms=<MS>", norm.text("target=ffmpeg ms=7"))
        self.assertEqual("target=ffmpeg.cmdx", norm.text("target=ffmpeg.cmdx"))


@unittest.skipUnless(os.name == "nt", "o .cmd só roda no Windows")
class RealCmdPinTests(unittest.TestCase):
    """O pino de verdade no Windows: o `.cmd` é aberto pelo CreateProcess, passa os argumentos ao shim e devolve o
    código de saída. Só o CI do Windows exercita isto."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="gb-pin-cmd-")  # pylint: disable=consider-using-with
        self.addCleanup(folder.cleanup)
        self.tmp = Path(folder.name).resolve()
        host = self.tmp / "host"
        host.mkdir()
        body = FAKE_FFMPEG.split("\n", 1)[1]  # sem o shebang
        (host / "fake_ffmpeg.py").write_text(body, encoding="utf-8", newline="\n")
        (host / "ffmpeg.cmd").write_bytes(f'@"{sys.executable}" "{host / "fake_ffmpeg.py"}" %*\r\n'.encode())
        self.enterContext(unittest.mock.patch.dict(os.environ, {"PATH": str(host)}))

    def call(self, *args):
        return subprocess.run([_golden.ffmpeg_pin(self.tmp), *args], capture_output=True, check=False)

    def test_the_cmd_hides_drawtext_and_keeps_the_exit_code(self):
        listed = self.call("-hide_banner", "-filters")
        self.assertNotIn(b"drawtext", listed.stdout)
        self.assertIn(b" blend ", listed.stdout)
        self.assertEqual(3, listed.returncode)

    def test_the_cmd_passes_arguments_with_spaces_and_the_exit_code_through(self):
        failed = self.call("-fail", "dois nomes", "scale=1:1,pad=a:b")
        self.assertEqual(7, failed.returncode)
        self.assertEqual("fake ffmpeg -fail dois nomes scale=1:1,pad=a:b", failed.stdout.decode().strip())


class GoldenAgainstV250Tests(unittest.TestCase):
    """Só dados: a diferença entre a gravação da v2.5.0 e o ouro é exatamente a lista fechada."""

    def setUp(self):
        self.accepted = json.loads(_golden.ACCEPTED.read_text(encoding="utf-8"))
        self.found = _golden_diff.diff(_golden_diff.load(_golden.V250), _golden_diff.load(_golden.EXPECTED))

    def test_every_difference_has_a_rule_and_every_rule_a_difference(self):
        taken, loose, stale = _golden_diff.classify(self.found, self.accepted)
        self.assertEqual([], [(item["case"], item["where"], item["kind"]) for item in loose])
        self.assertEqual([], stale)
        self.assertEqual([], _golden_diff.drifted(taken, self.accepted))

    def test_every_rule_is_on_the_closed_list_dated_and_in_the_changelog(self):
        changelog = _golden_diff.CHANGELOG.read_text(encoding="utf-8")
        self.assertEqual([], _golden_diff.problems(self.accepted, changelog))

    def test_no_item_waits_for_ratification(self):
        # A regravação nova entra com `"ratificado": null` e `pendente`, até o mantenedor dar o ok: a suíte
        # comum diz quais (no motivo do pulo); com GB_GOLDEN_REQUIRE_RATIFIED=1 (antes de publicar), falha.
        waiting = _golden_diff.pending(self.accepted)
        if waiting and os.environ.get("GB_GOLDEN_REQUIRE_RATIFIED") != "1":
            self.skipTest("aguardando ratificação: " + ", ".join(waiting))
        self.assertEqual([], waiting)

    def test_both_recordings_cover_the_same_cases(self):
        self.assertEqual(sorted(_golden_diff.load(_golden.V250)), sorted(_golden_diff.load(_golden.EXPECTED)))


class ClosedListGuardTests(unittest.TestCase):
    """A lista fechada leva só o que foi ratificado: outra mudança nos mesmos lugares fica de fora."""

    @classmethod
    def setUpClass(cls):
        cls.accepted = json.loads(_golden.ACCEPTED.read_text(encoding="utf-8"))
        cls.v250 = _golden_diff.load(_golden.V250)
        cls.expected = _golden_diff.load(_golden.EXPECTED)

    def assert_caught(self, mutate):
        records = copy.deepcopy(self.expected)
        mutate(records)
        taken, loose, stale = _golden_diff.classify(_golden_diff.diff(self.v250, records), self.accepted)
        drifted = _golden_diff.drifted(taken, self.accepted)
        self.assertTrue(loose or stale or drifted, "a mudança passou como se estivesse ratificada")
        return taken

    @staticmethod
    def edit_diary(record, rel, change):
        line = json.loads(record["appends"][rel][0])
        change(line)
        record["appends"][rel][0] = json.dumps(line, ensure_ascii=False)

    def test_a_diagnostics_line_counts_only_its_recovered_field(self):
        def mutate(records):
            def change(line):
                line["state_committed"] = False
                line["warnings"].append({"code": "NOVO"})

            self.edit_diary(records["fluxo/verify"], "work/proj/brolls/diagnostics.jsonl", change)

        self.assert_caught(mutate)

    def test_recovered_true_in_a_diagnostics_line_is_not_the_ratified_false(self):
        def mutate(records):
            self.edit_diary(
                records["fluxo/approve"],
                "work/proj/brolls/diagnostics.jsonl",
                lambda line: line.update(recovered=True),
            )

        self.assert_caught(mutate)

    def test_status_layout_is_version_0_inferred_on_a_2_5_project(self):
        def mutate(records):
            records["layout0/l0_status"]["stdout"]["json"]["layout"] = {
                "version": 1,
                "source": "marker",
                "problem": None,
            }

        self.assert_caught(mutate)

    def test_a_layout_key_outside_status_is_not_ratified(self):
        def mutate(records):
            records["fluxo/deliver"]["stdout"]["json"]["layout"] = {
                "version": 0,
                "source": "inferred",
                "problem": None,
            }

        self.assert_caught(mutate)

    def test_a_usage_error_still_exits_with_2(self):
        self.assert_caught(lambda records: records["fluxo/err_bad_int"].update(exit=1))

    def test_a_usage_error_gains_no_traceback_nor_repr(self):
        def mutate(records):
            records["fluxo/err_bad_int"]["stderr"]["json"].update(traceback="<TRACEBACK>", repr="X()")

        self.assert_caught(mutate)

    def test_a_usage_error_gains_no_recovered(self):
        self.assert_caught(lambda records: records["fluxo/err_bad_int"]["stderr"]["json"].update(recovered=True))

    def test_another_change_in_a_ratified_manifest_is_caught(self):
        def mutate(records):
            lines = records["fluxo/verify"]["files"]["work/proj/brolls/manifest.json"]["lines"]
            index = lines.index('        "status": "approved",')
            lines[index] = '        "status": "rejected",'

        self.assert_caught(mutate)

    def test_other_bytes_in_a_ratified_review_page_are_caught(self):
        def mutate(records):
            records["fluxo/preview"]["files"]["work/proj/brolls/review.html"] = {"bytes": 1, "sha256": "0" * 64}

        self.assert_caught(mutate)

    def test_an_extra_line_in_a_ratified_log_is_caught(self):
        def mutate(records):
            records["fluxo/err_project_locked"]["appends"]["work/proj/brolls/getbrolls.log"].append("level=INFO x")

        self.assert_caught(mutate)

    def test_an_unrelated_brief_problem_is_caught(self):
        def mutate(records):
            records["fluxo/brief"]["stdout"]["json"]["summary"]["problems"].append("Outro problema.")

        self.assert_caught(mutate)

    def test_other_text_in_a_ratified_error_is_caught(self):
        def mutate(records):
            records["fluxo/err_library_locked"]["stderr"]["json"]["error"] = "Qualquer coisa."

        self.assert_caught(mutate)

    def test_another_key_in_references_json_is_caught(self):
        def mutate(records):
            records["fluxo/remember"]["files"]["work/proj/brolls/references.json"]["lines"].insert(2, '  "x": 1,')

        self.assert_caught(mutate)

    def test_the_diagnostics_rule_takes_only_recovered_leaves(self):
        records = copy.deepcopy(self.expected)
        taken, _, _ = _golden_diff.classify(_golden_diff.diff(self.v250, records), self.accepted)
        for item in taken["diagnostico-recovered"]:
            with self.subTest(case=item["case"], where=item["where"]):
                self.assertTrue(item["where"].endswith("/recovered"))
                self.assertEqual(("added", False), (item["kind"], item["new"]))


class SnapshotPlatformOnlyTests(unittest.TestCase):
    """A foto deixa de fora só a trava da rotação de um log de projeto (`work/**/brolls/`)."""

    def test_only_the_project_log_lock_is_left_out(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            kept = [
                "gbhome/logs/.getbrolls.log.lock",
                "work/proj/.getbrolls.log.lock",
                "userhome/brolls/.getbrolls.log.lock",
            ]
            skipped = ["work/proj/brolls/.getbrolls.log.lock", "work/a/b/brolls/.getbrolls.log.lock"]
            for rel in kept + skipped:
                (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                (tmp / rel).write_bytes(b"")
            self.assertEqual(sorted(kept), sorted(_golden.snapshot(tmp)))


class GoldenFixtureHygieneTests(unittest.TestCase):
    def test_fixtures_carry_no_machine_path(self):
        problems = []
        for path in sorted(_golden.FIXTURES.rglob("*")):
            if path.is_file() and path.suffix in {".json", ".jsonl", ".md", ".html"}:
                text = path.read_text(encoding="utf-8")
                if LOCAL_PATH_PATTERN.search(text) or "/opt/homebrew" in text or "gb-ouro-" in text:
                    problems.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], problems)

    def test_fixtures_carry_no_frontmatter_date_of_a_recording_day(self):
        # As datas fixas dos modelos (docs/BRIEF.md, docs/RULES.md) ficam; a do dia em que se gravou, não.
        days = set(RECORDING_DAY.findall(_golden.REWRITES.read_text(encoding="utf-8")))
        problems = []
        for folder in (_golden.EXPECTED, _golden.V250, _golden.LAYOUT0):
            for path in sorted(folder.rglob("*")) if folder.is_dir() else ():
                if not path.is_file() or path.suffix not in {".json", ".md"}:
                    continue
                if days & set(FRONTMATTER_DATE.findall(path.read_text(encoding="utf-8"))):
                    problems.append(path.relative_to(ROOT).as_posix())
        self.assertTrue(days)
        self.assertEqual([], problems)

    def test_layout0_fixture_is_dated_on_a_fixed_past_day(self):
        # O que a CLI gerou no projeto congelado é de um dia anterior a qualquer rodada, como no
        # projeto de quem vem da 2.5; as datas fixas dos modelos (BRIEF.md, RULES.md) ficam.
        proj = _golden.LAYOUT0 / "proj"
        generated = [proj / "brolls" / "credits.md", proj / "entrega" / "README.md", *proj.glob("entrega/*/ORIGEM.md")]
        dates = {path.name: set(FRONTMATTER_DATE.findall(path.read_text(encoding="utf-8"))) for path in generated}
        self.assertEqual({name: {_golden.FROZEN_DAY} for name in ("credits.md", "README.md", "ORIGEM.md")}, dates)
        leftovers = [
            path.relative_to(ROOT).as_posix()
            for path in sorted(_golden.LAYOUT0.rglob("*"))
            if path.is_file()
            and path.suffix in {".json", ".jsonl", ".md"}
            and _golden.TODAY in path.read_text(encoding="utf-8")
        ]
        self.assertEqual([], leftovers)

    def test_materialize_keeps_the_past_day_of_the_frozen_project(self):
        with tempfile.TemporaryDirectory(prefix="gb-ouro-") as raw:
            project = _golden.materialize_layout0(Path(raw))
            credits_md = (project / "proj" / "brolls" / "credits.md").read_text(encoding="utf-8")
        self.assertIn(f"created: {_golden.FROZEN_DAY}\nupdated: {_golden.FROZEN_DAY}\n", credits_md)

    def test_layout0_golden_covers_a_project_created_on_an_earlier_day(self):
        # O `review` reescreve o credits.md com a data da rodada; o `verify` mantém o `created:` antigo.
        review = json.loads(_expected("layout0", "l0_review"))["files"]
        verify = json.loads(_expected("layout0", "l0_verify"))["files"]
        credits_md = review["work/layout0/proj/brolls/credits.md"]["lines"]
        readme = verify["work/layout0/proj/entrega/README.md"]["lines"]
        self.assertEqual(["created: <TODAY>", "updated: <TODAY>"], credits_md[3:5])
        self.assertEqual([f"created: {_golden.FROZEN_DAY}", "updated: <TODAY>"], readme[3:5])

    def test_update_needs_a_one_line_reason(self):
        for env in ({}, {"GB_GOLDEN_REASON": "  "}, {"GB_GOLDEN_REASON": "a\nb"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                _golden.update_reason(env)
        self.assertEqual(
            "volta ao byte da v2.5.0", _golden.update_reason({"GB_GOLDEN_REASON": " volta ao byte da v2.5.0 "})
        )


class NormalizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-ouro-norm-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        roots = _golden.Roots(self.tmp, PurePosixPath("/repo/getbrolls"), {"<PY>": "/opt/py/bin/python3"})
        self.norm = _golden.Normalizer(roots, "2.6.0")

    def test_masks_what_varies_between_runs(self):
        raw = (
            f'{{"at": "2026-10-02T14:25:49.234925+00:00", "project": "{self.tmp}/work/proj", '
            '"id": "0b9c2f3e-1d2a-4c5b-9e8f-7a6b5c4d3e2f", "duration_ms": 15, '
            '"traceback": "Traceback (most recent call last):\\n  File \\"x.py\\", line 3"}'
        )
        expected = (
            '{"at": "<TS>", "project": "<T>/work/proj", "id": "<UUID:1>", '
            '"duration_ms": "<MS>", "traceback": "<TRACEBACK>"}'
        )
        self.assertEqual(expected, self.norm.text(raw))

    def test_masks_the_version_of_the_pinned_playwright_package_and_nothing_else(self):
        self.assertEqual(
            '["npx", "--package", "@playwright/cli", "x"]',
            self.norm.text('["npx", "--package", "@playwright/cli@0.1.21", "x"]'),
        )
        for kept in (
            '"@playwright/cli"',
            '"@playwright/cli@latest"',
            '"@playwright/other@0.1.21"',
            '"@playwright/cli@0.1"',
        ):
            with self.subTest(kept=kept):
                self.assertEqual(kept, self.norm.text(kept))

    def test_masks_the_date_of_the_run_and_the_next_day_but_not_other_dates(self):
        norm = _golden.Normalizer(self.norm.roots, "2.6.0", today="2026-10-31")
        raw = "created: 2026-10-31\nupdated: 2026-11-01\nat 2026-10-31T23:59:59Z\nfonte de 2026-10-30\n"
        self.assertEqual("created: <TODAY>\nupdated: <TODAY>\nat <TS>\nfonte de 2026-10-30\n", norm.text(raw))

    def test_freeze_writes_the_fixed_past_day_in_the_frontmatter_only(self):
        text = "---\ncreated: 2026-10-08\nupdated: 2026-10-08\n---\nGravado em 2026-10-08.\n"
        self.assertEqual(
            "---\ncreated: 2000-01-01\nupdated: 2000-01-01\n---\nGravado em 2026-10-08.\n",
            _golden.freeze_text(text, self.norm.roots, "2026-10-08"),
        )
        self.assertEqual(
            "created: 2025-03-04\n", _golden.freeze_text("created: 2025-03-04\n", self.norm.roots, "2026-10-08")
        )
        with self.assertRaises(ValueError):
            _golden.freeze_text("/repo/getbrolls/scripts/gb.py", self.norm.roots, "2026-10-08")

    def test_keeps_what_the_contract_promises(self):
        raw = '{"exit": 2, "error_code": "INVALID_DATA", "summary": {"line": "Registrei o candidato local:f6c9."}}'
        self.assertEqual(raw, self.norm.text(raw))

    def test_masks_versions_interpreter_and_log_stamps(self):
        self.assertEqual(
            "getbrolls <VERSION> (Python <PYV>; dados: checkout)",
            self.norm.text("getbrolls 2.6.0 (Python 3.11.15; dados: checkout)"),
        )
        self.assertEqual(
            '<PYNAME> "<CODE>/scripts/gb.py" status', self.norm.text('python "/repo/getbrolls/scripts/gb.py" status')
        )
        self.assertEqual(
            "level=INFO logger=getbrolls.cli event=command_end ms=<MS>",
            self.norm.text("2026-10-02T14:25:49.234Z level=INFO logger=getbrolls.cli event=command_end ms=41"),
        )

    def test_accented_root_in_any_json_form(self):
        roots = _golden.Roots(PurePosixPath("/t/João"), PurePosixPath("/r"), {})
        for raw in ('"/t/João/proj"', '"/t/Jo\\u00e3o/proj"'):
            with self.subTest(raw=raw):
                self.assertEqual('"<T>/proj"', roots.apply(raw))

    def test_hermetic_env_ignores_the_callers_environment(self):
        leaked = {"PEXELS_API_KEY": "k", "GB_PLUGINS": "demo", "GB_PROFILE": "/x/getbrolls.toml", "GB_HOME": "/real"}
        with unittest.mock.patch.dict(os.environ, leaked):
            env = _golden.hermetic_env(self.tmp)
        self.assertNotIn("PEXELS_API_KEY", env)
        self.assertNotIn("GB_PLUGINS", env)
        self.assertEqual("off", env["GB_PROFILE"])
        self.assertEqual(str(self.tmp / "gbhome"), env["GB_HOME"])
        self.assertEqual(str(self.tmp / "vazio.env"), env["GB_ENV_FILE"])
        self.assertEqual(b"", (self.tmp / "vazio.env").read_bytes())

    def test_hermetic_env_gives_a_closed_profile_temporary_folder_never_the_callers(self):
        with unittest.mock.patch.dict(os.environ, {"LOCALAPPDATA": "/real/AppData/Local"}):
            env = _golden.hermetic_env(self.tmp)
        local = self.tmp / "userhome" / "AppData" / "Local"
        self.assertEqual(str(local), env["LOCALAPPDATA"])
        self.assertTrue((local / "Temp").is_dir())
        self.assertEqual(env["USERPROFILE"], str(local.parent.parent))

    def test_hermetic_env_keeps_coverage_measurement_and_nothing_else(self):
        # Sem a variável o filho sai do gate de cobertura; com ela, só ela passa.
        clean = {key: value for key, value in os.environ.items() if key != "COVERAGE_PROCESS_START"}
        with unittest.mock.patch.dict(os.environ, clean, clear=True):
            self.assertNotIn("COVERAGE_PROCESS_START", _golden.hermetic_env(self.tmp))
        with unittest.mock.patch.dict(os.environ, {"COVERAGE_PROCESS_START": "/x/coveragerc", "COVERAGE_RCFILE": "/y"}):
            env = _golden.hermetic_env(self.tmp)
        self.assertEqual("/x/coveragerc", env["COVERAGE_PROCESS_START"])
        self.assertNotIn("COVERAGE_RCFILE", env)

    def test_windows_quoting_of_an_option_value_reads_as_the_posix_one(self):
        roots = _golden.Roots(Path("C:\\t"), Path("C:\\r"), {})
        norm = _golden.Normalizer(roots, "2.6.0", windows=True)
        for raw, expected in (
            # Texto corrido, como no stderr e nos arquivos: o caminho perde as aspas, o texto ganha as simples.
            ('run --project "C:\\t\\work\\proj" --shot x', "run --project <T>/work/proj --shot x"),
            ('q --query "Cole aqui a fala" --by Ana', "q --query 'Cole aqui a fala' --by Ana"),
            # Dentro de uma string JSON: aspas escapadas e barras dobradas.
            (
                'run --project \\"C:\\\\t\\\\work\\\\proj\\" --query \\"Cole aqui\\"',
                "run --project <T>/work/proj --query 'Cole aqui'",
            ),
            # O que o POSIX já escreve, as aspas de um valor que não segue uma opção e um apóstrofo ficam como estão.
            ("run --project <T>/work/proj --query 'Cole aqui'", "run --project <T>/work/proj --query 'Cole aqui'"),
            ('{"project": "C:\\t\\work\\proj"}', '{"project": "<T>/work/proj"}'),
            ('python "C:\\r\\scripts\\gb.py" status', '<PYNAME> "<CODE>/scripts/gb.py" status'),
            # Texto fixo da mensagem, igual nos dois sistemas, mesmo com espaço.
            ('--statement \\"FRASE EXATA DITA\\"', '--statement \\"FRASE EXATA DITA\\"'),
            ('q --query "it\'s here"', 'q --query "it\'s here"'),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(expected, norm.text(raw))

    def test_the_one_byte_windows_project_lock_is_the_empty_posix_one(self):
        for name, data, expected in (
            (".command.lock", b"\0", b""),
            (".command.lock", b"", b""),
            (".command.lock", b"\0\0", b"\0\0"),
            ("index.lock", b"\0", b"\0"),
        ):
            with self.subTest(name=name, data=data):
                path = self.tmp / name
                path.write_bytes(data)
                self.assertEqual(expected, _golden.file_bytes(path, windows=True))

    def test_on_posix_a_changed_quoting_is_not_normalised_away(self):
        # A normalização do Windows não vale aqui: o POSIX compara a grafia exata do comando sugerido.
        norm = _golden.Normalizer(_golden.Roots(PurePosixPath("/t"), PurePosixPath("/r"), {}), "2.6.0", windows=False)
        golden = "run --project <T>/work/proj --query 'Cole aqui' --by Ana"
        self.assertEqual(golden, norm.text("run --project /t/work/proj --query 'Cole aqui' --by Ana"))
        for regression in (
            "run --project \"/t/work/proj\" --query 'Cole aqui' --by Ana",
            'run --project /t/work/proj --query "Cole aqui" --by Ana',
            "run --project \\\"/t/work/proj\\\" --query 'Cole aqui' --by Ana",
        ):
            with self.subTest(raw=regression):
                self.assertNotEqual(golden, norm.text(regression))
                self.assertNotEqual(_golden.comparable(golden), _golden.comparable(norm.text(regression)))
        # Sem o argumento o padrão é o sistema que roda: no POSIX, igual a `windows=False`.
        if os.name != "nt":
            self.assertNotEqual(golden, _golden.Normalizer(norm.roots, "2.6.0").text(regression))

    def test_on_posix_a_changed_lock_byte_is_a_difference(self):
        path = self.tmp / ".command.lock"
        path.write_bytes(b"\0")
        self.assertEqual(b"\0", _golden.file_bytes(path, windows=False))
        if os.name != "nt":
            self.assertEqual(b"\0", _golden.file_bytes(path))
        # E no snapshot da rodada: a trava de 1 byte não vira a vazia do ouro.
        lock = self.tmp / "work" / "proj" / "brolls" / ".command.lock"
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b"\0")
        with unittest.mock.patch.object(_golden, "_windows", return_value=False):
            self.assertEqual(b"\0", _golden.snapshot(self.tmp)["work/proj/brolls/.command.lock"])
        with unittest.mock.patch.object(_golden, "_windows", return_value=True):
            self.assertEqual(b"", _golden.snapshot(self.tmp)["work/proj/brolls/.command.lock"])

    def test_windows_separators_after_a_root_become_slashes(self):
        self.assertEqual(
            '"<T>/work/proj"', _golden.Roots(Path("C:\\t"), Path("C:\\r"), {}).apply('"C:\\\\t\\\\work\\\\proj"')
        )


if __name__ == "__main__":
    unittest.main()
