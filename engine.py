"""Offline transcription worker. No network operations or model downloads."""
from __future__ import annotations

import gc
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid

os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")

VERSION = "0.3.1"
SAMPLE_RATE = 16000
MAX_SECONDS = 90 * 60
EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".wma", ".mp4", ".webm", ".mkv"}
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
CHUNK_SECONDS = 300
CONTEXT_SECONDS = 2


def resources() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def model_path() -> Path:
    return resources() / "model"


def atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".writing")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.02)


def timestamp(seconds: float, srt: bool = False) -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}{',' if srt else '.'}{ms:03}"


def collect_paths(paths) -> list[Path]:
    found = {}
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        candidates = sorted(p.iterdir()) if p.is_dir() else [p]
        for f in candidates:
            if f.is_file() and f.suffix.lower() in EXTENSIONS:
                found[os.path.normcase(str(f))] = f
    return list(found.values())


def decode(path: Path, split: bool):
    """Bounded PCM decode; refuse more than 90 minutes even with absent metadata."""
    import av
    import numpy as np

    with av.open(str(path), metadata_errors="ignore") as container:
        if not container.streams.audio:
            raise ValueError("В файле нет звуковой дорожки")
        stream = container.streams.audio[0]
        channels = len(stream.codec_context.layout.channels)
        if split and channels != 2:
            raise ValueError("Режим двух каналов требует стереозапись. Выберите «Общий текст» для моно")
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else None
        if duration and duration > MAX_SECONDS + 1:
            raise ValueError("Запись длиннее 90 минут. Разделите её на несколько файлов")
        resampler = av.AudioResampler(format="s16", layout="stereo" if split else "mono", rate=SAMPLE_RATE)
        raw = bytearray()
        count = 0

        def append(frame):
            nonlocal count
            count += frame.samples
            if count > MAX_SECONDS * SAMPLE_RATE:
                raise ValueError("Запись длиннее 90 минут. Разделите её на несколько файлов")
            raw.extend(frame.to_ndarray().tobytes())

        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                append(converted)
        for converted in resampler.resample(None):
            append(converted)
    if not raw:
        raise ValueError("Звуковая дорожка пуста")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    audio *= 1.0 / 32768.0
    del raw
    if split:
        return [audio[0::2], audio[1::2]], count / SAMPLE_RATE
    return [audio], count / SAMPLE_RATE


def write_transcripts(folder: Path, data: dict) -> None:
    """Render all formats from the same structured transcript, including edited names."""
    names = data.get("speaker_names", {})
    mode = data.get("mode", "mixed")
    header = [f"Файл: {Path(data['source_file']).name}",
              f"Длительность: {timestamp(data['duration_seconds'])}",
              "Режим: " + {"channels": "два канала записи", "speakers": "по голосам", "mixed": "общий текст"}[mode], ""]
    text, subtitles = [], []
    for i, s in enumerate(data["segments"], 1):
        if mode == "speakers":
            who = names.get(s.get("speaker"), "Спикер не определён")
        elif mode == "channels":
            who = names.get(f"channel_{s['channel']}", f"Канал {s['channel']}")
        else:
            who = ""
        prefix = f"{who}: " if who else ""
        text.append(f"[{timestamp(s['start'])} — {timestamp(s['end'])}] {prefix}{s['text']}")
        subtitles.append(f"{i}\n{timestamp(s['start'], True)} --> {timestamp(s['end'], True)}\n{prefix}{s['text']}\n")
    if not data["segments"]:
        text.append("Речь не распознана. Проверьте запись на слух.")
    payloads = {
        "transcript.txt": ("\n\n".join(["\n".join(header)] + text) + "\n").encode("utf-8-sig"),
        "transcript.srt": "\n".join(subtitles).encode("utf-8-sig"),
        "transcript.json": (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }
    previous = {name: (folder / name).read_bytes() if (folder / name).exists() else None for name in payloads}
    written = []
    try:
        for name, payload in payloads.items():
            (folder / (name + ".writing")).write_bytes(payload)
        for name in payloads:
            os.replace(folder / (name + ".writing"), folder / name)
            written.append(name)
    except Exception:
        for name in written:
            if previous[name] is not None:
                (folder / name).write_bytes(previous[name])
            else:
                (folder / name).unlink(missing_ok=True)
        raise
    finally:
        for name in payloads:
            (folder / (name + ".writing")).unlink(missing_ok=True)


def rename_speakers(folder: Path, names: dict) -> None:
    data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    known = data.get("speaker_names", {})
    if set(names) != set(known):
        raise ValueError("Список спикеров изменился. Откройте окно имён заново")
    clean = {}
    for key, value in names.items():
        value = " ".join(value.split())
        if not value or len(value) > 80:
            raise ValueError("Укажите для каждого спикера имя длиной от 1 до 80 символов")
        clean[key] = value
    data["speaker_names"] = clean
    write_transcripts(folder, data)


def save_result(source: Path, output: Path, segments: list, duration: float, language: str,
                split: bool, elapsed: float, job_id: str, turns=None) -> Path:
    segments = [dict(s) for s in segments]
    for segment in segments:
        if segment["end"] <= segment["start"]:
            segment["end"] = min(duration, segment["start"] + 0.001)
            if segment["end"] <= segment["start"]:
                segment["start"] = max(0, segment["end"] - 0.001)
            segment["segment_time_adjusted"] = True
    output.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source.stem).strip(" .")[:65] or "запись"
    name = f"{safe_name}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}"
    staging = Path(tempfile.mkdtemp(prefix=f".monstr-{job_id}-", dir=output))
    try:
        status = "done" if segments else "no_speech"
        diarized = turns is not None
        ids = list(dict.fromkeys(s.get("speaker") for s in segments if s.get("speaker")))
        names = {k: f"Спикер {k.rsplit('_', 1)[-1]}" for k in ids} if diarized else ({f"channel_{i}": f"Канал {i}" for i in (1, 2)} if split else {})
        write_transcripts(staging, {
            "schema_version": 2, "app_version": VERSION, "status": status,
            "source_file": str(source), "duration_seconds": duration,
            "language": language, "mode": "speakers" if diarized else ("channels" if split else "mixed"),
            "engine": "faster-whisper", "model": "small", "compute_type": "int8",
            "elapsed_seconds": round(elapsed, 2), "segments": segments,
            "speaker_names": names, "speaker_turns": turns,
            "speaker_note": "Номера голосов относятся только к этой записи. Имена и роли задаются человеком" if diarized else None,
            "uncertain_word_count": sum(bool(w.get("uncertain")) for s in segments for w in s.get("words", [])),
            "diarization": "sherpa-onnx/pyannote3.0+3dspeaker-eres2netv2" if diarized else None,
            "channel_note": "Номера каналов записи; роли участников не определяются" if split else None,
        })
        target = output / name
        staging.rename(target)
        return target
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def transcribe_tracks(model, tracks, duration, language, split, report, keep_words=False):
    """Bound feature memory and reconcile repeated words in overlapping windows."""
    result, languages = [], []
    chunked = duration > CHUNK_SECONDS
    for channel, audio in enumerate(tracks, 1):
        chunks = []
        for core_start in range(0, max(1, int(duration + 0.999)), CHUNK_SECONDS):
            core_end = min(duration, core_start + CHUNK_SECONDS)
            left = max(0, core_start - CONTEXT_SECONDS) if chunked else 0
            right = min(duration, core_end + CONTEXT_SECONDS) if chunked else duration
            window = audio[int(left * SAMPLE_RATE):int(right * SAMPLE_RATE)]
            segments, info = model.transcribe(
                window, language=None if language == "auto" else language,
                beam_size=5, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False, word_timestamps=chunked or keep_words)
            languages.append(info.language)
            chunk_words = []
            with closing(segments):
                for phrase_number, s in enumerate(segments):
                    start, end = max(0.0, left + s.start), min(duration, left + s.end)
                    text = " ".join(s.text.split())
                    words = [{"start": round(max(0, left + w.start), 3),
                              "end": round(min(duration, left + w.end), 3),
                              "word": w.word} for w in (s.words or [])]
                    if chunked and s.words:
                        for word in words:
                            word["_phrase"] = (channel, core_start, phrase_number)
                        chunk_words.extend(words)
                    elif text and end > start and (not chunked or core_start <= (start + end) / 2 < core_end):
                        entry = {"start": round(start, 3), "end": round(end, 3), "text": text}
                        if keep_words:
                            entry["words"] = words
                        if split:
                            entry["channel"] = channel
                        result.append(entry)
                    report(((channel - 1) + min(1, (left + s.end) / duration)) / len(tracks), channel)
            if chunked:
                chunks.append({"core_start": core_start, "core_end": core_end, "window_start": left,
                               "window_end": right, "words": chunk_words})
            del segments, info, window
            report(((channel - 1) + core_end / duration) / len(tracks), channel)
        if chunked:
            from stitch import reconcile_words
            groups, last_phrase = [], None
            for raw in reconcile_words(chunks):
                word = {k: v for k, v in raw.items() if k != "_phrase"}
                phrase = raw.get("_phrase")
                if groups and phrase == last_phrase and word["start"] - groups[-1][-1]["end"] <= 1.5:
                    groups[-1].append(word)
                else:
                    groups.append([word])
                last_phrase = phrase
            for words in groups:
                entry = {"start": words[0]["start"], "end": max(w["end"] for w in words),
                         "text": " ".join("".join(w["word"] for w in words).split())}
                # Keep zero-length ASR words; subtitle containers still need a
                # positive interval. Original word timings remain untouched.
                if entry["end"] <= entry["start"]:
                    entry["end"] = min(duration, entry["start"] + 0.001)
                    if entry["end"] <= entry["start"]:
                        entry["start"] = max(0, entry["end"] - 0.001)
                if keep_words:
                    entry["words"] = words
                if split:
                    entry["channel"] = channel
                if entry["text"]:
                    result.append(entry)
    result.sort(key=lambda s: (s["start"], s.get("channel", 0), s["end"]))
    return result, ",".join(dict.fromkeys(languages))


def run_job(job_file: Path) -> int:
    job = json.loads(job_file.read_text(encoding="utf-8"))
    state_file = job_file.parent / "state.json"
    state = {"phase": "loading", "progress": 0, "items": [
        {"path": p, "status": "pending", "detail": "Ожидает"} for p in job["paths"]]}
    def publish(**kwargs):
        state.update(kwargs)
        atomic_json(state_file, state)
    try:
        publish(detail="Загружаю локальную модель Whisper…")
        mp = model_path()
        missing = [f for f in MODEL_FILES if not (mp / f).is_file()]
        if missing:
            raise ValueError("Нет локальной модели. Заново распакуйте и скопируйте приложение целиком" if sys.platform == "darwin" else
                             "Нет локальной модели. Распакуйте весь архив программы, включая папку _internal")
        from faster_whisper import WhisperModel
        model = WhisperModel(str(mp), device="cpu", compute_type="int8",
                             cpu_threads=min(4, os.cpu_count() or 2), num_workers=1,
                             local_files_only=True)
        diarizer = None
        diarize = bool(job.get("diarize", False))
        if diarize and job["split"]:
            raise ValueError("Выберите один способ разбивки: по голосам или по каналам")
        for index, item in enumerate(state["items"]):
            started = time.monotonic()
            source = Path(item["path"])
            tracks, result, turns = None, None, None
            try:
                item.update(status="running", detail="Читаю звук")
                publish(phase="decoding", current=index, progress=0, detail=f"Читаю: {source.name}")
                tracks, duration = decode(source, job["split"])
                def report(position, channel):
                    item.update(detail=f"Распознавание · {position:.0%}")
                    publish(phase="transcribing", detail=f"Распознаю: {source.name}" +
                            (f" · канал {channel}/2" if job["split"] else ""),
                            progress=min(65 if diarize else 98, int(position * (65 if diarize else 100))))
                publish(phase="transcribing", detail=f"Распознаю: {source.name}")
                result, language = transcribe_tracks(model, tracks, duration, job["language"], job["split"], report, keep_words=diarize)
                if diarize:
                    turns = []
                    if result:
                        from diarization import Diarizer, assign_speakers
                        publish(phase="diarizing", progress=65, detail=f"Разделяю голоса: {source.name}")
                        if diarizer is None:
                            diarizer = Diarizer(resources() / "speaker-model", int(job.get("num_speakers", 0)))
                        def speaker_progress(position):
                            publish(phase="diarizing", progress=min(98, 65 + int(position * 33)))
                        turns = diarizer.process(tracks[0], report=speaker_progress)
                        result = assign_speakers(result, turns)
                publish(phase="saving", progress=99, detail=f"Сохраняю: {source.name}")
                target = save_result(source, Path(job["output"]), result, duration, language,
                                     job["split"], time.monotonic() - started, job["id"], turns=turns)
                speaker_count = len({s.get("speaker") for s in result if s.get("speaker")})
                item.update(status="done" if result else "no_speech", output=str(target),
                            detail=(f"Готово · спикеров: {speaker_count}" if diarize else "Готово") if result else "Речь не распознана")
                publish(progress=100)
            except Exception as exc:
                if type(exc).__name__ in {"InvalidDataError", "EOFError"}:
                    message = "Не удалось прочитать звук. Проверьте, что файл целый и воспроизводится"
                elif isinstance(exc, PermissionError):
                    message = "Нет доступа к записи или папке результатов. Проверьте права и повторите"
                elif isinstance(exc, MemoryError):
                    message = "Не хватает оперативной памяти. Закройте другие программы или разделите запись"
                else:
                    message = str(exc)[:600]
                item.update(status="error", detail=message)
                publish(progress=0)
            finally:
                tracks, result, turns = None, None, None
                gc.collect()
        publish(phase="finished", detail="Обработка завершена")
        return 0
    except Exception as exc:
        publish(phase="fatal", detail=str(exc)[:1000])
        return 1


if __name__ == "__main__":
    raise SystemExit(run_job(Path(sys.argv[1])))
