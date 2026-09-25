"""Portabilidade para o job Windows do CI.

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

    @unittest.skipIf(IS_ROOT, "root ignora permissão de escrita")
    @unittest.skipIf(os.name == "nt", "fd-based rmtree walk (func=os.open) é POSIX-only")
    def test_dirs_without_read_or_execute_permission_are_fully_removed(self):
        """Numa subpasta sem leitura/execução (0 ou só escrita), o walk por fd
        do shutil.rmtree chama a retentativa com func=os.open, não
        os.unlink/os.rmdir/os.remove. Chamar `func(failed)` sem flags levantava
        TypeError, que escapava do antigo `suppress(OSError)` e violava o "nunca
        levanta" do docstring — cada retry deixava a árvore para trás."""
        base = Path(tempfile.mkdtemp(prefix="gb-b2-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)

        no_perm = base / "locked000"
        no_perm.mkdir()
        (no_perm / "inner.txt").write_text("x", encoding="utf-8")
        no_perm.chmod(0o000)
        self.addCleanup(lambda: no_perm.exists() and no_perm.chmod(stat.S_IRWXU))

        write_only = base / "locked200"
        write_only.mkdir()
        (write_only / "inner.txt").write_text("x", encoding="utf-8")
        write_only.chmod(0o200)
        self.addCleanup(lambda: write_only.exists() and write_only.chmod(stat.S_IRWXU))

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
        # `.gitconfig` com caminho do Windows (`C:\\Users\\...`) quebra o parser
        # do git ("bad config line"); o teste do smudge grava o caminho em forma POSIX.
        source = Path(__file__).with_name("test_sdk_install.py").read_text(encoding="utf-8")
        self.assertIn('touch "{marker.as_posix()}"', source)


@unittest.skipUnless(os.name == "nt", "junction do NTFS só existe no Windows")
class NtfsJunctionTests(InstallTestCase):
    """Junction não é `is_symlink()`, mas aponta para conteúdo fora do hash do pin
    como um link: o loader marca a pasta `invalid` e o install recusa."""

    def plugin_with_junction(self, folder):
        import _winapi  # só existe no Windows (a classe é pulada fora dele)

        write_plugin(folder)
        outside = self.work / "fora"
        outside.mkdir()
        (outside / "segredo.py").write_text("x = 1\n", encoding="utf-8")
        _winapi.CreateJunction(str(outside), str(folder / "vendor"))  # type: ignore[attr-defined] - só no Windows
        return folder

    def test_loader_marks_a_folder_with_a_junction_invalid(self):
        from getbrolls.sdk import loader

        self.plugin_with_junction(self.home / "plugins" / "demo")
        self.assertEqual(("link", "vendor"), loader.content_problem(self.home / "plugins" / "demo"))
        self.assertEqual("invalid", loader.inventory()[0]["status"])

    def test_install_refuses_a_source_with_a_junction(self):
        source = self.plugin_with_junction(self.work / "demo_src")
        with self.assertRaises(ValueError) as caught:
            install_mod.install(str(source), confirm=False)
        self.assertIn("vendor", str(caught.exception))
        self.assertEqual([], self.leftover_staging())


class ReparseTagTests(unittest.TestCase):
    """Só reparse point de "name surrogate" (junction, link do NTFS) conta como link.
    Arquivo sob demanda do OneDrive e deduplicação também são reparse points, mas
    guardam o próprio conteúdo: uma pasta sincronizada não pode ficar `invalid`.
    A lógica do Windows roda em qualquer sistema com um `lstat` falso."""

    CLOUD = 0x9000001A  # IO_REPARSE_TAG_CLOUD (OneDrive Files On-Demand)
    DEDUP = 0x80000013  # IO_REPARSE_TAG_DEDUP
    MOUNT_POINT = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT (junction)
    SYMLINK = 0xA000000C  # IO_REPARSE_TAG_SYMLINK

    def is_link_with_tag(self, tag, windows=True):
        from getbrolls.sdk import loader

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "pasta"
            folder.mkdir()
            real = os.lstat(folder)
            fake = type("FakeStat", (), {"st_mode": real.st_mode, "st_reparse_tag": tag})()
            with patch.object(loader.os, "lstat", return_value=fake):
                return loader._is_link(folder, windows=windows)

    def test_name_surrogate_tags_are_links(self):
        for tag in (self.MOUNT_POINT, self.SYMLINK):
            with self.subTest(tag=hex(tag)):
                self.assertTrue(self.is_link_with_tag(tag))

    def test_other_reparse_points_are_not_links(self):
        for tag in (self.CLOUD, self.DEDUP, 0):
            with self.subTest(tag=hex(tag)):
                self.assertFalse(self.is_link_with_tag(tag))

    def test_posix_ignores_the_reparse_tag(self):
        # No POSIX só `is_symlink()` decide; a tag (que lá nem existe) não é lida.
        self.assertFalse(self.is_link_with_tag(self.MOUNT_POINT, windows=False))


if __name__ == "__main__":
    unittest.main()
