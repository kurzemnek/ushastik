"""Windows integration checks against real audio and the actual worker process."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

p = argparse.ArgumentParser()
p.add_argument("--fixtures", type=Path, required=True)
p.add_argument("--work", type=Path, required=True)
p.add_argument("--exe", type=Path)
p.add_argument("--model", type=Path)
a = p.parse_args()
a.work.mkdir(parents=True, exist_ok=True)
root = Path(__file__).resolve().parent
environment = dict(os.environ, HF_HOME=str(a.work / "empty-hf"), HF_HUB_OFFLINE="1",
                   HF_HUB_DISABLE_TELEMETRY="1", HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1")
if not a.exe:
    # Block Python networking and supply a local model without developer cache fallback.
    launcher = a.work / "source_worker.py"
    launcher.write_text("import sys,socket\n"
        + "sys.path.insert(0," + repr(str(root)) + ")\n"
        + "sys._MEIPASS=" + repr(str(a.model.parent)) + "\n"
        + "def denied(*a,**k): raise RuntimeError('NETWORK FORBIDDEN IN ACCEPTANCE TEST')\n"
        + "socket.socket.connect=denied\n"
        + "from engine import run_job\nfrom pathlib import Path\n"
        + "raise SystemExit(run_job(Path(sys.argv[1])))\n", encoding="utf-8")
    command = [sys.executable, str(launcher)]
else:
    command = [str(a.exe), "--worker"]

def job(paths, split=False, cancel=False):
    directory = a.work / uuid.uuid4().hex
    directory.mkdir()
    output = directory / "Результаты звонков"
    config = {"id": uuid.uuid4().hex, "paths": [str(x) for x in paths], "output": str(output),
              "language": "ru", "split": split}
    (directory / "job.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    with (directory / "stderr.txt").open("w", encoding="utf-8") as err:
        child = subprocess.Popen(command + [str(directory / "job.json")], env=environment,
                                 stdout=err, stderr=err)
        start = time.monotonic()
        state = None
        while child.poll() is None:
            if time.monotonic() - start > 300:
                child.kill()
                raise AssertionError("Worker timed out")
            try:
                state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
                if cancel and state.get("phase") == "transcribing":
                    child.terminate()
                    child.wait(10)
                    assert not list(output.glob("*/transcript.json")), "Cancelled file was published as complete"
                    return {"cancelled": True, "elapsed": round(time.monotonic() - start, 2)}
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        assert child.returncode == 0, (directory, (directory / "stderr.txt").read_text())
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "finished", state
    return state

f = a.fixtures
mixed = job([f / "звонок тест.wav", f / "звонок тест.mp3", f / "звонок тест.m4a", f / "звонок тест.ogg",
             f / "тишина.wav", f / "короткий.wav", f / "битый.mp3",
             f / "same1" / "звонок.wav", f / "same2" / "звонок.wav"])
assert [i["status"] for i in mixed["items"]] == ["done"] * 4 + ["no_speech", "no_speech", "error", "done", "done"], mixed
paths = [i["output"] for i in mixed["items"] if i.get("output")]
assert len(paths) == len(set(paths))
for item in mixed["items"]:
    if item.get("output"):
        result = Path(item["output"])
        d = json.loads((result / "transcript.json").read_text(encoding="utf-8"))
        assert (result / "transcript.txt").is_file() and (result / "transcript.srt").is_file()
        for seg in d["segments"]:
            assert 0 <= seg["start"] < seg["end"] <= d["duration_seconds"] + 0.001
        if item["status"] == "done":
            text = " ".join(s["text"].lower() for s in d["segments"])
            assert "насос" in text and "анна" in text, text

stereo = job([f / "два канала.wav", f / "звонок тест.wav"], split=True)
assert [i["status"] for i in stereo["items"]] == ["done", "error"], stereo
sd = json.loads((Path(stereo["items"][0]["output"]) / "transcript.json").read_text(encoding="utf-8"))
left = " ".join(s["text"].lower() for s in sd["segments"] if s["channel"] == 1)
right = " ".join(s["text"].lower() for s in sd["segments"] if s["channel"] == 2)
assert "анна" in left and "насос" in right, (left, right)
boundary = job([f / "граница 5 минут.wav"])
bd = json.loads((Path(boundary["items"][0]["output"]) / "transcript.json").read_text(encoding="utf-8"))
assert any(s["start"] >= 300 for s in bd["segments"]), bd
assert min(s["start"] for s in bd["segments"]) > 290, bd
bt = " ".join(s["text"].lower() for s in bd["segments"])
assert "анна" in bt and "насос" in bt, bt
cancelled = job([f / "граница 5 минут.wav"], cancel=True)
receipt = {"passed": True, "windows_python": sys.version, "executable": str(a.exe) if a.exe else None,
           "network_policy": "empty HF cache, offline flags and unreachable proxies; source run additionally blocks socket.connect",
           "fixtures": "Synthetic Russian speech from Windows Irina, not real customer calls",
           "mixed": mixed, "stereo": stereo, "boundary": boundary, "cancelled": cancelled}
(a.work / "acceptance.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
print("PASS: formats, RU content, silence, corrupt-file continuation, unique names, stereo, mono rejection, chunk offsets, cancellation", flush=True)
