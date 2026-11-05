"""Offline CPU speaker diarization and conservative word/voice alignment.

The two ONNX files must already be installed locally. This module never imports
the transcription worker, downloads models, or infers participants' names/roles.
Sherpa API: https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/offline-speaker-diarization.py
"""
from __future__ import annotations

from bisect import bisect_right
import math
import os
from pathlib import Path
import sys

SAMPLE_RATE = 16000
# A word crossing an uncertain boundary stays intact and has no assigned voice.
AMBIGUOUS_OVERLAP_RATIO = 0.8


class Diarizer:
    """Reusable, lazily loaded pyannote + speaker-embedding ONNX pipeline.

    ``num_speakers=0`` lets the clusterer estimate the number of voices. A known
    count (e.g. 2) constrains clustering, not the participants' identities. Voice
    numbers are local to each recording and follow their first audible turn.
    """

    def __init__(self, model_dir: Path, num_speakers: int = 0):
        if not isinstance(num_speakers, int) or isinstance(num_speakers, bool) or num_speakers < 0:
            raise ValueError("Число голосов должно быть целым неотрицательным числом")
        self.model_dir = Path(model_dir)
        self.num_speakers = num_speakers
        self._runtime = None

    def _load(self):
        if self._runtime is not None:
            return self._runtime
        missing = [name for name in ("segmentation.onnx", "embedding.onnx")
                   if not (self.model_dir / name).is_file()]
        if missing:
            raise ValueError("Нет локальных моделей разделения голосов: " + ", ".join(missing)
                             + (". Заново распакуйте и скопируйте приложение целиком" if sys.platform == "darwin" else
                                ". Распакуйте весь архив программы, включая папку _internal"))
        import sherpa_onnx

        threads = min(4, os.cpu_count() or 2)
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(self.model_dir / "segmentation.onnx"), window_shift_ratio=0.1),
                num_threads=threads, provider="cpu"),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.model_dir / "embedding.onnx"),
                num_threads=threads, provider="cpu"),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=self.num_speakers if self.num_speakers else -1,
                threshold=0.5),
            min_duration_on=0.3, min_duration_off=0.5)
        if not config.validate():
            raise ValueError("Не удалось проверить локальные модели разделения голосов")
        runtime = sherpa_onnx.OfflineSpeakerDiarization(config)
        if runtime.sample_rate != SAMPLE_RATE:
            raise ValueError("Модель разделения голосов должна принимать звук 16 кГц")
        self._runtime = runtime
        return runtime

    def process(self, audio, report=None) -> list[dict]:
        """Accept mono 16 kHz float samples; return bounded, chronological turns.

        ``report`` receives monotonic progress in [0, 1]. The callback covers the
        native segmentation phase; final clustering may continue after its last
        chunk, so progress is held below 1 until the complete result is ready.
        """
        import numpy as np

        samples = np.asarray(audio, dtype=np.float32)
        if samples.ndim != 1:
            raise ValueError("Для разделения голосов нужен один звуковой канал")
        if not np.isfinite(samples).all():
            raise ValueError("В звуковых данных есть недопустимые значения")
        position = 0.0

        def notify(value):
            nonlocal position
            position = max(position, min(1.0, max(0.0, float(value))))
            if report is not None:
                report(position)

        notify(0)
        if not samples.size:
            notify(1)
            return []
        runtime = self._load()

        def callback(processed, total):
            if total:
                notify(0.95 * processed / total)
            return 0

        samples = np.ascontiguousarray(samples)
        try:
            native = runtime.process(samples, callback=callback)
        except TypeError as exc:
            # Older packaged Sherpa builds may expose only process(samples).
            if "unexpected keyword argument 'callback'" not in str(exc) and "incompatible function arguments" not in str(exc):
                raise
            native = runtime.process(samples)
        duration = samples.size / SAMPLE_RATE
        turns, names = [], {}
        for turn in native.sort_by_start_time():
            start, end = float(turn.start), float(turn.end)
            if not math.isfinite(start) or not math.isfinite(end) or turn.speaker < 0:
                continue
            start, end = max(0.0, start), min(duration, end)
            if end <= start:
                continue
            speaker = names.setdefault(turn.speaker, f"speaker_{len(names) + 1}")
            turns.append({"start": start, "end": end, "speaker": speaker})
        notify(1)
        return turns


def _bounds(entry: dict, fallback=(0.0, 0.0)) -> tuple[float, float]:
    """Keep malformed timestamps from placing text outside its source segment."""
    try:
        start, end = float(entry["start"]), float(entry["end"])
    except (KeyError, TypeError, ValueError):
        return fallback
    if not math.isfinite(start) or not math.isfinite(end):
        return fallback
    start = max(0.0, start)
    return start, max(start, end)


def _voice_intervals(turns: list[dict]) -> dict:
    """Index unions per voice so overlapping same-voice turns never double count."""
    by_voice = {}
    for turn in turns:
        speaker = turn.get("speaker")
        start, end = _bounds(turn)
        if speaker is not None and end > start:
            by_voice.setdefault(speaker, []).append((start, end))
    result = {}
    for speaker, intervals in by_voice.items():
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        result[speaker] = (merged, [interval[1] for interval in merged])
    return result


def _speaker_for(start: float, end: float, voices: dict):
    scores = []
    for speaker, (intervals, ends) in voices.items():
        index = bisect_right(ends, start)
        overlap = 0.0
        while index < len(intervals):
            left, right = intervals[index]
            if end == start:
                # Zero-length ASR words are retained as indivisible point events.
                overlap = 1.0 if left <= start < right else 0.0
                break
            if left >= end:
                break
            overlap += max(0.0, min(end, right) - max(start, left))
            index += 1
        if overlap > 0:
            scores.append((overlap, speaker))
    scores.sort(key=lambda item: item[0], reverse=True)
    if not scores or (len(scores) > 1 and scores[1][0] >= scores[0][0] * AMBIGUOUS_OVERLAP_RATIO):
        return None
    return scores[0][1]


def _word_text(words: list[dict]) -> str:
    tokens = [str(word.get("word", "")) for word in words]
    # Whisper supplies leading whitespace. Preserve punctuation and its spacing.
    if any(token[:1].isspace() for token in tokens):
        return "".join(tokens).strip()
    # Also support external word sources containing bare tokens.
    text = ""
    for token in tokens:
        if not token:
            continue
        if not text or token[0] in ".,!?;:…)]}»'’" or text[-1] in "([{«'’":
            text += token
        else:
            text += " " + token
    return text.strip()


def assign_speakers(segments: list[dict], turns: list[dict]) -> list[dict]:
    """Split ASR phrases at voice changes, preserving every atomic word once.

    Word timestamps are absolute. Each word goes to the voice with the greatest
    audio overlap; a second voice covering >=80% of the best overlap makes it
    uncertain. Unmatched words also use speaker=None, never a guessed role.
    Parent segment bounds keep ASR rounding from creating out-of-recording turns.
    Sources without word timestamps are kept intact as one uncertain-or-known
    phrase; no fictional per-word timing is generated.
    """
    voices = _voice_intervals(turns)
    entries = []
    for parent_index, segment in enumerate(segments):
        parent_start, parent_end = _bounds(segment)
        words = segment.get("words") or []
        if not words:
            speaker = _speaker_for(parent_start, parent_end, voices)
            entry = dict(segment, start=parent_start, end=parent_end, words=[],
                         speaker=speaker, uncertain=speaker is None)
            entries.append((parent_start, parent_index, None, entry))
            continue
        for word in words:
            start, end = _bounds(word, (parent_start, parent_end))
            start = min(parent_end, max(parent_start, start))
            end = min(parent_end, max(start, end))
            speaker = _speaker_for(start, end, voices)
            annotated = dict(word, start=start, end=end, speaker=speaker, uncertain=speaker is None)
            entries.append((start, parent_index, segment, annotated))
    # Python's stable sort keeps original order for words with equal timestamps.
    entries.sort(key=lambda entry: entry[0])
    result = []
    active_parent = None
    for _, parent_index, parent, entry in entries:
        if parent is None:
            result.append(entry)
            active_parent = None
            continue
        if (result and active_parent == parent_index and result[-1]["speaker"] == entry["speaker"]
                and entry["start"] - result[-1]["end"] <= 1.5):
            result[-1]["words"].append(entry)
            result[-1]["end"] = max(result[-1]["end"], entry["end"])
        else:
            turn = dict(parent, start=entry["start"], end=entry["end"], words=[entry],
                        speaker=entry["speaker"], uncertain=entry["uncertain"])
            result.append(turn)
            active_parent = parent_index
    for segment in result:
        if segment["words"]:
            segment["text"] = _word_text(segment["words"])
    return result
