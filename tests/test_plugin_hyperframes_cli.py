"""O projeto gerado passa no `lint` (e, com rede, no `check`) da CLI HyperFrames de verdade.

A CLI é opcional: `GB_HYPERFRAMES_CLI` (caminho do binário) ou `hyperframes` no PATH.
Sem ela, com `--version` falhando ou com `lint --json` que não devolve JSON (um
wrapper quebrado), os testes pulam dizendo o motivo — nunca falham por isso. O
`check` baixa GSAP e a fonte Inter na primeira vez: só roda com `GB_EVAL_NETWORK=1`.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import warnings
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _media import synth_image, synth_video
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_plugin_hyperframes import fixture, hf

ENV = {**os.environ, "HYPERFRAMES_NO_TELEMETRY": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1", "DO_NOT_TRACK": "1"}


_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def find_cli():
    """(binário, versão) da CLI HyperFrames que responde a `--version`, ou (None, motivo do skip).

    Aceita só a versão no formato semver puro (`^\\d+\\.\\d+\\.\\d+$`): outro
    programa qualquer chamado `hyperframes` no PATH (visto numa máquina real:
    "OpenAI SDK: 2.24.0" na última linha) tem que pular com o motivo, nunca ser
    lido como se fosse a CLI real.
    """
    binary = os.environ.get("GB_HYPERFRAMES_CLI") or shutil.which("hyperframes")
    if not binary:
        return None, "CLI HyperFrames ausente: defina GB_HYPERFRAMES_CLI ou ponha hyperframes no PATH"
    try:
        done = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, encoding="utf-8", timeout=60, env=ENV, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"CLI HyperFrames em {binary} não roda ({type(exc).__name__})"
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        return None, f"CLI HyperFrames em {binary}: --version saiu com {done.returncode}"
    version = lines[-1].strip()
    if not _VERSION_RE.match(version):
        return None, f"CLI HyperFrames em {binary}: --version não devolveu semver puro ({version!r})"
    return binary, version


def synth_audio_file(path, seconds=2):
    """Áudio sintético no formato da extensão (.wav em PCM, .mp3 em MP3, resto em AAC)."""
    codec = {".wav": "pcm_s16le", ".mp3": "libmp3lame"}.get(path.suffix, "aac")
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
    )


def write_project(plan, folder):
    """Grava o que o exporter devolveu e uma mídia sintética em cada destino pedido."""
    result = hf.generate(plan)
    for relative, text in result["files"].items():
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for request in result["media"]:
        target = folder / request["dest"]
        target.parent.mkdir(parents=True, exist_ok=True)
        kind = plan["media"][request["media_id"]]["kind"]
        if kind == "audio":
            synth_audio_file(target)
        elif kind == "image":
            synth_image(target)
        else:
            synth_video(target, size="320x568", duration=6)
    return result


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required")
class HyperframesCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary, cls.version = find_cli()
        if cls.binary is None:
            raise unittest.SkipTest(cls.version)
        if cls.version != hf.HYPERFRAMES_VERSION:
            warnings.warn(
                f"CLI HyperFrames {cls.version} difere da versão testada {hf.HYPERFRAMES_VERSION}", stacklevel=2
            )

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-hf-cli-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_cli(self, *args, timeout=180):
        assert self.binary is not None
        done = subprocess.run(
            [self.binary, *args, "--json"],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=ENV, check=False,
        )  # fmt: skip
        try:
            return done.returncode, json.loads(done.stdout)
        except json.JSONDecodeError:
            self.skipTest(f"CLI HyperFrames {self.version} não devolveu JSON em {args[0]} (wrapper quebrado?)")
            raise  # skipTest já levantou; o raise só fecha o caminho para o pyright

    def lint(self, name, aspect=None):
        plan = fixture(name)
        if aspect:
            plan["meta"]["aspecto"] = aspect
        project = self.root / f"{Path(name).stem}-{(aspect or plan['meta']['aspecto']).replace(':', 'x')}"
        write_project(plan, project)
        code, report = self.run_cli("lint", str(project))
        findings = [(f.get("severity"), f.get("code")) for f in report.get("findings", [])]
        self.assertEqual((0, 0), (code, report.get("errorCount")), f"CLI {self.version}: {findings}")
        return project

    def test_min_fixture_lints_clean(self):
        self.lint("export_plan_min.json")

    def test_full_fixture_lints_clean_in_both_aspects(self):
        for aspect in ("9:16", "16:9"):
            with self.subTest(aspect=aspect):
                self.lint("export_plan_full.json", aspect)

    @unittest.skipUnless(os.environ.get("GB_EVAL_NETWORK") == "1", "rede: defina GB_EVAL_NETWORK=1 (GSAP e Inter)")
    def test_full_fixture_passes_check(self):
        project = self.lint("export_plan_full.json")
        code, report = self.run_cli("check", str(project), timeout=600)
        sections = {
            key: (report.get(key) or {}).get("ok") for key in ("lint", "runtime", "layout", "motion", "contrast")
        }
        self.assertEqual((0, True), (code, report.get("ok")), f"CLI {self.version}: {sections}")


if __name__ == "__main__":
    unittest.main()
