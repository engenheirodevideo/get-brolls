"""search_commands.py: validação dos argumentos de `resolve` (contexto, metadados locais, origem, beat)."""

import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import search_commands


def _args(**changes):
    base = {
        "file": None,
        "context_image": None,
        "full_preview_file": None,
        "asset_type": None,
        "title": None,
        "captured_at": None,
        "source_url": None,
        "creator": None,
        "shot": None,
    }
    base.update(changes)
    return types.SimpleNamespace(**base)


def _candidate():
    return {"id": "c1", "media": {}, "creator": {}}


class ResolveFixtureCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-resolve-edges-")).resolve()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _file(self, name, data=b"x"):
        path = self.base / name
        path.write_bytes(data)
        return path


class ContextFileTests(ResolveFixtureCase):
    def test_no_context_argument_changes_nothing(self):
        item = _candidate()
        search_commands._resolve_context_files(item, _args())  # pylint: disable=protected-access
        self.assertEqual(_candidate(), item)

    def test_a_context_file_without_a_local_broll_is_refused(self):
        shot = self._file("print.png")
        with self.assertRaisesRegex(ValueError, "exigem um B-roll local"):
            search_commands._resolve_context_files(_candidate(), _args(context_image=str(shot)))  # pylint: disable=protected-access

    def test_a_missing_context_file_is_refused(self):
        args = _args(file=str(self._file("a.mp4")), full_preview_file=str(self.base / "ausente.mp4"))
        with self.assertRaisesRegex(ValueError, "não encontrado"):
            search_commands._resolve_context_files(_candidate(), args)  # pylint: disable=protected-access

    def test_a_context_image_must_be_a_picture(self):
        args = _args(file=str(self._file("a.mp4")), context_image=str(self._file("notas.txt")))
        with self.assertRaisesRegex(ValueError, "PNG/JPG/WebP"):
            search_commands._resolve_context_files(_candidate(), args)  # pylint: disable=protected-access

    def test_a_valid_context_image_is_recorded_with_its_hash_and_media(self):
        picture = self._file("print.PNG", b"png-bytes")
        item = _candidate()
        with patch.object(search_commands, "probe", return_value={"width": 10}):
            search_commands._resolve_context_files(  # pylint: disable=protected-access
                item, _args(file=str(self._file("a.mp4")), context_image=str(picture))
            )
        self.assertEqual(str(picture.resolve()), item["context_image_path"])
        self.assertEqual(search_commands.digest(picture), item["context_image_sha256"])
        self.assertEqual({"width": 10}, item["context_image_media"])


class LocalMetadataTests(ResolveFixtureCase):
    def test_metadata_without_a_file_is_refused_and_without_metadata_it_is_a_no_op(self):
        item = _candidate()
        search_commands._resolve_local_metadata(item, _args())  # pylint: disable=protected-access
        self.assertEqual(_candidate(), item)
        for changes in ({"asset_type": "video"}, {"title": "t"}, {"captured_at": "2026-01-01"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "exigem --file"):
                search_commands._resolve_local_metadata(_candidate(), _args(**changes))  # pylint: disable=protected-access

    def test_the_asset_type_is_inferred_from_the_file_and_must_agree_with_it(self):
        item = _candidate()
        search_commands._resolve_local_metadata(item, _args(file="/x/foto.jpg"))  # pylint: disable=protected-access
        self.assertEqual(("image", "image"), (item["asset_type"], item["media"]["kind"]))
        clip = _candidate()
        search_commands._resolve_local_metadata(clip, _args(file="/x/clip.mp4"))  # pylint: disable=protected-access
        self.assertEqual(("video", "video"), (clip["asset_type"], clip["media"]["kind"]))
        with self.assertRaisesRegex(ValueError, "asset-type não corresponde"):
            search_commands._resolve_local_metadata(
                _candidate(), _args(file="/x/clip.mp4", asset_type="web_screenshot")
            )  # pylint: disable=protected-access

    def test_title_and_capture_date_are_recorded_and_a_bad_date_is_refused(self):
        item = _candidate()
        search_commands._resolve_local_metadata(  # pylint: disable=protected-access
            item, _args(file="/x/clip.mp4", title="Foguete", captured_at="2026-03-04T05:06:07")
        )
        self.assertEqual(("Foguete", "2026-03-04T05:06:07"), (item["title"], item["captured_at"]))
        with self.assertRaises(ValueError):
            search_commands._resolve_local_metadata(  # pylint: disable=protected-access
                _candidate(), _args(file="/x/clip.mp4", captured_at="ontem")
            )


class SourceInfoAndShotTests(unittest.TestCase):
    def test_source_info_is_exclusive_to_a_local_file(self):
        item = _candidate()
        search_commands._resolve_source_info(item, _args())  # pylint: disable=protected-access
        self.assertEqual(_candidate(), item)
        with self.assertRaisesRegex(ValueError, "exclusivos de --file"):
            search_commands._resolve_source_info(_candidate(), _args(creator="Ana"))  # pylint: disable=protected-access

    def test_the_source_url_must_be_public_https_and_the_creator_is_recorded(self):
        with (
            patch("getbrolls.http.public_url", return_value=None),
            self.assertRaisesRegex(ValueError, "URL HTTPS pública"),
        ):
            search_commands._resolve_source_info(  # pylint: disable=protected-access
                _candidate(), _args(file="/x/a.mp4", source_url="http://user:pw@example.test/")
            )
        item = _candidate()
        with patch("getbrolls.http.public_url", return_value="https://example.test/a") as public:
            search_commands._resolve_source_info(  # pylint: disable=protected-access
                item, _args(file="/x/a.mp4", source_url="https://example.test/a", creator="Ana")
            )
        public.assert_called_once_with("https://example.test/a")
        self.assertEqual(("https://example.test/a", "Ana"), (item["source_url"], item["creator"]["name"]))

    def test_only_a_creator_needs_no_url(self):
        item = _candidate()
        search_commands._resolve_source_info(item, _args(file="/x/a.mp4", creator="Ana"))  # pylint: disable=protected-access
        self.assertEqual("Ana", item["creator"]["name"])
        self.assertNotIn("source_url", item)

    def test_the_shot_suffixes_the_id_and_a_bad_shot_is_refused(self):
        item = _candidate()
        search_commands._resolve_shot_suffix(item, _args())  # pylint: disable=protected-access
        self.assertEqual("c1", item["id"])
        search_commands._resolve_shot_suffix(item, _args(shot="beat_1-a"))  # pylint: disable=protected-access
        self.assertEqual(("c1:shot:beat_1-a", "beat_1-a"), (item["id"], item["shot"]))
        for bad in ("com espaço", "x" * 81, "a:b"):
            with self.subTest(shot=bad), self.assertRaisesRegex(ValueError, "--shot"):
                search_commands._resolve_shot_suffix(_candidate(), _args(shot=bad))  # pylint: disable=protected-access


if __name__ == "__main__":
    unittest.main()
