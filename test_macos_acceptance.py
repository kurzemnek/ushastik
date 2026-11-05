"""Native macOS acceptance: frozen offline workers and real source Tk controls.

Run with the same Python 3.11 environment used to build the app. This must run
on a logged-in macOS runner; a headless or non-macOS run is an error, not a skip.
The GUI flow uses source widgets with the actual bundled worker. It does not
claim to automate mouse interactions inside the frozen application.
"""
from __future__ import annotations

import argparse
import errno
import inspect
import json
import os
from pathlib import Path
import platform
import plistlib
import signal
import socket
import subprocess
import sys
import time
import traceback
import uuid


REPO = Path(__file__).resolve().parent
SANDBOX = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)"]
JOB_TIMEOUT = 600


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def stop_child(child):
    if child.poll() is None:
        # Top-level launches own a process group. Killing a timed-out GUI also
        # stops its inherited worker group; workers stopped inside the GUI are
        # signalled individually so the acceptance process stays alive.
        def send(sig):
            try:
                if os.getpgid(child.pid) == child.pid:
                    os.killpg(child.pid, sig)
                else:
                    child.send_signal(sig)
            except ProcessLookupError:
                pass
        send(signal.SIGTERM)
        try:
            child.wait(8)
        except subprocess.TimeoutExpired:
            send(signal.SIGKILL)
            child.wait(8)


def offline_environment(work):
    cache = work / "empty-hf"
    cache.mkdir(parents=True, exist_ok=True)
    return dict(os.environ, HF_HOME=str(cache), HF_HUB_CACHE=str(cache / "hub"),
                HUGGINGFACE_HUB_CACHE=str(cache / "hub"),
                TRANSFORMERS_CACHE=str(cache / "transformers"),
                HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
                HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1",
                ALL_PROXY="http://127.0.0.1:1", NO_PROXY="",
                http_proxy="http://127.0.0.1:1", https_proxy="http://127.0.0.1:1",
                all_proxy="http://127.0.0.1:1", no_proxy="")


def command(args, *, timeout=30, **kwargs):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, **kwargs)
    assert result.returncode == 0, f"Command failed: {args!r}\n{result.stdout[-6000:]}"
    return result.stdout.strip()


def network_proof(work, env):
    # A real local listener makes timeout, DNS failure, or a dead endpoint an
    # insufficient explanation for the sandboxed probe's EPERM/EACCES.
    probe = (
        "import errno,json,socket,sys\n"
        "s=socket.socket();s.settimeout(2)\n"
        "try:\n s.connect(('127.0.0.1',int(sys.argv[1])))\n"
        "except OSError as e:\n print(json.dumps({'connected':False,'errno':e.errno}));"
        "sys.exit(0 if e.errno in (errno.EPERM,errno.EACCES) else 3)\n"
        "else:\n print(json.dumps({'connected':True}));sys.exit(0)\n"
        "finally:\n s.close()\n"
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        port = str(listener.getsockname()[1])
        control = json.loads(command([sys.executable, "-c", probe, port], env=env))
        assert control["connected"] is True, control
        denied = json.loads(command(SANDBOX + [sys.executable, "-c", probe, port], env=env))
    assert denied["connected"] is False and denied["errno"] in (errno.EPERM, errno.EACCES), denied
    proof = {"control": control, "sandbox": denied, "profile": SANDBOX[-1],
             "scope": "The same macOS network-deny sandbox wraps every tested frozen worker."}
    write_json(work / "network-proof.json", proof)
    return proof


def run_job(binary, work, env, paths, *, diarize=False, split=False):
    directory = work / ("job-" + uuid.uuid4().hex[:10])
    directory.mkdir()
    config = {"id": uuid.uuid4().hex, "paths": list(map(str, paths)), "language": "ru",
              "split": split, "diarize": diarize, "num_speakers": 2,
              "output": str(directory / "Результаты звонков")}
    write_json(directory / "job.json", config)
    started = time.monotonic()
    with (directory / "worker.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(SANDBOX + [str(binary), "--worker", str(directory / "job.json")],
                                 env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True)
        try:
            child.wait(JOB_TIMEOUT)
        finally:
            stop_child(child)
    assert child.returncode == 0, (directory / "worker.log").read_text(encoding="utf-8")[-6000:]
    state = read_json(directory / "state.json")
    assert state["phase"] == "finished", state
    assert not list(Path(config["output"]).glob(".monstr-*")), "Published job left staging files"
    return {"directory": str(directory), "seconds": round(time.monotonic() - started, 2), "state": state}


def result_data(item):
    folder = Path(item["output"])
    assert all((folder / ("transcript." + ext)).is_file() for ext in ("json", "txt", "srt")), folder
    data = read_json(folder / "transcript.json")
    for segment in data["segments"]:
        assert 0 <= segment["start"] < segment["end"] <= data["duration_seconds"] + .001, segment
    if data["segments"]:
        assert " --> " in (folder / "transcript.srt").read_text(encoding="utf-8-sig")
    return data


def combined_text(data):
    return " ".join(s["text"].casefold() for s in data["segments"])


def check_rename(folder, before, names):
    from engine import rename_speakers
    rename_speakers(folder, names)
    after = read_json(folder / "transcript.json")
    assert after["speaker_names"] == names, after["speaker_names"]
    assert {k: v for k, v in before.items() if k != "speaker_names"} == {
        k: v for k, v in after.items() if k != "speaker_names"}, "Rename changed speech or timing"
    for ext in ("txt", "srt"):
        text = (folder / ("transcript." + ext)).read_text(encoding="utf-8-sig")
        assert all(name in text for name in names.values()), (ext, names)
    return after


def backend_checks(binary, work, fixtures, env):
    dialog = run_job(binary, work, env, [fixtures / "диалог 2 голоса.wav"], diarize=True)
    assert [i["status"] for i in dialog["state"]["items"]] == ["done"], dialog
    item = dialog["state"]["items"][0]
    data = result_data(item)
    assert data["mode"] == "speakers" and len(data["speaker_names"]) == 2, data["speaker_names"]
    text = combined_text(data)
    assert "анна" in text and "алексей" in text and "насос" in text, text
    voices = {s.get("speaker") for s in data["segments"] if s.get("speaker")}
    assert voices == set(data["speaker_names"]), voices
    assert all(s.get("words") for s in data["segments"]), "Diarization lost word metadata"
    check_rename(Path(item["output"]), data,
                 dict(zip(data["speaker_names"], ("Менеджер Анна", "Клиент Алексей"))))

    plain = run_job(binary, work, env, [fixtures / "тишина.wav", fixtures / "битый.mp3",
                                      fixtures / "звонок тест.wav"])
    assert [i["status"] for i in plain["state"]["items"]] == ["no_speech", "error", "done"], plain
    assert result_data(plain["state"]["items"][0])["segments"] == []
    pd = result_data(plain["state"]["items"][2])
    assert pd["mode"] == "mixed" and not pd["speaker_names"], pd
    assert "анна" in combined_text(pd) and "насос" in combined_text(pd), combined_text(pd)
    assert not plain["state"]["items"][1].get("output"), "Corrupt input published a transcript"

    stereo = run_job(binary, work, env, [fixtures / "два канала.wav"], split=True)
    assert [i["status"] for i in stereo["state"]["items"]] == ["done"], stereo
    sd = result_data(stereo["state"]["items"][0])
    left = " ".join(s["text"].casefold() for s in sd["segments"] if s["channel"] == 1)
    right = " ".join(s["text"].casefold() for s in sd["segments"] if s["channel"] == 2)
    assert sd["mode"] == "channels" and "анна" in left and "насос" in right, (left, right)
    return {"diarized": dialog, "plain_queue": plain, "stereo": stereo,
            "rename": "TXT/SRT/JSON updated; all other JSON fields, including words/times, unchanged"}


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def gui_checks(args):
    import tkinter as tk
    from tkinter import messagebox
    from tkinterdnd2 import TkinterDnD
    import app
    import information

    real_factory, real_popen = TkinterDnD.Tk, subprocess.Popen
    workers, logs, screenshots, layout, state = [], [], {}, {}, {}
    env = offline_environment(args.work)
    information.preferences_path = lambda: args.work / "preferences" / "ui.json"

    def popen(cmd, *positional, **kwargs):
        # Redirect only the source application's worker launch, never unrelated
        # subprocesses or the GUI itself. The native binary/model are exercised.
        if isinstance(cmd, (list, tuple)) and "--worker" in cmd and str(REPO / "app.py") in cmd:
            manifest = cmd[cmd.index("--worker") + 1]
            log = (args.work / f"gui-worker-{len(workers) + 1}.log").open("w", encoding="utf-8")
            logs.append(log)
            kwargs.update(env=env, stdout=log, stderr=log)
            child = real_popen(SANDBOX + [str(args.binary), "--worker", manifest], *positional, **kwargs)
            workers.append(child)
            return child
        return real_popen(cmd, *positional, **kwargs)

    subprocess.Popen = popen

    def factory(*positional, **kwargs):
        root = real_factory(*positional, **kwargs)
        state["root"] = root

        def mainloop(*unused):
            # The live gui frame owns the real nested callbacks and Tk widgets.
            frame = inspect.currentframe().f_back
            def live():
                return frame.f_locals

            def pump_until(predicate, timeout=15, description="Tk condition"):
                deadline = time.monotonic() + timeout
                while True:
                    root.update()
                    assert not callback_errors, "\n".join(callback_errors)
                    if predicate():
                        return
                    assert time.monotonic() < deadline, f"Timed out: {description}"
                    time.sleep(.025)

            def button(parent, text):
                matches = [w for w in descendants(parent)
                           if isinstance(w, tk.Button) and w.cget("text") == text]
                assert len(matches) == 1, (text, matches)
                return matches[0]

            def screenshot(name):
                target = args.work / (name + ".png")
                try:
                    result = subprocess.run(["/usr/sbin/screencapture", "-x", str(target)],
                                            capture_output=True, text=True, timeout=15)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    screenshots[name] = {"unavailable": str(exc)}
                    return
                screenshots[name] = str(target) if result.returncode == 0 and target.is_file() else {
                    "unavailable": result.stderr[-600:] or "No screenshot file; screen-recording permission may be absent"}

            def check_dialog(dialog, name):
                root.update()
                bounds = (dialog.winfo_width(), dialog.winfo_height())
                assert bounds[0] <= root.winfo_screenwidth() and bounds[1] <= root.winfo_screenheight(), bounds
                clipped = []
                for widget in descendants(dialog):
                    if not widget.winfo_ismapped():
                        continue
                    x = widget.winfo_rootx() - dialog.winfo_rootx()
                    y = widget.winfo_rooty() - dialog.winfo_rooty()
                    if x < -3 or y < -3 or x + widget.winfo_width() > bounds[0] + 3 or y + widget.winfo_height() > bounds[1] + 3:
                        clipped.append(str(widget))
                    if isinstance(widget, (tk.Label, tk.Button)) and widget.winfo_reqheight() > widget.winfo_height() + 3:
                        clipped.append(str(widget) + ":height")
                assert not clipped, (name, clipped)
                layout[name] = {"width": bounds[0], "height": bounds[1], "clipped": clipped}
                screenshot(name)

            callback_errors = []
            root.report_callback_exception = lambda *exc: callback_errors.append("".join(traceback.format_exception(*exc)))
            ui = live()
            root.update()
            check_dialog(root, "main")
            assert root.winfo_width() <= root.winfo_screenwidth() - 32
            assert root.winfo_height() <= root.winfo_screenheight() - 100
            ui["output"].set(str(args.work / "GUI результаты"))
            pump_until(lambda: ui["information"].notice_window is not None,
                       description="first-launch local notice")
            notice = ui["information"].notice_window
            check_dialog(notice, "notice")
            button(notice, "Понятно").invoke()
            assert read_json(information.preferences_path())["local_notice_seen"] is True
            ui["information"].show_notice_if_needed()
            assert not notice.winfo_exists(), "Acknowledged notice was displayed again"

            required = ("::tk::mac::OpenDocument", "::tk::mac::Quit", "::tk::mac::ReopenApplication", "tkAboutDialog")
            for callback in required:
                assert root.tk.call("info", "commands", callback), f"Missing native macOS callback {callback}"
            root.tk.call("tkAboutDialog")
            about = ui["information"].about_window
            assert about is not None and about.winfo_exists()
            check_dialog(about, "about")
            button(about, "Закрыть").invoke()
            root.tk.call("::tk::mac::ReopenApplication")
            root.tk.call("::tk::mac::OpenDocument", str(args.fixtures / "звонок тест.wav"))
            assert len(ui["items"]) == 1, "OpenDocument did not add the Cyrillic filename"

            # Dispatch through TkinterDnD's registered Tcl handler with its exact
            # substitution order. This checks DND parsing, not a physical drag.
            drop = ui["drop"]
            registered = [c for c in drop._tclCommands if c.endswith("drop_files")]
            assert len(registered) == 1, registered
            placeholders = tuple(p.strip("{}") for p in drop._subst_format_dnd)
            values = {"%A": "copy", "%a": ("copy",), "%b": "1", "%C": ("DND_Files",),
                      "%c": ("DND_Files",), "%CST": ("DND_Files",), "%CTT": ("DND_Files",),
                      "%D": (str(args.fixtures / "диалог 2 голоса.wav"),), "%e": "<<Drop>>",
                      "%L": ("DND_Files",), "%m": "0", "%ST": ("DND_Files",),
                      "%T": "DND_Files", "%t": "DND_Files", "%TT": ("DND_Files",),
                      "%W": str(drop), "%X": "10", "%Y": "10"}
            assert set(placeholders) <= values.keys(), placeholders
            root.tk.call(registered[0], *(values[p] for p in placeholders))
            assert len(ui["items"]) == 2, "Tcl DND callback did not add the Cyrillic filename"
            ui["clear_button"].invoke()
            root.tk.call("::tk::mac::OpenDocument", str(args.fixtures / "диалог 2 голоса.wav"))
            ui["start_button"].invoke()
            pump_until(lambda: live()["process"] is not None, description="frozen worker start")

            def transcribing():
                try:
                    return read_json(live()["job_dir"] / "state.json")["phase"] == "transcribing"
                except (OSError, ValueError, TypeError):
                    return False

            pump_until(transcribing, JOB_TIMEOUT, "worker enters transcription before stop")
            ui["stop_button"].invoke()
            pump_until(lambda: live()["process"] is None, 20, "stopped worker reaped")
            assert next(iter(ui["items"].values()))["status"] == "cancelled", ui["items"]
            assert not list(Path(ui["output"].get()).glob("*/transcript.json")), "Stopped input published as complete"
            assert not list(Path(ui["output"].get()).glob(".monstr-*")), "Stop left staging output"

            ui["start_button"].invoke()
            pump_until(lambda: live()["process"] is None, JOB_TIMEOUT, "retry completed")
            item = next(iter(ui["items"].values()))
            assert item["status"] == "done", item
            ui["tree"].selection_set(item["iid"])
            ui["tree"].event_generate("<<TreeviewSelect>>")
            root.update()
            preview = ui["transcript"].get("1.0", "end")
            assert "насос" in preview.casefold() and "Спикер" in preview, preview
            assert ui["rename_button"].cget("state") == "normal"
            before = result_data(item)
            ui["rename_button"].invoke()
            root.update()
            dialogs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()
                       and w.title() == "Имена участников · Ушастик"]
            assert len(dialogs) == 1, dialogs
            dialog = dialogs[0]
            fields = [w for w in descendants(dialog) if isinstance(w, tk.Entry)]
            names = ("Менеджер Анна", "Клиент Алексей")
            assert len(fields) == len(names)
            for field, name in zip(fields, names):
                field.delete(0, "end")
                field.insert(0, name)
            button(dialog, "Сохранить имена").invoke()
            after = read_json(Path(item["output"]) / "transcript.json")
            assert list(after["speaker_names"].values()) == list(names)
            assert {k: v for k, v in before.items() if k != "speaker_names"} == {
                k: v for k, v in after.items() if k != "speaker_names"}
            for ext in ("txt", "srt"):
                assert all(name in (Path(item["output"]) / ("transcript." + ext)).read_text(encoding="utf-8-sig") for name in names)
            ui["copy_button"].invoke()
            root.update()
            expected = (Path(item["output"]) / "transcript.txt").read_text(encoding="utf-8-sig")
            assert root.clipboard_get() == expected, "Clipboard differs from saved TXT"
            assert all(name in ui["transcript"].get("1.0", "end") for name in names)
            screenshot("conversation")
            assert not callback_errors, "\n".join(callback_errors)
            assert all(child.poll() is not None for child in workers), "GUI left a worker alive"
            state["result"] = {"passed": True, "native_callbacks": list(required), "layout": layout,
                               "screenshots": screenshots, "workers": len(workers),
                               "checks": ["notice acknowledgement", "About", "OpenDocument", "Tcl DND dispatch",
                                          "start", "stop", "retry", "preview", "rename", "clipboard", "native Quit"],
                               "scope": "Real source Tk controls; every worker is the frozen app in macOS network-deny sandbox; DND is registered Tcl-handler dispatch."}
            root.tk.call("::tk::mac::Quit")
            try:
                exists = root.winfo_exists()
            except tk.TclError:
                exists = False  # Tk removed its window commands after destroy.
            assert not exists, "Native Quit did not destroy the GUI"

        root.mainloop = mainloop
        return root

    TkinterDnD.Tk = factory
    # Unexpected modal errors must fail promptly instead of hanging the runner.
    def unexpected_dialog(title, message, **kwargs):
        raise AssertionError(f"Unexpected GUI dialog: {title}: {message}")
    messagebox.showerror = unexpected_dialog
    messagebox.askyesno = unexpected_dialog
    try:
        app.gui()
        return state["result"]
    finally:
        for child in workers:
            stop_child(child)
        for log in logs:
            log.close()
        root = state.get("root")
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        TkinterDnD.Tk, subprocess.Popen = real_factory, real_popen


def frozen_gui_smoke(args, env):
    # This is a separate frozen-GUI startup/AppleEvent quit smoke. Detailed
    # widget automation above remains explicitly a source-GUI test.
    log_path = args.work / "frozen-gui.log"
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen(SANDBOX + [str(args.binary)], env=env,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True)
        try:
            time.sleep(4)
            assert child.poll() is None, log_path.read_text(encoding="utf-8")[-6000:]
            apple_path = str(args.app).replace("\\", "\\\\").replace('"', '\\"')
            quit_output = command(["/usr/bin/osascript", "-e", f'tell application "{apple_path}" to quit'], timeout=20)
            child.wait(20)
            assert child.returncode == 0, (child.returncode, log_path.read_text(encoding="utf-8")[-6000:])
            return {"passed": True, "launch": "bundled Contents/MacOS/Ushastik in network-deny sandbox",
                    "quit": "macOS AppleEvent to this .app path", "output": quit_output,
                    "scope": "Startup and native Quit; detailed widget actions use source GUI with frozen workers."}
        finally:
            stop_child(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, default=REPO / "fixtures")
    parser.add_argument("--gui-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.app, args.work, args.fixtures = (p.resolve() for p in (args.app, args.work, args.fixtures))
    args.work.mkdir(parents=True, exist_ok=True)
    args.binary = args.app / "Contents" / "MacOS" / "Ushastik"
    receipt_path = args.work / ("gui-acceptance.json" if args.gui_child else "acceptance.json")
    receipt = {"passed": False, "platform": platform.platform(), "machine": platform.machine(),
               "python": sys.version, "app": str(args.app), "work": str(args.work)}
    try:
        assert sys.platform == "darwin", "Native macOS is required; Linux cross-tests cannot pass Mac acceptance"
        assert sys.version_info[:2] == (3, 11), "Run with the build environment's Python 3.11"
        assert args.binary.is_file(), args.binary
        if args.gui_child:
            receipt.update(gui_checks(args))
        else:
            for name in ("диалог 2 голоса.wav", "тишина.wav", "битый.mp3", "два канала.wav", "звонок тест.wav"):
                assert (args.fixtures / name).is_file(), args.fixtures / name
            resources = args.app / "Contents" / "Resources"
            assert (resources / "model" / "model.bin").is_file(), "Whisper model absent from .app"
            assert (resources / "speaker-model" / "segmentation.onnx").is_file(), "Segmentation model absent"
            assert (resources / "speaker-model" / "embedding.onnx").is_file(), "Embedding model absent"
            receipt["sw_vers"] = command(["/usr/bin/sw_vers"])
            receipt["codesign"] = command(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(args.app)])
            receipt["binary_file"] = command(["/usr/bin/file", str(args.binary)])
            receipt["architectures"] = command(["/usr/bin/lipo", "-archs", str(args.binary)]).split()
            assert platform.machine() in receipt["architectures"], receipt["architectures"]
            with (args.app / "Contents" / "Info.plist").open("rb") as stream:
                plist = plistlib.load(stream)
            receipt["bundle"] = {key: plist.get(key) for key in ("CFBundleIdentifier", "CFBundleShortVersionString", "CFBundleExecutable")}
            env = offline_environment(args.work)
            assert not any((args.work / "empty-hf").iterdir()), "Use a fresh --work path; HF cache must start empty"
            receipt["network"] = network_proof(args.work, env)
            receipt["backend"] = backend_checks(args.binary, args.work, args.fixtures, env)
            gui_work = args.work / "gui"
            gui_work.mkdir()
            gui_cmd = [sys.executable, str(Path(__file__).resolve()), "--app", str(args.app),
                       "--work", str(gui_work), "--fixtures", str(args.fixtures), "--gui-child"]
            with (args.work / "gui.log").open("w", encoding="utf-8") as log:
                child = subprocess.Popen(gui_cmd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                         start_new_session=True)
                try:
                    child.wait(2 * JOB_TIMEOUT + 120)
                finally:
                    stop_child(child)
            receipt["gui"] = read_json(gui_work / "gui-acceptance.json") if (gui_work / "gui-acceptance.json").is_file() else {}
            assert child.returncode == 0 and receipt["gui"].get("passed"), (args.work / "gui.log").read_text(encoding="utf-8")[-8000:]
            receipt["frozen_gui_smoke"] = frozen_gui_smoke(args, env)
            receipt["fixture_scope"] = "Synthetic Russian voices, not real UIS/CoMagic calls or overlapping/noisy customer speech"
            receipt["passed"] = True
        write_json(receipt_path, receipt)
        print("PASS: " + ("native Tk controls, dialogs, clipboard, stop/retry, frozen workers" if args.gui_child else
              "macOS signature/arch, OS-denied networking, frozen RU diarization/plain/stereo, outputs/rename, native GUI"), flush=True)
        return 0
    except Exception as exc:
        receipt.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        write_json(receipt_path, receipt)
        print("FAIL: " + receipt["error"], file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
