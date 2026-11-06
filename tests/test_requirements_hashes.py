"""O pip da venv e o conjunto do runtime com hash: o pino do pip e o `setup` de verdade, sem rede.

`tests/test_setup_hashes.py` confere a forma do `requirements.txt`; aqui fica o `requirements-bootstrap.txt`
(o pip que o PyPI publica) e o `setup` real recusando, num índice local, o arquivo cujo sha256 não confere.
"""

import base64
import contextlib
import hashlib
import io
import os
import re
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import _paths, bootstrap

LINE = re.compile(r"([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.!+]+)(?: ; ([^\s].*?))?\s+((?:--hash=sha256:[0-9a-f]{64}\s*)+)")


def entries(name):
    """`{nome: (versão, marcador, [hashes])}`; uma linha fora do formato fixado com hashes derruba o teste."""
    text = (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = [line for line in text.replace("\\\n", " ").splitlines() if line.strip()]
    found: dict[str, tuple[str, str, list[str]]] = {}
    for line in (line for line in lines if not line.lstrip().startswith("#")):
        match = LINE.fullmatch(line.strip())
        assert match, f"{name}: linha fora do formato fixado com hashes: {line[:80]}"
        hashes = re.findall(r"[0-9a-f]{64}", match.group(4))
        found[match.group(1).lower()] = (match.group(2), match.group(3) or "", hashes)
    return found


class BootstrapPinTests(unittest.TestCase):
    def test_the_bootstrap_pins_the_pip_that_pypi_publishes(self):
        # sha256 de https://pypi.org/pypi/pip/26.2.2/json (roda e sdist), conferidos na adoção.
        self.assertEqual(
            {
                "pip": (
                    "26.2.2",
                    "",
                    [
                        "fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70",
                        "3a8e5c800492787f33a4114ef54444546c340df428974ebba17e0fd47e11af2f",
                    ],
                )
            },
            entries("requirements-bootstrap.txt"),
        )


def build_wheel(folder, project, modules, script=None):
    """Roda pura mínima `<project>-1.0-py3-none-any.whl` com um `__init__.py` por módulo e, se pedido, o
    executável `yt-dlp` (um console script que o pip gera) respondendo `script`; devolve o sha256 do arquivo."""
    dist = f"{project}-1.0.dist-info"
    files = {f"{module}/__init__.py": b"" for module in modules}
    if script is not None:
        files[f"{project}_cli.py"] = f"def main():\n    print({script!r})\n".encode()
        files[f"{dist}/entry_points.txt"] = f"[console_scripts]\nyt-dlp = {project}_cli:main\n".encode()
    files[f"{dist}/METADATA"] = f"Metadata-Version: 2.1\nName: {project}\nVersion: 1.0\n".encode()
    files[f"{dist}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    record = []
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(data)}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(record) + "\n").encode()
    wheel = Path(folder) / f"{project}-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return hashlib.sha256(wheel.read_bytes()).hexdigest()


@unittest.skipIf(
    os.name == "nt", "o executável de roda pura precisa do shebang reescrito; a CI do Windows instala de verdade"
)
class SetupHashEnforcementTests(unittest.TestCase):
    """`setup` de verdade (venv, pip e sondas reais), sem rede: um índice local no lugar do PyPI."""

    WRONG = "0" * 64
    # Sem índice nenhum alcançável: o pip procura no endereço que não responde.
    UNREACHABLE: ClassVar[dict] = {
        "PIP_NO_INDEX": None,
        "PIP_FIND_LINKS": None,
        "PIP_INDEX_URL": "http://127.0.0.1:9/simple",
        # Um retry: é o aviso dele ("Retrying ... NewConnectionError") que diz rede na saída do pip.
        "PIP_RETRIES": "1",
        "PIP_TIMEOUT": "3",
    }

    def setup_venv(self, bootstrap_hash=None, runtime_hash=None, runtime_line=None, **pip_env):
        """Roda `ensure_venv` com os dois arquivos de requisitos de um pacote falso; devolve (entrada, venv)."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        wheels = tmp / "wheels"
        wheels.mkdir()
        bootstrap_sha = build_wheel(wheels, "gbbootstrap", ["gbbootstrap"])
        runtime_sha = build_wheel(wheels, "gbruntime", ["yt_dlp", "yt_dlp_ejs"], script="2026.1.1")
        data = tmp / "site" / "getbrolls" / "_data"
        data.mkdir(parents=True)
        (data / "MANIFEST").write_text("", encoding="utf-8")
        (data / "requirements-bootstrap.txt").write_text(
            f"gbbootstrap==1.0 --hash=sha256:{bootstrap_hash or bootstrap_sha}\n", encoding="utf-8"
        )
        (data / "requirements.txt").write_text(
            runtime_line or f"gbruntime==1.0 --hash=sha256:{runtime_hash or runtime_sha}\n", encoding="utf-8"
        )
        env = {key: value for key, value in os.environ.items() if not key.startswith(("PIP_", "GB_"))}
        env.update(
            GB_HOME=str(tmp / "home"),
            PIP_NO_INDEX="1",
            PIP_FIND_LINKS=str(wheels),
            PIP_CONFIG_FILE=os.devnull,
            PIP_DISABLE_PIP_VERSION_CHECK="1",
        )
        # `None` tira a variável: o pip passa a procurar no índice de `PIP_INDEX_URL`.
        env.update({key: value for key, value in pip_env.items() if value is not None})
        for key in [key for key, value in pip_env.items() if value is None]:
            env.pop(key, None)
        with (
            patch.object(_paths, "install", return_value=_paths.detect(data.parent)),
            patch.dict(os.environ, env, clear=True),
            patch.dict(_paths._PROFILE_ENV, {}, clear=True),  # pylint: disable=protected-access
            contextlib.redirect_stderr(io.StringIO()),
        ):
            entry, _upgrade = bootstrap.ensure_venv()
        return entry, Path(entry["path"])

    def installed(self, venv):
        return sorted(path.name.split("-")[0] for path in venv.glob("lib/python*/site-packages/gb*.dist-info"))

    def test_matching_hashes_install_both_files(self):
        entry, venv = self.setup_venv()
        self.assertEqual("installed", entry["status"], entry)
        self.assertEqual("2026.1.1", entry["version"])
        self.assertEqual(["gbbootstrap", "gbruntime"], self.installed(venv))

    def test_a_wrong_bootstrap_hash_stops_before_the_requirements(self):
        entry, venv = self.setup_venv(bootstrap_hash=self.WRONG)
        self.assertEqual("failed", entry["status"], entry)
        self.assertEqual(bootstrap.PIP_HASH_FAILED.format(file="requirements-bootstrap.txt"), entry["error"])
        self.assertIn("DO NOT MATCH THE HASHES", "\n".join(entry["output_tail"]))
        # A venv que falhou não fica: nada de lá dentro chega a ser usado.
        self.assertFalse(venv.exists())

    def test_a_wrong_requirements_hash_fails_the_venv(self):
        entry, venv = self.setup_venv(runtime_hash=self.WRONG)
        self.assertEqual("failed", entry["status"], entry)
        self.assertEqual(bootstrap.PIP_HASH_FAILED.format(file="requirements.txt"), entry["error"])
        self.assertFalse(venv.exists())

    def test_a_requirement_without_a_hash_is_refused(self):
        # Sem nenhum `--hash` no arquivo o pip não liga o modo de hash sozinho: só a opção do `setup` recusa.
        entry, _venv = self.setup_venv(runtime_line="gbruntime==1.0\n")
        self.assertEqual("failed", entry["status"], entry)
        self.assertIn("Hashes are required in --require-hashes mode", "\n".join(entry["output_tail"]))
        self.assertEqual(bootstrap.PIP_HASH_FAILED.format(file="requirements.txt"), entry["error"])

    def test_an_unreachable_index_at_the_bootstrap_says_network(self):
        entry, _venv = self.setup_venv(**self.UNREACHABLE)
        self.assertEqual("failed", entry["status"], entry)
        self.assertEqual(bootstrap.PIP_NETWORK_FAILED, entry["error"], entry["output_tail"])

    def test_no_distribution_without_network_errors_is_not_called_network(self):
        entry, _venv = self.setup_venv(runtime_line=f"gbausente==1.0 --hash=sha256:{'1' * 64}\n")
        self.assertEqual("failed", entry["status"], entry)
        self.assertNotEqual(bootstrap.PIP_NETWORK_FAILED, entry["error"])
        self.assertNotEqual(bootstrap.PIP_HASH_FAILED.format(file="requirements.txt"), entry["error"])


if __name__ == "__main__":
    unittest.main()
