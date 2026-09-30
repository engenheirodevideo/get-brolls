"""Schemas de análise (`analysis/`) e o validador estrito que lê esses arquivos."""

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT
from _schemas import close

from getbrolls import _paths as gb_paths
from getbrolls import analysis_contract as ac
from getbrolls import vocab
from getbrolls.sdk import schemas
from getbrolls.sdk.jsonschema import check_schema, errors

FIXTURES = ROOT / "tests" / "fixtures" / "analysis"
NAMES = (*ac.COMPONENTS, "analysis_index", "markers")
MEDIA_ID = "9f86d081884c7d65"


def example(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def published(name):
    return json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


def codes(found):
    return [item["code"] for item in found]


class PublishedSchemaTests(unittest.TestCase):
    def test_every_schema_is_in_the_subset_and_registered(self):
        manifest = (ROOT / "packaging" / "data_manifest.txt").read_text(encoding="utf-8").splitlines()
        for name in NAMES:
            with self.subTest(schema=name):
                check_schema(published(name))
                rel = f"schemas/{name}.schema.json"
                self.assertIn(rel, gb_paths.REQUIRED_DATA)
                self.assertIn(rel, manifest)
                self.assertIn(name, schemas.NAMES)
                self.assertEqual(published(name), schemas.load(name))

    def test_schema_const_is_the_namespaced_version(self):
        for name in NAMES:
            with self.subTest(schema=name):
                self.assertEqual({"const": f"getbrolls.{name}/1"}, published(name)["properties"]["schema"])

    def test_examples_pass_the_published_and_the_closed_variant(self):
        for name in NAMES:
            with self.subTest(schema=name):
                self.assertEqual([], errors(example(name), published(name)))
                self.assertEqual([], errors(example(name), close(published(name))))
                self.assertEqual([], ac.check_doc(example(name), name))

    def test_enums_come_from_the_shared_vocabulary(self):
        for name in ac.COMPONENTS:
            with self.subTest(schema=name):
                self.assertEqual(list(vocab.ANALYSIS_STATUSES), published(name)["properties"]["status"]["enum"])
        components = published("analysis_index")["properties"]["media"]["items"]["properties"]["components"]
        self.assertEqual(list(ac.COMPONENTS), list(components["properties"]))
        for node in components["properties"].values():
            self.assertEqual(list(vocab.ANALYSIS_STATUSES), node["enum"])
        marker = published("markers")["properties"]["markers"]["items"]["properties"]
        self.assertEqual(list(vocab.MARKER_COLORS), marker["color"]["enum"])
        self.assertEqual(list(ac.FAILURE_STATUSES), list(vocab.ANALYSIS_STATUSES_WITH_REASON))

    def test_role_enum_matches_the_media_roles(self):
        self.assertEqual(list(ac._MEDIA_ROLES), published("media")["properties"]["role"]["enum"])
        entry = published("analysis_index")["properties"]["media"]["items"]["properties"]
        self.assertEqual(list(ac._MEDIA_ROLES), entry["role"]["enum"])

    def test_minimal_components_carry_no_producer(self):
        for name in ("scenes", "silence", "speakers", "visual"):
            with self.subTest(schema=name):
                self.assertNotIn("producer", published(name)["properties"])
        for name in ("media", "transcript"):
            self.assertIn("producer", published(name)["required"])


class SchemaNameTests(unittest.TestCase):
    def test_known_names(self):
        self.assertEqual(("media", 1), ac.schema_name({"schema": "getbrolls.media/1"}))
        self.assertEqual(("analysis_index", 1), ac.schema_name({"schema": "getbrolls.analysis_index/1"}))

    def test_newer_version_is_refused(self):
        with self.assertRaises(ac.Invalid) as caught:
            ac.schema_name({"schema": "getbrolls.media/2"})
        self.assertEqual("NEWER_SCHEMA", caught.exception.code)

    def test_unknown_namespace_and_malformed_values(self):
        for value in ("nomade.media/1", "getbrolls.outro/1", "getbrolls.media/0", "getbrolls.media", 1, None):
            with self.subTest(value=value), self.assertRaises(ac.Invalid) as caught:
                ac.schema_name({"schema": value})
            self.assertEqual("UNKNOWN_SCHEMA", caught.exception.code)

    def test_check_doc_reports_the_schema_problem(self):
        doc = example("media")
        doc["schema"] = "getbrolls.media/2"
        self.assertEqual(["NEWER_SCHEMA"], codes(ac.check_doc(doc, "media")))
        self.assertEqual(["SCHEMA_MISMATCH"], codes(ac.check_doc(example("media"), "transcript")))


class CrossFieldTests(unittest.TestCase):
    def test_failure_status_needs_a_reason(self):
        for status in ac.FAILURE_STATUSES:
            doc = example("scenes")
            doc.update(status=status, scenes=[])
            with self.subTest(status=status):
                self.assertEqual(["MISSING_REASON"], codes(ac.check_doc(doc, "scenes")))
                doc["reason"] = "ffprobe ausente"
                self.assertEqual([], ac.check_doc(doc, "scenes"))

    def test_media_id_must_be_the_sha256_prefix(self):
        doc = example("media")
        doc["media_id"] = "0" * 16
        self.assertEqual(["IDENTITY_MISMATCH"], codes(ac.check_doc(doc, "media")))

    def test_word_count_must_match_the_words(self):
        doc = example("transcript")
        doc["word_count"] = 3
        self.assertEqual(["WORD_COUNT_MISMATCH"], codes(ac.check_doc(doc, "transcript")))

    def test_start_after_end_is_refused(self):
        doc = example("silence")
        doc["silences"][0].update(start=4.0, end=3.9)
        found = ac.check_doc(doc, "silence")
        self.assertEqual(["INVALID_INTERVAL"], codes(found))
        self.assertEqual("$.silences[0]", found[0]["where"])

    def test_nan_in_memory_is_refused(self):
        doc = example("transcript")
        doc["words"][0]["start"] = float("nan")
        self.assertIn("NONFINITE_NUMBER", codes(ac.check_doc(doc, "transcript")))

    def test_turn_speaker_must_be_declared(self):
        doc = example("speakers")
        doc["turns"][0]["speaker"] = "spk9"
        self.assertEqual(["UNKNOWN_SPEAKER"], codes(ac.check_doc(doc, "speakers")))

    def test_marker_ids_are_unique_and_open_end_is_valid(self):
        doc = example("markers")
        doc["markers"].append(copy.deepcopy(doc["markers"][0]))
        self.assertEqual(["DUPLICATE_ID"], codes(ac.check_doc(doc, "markers")))

    def test_index_paths_are_relative_and_unique(self):
        doc = example("analysis_index")
        doc["media"][0]["path"] = "../fora.mp4"
        self.assertEqual(["UNSAFE_PATH"], codes(ac.check_doc(doc, "analysis_index")))
        doc = example("analysis_index")
        doc["media"].append(copy.deepcopy(doc["media"][0]))
        self.assertEqual(["DUPLICATE_ID"], codes(ac.check_doc(doc, "analysis_index")))

    def test_bad_role_and_color_are_schema_violations(self):
        doc = example("media")
        doc["role"] = "voiceover"
        self.assertEqual(["SCHEMA_VIOLATION"], codes(ac.check_doc(doc, "media")))
        doc = example("markers")
        doc["markers"][0]["color"] = "green"
        self.assertEqual(["SCHEMA_VIOLATION"], codes(ac.check_doc(doc, "markers")))

    def test_secret_key_and_absolute_path_in_memory(self):
        doc = example("visual")
        doc["samples"][0]["api_key"] = "x"
        self.assertEqual(["SECRET_FIELD"], codes(ac.check_doc(doc, "visual")))
        for value in ("/Users/x", "C:\\x", "\\\\servidor\\x", "//servidor/x", "~/x"):
            doc = example("visual")
            doc["samples"][0]["description"] = value
            with self.subTest(value=value):
                self.assertEqual(["ABSOLUTE_PATH"], codes(ac.check_doc(doc, "visual")))

    def test_check_doc_refuses_a_non_object(self):
        self.assertEqual(["EXPECTED_OBJECT"], codes(ac.check_doc([], "media")))


class CheckTimesTests(unittest.TestCase):
    def test_accepts_intervals_points_and_open_ends(self):
        ac.check_times([{"start": 0, "end": 0.0}, {"start": 1.5, "end": None}, {"time": 2}], "$.x")

    def test_refuses_negative_bool_and_infinite(self):
        for item in ({"start": -1, "end": 1}, {"start": True, "end": 1}, {"time": float("inf")}, {"start": "1"}):
            with self.subTest(item=item), self.assertRaises(ac.Invalid) as caught:
                ac.check_times([item], "$.x")
            self.assertEqual("INVALID_TIME", caught.exception.code)


class ParseTests(unittest.TestCase):
    def assert_code(self, raw, code):
        with self.assertRaises(ac.Invalid) as caught:
            ac.parse(raw, "x.json")
        self.assertEqual(code, caught.exception.code)

    def test_valid_document(self):
        self.assertEqual({"a": [1, 2.5]}, ac.parse(b'{"a": [1, 2.5]}', "x.json"))

    def test_duplicate_key(self):
        self.assert_code(b'{"a": 1, "a": 2}', "DUPLICATE_KEY")

    def test_nan_and_infinity(self):
        self.assert_code(b'{"a": NaN}', "NONFINITE_NUMBER")
        self.assert_code(b'{"a": -Infinity}', "NONFINITE_NUMBER")

    def test_depth_limit(self):
        ok = b'{"a": ' + b"[" * 31 + b"]" * 31 + b"}"
        ac.parse(ok, "x.json")
        self.assert_code(b'{"a": ' + b"[" * 32 + b"]" * 32 + b"}", "TOO_DEEP")

    def test_secret_like_key_is_refused_not_scrubbed(self):
        self.assert_code(b'{"meta": {"api_key": "x"}}', "SECRET_FIELD")
        self.assert_code(b'{"Authorization": "x"}', "SECRET_FIELD")

    def test_absolute_paths(self):
        self.assert_code(b'{"p": "/Users/x"}', "ABSOLUTE_PATH")
        self.assert_code(b'{"p": "C:\\\\x"}', "ABSOLUTE_PATH")
        self.assert_code(b'{"p": ["\\\\\\\\host\\\\share"]}', "ABSOLUTE_PATH")

    def test_invalid_json_and_non_object(self):
        self.assert_code(b"{", "INVALID_JSON")
        self.assert_code(b"\xff", "INVALID_JSON")
        self.assert_code(b"[]", "EXPECTED_OBJECT")


class RelativeTests(unittest.TestCase):
    def test_safe_paths(self):
        self.assertEqual(("media", MEDIA_ID, "media.json"), ac.relative(f"media/{MEDIA_ID}/media.json"))

    def test_unsafe_paths(self):
        for value in ("", "/a", "a/../b", "./a", "a//b", "a\\b", "C:a", "a\x00", "a/", 3):
            with self.subTest(value=value), self.assertRaises(ac.Invalid) as caught:
                ac.relative(value)
            self.assertEqual("UNSAFE_PATH", caught.exception.code)


class ReadJsonTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-analysis-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.rel = f"media/{MEDIA_ID}/media.json"
        target = self.root / self.rel
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(example("media")), encoding="utf-8")

    def assert_code(self, rel, code):
        with self.assertRaises(ac.Invalid) as caught:
            ac.read_json(self.root, rel)
        self.assertEqual(code, caught.exception.code)

    def test_reads_a_valid_file(self):
        self.assertEqual(example("media"), ac.read_json(self.root, self.rel))

    def test_missing_file(self):
        self.assert_code(f"media/{MEDIA_ID}/transcript.json", "FILE_UNAVAILABLE")

    def test_symlinked_media_directory_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="gb-analysis-out-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "media.json").write_text(json.dumps(example("media")), encoding="utf-8")
        link_id = "0123456789abcdef"
        try:
            (self.root / "media" / link_id).symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink indisponível")
        self.assert_code(f"media/{link_id}/media.json", "UNSAFE_LINK")

    def test_symlinked_file_is_refused(self):
        link = self.root / "media" / MEDIA_ID / "transcript.json"
        try:
            link.symlink_to(self.root / self.rel)
        except OSError:
            self.skipTest("symlink indisponível")
        self.assert_code(f"media/{MEDIA_ID}/transcript.json", "UNSAFE_LINK")

    def test_file_over_16_mib_is_refused(self):
        big = self.root / "markers.json"
        with big.open("wb") as handle:
            handle.truncate(ac.MAX_FILE + 1)
        self.assert_code("markers.json", "FILE_TOO_LARGE")

    def test_directory_in_place_of_file(self):
        (self.root / "index.json").mkdir()
        self.assert_code("index.json", "NOT_REGULAR_FILE")

    def test_unsafe_relative_path(self):
        self.assert_code("../media.json", "UNSAFE_PATH")

    def test_content_goes_through_the_parser(self):
        (self.root / "markers.json").write_bytes(b'{"a": 1, "a": 1}')
        self.assert_code("markers.json", "DUPLICATE_KEY")


class ReadJsonWithoutDirFdTests(ReadJsonTests):
    """O mesmo contrato pelo caminho do Windows, onde `os.open` não aceita `dir_fd`."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(os, "supports_dir_fd", set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_uses_the_fallback(self):
        with mock.patch.object(ac, "_read_with_dir_fd", side_effect=AssertionError("dir_fd")):
            self.assertEqual(example("media"), ac.read_json(self.root, self.rel))

    def test_swapped_file_between_lstat_and_open_is_refused(self):
        real_open = os.open
        other = self.root / "other.json"
        other.write_text("{}", encoding="utf-8")

        def swapping_open(path, flags, *args, **kwargs):
            if str(path).endswith("media.json"):
                return real_open(other, flags, *args, **kwargs)
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(os, "open", swapping_open):
            self.assert_code(self.rel, "UNSAFE_LINK")


if __name__ == "__main__":
    unittest.main()
