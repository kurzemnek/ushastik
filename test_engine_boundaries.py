"""Regressions for ASR timestamp drift at an actual engine window boundary."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from engine import save_result, transcribe_tracks
from diarization import assign_speakers


class Audio:
    def __getitem__(self, index):
        return None


class FakeModel:
    def __init__(self, windows):
        self.windows = iter(windows)

    def transcribe(self, *args, **kwargs):
        words = [NS(start=a, end=b, word=t) for a, b, t in next(self.windows)]
        segment = NS(start=min((w.start for w in words), default=0),
                     end=max((w.end for w in words), default=0) + 0.01,
                     words=words, text="".join(w.word for w in words))
        return (x for x in [segment] if words), NS(language="ru")


class WindowIntegration(unittest.TestCase):
    def run_windows(self, words):
        return transcribe_tracks(FakeModel(words), [Audio()], 301, "ru", False,
                                 lambda *_: None, keep_words=True)[0]

    def test_duplicate_owned_word_is_reconciled(self):
        result = self.run_windows([[(299.7, 300.1, " слово")], [(1.9, 2.3, " слово")]])
        self.assertEqual([w["word"] for s in result for w in s["words"]], [" слово"])

    def test_word_lost_by_both_midpoints_is_rescued(self):
        result = self.run_windows([[(299.9, 300.3, " слово")], [(1.7, 2.1, " слово")]])
        self.assertEqual([w["word"] for s in result for w in s["words"]], [" слово"])

    def test_separate_repeated_words_survive(self):
        result = self.run_windows([[(299.5, 299.7, " Да,")], [(2.1, 2.3, " да.")]])
        self.assertEqual([w["word"] for s in result for w in s["words"]], [" Да,", " да."])

    def test_zero_word_survives_as_positive_subtitle_after_diarization(self):
        result = self.run_windows([[(299.9, 299.9, " слово")], []])
        self.assertGreater(result[0]["end"], result[0]["start"])
        result = assign_speakers(result, [{"start": 299, "end": 301, "speaker": "speaker_1"}])
        with tempfile.TemporaryDirectory() as tmp:
            output = save_result(Path("test.wav"), Path(tmp), result, 301, "ru", False, 1, "test", turns=[])
            data = json.loads((output / "transcript.json").read_text())
            self.assertGreater(data["segments"][0]["end"], data["segments"][0]["start"])
            self.assertEqual(data["segments"][0]["words"][0]["start"], 299.9)
            self.assertEqual(data["segments"][0]["words"][0]["end"], 299.9)


if __name__ == "__main__":
    unittest.main()
