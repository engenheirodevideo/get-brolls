"""inspecting.py: legendas e descrições malformadas, janelas sem duração e o mapa de reserva."""

import unittest

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import inspecting


class MalformedCueTests(unittest.TestCase):
    def test_a_cue_without_speech_is_dropped_and_the_next_one_survives(self):
        text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n\n00:00:03.000 --> 00:00:04.000\nolá mundo\n"
        self.assertEqual([{"start_s": 3.0, "end_s": 4.0, "text": "olá mundo"}], inspecting.parse_vtt(text))

    def test_a_cue_that_ends_before_it_starts_is_dropped(self):
        text = "WEBVTT\n\n00:00:05.000 --> 00:00:02.000\nao contrário\n\n00:00:06.000 --> 00:00:07.000\nok\n"
        self.assertEqual(["ok"], [cue["text"] for cue in inspecting.parse_vtt(text)])

    def test_a_cue_made_only_of_markup_is_dropped(self):
        text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<c></c>\n"
        self.assertEqual([], inspecting.parse_vtt(text))

    def test_an_unparseable_stamp_gives_no_seconds(self):
        self.assertIsNone(inspecting._seconds("amanhã"))
        self.assertEqual(65.5, inspecting._seconds("1:05.5"))


class DescriptionTimestampTests(unittest.TestCase):
    def test_a_stamp_with_nothing_after_it_is_not_a_mark(self):
        marks = inspecting.description_timestamps("00:10 \n00:20 céu laranja\nsem tempo nenhum")
        self.assertEqual([{"start_s": 20.0, "text": "céu laranja"}], marks)


class CandidateWindowEdgeTests(unittest.TestCase):
    def test_a_chapter_without_a_start_is_skipped(self):
        probe = {
            "duration_s": 100.0,
            "chapters": [{"start_s": None, "end_s": 50.0, "title": "sem início"}, {"start_s": 10.0, "title": "poeira"}],
        }
        windows = inspecting.candidate_windows(probe, "poeira", 3)
        self.assertEqual(["poeira"], [w["text"] for w in windows])
        self.assertEqual(10.0, windows[0]["start_s"])
        self.assertEqual(10.0 + inspecting.DEFAULT_WINDOW_S, windows[0]["end_s"])

    def test_without_a_known_duration_windows_are_not_clipped(self):
        probe = {"duration_s": None, "chapters": [{"start_s": 500.0, "end_s": 900.0, "title": "final longo"}]}
        windows = inspecting.candidate_windows(probe, "final", 3)
        self.assertEqual([(500.0, 900.0)], [(w["start_s"], w["end_s"]) for w in windows])

    def test_with_a_known_duration_windows_are_clipped_and_empty_ones_dropped(self):
        probe = {
            "duration_s": 100.0,
            "chapters": [
                {"start_s": 90.0, "end_s": 400.0, "title": "tarde"},
                {"start_s": 300.0, "end_s": 400.0, "title": "depois do fim"},
            ],
        }
        windows = inspecting.candidate_windows(probe, "tarde", 3)
        self.assertEqual([(90.0, 100.0)], [(w["start_s"], w["end_s"]) for w in windows])


class FallbackWindowEdgeTests(unittest.TestCase):
    def test_the_first_language_with_cues_feeds_the_map_and_untitled_chapters_without_start_are_skipped(self):
        probe = {
            "duration_s": 100.0,
            "chapters": [{"start_s": None, "title": "sem início"}],
            "subtitles": {
                "en": {"cues": []},
                "pt": {"cues": [{"start_s": 1.0, "end_s": 2.0, "text": "primeira"}]},
                "es": {"cues": [{"start_s": 5.0, "end_s": 6.0, "text": "outra língua"}]},
            },
        }
        windows = inspecting.fallback_windows(probe, 3)
        self.assertEqual(["primeira"], [w["text"] for w in windows])
        self.assertEqual({"subtitle"}, {w["source"] for w in windows})

    def test_many_cues_are_spread_along_the_video_without_repeating(self):
        cues = [{"start_s": float(n), "end_s": n + 0.5, "text": f"fala {n}"} for n in range(10)]
        windows = inspecting.fallback_windows({"duration_s": 20.0, "subtitles": {"pt": {"cues": cues}}}, 3)
        self.assertEqual(["fala 0", "fala 3", "fala 6"], [w["text"] for w in windows])

    def test_evenly_keeps_everything_when_there_are_few_items_and_nothing_when_empty(self):
        self.assertEqual([1, 2], inspecting._evenly([1, 2], 3))
        self.assertEqual([], inspecting._evenly([], 3))

    def test_evenly_never_repeats_an_item(self):
        self.assertEqual(["a"], inspecting._evenly(["a", "a", "a", "a"], 2))


if __name__ == "__main__":
    unittest.main()
