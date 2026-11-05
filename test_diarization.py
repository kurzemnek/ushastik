"""Word-boundary and voice-identity checks; no model download is needed."""
from copy import deepcopy
from pathlib import Path
import unittest

from diarization import Diarizer, assign_speakers


def word(start, end, text, **metadata):
    return dict(start=start, end=end, word=text, **metadata)


def phrase(words):
    return dict(start=min(w["start"] for w in words), end=max(w["end"] for w in words),
                text="".join(w["word"] for w in words).strip(), words=words)


class AssignmentTests(unittest.TestCase):
    def test_word_crossing_boundary_is_atomic_and_uses_largest_overlap(self):
        words = [word(0, 0.5, "Добрый"), word(0.5, 1.5, " день!", probability=0.91),
                 word(1.5, 2, " Здравствуйте.")]
        result = assign_speakers([phrase(words)], [dict(start=0, end=1.2, speaker="speaker_1"),
                                                   dict(start=1.2, end=2, speaker="speaker_2")])
        self.assertEqual([r["speaker"] for r in result], ["speaker_1", "speaker_2"])
        self.assertEqual(result[0]["text"], "Добрый день!")
        self.assertEqual(result[0]["words"][1]["probability"], 0.91)
        self.assertEqual(result[0]["end"], 1.5)  # word is not cut at 1.2
        self.assertEqual([w["word"] for r in result for w in r["words"]], [w["word"] for w in words])

    def test_repeated_voice_keeps_identity(self):
        words = [word(0, 1, "Первый."), word(1, 2, " Второй."), word(2, 3, " Снова первый.")]
        turns = [dict(start=0, end=1, speaker="speaker_1"), dict(start=1, end=2, speaker="speaker_2"),
                 dict(start=2, end=3, speaker="speaker_1")]
        self.assertEqual([r["speaker"] for r in assign_speakers([phrase(words)], turns)],
                         ["speaker_1", "speaker_2", "speaker_1"])

    def test_simultaneous_voices_and_similar_boundary_are_uncertain(self):
        for a_end, b_start in [(1, 0), (0.52, 0.52)]:
            result = assign_speakers([phrase([word(0, 1, "Спорное слово.")])],
                                    [dict(start=0, end=a_end, speaker="speaker_1"),
                                     dict(start=b_start, end=1, speaker="speaker_2")])
            self.assertIsNone(result[0]["speaker"])
            self.assertTrue(result[0]["uncertain"])
            self.assertTrue(result[0]["words"][0]["uncertain"])

    def test_unknown_does_not_borrow_nearest_voice_and_no_turns_preserve_words(self):
        words = [word(0, 0.5, "Речь"), word(1, 1.5, " вне"), word(2, 2.5, " интервалов.")]
        source = [phrase(words)]
        original = deepcopy(source)
        result = assign_speakers(source, [dict(start=0, end=0.5, speaker="speaker_1")])
        self.assertEqual([r["speaker"] for r in result], ["speaker_1", None])
        self.assertEqual([w["word"] for r in result for w in r["words"]], [w["word"] for w in words])
        unknown = assign_speakers(source, [])
        self.assertTrue(all(r["speaker"] is None for r in unknown))
        self.assertEqual([w["word"] for r in unknown for w in r["words"]], [w["word"] for w in words])
        self.assertEqual(source, original)

    def test_same_voice_overlaps_are_not_double_counted(self):
        turns = [dict(start=0, end=0.6, speaker="speaker_1"),
                 dict(start=0, end=0.6, speaker="speaker_1"),
                 dict(start=0, end=0.9, speaker="speaker_2")]
        result = assign_speakers([phrase([word(0, 1, "Слово.")])], turns)
        self.assertEqual(result[0]["speaker"], "speaker_2")

    def test_stable_order_punctuation_and_metadata(self):
        words = [word(0, 0.1, "Добрый", sequence=1), word(0.1, 0.2, "день", sequence=2),
                 word(0.2, 0.3, ",", sequence=3), word(0.3, 0.4, "Анна", sequence=4),
                 word(0.4, 0.4, "!", sequence=5)]
        result = assign_speakers([phrase(words)], [dict(start=0, end=1, speaker="speaker_1")])
        self.assertEqual(result[0]["text"], "Добрый день, Анна!")
        self.assertEqual([w["sequence"] for r in result for w in r["words"]], [1, 2, 3, 4, 5])
        self.assertFalse(result[0]["uncertain"])

    def test_empty_and_non_word_source(self):
        self.assertEqual(assign_speakers([], []), [])
        source = [dict(start=0, end=1, text="Сохраняем весь текст.")]
        result = assign_speakers(source, [])
        self.assertEqual(result[0]["text"], source[0]["text"])
        self.assertIsNone(result[0]["speaker"])
        self.assertEqual(result[0]["words"], [])

    def test_clamp_rounded_words_to_parent_and_keep_zero_duration_word(self):
        source = [dict(start=0, end=1, text="Тест.",
                       words=[word(-0.001, 0.5, "Тест"), word(0.5, 1.001, ".")])]
        result = assign_speakers(source, [dict(start=0, end=1, speaker="speaker_1")])
        self.assertEqual(result[0]["start"], 0)
        self.assertEqual(result[0]["end"], 1)
        self.assertEqual(len(result[0]["words"]), 2)


class RuntimeBoundaryTests(unittest.TestCase):
    def test_invalid_speaker_count(self):
        for count in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                Diarizer(Path("missing"), count)

    def test_native_turns_stable_ids_bounds_and_callback(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy is installed by the application build")
        from types import SimpleNamespace

        class Native:
            def process(self, audio, callback=None):
                callback(1, 2)
                callback(2, 2)
                return self

            def sort_by_start_time(self):
                return [SimpleNamespace(start=-0.1, end=0.3, speaker=8),
                        SimpleNamespace(start=0.3, end=0.6, speaker=3),
                        SimpleNamespace(start=0.6, end=1.2, speaker=8)]

        diarizer = Diarizer(Path("missing"))
        diarizer._runtime = Native()
        progress = []
        turns = diarizer.process(np.zeros(16000, dtype=np.float32), progress.append)
        self.assertEqual([t["speaker"] for t in turns], ["speaker_1", "speaker_2", "speaker_1"])
        self.assertEqual((turns[0]["start"], turns[-1]["end"]), (0, 1))
        self.assertEqual(progress, sorted(progress))
        self.assertEqual((progress[0], progress[-1]), (0, 1))
        self.assertEqual(diarizer.process(np.zeros(0, dtype=np.float32)), [])
        with self.assertRaises(ValueError):
            diarizer.process(np.zeros((10, 2), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
