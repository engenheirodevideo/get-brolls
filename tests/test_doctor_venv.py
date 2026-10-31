"""O doctor confere a `.venv` que o runtime usa contra os pinos fixados (F01 do QA da 2.5.1)."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV, ROOT, WHEEL_MODE

from getbrolls import _paths as gb_paths
from getbrolls import cli, runtime_parts, venv_check
from getbrolls.config import PATH_KEYS
from getbrolls.errors import DataRootError

# O `requirements.txt` da 2.5.0: a venv de quem atualizou o código sem rodar o instalador de novo.
PINS_2_5_0 = {
    "yt-dlp": "2026.8.19",
    "yt-dlp-ejs": "0.8.0",
    "brotli": "1.2.0",
    "certifi": "2026.7.22",
    "charset-normalizer": "3.5.1",
    "idna": "3.19",
    "mutagen": "1.48.1",
    "pycryptodomex": "3.23.0",
    "requests": "2.34.2",
    "urllib3": "2.8.0",
    "websockets": "17.1",
    "pip": "24.0",
}


def make_venv(folder, versions, ytdlp=True):
    """Venv real sem pip, com um `.dist-info` mínimo por pacote: o que o `importlib.metadata` dela enxerga."""
    base = Path(folder).resolve() / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(base)], check=True, timeout=120)
    python = venv_check.venv_python(base)
    assert python is not None
    purelib = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    for name, version in versions.items():
        dist = Path(purelib) / f"{name.replace('-', '_')}-{version}.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    if ytdlp:
        scripts = base / ("Scripts" if os.name == "nt" else "bin")
        stub = scripts / "yt-dlp"
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return base


class VenvReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.pins = venv_check.pinned_versions(ROOT)

    def test_the_pins_come_from_both_requirements_files(self):
        self.assertEqual("2026.10.23", self.pins["certifi"])
        self.assertEqual("26.2.2", self.pins["pip"])
        for pypy_only in ("brotlicffi", "cffi", "pycparser"):
            self.assertNotIn(pypy_only, self.pins, "dependência só do PyPy não é cobrada no CPython")

    def test_a_venv_that_matches_the_pins_is_ok(self):
        base = make_venv(self.tmp, self.pins)
        report = venv_check.venv_report(base, ROOT)
        self.assertEqual("ok", report["status"], report)
        self.assertEqual([], report["differences"])
        self.assertEqual([], venv_check.missing_entries(report, "INSTALLER"))

    def test_a_2_5_0_venv_is_stale_and_names_the_emsign_root(self):
        base = make_venv(self.tmp, PINS_2_5_0)
        report = venv_check.venv_report(base, ROOT)
        self.assertEqual("stale", report["status"])
        self.assertIn("certifi 2026.7.22 (fixado 2026.10.23)", report["differences"])
        self.assertIn("pip 24.0 (fixado 26.2.2)", report["differences"])
        [entry] = venv_check.missing_entries(report, "INSTALLER")
        self.assertEqual("venv", entry["item"])
        self.assertEqual("INSTALLER", entry["fix"])
        self.assertIn("emSign ECC Root CA - G3", entry["note"])
        self.assertIn("GHSA-6q48-6c7w-9gw2", entry["note"])

    def test_versions_compare_without_leading_zeros(self):
        # O certifi publica `2026.07.22` no __version__ e `2026.7.22` no METADATA: é a mesma versão.
        pins = {**self.pins, "certifi": "2026.10.23"}
        base = make_venv(self.tmp, {**pins, "certifi": "2026.010.023"})
        self.assertEqual("ok", venv_check.venv_report(base, ROOT)["status"])

    def test_a_half_built_venv_lists_what_is_absent(self):
        base = make_venv(self.tmp, {"pip": self.pins["pip"]}, ytdlp=False)
        report = venv_check.venv_report(base, ROOT)
        self.assertEqual("stale", report["status"])
        self.assertIn("certifi ausente (fixado 2026.10.23)", report["differences"])
        self.assertIn("yt-dlp ausente (fixado 2026.10.21)", report["differences"])

    def test_no_venv_at_all_is_reported_but_left_to_the_ytdlp_entry(self):
        # Sem venv nenhuma o yt-dlp vem do PATH (que vale) ou falta, e aí o item `yt-dlp` já leva o conserto.
        report = venv_check.venv_report(self.tmp / ".venv", ROOT)
        self.assertEqual("absent", report["status"])
        self.assertEqual([], venv_check.missing_entries(report, "INSTALLER"))

    @unittest.skipIf(os.name == "nt", "stub de shell")
    def test_a_venv_whose_python_no_longer_runs_is_broken(self):
        # Ex.: o Python do Homebrew foi atualizado e o link da venv aponta para um interpretador que sumiu.
        base = self.tmp / ".venv"
        (base / "bin").mkdir(parents=True)
        python = base / "bin" / "python"
        python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        python.chmod(0o755)
        report = venv_check.venv_report(base, ROOT)
        self.assertEqual("broken", report["status"])
        [entry] = venv_check.missing_entries(report, "INSTALLER")
        self.assertIn("não roda", entry["note"])

    def test_two_versions_of_one_package_are_stale_whatever_the_listing_order(self):
        # Dois `.dist-info` do certifi: qual o `import` acha depende da ordem do disco; o doctor não aposta.
        base = make_venv(self.tmp, self.pins)
        purelib = next(base.glob("lib/python*/site-packages")) if os.name != "nt" else base / "Lib" / "site-packages"
        old = purelib / "certifi-2026.7.22.dist-info"
        old.mkdir()
        (old / "METADATA").write_text("Metadata-Version: 2.1\nName: certifi\nVersion: 2026.7.22\n", encoding="utf-8")
        report = venv_check.venv_report(base, ROOT)
        self.assertEqual("stale", report["status"])
        self.assertIn("certifi duplicado (2026.10.23, 2026.7.22)", report["differences"])
        self.assertTrue(report["certifi_distrusted_root"])

    @unittest.skipIf(os.name == "nt", "symlink")
    def test_a_dangling_python_symlink_is_broken_not_absent(self):
        # O caso do Homebrew: a venv aponta para um Python que o upgrade removeu.
        base = self.tmp / ".venv"
        (base / "bin").mkdir(parents=True)
        (base / "bin" / "python").symlink_to(self.tmp / "python-que-sumiu")
        report = venv_check.venv_report(base, ROOT)
        self.assertEqual("broken", report["status"])

    @unittest.skipIf(os.name == "nt", "stub de shell")
    def test_a_python_that_prints_something_else_is_broken(self):
        base = self.tmp / ".venv"
        (base / "bin").mkdir(parents=True)
        python = base / "bin" / "python"
        python.write_text("#!/bin/sh\necho '[1, 2]'\necho detalhe >&2\n", encoding="utf-8")
        python.chmod(0o755)
        report = venv_check.venv_report(base, ROOT)
        self.assertEqual("broken", report["status"])

    def test_an_invalid_ytdlp_pin_skips_the_venv_instead_of_raising(self):
        with mock.patch.dict(os.environ, {"GB_YTDLP_PATH": str(self.tmp / "gb-inexistente-yt-dlp")}):
            report = venv_check.venv_report()
        self.assertEqual("skipped", report["status"])


class DoctorVenvTests(unittest.TestCase):
    """O `doctor` de verdade, pela CLI: a venv do runtime (ou a de GB_VENV_PATH) contra os pinos."""

    def doctor(self, tmp, venv, path_ytdlp=True, argv=CLI_ARGV, **extra):
        empty = Path(tmp) / "vazio.env"
        empty.write_text("", encoding="utf-8")
        decoy = Path(tmp) / "path-bin"
        decoy.mkdir()
        if path_ytdlp:
            # Um yt-dlp fora da venv no PATH (pipx/uv/brew): o runtime não o usa quando a venv tem o dela.
            stub = decoy / "yt-dlp"
            stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in PATH_KEYS}
        env.update(PATH=str(decoy) + os.pathsep + env.get("PATH", ""))
        env.pop("GB_VENV_PATH", None)
        env.pop("GB_RUNTIME_DIR", None)
        # Uma pasta pessoal só deste teste: o runtime compartilhado dela começa vazio.
        env["GB_HOME"] = str(Path(tmp).resolve() / "gbhome")
        if venv is not None:
            env["GB_VENV_PATH"] = str(venv)
        env.update(extra)
        done = subprocess.run(
            [*argv, "--env-file", str(empty), "doctor"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
            check=False,
        )
        payload = json.loads(done.stdout)
        # `doctor` sai 4 quando falta item obrigatório (`summary.missing`), 0 quando não falta nada.
        self.assertEqual(4 if payload["summary"]["missing"] else 0, done.returncode, done.stderr)
        return payload, decoy

    def test_code_of_2_5_1_on_a_venv_of_2_5_0_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = make_venv(tmp, PINS_2_5_0)
            payload, _decoy = self.doctor(tmp, venv)
            entries = [e for e in payload["summary"]["missing"] if e["item"] == "venv"]
            self.assertEqual(1, len(entries), payload["summary"])
            self.assertIn("certifi 2026.7.22 (fixado 2026.10.23)", entries[0]["note"])
            # A venv é a de GB_VENV_PATH: o `setup` não mexe nela, então o conserto diz o que fazer com ela.
            self.assertIn("GB_VENV_PATH", entries[0]["fix"])
            self.assertIn("setup", entries[0]["fix"])
            self.assertEqual(entries[0]["fix"], payload["summary"]["do"])
            self.assertEqual("stale", payload["venv"]["status"])
            self.assertEqual("GB_VENV_PATH", payload["venv"]["source"])
            self.assertNotIn("venv", payload["summary"]["ok"])
            self.assertFalse(payload["ready"])

    def test_a_stale_managed_runtime_is_flagged_with_the_setup_fix(self):
        # A venv gerenciada em $GB_HOME/runtime/<sha>/ pronta (marcador `ready`) mas com versões antigas.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "gbhome" / "runtime" / str(gb_paths.part_sha("venv"))
            venv = make_venv(root, PINS_2_5_0)
            runtime_parts.write_marker("venv", root, "ready")
            payload, _decoy = self.doctor(tmp, None)
            self.assertEqual(str(venv), payload["venv"]["path"])
            self.assertEqual("gb_home", payload["venv"]["source"])
            self.assertEqual("stale", payload["venv"]["status"])
            [entry] = [e for e in payload["summary"]["missing"] if e["item"] == "venv"]
            self.assertIn("certifi 2026.7.22 (fixado 2026.10.23)", entry["note"])
            self.assertIn(str(ROOT / "scripts" / "install.sh"), entry["fix"])
            self.assertIn(str(ROOT / "scripts" / "install.sh"), payload["summary"]["do"])

    @unittest.skipIf(os.name == "nt", "stub de shell no PATH")
    def test_resolved_ytdlp_is_the_one_the_runtime_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = make_venv(tmp, venv_check.pinned_versions(ROOT))
            payload, decoy = self.doctor(tmp, venv)
            self.assertEqual(str((venv / "bin" / "yt-dlp").resolve()), payload["resolved"]["yt-dlp"])
            self.assertNotEqual(str((decoy / "yt-dlp").resolve()), payload["resolved"]["yt-dlp"])

    def test_a_matching_venv_adds_nothing_to_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = make_venv(tmp, venv_check.pinned_versions(ROOT))
            payload, _decoy = self.doctor(tmp, venv)
            self.assertEqual("ok", payload["venv"]["status"])
            self.assertNotIn("venv", {e["item"] for e in payload["summary"]["missing"]})
            self.assertIn("venv", payload["summary"]["ok"])

    @unittest.skipIf(os.name == "nt", "stub de shell no PATH")
    @unittest.skipIf(WHEEL_MODE, "a `.venv` antiga é a de um checkout")
    def test_a_checkout_venv_without_ytdlp_falls_back_to_path_and_is_still_flagged(self):
        # Variante do F01: sem runtime compartilhado pronto, a `.venv` antiga do checkout vale; pela metade, o
        # runtime cai no yt-dlp do PATH (com outro certifi). Uma cópia da skill numa raiz temporária.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "skill"
            shutil.copytree(ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            for name in ("requirements.txt", "requirements-bootstrap.txt", "docs/RULES.md", "assets/brand-logo.png"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / name, root / name)
            venv = make_venv(root, {"pip": "26.2.2"}, ytdlp=False)
            payload, decoy = self.doctor(tmp, None, argv=(sys.executable, str(root / "scripts" / "gb.py")))
            self.assertEqual(str((decoy / "yt-dlp").resolve()), payload["resolved"]["yt-dlp"])
            self.assertEqual(str(venv), payload["venv"]["path"])
            self.assertEqual("checkout", payload["venv"]["source"])
            self.assertIn("venv", {e["item"] for e in payload["summary"]["missing"]})

    def test_an_invalid_ytdlp_pin_is_a_missing_entry_not_a_crash(self):
        # Regressão achada na revisão adversarial da 2.5.2: na 2.5.1 isto já era um item de `missing`.
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp).resolve() / "gb-inexistente-yt-dlp")
            payload, _decoy = self.doctor(tmp, None, GB_YTDLP_PATH=missing)
            self.assertIn("GB_YTDLP_PATH", {e["item"] for e in payload["summary"]["missing"]})
            # Sem venv em uso o `doctor` não ganha o bloco `venv` (o ouro da 2.5 fixa o yt-dlp e segue igual).
            self.assertNotIn("venv", payload)
            self.assertNotIn("venv", {e["item"] for e in payload["summary"]["missing"]})

    def test_doctor_without_the_install_data_still_answers_with_an_explicit_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = make_venv(tmp, PINS_2_5_0)
            env = {k: v for k, v in os.environ.items() if k not in PATH_KEYS}
            env.update(GB_VENV_PATH=str(venv), GB_HOME=str(Path(tmp).resolve() / "gbhome"))
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(gb_paths, "data_root", side_effect=DataRootError("sem dados")),
                mock.patch.object(gb_paths, "data_inventory", return_value=(["docs/RULES.md"], [])),
            ):
                result = cli.main(["doctor"])
            self.assertIs(False, result["ready"])
            self.assertEqual(4, cli.result_exit("doctor", result))
            self.assertIn("dados da instalação", {e["item"] for e in result["summary"]["missing"]})
            self.assertNotIn("venv", {e["item"] for e in result["summary"]["missing"]})
            self.assertEqual("skipped", result["venv"]["status"])
            self.assertEqual("install_data_missing", result["venv"]["reason"])
            self.assertEqual(str(venv), result["venv"]["path"])


if __name__ == "__main__":
    unittest.main()
