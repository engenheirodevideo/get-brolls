"""Portabilidade para o job Windows do CI (I3 da revisão final).

O Git para Windows grava objetos como somente-leitura, e `shutil.rmtree(...,
ignore_errors=True)` deixa esses arquivos para trás em silêncio — um `.git` ficava
dentro do plugin instalado e `.install-*` se acumulavam. `force_rmtree` limpa o bit
de somente-leitura e tenta de novo; aqui o mesmo efeito é reproduzido também em
POSIX com uma pasta sem permissão de escrita.
"""

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from test_sdk_install import HAS_GIT, InstallTestCase, git, write_plugin

from getbrolls import runtime
from getbrolls.sdk import install as install_mod

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _read_only_tree(base):
    """Pasta com um arquivo somente-leitura dentro de uma subpasta sem escrita — o
    equivalente POSIX (e Windows, pelo atributo do arquivo) de um objeto git."""
    sub = base / "objects" / "ab"
    sub.mkdir(parents=True)
    obj = sub / "cdef"
    obj.write_bytes(b"blob")
    obj.chmod(stat.S_IREAD)
    sub.chmod(stat.S_IREAD | stat.S_IEXEC)
    return sub


class ForceRmtreeTests(unittest.TestCase):
    @unittest.skipIf(IS_ROOT, "root ignora permissão de escrita")
    def test_read_only_entries_are_removed(self):
        base = Path(tempfile.mkdtemp(prefix="gb-ro-"))
        sub = _read_only_tree(base)
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.addCleanup(lambda: sub.exists() and sub.chmod(stat.S_IRWXU))
        runtime.force_rmtree(base)
        self.assertFalse(base.exists())

    def test_missing_folder_is_fine(self):
        runtime.force_rmtree(Path(tempfile.gettempdir()) / "gb-nao-existe-mesmo-xyz")


@unittest.skipUnless(HAS_GIT, "git required")
class GitCleanupTests(InstallTestCase):
    def repo(self):
        folder = write_plugin(self.work / "demo_repo")
        git(folder, "init", "--quiet")
        git(folder, "add", ".")
        git(folder, "commit", "--quiet", "-m", "v0.1.0")
        return folder

    def test_install_cleans_clone_and_staging_with_the_forced_removal(self):
        repo = self.repo()
        removed = []
        real = runtime.force_rmtree

        def spy(path):
            removed.append(Path(path).name)
            return real(path)

        with patch.object(install_mod, "force_rmtree", side_effect=spy):
            preview = install_mod.install(str(repo), confirm=False)
            install_mod.install(str(repo), confirm=True, expect=preview["plugin"]["sha256"])
        self.assertTrue(all(name.startswith(".install-") for name in removed), removed)
        self.assertGreaterEqual(len(removed), 4)  # clone + staging, em cada uma das duas chamadas
        self.assertEqual([], self.leftover_staging())
        self.assertFalse((self.home / "plugins" / "demo" / ".git").exists())

    def test_git_config_written_by_tests_uses_forward_slashes(self):
        # I3(b): `.gitconfig` com caminho do Windows (`C:\\Users\\...`) quebra o parser
        # do git ("bad config line"); o teste do smudge grava o caminho em forma POSIX.
        source = Path(__file__).with_name("test_sdk_install.py").read_text(encoding="utf-8")
        self.assertIn('touch "{marker.as_posix()}"', source)


if __name__ == "__main__":
    unittest.main()
