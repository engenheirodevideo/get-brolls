"""acquisition.py: índice de fontes adulterado, cache inconsistente, rota de plugin e recusas de mídia."""

import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
import _logs
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import acquisition, runtime
from getbrolls.ledger import digest
from getbrolls.models import candidate


class AcquisitionCase(unittest.TestCase):
    def setUp(self):
        # O ciclo de vida cobre o teste inteiro; a limpeza é feita via addCleanup.
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cache = self.root / ".getbrolls-sources"
        self.cache.mkdir(mode=0o700)
        self.saved = []
        self.ledger = SimpleNamespace(
            root=self.root / "brolls", save=lambda operation, item: self.saved.append((operation, dict(item)))
        )

    def warnings(self):
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        return event["warnings"]

    def write_index(self, payload):
        path = self.cache / acquisition.INDEX_NAME
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        return path

    def media_file(self, name="clip.mp4", content=b"conteudo"):
        path = self.cache / name
        path.write_bytes(content)
        return path


class SourceIndexTests(AcquisitionCase):
    def test_an_unreadable_index_is_treated_as_empty_and_warns(self):
        warnings = self.warnings()
        self.write_index("{}")
        real = Path.read_text

        def read_text(self, *args, **kwargs):
            if self.name == acquisition.INDEX_NAME:
                raise PermissionError("sem acesso")
            return real(self, *args, **kwargs)

        with patch.object(Path, "read_text", read_text):
            self.assertEqual({}, acquisition._load_index(self.cache))
        self.assertEqual(["SOURCE_INDEX_UNREADABLE"], [w["code"] for w in warnings])
        self.assertIn("PermissionError", warnings[0]["message"])

    def test_a_corrupt_index_is_set_aside_as_bad_and_warns(self):
        warnings = self.warnings()
        self.write_index("{ nao e json")
        self.assertEqual({}, acquisition._load_index(self.cache))
        self.assertEqual(["SOURCE_INDEX_UNREADABLE"], [w["code"] for w in warnings])
        self.assertFalse((self.cache / acquisition.INDEX_NAME).exists())
        self.assertEqual("{ nao e json", (self.cache / "index.json.bad").read_text(encoding="utf-8"))

    def test_a_json_index_that_is_not_an_object_counts_as_empty(self):
        self.write_index([1, 2])
        self.assertEqual({}, acquisition._load_index(self.cache))

    def test_wrong_shapes_inside_the_index_are_dropped(self):
        self.write_index({"a": "texto", "b": [1, {"sha": "x"}, "y"]})
        self.assertEqual({"a": [], "b": [{"sha": "x"}]}, acquisition._load_index(self.cache))

    def test_saving_something_that_is_not_an_index_is_a_bug_and_writes_nothing(self):
        with self.assertRaisesRegex(TypeError, "Índice de fontes inválido"):
            acquisition._save_index(self.cache, [])
        self.assertEqual([], list(self.cache.iterdir()))

    def test_a_failed_write_leaves_neither_a_temp_file_nor_a_new_index(self):
        with (
            patch.object(acquisition.os, "fdopen", side_effect=OSError("disco cheio")),
            self.assertRaisesRegex(OSError, "disco cheio"),
        ):
            acquisition._save_index(self.cache, {"a": []})
        self.assertEqual([], list(self.cache.iterdir()))

    def refused_fdopen(self, error):
        """Run `_save_index` with an `fdopen` that refuses; returns the descriptors `mkstemp` opened and `close` saw."""
        opened, closed = [], []
        real_close = os.close

        def failing_fdopen(fd, mode="r", **kwargs):
            if mode == "w" and kwargs.get("encoding") == "utf-8":  # the index's temp file, not the salt's
                opened.append(fd)
            raise error

        def recording_close(fd):
            closed.append(fd)
            real_close(fd)

        with (
            patch.object(acquisition.os, "fdopen", failing_fdopen),
            patch.object(acquisition.os, "close", recording_close),
            self.assertRaises(type(error)),
        ):
            acquisition._save_index(self.cache, {"a": []})
        return opened, closed

    def test_a_failed_fdopen_closes_the_descriptor_before_the_temp_file_goes(self):
        # On Windows an open handle makes `unlink` fail with a sharing violation that hides "disco cheio".
        opened, closed = self.refused_fdopen(OSError("disco cheio"))
        self.assertEqual(1, len(opened))
        self.assertIn(opened[0], closed)
        self.assertEqual([], list(self.cache.iterdir()))

    def test_a_descriptor_that_fdopen_refuses_with_another_error_is_closed_and_leaves_no_temp_file(self):
        opened, closed = self.refused_fdopen(ValueError("modo invalido"))
        self.assertEqual(1, len(opened))
        self.assertIn(opened[0], closed)
        self.assertEqual([], list(self.cache.iterdir()))

    def test_an_index_that_json_cannot_write_leaves_no_temp_file_and_closes_once(self):
        closed = []
        real_close = os.close

        def recording_close(fd):
            closed.append(fd)
            real_close(fd)

        with (
            patch.object(acquisition.os, "close", recording_close),
            self.assertRaisesRegex(TypeError, "not JSON serializable"),
        ):
            acquisition._save_index(self.cache, {"a": [object()]})
        self.assertEqual([], list(self.cache.iterdir()))
        self.assertEqual([], closed)  # the stream owned the descriptor: no second close from our code

    def test_a_descriptor_that_is_already_closed_does_not_replace_the_real_error(self):
        # `io.open` closes the descriptor itself when it fails after the raw file exists (`LookupError`).
        def failing_fdopen(fd, mode="r", **kwargs):
            if mode == "w" and kwargs.get("encoding") == "utf-8":
                os.close(fd)
                raise LookupError("codificacao")
            raise OSError("outro fdopen")

        with (
            patch.object(acquisition.os, "fdopen", failing_fdopen),
            self.assertRaisesRegex(LookupError, "codificacao"),
        ):
            acquisition._save_index(self.cache, {"a": []})
        self.assertEqual([], list(self.cache.iterdir()))

    def test_a_cleanup_that_fails_does_not_replace_the_real_error(self):
        # On Windows an antivirus can hold the temp file: `unlink` raises, "disco cheio" must still come out.
        with (
            patch.object(acquisition.os, "fdopen", side_effect=OSError("disco cheio")),
            patch.object(Path, "unlink", side_effect=PermissionError("em uso")),
            self.assertRaisesRegex(OSError, "disco cheio"),
        ):
            acquisition._save_index(self.cache, {"a": []})

    def test_a_good_save_replaces_the_index_and_leaves_no_temp_file(self):
        acquisition._save_index(self.cache, {"a": [{"sha": "x"}]})
        self.assertEqual(
            {"schema_version": 1, "a": [{"sha": "x"}]},
            json.loads((self.cache / acquisition.INDEX_NAME).read_text("utf-8")),
        )
        self.assertEqual([acquisition.INDEX_NAME], [path.name for path in self.cache.iterdir()])


class ReuseFromIndexTests(AcquisitionCase):
    def test_entries_with_unusable_ranges_or_missing_files_are_not_reused(self):
        self.write_index(
            {
                "yt:1": [
                    {"start": "zero", "duration": 10, "path": str(self.media_file("a.mp4")), "sha": "x"},
                    {"start": 0, "duration": 10, "path": str(self.cache / "sumiu.mp4"), "sha": "x"},
                ]
            }
        )
        with self.assertLogs("getbrolls.acquisition", level="INFO") as cm:
            self.assertIsNone(acquisition._reuse_from_index(self.cache, "yt:1", 1, 5))
        event = _logs.events(cm.output, "source_cache")[0]
        self.assertEqual(("miss", "no_match"), (event["result"], event["reason"]))

    def test_a_covering_entry_whose_file_changed_is_ignored_with_a_warning(self):
        warnings = self.warnings()
        path = self.media_file("a.mp4", b"original")
        self.write_index({"yt:1": [{"start": 0, "duration": 10, "path": str(path), "sha": digest(path)}]})
        path.write_bytes(b"adulterado")
        with self.assertLogs("getbrolls.acquisition", level="INFO") as cm:
            self.assertIsNone(acquisition._reuse_from_index(self.cache, "yt:1", 1, 5))
        self.assertEqual(["SOURCE_CACHE_STALE"], [w["code"] for w in warnings])
        self.assertEqual("stale", _logs.events(cm.output, "source_cache")[0]["result"])

    def test_an_unintelligible_suffix_falls_back_to_bin(self):
        self.assertEqual(".mp4", acquisition._cache_suffix("a/b.MP4"))
        for name in ("semextensao", "x.", "x.mp4!", "x.aaaaaaaaaa"):
            with self.subTest(name=name):
                self.assertEqual(".bin", acquisition._cache_suffix(name))


class FetchedCacheTests(AcquisitionCase):
    def test_a_fetch_entry_with_a_non_text_path_is_not_reused(self):
        self.write_index({"yt:1#fetch": [{"path": 5, "sha": "x"}, {"path": "", "sha": "x"}]})
        self.assertIsNone(acquisition._reuse_fetched(self.cache, {"id": "yt:1"}))
        self.assertIsNone(acquisition._fetched_in_cache(self.cache, None))
        self.assertIsNone(acquisition._fetched_in_cache(self.cache, ["a"]))

    def test_a_fetch_entry_is_reused_by_file_name_and_keeps_its_license(self):
        path = self.media_file("yt-1-fetch-abc.mp4")
        self.write_index(
            {
                "yt:1#fetch": [
                    {
                        "path": "/outro/projeto/" + path.name,
                        "sha": digest(path),
                        "duration": 4.5,
                        "plugin": "acervo",
                        "license": "CC-BY",
                    }
                ]
            }
        )
        with self.assertLogs("getbrolls.acquisition", level="INFO"):
            reused = acquisition._reuse_fetched(self.cache, {"id": "yt:1"})
        self.assertIsNotNone(reused)
        assert reused is not None  # para o pyright
        self.assertEqual((path, "CC-BY", "acervo", 4.5, True), tuple(reused))


class MoveIntoCacheTests(AcquisitionCase):
    def test_the_same_content_already_in_the_cache_is_not_replaced(self):
        target = self.media_file("source.bin", b"video")
        sha = digest(target)
        final = self.media_file(f"{acquisition.id_stem('yt:1')}-{sha}.mp4", b"video")
        moved = acquisition._move_into_cache(self.cache, "yt:1", target, sha, ".mp4")
        self.assertEqual(final, moved)
        self.assertTrue(target.exists())

    def test_a_cache_file_with_the_same_name_and_other_content_is_refused_and_kept(self):
        target = self.media_file("source.bin", b"video")
        sha = digest(target)
        final = self.media_file(f"{acquisition.id_stem('yt:1')}-{sha}.mp4", b"corrompido")
        with (
            self.assertLogs("getbrolls.acquisition", level="WARNING") as cm,
            self.assertRaisesRegex(ValueError, "Cache de mídia inconsistente"),
        ):
            acquisition._move_into_cache(self.cache, "yt:1", target, sha, ".mp4")
        self.assertEqual(b"corrompido", final.read_bytes())
        self.assertEqual("digest_mismatch", _logs.events(cm.output, "source_cache")[0]["reason"])


class DirectDownloadTests(AcquisitionCase):
    def test_a_candidate_without_a_file_url_is_refused(self):
        with self.assertRaisesRegex(ValueError, "não está mais disponível"):
            acquisition._direct_download({"media_url": None}, refresh=False)

    def test_a_refresh_that_finds_nothing_falls_back_to_the_stored_url(self):
        with patch("getbrolls.providers.refresh", return_value=None):
            result = acquisition._direct_download({"media_url": "https://x.test/a.mp4"}, refresh=True)
        self.assertEqual({"media_url": "https://x.test/a.mp4"}, result)

    def test_a_refresh_with_a_new_url_replaces_the_stored_item(self):
        fresh = {"media_url": "https://x.test/b.mp4", "media_url_fallbacks": ["https://x.test/c.mp4"]}
        with patch("getbrolls.providers.refresh", return_value=fresh):
            result = acquisition._direct_download({"media_url": "https://x.test/a.mp4"}, refresh=True)
        self.assertEqual(fresh, result)


class WorkingCopyTests(AcquisitionCase):
    def setUp(self):
        super().setUp()
        self.item = candidate("youtube", "abc", "Foguete")
        self.item["media"]["kind"] = "image"

    def test_an_unchanged_working_copy_of_a_photo_is_kept_without_downloading(self):
        path = self.media_file("foto.jpg", b"jpg")
        self.item.update(local_path=str(path), local_sha256=digest(path))
        with patch.object(acquisition, "cache_direct_media") as download:
            self.assertIsNone(acquisition.prepare_image_source(self.ledger, self.item))
        download.assert_not_called()

    def test_a_changed_working_copy_of_a_photo_is_refused(self):
        path = self.media_file("foto.jpg", b"jpg")
        self.item.update(local_path=str(path), local_sha256=digest(path))
        path.write_bytes(b"outra coisa")
        with self.assertRaisesRegex(ValueError, "Fonte de trabalho alterada"):
            acquisition.prepare_image_source(self.ledger, self.item)

    def test_a_missing_photo_is_downloaded_and_its_size_recorded(self):
        final = self.media_file("baixada.jpg", b"jpg")
        info = {"width": 640, "height": 480, "duration_s": 0, "fps": None}
        with (
            patch.object(acquisition, "cache_direct_media", return_value=final) as download,
            patch.object(acquisition, "probe", return_value=info),
        ):
            acquisition.prepare_image_source(self.ledger, self.item, stage="inspect")
        self.assertEqual("inspect", download.call_args.kwargs["stage"])
        self.assertEqual(str(final.resolve()), self.item["local_path"])
        self.assertEqual(digest(final), self.item["local_sha256"])
        self.assertEqual((640, 480), (self.item["media"]["width"], self.item["media"]["height"]))

    def test_a_changed_working_copy_is_refused_when_checking_the_interval(self):
        path = self.media_file("trabalho.mp4", b"video")
        self.item.update(local_path=str(path), local_sha256="outro-hash")
        with self.assertRaisesRegex(ValueError, "Fonte de trabalho alterada"):
            acquisition._ready_locally(self.item, 0, 3)

    def test_a_working_copy_covers_only_the_interval_it_holds(self):
        path = self.media_file("trabalho.mp4", b"video")
        self.item.update(local_path=str(path), local_sha256=digest(path), local_start_s=10, local_duration_s=5)
        self.assertTrue(acquisition._ready_locally(self.item, 11, 14))
        self.assertFalse(acquisition._ready_locally(self.item, 11, 16))

    def test_https_source_that_the_provider_no_longer_serves_is_refused(self):
        item = candidate("nasa", "1", "Foguete")
        item["acquisition"] = {"method": "https"}
        with (
            patch("getbrolls.providers.refresh", return_value={"media_url": None}),
            self.assertRaisesRegex(ValueError, "não está mais disponível"),
        ):
            acquisition._acquire(
                item, self.cache / "source.mp4", (0, 3), contextlib.ExitStack(), (self.ledger, "preview")
            )


class RouteMarkTests(AcquisitionCase):
    def test_the_route_mark_is_saved_even_without_an_active_command(self):
        self.assertIsNone(runtime.ACTIVE.get())
        item = {"id": "x"}
        acquisition._save_route_mark(self.ledger, item, "fetch-route-start")
        self.assertEqual(["fetch-route-start"], [op for op, _ in self.saved])

    def test_the_route_mark_never_turns_state_committed_on(self):
        event = {"warnings": [], "state_committed": False}
        token = runtime.ACTIVE.set(event)
        self.addCleanup(runtime.ACTIVE.reset, token)
        acquisition._save_route_mark(
            SimpleNamespace(save=lambda op, item: event.update(state_committed=True)), {"id": "x"}, "fetch-route-start"
        )
        self.assertFalse(event["state_committed"])


class RouteOwnershipTests(AcquisitionCase):
    def _registry(self, provider_owner):
        owners = {("route", "r"): "acervo", ("provider", "youtube"): provider_owner}
        return SimpleNamespace(
            route=lambda name: object(),
            owner=lambda kind, name: owners.get((kind, name)),
            route_stage=lambda name: "preview",
        )

    def test_a_route_that_belongs_to_another_plugin_is_refused(self):
        item = candidate("youtube", "abc", "Foguete")
        item["acquisition"] = {"method": "plugin:r"}
        with (
            patch("getbrolls.sdk.registry.get_registry", return_value=self._registry("outro")),
            self.assertRaisesRegex(ValueError, "não pertence à fonte youtube"),
        ):
            acquisition._route_for(item)

    def test_a_route_that_belongs_to_the_providers_plugin_is_accepted(self):
        item = candidate("youtube", "abc", "Foguete")
        item["acquisition"] = {"method": "plugin:r"}
        with patch("getbrolls.sdk.registry.get_registry", return_value=self._registry("acervo")):
            owner, name, _route, stage = acquisition._route_for(item)
        self.assertEqual(("acervo", "r", "preview"), (owner, name, stage))


class FetchRoutedSourceCacheTests(AcquisitionCase):
    def _routed(self, content):
        source = self.root / "work" / "routed.mp4"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(content)

        @contextlib.contextmanager
        def plugin_source(_ledger, _candidate, _stage, on_returned=None):
            if on_returned:
                on_returned()
            yield acquisition.RoutedFile(source, "CC0", "acervo")

        return source, plugin_source

    def _fetch(self, item, plugin_source):
        info = {"duration_s": 3.0, "width": 160, "height": 90, "fps": 10.0}
        with (
            patch.object(acquisition, "plugin_source", plugin_source),
            patch.object(acquisition, "probe", return_value=info),
        ):
            return acquisition.fetch_routed_source(self.ledger, item)

    def test_the_licensed_file_already_in_the_cache_is_not_moved_again(self):
        source, plugin_source = self._routed(b"licenciado")
        sha = digest(source)
        item = candidate("acervo", "1", "Foguete")
        final = self.cache / f"{acquisition.id_stem('acervo:1')}-fetch-{sha}.mp4"
        final.write_bytes(b"licenciado")
        fetched = self._fetch(item, plugin_source)
        self.assertEqual(
            (final, "CC0", "acervo", False), (fetched.path, fetched.license, fetched.plugin, fetched.reused)
        )
        self.assertTrue(source.exists())
        index = json.loads((self.cache / acquisition.INDEX_NAME).read_text(encoding="utf-8"))
        self.assertEqual([final.name], [e["path"] for e in index["acervo:1#fetch"]])

    def test_a_cache_file_with_the_right_name_and_wrong_content_is_refused_and_the_mark_stays(self):
        source, plugin_source = self._routed(b"licenciado")
        sha = digest(source)
        item = candidate("acervo", "1", "Foguete")
        final = self.cache / f"{acquisition.id_stem('acervo:1')}-fetch-{sha}.mp4"
        final.write_bytes(b"corrompido")
        with self.assertRaisesRegex(ValueError, "Cache de mídia inconsistente"):
            self._fetch(item, plugin_source)
        self.assertEqual(b"corrompido", final.read_bytes())
        self.assertFalse((self.cache / acquisition.INDEX_NAME).exists())
        # A rota já devolveu: a licença pode ter saído, então a marca de início fica.
        self.assertIn("route_started_at", item["acquisition"])
        self.assertEqual(["fetch-route-start"], [op for op, _ in self.saved])


if __name__ == "__main__":
    unittest.main()
