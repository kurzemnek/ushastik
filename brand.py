"""Ushastik's vector identity. No asset/font downloads at runtime."""
from pathlib import Path
import sys

BG, PAPER, INK = '#F5F3ED', '#FFFFFF', '#263A31'
MUTED, GREEN, GREEN_LIGHT = '#69756D', '#315D47', '#EAF1E9'
ORANGE, PEACH, LINE = '#B35432', '#F6E8DD', '#E3E7DF'


def mascot(canvas, x=0, y=0, size=100):
    scale = size / 100
    def oval(box, color):
        canvas.create_oval(*[x + v * scale if i % 2 == 0 else y + v * scale
                             for i, v in enumerate(box)], fill=color, outline='')
    oval((16, 1, 37, 65), GREEN)
    oval((56, 8, 80, 67), GREEN)
    oval((23, 12, 30, 46), '#D5B28F')
    oval((64, 18, 72, 47), '#D5B28F')
    oval((10, 39, 90, 98), GREEN)
    oval((30, 62, 36, 69), PAPER)
    oval((64, 62, 70, 69), PAPER)
    oval((46, 73, 54, 79), '#E7AF87')
    canvas.create_line(x+50*scale,y+79*scale,x+50*scale,y+84*scale,
                       fill='#E7AF87',width=max(1,scale*2))


def icon(root):
    import tkinter as tk
    folder = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'assets'
    if (folder / 'ushastik.png').is_file():
        root._ushastik_icon = tk.PhotoImage(file=str(folder / 'ushastik.png'))
        root.iconphoto(True, root._ushastik_icon)
