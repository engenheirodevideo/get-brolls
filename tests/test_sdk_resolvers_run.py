"""`resolve_with_plugins`: o que um resolvedor de plugin pode apontar, e o que o core recusa."""

import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Any só aparece citado em cast("Any", ...); o pyright resolve a string, o pylint não.
from typing import Any, cast  # pylint: disable=unused-import
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import http
from getbrolls.sdk import PluginError, ResolverHit, ResolverSpec, safe_copy, testing
from getbrolls.sdk.api import PluginApi
from getbrolls.sdk.registry import Registry
from getbrolls.sdk.resolvers import license_line, resolve_with_plugins

POSIX = os.name != "nt"
AUDIO = (".wav", ".mp3")


class ResolverTestCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-resolver-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = self.base / "acervo"
        self.outside = self.base / "fora"
        self.root.mkdir()
        self.outside.mkdir()
        self.sound = self.root / "porta.wav"
        self.sound.write_bytes(b"rangido")
        (self.outside / "segredo.wav").write_bytes(b"fora da raiz")
        self.registry = Registry()

    def add(self, resolve, owner="demo", name=None, kinds=("sfx",), roots=None):
        roots = (str(self.root),) if roots is None else roots
        spec = ResolverSpec(name or owner, kinds, resolve)
        self.registry.add_resolver(spec, owner=owner, roots=roots)

    def returns(self, value, **kwargs):
        self.add(lambda kind, name: value, **kwargs)

    def resolve(self, name="porta"):
        return resolve_with_plugins(self.registry, "sfx", name, AUDIO)

    def refused(self, fragment):
        hit, warnings = self.resolve()
        self.assertIsNone(hit)
        self.assertEqual(1, len(warnings), warnings)
        self.assertIn(fragment, warnings[0])
        self.assertTrue(warnings[0].startswith("Plugin demo:"), warnings[0])
        return warnings[0]


class HitTests(ResolverTestCase):
    def test_valid_hit_carries_owner_identity_and_license(self):
        self.returns(ResolverHit(self.sound, "CC BY 4.0 — Acervo\nsegunda linha"))
        hit, warnings = self.resolve()
        assert hit is not None
        info = self.sound.stat()
        self.assertEqual([], warnings)
        self.assertEqual(
            {
                "path": str(self.sound),
                "store": "demo",
                "license": "CC BY 4.0 — Acervo segunda linha",
                "resolver": "demo",
                "st_dev": info.st_dev,
                "st_ino": info.st_ino,
                "st_size": info.st_size,
            },
            hit,
        )

    def test_nothing_found_is_not_a_warning(self):
        self.returns(None)
        self.assertEqual((None, []), self.resolve())

    def test_resolvers_run_in_owner_order_and_the_first_valid_hit_wins(self):
        calls = []

        def resolver(owner, value):
            def resolve(kind, name):
                calls.append(owner)
                return value

            return resolve

        self.add(resolver("zeta", ResolverHit(self.sound)), owner="zeta")
        self.add(resolver("beta", ResolverHit(self.sound)), owner="beta")
        self.add(resolver("alfa", ResolverHit(self.outside / "segredo.wav")), owner="alfa")
        hit, warnings = self.resolve()
        assert hit is not None
        self.assertEqual(["alfa", "beta"], calls)
        self.assertEqual("beta", hit["store"])
        self.assertEqual(1, len(warnings))
        self.assertIn("Plugin alfa:", warnings[0])

    def test_only_resolver_kinds_are_accepted(self):
        for kind in ("marca", "aroll", "beat"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                resolve_with_plugins(self.registry, kind, "porta", AUDIO)
        self.returns(ResolverHit(self.sound), kinds=("musica",))
        self.assertEqual((None, []), self.resolve())


class RefusedHitTests(ResolverTestCase):
    def test_outside_the_roots(self):
        self.returns(ResolverHit(self.outside / "segredo.wav"))
        warning = self.refused("fora de permissions.paths")
        self.assertNotIn(str(self.outside), warning)

    def test_parent_segments_cannot_climb_out(self):
        self.returns(ResolverHit(self.root / ".." / "fora" / "segredo.wav"))
        self.refused("fora de permissions.paths")

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_symlink_even_inside_the_root(self):
        link = self.root / "atalho.wav"
        link.symlink_to(self.sound)
        self.returns(ResolverHit(link))
        self.refused("é um link")

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_folder_link_pointing_outside(self):
        (self.root / "sub").symlink_to(self.outside)
        self.returns(ResolverHit(self.root / "sub" / "segredo.wav"))
        self.refused("fora de permissions.paths")

    @unittest.skipUnless(os.name == "nt", "junction é do NTFS")
    def test_junction_is_a_link(self):
        # _winapi só existe no Windows; o teste inteiro já é pulado fora dele.
        import _winapi  # type: ignore[import-not-found]  # pylint: disable=import-outside-toplevel,import-error

        junction = self.root / "atalho.wav"
        _winapi.CreateJunction(str(self.outside), str(junction))  # type: ignore[attr-defined]
        self.returns(ResolverHit(junction))
        self.refused("é um link")

    def test_directory(self):
        (self.root / "pasta.wav").mkdir()
        self.returns(ResolverHit(self.root / "pasta.wav"))
        hit, warnings = self.resolve()
        self.assertIsNone(hit)
        self.assertEqual(1, len(warnings))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO é POSIX")
    def test_fifo_is_refused_without_blocking(self):
        os.mkfifo(self.root / "fila.wav")
        self.returns(ResolverHit(self.root / "fila.wav"))
        self.refused("não é um arquivo")

    def test_hardlink_with_more_than_one_name(self):
        os.link(self.outside / "segredo.wav", self.root / "copia.wav")
        self.returns(ResolverHit(self.root / "copia.wav"))
        self.refused("hardlink")

    def test_wrong_extension(self):
        (self.root / "notas.txt").write_text("x", encoding="utf-8")
        self.returns(ResolverHit(self.root / "notas.txt"))
        self.refused("extensão")

    def test_missing_file(self):
        self.returns(ResolverHit(self.root / "nada.wav"))
        self.refused("não foi encontrado")

    def test_oversize(self):
        self.returns(ResolverHit(self.sound))
        with patch.object(http, "DOWNLOAD_MAX_BYTES", 4):
            self.refused("teto")

    def test_not_a_resolver_hit_or_not_absolute(self):
        for value, fragment in (
            (str(self.sound), "ResolverHit"),
            (ResolverHit("porta.wav"), "absoluto"),
            (ResolverHit(""), "absoluto"),
            (ResolverHit(self.sound, "x" * 501), "license"),
            (ResolverHit(self.sound, "  "), "license"),
        ):
            self.registry = Registry()
            self.returns(value)
            with self.subTest(fragment=fragment):
                self.refused(fragment)

    def test_subclass_of_resolver_hit_is_refused(self):
        class Hit(ResolverHit):
            pass

        self.returns(Hit(self.sound))
        self.refused("ResolverHit")

    def test_empty_roots(self):
        self.returns(ResolverHit(self.sound), roots=())
        self.refused("permissions.paths")

    def test_too_broad_root_is_ignored(self):
        manifest = {
            "id": "demo",
            "contributes": {"resolvers": ["demo"]},
            "permissions": {"network": [], "env": [], "paths": [str(self.base)]},
        }
        with patch.dict(os.environ, {"HOME": str(self.base), "USERPROFILE": str(self.base)}):
            api = PluginApi(manifest, self.registry)
            api.resolver("demo", lambda kind, name: ResolverHit(self.sound), ["sfx"])
        self.assertEqual((), self.registry.resolver_roots("demo"))
        self.refused("permissions.paths")


class SwapTests(ResolverTestCase):
    """Uma pasta do meio trocada por link entre a conferência da raiz e a abertura."""

    def setUp(self):
        super().setUp()
        (self.root / "sub").mkdir()
        (self.root / "sub" / "eco.wav").write_bytes(b"eco de dentro")
        (self.outside / "sub").mkdir()
        (self.outside / "sub" / "eco.wav").write_bytes(b"eco de fora")

    def swap_sub_for_a_link(self):
        (self.root / "sub").rename(self.base / "sub-original")
        (self.root / "sub").symlink_to(self.outside / "sub")

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_folder_swapped_before_the_open_is_refused(self):
        real_open = safe_copy.open_regular

        def swapping_open(path, **kwargs):
            self.swap_sub_for_a_link()
            return real_open(path, **kwargs)

        self.returns(ResolverHit(self.root / "sub" / "eco.wav"))
        with patch.object(safe_copy, "open_regular", swapping_open):
            self.refused("fora de permissions.paths")

    @unittest.skipUnless(POSIX, "symlink exige privilégio no Windows")
    def test_copy_step_rechecks_the_root(self):
        self.returns(ResolverHit(self.root / "sub" / "eco.wav"))
        hit, _ = self.resolve()
        assert hit is not None
        # O mesmo arquivo (mesmo inode) agora é alcançado por um link que sai da raiz.
        (self.root / "sub").rename(self.outside / "movida")
        (self.root / "sub").symlink_to(self.outside / "movida")
        found = (hit["path"], hit["st_dev"], hit["st_ino"], hit["st_size"])
        with self.assertRaises(safe_copy.UnsafeFileError) as caught:
            safe_copy.recheck(*found, roots=self.registry.resolver_roots("demo"))
        self.assertEqual(safe_copy.OUTSIDE, caught.exception.reason)

    def test_case_variant_of_the_root_is_inside(self):
        variant = self.base / self.root.name.upper() / "porta.wav"
        if not variant.exists():
            self.skipTest("disco diferencia maiúsculas de minúsculas")
        self.returns(ResolverHit(variant))
        hit, warnings = self.resolve()
        self.assertEqual([], warnings)
        assert hit is not None
        self.assertEqual(self.sound.stat().st_ino, hit["st_ino"])


class IsolationTests(ResolverTestCase):
    def test_exception_becomes_a_warning_and_the_next_resolver_runs(self):
        def says(kind, name):
            raise PluginError("Configure o acervo.")

        def leaks(kind, name):
            raise RuntimeError("segredo-do-plugin")

        def exits(kind, name):
            raise SystemExit(3)

        self.add(says, owner="alfa")
        self.add(leaks, owner="beta")
        self.add(exits, owner="gama")
        self.add(lambda kind, name: ResolverHit(self.sound), owner="zeta")
        hit, warnings = self.resolve()
        assert hit is not None
        self.assertEqual("zeta", hit["store"])
        self.assertEqual("Plugin alfa: Configure o acervo.", warnings[0])
        self.assertIn("RuntimeError", warnings[1])
        self.assertNotIn("segredo-do-plugin", warnings[1])
        self.assertIn("SystemExit", warnings[2])

    def test_hostile_path_object_is_isolated(self):
        class HostilePath:
            def __fspath__(self):
                raise SystemExit(1)

        self.returns(ResolverHit(HostilePath()))  # type: ignore[arg-type]
        self.refused("SystemExit")

    def test_plugin_stdout_goes_to_stderr(self):
        def noisy(kind, name):
            sys.stdout.write("barulho do resolvedor\n")

        self.add(noisy)
        with patch("sys.stdout", new=io.StringIO()) as out, patch("sys.stderr", new=io.StringIO()) as err:
            self.resolve()
        self.assertNotIn("barulho", out.getvalue())
        self.assertIn("barulho", err.getvalue())


class CopyTests(ResolverTestCase):
    def hit(self):
        self.returns(ResolverHit(self.sound))
        hit, _ = self.resolve()
        assert hit is not None
        return hit

    def test_copy_rechecks_and_never_touches_the_original(self):
        hit = self.hit()
        target = self.base / "export" / "som.wav"
        target.parent.mkdir()
        before = self.sound.stat()
        fd, _ = safe_copy.recheck(hit["path"], hit["st_dev"], hit["st_ino"], hit["st_size"])
        try:
            safe_copy.copy_from_fd(fd, target, http.DOWNLOAD_MAX_BYTES)
        finally:
            os.close(fd)
        self.assertEqual(b"rangido", target.read_bytes())
        self.assertNotEqual(before.st_ino, target.stat().st_ino)
        self.assertEqual(before.st_mode, self.sound.stat().st_mode)
        self.assertEqual(1, self.sound.stat().st_nlink)

    def test_file_swapped_after_the_hit_is_refused_at_copy_time(self):
        hit = self.hit()
        swapped = self.root / "trocado.wav"
        swapped.write_bytes(b"outro som")
        swapped.replace(self.sound)
        with self.assertRaises(safe_copy.UnsafeFileError) as caught:
            safe_copy.recheck(hit["path"], hit["st_dev"], hit["st_ino"], hit["st_size"])
        self.assertEqual(safe_copy.CHANGED, caught.exception.reason)


class LicenseLineTests(unittest.TestCase):
    def test_license_is_one_inert_line_with_the_plugin_prefix(self):
        line = license_line("demo", "CC BY [veja](http://exemplo.com) <img src=x>\n**livre**")
        self.assertTrue(line.startswith("Licença informada pelo plugin demo: "))
        self.assertNotIn("\n", line)
        # Marcação sai escapada por barra invertida: vira texto, nunca link, imagem ou ênfase.
        self.assertIn("\\[veja\\]", line)
        self.assertIn("\\<img", line)
        self.assertIn("\\*\\*livre\\*\\*", line)
        self.assertNotIn("http://exemplo.com", line)


class CheckResolverTests(unittest.TestCase):
    def test_shape_failures_read_clearly(self):
        testing.check_resolver(ResolverSpec("demo", ("sfx",), lambda kind, name: None))
        for spec, fragment in (
            (object(), "api.resolver"),
            (ResolverSpec("demo", ("marca",), lambda kind, name: None), "kinds"),
            (ResolverSpec("demo", (), lambda kind, name: None), "kinds"),
            (ResolverSpec("demo", ("sfx",), cast("Any", lambda kind: None)), "(kind, name)"),
        ):
            with self.subTest(fragment=fragment), self.assertRaises(AssertionError) as caught:
                testing.check_resolver(spec)  # type: ignore[arg-type]
            self.assertIn(fragment, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
