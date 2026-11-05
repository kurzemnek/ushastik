"""Local information windows and a small, per-user notice acknowledgement."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

from brand import BG, PAPER, INK, MUTED, GREEN, GREEN_LIGHT, LINE, mascot, icon
from engine import VERSION, atomic_json

LOCAL_TITLE = "Ушастик работает локально"
LOCAL_TEXT = (
    "Все вычисления выполняются на ресурсах вашего компьютера. "
    "Записи и расшифровки остаются у вас и никуда не отправляются. "
    "Приложение не обращается к интернету."
)
LOCAL_DETAILS = (
    "Модели уже в комплекте — ничего скачивать не нужно.",
    "Для обработки используются процессор и оперативная память.",
    "Нет сетевых запросов, телеметрии и проверки обновлений.",
)


def preferences_path():
    if sys.platform == "win32":
        folder = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        folder = Path.home() / "Library" / "Application Support"
    else:
        folder = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return folder / "Ushastik" / "ui.json"


class ApplicationInfo:
    def __init__(self, root, font):
        self.root, self.font = root, font
        self.about_window = self.notice_window = None

    def label(self, parent, text, size=11, color=INK, bold=False, **kwargs):
        import tkinter as tk
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color,
                        font=(self.font, size, "bold" if bold else "normal"), **kwargs)

    def button(self, parent, text, command):
        import tkinter as tk
        return tk.Button(parent, text=text, command=command, bg=GREEN, fg=GREEN if sys.platform == "darwin" else PAPER,
                         activebackground="#254B38", activeforeground=PAPER, relief="flat", bd=0,
                         padx=22, pady=10, cursor="hand2", font=(self.font, 11, "bold"))

    def window(self, title, width, height):
        import tkinter as tk
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        icon(dialog)
        self.root.update_idletasks()
        if sys.platform == "darwin":
            width = min(width, self.root.winfo_screenwidth() - 48)
            height = min(height, self.root.winfo_screenheight() - 128)
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2)
        if sys.platform == "darwin":
            x = min(x, max(0, self.root.winfo_screenwidth() - width - 24))
            y = min(y, max(0, self.root.winfo_screenheight() - height - 100))
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        return dialog

    def show_notice_if_needed(self):
        try:
            preferences = json.loads(preferences_path().read_text(encoding="utf-8"))
            if isinstance(preferences, dict) and preferences.get("local_notice_seen") is True:
                return
        except (OSError, ValueError):
            pass
        self.show_notice()

    def show_notice(self):
        import tkinter as tk
        if self.notice_window is not None and self.notice_window.winfo_exists():
            self.notice_window.lift()
            return
        dialog = self.window("Локальная работа · Ушастик", 600, 475)
        self.notice_window = dialog
        dialog.resizable(False, False)
        content = tk.Frame(dialog, bg=BG, padx=28, pady=22)
        content.pack(fill="both", expand=True)
        mark = tk.Canvas(content, bg=BG, width=56, height=59, highlightthickness=0)
        mark.pack(anchor="w", pady=(0, 12))
        mascot(mark, 0, 0, 56)
        self.label(content, LOCAL_TITLE, 19, bold=True).pack(anchor="w")
        self.label(content, LOCAL_TEXT, 11, wraplength=540, justify="left").pack(fill="x", pady=(12, 15))
        for text in LOCAL_DETAILS:
            self.label(content, "●  " + text, 10, GREEN, wraplength=540, justify="left").pack(anchor="w", pady=4)
        self.label(content, "Эта информация всегда доступна в разделе «О программе».",
                   9, MUTED, wraplength=540, justify="left").pack(anchor="w", pady=(14, 0))

        def acknowledge():
            try:
                path = preferences_path()
                try:
                    preferences = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    preferences = {}
                if not isinstance(preferences, dict):
                    preferences = {}
                preferences["local_notice_seen"] = True
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(path, preferences)
            except OSError:
                # An unwritable user profile must not prevent offline transcription.
                # The notice will simply be shown on the next launch as well.
                pass
            dialog.destroy()

        actions = tk.Frame(content, bg=BG)
        actions.pack(side="bottom", fill="x", pady=(14, 0))
        confirm = self.button(actions, "Понятно", acknowledge)
        confirm.pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", acknowledge)
        dialog.bind("<Escape>", lambda event: acknowledge())
        dialog.bind("<Return>", lambda event: acknowledge())
        dialog.grab_set()
        confirm.focus_set()

    def show_about(self):
        import tkinter as tk
        from tkinter import ttk
        if self.about_window is not None and self.about_window.winfo_exists():
            self.about_window.lift()
            return
        dialog = self.window("О программе · Ушастик", 660, 650)
        self.about_window = dialog
        dialog.minsize(600, 540)
        head = tk.Frame(dialog, bg=BG, padx=24, pady=20)
        head.pack(fill="x")
        mark = tk.Canvas(head, bg=BG, width=58, height=61, highlightthickness=0)
        mark.pack(side="left", padx=(0, 16))
        mascot(mark, 0, 0, 58)
        names = tk.Frame(head, bg=BG)
        names.pack(side="left")
        self.label(names, "Ушастик", 24, bold=True).pack(anchor="w")
        self.label(names, f"Услышит. Разберёт. Запишет.  /  Версия {VERSION}", 10, MUTED).pack(anchor="w", pady=(3, 0))
        card = tk.Frame(dialog, bg=GREEN_LIGHT, padx=20, pady=14)
        card.pack(fill="x", padx=24, pady=(0, 14))
        self.label(card, "Локально · без интернета", 12, GREEN, bold=True).pack(anchor="w")
        self.label(card, LOCAL_TEXT, 10, wraplength=570, justify="left").pack(fill="x", pady=(6, 0))
        reader = tk.Frame(dialog, bg=PAPER, highlightbackground=LINE, highlightthickness=1)
        reader.pack(fill="both", expand=True, padx=24)
        text = tk.Text(reader, bg=PAPER, fg=INK, relief="flat", bd=0, padx=16, pady=10,
                       font=(self.font, 10), wrap="word", highlightthickness=0, cursor="arrow",
                       selectbackground=GREEN_LIGHT, selectforeground=INK, width=60, height=8)
        scroll = ttk.Scrollbar(reader, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill="both", expand=True)
        text.tag_configure("heading", foreground=GREEN, font=(self.font, 11, "bold"), spacing1=10, spacing3=6)
        text.tag_configure("body", spacing3=12)
        sections = (
            ("Для чего", "Расшифровывает записи звонков и встреч, разделяет реплики по голосам и добавляет таймкоды. "
             "Имена или роли участников можно указать после обработки."),
            ("На ресурсах вашего компьютера", "\n".join(LOCAL_DETAILS) + "\nСкорость зависит от вашего процессора, памяти и длины записи."),
            ("Что сохраняется", "TXT — текст разговора, SRT — субтитры, JSON — реплики, слова и метаданные. "
             "Результаты остаются в выбранной вами папке. Старые расшифровки не перезаписываются."),
            ("Как пользоваться", "Добавьте файлы или папку, выберите разбивку и число участников, нажмите «Расшифровать». "
             "Затем выберите готовую запись, чтобы прочитать диалог, задать имена и скопировать текст. "
             "Поддерживаются аудио и видео со звуком, до 90 минут на файл."),
            ("Точность", "Шум, перебивания и короткие реплики могут ухудшать распознавание. "
             "Важные формулировки сверяйте со звуком. Номера голосов действуют внутри одной записи; "
             "имена и роли назначает человек."),
            ("Технологии и лицензии", "Whisper Small / faster-whisper — распознавание речи. "
             "sherpa-onnx, pyannote и 3D-Speaker — разделение голосов. Модели включены в сборку. "
             + ("Лицензии находятся внутри приложения: Contents/Resources/licenses, "
                "Contents/Resources/model и Contents/Resources/speaker-model." if sys.platform == "darwin" else
                "Лицензии библиотек находятся в папке licenses рядом с программой, "
                "лицензии моделей — в _internal/model и _internal/speaker-model.")),
            ("Автор", "Валерий Курземнек · kurzemnek.ru"),
        )
        for heading, body in sections:
            text.insert("end", heading + "\n", "heading")
            text.insert("end", body + "\n", "body")
        text.configure(state="disabled")
        foot = tk.Frame(dialog, bg=BG, padx=24, pady=14)
        foot.pack(fill="x")
        self.label(foot, "Без регистрации, подписки и API-ключей.", 9, MUTED).pack(side="left")
        self.button(foot, "Закрыть", dialog.destroy).pack(side="right")
        dialog.bind("<Escape>", lambda event: dialog.destroy())
