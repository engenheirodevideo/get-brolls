"""Plugin HyperFrames no SDK: manifesto, registro do exporter e do resolvedor, e o resolvedor do media-use."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import ROOT
from _plugin_pins import pin_plugins
from test_plugin_hyperframes import fixture, hf

from getbrolls.sdk import ExportResult, PluginError, ResolverHit
from getbrolls.sdk.registry import get_registry, reset_registry

EXAMPLE = ROOT / "examples" / "plugins" / "hyperframes"


class FakeApi:
    def __init__(self, settings=None):
        self.settings = settings or {}

    def config(self):
        return self.settings


class HomeCase(unittest.TestCase):
    """HOME e GB_HOME temporários: `~/.media` e `~/.getbrolls` de mentira."""

    def setUp(self):
        self.user = Path(tempfile.mkdtemp(prefix="gb-hf-user-")).resolve()
        self.addCleanup(shutil.rmtree, self.user, ignore_errors=True)
        self.home = Path(tempfile.mkdtemp(prefix="gb-hf-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.user), "GB_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GB_PLUGINS", None)
        reset_registry()
        self.addCleanup(reset_registry)

    def plugin_copy(self, parent):
        target = Path(parent) / "hyperframes"
        shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("__pycache__"))
        return target


class RegistrationTests(HomeCase):
    def test_example_passes_plugins_check(self):
        out = run_cli("plugins", "--action", "check", "--path", self.plugin_copy(self.user))
        self.assertTrue(out["ok"])
        self.assertEqual(
            (["hyperframes"], ["hyperframes_media"]), (out["contracts"]["exporters"], out["contracts"]["resolvers"])
        )
        self.assertEqual(["~/.media"], out["permissions"]["paths"])

    def test_enabled_plugin_registers_exporter_and_resolver(self):
        self.plugin_copy(self.home / "plugins")
        pin_plugins("hyperframes", home=self.home)
        registry = get_registry()
        spec = registry.exporter("hyperframes")
        assert spec is not None
        self.assertEqual("hyperframes", registry.owner("exporter", "hyperframes"))
        result = spec.export(fixture(), {"args": {}})
        self.assertIs(type(result), ExportResult)
        self.assertIn("index.html", result.files)
        self.assertEqual(
            [("hyperframes", "hyperframes_media")], [(o, s.name) for o, s in registry.resolvers_for("sfx")]
        )
        resolver = registry.resolver("hyperframes_media")
        assert resolver is not None
        self.assertEqual(("sfx", "musica"), resolver.kinds)
        self.assertEqual((str(self.user / ".media"),), registry.resolver_roots("hyperframes_media"))


class ResolverTests(HomeCase):
    def media_use(self, records, folder=None):
        store = folder or (self.user / ".media")
        store.mkdir(parents=True, exist_ok=True)
        lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
        (store / "manifest.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return store

    def cached(self, name, sentinel=True):
        folder = self.user / ".media" / f"mu-v1-{Path(name).stem}"
        folder.mkdir(parents=True, exist_ok=True)
        if sentinel:
            (folder / ".hf-complete").write_text("", encoding="utf-8")
        path = folder / name
        path.write_bytes(b"audio")
        return path

    def record(self, ident, kind, path, **extra):
        base = {
            "id": ident, "type": kind, "source": "bundled", "description": "Whoosh curto",
            "provenance": {"provider": "bundled.sfx", "prompt": "whoosh", "library_key": "whoosh"},
            "sha": ident, "reusable": True, "cached_path": str(path),
        }  # fmt: skip
        return {**base, **extra}

    def resolve(self, kind, name, settings=None):
        return hf.MediaUseResolver(FakeApi(settings))(kind, name)

    def test_global_cache_hit_with_sentinel(self):
        path = self.cached("sfx_001.mp3")
        self.media_use([self.record("sfx_001", "sfx", path)])
        hit = self.resolve("sfx", "  Whoosh ")
        self.assertIs(type(hit), ResolverHit)
        assert hit is not None
        self.assertEqual(str(path), hit.path)
        self.assertEqual("media-use bundled; provider bundled.sfx; Whoosh curto", hit.license)

    def test_music_maps_to_bgm_and_marca_is_never_resolved(self):
        path = self.cached("bgm_001.mp3")
        self.media_use([self.record("bgm_001", "bgm", path, entity="lofi", provenance={})])
        hit = self.resolve("musica", "lofi")
        assert hit is not None
        self.assertEqual(str(path), hit.path)
        self.assertIsNone(self.resolve("sfx", "lofi"))
        self.assertIsNone(self.resolve("marca", "lofi"))

    def test_without_sentinel_or_not_reusable_nothing(self):
        path = self.cached("sfx_001.mp3", sentinel=False)
        self.media_use([self.record("sfx_001", "sfx", path)])
        self.assertIsNone(self.resolve("sfx", "whoosh"))
        path = self.cached("sfx_002.mp3")
        self.media_use([self.record("sfx_002", "sfx", path, reusable=False)])
        self.assertIsNone(self.resolve("sfx", "whoosh"))

    def test_bad_lines_are_skipped(self):
        path = self.cached("sfx_001.mp3")
        self.media_use(["{nao e json", "[1, 2]", self.record("sfx_001", "sfx", path)])
        self.assertIsNotNone(self.resolve("sfx", "whoosh"))

    def test_deeply_nested_line_is_skipped_too(self):
        path = self.cached("sfx_001.mp3")
        self.media_use(["[" * 100_000 + "]" * 100_000, self.record("sfx_001", "sfx", path)])
        self.assertIsNotNone(self.resolve("sfx", "whoosh"))

    def test_ambiguous_level_is_a_plugin_error(self):
        first, second = self.cached("sfx_001.mp3"), self.cached("sfx_004.mp3")
        self.media_use([self.record("sfx_001", "sfx", first), self.record("sfx_004", "sfx", second)])
        with self.assertRaisesRegex(PluginError, "ambíguo no media-use: sfx_001, sfx_004"):
            self.resolve("sfx", "whoosh")

    def test_id_beats_prompt_and_same_sha_is_not_ambiguous(self):
        first, second = self.cached("sfx_001.mp3"), self.cached("sfx_002.mp3")
        self.media_use([self.record("sfx_001", "sfx", first, sha="x"), self.record("sfx_002", "sfx", second, sha="x")])
        hit = self.resolve("sfx", "sfx_002")
        assert hit is not None
        self.assertEqual(str(second), hit.path)
        self.assertIsNotNone(self.resolve("sfx", "whoosh"))

    def test_project_ledger_comes_first_and_only_inside_permitted_paths(self):
        global_path = self.cached("sfx_009.mp3")
        self.media_use([self.record("sfx_009", "sfx", global_path)])
        project = self.user / ".media" / "projetos" / "reel"
        (project / ".media" / "audio" / "sfx").mkdir(parents=True)
        (project / ".media" / "audio" / "sfx" / "sfx_001.mp3").write_bytes(b"do projeto")
        self.media_use([{"id": "sfx_001", "type": "sfx", "path": ".media/audio/sfx/sfx_001.mp3", "source": "bundled",
                         "provenance": {"prompt": "whoosh"}}], project / ".media")  # fmt: skip
        hit = self.resolve("sfx", "whoosh", {"media_projects": [str(project)]})
        assert hit is not None
        self.assertEqual(str(project / ".media" / "audio" / "sfx" / "sfx_001.mp3"), hit.path)
        outside = Path(tempfile.mkdtemp(prefix="gb-hf-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        shutil.copytree(project / ".media", outside / ".media")
        hit = self.resolve("sfx", "whoosh", {"media_projects": [str(outside)]})
        assert hit is not None
        self.assertEqual(str(global_path), hit.path)

    def test_project_records_without_sha_are_told_apart_by_path(self):
        project = self.user / ".media" / "projetos" / "reel"
        (project / ".media" / "audio" / "sfx").mkdir(parents=True)
        for ident in ("sfx_001", "sfx_002"):
            (project / ".media" / "audio" / "sfx" / f"{ident}.mp3").write_bytes(b"do projeto")
        rows = [{"id": i, "type": "sfx", "path": f".media/audio/sfx/{i}.mp3", "provenance": {"prompt": "whoosh"}}
                for i in ("sfx_001", "sfx_002")]  # fmt: skip
        self.media_use(rows, project / ".media")
        settings = {"media_projects": [str(project)]}
        with self.assertRaisesRegex(PluginError, "ambíguo no media-use: sfx_001, sfx_002"):
            self.resolve("sfx", "whoosh", settings)
        self.media_use([rows[0], {**rows[1], "id": "sfx_003", "path": rows[0]["path"]}], project / ".media")
        hit = self.resolve("sfx", "whoosh", settings)
        assert hit is not None
        self.assertEqual(str(project / ".media" / "audio" / "sfx" / "sfx_001.mp3"), hit.path)

    def test_bad_project_entries_are_skipped_and_global_still_answers(self):
        path = self.cached("sfx_001.mp3")
        self.media_use([self.record("sfx_001", "sfx", path)])
        settings = {"media_projects": ["/proj\x00eto", "relativo/proj", 7, None, "~gb-ninguem-assim/proj"]}
        hit = self.resolve("sfx", "whoosh", settings)
        assert hit is not None
        self.assertEqual(str(path), hit.path)
        self.assertIsNotNone(self.resolve("sfx", "whoosh", {"media_projects": "nao-e-lista"}))

    def test_project_path_with_parent_segments_is_ignored(self):
        project = self.user / ".media" / "projetos" / "reel"
        (project / ".media").mkdir(parents=True)
        self.media_use(
            [{"id": "sfx_001", "type": "sfx", "path": "../../segredo.mp3", "provenance": {"prompt": "whoosh"}}],
            project / ".media",
        )
        self.assertIsNone(self.resolve("sfx", "whoosh", {"media_projects": [str(project)]}))

    def test_resolver_never_writes(self):
        path = self.cached("sfx_001.mp3")
        self.media_use([self.record("sfx_001", "sfx", path)])
        before = sorted(p.relative_to(self.user).as_posix() for p in self.user.rglob("*"))
        self.resolve("sfx", "whoosh")
        self.resolve("musica", "nada")
        self.assertEqual(before, sorted(p.relative_to(self.user).as_posix() for p in self.user.rglob("*")))


if __name__ == "__main__":
    unittest.main()
