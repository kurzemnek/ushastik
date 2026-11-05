"""Regression checks for overlap drift and actual repeated spoken words."""
from copy import deepcopy
import unittest

from stitch import reconcile_words


def word(start, end, text, **metadata):
    return dict(start=start, end=end, word=text, **metadata)


def chunk(start, end, words, window_start=None, window_end=None):
    return dict(core_start=start, core_end=end, words=words,
                window_start=start if window_start is None else window_start,
                window_end=end if window_end is None else window_end)


def seam(left, right):
    return [chunk(0, 300, left, 0, 302), chunk(300, 600, right, 298, 600)]


class ReconciliationTests(unittest.TestCase):
    def test_same_word_owned_by_both_windows_is_emitted_once(self):
        result = reconcile_words(seam([word(299.7, 300.1, " слово")],
                                      [word(299.9, 300.3, " слово")]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["word"], " слово")

    def test_same_word_owned_by_neither_window_is_rescued(self):
        result = reconcile_words(seam([word(299.9, 300.3, " слово")],
                                      [word(299.7, 300.1, " слово")]))
        self.assertEqual(len(result), 1)

    def test_real_da_da_at_seam_keeps_both_utterances(self):
        left = [word(299.5, 299.7, " Да,"), word(299.95, 300.15, " да!")]
        right = [word(299.55, 299.75, " да"), word(300, 300.2, " да.")]
        result = reconcile_words(seam(left, right))
        self.assertEqual(len(result), 2)
        self.assertLess(result[0]["start"], result[1]["start"])

    def test_disjoint_real_da_da_from_different_cores_is_not_merged(self):
        left = word(299.5, 299.7, " Да,")
        right = word(300.1, 300.3, " да.")
        self.assertEqual(reconcile_words(seam([left], [right])), [left, right])

    def test_touching_real_repetitions_require_positive_temporal_overlap(self):
        left = word(299.8, 300, " Да,")
        right = word(300, 300.2, " да.")
        self.assertEqual(reconcile_words(seam([left], [right])), [left, right])

    def test_tiny_overlap_does_not_merge_distinct_utterances(self):
        left = word(299.7, 300.01, " Да,")
        right = word(300, 300.3, " да.")
        self.assertEqual(reconcile_words(seam([left], [right])), [left, right])

    def test_zero_duration_words_require_nearly_identical_timestamps(self):
        self.assertEqual(len(reconcile_words(seam([word(299.99, 299.99, "да")],
                                                  [word(300.01, 300.01, "да")]))), 1)
        left, right = word(299.98, 299.98, "да"), word(300.02, 300.02, "да")
        self.assertEqual(reconcile_words(seam([left], [right])), [left, right])

    def test_repetition_in_one_window_is_not_text_deduplicated(self):
        words = [word(1, 1.2, "да"), word(1.3, 1.5, "да"), word(2, 2.2, "да")]
        self.assertEqual(reconcile_words([chunk(0, 3, words)]), words)

    def test_repeated_words_align_by_nearest_timestamp(self):
        # The right hypothesis missed the first "да". Align its surviving word
        # with the second one, preserving the independently owned first word.
        left = [word(299.1, 299.3, " да", sequence=1),
                word(299.6, 299.8, " да", sequence=2)]
        right = [word(299.65, 299.85, " да", sequence=2)]
        result = reconcile_words(seam(left, right))
        self.assertEqual([w["sequence"] for w in result], [1, 2])

    def test_case_and_punctuation_are_normalized_metadata_and_times_survive(self):
        left = word(299.7, 300.1, " СЛОВО,", probability=0.9, _phrase="left")
        right = word(299.9, 300.3, " слово.", probability=0.7, _phrase="right")
        result = reconcile_words(seam([left], [right]))
        self.assertEqual(len(result), 1)
        self.assertIn(result[0], [left, right])
        self.assertEqual(set(result[0]), set(left))

    def test_unmatched_hypotheses_keep_only_their_core_words(self):
        left = [word(297, 297.2, " начало"), word(299.6, 299.8, " насос"),
                word(300.2, 300.4, " чужое")]
        right = [word(299.5, 299.7, " контекст"), word(300.1, 300.3, " клапан")]
        result = reconcile_words(seam(left, right))
        self.assertEqual([w["word"] for w in result], [" начало", " насос", " клапан"])

    def test_same_text_far_apart_is_not_a_duplicate(self):
        result = reconcile_words(seam([word(299, 299.2, "да")],
                                      [word(300.5, 300.7, "да")]))
        self.assertEqual(len(result), 2)

    def test_no_overlap_and_exact_final_endpoint_use_simple_ownership(self):
        chunks = [chunk(0, 2, [word(1.8, 2, "первый"), word(2, 2, "контекст")]),
                  chunk(2, 4, [word(2, 2, "второй"), word(4, 4, "!")])]
        self.assertEqual([w["word"] for w in reconcile_words(chunks)], ["первый", "второй", "!"])

    def test_three_windows_chain_one_physical_word_without_merging_repetitions(self):
        chunks = [chunk(0, 1, [word(.85, .95, "да"), word(1.2, 1.3, "да")], 0, 3),
                  chunk(1, 2, [word(.9, 1, "да"), word(1.25, 1.35, "да")], 0, 3),
                  chunk(2, 3, [word(.95, 1.05, "да"), word(1.3, 1.4, "да")], 0, 3)]
        self.assertEqual(len(reconcile_words(chunks)), 2)

    def test_empty_and_input_immutability(self):
        self.assertEqual(reconcile_words([]), [])
        self.assertEqual(reconcile_words([chunk(0, 3, [])]), [])
        chunks = seam([word(299.7, 300.1, "слово", metadata={"x": 1})],
                      [word(299.9, 300.3, "слово")])
        original = deepcopy(chunks)
        reconcile_words(chunks)
        self.assertEqual(chunks, original)

    def test_equal_timestamps_preserve_source_word_order(self):
        words = [word(1, 1, "раз", sequence=1), word(1, 1, "два", sequence=2)]
        self.assertEqual(reconcile_words([chunk(0, 2, words)]), words)


if __name__ == "__main__":
    unittest.main()
