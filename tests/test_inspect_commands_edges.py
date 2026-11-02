"""inspect_commands.py: origem das janelas, flags de --scan, teto das janelas e a preparação do trecho da varredura."""

import types
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import acquisition, inspect_commands, social

CONFIG = {"scan_max_seconds": 900}


def _candidate(**changes):
    item = {"provider": "local", "media": {}, "local_path": "/work/clip.mp4", "source_url": "https://example.test/v"}
    item.update(changes)
    return item


class WindowsSourceTests(unittest.TestCase):
    def test_the_richest_source_names_the_event(self):
        windows = inspect_commands._inspect_windows_source  # pylint: disable=protected-access
        self.assertEqual("subtitles", windows([{"source": "chapter"}, {"source": "subtitle"}]))
        self.assertEqual("chapters", windows([{"source": "description_timestamp"}, {"source": "chapter"}]))
        self.assertEqual("description", windows([{"source": "description_timestamp"}, None]))
        self.assertEqual("none", windows([]))
        self.assertEqual("none", windows([{"source": "fallback"}]))


class ScanFlagTests(unittest.TestCase):
    @staticmethod
    def _args(**changes):
        base = {"scan": True, "start": None, "end": None, "reference_only": False}
        base.update(changes)
        return types.SimpleNamespace(**base)

    def test_scan_alone_is_accepted_and_other_commands_are_not_checked(self):
        check = inspect_commands._validate_scan_flags  # pylint: disable=protected-access
        self.assertIsNone(check("preview", self._args()))
        self.assertIsNone(check("preview", self._args(scan=False, start=1.0)))
        self.assertIsNone(check("inspect", self._args(start=1.0, reference_only=True)))

    def test_scan_refuses_an_interval_and_reference_only(self):
        check = inspect_commands._validate_scan_flags  # pylint: disable=protected-access
        for changes in ({"start": 1.0}, {"end": 5.0}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "--start/--end"):
                check("preview", self._args(**changes))
        with self.assertRaisesRegex(ValueError, "--reference-only"):
            check("preview", self._args(reference_only=True))


class ClampWindowsTests(unittest.TestCase):
    def test_no_usable_cap_leaves_the_windows_untouched(self):
        for cap in (None, 0, 0.0, -5):
            with self.subTest(cap=cap):
                windows = [{"start_s": 0.0, "end_s": 500.0}]
                self.assertEqual([{"start_s": 0.0, "end_s": 500.0}], inspect_commands.clamp_windows(windows, cap))

    def test_a_window_over_the_cap_is_cut_at_its_end_only(self):
        windows = [{"start_s": 10.0, "end_s": 500.0}, {"start_s": 0.0, "end_s": 5.0}]
        self.assertEqual(
            [{"start_s": 10.0, "end_s": 40.0}, {"start_s": 0.0, "end_s": 5.0}],
            inspect_commands.clamp_windows(windows, 30.0),
        )


class MaxWindowsTests(unittest.TestCase):
    def test_a_count_outside_one_to_twenty_is_refused_before_anything_is_read(self):
        for count in (0, -1, 21):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "--max-windows entre 1 e 20"):
                inspect_commands.inspect_source(None, types.SimpleNamespace(max_windows=count))


class InspectTargetTests(unittest.TestCase):
    def test_a_candidate_that_is_a_still_image_has_nothing_to_inspect(self):
        item = {"id": "img_1", "media": {"kind": "image"}, "source_url": "https://example.test/i.jpg"}
        ledger = types.SimpleNamespace(get=lambda _candidate: item)
        with self.assertRaisesRegex(ValueError, "Imagem estática"):
            inspect_commands._inspect_target(ledger, types.SimpleNamespace(candidate="img_1"))  # pylint: disable=protected-access

    def test_a_url_that_resolves_to_a_still_image_has_nothing_to_inspect(self):
        resolved = {"media": {"kind": "image"}}
        with (
            patch("getbrolls.providers.resolve", return_value=resolved),
            self.assertRaisesRegex(ValueError, "imagem estática"),
        ):
            inspect_commands._inspect_target(  # pylint: disable=protected-access
                None, types.SimpleNamespace(candidate=None, url="https://example.test/i.jpg")
            )


class ScanDurationTests(unittest.TestCase):
    def test_a_known_or_local_duration_is_returned_without_a_probe(self):
        known = _candidate(provider="youtube", media={"duration_s": 61.0})
        local_unknown = _candidate()
        with patch.object(social, "probe_remote") as remote:
            self.assertEqual(61.0, inspect_commands._scan_known_duration(None, known))  # pylint: disable=protected-access
            self.assertIsNone(inspect_commands._scan_known_duration(None, local_unknown))  # pylint: disable=protected-access
        remote.assert_not_called()

    def test_a_remote_page_is_probed_and_the_duration_is_kept_on_the_candidate(self):
        item = _candidate(provider="youtube")
        ledger = types.SimpleNamespace(root=Path("/p/project/library"))
        with patch.object(social, "probe_remote", return_value={"duration_s": 42.0}) as remote:
            self.assertEqual(42.0, inspect_commands._scan_known_duration(ledger, item))  # pylint: disable=protected-access
        self.assertEqual("https://example.test/v", remote.call_args.args[0])
        self.assertEqual(42.0, item["media"]["duration_s"])

    def test_a_direct_file_is_probed_by_ffprobe_and_an_unknown_duration_is_not_stored(self):
        item = _candidate(provider="nasa")
        with (
            patch.object(acquisition, "direct_media", return_value=True),
            patch.object(inspect_commands, "probe_direct", return_value={"duration_s": 0}) as direct,
        ):
            self.assertEqual(0, inspect_commands._scan_known_duration(None, item))  # pylint: disable=protected-access
        direct.assert_called_once()
        self.assertEqual("scan", direct.call_args.kwargs["stage"])
        self.assertNotIn("duration_s", item["media"])


class ScanSpanTests(unittest.TestCase):
    @staticmethod
    def _span(item, duration=60.0):
        return inspect_commands._scan_prepare_span(None, item, CONFIG, duration)  # pylint: disable=protected-access

    def test_the_grid_follows_what_exists_in_the_working_file(self):
        item = _candidate(local_duration_s=10.0, local_start_s=-2)
        info = self._span(item)
        self.assertEqual(
            {"source": "/work/clip.mp4", "offset": -2, "local_start": 2, "span": 8.0, "duration": 60.0}, info
        )

    def test_the_scan_ceiling_caps_the_span(self):
        info = self._span(_candidate(local_duration_s=5000.0), duration=5000.0)
        self.assertEqual(900.0, info["span"])

    def test_the_working_file_is_probed_when_its_duration_was_not_recorded(self):
        with patch("getbrolls.media.probe", return_value={"duration_s": 20.0}) as probe:
            info = self._span(_candidate())
        probe.assert_called_once_with("/work/clip.mp4")
        self.assertEqual(20.0, info["span"])

    def test_an_unreadable_working_file_keeps_the_requested_span(self):
        with patch("getbrolls.media.probe", return_value={}):
            self.assertEqual(60.0, self._span(_candidate())["span"])

    def test_a_source_without_working_media_is_refused(self):
        with self.assertRaisesRegex(ValueError, "mídia de trabalho"):
            self._span(_candidate(local_path=None))

    def test_a_working_file_without_frames_is_refused(self):
        for item in (_candidate(local_duration_s=1.0, local_start_s=-5), _candidate(local_duration_s=0.0)):
            with self.subTest(item=item["local_duration_s"]), self.assertRaisesRegex(ValueError, "não tem quadros"):
                self._span(item, duration=0.0 if item["local_duration_s"] == 0.0 else 60.0)


if __name__ == "__main__":
    unittest.main()
