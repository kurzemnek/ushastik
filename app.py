"""Ushastik: an offline transcription workspace and worker supervisor."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from engine import VERSION, EXTENSIONS, atomic_json, collect_paths, run_job, rename_speakers
from brand import BG, PAPER, INK, MUTED, GREEN, GREEN_LIGHT, ORANGE, LINE, mascot, icon
from information import ApplicationInfo


def recordings_count(number):
    word = "записей" if 11 <= number % 100 <= 14 else ("запись" if number % 10 == 1 else
                                                     "записи" if number % 10 in (2, 3, 4) else "записей")
    return f"{number} {word}"


def gui(paths=()):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinterdnd2 import TkinterDnD, DND_FILES

    root = TkinterDnD.Tk()
    root.title(f"Ушастик · {VERSION}")
    compact = sys.platform == "darwin" and (root.winfo_screenwidth() < 1200 or root.winfo_screenheight() < 900)
    if sys.platform == "darwin":
        width, height = min(1180, root.winfo_screenwidth() - 64), min(820, root.winfo_screenheight() - 128)
        root.geometry(f"{width}x{height}+{(root.winfo_screenwidth() - width) // 2}+24")
        root.minsize(min(1040, width), min(740, height))
    else:
        root.geometry("1180x820")
        root.minsize(1040, 740)
    root.configure(bg=BG)
    icon(root)
    font = "Helvetica Neue" if sys.platform == "darwin" else "Segoe UI"
    information = ApplicationInfo(root, font)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Treeview", background=PAPER, foreground=INK, fieldbackground=PAPER,
                    rowheight=38 if compact else 48, font=(font, 10), borderwidth=0, relief="flat")
    style.configure("Treeview.Heading", background=PAPER, foreground=MUTED,
                    font=(font, 9), padding=(10, 10), relief="flat")
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.map("Treeview", background=[("selected", GREEN_LIGHT)], foreground=[("selected", INK)])
    style.map("Treeview.Heading", background=[("active", PAPER)])
    style.configure("Horizontal.TProgressbar", background=GREEN, troughcolor=LINE,
                    borderwidth=0, thickness=4, bordercolor=BG, lightcolor=GREEN, darkcolor=GREEN)
    style.configure("Vertical.TScrollbar", background=LINE, troughcolor=PAPER,
                    bordercolor=PAPER, arrowcolor=MUTED, width=12)
    items, process, job_dir, job_id = {}, None, None, None
    closing, cancel_requested, started = False, False, 0
    preview_key, preview_data = None, None
    old_output = Path.home() / "Documents" / "Расшифровки монстра"
    default_output = old_output if old_output.is_dir() else Path.home() / "Documents" / "Ушастик"
    output = tk.StringVar(value=str(default_output))
    language, mode, speaker_number = tk.StringVar(value="Русский"), tk.StringVar(value="По голосам"), tk.StringVar(value="2")
    status = tk.StringVar(value="Готов слушать")
    detail = tk.StringVar(value="Добавьте записи и нажмите «Расшифровать».")
    count = tk.StringVar(value=recordings_count(0))
    preview_sub = tk.StringVar(value="Текст с таймкодами и подписями участников")
    selected_detail = tk.StringVar(value="До 90 минут на запись · папки без вложенных папок")
    mode_hint = tk.StringVar()

    def label(parent, text=None, size=11, color=INK, bold=False, **kwargs):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color,
                        font=(font, size, "bold" if bold else "normal"), **kwargs)

    def button(parent, text, command, primary=False, small=False):
        # Aqua draws its own light button face and ignores Tk's background.
        primary_ink = GREEN if sys.platform == "darwin" else PAPER
        return tk.Button(parent, text=text, command=command,
            font=(font, 10 if small else 11, "bold" if primary else "normal"),
            bg=GREEN if primary else GREEN_LIGHT, fg=primary_ink if primary else GREEN,
            activebackground="#254B38" if primary else "#DBE7DA", activeforeground=PAPER if primary else GREEN,
            bd=0, padx=13 if small else 20, pady=8 if small else 11, cursor="hand2", relief="flat",
            disabledforeground="#909C92", highlightthickness=1, highlightbackground=LINE, highlightcolor=ORANGE)

    head = tk.Frame(root, bg=BG, padx=18 if compact else 26, pady=12 if compact else 18)
    head.pack(fill="x")
    mark = tk.Canvas(head, width=52, height=56, bg=BG, highlightthickness=0)
    mark.pack(side="left", padx=(0, 14))
    mascot(mark, 1, 1, 50)
    wordmark = tk.Frame(head, bg=BG)
    wordmark.pack(side="left")
    label(wordmark, "Ушастик", 26, bold=True).pack(anchor="w")
    label(wordmark, "Услышит. Разберёт. Запишет.", 10, MUTED).pack(anchor="w")
    badge = tk.Frame(head, bg=GREEN_LIGHT, padx=12, pady=8)
    badge.pack(side="right")
    label(badge, "●  Локально · без интернета", 10, GREEN, bold=True).pack(anchor="w")
    label(badge, "Процессор и память вашего компьютера", 9, MUTED).pack(anchor="w", pady=(3, 0))

    body = tk.Frame(root, bg=BG, padx=18 if compact else 26)
    body.pack(fill="both", expand=True)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    workspace = tk.Frame(body, bg=BG)
    workspace.grid(row=0, column=0, sticky="nsew")
    workspace.columnconfigure(0, weight=4, minsize=310 if compact else 366)
    workspace.columnconfigure(1, weight=6, minsize=440 if compact else 500)
    workspace.rowconfigure(0, weight=1)
    queue = tk.Frame(workspace, bg=PAPER, highlightbackground=LINE, highlightthickness=1)
    queue.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
    queue.columnconfigure(0, weight=1)
    queue.rowconfigure(3, weight=1)
    qhead = tk.Frame(queue, bg=PAPER, padx=16, pady=10 if compact else 13)
    qhead.grid(row=0, column=0, sticky="ew")
    label(qhead, "Записи", 14, bold=True).pack(side="left")
    label(qhead, size=10, color=MUTED, textvariable=count).pack(side="right")
    tools = tk.Frame(queue, bg=PAPER, padx=16)
    tools.grid(row=1, column=0, sticky="ew", pady=(0, 8 if compact else 12))

    def paint_item(item):
        statuses = {"pending": "Ожидает", "running": "Слушаю…", "done": "Готово", "error": "Ошибка",
                    "no_speech": "Нет речи", "cancelled": "Остановлено"}
        tree.item(item["iid"], values=(Path(item["path"]).name, statuses.get(item["status"], item["status"])),
                  tags=(item["status"],))

    def add(raw):
        if process is not None:
            return
        try:
            additions = collect_paths(raw)
        except OSError as exc:
            messagebox.showerror("Не удалось открыть файлы", str(exc), parent=root)
            return
        for p in additions:
            key = os.path.normcase(str(p))
            if key not in items:
                item = {"path": str(p), "iid": tree.insert("", "end"), "status": "pending"}
                items[key] = item
                paint_item(item)
        if additions:
            status.set("Записи в очереди")
            detail.set("Можно начать. Ушастик обработает записи по очереди.")
        elif raw:
            status.set("Подходящих записей не найдено")
            detail.set("Добавьте аудио или видео со звуком. Папки просматриваются без вложенных папок.")
        refresh()

    add_button = button(tools, "+  Файлы", lambda: add(filedialog.askopenfilenames(
        parent=root, title="Добавить записи", filetypes=[("Аудио и видео", " ".join("*"+x for x in sorted(EXTENSIONS))),
                                                       ("Все файлы", "*.*")])), small=True)
    add_button.pack(side="left", padx=(0, 8))
    folder_button = button(tools, "Папка", lambda: add([p] if (p := filedialog.askdirectory(parent=root)) else []), small=True)
    folder_button.pack(side="left")
    drop = tk.Frame(queue, bg=BG, highlightbackground=LINE, highlightthickness=1, pady=8 if compact else 12)
    drop.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
    drop_title = label(drop, "↓  Или перетащите сюда", 11, GREEN, bold=True)
    drop_title.pack()
    drop_sub = label(drop, "MP3 · WAV · M4A · аудио и видео", 9, MUTED)
    drop_sub.pack(pady=(4, 0))
    table_frame = tk.Frame(queue, bg=PAPER, height=48 if compact else 80)
    table_frame.grid(row=3, column=0, sticky="nsew", padx=6)
    table_frame.pack_propagate(False)
    tree = ttk.Treeview(table_frame, columns=("file", "status"), show="headings", selectmode="browse", height=4)
    for col, title, width in (("file", "ЗАПИСЬ", 235), ("status", "СТАТУС", 105)):
        tree.heading(col, text=title, anchor="w")
        tree.column(col, width=width, minwidth=(135 if compact else 175) if col == "file" else 100, stretch=col == "file")
    tree.tag_configure("done", foreground=GREEN)
    tree.tag_configure("error", foreground=ORANGE)
    tree.tag_configure("cancelled", foreground=MUTED)
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    qfoot = tk.Frame(queue, bg=PAPER, padx=16, pady=5 if compact else 8)
    qfoot.grid(row=4, column=0, sticky="ew")

    def clear():
        tree.delete(*tree.get_children())
        items.clear()
        show_preview(None, force=True)
        status.set("Очередь очищена")
        detail.set("Сохранённые расшифровки остались в папке результатов.")
        selected_detail.set("До 90 минут на запись · папки без вложенных папок")
        progress["value"] = 0
        refresh()

    clear_button = button(qfoot, "Очистить очередь", clear, small=True)
    clear_button.pack(side="left")
    conversation = tk.Frame(workspace, bg=PAPER, highlightbackground=LINE, highlightthickness=1)
    conversation.grid(row=0, column=1, sticky="nsew")
    conversation.columnconfigure(0, weight=1)
    conversation.rowconfigure(1, weight=1)
    phead = tk.Frame(conversation, bg=PAPER, padx=20, pady=13)
    phead.grid(row=0, column=0, sticky="ew")
    label(phead, "Разговор", 14, bold=True, anchor="w").pack(fill="x")
    preview_sub_label = label(phead, size=10, color=MUTED, textvariable=preview_sub, anchor="w")
    preview_sub_label.pack(fill="x", pady=(4, 0))
    reader = tk.Frame(conversation, bg=PAPER)
    reader.grid(row=1, column=0, sticky="nsew")
    transcript = tk.Text(reader, bg=PAPER, fg=INK, font=(font, 11), relief="flat", bd=0,
        padx=20, pady=6, wrap="word", state="disabled", cursor="arrow", selectbackground=GREEN_LIGHT,
        selectforeground=INK, highlightthickness=0, width=40, height=5)
    pscroll = ttk.Scrollbar(reader, orient="vertical", command=transcript.yview)
    transcript.configure(yscrollcommand=pscroll.set)
    pscroll.pack(side="right", fill="y")
    transcript.pack(side="left", fill="both", expand=True)
    transcript.tag_configure("time", foreground=MUTED, font=(font, 9), spacing1=12, spacing3=3)
    for i, color in enumerate((GREEN, ORANGE, "#69568C")):
        transcript.tag_configure(f"voice{i}", foreground=color, font=(font, 10, "bold"), spacing3=5)
    transcript.tag_configure("body", spacing3=12)
    empty = tk.Frame(reader, bg=PAPER)
    empty.place(relx=0.5, rely=0.47, anchor="center")
    bunny = tk.Canvas(empty, width=82, height=86, bg=PAPER, highlightthickness=0)
    bunny.pack(pady=(0, 18))
    mascot(bunny, 1, 1, 80)
    empty_title = label(empty, "Здесь появится разговор", 15, bold=True)
    empty_title.pack()
    empty_help = label(empty, "Добавьте запись слева и запустите расшифровку.\nУшастик разложит разговор по голосам.",
                       10, MUTED, justify="center")
    empty_help.pack(pady=(9, 0))
    ptools = tk.Frame(conversation, bg=PAPER, padx=14, pady=11)
    ptools.grid(row=2, column=0, sticky="ew")

    def selected_item():
        selection = tree.selection()
        return next((i for i in items.values() if selection and i["iid"] == selection[0]), None)

    def show_preview(item, force=False):
        nonlocal preview_key, preview_data
        key = (item.get("output"), item.get("status"), item.get("path")) if item else None
        if key == preview_key and not force:
            return
        preview_key, preview_data = key, None
        transcript.configure(state="normal")
        transcript.delete("1.0", "end")
        data = None
        if item and item.get("output"):
            try:
                data = json.loads((Path(item["output"]) / "transcript.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                selected_detail.set(f"Не удалось прочитать результат: {exc}")
        if data and data.get("segments"):
            preview_data = data
            empty.place_forget()
            names = data.get("speaker_names", {})
            voices = list(names)
            for segment in data["segments"]:
                seconds = max(0, int(segment["start"]))
                clock = (f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}" if seconds >= 3600 else
                         f"{seconds//60:02}:{seconds%60:02}")
                transcript.insert("end", clock+"\n", "time")
                speaker = (segment.get("speaker") if data["mode"] == "speakers" else
                           f"channel_{segment['channel']}" if data["mode"] == "channels" else None)
                if data["mode"] != "mixed":
                    color = voices.index(speaker) % 3 if speaker in voices else 2
                    transcript.insert("end", names.get(speaker, "Спикер не определён")+"\n", f"voice{color}")
                transcript.insert("end", segment["text"].strip()+"\n", "body")
            transcript.yview_moveto(0)
            length = int(data.get("duration_seconds", 0))
            preview_sub.set(f"{Path(item['path']).name}  ·  {length//60}:{length%60:02}" +
                            (f"  ·  Голосов: {len(names)}" if names else ""))
        else:
            empty.place(relx=0.5, rely=0.47, anchor="center")
            preview_sub.set(Path(item["path"]).name if item else "Текст с таймкодами и подписями участников")
            title, help_text = "Здесь появится разговор", "Добавьте запись слева и запустите расшифровку.\nУшастик разложит разговор по голосам."
            if item:
                title, help_text = {
                    "running": ("Ушастик слушает…", "Текст появится, когда эта запись будет готова."),
                    "error": ("Не получилось прочитать запись", "Подробности — под очередью.\nМожно проверить файл и повторить обработку."),
                    "no_speech": ("Речь не найдена", "В этой записи не удалось распознать речь."),
                    "cancelled": ("Обработка остановлена", "Нажмите «Расшифровать», чтобы повторить.")
                }.get(item["status"], ("Запись ждёт своей очереди", "Нажмите «Расшифровать».\nГотовый диалог появится здесь."))
            empty_title.configure(text=title)
            empty_help.configure(text=help_text)
        transcript.configure(state="disabled")
        copy_button.configure(state="normal" if preview_data else "disabled")
        result_button.configure(state="normal" if item and item.get("output") else "disabled")

    def selection_changed(event=None):
        item = selected_item()
        selected_detail.set(item.get("detail", item["path"]) if item else "До 90 минут на запись · папки без вложенных папок")
        show_preview(item)
        rename_button.configure(state="normal" if process is None and preview_data and preview_data.get("speaker_names") else "disabled")

    tree.bind("<<TreeviewSelect>>", selection_changed)

    def open_folder(path):
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            messagebox.showinfo("Результаты", "Папка появится после обработки первой записи.", parent=root)
            return
        if sys.platform == "win32":
            os.startfile(str(p))
        else:
            subprocess.Popen(["/usr/bin/open" if sys.platform == "darwin" else "xdg-open", str(p)])

    def open_selected(event=None):
        item = selected_item()
        if item and item.get("output"):
            open_folder(item["output"])

    tree.bind("<Double-1>", open_selected)

    def copy_text():
        item = selected_item()
        if not item or not item.get("output"):
            return
        try:
            text = (Path(item["output"])/"transcript.txt").read_text(encoding="utf-8-sig")
            root.clipboard_clear()
            root.clipboard_append(text)
            status.set("Текст скопирован")
        except OSError as exc:
            messagebox.showerror("Не удалось скопировать", str(exc), parent=root)

    def edit_names():
        item = selected_item()
        if not item or not item.get("output") or process is not None:
            return
        folder = Path(item["output"])
        try:
            names = json.loads((folder/"transcript.json").read_text(encoding="utf-8")).get("speaker_names", {})
            if not names:
                return
        except (OSError, ValueError) as exc:
            messagebox.showerror("Не удалось прочитать результат", str(exc), parent=root)
            return
        dialog = tk.Toplevel(root)
        dialog.title("Имена участников · Ушастик")
        dialog.configure(bg=BG, padx=24, pady=20)
        dialog.transient(root)
        dialog.resizable(False, False)
        label(dialog, "Кто участвует в разговоре?", 16, bold=True).pack(anchor="w")
        label(dialog, "Можно указать имена или роли: менеджер, клиент.\nПодписи обновятся в тексте, субтитрах и JSON.",
              10, MUTED, justify="left").pack(anchor="w", pady=(8, 16))
        fields = {}
        for key, name in names.items():
            row = tk.Frame(dialog, bg=BG)
            row.pack(fill="x", pady=5)
            label(row, name, 11, MUTED, width=16, anchor="w").pack(side="left")
            value = tk.StringVar(value=name)
            fields[key] = value
            tk.Entry(row, textvariable=value, bg=PAPER, fg=INK, insertbackground=INK, font=(font, 11), width=28,
                     relief="flat", highlightbackground=LINE, highlightcolor=GREEN, highlightthickness=1).pack(side="left", ipady=7)
        def save():
            try:
                rename_speakers(folder, {key: value.get() for key, value in fields.items()})
            except (OSError, ValueError) as exc:
                messagebox.showerror("Не удалось сохранить имена", str(exc), parent=dialog)
                return
            show_preview(item, force=True)
            status.set("Имена сохранены")
            detail.set("Подписи обновлены в разговоре, TXT, SRT и JSON.")
            dialog.destroy()
        button(dialog, "Сохранить имена", save, True).pack(anchor="e", pady=(18, 0))
        dialog.grab_set()

    rename_button = button(ptools, "Имена участников", edit_names, small=True)
    rename_button.pack(side="left", padx=(0, 8))
    copy_button = button(ptools, "Копировать", copy_text, small=True)
    copy_button.pack(side="left")
    result_button = button(ptools, "Папка", open_selected, small=True)
    result_button.pack(side="right")
    info_label = tk.Label(body, textvariable=selected_detail, bg=BG, fg=MUTED, font=(font, 9), anchor="w", justify="left", height=1 if compact else 2)
    info_label.grid(row=1, column=0, sticky="ew", pady=(4, 5))
    settings = tk.Frame(body, bg=PAPER, highlightbackground=LINE, highlightthickness=1, padx=16, pady=8 if compact else 12)
    settings.grid(row=2, column=0, sticky="ew")
    settings.columnconfigure(0, weight=1)
    options = tk.Frame(settings, bg=PAPER)
    options.grid(row=0, column=0, sticky="ew")

    def menu(parent, variable, values, width):
        w = tk.OptionMenu(parent, variable, *values)
        w.configure(bg=BG, fg=INK, activebackground=GREEN_LIGHT, activeforeground=INK,
                    highlightthickness=0, bd=0, font=(font, 10), width=width, padx=9, pady=5)
        w["menu"].configure(bg=PAPER, fg=INK, activebackground=GREEN_LIGHT, activeforeground=INK, font=(font, 10), bd=0)
        return w

    label(options, "Разбивка", 10, MUTED).pack(side="left", padx=(0, 8))
    mode_menu = menu(options, mode, ("По голосам", "Два канала", "Общий текст"), 13)
    mode_menu.pack(side="left")
    label(options, "Участников", 10, MUTED).pack(side="left", padx=(22, 8))
    speaker_menu = menu(options, speaker_number, ("Авто", "1", "2", "3", "4", "5", "6"), 5)
    speaker_menu.pack(side="left")
    label(options, "Язык", 10, MUTED).pack(side="left", padx=(22, 8))
    language_menu = menu(options, language, ("Русский", "Авто", "English"), 9)
    language_menu.pack(side="left")
    label(options, "TXT  ·  SRT  ·  JSON", 9, MUTED).pack(side="right")
    label(settings, size=9, color=MUTED, textvariable=mode_hint, anchor="w").grid(row=1, column=0, sticky="ew", pady=(3, 5) if compact else (5, 8))
    dest = tk.Frame(settings, bg=PAPER)
    dest.grid(row=2, column=0, sticky="ew")
    label(dest, "Результаты", 10, MUTED).pack(side="left", padx=(0, 12))
    output_entry = tk.Entry(dest, textvariable=output, bg=BG, fg=INK, insertbackground=INK, font=(font, 10),
                            relief="flat", highlightthickness=1, highlightbackground=LINE, highlightcolor=GREEN)
    output_entry.pack(side="left", fill="x", expand=True, ipady=7)
    def choose_output():
        if p := filedialog.askdirectory(parent=root, title="Папка результатов"):
            output.set(p)
    output_button = button(dest, "Изменить", choose_output, small=True)
    output_button.pack(side="left", padx=(8, 0))
    footer = tk.Frame(body, bg=BG, pady=8 if compact else 14)
    footer.grid(row=3, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    status_label = label(footer, size=11, bold=True, textvariable=status, anchor="w")
    status_label.grid(row=0, column=0, sticky="ew")
    detail_label = label(footer, size=9, color=MUTED, textvariable=detail, anchor="w")
    detail_label.grid(row=1, column=0, sticky="ew", pady=(3, 0))
    actions = tk.Frame(footer, bg=BG)
    actions.grid(row=0, column=1, rowspan=2, padx=(18, 0))

    def refresh():
        busy = process is not None
        for w in (add_button, folder_button, clear_button, output_button, language_menu, mode_menu, output_entry):
            w.configure(state="disabled" if busy else "normal")
        start_button.configure(state="normal" if not busy and any(i["status"] in ("pending", "error", "cancelled") for i in items.values()) else "disabled")
        stop_button.configure(state="normal" if busy and not cancel_requested else "disabled")
        speaker_menu.configure(state="normal" if not busy and mode.get() == "По голосам" else "disabled")
        rename_button.configure(state="normal" if not busy and preview_data and preview_data.get("speaker_names") else "disabled")
        count.set(recordings_count(len(items)))
        mode_hint.set({"По голосам": "Участники определяются по голосам. Имена можно задать после обработки.",
                       "Два канала": "Для стереозаписи, где каждый участник записан в отдельный канал.",
                       "Общий текст": "Единый текст без определения участников."}[mode.get()])

    def start():
        nonlocal process, job_dir, job_id, cancel_requested, started
        pending = [i["path"] for i in items.values() if i["status"] in ("pending", "error", "cancelled")]
        if not pending or process is not None:
            return
        try:
            target = Path(output.get()).expanduser().resolve()
            target.mkdir(parents=True, exist_ok=True)
            output.set(str(target))
            with tempfile.TemporaryFile(dir=target):
                pass
            job_dir = Path(tempfile.mkdtemp(prefix="ushastik-"))
            job_id = uuid.uuid4().hex
            atomic_json(job_dir/"job.json", {"id": job_id, "paths": pending,
                "language": {"Русский": "ru", "Авто": "auto", "English": "en"}[language.get()],
                "split": mode.get() == "Два канала", "diarize": mode.get() == "По голосам",
                "num_speakers": 0 if speaker_number.get() == "Авто" else int(speaker_number.get()), "output": str(target)})
            for p in pending:
                item = items[os.path.normcase(p)]
                item.update(status="pending", detail="Ожидает")
                paint_item(item)
            cmd = [sys.executable]
            if not getattr(sys, "frozen", False):
                cmd.append(str(Path(__file__).resolve()))
            cmd.extend(["--worker", str(job_dir/"job.json")])
            process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            cancel_requested, started = False, time.monotonic()
            progress["value"] = 0
            status.set("Ушастик готовится слушать…")
            detail.set("Первый запуск может занять немного времени: загружаются локальные модели.")
            if not tree.selection():
                tree.selection_set(items[os.path.normcase(pending[0])]["iid"])
            refresh()
        except Exception as exc:
            if job_dir:
                shutil.rmtree(job_dir, ignore_errors=True)
                job_dir = None
            messagebox.showerror("Не удалось начать", str(exc), parent=root)

    def stop():
        nonlocal cancel_requested
        if process is not None and process.poll() is None:
            cancel_requested = True
            status.set("Останавливаю обработку…")
            process.terminate()
            refresh()

    stop_button = button(actions, "Остановить", stop)
    stop_button.pack(side="left", padx=(0, 10))
    start_button = button(actions, "Расшифровать", start, True)
    start_button.pack(side="left")
    progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
    progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8 if compact else 12, 0))
    bottom = tk.Frame(body, bg=BG)
    bottom.grid(row=4, column=0, sticky="ew", pady=(0, 6 if compact else 12))
    label(bottom, "Ушастик  /  "+VERSION, 9, MUTED).pack(side="left")
    about_button = button(bottom, "О программе", information.show_about, small=True)
    about_button.configure(pady=3, bg=BG, highlightthickness=0)
    about_button.pack(side="left", padx=(16, 0))
    all_results = button(bottom, "Все результаты ↗", lambda: open_folder(output.get()), small=True)
    all_results.configure(pady=3, bg=BG, highlightthickness=0)
    all_results.pack(side="right")

    def ingest(state):
        for entry in state["items"]:
            item = items.get(os.path.normcase(entry["path"]))
            if item:
                item.update(entry)
                paint_item(item)
        selection_changed()

    def tick():
        nonlocal process, job_dir
        state = None
        if process is not None:
            try:
                state = json.loads((job_dir/"state.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            if state:
                ingest(state)
                if not cancel_requested:
                    status.set(state.get("detail", "Обрабатываю…"))
                progress["value"] = 100*(state.get("current", 0)+state.get("progress", 0)/100)/max(1, len(state["items"]))
            elapsed = int(time.monotonic()-started)
            detail.set(f"Прошло {elapsed//60}:{elapsed%60:02} · записи обрабатываются на компьютере")
            if process.poll() is not None:
                code = process.returncode
                # Re-read after exit to include the worker's final atomic state.
                try:
                    state = json.loads((job_dir/"state.json").read_text(encoding="utf-8"))
                    ingest(state)
                except (OSError, ValueError):
                    pass
                for item in items.values():
                    if item["status"] in ("running", "pending"):
                        item.update(status="cancelled" if cancel_requested else "error",
                            detail="Остановлено" if cancel_requested else (state or {}).get("detail", f"Ошибка процесса ({code})"))
                        paint_item(item)
                if job_id:
                    for p in Path(output.get()).glob(f".monstr-{job_id}-*"):
                        if p.is_dir():
                            shutil.rmtree(p, ignore_errors=True)
                counts = {s: sum(i["status"] == s for i in items.values()) for s in ("done", "error", "no_speech", "cancelled")}
                status.set(f"{'Остановлено' if cancel_requested else 'Готово'} · {recordings_count(counts['done'])}")
                if state and state.get("phase") == "fatal":
                    detail.set(state["detail"])
                elif counts["error"] or counts["no_speech"] or counts["cancelled"]:
                    detail.set(f"Без речи: {counts['no_speech']} · Ошибки: {counts['error']} · Остановлено: {counts['cancelled']}. Подробности — в очереди.")
                else:
                    detail.set("Выберите запись, чтобы прочитать разговор и задать имена участников.")
                process = None
                shutil.rmtree(job_dir, ignore_errors=True)
                job_dir = None
                if code == 0 and not cancel_requested:
                    progress["value"] = 100
                selection_changed()
                refresh()
                if closing:
                    root.destroy()
                    return
        root.after(250, tick)

    def close():
        nonlocal closing
        if process is not None:
            if messagebox.askyesno("Обработка ещё идёт", "Остановить обработку и закрыть Ушастика?", parent=root):
                closing = True
                if process is None:
                    root.destroy()
                else:
                    stop()
        else:
            root.destroy()

    def resize(event=None):
        info_label.configure(wraplength=max(700, body.winfo_width()-56))
        available = max(350, footer.winfo_width()-actions.winfo_reqwidth()-30)
        status_label.configure(wraplength=available)
        detail_label.configure(wraplength=available)
        preview_sub_label.configure(wraplength=max(380, conversation.winfo_width()-50))

    def drop_files(event):
        add(root.tk.splitlist(event.data))
        return "break"

    for target in (drop, drop_title, drop_sub, tree):
        target.drop_target_register(DND_FILES)
        target.dnd_bind("<<Drop>>", drop_files)
    root.protocol("WM_DELETE_WINDOW", close)
    if sys.platform == "darwin":
        # Finder/Dock file delivery and Cmd+Q use Apple events rather than WM_DELETE_WINDOW.
        root.createcommand("::tk::mac::OpenDocument", lambda *files: add(files))
        root.createcommand("::tk::mac::Quit", close)
        root.createcommand("::tk::mac::ReopenApplication", lambda: (root.deiconify(), root.lift()))
        root.createcommand("tkAboutDialog", information.show_about)
    root.bind("<Configure>", resize)
    mode.trace_add("write", lambda *_: refresh())
    add(paths)
    show_preview(None, force=True)
    refresh()
    tick()
    root.after(300, information.show_notice_if_needed)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        raise SystemExit(run_job(Path(sys.argv[2])))
    gui(sys.argv[1:])
