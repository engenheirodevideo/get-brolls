"""Work a repeated collection must not redo: the NASA N+1 and the interval-aware source cache."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from getbrolls import acquisition, providers, runtime
from getbrolls.acquisition import prepare_source
from getbrolls.ledger import Ledger
from getbrolls.models import candidate


def _search_page(n):
    return {
        "collection": {
            "items": [
                {
                    "data": [{"nasa_id": f"id{i}", "media_type": "video", "title": f"t{i}"}],
                    "links": [],
                }
                for i in range(n)
            ]
        }
    }


def _asset_response(ident):
    return {"collection": {"items": [{"href": f"https://images-assets.nasa.gov/video/{ident}/{ident}~orig.mp4"}]}}


def _search_page_with_invalid_lines(n, invalid_ratio=2):
    """Every `invalid_ratio`-th row is missing nasa_id / has the wrong media_type."""
    items = []
    for i in range(n):
        if i % invalid_ratio == 0:
            items.append({"data": [{"media_type": "image", "title": f"bad{i}"}], "links": []})
        else:
            items.append({
                "data": [{"nasa_id": f"id{i}", "media_type": "video", "title": f"t{i}"}],
                "links": [],
            })
    return {"collection": {"items": items}}


class Finding20NasaInvalidLinesTests(unittest.TestCase):
    def test_invalid_lines_are_skipped_and_result_len_equals_limit_and_asset_calls(self):
        calls = []

        def fake_get_json(url, params=None, headers=None, cache_ttl=0):
            calls.append(url)
            if "search" in url:
                return _search_page_with_invalid_lines(10)
            ident = url.rsplit("/", 1)[-1]
            return _asset_response(ident)

        with patch.object(providers, "get_json", side_effect=fake_get_json):
            items = providers._nasa("apollo", 3)

        self.assertEqual(len(items), 3)
        asset_calls = [c for c in calls if "asset" in c]
        self.assertEqual(len(asset_calls), 3)


class NasaSearchNPlusOneTests(unittest.TestCase):
    def test_stops_after_limit_valid_items_and_caches_asset_lookup(self):
        calls = []

        def fake_get_json(url, params=None, headers=None, cache_ttl=0):
            calls.append((url, cache_ttl))
            if "search" in url:
                return _search_page(10)
            ident = url.rsplit("/", 1)[-1]
            return _asset_response(ident)

        with patch.object(providers, "get_json", side_effect=fake_get_json):
            items = providers._nasa("apollo", 3)

        self.assertEqual(len(items), 3)
        asset_calls = [c for c in calls if "asset" in c[0]]
        # Exactly `limit` asset lookups, not one per row in the page (10 rows returned).
        self.assertEqual(len(asset_calls), 3)
        for _url, cache_ttl in asset_calls:
            self.assertEqual(cache_ttl, 86400)


class IntervalCacheIndexTests(unittest.TestCase):
    def _candidate(self):
        c = candidate('youtube', 'abc123', 'Title')
        c['acquisition']['method'] = 'yt-dlp'
        c['source_url'] = 'https://www.youtube.com/watch?v=abc123ABCDE'
        return c

    def test_second_preview_within_first_segment_reuses_without_downloading(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'proj'
            ledger = Ledger(project)
            c = self._candidate()

            def fake_download_segment(url, target, start, end):
                Path(target).write_bytes(b'fake-video-bytes')
                return target

            with patch('getbrolls.social.download_segment', side_effect=fake_download_segment), \
                 patch('getbrolls.acquisition.probe', return_value={'duration_s': 20.0, 'width': 640, 'height': 360, 'fps': 24}):
                prepare_source(ledger, c, 0, 20)

            index_path = ledger.root.parent / '.getbrolls-sources' / 'index.json'
            self.assertTrue(index_path.is_file())
            index = json.loads(index_path.read_text(encoding='utf-8'))
            self.assertEqual(len(index[c['id']]), 1)

            # A second candidate object (as if this were a fresh CLI process) asking for a
            # sub-range of the already-cached [0, 20] segment must reuse it via the index,
            # not just via the same candidate's own local_path (which a fresh process wouldn't have).
            c2 = self._candidate()
            with patch('getbrolls.social.download_segment') as download_mock, \
                 patch('getbrolls.acquisition.probe', return_value={'duration_s': 20.0, 'width': 640, 'height': 360, 'fps': 24}):
                prepare_source(ledger, c2, 5, 12)
            download_mock.assert_not_called()
            self.assertEqual(c2['local_start_s'], 0)

    def test_segment_outside_any_cached_interval_downloads_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'proj'
            ledger = Ledger(project)
            c = self._candidate()

            def fake_download_segment(url, target, start, end):
                Path(target).write_bytes(f'bytes-{start}-{end}'.encode())
                return target

            with patch('getbrolls.social.download_segment', side_effect=fake_download_segment), \
                 patch('getbrolls.acquisition.probe', return_value={'duration_s': 10.0, 'width': 640, 'height': 360, 'fps': 24}):
                prepare_source(ledger, c, 0, 10)

            c2 = self._candidate()
            with patch('getbrolls.social.download_segment', side_effect=fake_download_segment) as download_mock, \
                 patch('getbrolls.acquisition.probe', return_value={'duration_s': 10.0, 'width': 640, 'height': 360, 'fps': 24}):
                prepare_source(ledger, c2, 50, 60)
            download_mock.assert_called_once()


class Finding40SearchSummaryMentionsFailedSourcesTests(unittest.TestCase):
    def test_summary_line_appends_failed_source_count(self):
        from getbrolls import commands

        result = {
            "items": [{"id": "a"}],
            "errors": [{"provider": "pexels", "error": "boom"}, {"provider": "pixabay", "error": "boom2"}],
            "excluded_by_rules": 0,
        }
        line = commands.FLOW_SUMMARIES["search"](result)
        self.assertIn("2 fonte(s) falharam", line)

    def test_summary_line_omits_failed_suffix_when_no_errors(self):
        from getbrolls import commands

        result = {"items": [{"id": "a"}], "errors": [], "excluded_by_rules": 0}
        line = commands.FLOW_SUMMARIES["search"](result)
        self.assertNotIn("falharam", line)


class Finding6And37CoversGuardTests(unittest.TestCase):
    def test_covers_ignores_entry_missing_start_instead_of_raising(self):
        self.assertFalse(acquisition._covers({'duration': 5}, 0, 3))

    def test_covers_ignores_entry_missing_duration_instead_of_raising(self):
        self.assertFalse(acquisition._covers({'start': 0}, 0, 3))

    def test_reuse_from_index_skips_corrupted_entry_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / acquisition.INDEX_NAME).write_text(
                json.dumps({'cand-1': [{'duration': 5}, {'start': 'oops'}]}), encoding='utf-8'
            )
            # Must not raise KeyError/TypeError; a corrupted entry is simply not a match.
            self.assertIsNone(acquisition._reuse_from_index(cache, 'cand-1', 0, 3))


class Finding37StaleShaWarningTests(unittest.TestCase):
    def test_sha_mismatch_on_reuse_emits_warning_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            media_file = cache / 'stale.mp4'
            media_file.write_bytes(b'current bytes')
            index = {'cand-1': [{'path': str(media_file), 'sha': 'not-the-real-sha',
                                  'start': 0, 'duration': 10}]}
            (cache / acquisition.INDEX_NAME).write_text(json.dumps(index), encoding='utf-8')
            event = {'warnings': [], 'state_committed': False}
            token = runtime.ACTIVE.set(event)
            try:
                result = acquisition._reuse_from_index(cache, 'cand-1', 0, 5)
            finally:
                runtime.ACTIVE.reset(token)
        self.assertIsNone(result)
        self.assertTrue(any(w['code'] == 'SOURCE_CACHE_STALE' for w in event['warnings']))


class Finding21And26IndexRobustnessTests(unittest.TestCase):
    def test_missing_index_file_returns_empty_without_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(acquisition._load_index(Path(tmp)), {})

    def test_corrupted_index_emits_warning_and_renames_to_bad(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            index_path = cache / acquisition.INDEX_NAME
            index_path.write_text('not valid json {{{', encoding='utf-8')
            event = {'warnings': [], 'state_committed': False}
            token = runtime.ACTIVE.set(event)
            try:
                result = acquisition._load_index(cache)
            finally:
                runtime.ACTIVE.reset(token)
            self.assertEqual(result, {})
            self.assertTrue(any(w['code'] == 'SOURCE_INDEX_UNREADABLE' for w in event['warnings']))
            self.assertFalse(index_path.exists())
            self.assertTrue((cache / (acquisition.INDEX_NAME + '.bad')).exists())

    def test_covers_within_tolerance_but_not_beyond_it(self):
        entry = {'start': 0, 'duration': 10}
        # end - (start+duration) == 0.05 must still count as covered (existing tolerance).
        self.assertTrue(acquisition._covers(entry, 0, 10.05))
        # 0.06 beyond duration must NOT be treated as covered.
        self.assertFalse(acquisition._covers(entry, 0, 10.06))

    def test_save_index_uses_unique_tmp_file_not_a_fixed_name(self):
        # #62: a fixed tmp filename races under concurrent writers; mkstemp gives each
        # call its own unique path.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            acquisition._save_index(cache, {'a': []})
            leftover_tmp_files = [p for p in cache.iterdir() if p.suffix == '.tmp']
            self.assertEqual(leftover_tmp_files, [])

    def test_save_index_sets_restrictive_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            acquisition._save_index(cache, {'a': []})
            mode = stat.S_IMODE((cache / acquisition.INDEX_NAME).stat().st_mode)
            if os.name != 'nt':  # Windows não tem bits POSIX de permissão
                self.assertEqual(mode, 0o600)


if __name__ == '__main__':
    unittest.main()
