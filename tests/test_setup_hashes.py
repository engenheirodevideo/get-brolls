"""O `requirements.txt` que o `setup` instala com `--require-hashes --only-binary :all:`: forma, fonte e cobertura."""

import re
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT


class HashedRequirementsTests(unittest.TestCase):
    """O `requirements.txt` que o `setup` instala: cada linha com versão exata e hashes."""

    # Pacotes com extensão compilada: uma trava feita numa máquina só listaria a roda dela.
    COMPILED = ("brotli", "charset-normalizer", "pycryptodomex", "websockets")
    RUNTIME = (
        "yt-dlp",
        "yt-dlp-ejs",
        "brotli",
        "certifi",
        "charset-normalizer",
        "idna",
        "mutagen",
        "pycryptodomex",
        "requests",
        "urllib3",
        "websockets",
    )
    LINE = re.compile(
        r"([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.!+]+)(?: ; ([^\s].*?))?\s+((?:--hash=sha256:[0-9a-f]{64}\s*)+)"
    )

    @classmethod
    def logical_lines(cls):
        """As linhas de requisito do arquivo, com as continuações (`\\`) juntas e os comentários de fora."""
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8").replace("\r\n", "\n")
        lines = [line for line in text.replace("\\\n", " ").splitlines() if line.strip()]
        return [line for line in lines if not line.lstrip().startswith("#")]

    @classmethod
    def entries(cls):
        """`{nome: (versão, marcador, [hashes])}`; uma linha fora do formato fixado com hashes derruba o teste."""
        found: dict[str, tuple[str, str, list[str]]] = {}
        for line in cls.logical_lines():
            match = cls.LINE.fullmatch(line.strip())
            assert match, f"linha fora do formato fixado com hashes: {line[:80]}"
            hashes = re.findall(r"[0-9a-f]{64}", match.group(4))
            found[match.group(1).lower()] = (match.group(2), match.group(3) or "", hashes)
        return found

    def test_every_requirement_is_pinned_and_carries_hashes(self):
        entries = self.entries()
        self.assertEqual(len(self.logical_lines()), len(entries))
        for name, (version, _marker, hashes) in entries.items():
            self.assertTrue(version, name)
            self.assertTrue(hashes, f"{name} sem hash: o pip recusa o arquivo inteiro com --require-hashes")
            self.assertEqual(len(hashes), len(set(hashes)), f"{name} repete hash")

    def test_the_file_holds_no_option_that_widens_the_install(self):
        for line in self.logical_lines():
            self.assertFalse(line.lstrip().startswith("-"), f"opção no arquivo: {line[:60]}")
            self.assertNotIn("://", line)
            self.assertNotIn(" @ ", line, "pino por URL ou VCS fica fora")

    def test_the_runtime_set_is_all_there(self):
        entries = self.entries()
        for name in self.RUNTIME:
            self.assertIn(name, entries)

    def test_requirements_in_is_the_source_of_the_top_level_pins(self):
        # `requirements.in` é o que o comando do cabeçalho compila: os pinos de lá são os do arquivo com hashes.
        text = (ROOT / "requirements.in").read_text(encoding="utf-8")
        pins = {}
        for line in text.splitlines():
            if line.strip() and not line.startswith("#"):
                match = re.fullmatch(r"([A-Za-z0-9_.\-]+)(?:\[[a-z,]+\])?==([A-Za-z0-9_.!+]+)", line.strip())
                assert match, f"linha fora do formato de pino exato: {line}"
                pins[match.group(1).lower()] = match.group(2)
        entries = self.entries()
        self.assertEqual(set(self.RUNTIME), set(pins))
        for name, version in pins.items():
            self.assertEqual(version, entries[name][0], name)
        header = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn(
            "uv pip compile --universal --generate-hashes --python-version 3.11 requirements.in -o requirements.txt",
            header,
        )
        self.assertIn("--only-binary :all:", header)
        # A receita do CONTRIBUTING guarda o cabeçalho escrito à mão, que o `uv` descarta ao regenerar.
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn(
            "uv pip compile --universal --generate-hashes --python-version 3.11 requirements.in", contributing
        )
        self.assertIn("header.tmp", contributing)

    def test_compiled_packages_list_the_wheels_of_every_platform(self):
        entries = self.entries()
        for name in self.COMPILED:
            self.assertGreaterEqual(len(entries[name][2]), 10, f"{name}: poucas rodas para Linux, macOS e Windows")

    def test_only_pypy_dependencies_carry_a_marker(self):
        markers = {name: marker for name, (_version, marker, _hashes) in self.entries().items() if marker}
        self.assertEqual({"brotlicffi", "cffi", "pycparser"}, set(markers))
        for marker in markers.values():
            self.assertIn("implementation_name", marker)


if __name__ == "__main__":
    unittest.main()
